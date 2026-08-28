"""Telegram Frontend entry point (work_plan.md §8, chiefly §8.1)."""

import argparse
import asyncio
import importlib
import logging
import os
from pathlib import Path
from typing import Awaitable, Callable

from config import ModelTierError, TierModel, resolve_tier_model_from_env
from profiles.loader import LoadedProfile, ProfileLoadError, ProfileValidationError, load_profile
from tools import configure_logging

from bot import interactions
from bot.transports import HttpApiClient, PTBTelegramClient
from bot.contracts import ApiNotImplementedError, ApiRequestError, BotDeps, BotStartupError
from bot.background_services import NotificationCursorStore, SingleInstanceLock, run_notification_poll_loop
from bot.interactions import check_permission, resolve_caller

logger = logging.getLogger(__name__)

NOTIFICATION_POLL_INTERVAL_SECONDS = 5.0

REGISTERED_COMMANDS = ("profile", "settings")


def _resolve_bot_token(module_path: str, loaded_profile: LoadedProfile) -> str | None:
    """The token named by the profile's `BOT_TOKEN_ENV`, already read into `loaded_profile.resolved_secrets` at load time (§1.5) — this re-imports the (already-cached, per `importlib`)..."""

    profile_module = importlib.import_module(module_path)
    token_env_name = profile_module.BOT_TOKEN_ENV
    token = loaded_profile.resolved_secrets[token_env_name]

    if not token.strip():
        logger.warning(
            f"Bot token not found: environment variable {token_env_name}, as configured in "
            "BOT_TOKEN_ENV, is not set — Telegram connection skipped",
            extra={"event": "bot_token_missing", "env_var": token_env_name},
        )
        return None

    return token


def build_deps(module_path: str, core_model: TierModel, sub_model: TierModel) -> BotDeps | None:
    """Returns `None` (never raises for this specific reason) when the configured bot token is missing/blank — see `_resolve_bot_token`."""

    loaded_profile = load_profile(module_path, core_model=core_model, sub_model=sub_model)
    configure_logging(loaded_profile.module_path)

    bot_token = _resolve_bot_token(module_path, loaded_profile)
    if bot_token is None:
        return None

    telegram_client = PTBTelegramClient(bot_token)

    api_client = HttpApiClient(f"http://localhost:{loaded_profile.api_port}")

    return BotDeps(loaded_profile=loaded_profile, telegram_client=telegram_client, api_client=api_client)


async def _validate_bot_token(deps: BotDeps) -> None:
    if not await deps.telegram_client.validate_token():
        raise BotStartupError(
            "Telegram rejected the configured bot token — check the value of the "
            "environment variable named by BOT_TOKEN_ENV in the active profile"
        )


def _identity_and_chat_id(update) -> tuple[str, str]:
    return str(update.effective_user.id), str(update.effective_chat.id)


def _guarded(handler: Callable[..., Awaitable[None]]):
    """Wrap a handler so `ApiNotImplementedError` and any other unexpected exception become a clear chat reply rather than a crash — never a leaked stack trace, matching the spirit of..."""

    async def _wrapped(update, context):
        try:
            await handler(update, context)
        except ApiNotImplementedError as exc:
            logger.info("handler blocked on unimplemented API: %s", exc, extra={"event": "bot_api_not_implemented"})
            if update.effective_chat is not None:
                await context.bot_data["deps"].telegram_client.send_text(
                    str(update.effective_chat.id),
                    f"This isn't available yet: {exc}",
                )
        except Exception:
            logger.exception("unhandled error in bot handler", extra={"event": "bot_handler_failed"})
            if update.effective_chat is not None:
                await context.bot_data["deps"].telegram_client.send_text(
                    str(update.effective_chat.id),
                    "Something went wrong handling that. It has been logged.",
                )

    return _wrapped


async def handle_incoming_message(
    deps: BotDeps,
    telegram_identity: str,
    text: str,
    message_id: str,
    conversation_id: str | None = None,
) -> str:
    """Route free-form Telegram text through the single message endpoint."""

    try:
        submission_result = await deps.api_client.submit_message(text, telegram_identity, message_id, conversation_id)
    except ApiRequestError as exc:
        if exc.status_code == 401:
            return interactions._unregistered_message(telegram_identity)
        if exc.status_code == 403:
            return f"Refused: {exc.message}"
        raise
    if submission_result.kind in {"question", "conversational", "clarification"}:
        return submission_result.answer_text or "(no answer was returned)"

    lines = [f"Got it — taken as a {submission_result.kind}."]
    if submission_result.awaiting_approval:
        lines.append("It is now waiting for a commander's approval.")
    elif submission_result.job_id:
        lines.append(f"Job ID: {submission_result.job_id}. You'll hear back here once it's done.")
    return "\n".join(lines)


