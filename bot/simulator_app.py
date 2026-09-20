"""Simulation-mode bot process entry point (docs/bot_simulation_mode_design.md).

A second, dedicated process from the real `bot/app.py` — started separately
(`python -m bot.simulator_app <profile_module>`), never as part of it. It
reuses `register_handlers()` (`bot/app.py`) and both background loops
(`bot/background_services.py`) completely unmodified, with only the two
Telegram network legs stubbed (`bot/simulator_transport.py`), so the admin
simulator's message-kind scenario steps — proxied here from
`api/admin.py`'s `POST /admin/simulator/bot-msg` — are fed through the
bot's *real* decision-making code, not a reimplementation of it. See the
design doc for the full architecture and the isolation guarantees this
relies on.
"""

import argparse
import asyncio
import logging
import threading
from pathlib import Path
from typing import TYPE_CHECKING

import telegram
from flask import Flask, jsonify, request
from telegram.ext import ApplicationBuilder

from bot.app import register_handlers
from bot.background_services import SingleInstanceLock
from bot.contracts import BotDeps, BotStartupError, resolve_bot_service_key
from bot.simulator_transport import FakeBotRequest, SimulatorTelegramClient, build_synthetic_text_update
from bot.transports import HttpApiClient
from config import ModelTierError, TierModel, resolve_tier_model_from_env
from profiles import simulation_group_chat_id, simulation_user_telegram_id
from profiles.loader import ProfileLoadError, ProfileValidationError, load_profile
from tools import configure_logging

if TYPE_CHECKING:
    from profiles.contracts import LoadedProfile

logger = logging.getLogger(__name__)

# Matches the literal `bot/transports.py`'s `HttpApiClient` already sends this same header
# as (no shared named constant crosses the api/bot package boundary today — see
# api/request_boundary.py's own SERVICE_KEY_HEADER for the API-side half of this pair).
SERVICE_KEY_HEADER = "X-Service-Key"


class SimulatorRequestRefused(Exception):
    """A `POST /Simulator-msg` request failed the identity-allowlist gate or basic
    shape validation — refused before any handler code ever runs (§4.3/§5)."""


def _tier_model_from_environ(prefix: str) -> TierModel:
    return resolve_tier_model_from_env(prefix, error_type=ModelTierError)


