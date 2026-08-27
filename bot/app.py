"""Telegram Frontend entry point (work_plan.md §8, chiefly §8.1).

The package's one declared entry point (docs/allowed_calls.md). Wires
together every other module in this package: loads the named profile,
resolves and validates the bot token, guards against a second instance
for the same deployment, registers every handler, and runs the polling
loop.

`bot.http_api_client.HttpApiClient` is the default `BotApiClient` built
here — real HTTP, against the profile's own `api_port` (§8.1). Every
handler below stays wrapped so that any unexpected failure reaching it
becomes a clear chat reply instead of a crash or a silently dropped
update — including `bot.startup.ApiRequestError` (a real API call that
failed) and, for any `BotApiClient` implementation that still legitimately
has no real counterpart for an operation, `ApiNotImplementedError` (see
`bot.api_client.UnimplementedApiClient`'s own docstring for when that
still applies).
"""

import argparse
import asyncio
import importlib
import logging
import os
from pathlib import Path
from typing import Awaitable, Callable

from config.base import ModelTierError, TierModel, build_tier_model
from profiles.loader import LoadedProfile, ProfileLoadError, ProfileValidationError, load_profile
from tools.logging_config import configure_logging

from bot import commands, holds
from bot.http_api_client import HttpApiClient
from bot.deps import BotDeps
from bot.notifications import NotificationCursorStore, run_notification_poll_loop
from bot.startup import ApiNotImplementedError, BotStartupError, SingleInstanceLock
from bot.telegram_client import PTBTelegramClient
from bot.users import check_permission, resolve_caller

logger = logging.getLogger(__name__)

NOTIFICATION_POLL_INTERVAL_SECONDS = 5.0

# The complete, exhaustive set of slash-commands this bot registers.
# §8.2's own explicit prohibition — no command that adds, changes,
# removes, or lists users — is what `tests/test_bot_users.py` checks
# this constant against; a third command added here without updating
# that test's expectation would fail it loudly rather than passing
# unnoticed.
REGISTERED_COMMANDS = ("profile", "settings")


def _resolve_bot_token(module_path: str, loaded_profile: LoadedProfile) -> str | None:
    """The token named by the profile's `BOT_TOKEN_ENV`, already read into
    `loaded_profile.resolved_secrets` at load time (§1.5) — this re-imports
    the (already-cached, per `importlib`) profile module only to read the
    *name* of the variable holding it, never the value itself again.

    `profiles.loader` already fails loudly, before this is ever reached, if
    the named variable is entirely unset (`os.environ.get(...) is None`).
    It does not, however, reject an empty or whitespace-only value — that
    gap is closed here, before any Telegram connection is attempted. Unlike
    the entirely-unset case, this is deliberately *not* a startup failure:
    a missing bot token must not block this process (or the separate `api`
    process — a different OS process entirely, per docs/allowed_calls.md's
    "bot calls only api... a network boundary, never a Python import") from
    coming up. Returns `None` (after logging a WARNING naming the specific
    variable) instead of raising, so the caller can skip the Telegram
    connection step without treating this as an error.
    """

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
    """Returns `None` (never raises for this specific reason) when the
    configured bot token is missing/blank — see `_resolve_bot_token`. Every
    other failure this function can hit still raises normally.

    `core_model`/`sub_model` are required, already-resolved `TierModel`s,
    threaded straight through to `load_profile` — no environment access
    anywhere in this function; `main`, below, is the one place in this
    module that decides where these values come from.
    """

    loaded_profile = load_profile(module_path, core_model=core_model, sub_model=sub_model)
    configure_logging(loaded_profile.module_path)

    bot_token = _resolve_bot_token(module_path, loaded_profile)
    if bot_token is None:
        return None

    telegram_client = PTBTelegramClient(bot_token)

    # Real HTTP, at last — §8.1's "use the profile's port when talking to
    # the API". Needs bot.api_client.BOT_SERVICE_IDENTITY provisioned in
    # this deployment's user table first (docs/api_spec.md's "Service
    # identity" section) — every call fails authentication otherwise, the
    # same as any other unregistered identity.
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
    """Wrap a handler so `ApiNotImplementedError` and any other
    unexpected exception become a clear chat reply rather than a crash —
    never a leaked stack trace, matching the spirit of §7.10's error
    contract even though this bot talks to the seam in `bot.api_client`,
    not a real API yet.
    """

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