async def _on_text_message(update, context) -> None:
    deps: BotDeps = context.bot_data["deps"]
    telegram_identity, chat_id = _identity_and_chat_id(update)

    thread_id = getattr(update.message, "message_thread_id", None)
    conversation_id = f"telegram:{chat_id}:{thread_id if thread_id is not None else 'main'}"

    async def _show_activity() -> None:
        while True:
            await deps.telegram_client.send_activity(chat_id, "typing")
            await asyncio.sleep(4.0)

    activity_task = asyncio.create_task(_show_activity())
    try:
        reply = await handle_incoming_message(
            deps, telegram_identity, update.message.text, str(update.message.message_id), conversation_id
        )
        await deps.telegram_client.send_text(chat_id, reply)
    finally:
        activity_task.cancel()


async def _on_callback_query(update, context) -> None:
    deps: BotDeps = context.bot_data["deps"]
    query = update.callback_query
    telegram_identity, chat_id = _identity_and_chat_id(update)

    await deps.telegram_client.answer_callback_query(query.id)

    namespace = query.data.split(":", 1)[0]

    if namespace == interactions.CLARIFICATION_CALLBACK_PREFIX:
        event_id, choice = interactions.parse_clarification_callback_data(query.data)
        await interactions.handle_clarification_answer(deps, chat_id, telegram_identity, event_id, choice)
        return

    if namespace == interactions.CALLBACK_PREFIX:
        event_id, choice = interactions.parse_callback_data(query.data)
        await interactions.handle_approval_answer(deps, chat_id, telegram_identity, event_id, choice)
        return

    logger.warning("unrecognized callback namespace: %s", namespace, extra={"event": "bot_unknown_callback"})


def _parse_protocol_write_command(rest: str) -> tuple[str, dict] | str:
    """Parse `"<name> | <description> | <agents,...> | <tools,...> | " "<expected_success_output> | <criticality> | <true|false>"` into (name, payload), or return an error message."""

    fields = [part.strip() for part in rest.split("|")]
    if len(fields) != 7:
        return (
            "Refused: expected 7 pipe-separated fields — name | description | "
            "participating_agents (comma-separated) | approved_tools (comma-separated) | "
            "expected_success_output | criticality | approval_flag (true/false)."
        )

    name, description, agents_csv, tools_csv, expected_output, criticality, flag_text = fields

    if flag_text.lower() not in ("true", "false"):
        return "Refused: 'approval_flag' must be exactly 'true' or 'false'."

    payload = {
        "name": name,
        "description": description,
        "participating_agents": [a.strip() for a in agents_csv.split(",") if a.strip()],
        "approved_tools": [t.strip() for t in tools_csv.split(",") if t.strip()],
        "expected_success_output": expected_output,
        "criticality": criticality,
        "approval_flag": flag_text.lower() == "true",
    }
    return name, payload


async def _resolve_caller_or_refuse(deps: BotDeps, chat_id: str, telegram_identity: str):
    """Resolve `telegram_identity` and, if unregistered, send the refusal reply and return `None` — the caller must then return immediately."""

    resolution = await resolve_caller(deps.api_client, telegram_identity)
    if resolution.status == "unregistered":
        await deps.telegram_client.send_text(chat_id, resolution.refusal_message)
        return None

    return resolution.caller


async def _on_profile_command(update, context) -> None:
    deps: BotDeps = context.bot_data["deps"]
    telegram_identity, chat_id = _identity_and_chat_id(update)
    args = context.args or []

    if not args or args[0] == "view":
        caller = await _resolve_caller_or_refuse(deps, chat_id, telegram_identity)
        if caller is None:
            return
        await deps.telegram_client.send_text(chat_id, await interactions.view_profile(deps, caller.telegram_identity))
        return

    if args[0] == "diff":
        if await _resolve_caller_or_refuse(deps, chat_id, telegram_identity) is None:
            return
        await deps.telegram_client.send_text(chat_id, await interactions.profile_diff_status(deps))
        return

    if args[0] in ("add", "edit", "remove"):
        caller = await _resolve_caller_or_refuse(deps, chat_id, telegram_identity)
        if caller is None:
            return

        action = args[0]
        rest = " ".join(args[1:])

        if action == "remove":
            reply = await interactions.write_protocol(deps, caller, "remove", {"name": rest.strip()})
        else:
            parsed = _parse_protocol_write_command(rest)
            if isinstance(parsed, str):
                await deps.telegram_client.send_text(chat_id, parsed)
                return
            _, payload = parsed
            reply = await interactions.write_protocol(deps, caller, action, payload)

        await deps.telegram_client.send_text(chat_id, reply)
        return

    await deps.telegram_client.send_text(chat_id, "Usage: /profile view | diff | add ... | edit ... | remove <name>")