class SimulatorRuntime:
    """One running simulation-mode process's state: the real PTB `Application` (real
    handlers, real background loops — `register_handlers()` reused unmodified), the
    two Telegram-network stubs, and the *real* `HttpApiClient` talking to the real
    API server, so every downstream effect (persistence writes, `/TeamStatus/
    AttendanceCheck`, ...) is genuinely real. Owns the asyncio loop's lifecycle;
    `bot.simulator_app`'s Flask thread reaches back into it via
    `asyncio.run_coroutine_threadsafe` for every request (§4.2 point 3)."""

    def __init__(self, loaded_profile: "LoadedProfile", loop: asyncio.AbstractEventLoop, api_client=None):
        """`api_client` defaults to the real `HttpApiClient` pointed at this profile's own
        API server — always the case in production (`run_simulator` never passes one).
        Tests inject a `FakeBotApiClient` here instead, so the dispatch-through-real-handlers
        path can be exercised without a live API server (§8's test plan)."""

        self.loaded_profile = loaded_profile
        self.loop = loop
        self.telegram_client = SimulatorTelegramClient()
        self.api_client = api_client or HttpApiClient(
            f"http://localhost:{loaded_profile.api_port}",
            bot_service_key=resolve_bot_service_key(),
        )
        self.deps = BotDeps(loaded_profile=loaded_profile, telegram_client=self.telegram_client, api_client=self.api_client)
        self.bot = telegram.Bot(token="simulator", request=FakeBotRequest(), get_updates_request=FakeBotRequest())
        self.application = ApplicationBuilder().bot(self.bot).build()
        register_handlers(self.application, self.deps)
        self._next_update_id = 0
        self._allowed_users = {
            simulation_user_telegram_id(persona.offset) for persona in loaded_profile.simulation_users
        }
        self._allowed_groups = {
            simulation_group_chat_id(group.offset) for group in loaded_profile.simulation_groups
        }

    async def startup(self) -> None:
        """`initialize -> post_init -> start`, in that order — the same order
        `Application.run_polling()` documents and follows internally, just invoked by
        hand since we never call `run_polling()` itself (§1.2/§4.2). `post_init`
        (`bot/app.py`'s `_post_init`, unmodified) is what starts both background loops."""

        await self.application.initialize()
        if self.application.post_init is not None:
            await self.application.post_init(self.application)
        await self.application.start()

    async def shutdown(self) -> None:
        """The mirror image of `startup()` — `stop -> post_stop -> shutdown ->
        post_shutdown`. `post_shutdown` (`bot/app.py`'s `_post_shutdown`, unmodified)
        cancels both background loops and closes the API client."""

        await self.application.stop()
        if self.application.post_stop is not None:
            await self.application.post_stop(self.application)
        await self.application.shutdown()
        if self.application.post_shutdown is not None:
            await self.application.post_shutdown(self.application)

    def _next_id(self) -> int:
        self._next_update_id += 1
        return self._next_update_id

    async def handle_message(self, payload: dict) -> dict:
        """Validate, gate, dispatch, and read back one simulated text message
        (§4.3). Raises `SimulatorRequestRefused` for anything not exactly a
        currently-declared simulation persona/group — the core "never touches real
        traffic" guarantee (§5); every other failure propagates as-is."""

        sender_identity = str(payload.get("sender_identity") or "")
        chat_id = str(payload.get("chat_id") or "")
        chat_type = payload.get("chat_type")
        text = payload.get("text")
        source_message_id = str(payload.get("source_message_id") or "")

        if sender_identity not in self._allowed_users:
            raise SimulatorRequestRefused(
                f"{sender_identity!r} is not a currently-declared SIMULATION_USERS persona for this profile"
            )
        if chat_type == "private":
            if chat_id != sender_identity:
                raise SimulatorRequestRefused("a private chat_id must equal sender_identity, as Telegram itself gives it")
        elif chat_type in ("group", "supergroup"):
            if chat_id not in self._allowed_groups:
                raise SimulatorRequestRefused(
                    f"{chat_id!r} is not a currently-declared SIMULATION_GROUPS chat_id for this profile"
                )
        else:
            raise SimulatorRequestRefused(f"chat_type must be 'private', 'group' or 'supergroup', got {chat_type!r}")
        if not text or not isinstance(text, str):
            raise SimulatorRequestRefused("text is required")
        if not source_message_id:
            raise SimulatorRequestRefused("source_message_id is required")

        mark = self.telegram_client.mark()
        update = build_synthetic_text_update(
            update_id=self._next_id(),
            source_message_id=source_message_id,
            sender_identity=sender_identity,
            chat_id=chat_id,
            chat_type=chat_type,
            text=text,
            bot=self.bot,
        )
        await self.application.process_update(update)
        reply_text = self.telegram_client.reply_since(mark, chat_id)
        return {"reply_text": reply_text, "watermark": _mark_to_dict(self.telegram_client.mark())}

    def poll_chat(self, chat_id: str, since: tuple[int, int]) -> dict:
        """Anything sent to `chat_id` since `since` (a watermark from `handle_message`
        or a previous `poll_chat`) — surfaces `run_notification_poll_loop`'s own,
        real, unmodified background deliveries (job results, held-approval/
        clarification prompts, ...) once they actually arrive, the same way a real
        Telegram user would see a second message appear in their chat (§10's
        "admin page has no way to observe an async reply" gap). Gated by the same
        identity allowlist as `handle_message` — a poll can only ever watch a
        currently-declared simulation chat, never an arbitrary string."""

        if chat_id not in self._allowed_users and chat_id not in self._allowed_groups:
            raise SimulatorRequestRefused(f"{chat_id!r} is not a currently-declared simulation chat_id for this profile")

        reply_text = self.telegram_client.reply_since(since, chat_id)
        return {"reply_text": reply_text, "watermark": _mark_to_dict(self.telegram_client.mark())}


def _mark_to_dict(mark: tuple[int, int]) -> dict:
    return {"status_len": mark[0], "sent_len": mark[1]}


def _mark_from_dict(payload: dict) -> tuple[int, int]:
    try:
        return int(payload.get("status_len", 0)), int(payload.get("sent_len", 0))
    except (TypeError, ValueError):
        raise SimulatorRequestRefused("watermark must be two integers (status_len, sent_len)") from None