async def handle_incoming_message(deps: BotDeps, telegram_identity: str, text: str, message_id: str) -> str:
    """Route free-form Telegram text through the single message endpoint."""

    resolution = await resolve_caller(deps.api_client, telegram_identity)
    if resolution.status == "unregistered":
        return resolution.refusal_message

    caller = resolution.caller
    refusal = check_permission(caller, "send_message")
    if refusal is not None:
        return refusal

    result = await deps.api_client.submit_message(text, telegram_identity, message_id)
    if result.kind == "question":
        return result.answer_text or "(no answer was returned)"

    lines = [f"Got it — taken as a {result.kind}."]
    if result.awaiting_approval:
        lines.append("It is now waiting for a commander's approval.")
    elif result.job_id:
        lines.append(f"Job ID: {result.job_id}. You'll hear back here once it's done.")
    return "\n".join(lines)


async def _on_text_message(update, context) -> None:
    deps: BotDeps = context.bot_data["deps"]
    telegram_identity, chat_id = _identity_and_chat_id(update)

    reply = await handle_incoming_message(deps, telegram_identity, update.message.text, str(update.message.message_id))
    await deps.telegram_client.send_text(chat_id, reply)


async def _on_callback_query(update, context) -> None:
    deps: BotDeps = context.bot_data["deps"]
    query = update.callback_query
    telegram_identity, chat_id = _identity_and_chat_id(update)

    await deps.telegram_client.answer_callback_query(query.id)

    namespace = query.data.split(":", 1)[0]

    if namespace == holds.CLARIFICATION_CALLBACK_PREFIX:
        event_id, choice = holds.parse_clarification_callback_data(query.data)
        await holds.handle_clarification_answer(deps, chat_id, telegram_identity, event_id, choice)
        return

    if namespace == holds.CALLBACK_PREFIX:
        event_id, choice = holds.parse_callback_data(query.data)
        await holds.handle_approval_answer(deps, chat_id, telegram_identity, event_id, choice)
        return

    logger.warning("unrecognized callback namespace: %s", namespace, extra={"event": "bot_unknown_callback"})


def _parse_protocol_write_command(rest: str) -> tuple[str, dict] | str:
    """Parse `"<name> | <description> | <agents,...> | <tools,...> | "
    "<expected_success_output> | <criticality> | <true|false>"` into
    (name, payload), or return an error message. Pipe-delimited because
    every field but the flag may itself contain commas or spaces.
    """

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
    """Resolve `telegram_identity` and, if unregistered, send the refusal
    reply and return `None` — the caller must then return immediately.
    Returns the resolved `CallerContext` on success.

    Required for every interaction, reads included (§8.2's "look up every
    Telegram identity... on every interaction" — reading is still an
    interaction, and "allow viewers to read" (§8.7/§8.8) names a real,
    registered permission level, not "anyone"). `/profile view`/`diff` and
    `/settings view` need no level check beyond registration itself —
    viewer is the lowest registered level and §8.7/§8.8 both grant it read
    access explicitly — so this helper only refuses the unregistered case;
    a write branch layers its own `check_permission` call on top of what
    this returns, same as before.
    """

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
        await deps.telegram_client.send_text(chat_id, await commands.view_profile(deps, caller.telegram_identity))
        return

    if args[0] == "diff":
        if await _resolve_caller_or_refuse(deps, chat_id, telegram_identity) is None:
            return
        await deps.telegram_client.send_text(chat_id, await commands.profile_diff_status(deps))
        return

    if args[0] in ("add", "edit", "remove"):
        caller = await _resolve_caller_or_refuse(deps, chat_id, telegram_identity)
        if caller is None:
            return

        action = args[0]
        rest = " ".join(args[1:])

        if action == "remove":
            reply = await commands.write_protocol(deps, caller, "remove", {"name": rest.strip()})
        else:
            parsed = _parse_protocol_write_command(rest)
            if isinstance(parsed, str):
                await deps.telegram_client.send_text(chat_id, parsed)
                return
            _, payload = parsed
            reply = await commands.write_protocol(deps, caller, action, payload)

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
        await deps.telegram_client.send_text(chat_id, await commands.view_settings(deps, caller.telegram_identity))
        return

    if args[0] == "set" and len(args) == 3:
        caller = await _resolve_caller_or_refuse(deps, chat_id, telegram_identity)
        if caller is None:
            return

        _, field, raw_value = args
        reply = await commands.change_setting(deps, caller, field, raw_value)
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

    # `Application.post_init` runs once the polling loop actually starts —
    # the earliest point at which `application.create_task` (which needs a
    # running event loop) is safe to call. This is what starts the §8.4-
    # §8.6/§8.9/§8.11/§8.12 notification poll loop alongside message
    # handling. The cursor store is built here, not earlier, since it
    # needs `deps.loaded_profile.db_path` — real only once a real profile
    # started this bot, never assumed at import time.
    async def _post_init(started_application) -> None:
        cursor_store = NotificationCursorStore(Path(f"{deps.loaded_profile.db_path}.notification_cursor"))
        started_application.create_task(run_notification_poll_loop(deps, NOTIFICATION_POLL_INTERVAL_SECONDS, cursor_store=cursor_store))

    application.post_init = _post_init


