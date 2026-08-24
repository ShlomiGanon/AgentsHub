"""Telegram Frontend entry point (work_plan.md §8, chiefly §8.1).

The package's one declared entry point (docs/allowed_calls.md). Wires
together every other module in this package: loads the named profile,
resolves and validates the bot token, guards against a second instance
for the same deployment, registers every handler, and runs the polling
loop.

`bot.api_client.UnimplementedApiClient` is the default `BotApiClient`
built here — see that module's docstring for why, and for what a future
Mission 7 needs to replace it with. Every handler below is wrapped so
that an `ApiNotImplementedError` reaching it becomes a clear, honest chat
reply instead of a crash or a silently dropped update — this bot is
runnable against a real Telegram token today; it will simply say, for
each capability, exactly which work_plan.md §7 subtask it is waiting on.
"""

import argparse
import asyncio
import importlib
import logging
from pathlib import Path
from typing import Awaitable, Callable

from profiles.loader import LoadedProfile, ProfileLoadError, ProfileValidationError, load_profile
from tools.logging_config import configure_logging

from bot import approval, clarification, entrypoint, profile_commands, settings_commands
from bot.api_client import UnimplementedApiClient
from bot.deps import BotDeps
from bot.errors import ApiNotImplementedError, BotStartupError
from bot.notifications import run_notification_poll_loop
from bot.singleton_lock import SingleInstanceLock
from bot.telegram_client import PTBTelegramClient
from bot.users import resolve_caller

logger = logging.getLogger(__name__)

NOTIFICATION_POLL_INTERVAL_SECONDS = 5.0

# The complete, exhaustive set of slash-commands this bot registers.
# §8.2's own explicit prohibition — no command that adds, changes,
# removes, or lists users — is what `tests/test_bot_users.py` checks
# this constant against; a third command added here without updating
# that test's expectation would fail it loudly rather than passing
# unnoticed.
REGISTERED_COMMANDS = ("profile", "settings")


def _resolve_bot_token(module_path: str, loaded_profile: LoadedProfile) -> str:
    """The token named by the profile's `BOT_TOKEN_ENV`, already read into
    `loaded_profile.resolved_secrets` at load time (§1.5) — this re-imports
    the (already-cached, per `importlib`) profile module only to read the
    *name* of the variable holding it, never the value itself again.
    """

    profile_module = importlib.import_module(module_path)
    return loaded_profile.resolved_secrets[profile_module.BOT_TOKEN_ENV]


def build_deps(module_path: str) -> BotDeps:
    loaded_profile = load_profile(module_path)
    configure_logging(loaded_profile.module_path)

    bot_token = _resolve_bot_token(module_path, loaded_profile)
    telegram_client = PTBTelegramClient(bot_token)

    # TODO(Mission 7): swap for a real HTTP client (talking to
    # `loaded_profile.api_port`, per §8.1's "use the profile's port when
    # talking to the API") once §7 exists. See bot/api_client.py.
    api_client = UnimplementedApiClient()

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


async def _on_text_message(update, context) -> None:
    deps: BotDeps = context.bot_data["deps"]
    telegram_identity, chat_id = _identity_and_chat_id(update)

    reply = await entrypoint.handle_incoming_message(deps, telegram_identity, update.message.text)
    await deps.telegram_client.send_text(chat_id, reply)


async def _on_callback_query(update, context) -> None:
    deps: BotDeps = context.bot_data["deps"]
    query = update.callback_query
    telegram_identity, chat_id = _identity_and_chat_id(update)

    await deps.telegram_client.answer_callback_query(query.id)

    namespace = query.data.split(":", 1)[0]

    if namespace == clarification.CALLBACK_PREFIX:
        hold_id, choice = clarification.parse_callback_data(query.data)
        await clarification.handle_clarification_answer(deps, chat_id, telegram_identity, hold_id, choice)
        return

    if namespace == approval.CALLBACK_PREFIX:
        hold_id, choice = approval.parse_callback_data(query.data)
        await approval.handle_approval_answer(deps, chat_id, telegram_identity, hold_id, choice)
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


async def _on_profile_command(update, context) -> None:
    deps: BotDeps = context.bot_data["deps"]
    telegram_identity, chat_id = _identity_and_chat_id(update)
    args = context.args or []

    if not args or args[0] == "view":
        await deps.telegram_client.send_text(chat_id, await profile_commands.view_profile(deps))
        return

    if args[0] == "diff":
        await deps.telegram_client.send_text(chat_id, await profile_commands.profile_diff_status(deps))
        return

    if args[0] in ("add", "edit", "remove"):
        resolution = await resolve_caller(deps.api_client, telegram_identity)
        if resolution.status == "unregistered":
            await deps.telegram_client.send_text(chat_id, resolution.refusal_message)
            return

        action = args[0]
        rest = " ".join(args[1:])

        if action == "remove":
            reply = await profile_commands.write_protocol(deps, resolution.caller, "remove", {"name": rest.strip()})
        else:
            parsed = _parse_protocol_write_command(rest)
            if isinstance(parsed, str):
                await deps.telegram_client.send_text(chat_id, parsed)
                return
            _, payload = parsed
            reply = await profile_commands.write_protocol(deps, resolution.caller, action, payload)

        await deps.telegram_client.send_text(chat_id, reply)
        return

    await deps.telegram_client.send_text(chat_id, "Usage: /profile view | diff | add ... | edit ... | remove <name>")


async def _on_settings_command(update, context) -> None:
    deps: BotDeps = context.bot_data["deps"]
    telegram_identity, chat_id = _identity_and_chat_id(update)
    args = context.args or []

    if not args or args[0] == "view":
        await deps.telegram_client.send_text(chat_id, await settings_commands.view_settings(deps))
        return

    if args[0] == "set" and len(args) == 3:
        resolution = await resolve_caller(deps.api_client, telegram_identity)
        if resolution.status == "unregistered":
            await deps.telegram_client.send_text(chat_id, resolution.refusal_message)
            return

        _, field, raw_value = args
        reply = await settings_commands.change_setting(deps, resolution.caller, field, raw_value)
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
    # §8.6/§8.9/§8.11 notification poll loop alongside message handling.
    async def _post_init(started_application) -> None:
        started_application.create_task(run_notification_poll_loop(deps, NOTIFICATION_POLL_INTERVAL_SECONDS))

    application.post_init = _post_init


def run_bot(deps: BotDeps) -> None:
    deps.telegram_client.run_polling(lambda application: register_handlers(application, deps))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run the Telegram bot frontend for one deployment (work_plan.md §8).")
    parser.add_argument("profile_module", help="dotted module path of the profile to run, e.g. profiles.demo")
    args = parser.parse_args(argv)

    try:
        deps = build_deps(args.profile_module)
    except (ProfileLoadError, ProfileValidationError) as exc:
        raise SystemExit(f"failed to start bot: {exc}") from exc

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