def build_flask_app(runtime: SimulatorRuntime, bot_service_key: str) -> Flask:
    """The one endpoint this process serves — `POST /Simulator-msg`
    (§4.3). Runs on its own thread (`run_simulator`); every request bridges into
    `runtime.loop` via `asyncio.run_coroutine_threadsafe` and blocks for the result,
    so the HTTP response only returns once the real handler has fully finished —
    exactly the synchronous request/response shape `/Msg` itself already has."""

    app = Flask(__name__)

    def _check_service_key():
        """None if `X-Service-Key` matches; otherwise the (jsonify'd body, status) a route must return immediately."""

        provided_key = request.headers.get(SERVICE_KEY_HEADER)
        if not provided_key or provided_key != bot_service_key:
            return jsonify({"error": {"message": "missing or invalid X-Service-Key"}}), 403
        return None

    @app.route("/Simulator-msg", methods=["POST"])
    def simulator_msg():
        refused = _check_service_key()
        if refused is not None:
            return refused

        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify({"error": {"message": "request body must be a JSON object"}}), 400

        future = asyncio.run_coroutine_threadsafe(runtime.handle_message(payload), runtime.loop)
        try:
            result = future.result(timeout=120)
        except SimulatorRequestRefused as exc:
            return jsonify({"error": {"message": str(exc)}}), 403
        except Exception:
            logger.exception(
                "bot.simulator_app failed handling a simulated message",
                extra={"event": "bot_simulator_msg_failed"},
            )
            return jsonify({"error": {"message": "the simulation-mode bot process failed handling this message"}}), 500

        return jsonify(result)

    @app.route("/Simulator-msg/poll", methods=["GET"])
    def simulator_msg_poll():
        """Priority 3 (docs/work_process.md §16): lets the admin page ask "has
        anything new arrived in this chat" after the fact, so a
        `run_notification_poll_loop`-delivered async follow-up (a real, unmodified
        background delivery — job result, held-approval/clarification prompt, ...)
        actually reaches the operator instead of a dead-end promise."""

        refused = _check_service_key()
        if refused is not None:
            return refused

        chat_id = request.args.get("chat_id") or ""
        try:
            since = _mark_from_dict(
                {"status_len": request.args.get("status_len"), "sent_len": request.args.get("sent_len")}
            )
        except SimulatorRequestRefused as exc:
            return jsonify({"error": {"message": str(exc)}}), 400

        future = asyncio.run_coroutine_threadsafe(_poll_async(runtime, chat_id, since), runtime.loop)
        try:
            result = future.result(timeout=30)
        except SimulatorRequestRefused as exc:
            return jsonify({"error": {"message": str(exc)}}), 403
        except Exception:
            logger.exception(
                "bot.simulator_app failed polling a chat",
                extra={"event": "bot_simulator_poll_failed"},
            )
            return jsonify({"error": {"message": "the simulation-mode bot process failed handling this poll"}}), 500

        return jsonify(result)

    return app


async def _poll_async(runtime: SimulatorRuntime, chat_id: str, since: tuple[int, int]) -> dict:
    """`poll_chat` itself needs no `await` (it only reads `SimulatorTelegramClient`'s
    in-memory record) — wrapped as a coroutine purely so it runs on `runtime.loop`
    via the same `run_coroutine_threadsafe` bridge every other request uses,
    rather than reading that record from the Flask thread directly."""

    return runtime.poll_chat(chat_id, since)


def run_simulator(loaded_profile: "LoadedProfile") -> None:
    if not loaded_profile.simulator_port:
        raise SystemExit(
            f"profile {loaded_profile.module_path!r} does not declare SIMULATOR_PORT — "
            "there is nothing for bot.simulator_app to serve (docs/bot_simulation_mode_design.md)"
        )
    bot_service_key = resolve_bot_service_key()
    if not bot_service_key:
        raise SystemExit(
            "BOT_SERVICE_KEY is not set — bot.simulator_app refuses to start without it, since it is "
            "the only thing authenticating a POST /Simulator-msg caller (docs/bot_simulation_mode_design.md §4.3)"
        )

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    runtime = SimulatorRuntime(loaded_profile, loop)
    loop.run_until_complete(runtime.startup())

    flask_app = build_flask_app(runtime, bot_service_key)
    flask_thread = threading.Thread(
        target=lambda: flask_app.run(host="127.0.0.1", port=loaded_profile.simulator_port, use_reloader=False),
        name="bot-simulator-http",
        daemon=True,
    )
    flask_thread.start()
    logger.info(
        "bot.simulator_app started",
        extra={"event": "bot_simulator_started", "port": loaded_profile.simulator_port},
    )

    try:
        loop.run_forever()
    except KeyboardInterrupt:
        pass
    finally:
        loop.run_until_complete(runtime.shutdown())
        loop.close()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Run the simulation-mode bot process for one deployment (docs/bot_simulation_mode_design.md)."
    )
    parser.add_argument("profile_module", help="dotted module path of the profile to run, e.g. profiles.standby_squad")
    args = parser.parse_args(argv)

    try:
        core_model = _tier_model_from_environ("CORE")
        sub_model = _tier_model_from_environ("SUB")
    except ModelTierError as exc:
        raise SystemExit(f"failed to start bot.simulator_app: {exc}") from exc

    try:
        loaded_profile = load_profile(args.profile_module, core_model=core_model, sub_model=sub_model)
    except (ProfileLoadError, ProfileValidationError) as exc:
        raise SystemExit(f"failed to start bot.simulator_app: {exc}") from exc

    configure_logging(loaded_profile.module_path)

    # A separate lock path from the real bot's `.bot.lock` (docs/bot_simulation_mode_design.md
    # §4.2) — a simulation-mode process and a real bot process for the same profile answer
    # fundamentally different traffic and may run concurrently; two simulator processes for the
    # same profile still may not.
    lock = SingleInstanceLock(Path(f"{loaded_profile.db_path}.bot-simulator.lock"))
    try:
        lock.acquire()
    except BotStartupError as exc:
        raise SystemExit(str(exc)) from exc

    try:
        run_simulator(loaded_profile)
    finally:
        lock.release()


if __name__ == "__main__":
    main()