async def _on_settings_command(update, context) -> None:
    deps: BotDeps = context.bot_data["deps"]
    telegram_identity, chat_id = _identity_and_chat_id(update)
    args = context.args or []

    if not args or args[0] == "view":
        caller = await _resolve_caller_or_refuse(deps, chat_id, telegram_identity)
        if caller is None:
            return
        await deps.telegram_client.send_text(chat_id, await interactions.view_settings(deps, caller.telegram_identity))
        return

    if args[0] == "set" and len(args) == 3:
        caller = await _resolve_caller_or_refuse(deps, chat_id, telegram_identity)
        if caller is None:
            return

        _, field, raw_value = args
        reply = await interactions.change_setting(deps, caller, field, raw_value)
        await deps.telegram_client.send_text(chat_id, reply)
        return

    await deps.telegram_client.send_text(chat_id, "Usage: /settings view | set <retry_count|risk_threshold|lookback_window_days> <value>")


def register_handlers(application, deps: BotDeps) -> None:
    from telegram.ext import CallbackQueryHandler, CommandHandler, MessageHandler, filters

    application.bot_data["deps"] = deps

    assert REGISTERED_COMMANDS == ("profile", "settings")
    application.add_handler(CommandHandler(REGISTERED_COMMANDS[0], _guarded(_on_profile_command)))
    application.add_handler(CommandHandler(REGISTERED_COMMANDS[1], _guarded(_on_settings_command)))
    application.add_handler(CallbackQueryHandler(_guarded(_on_callback_query)))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _guarded(_on_text_message)))

    async def _post_init(started_application) -> None:
        await deps.api_client.start()
        cursor_store = NotificationCursorStore(Path(f"{deps.loaded_profile.db_path}.notification_cursor"))
        started_application.create_task(run_notification_poll_loop(deps, NOTIFICATION_POLL_INTERVAL_SECONDS, cursor_store=cursor_store))

    application.post_init = _post_init

    async def _post_shutdown(_stopped_application) -> None:
        await deps.api_client.close()

    application.post_shutdown = _post_shutdown


def run_bot(deps: BotDeps) -> None:
    deps.telegram_client.run_polling(lambda application: register_handlers(application, deps))


def _tier_model_from_environ(prefix: str) -> TierModel:
    """Read one tier's provider/model name/API key straight from the real process environment — `main`'s own job, the one place in this module `os.environ` is read for model-tier confi..."""

    return resolve_tier_model_from_env(prefix, error_type=ModelTierError)


def main(argv: list[str] | None = None) -> None:
    """One of the three real entry points (with `api.app.main`, `cli.user_admin.main`) that reads `os.environ` for model-tier config — everything below it takes already-resolved `TierM..."""

    parser = argparse.ArgumentParser(description="Run the Telegram bot frontend for one deployment (work_plan.md §8).")
    parser.add_argument("profile_module", help="dotted module path of the profile to run, e.g. profiles.demo")
    args = parser.parse_args(argv)

    try:
        core_model = _tier_model_from_environ("CORE")
        sub_model = _tier_model_from_environ("SUB")
    except ModelTierError as exc:
        raise SystemExit(f"failed to start bot: {exc}") from exc

    try:
        bot_dependencies = build_deps(args.profile_module, core_model=core_model, sub_model=sub_model)
    except (ProfileLoadError, ProfileValidationError) as exc:
        raise SystemExit(f"failed to start bot: {exc}") from exc

    if bot_dependencies is None:
        return

    lock = SingleInstanceLock(Path(f"{bot_dependencies.loaded_profile.db_path}.bot.lock"))

    try:
        lock.acquire()
    except BotStartupError as exc:
        raise SystemExit(str(exc)) from exc

    try:
        asyncio.run(_validate_bot_token(bot_dependencies))
    except BotStartupError as exc:
        lock.release()
        raise SystemExit(str(exc)) from exc

    try:
        run_bot(bot_dependencies)
    finally:
        lock.release()


if __name__ == "__main__":
    main()
