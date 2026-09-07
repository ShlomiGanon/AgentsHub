"""Telegram Frontend entry point (work_plan.md §8, chiefly §8.1)."""

import argparse
import asyncio
import importlib
import logging
import os
import sys
from pathlib import Path
from typing import Awaitable, Callable

from config import ModelTierError, TierModel, resolve_tier_model_from_env
from profiles.loader import LoadedProfile, ProfileLoadError, ProfileValidationError, load_profile
from tools import configure_logging, deep_debug_enabled, new_trace_id

from auth.permissions import PermissionLevel, RequestedOperation

from bot import interactions
from bot.transports import HttpApiClient, PTBTelegramClient
from bot.contracts import (
    ApiNotImplementedError,
    ApiRequestError,
    BotDeps,
    BotStartupError,
    MessageSubmissionResult,
    resolve_bot_service_key,
)
from bot.background_services import NotificationCursorStore, SingleInstanceLock, run_notification_poll_loop
from bot.interactions import check_permission, resolve_caller
from bot.presentation import replace_status

logger = logging.getLogger(__name__)

NOTIFICATION_POLL_INTERVAL_SECONDS = 5.0

REGISTERED_COMMANDS = ("profile", "settings")
_background_trace_tasks: set[asyncio.Task] = set()


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

    bot_service_key = resolve_bot_service_key()
    if not bot_service_key:
        logger.warning(
            "BOT_SERVICE_KEY is not set — every call this bot makes as its own service "
            "identity (notification delivery, the commander roster, profile-change checks, "
            "resolving a Telegram user) will be rejected by the API",
            extra={"event": "bot_service_key_missing"},
        )
    api_client = HttpApiClient(f"http://localhost:{loaded_profile.api_port}", bot_service_key=bot_service_key)

    return BotDeps(loaded_profile=loaded_profile, telegram_client=telegram_client, api_client=api_client)


_INVALID_TOKEN_MESSAGE = (
    "Telegram rejected the configured bot token — check the value of the "
    "environment variable named by BOT_TOKEN_ENV in the active profile"
)


async def _validate_bot_token(deps: BotDeps) -> None:
    """Kept as a standalone check (and directly unit-tested) — no longer called from `main()`.

    `main()` used to run this via its own `asyncio.run(...)` before `run_bot()`. That pre-check
    built/used the Telegram client's async HTTP client on a loop `asyncio.run()` then closes;
    `run_polling()` afterwards starts a *different* event loop, leaving that HTTP client bound to
    an already-closed one — `RuntimeError: Event loop is closed` / `NetworkError`. `run_polling()`'s
    own bootstrap (`Application.initialize()`) already calls `Bot.get_me()` and raises
    `telegram.error.InvalidToken` immediately (never retried, regardless of `bootstrap_retries`) if
    the token is bad, so `main()` now relies on that single event loop instead — see there.
    """
    if not await deps.telegram_client.validate_token():
        raise BotStartupError(_INVALID_TOKEN_MESSAGE)


def _identity_and_chat_id(update) -> tuple[str, str]:
    return str(update.effective_user.id), str(update.effective_chat.id)


def _bot_commands(catalog) -> list[tuple[str, str]]:
    """The bot's command menu (Telegram's native "/" picker) — one (name, description) pair per
    registered command, `/start` included even though it's also Telegram's own implicit first
    action, so it's visible in the menu too rather than only working before any message exists."""

    return [
        ("start", catalog.text("command.menu_start")),
        ("profile", catalog.text("command.menu_profile")),
        ("settings", catalog.text("command.menu_settings")),
    ]


async def _on_start_command(update, context) -> None:
    deps: BotDeps = context.bot_data["deps"]
    telegram_identity, chat_id = _identity_and_chat_id(update)
    messages = interactions.message_catalog_for(deps)

    resolution = await resolve_caller(deps.api_client, telegram_identity, messages)
    if resolution.status == "unregistered":
        await deps.telegram_client.send_text(chat_id, resolution.refusal_message)
        return

    keyboard = None
    if resolution.caller is not None:
        module_path = getattr(deps.loaded_profile, "module_path", None)
        profile_mod = sys.modules.get(module_path) if module_path else None
        if profile_mod is None and module_path:
            try:
                profile_mod = importlib.import_module(module_path)
            except Exception:
                profile_mod = None
        if resolution.caller.level == PermissionLevel.COMMANDER:
            keyboard = getattr(profile_mod, "COMMANDER_KEYBOARD", None) if profile_mod else None
        elif resolution.caller.level == PermissionLevel.VIEWER:
            keyboard = getattr(profile_mod, "VIEWER_KEYBOARD", None) if profile_mod else None

    profile_name = getattr(deps.loaded_profile, "profile_name", None) or "AgentsHub"
    welcome_text = messages.text("bot.welcome", profile_name=profile_name)
    await deps.telegram_client.send_text(chat_id, welcome_text, keyboard=keyboard)