def run_bot(deps: BotDeps) -> None:
    deps.telegram_client.run_polling(lambda application: register_handlers(application, deps))


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if value is None:
        raise ModelTierError(f"required environment variable '{name}' is not set")
    return value


def _tier_model_from_environ(prefix: str) -> TierModel:
    """Read one tier's provider/model name/API key straight from the real
    process environment — `main`'s own job, the one place in this module
    `os.environ` is read for model-tier config (see config/base.py's
    module docstring). `prefix` is `"CORE"` or `"SUB"`.
    """

    provider = _require_env(f"{prefix}_MODEL_PROVIDER")
    model_name = _require_env(f"{prefix}_MODEL_NAME")
    api_key_env_name = _require_env(f"{prefix}_MODEL_API_KEY_ENV")
    api_key = _require_env(api_key_env_name)
    return build_tier_model(provider, model_name, api_key)


def main(argv: list[str] | None = None) -> None:
    """One of the three real entry points (with `api.app.main`,
    `cli.user_admin.main`) that reads `os.environ` for model-tier config —
    everything below it takes already-resolved `TierModel` values instead.
    """

    parser = argparse.ArgumentParser(description="Run the Telegram bot frontend for one deployment (work_plan.md §8).")
    parser.add_argument("profile_module", help="dotted module path of the profile to run, e.g. profiles.demo")
    args = parser.parse_args(argv)

    try:
        core_model = _tier_model_from_environ("CORE")
        sub_model = _tier_model_from_environ("SUB")
    except ModelTierError as exc:
        raise SystemExit(f"failed to start bot: {exc}") from exc

    try:
        deps = build_deps(args.profile_module, core_model=core_model, sub_model=sub_model)
    except (ProfileLoadError, ProfileValidationError) as exc:
        raise SystemExit(f"failed to start bot: {exc}") from exc

    if deps is None:
        # The bot token is missing/blank (already logged, with the specific
        # env var name, inside _resolve_bot_token). Deliberately not a
        # startup failure — no Telegram connection to make without a token,
        # so there is nothing further for this process to do, but that is
        # not an error: exit cleanly (status 0), not via SystemExit(message).
        return

    lock = SingleInstanceLock(Path(f"{deps.loaded_profile.db_path}.bot.lock"))

    try:
        lock.acquire()
    except BotStartupError as exc:
        raise SystemExit(str(exc)) from exc

    try:
        asyncio.run(_validate_bot_token(deps))
    except BotStartupError as exc:
        lock.release()
        raise SystemExit(str(exc)) from exc

    try:
        run_bot(deps)
    finally:
        lock.release()


if __name__ == "__main__":
    main()