def _guarded(handler: Callable[..., Awaitable[None]]):
    """Wrap a handler so `ApiNotImplementedError` and any other unexpected exception become a clear chat reply rather than a crash — never a leaked stack trace, matching the spirit of..."""

    async def _wrapped(update, context):
        deps = context.bot_data["deps"]
        messages = interactions.message_catalog_for(deps)
        try:
            await handler(update, context)
        except ApiNotImplementedError as exc:
            logger.info("handler blocked on unimplemented API: %s", exc, extra={"event": "bot_api_not_implemented"})
            if update.effective_chat is not None:
                await deps.telegram_client.send_text(
                    str(update.effective_chat.id),
                    messages.text("bot.not_available", reason=exc),
                )
        except Exception:
            logger.exception("unhandled error in bot handler", extra={"event": "bot_handler_failed"})
            if update.effective_chat is not None:
                await deps.telegram_client.send_text(
                    str(update.effective_chat.id),
                    messages.text("bot.handler_error"),
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

    reply, _submission = await _submit_and_format_message(
        deps,
        telegram_identity,
        text,
        message_id,
        conversation_id,
    )
    return reply


async def _submit_and_format_message(
    deps: BotDeps,
    telegram_identity: str,
    text: str,
    message_id: str,
    conversation_id: str | None,
    trace_id: str | None = None,
) -> tuple[str, MessageSubmissionResult | None]:
    """Submit one message and return both presentation text and semantic result."""

    try:
        submission_result = await deps.api_client.submit_message(
            text,
            telegram_identity,
            message_id,
            conversation_id,
            trace_id,
        )
    except ApiRequestError as exc:
        messages = interactions.message_catalog_for(deps)
        if exc.status_code == 401:
            return interactions._unregistered_message(telegram_identity, messages), None
        if exc.status_code == 403:
            return messages.text("bot.refused", message=exc.message), None
        raise
    messages = interactions.message_catalog_for(deps)
    if submission_result.awaiting_approval and submission_result.job_id:
        interactions.register_open_approval_hold(submission_result.job_id)
    if submission_result.kind in {"question", "conversational", "clarification", "event_update"}:
        return submission_result.answer_text or messages.text("bot.no_answer"), submission_result

    if submission_result.job_id:
        return messages.text("status.async_ack", task_id=submission_result.job_id), submission_result

    # Note: a truthy `submission_result.job_id` always returns above via the
    # `status.async_ack` branch, so this fallback never has a job_id to report —
    # only `awaiting_approval` (or neither) is reachable here.
    lines = [messages.text("bot.taken_as", kind=submission_result.kind)]
    if submission_result.awaiting_approval:
        lines.append(messages.text("bot.waiting_approval"))
    return "\n".join(lines), submission_result


async def _poll_live_trace(
    deps: BotDeps,
    chat_id: str,
    telegram_identity: str,
    trace_id: str,
    stop_when_idle: asyncio.Event,
) -> None:
    cursor = 0
    while True:
        stopping = stop_when_idle.is_set()
        result = await deps.api_client.poll_trace(
            trace_id,
            cursor,
            0 if stopping else 5,
            telegram_identity,
        )
        cursor = result.next_cursor
        for debug_message in result.messages:
            await deps.telegram_client.send_text(chat_id, debug_message)
        if result.terminal or stopping:
            return


def _keep_trace_task(task: asyncio.Task) -> None:
    _background_trace_tasks.add(task)

    def _finished(completed: asyncio.Task) -> None:
        _background_trace_tasks.discard(completed)
        if not completed.cancelled() and completed.exception() is not None:
            logger.warning(
                "live Deep Debug polling stopped after an error: %s",
                completed.exception(),
                extra={"event": "deep_debug_poll_failed"},
            )

    task.add_done_callback(_finished)


async def present_incoming_message(
    deps: BotDeps,
    chat_id: str,
    telegram_identity: str,
    text: str,
    message_id: str,
    conversation_id: str | None = None,
) -> str | None:
    """Present one free-form message with the shared status/edit lifecycle."""

    messages = interactions.message_catalog_for(deps)
    status_message_id = await deps.telegram_client.send_status(
        chat_id,
        messages.text("status.thinking"),
    )
    trace_id = new_trace_id()
    trace_stop = asyncio.Event()
    trace_task: asyncio.Task | None = None
    submission: MessageSubmissionResult | None = None
    try:
        if deep_debug_enabled():
            caller = await deps.api_client.resolve_user(telegram_identity)
            if caller.registered and caller.permission_level == "commander":
                trace_task = asyncio.create_task(
                    _poll_live_trace(deps, chat_id, telegram_identity, trace_id, trace_stop)
                )

        reply, submission = await _submit_and_format_message(
            deps,
            telegram_identity,
            text,
            message_id,
            conversation_id,
            trace_id,
        )
    except ApiNotImplementedError as exc:
        logger.info(
            "message request blocked on unimplemented API: %s",
            exc,
            extra={"event": "bot_api_not_implemented"},
        )
        reply = messages.text("bot.not_available", reason=exc)
    except ApiRequestError as exc:
        # "run_failure" (RunFailureError, 422) is the one API error class this codebase always
        # raises from a raw internal/model-produced string (api/routes.py wraps
        # OrchestrationParseError verbatim) rather than a deliberately-crafted, already-localized
        # catalog message — the only class genuinely unsafe to show a caller directly. Every other
        # ApiError subclass (InvalidInputError, NotFoundError, ConflictError,
        # ServiceUnavailableError, ...) is raised with real catalog text throughout this codebase
        # and stays exactly as informative as before (docs/IMPROVES/CRITICAL_FIXES_PLAN.MD item 4).
        if exc.error_class == "run_failure":
            reply = messages.text("error.run_failure_generic")
        else:
            reply = messages.text("error.request_failed", reason=exc.message)
    except Exception:
        logger.exception("unhandled error in message request", extra={"event": "bot_handler_failed"})
        reply = messages.text("bot.handler_error")
        submission = None

    await replace_status(deps.telegram_client, chat_id, status_message_id, reply)
    if trace_task is not None:
        if submission is not None and submission.job_id:
            _keep_trace_task(trace_task)
        else:
            trace_stop.set()
            try:
                await trace_task
            except Exception:
                logger.warning(
                    "live Deep Debug polling stopped after an error",
                    exc_info=True,
                    extra={"event": "deep_debug_poll_failed"},
                )
    return reply


BUTTON_PROMPTS = {
    "🛸 \u05de\u05e6\u05d1 \u05e6\u05d9 \u05e8\u05d7\u05e4\u05e0\u05d9\u05dd": "\u05de\u05d4 \u05de\u05e6\u05d1 \u05e6\u05d9 \u05d4\u05e8\u05d7\u05e4\u05e0\u05d9\u05dd \u05d5\u05d4\u05e1\u05d5\u05dc\u05dc\u05d5\u05ea \u05db\u05e8\u05d2\u05e2? \u05d4\u05e9\u05d1 \u05d1\u05e2\u05d1\u05e8\u05d9\u05ea \u05e7\u05e6\u05e8\u05d4 \u05d5\u05de\u05d1\u05e6\u05e2\u05d9\u05ea \u05d1\u05dc\u05d1\u05d3 (\u05e2\u05d3 3-4 \u05e9\u05d5\u05e8\u05d5\u05ea).",
    "🔄 \u05d4\u05d7\u05d6\u05e8\u05ea \u05e8\u05d7\u05e4\u05df \u05dc\u05d1\u05e1\u05d9\u05e1": "\u05d4\u05d7\u05d6\u05e8 \u05d0\u05ea \u05db\u05dc \u05d4\u05e8\u05d7\u05e4\u05e0\u05d9\u05dd \u05e9\u05d1\u05d0\u05d5\u05d5\u05d9\u05e8 \u05d7\u05d6\u05e8\u05d4 \u05dc\u05d1\u05e1\u05d9\u05e1.",
    "📹 \u05de\u05e6\u05d1 \u05de\u05e6\u05dc\u05de\u05d5\u05ea": "\u05de\u05d4 \u05e1\u05d8\u05d8\u05d5\u05e1 \u05de\u05e6\u05dc\u05de\u05d5\u05ea \u05d4\u05d0\u05d1\u05d8\u05d7\u05d4 \u05d1\u05db\u05dc \u05d4\u05d2\u05d6\u05e8\u05d5\u05ea? \u05d4\u05e9\u05d1 \u05d1\u05e2\u05d1\u05e8\u05d9\u05ea \u05e7\u05e6\u05e8\u05d4 \u05d5\u05de\u05d1\u05e6\u05e2\u05d9\u05ea \u05d1\u05dc\u05d1\u05d3 (\u05e2\u05d3 3-4 \u05e9\u05d5\u05e8\u05d5\u05ea).",
    "📹 \u05ea\u05e6\u05e4\u05d9\u05ea \u05d5\u05de\u05e6\u05dc\u05de\u05d5\u05ea": "\u05de\u05d4 \u05e1\u05d8\u05d8\u05d5\u05e1 \u05de\u05e6\u05dc\u05de\u05d5\u05ea \u05d4\u05d0\u05d1\u05d8\u05d7\u05d4 \u05d1\u05db\u05dc \u05d4\u05d2\u05d6\u05e8\u05d5\u05ea? \u05d4\u05e9\u05d1 \u05d1\u05e2\u05d1\u05e8\u05d9\u05ea \u05e7\u05e6\u05e8\u05d4 \u05d5\u05de\u05d1\u05e6\u05e2\u05d9\u05ea \u05d1\u05dc\u05d1\u05d3 (\u05e2\u05d3 3-4 \u05e9\u05d5\u05e8\u05d5\u05ea).",
    "📊 \u05ea\u05de\u05d5\u05e0\u05ea \u05de\u05e6\u05d1 \u05db\u05dc\u05dc\u05d9\u05ea": "\u05de\u05d4 \u05ea\u05de\u05d5\u05e0\u05ea \u05d4\u05de\u05e6\u05d1 \u05d4\u05db\u05d5\u05dc\u05dc\u05ea \u05d1\u05d2\u05d6\u05e8\u05d4? \u05e1\u05db\u05dd \u05ea\u05e6\u05e4\u05d9\u05ea (\u05de\u05e6\u05dc\u05de\u05d5\u05ea \u05d5\u05e8\u05d7\u05e4\u05e0\u05d9\u05dd) \u05d5\u05d6\u05de\u05d9\u05e0\u05d5\u05ea \u05db\u05d9\u05ea\u05ea \u05db\u05d5\u05e0\u05e0\u05d5\u05ea. \u05d4\u05e9\u05d1 \u05d1\u05e2\u05d1\u05e8\u05d9\u05ea \u05e7\u05e6\u05e8\u05d4 \u05d5\u05de\u05d1\u05e6\u05e2\u05d9\u05ea (\u05e2\u05d3 4-5 \u05e9\u05d5\u05e8\u05d5\u05ea).",
    "🌐 \u05ea\u05de\u05d5\u05e0\u05ea \u05de\u05e6\u05d1 \u05d2\u05d6\u05e8\u05ea\u05d9\u05ea \u05db\u05d5\u05dc\u05dc\u05ea": "\u05de\u05d4 \u05ea\u05de\u05d5\u05e0\u05ea \u05d4\u05de\u05e6\u05d1 \u05d4\u05db\u05d5\u05dc\u05dc\u05ea \u05d1\u05d2\u05d6\u05e8\u05d4? \u05e1\u05db\u05dd \u05ea\u05e6\u05e4\u05d9\u05ea (\u05de\u05e6\u05dc\u05de\u05d5\u05ea \u05d5\u05e8\u05d7\u05e4\u05e0\u05d9\u05dd) \u05d5\u05d6\u05de\u05d9\u05e0\u05d5\u05ea \u05db\u05d9\u05ea\u05ea \u05db\u05d5\u05e0\u05e0\u05d5\u05ea. \u05d4\u05e9\u05d1 \u05d1\u05e2\u05d1\u05e8\u05d9\u05ea \u05e7\u05e6\u05e8\u05d4 \u05d5\u05de\u05d1\u05e6\u05e2\u05d9\u05ea (\u05e2\u05d3 4-5 \u05e9\u05d5\u05e8\u05d5\u05ea).",
    "ℹ️ \u05e1\u05d8\u05d8\u05d5\u05e1 \u05d2\u05d6\u05e8\u05d4": "\u05de\u05d4 \u05ea\u05de\u05d5\u05e0\u05ea \u05d4\u05de\u05e6\u05d1 \u05d4\u05db\u05d5\u05dc\u05dc\u05ea \u05d1\u05d2\u05d6\u05e8\u05d4? \u05e1\u05db\u05dd \u05ea\u05e6\u05e4\u05d9\u05ea (\u05de\u05e6\u05dc\u05de\u05d5\u05ea \u05d5\u05e8\u05d7\u05e4\u05e0\u05d9\u05dd) \u05d5\u05d6\u05de\u05d9\u05e0\u05d5\u05ea \u05db\u05d9\u05ea\u05ea \u05db\u05d5\u05e0\u05e0\u05d5\u05ea. \u05d4\u05e9\u05d1 \u05d1\u05e2\u05d1\u05e8\u05d9\u05ea \u05e7\u05e6\u05e8\u05d4 \u05d5\u05de\u05d1\u05e6\u05e2\u05d9\u05ea (\u05e2\u05d3 4-5 \u05e9\u05d5\u05e8\u05d5\u05ea).",
    "👥 \u05e1\u05d8\u05d8\u05d5\u05e1 \u05db\u05d9\u05ea\u05ea \u05db\u05d5\u05e0\u05e0\u05d5\u05ea": "\u05de\u05d4 \u05e1\u05d8\u05d8\u05d5\u05e1 \u05db\u05d9\u05ea\u05ea \u05d4\u05db\u05d5\u05e0\u05e0\u05d5\u05ea \u05d5\u05d4\u05e0\u05d5\u05db\u05d7\u05d5\u05ea \u05db\u05e8\u05d2\u05e2? \u05d4\u05e9\u05d1 \u05d1\u05e2\u05d1\u05e8\u05d9\u05ea \u05e7\u05e6\u05e8\u05d4 \u05d5\u05de\u05d1\u05e6\u05e2\u05d9\u05ea \u05d1\u05dc\u05d1\u05d3 (\u05e2\u05d3 3-4 \u05e9\u05d5\u05e8\u05d5\u05ea).",
    "📜 \u05d4\u05d9\u05e1\u05d8\u05d5\u05e8\u05d9\u05d9\u05ea \u05d0\u05d9\u05e8\u05d5\u05e2\u05d9\u05dd": "\u05de\u05d4\u05dd \u05d4\u05d0\u05d9\u05e8\u05d5\u05e2\u05d9\u05dd \u05d4\u05d0\u05d7\u05e8\u05d5\u05e0\u05d9\u05dd \u05e9\u05e0\u05e8\u05e9\u05de\u05d5 \u05d1\u05d9\u05d5\u05de\u05df \u05d4\u05de\u05d1\u05e6\u05e2\u05d9? \u05d4\u05e9\u05d1 \u05d1\u05e2\u05d1\u05e8\u05d9\u05ea \u05e7\u05e6\u05e8\u05d4 \u05d5\u05de\u05d1\u05e6\u05e2\u05d9\u05ea \u05d1\u05dc\u05d1\u05d3 (\u05e2\u05d3 3-4 \u05e9\u05d5\u05e8\u05d5\u05ea).",
    "📋 \u05d0\u05d9\u05e8\u05d5\u05e2\u05d9\u05dd \u05d0\u05d7\u05e8\u05d5\u05e0\u05d9\u05dd": "\u05de\u05d4\u05dd \u05d4\u05d0\u05d9\u05e8\u05d5\u05e2\u05d9\u05dd \u05d4\u05d0\u05d7\u05e8\u05d5\u05e0\u05d9\u05dd \u05e9\u05e0\u05e8\u05e9\u05de\u05d5 \u05d1\u05d9\u05d5\u05de\u05df \u05d4\u05de\u05d1\u05e6\u05e2\u05d9? \u05d4\u05e9\u05d1 \u05d1\u05e2\u05d1\u05e8\u05d9\u05ea \u05e7\u05e6\u05e8\u05d4 \u05d5\u05de\u05d1\u05e6\u05e2\u05d9\u05ea \u05d1\u05dc\u05d1\u05d3 (\u05e2\u05d3 3-4 \u05e9\u05d5\u05e8\u05d5\u05ea).",
    "📋 \u05d9\u05d5\u05de\u05df \u05d0\u05d9\u05e8\u05d5\u05e2\u05d9\u05dd \u05d5\u05ea\u05d7\u05e7\u05d5\u05e8": "\u05de\u05d4\u05dd \u05d4\u05d0\u05d9\u05e8\u05d5\u05e2\u05d9\u05dd \u05d4\u05d0\u05d7\u05e8\u05d5\u05e0\u05d9\u05dd \u05e9\u05e0\u05e8\u05e9\u05de\u05d5 \u05d1\u05d9\u05d5\u05de\u05df \u05d4\u05de\u05d1\u05e6\u05e2\u05d9? \u05d4\u05e9\u05d1 \u05d1\u05e2\u05d1\u05e8\u05d9\u05ea \u05e7\u05e6\u05e8\u05d4 \u05d5\u05de\u05d1\u05e6\u05e2\u05d9\u05ea \u05d1\u05dc\u05d1\u05d3 (\u05e2\u05d3 3-4 \u05e9\u05d5\u05e8\u05d5\u05ea).",
}


async def _on_text_message(update, context) -> None:
    deps: BotDeps = context.bot_data["deps"]
    telegram_identity, chat_id = _identity_and_chat_id(update)
    messages = interactions.message_catalog_for(deps)

    resolution = await resolve_caller(deps.api_client, telegram_identity, messages)
    if resolution.status == "unregistered":
        await deps.telegram_client.send_text(chat_id, resolution.refusal_message)
        return

    incoming_text = (update.message.text or "").strip()

    approval_words = {"אישור", "אשר", "מאשר", "מאושר", "approve", "yes", "כן"}
    rejection_words = {"ביטול", "בטל", "דחה", "דחייה", "reject", "no", "לא"}
    norm_text = incoming_text.strip().lower()
    # Match if text IS an approval/rejection word OR starts with one
    # (handles e.g. "מאושר תשלח" → approved, "בטל את זה" → rejected)
    def _is_approval(txt: str) -> bool:
        return txt in approval_words or any(txt.startswith(w) for w in approval_words)
    def _is_rejection(txt: str) -> bool:
        return txt in rejection_words or any(txt.startswith(w) for w in rejection_words)
    if resolution.caller and resolution.caller.level == PermissionLevel.COMMANDER and (_is_approval(norm_text) or _is_rejection(norm_text)):
        open_holds = interactions.get_open_approval_holds()
        choice = "approved" if _is_approval(norm_text) else "rejected"
        if len(open_holds) == 1:
            hold_event_id = open_holds[0]
            await interactions.handle_approval_answer(deps, chat_id, telegram_identity, hold_event_id, choice)
            return
        elif len(open_holds) > 1:
            await deps.telegram_client.send_text(
                chat_id,
                "\u05e7\u05d9\u05d9\u05de\u05d5\u05ea \u05de\u05e1\u05e4\u05e8 \u05d1\u05e7\u05e9\u05d5\u05ea \u05d4\u05de\u05de\u05ea\u05d9\u05e0\u05d5\u05ea \u05dc\u05d0\u05d9\u05e9\u05d5\u05e8\u05da. \u05d0\u05e0\u05d0 \u05d4\u05e9\u05ea\u05de\u05e9 \u05d1\u05db\u05e4\u05ea\u05d5\u05e8\u05d9 \u05d4\u05d0\u05d9\u05e9\u05d5\u05e8/\u05d3\u05d7\u05d9\u05d9\u05d4 \u05e9\u05d1\u05d4\u05d5\u05d3\u05e2\u05ea \u05d4\u05d1\u05e7\u05e9\u05d4 \u05d4\u05de\u05ea\u05d0\u05d9\u05de\u05d4.",
            )
            return
        else:
            await deps.telegram_client.send_text(
                chat_id,
                "\u05d0\u05d9\u05df \u05db\u05e8\u05d2\u05e2 \u05e4\u05e2\u05d5\u05dc\u05d5\u05ea \u05d4\u05de\u05de\u05ea\u05d9\u05e0\u05d5\u05ea \u05dc\u05d0\u05d9\u05e9\u05d5\u05e8 \u05de\u05e4\u05e7\u05d3.",
            )
            return
    if incoming_text == "❌ \u05d0\u05d9\u05e0\u05d9 \u05d6\u05de\u05d9\u05df":
        await deps.telegram_client.send_text(
            chat_id, "\u05d0\u05e0\u05d0 \u05e6\u05d9\u05d9\u05df \u05d0\u05ea \u05e1\u05d9\u05d1\u05ea \u05d0\u05d9-\u05d4\u05d6\u05de\u05d9\u05e0\u05d5\u05ea \u05d5\u05de\u05e1\u05e4\u05e8 \u05d9\u05de\u05d9\u05dd \u05de\u05e9\u05d5\u05e2\u05e8 (\u05dc\u05d3\u05d5\u05d2\u05de\u05d4: '\u05d0\u05d9\u05e0\u05d9 \u05d6\u05de\u05d9\u05df \u05e2\u05e7\u05d1 \u05de\u05d7\u05dc\u05d4 \u05dc\u05d9\u05d5\u05de\u05d9\u05d9\u05dd')"
        )
        return

    if incoming_text == "🚀 \u05d4\u05d6\u05e0\u05e7\u05ea \u05e8\u05d7\u05e4\u05df":
        if resolution.caller and resolution.caller.level != PermissionLevel.COMMANDER:
            await deps.telegram_client.send_text(
                chat_id, "\u05e4\u05e2\u05d5\u05dc\u05d4 \u05d6\u05d5 \u05de\u05d9\u05d5\u05e2\u05d3\u05ea \u05dc\u05de\u05e4\u05e7\u05d3 \u05d1\u05dc\u05d1\u05d3."
            )
            return
        await deps.telegram_client.send_text(
            chat_id,
            "🚀 \u05dc\u05e9\u05d9\u05d2\u05d5\u05e8 \u05d5\u05d4\u05d6\u05e0\u05e7\u05ea \u05e8\u05d7\u05e4\u05df \u05d8\u05e7\u05d8\u05d9, \u05e9\u05dc\u05d7 \u05d4\u05d5\u05d3\u05e2\u05d4 \u05e2\u05dd \u05d2\u05d6\u05e8\u05ea \u05d4\u05d9\u05e2\u05d3:\n"
            "• \u05dc\u05d3\u05d5\u05d2\u05de\u05d4: '\u05e9\u05d2\u05e8 \u05e8\u05d7\u05e4\u05df \u05dc\u05e9\u05e2\u05e8 \u05e6\u05e4\u05d5\u05df'\n"
            "• \u05dc\u05d3\u05d5\u05d2\u05de\u05d4: '\u05d4\u05d6\u05e0\u05e7 \u05e8\u05d7\u05e4\u05df \u05dc\u05d2\u05d6\u05e8\u05d4 \u05d3\u05e8\u05d5\u05de\u05d9\u05ea \u05dc\u05d1\u05d3\u05d9\u05e7\u05ea \u05d7\u05e9\u05d3'\n"
            "• \u05dc\u05d3\u05d5\u05d2\u05de\u05d4: '\u05e9\u05d2\u05e8 \u05e8\u05d7\u05e4\u05df DRONE-01 \u05dc\u05d2\u05d3\u05e8 \u05de\u05d6\u05e8\u05d7\u05d9\u05ea'\n"
            "• \u05d4\u05de\u05e2\u05e8\u05db\u05ea \u05ea\u05d1\u05d7\u05e8 \u05d0\u05d5\u05d8\u05d5\u05de\u05d8\u05d9\u05ea \u05d0\u05ea \u05d4\u05e8\u05d7\u05e4\u05df \u05d4\u05de\u05ea\u05d0\u05d9\u05dd \u05d1\u05d9\u05d5\u05ea\u05e8 \u05d5\u05ea\u05d7\u05e9\u05d1 \u05d6\u05de\u05df \u05d4\u05d2\u05e2\u05d4 (ETA).",
        )
        return

    if incoming_text == "🚨 \u05d4\u05d6\u05e0\u05e7\u05ea \u05db\u05d5\u05d7\u05d5\u05ea":
        if resolution.caller and resolution.caller.level != PermissionLevel.COMMANDER:
            await deps.telegram_client.send_text(
                chat_id, "\u05e4\u05e2\u05d5\u05dc\u05d4 \u05d6\u05d5 \u05de\u05d9\u05d5\u05e2\u05d3\u05ea \u05dc\u05de\u05e4\u05e7\u05d3 \u05d1\u05dc\u05d1\u05d3."
            )
            return
        await deps.telegram_client.send_text(
            chat_id,
            "🚨 \u05dc\u05d4\u05d6\u05e0\u05e7\u05ea \u05db\u05d5\u05d7\u05d5\u05ea \u05d7\u05d9\u05e8\u05d5\u05dd \u05d5\u05d1\u05d9\u05d8\u05d7\u05d5\u05df, \u05e9\u05dc\u05d7 \u05d4\u05d5\u05d3\u05e2\u05d4 \u05e2\u05dd \u05e1\u05d5\u05d2 \u05d4\u05db\u05d5\u05d7 \u05d5\u05d4\u05d9\u05e2\u05d3:\n"
            "• \u05dc\u05d3\u05d5\u05d2\u05de\u05d4: '\u05d4\u05d6\u05e0\u05e7 \u05de\u05e9\u05d8\u05e8\u05d4 \u05dc\u05e9\u05e2\u05e8 \u05e6\u05e4\u05d5\u05df'\n"
            "• \u05dc\u05d3\u05d5\u05d2\u05de\u05d4: '\u05d4\u05d6\u05e0\u05e7 \u05de\u05d3\"\u05d0 \u05dc\u05d2\u05d6\u05e8\u05d4 \u05d3\u05e8\u05d5\u05de\u05d9\u05ea'\n"
            "• \u05dc\u05d3\u05d5\u05d2\u05de\u05d4: '\u05d4\u05d6\u05e0\u05e7 \u05db\u05d9\u05d1\u05d5\u05d9 \u05d0\u05e9 \u05dc\u05d7\u05de\"\u05dc'\n"
            "• \u05dc\u05d3\u05d5\u05d2\u05de\u05d4: '\u05d4\u05d6\u05e0\u05e7 \u05db\u05d5\u05d7 \u05e6\u05d1\u05d0\u05d9 \u05dc\u05d2\u05d3\u05e8 \u05de\u05d6\u05e8\u05d7\u05d9\u05ea'",
        )
        return

    if incoming_text == "✅ \u05d0\u05e0\u05d9 \u05d6\u05de\u05d9\u05df \u05dc\u05db\u05d5\u05e0\u05e0\u05d5\u05ea":
        incoming_text = f"\u05d3\u05d9\u05d5\u05d5\u05d7 \u05e0\u05d5\u05db\u05d7\u05d5\u05ea \u05db\u05d9\u05ea\u05ea \u05db\u05d5\u05e0\u05e0\u05d5\u05ea: \u05d4\u05de\u05e9\u05ea\u05de\u05e9 {telegram_identity} \u05d6\u05de\u05d9\u05df \u05dc\u05db\u05d5\u05e0\u05e0\u05d5\u05ea"
    elif incoming_text in BUTTON_PROMPTS:
        incoming_text = BUTTON_PROMPTS[incoming_text]

    thread_id = getattr(update.message, "message_thread_id", None)
    conversation_id = f"telegram:{chat_id}:{thread_id if thread_id is not None else 'main'}"

    async def _show_activity() -> None:
        while True:
            await deps.telegram_client.send_activity(chat_id, "typing")
            await asyncio.sleep(4.0)

    activity_task = asyncio.create_task(_show_activity())
    try:
        await present_incoming_message(
            deps,
            chat_id,
            telegram_identity,
            incoming_text,
            str(update.message.message_id),
            conversation_id,
        )
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


def _parse_protocol_write_command(rest: str, catalog=None) -> tuple[str, dict] | str:
    """Parse `"<name> | <description> | <agents,...> | <tools,...> | " "<expected_success_output> | <criticality> | <true|false>"` into (name, payload), or return an error message."""

    fields = [part.strip() for part in rest.split("|")]
    messages = catalog or interactions._catalog()
    if len(fields) != 7:
        return messages.text("protocol.expected_fields")

    name, description, agents_csv, tools_csv, expected_output, criticality, flag_text = fields

    if flag_text.lower() not in ("true", "false"):
        return messages.text("protocol.flag_boolean")

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

    resolution = await resolve_caller(
        deps.api_client, telegram_identity, interactions.message_catalog_for(deps)
    )
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
        refusal = check_permission(
            caller, RequestedOperation.VIEW_PROFILE_OVERVIEW, interactions.message_catalog_for(deps)
        )
        if refusal is not None:
            await deps.telegram_client.send_text(chat_id, refusal)
            return
        await deps.telegram_client.send_text(chat_id, await interactions.view_profile(deps, caller.telegram_identity))
        return

    if args[0] == "diff":
        caller = await _resolve_caller_or_refuse(deps, chat_id, telegram_identity)
        if caller is None:
            return
        refusal = check_permission(
            caller, RequestedOperation.VIEW_PROFILE_OVERVIEW, interactions.message_catalog_for(deps)
        )
        if refusal is not None:
            await deps.telegram_client.send_text(chat_id, refusal)
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
            parsed = _parse_protocol_write_command(rest, interactions.message_catalog_for(deps))
            if isinstance(parsed, str):
                await deps.telegram_client.send_text(chat_id, parsed)
                return
            _, payload = parsed
            reply = await interactions.write_protocol(deps, caller, action, payload)

        await deps.telegram_client.send_text(chat_id, reply)
        return

    await deps.telegram_client.send_text(
        chat_id, interactions.message_catalog_for(deps).text("command.profile_usage")
    )


async def _on_settings_command(update, context) -> None:
    deps: BotDeps = context.bot_data["deps"]
    telegram_identity, chat_id = _identity_and_chat_id(update)
    args = context.args or []

    if not args or args[0] == "view":
        caller = await _resolve_caller_or_refuse(deps, chat_id, telegram_identity)
        if caller is None:
            return
        refusal = check_permission(
            caller, RequestedOperation.VIEW_SETTINGS, interactions.message_catalog_for(deps)
        )
        if refusal is not None:
            await deps.telegram_client.send_text(chat_id, refusal)
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

    await deps.telegram_client.send_text(
        chat_id, interactions.message_catalog_for(deps).text("command.settings_usage")
    )


def register_handlers(application, deps: BotDeps) -> None:
    from telegram.ext import CallbackQueryHandler, CommandHandler, MessageHandler, filters

    application.bot_data["deps"] = deps

    assert REGISTERED_COMMANDS == ("profile", "settings")
    application.add_handler(CommandHandler("start", _guarded(_on_start_command)))
    application.add_handler(CommandHandler("menu", _guarded(_on_start_command)))
    application.add_handler(CommandHandler(REGISTERED_COMMANDS[0], _guarded(_on_profile_command)))
    application.add_handler(CommandHandler(REGISTERED_COMMANDS[1], _guarded(_on_settings_command)))
    application.add_handler(CallbackQueryHandler(_guarded(_on_callback_query)))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _guarded(_on_text_message)))

    async def _post_init(started_application) -> None:
        await deps.api_client.start()
        from telegram import BotCommand

        messages = interactions.message_catalog_for(deps)
        await started_application.bot.set_my_commands(
            [BotCommand(name, description) for name, description in _bot_commands(messages)]
        )
        cursor_store = NotificationCursorStore(Path(f"{deps.loaded_profile.db_path}.notification_cursor"))
        # If the cursor is 0 (first run / reset), fast-forward to the current
        # notification head so we don't redeliver old approval prompts from
        # previous server sessions or test runs. Only new notifications from
        # this point onward will be dispatched.
        if cursor_store.read() == 0:
            try:
                _, head_cursor = await deps.api_client.poll_pending_notifications(since=0, wait_seconds=0)
                if head_cursor > 0:
                    cursor_store.write(head_cursor)
                    logger.info(
                        "notification cursor initialized to head=%d (skipping old notifications)",
                        head_cursor,
                        extra={"event": "notification_cursor_init"},
                    )
            except Exception as exc:
                logger.warning("could not initialize notification cursor: %s", exc)
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

    # Token validity is verified by run_polling()'s own bootstrap (Application.initialize()
    # calls Bot.get_me()) rather than by a separate asyncio.run(_validate_bot_token(...))
    # pre-check here — see _validate_bot_token's docstring for why running that in its own
    # event loop before run_polling() breaks the Telegram client's async HTTP client.
    from telegram.error import InvalidToken

    try:
        run_bot(bot_dependencies)
    except InvalidToken as exc:
        raise SystemExit(_INVALID_TOKEN_MESSAGE) from exc
    finally:
        lock.release()


if __name__ == "__main__":
    main()
