"""Telegram Frontend entry point (work_plan.md §8, chiefly §8.1)."""

import argparse
import asyncio
import importlib
import logging
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable

from config import ModelTierError, TierModel, resolve_tier_model_from_env
from profiles.loader import LoadedProfile, ProfileLoadError, ProfileValidationError, load_profile
from tools import configure_logging, deep_debug_enabled, new_trace_id

from auth.permissions import PermissionLevel, RequestedOperation
from auth.permissions import InvalidFullNameError, normalize_full_name

from bot import interactions
from bot.transports import HttpApiClient, PTBTelegramClient
from bot.contracts import (
    ApiNotImplementedError,
    ApiRequestError,
    BotDeps,
    BotStartupError,
    GroupBindingView,
    MessageSubmissionResult,
    resolve_bot_service_key,
)
from bot.background_services import (
    ATTENDANCE_CALLBACK_PREFIX,
    NotificationCursorStore,
    SingleInstanceLock,
    run_attendance_check_loop,
    run_notification_poll_loop,
)
from bot.interactions import check_permission, resolve_caller
from bot.presentation import replace_status

logger = logging.getLogger(__name__)

NOTIFICATION_POLL_INTERVAL_SECONDS = 5.0
ATTENDANCE_CHECK_INTERVAL_SECONDS = 60.0

REGISTERED_COMMANDS = ("profile", "settings")
_background_trace_tasks: set[asyncio.Task] = set()

_USER_ROLE_CACHE: dict[tuple[int, str], tuple[interactions.UserResolutionResult, float]] = {}
_USER_ROLE_CACHE_TTL_SECONDS = 60.0


@dataclass
class _PendingNameAction:
    handler: Callable[..., Awaitable[None]]
    update: object
    context: object


_PENDING_NAME_ACTIONS: dict[str, _PendingNameAction] = {}

# Telegram's own chat.type values for multi-member chats. Mirrors
# orchestrator.group_routing.GROUP_CHAT_TYPES (bot may not import orchestrator).
GROUP_CHAT_TYPES = frozenset({"group", "supergroup"})

# chat_id -> binding, plus the moment the whole table was fetched. One GET /Groups
# per TTL per api client, not one per message; a group bound in the admin panel
# becomes visible here within the TTL.
_GROUP_BINDINGS_CACHE: dict[int, tuple[dict[str, GroupBindingView], float]] = {}
_GROUP_BINDINGS_CACHE_TTL_SECONDS = 60.0


def clear_caller_cache() -> None:
    """Clear in-memory caller resolution cache (for test isolation or admin resets)."""
    _USER_ROLE_CACHE.clear()
    _GROUP_BINDINGS_CACHE.clear()
    _PENDING_NAME_ACTIONS.clear()


async def _group_binding_cached(api_client, chat_id: str) -> GroupBindingView | None:
    """The binding for `chat_id`, from a TTL-cached copy of the server's routing table."""

    now = time.monotonic()
    key = id(api_client)
    cached = _GROUP_BINDINGS_CACHE.get(key)
    if cached is None or now >= cached[1]:
        bindings = {binding.chat_id: binding for binding in await api_client.list_groups()}
        _GROUP_BINDINGS_CACHE[key] = (bindings, now + _GROUP_BINDINGS_CACHE_TTL_SECONDS)
    else:
        bindings = cached[0]
    return bindings.get(str(chat_id))


def _chat_type(update) -> str:
    chat = getattr(update, "effective_chat", None)
    return str(getattr(chat, "type", None) or "private")


async def _group_is_handled(deps: BotDeps, update) -> bool:
    """False when the update comes from a group the server has no binding for — the bot stays silent there (and logs), by design."""

    chat_type = _chat_type(update)
    if chat_type not in GROUP_CHAT_TYPES:
        return True
    chat_id = str(update.effective_chat.id)
    binding = await _group_binding_cached(deps.api_client, chat_id)
    if binding is None:
        logger.info(
            "ignoring update from unregistered telegram group",
            extra={"event": "bot_group_unregistered", "chat_id": chat_id, "chat_type": chat_type},
        )
        return False
    return True


async def _resolve_caller_cached(
    api_client, telegram_identity: str, messages
) -> interactions.UserResolutionResult:
    """Resolve caller role with in-memory TTL caching to eliminate redundant GET /User round-trips."""
    now = time.monotonic()
    key = (id(api_client), telegram_identity)
    cached = _USER_ROLE_CACHE.get(key)
    if cached is not None:
        resolution, expires_at = cached
        if now < expires_at:
            return resolution

    resolution = await resolve_caller(api_client, telegram_identity, messages)
    ttl = _USER_ROLE_CACHE_TTL_SECONDS if resolution.status == "ok" else 5.0
    _USER_ROLE_CACHE[key] = (resolution, now + ttl)
    return resolution


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

    if not await _group_is_handled(deps, update):
        return
    resolution = await _resolve_caller_cached(deps.api_client, telegram_identity, messages)
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


async def _gate_on_full_name(handler, update, context) -> bool:
    """Return True after handling a missing-name interaction, so the handler must stop."""

    deps: BotDeps = context.bot_data["deps"]
    messages = interactions.message_catalog_for(deps)
    if not await _group_is_handled(deps, update):
        return True
    telegram_identity, chat_id = _identity_and_chat_id(update)
    resolution = await _resolve_caller_cached(deps.api_client, telegram_identity, messages)
    if resolution.status != "ok" or resolution.full_name:
        return False

    pending = _PENDING_NAME_ACTIONS.get(telegram_identity)
    candidate = getattr(getattr(update, "message", None), "text", None)
    if pending is not None and isinstance(candidate, str):
        try:
            full_name = normalize_full_name(candidate)
        except InvalidFullNameError:
            await deps.telegram_client.send_text(chat_id, messages.text("bot.full_name_invalid"))
            return True
        await deps.api_client.update_own_full_name(telegram_identity, full_name)
        _PENDING_NAME_ACTIONS.pop(telegram_identity, None)
        _USER_ROLE_CACHE.pop((id(deps.api_client), telegram_identity), None)
        await deps.telegram_client.send_text(chat_id, messages.text("bot.full_name_saved", name=full_name))
        await pending.handler(pending.update, pending.context)
        return True

    if pending is None:
        _PENDING_NAME_ACTIONS[telegram_identity] = _PendingNameAction(handler, update, context)
    query = getattr(update, "callback_query", None)
    if query is not None:
        try:
            await deps.telegram_client.answer_callback_query(query.id)
        except Exception:
            logger.warning("could not acknowledge callback while collecting name", exc_info=True)
    await deps.telegram_client.send_text(chat_id, messages.text("bot.full_name_prompt"))
    return True


def _guarded(handler: Callable[..., Awaitable[None]], *, require_full_name: bool = True):
    """Wrap a handler so `ApiNotImplementedError` and any other unexpected exception become a clear chat reply rather than a crash — never a leaked stack trace, matching the spirit of..."""

    async def _wrapped(update, context):
        deps = context.bot_data["deps"]
        messages = interactions.message_catalog_for(deps)
        try:
            if require_full_name and await _gate_on_full_name(handler, update, context):
                return
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
    event_data_event_id: str | None = None,
) -> str:
    """Route free-form Telegram text through the single message endpoint."""

    reply, _submission = await _submit_and_format_message(
        deps,
        telegram_identity,
        text,
        message_id,
        conversation_id,
        event_data_event_id=event_data_event_id,
    )
    return reply


async def _submit_and_format_message(
    deps: BotDeps,
    telegram_identity: str,
    text: str,
    message_id: str,
    conversation_id: str | None,
    trace_id: str | None = None,
    event_data_event_id: str | None = None,
    protocol_hint: str | None = None,
    telegram_chat_id: str | None = None,
    telegram_chat_type: str | None = None,
) -> tuple[str, MessageSubmissionResult | None]:
    """Submit one message and return both presentation text and semantic result."""

    try:
        submission_result = await deps.api_client.submit_message(
            text,
            telegram_identity,
            message_id,
            conversation_id,
            trace_id,
            event_data_event_id,
            protocol_hint,
            telegram_chat_id=telegram_chat_id,
            telegram_chat_type=telegram_chat_type,
        )
    except ApiRequestError as exc:
        messages = interactions.message_catalog_for(deps)
        if event_data_event_id is not None:
            interactions.unregister_event_data_reply_target(event_data_event_id)
        if exc.status_code == 401:
            return interactions._unregistered_message(telegram_identity, messages), None
        if exc.status_code == 403:
            return messages.text("bot.refused", message=exc.message), None
        raise
    messages = interactions.message_catalog_for(deps)
    if event_data_event_id is not None:
        interactions.unregister_event_data_reply_target(event_data_event_id)
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
    event_data_event_id: str | None = None,
    protocol_hint: str | None = None,
    telegram_chat_type: str | None = None,
) -> str | None:
    """Present one free-form message with the shared status/edit lifecycle.

    `telegram_chat_type` (Telegram's `chat.type`) is forwarded with `chat_id` so
    the server can scope a bound group's message; None means "not known", which
    the server treats like a private chat."""

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
            caller_res = await _resolve_caller_cached(deps.api_client, telegram_identity, messages)
            if caller_res.caller and caller_res.caller.level == PermissionLevel.COMMANDER:
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
            event_data_event_id,
            protocol_hint,
            telegram_chat_id=chat_id if telegram_chat_type is not None else None,
            telegram_chat_type=telegram_chat_type,
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


# Pending attendance replies are scoped to both the chat and the authenticated
# Telegram identity.  This matters in group chats, where a chat-only key lets
# one member accidentally complete another member's report.
_PENDING_UNAVAILABILITY: dict[tuple[str, str], dict[str, str | None]] = {}


def _extract_unavailable_days(text: str) -> int | None:
    """Extract a small, positive day count without involving the LLM."""

    match = re.search(r"\b(\d{1,3})\b", text)
    if match:
        days = int(match.group(1))
        return days if days > 0 else None

    normalized = text.casefold()
    word_values = {
        "one": 1,
        "two": 2,
        "three": 3,
        "four": 4,
        "five": 5,
        "\u05d9\u05d5\u05dd": 1,
        "\u05dc\u05d9\u05d5\u05dd": 1,
        "\u05d9\u05d5\u05de\u05d9\u05d9\u05dd": 2,
        "\u05dc\u05d9\u05d5\u05de\u05d9\u05d9\u05dd": 2,
        "\u05e9\u05dc\u05d5\u05e9\u05d4": 3,
        "\u05d0\u05e8\u05d1\u05e2\u05d4": 4,
        "\u05d7\u05de\u05d9\u05e9\u05d4": 5,
    }
    for word, value in word_values.items():
        if word in normalized:
            return value
    return None

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
    "📋 \u05d9\u05d5\u05de\u05df \u05d0\u05d9\u05e8\u05d5\u05e2\u05d9\u05dd \u05d5\u05ea\u05d7\u05e7\u05d5\u05e8": "\u05de\u05d4\u05dd \u05d4\u05d0\u05d9\u05e8\u05d5\u05e2\u05d9\u05dd \u05d4\u05d0\u05d7\u05e8\u05d5\u05e0\u05d9\u05dd \u05e9\u05e0\u05e8\u05e9\u05de\u05d5 \u05d1\u05d9\u05d5\u05de\u05df \u05d4\u05de\u05d1\u05e6\u05e2\u05d9? \u05d4\u05e9\u05d1 \u05d1\u05e2\u05d1\u05e8\u05d9\u05ea \u05e7\u05e6\u05e8\u05d4 \u05d5\u05de\u05d1\u05e6\u05e2\u05d9\u05ea \u05d1\u05dc\u05d1\u05d35e2\u05d9\u05ea \u05d1\u05dc\u05d1\u05d3 (\u05e2\u05d3 3-4 \u05e9\u05d5\u05e8\u05d5\u05ea).",
}

BUTTON_PROTOCOL_HINTS = {
    "🛸 \u05de\u05e6\u05d1 \u05e6\u05d9 \u05e8\u05d7\u05e4\u05e0\u05d9\u05dd": "query_drone_fleet_status",
    "🔄 \u05d4\u05d7\u05d6\u05e8\u05ea \u05e8\u05d7\u05e4\u05df \u05dc\u05d1\u05e1\u05d9\u05e1": "recall_drone_to_base",
    "📹 \u05de\u05e6\u05d1 \u05de\u05e6\u05dc\u05de\u05d5\u05ea": "query_camera_status",
    "📹 \u05ea\u05e6\u05e4\u05d9\u05ea \u05d5\u05de\u05e6\u05dc\u05de\u05d5\u05ea": "query_camera_status",
    "📊 \u05ea\u05de\u05d5\u05e0\u05ea \u05de\u05e6\u05d1 \u05db\u05dc\u05dc\u05d9\u05ea": "overall_situational_picture",
    "🌐 \u05ea\u05de\u05d5\u05e0\u05ea \u05de\u05e6\u05d1 \u05d2\u05d6\u05e8\u05ea\u05d9\u05ea \u05db\u05d5\u05dc\u05dc\u05ea": "overall_situational_picture",
    "ℹ️ \u05e1\u05d8\u05d8\u05d5\u05e1 \u05d2\u05d6\u05e8\u05d4": "overall_situational_picture",
    "👥 \u05e1\u05d8\u05d8\u05d5\u05e1 \u05db\u05d9\u05ea\u05ea \u05db\u05d5\u05e0\u05e0\u05d5\u05ea": "report_team_availability",
    "📜 \u05d4\u05d9\u05e1\u05d8\u05d5\u05e8\u05d9\u05d9\u05ea \u05d0\u05d9\u05e8\u05d5\u05e2\u05d9\u05dd": "query_historical_incidents",
    "📋 \u05d0\u05d9\u05e8\u05d5\u05e2\u05d9\u05dd \u05d0\u05d7\u05e8\u05d5\u05e0\u05d9\u05dd": "query_historical_incidents",
    "📋 \u05d9\u05d5\u05de\u05df \u05d0\u05d9\u05e8\u05d5\u05e2\u05d9\u05dd \u05d5\u05ea\u05d7\u05e7\u05d5\u05e8": "query_historical_incidents",
}

QUEUE_SHORTCUT_PHRASES = {
    "\u23f3 \u05ea\u05d5\u05e8 \u05d0\u05d9\u05e9\u05d5\u05e8\u05d9\u05dd",  # Approvals queue with hourglass
    "\u05ea\u05d5\u05e8 \u05d0\u05d9\u05e9\u05d5\u05e8\u05d9\u05dd",          # Approvals queue
    "\u05d0\u05d9\u05e9\u05d5\u05e8\u05d9\u05dd",                              # Approvals
    "\u05de\u05d4 \u05de\u05de\u05ea\u05d9\u05df \u05dc\u05d0\u05d9\u05e9\u05d5\u05e8",  # What is awaiting approval
    "approvals queue",
    "approvals",
    "pending approvals",
}


async def _on_text_message(update, context) -> None:
    deps: BotDeps = context.bot_data["deps"]
    telegram_identity, chat_id = _identity_and_chat_id(update)
    messages = interactions.message_catalog_for(deps)
    chat_type = _chat_type(update)

    if not await _group_is_handled(deps, update):
        return
    resolution = await _resolve_caller_cached(deps.api_client, telegram_identity, messages)
    if resolution.status == "unregistered":
        await deps.telegram_client.send_text(chat_id, resolution.refusal_message)
        return

    incoming_text = (update.message.text or "").strip()
    if (
        incoming_text in QUEUE_SHORTCUT_PHRASES
        or incoming_text.casefold() in QUEUE_SHORTCUT_PHRASES
    ):
        if resolution.caller and resolution.caller.level != PermissionLevel.COMMANDER:
            await deps.telegram_client.send_text(
                chat_id, messages.text("bot.commander_only")
            )
            return
        await interactions.present_pending_approvals_queue(deps, chat_id, telegram_identity)
        return

    attendance_key = (chat_id, telegram_identity)
    available_button = incoming_text == "\u2705 \u05d0\u05e0\u05d9 \u05d6\u05de\u05d9\u05df \u05dc\u05db\u05d5\u05e0\u05e0\u05d5\u05ea"
    unavailable_button = incoming_text == "\u274c \u05d0\u05d9\u05e0\u05d9 \u05d6\u05de\u05d9\u05df"
    is_attendance_submission = False

    exact_approval_words = {
        "\u05d0\u05d9\u05e9\u05d5\u05e8", "\u05d0\u05e9\u05e8", "\u05de\u05d0\u05e9\u05e8",
        "\u05de\u05d0\u05d5\u05e9\u05e8", "approve", "yes", "\u05db\u05df",
    }
    prefix_approval_words = (
        "\u05de\u05d0\u05d5\u05e9\u05e8 ", "\u05de\u05d0\u05e9\u05e8 ", "\u05d0\u05e9\u05e8 ",
        "\u05d0\u05d9\u05e9\u05d5\u05e8 ", "approve ",
        "\u05db\u05df \u05d0\u05e9\u05e8", "\u05db\u05df \u05ea\u05d0\u05e9\u05e8",
        "\u05db\u05df, \u05d0\u05e9\u05e8", "\u05db\u05df, \u05ea\u05d0\u05e9\u05e8",
        "\u05db\u05df \u05dc\u05e9\u05d2\u05e8", "\u05de\u05d0\u05d5\u05e9\u05e8 \u05ea\u05e9\u05dc\u05d7",
        "\u05de\u05d0\u05d5\u05e9\u05e8 \u05dc\u05e9\u05dc\u05d5\u05d7",
    )
    exact_rejection_words = {
        "\u05d1\u05d9\u05d8\u05d5\u05dc", "\u05d1\u05d8\u05dc", "\u05d3\u05d7\u05d4",
        "\u05d3\u05d7\u05d9\u05d9\u05d4", "reject", "no", "\u05dc\u05d0",
    }
    prefix_rejection_words = (
        "\u05d1\u05d8\u05dc ", "\u05d1\u05d9\u05d8\u05d5\u05dc ", "\u05d3\u05d7\u05d4 ",
        "\u05d3\u05d7\u05d9\u05d9\u05d4 ", "reject ",
        "\u05dc\u05d0 \u05d1\u05d8\u05dc", "\u05dc\u05d0, \u05d1\u05d8\u05dc",
        "\u05d3\u05d7\u05d4 \u05e9\u05d9\u05d2\u05d5\u05e8", "\u05d1\u05d8\u05dc \u05e9\u05d9\u05d2\u05d5\u05e8",
    )
    norm_text = incoming_text.strip().lower()

    def _is_approval(txt: str) -> bool:
        return txt in exact_approval_words or any(txt.startswith(p) for p in prefix_approval_words)

    def _is_rejection(txt: str) -> bool:
        return txt in exact_rejection_words or any(txt.startswith(p) for p in prefix_rejection_words)

    is_appr = _is_approval(norm_text)
    is_rej = _is_rejection(norm_text)
    if resolution.caller and resolution.caller.level == PermissionLevel.COMMANDER and (is_appr or is_rej):
        db_path = getattr(deps.loaded_profile, "db_path", None)
        open_holds = interactions.get_open_approval_holds(db_path)
        choice = "approved" if is_appr else "rejected"
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
        elif norm_text in exact_approval_words or norm_text in exact_rejection_words:
            await deps.telegram_client.send_text(
                chat_id,
                "\u05d0\u05d9\u05df \u05db\u05e8\u05d2\u05e2 \u05e4\u05e2\u05d5\u05dc\u05d5\u05ea \u05d4\u05de\u05de\u05ea\u05d9\u05e0\u05d5\u05ea \u05dc\u05d0\u05d9\u05e9\u05d5\u05e8 \u05de\u05e4\u05e7\u05d3.",
            )
            return
    # Attendance buttons are handled before a pending free-form reply.  A user
    # can therefore correct/cancel an unfinished unavailability report simply
    # by pressing one of the buttons again.
    if available_button:
        _PENDING_UNAVAILABILITY.pop(attendance_key, None)
        incoming_text = messages.text("bot.availability_report_available", identity=telegram_identity)
        is_attendance_submission = True
    elif unavailable_button:
        _PENDING_UNAVAILABILITY[attendance_key] = {"reason": None}
        await deps.telegram_client.send_text(chat_id, messages.text("bot.unavailability_prompt"))
        return
    elif attendance_key in _PENDING_UNAVAILABILITY:
        pending = _PENDING_UNAVAILABILITY[attendance_key]
        days = _extract_unavailable_days(incoming_text)
        if pending["reason"] is None:
            pending["reason"] = incoming_text
        if days is None:
            await deps.telegram_client.send_text(chat_id, messages.text("bot.unavailability_days_prompt"))
            return
        reason_text = pending["reason"] or incoming_text
        incoming_text = messages.text(
            "bot.availability_report_unavailable",
            identity=telegram_identity,
            reason=reason_text,
            days=days,
        )
        _PENDING_UNAVAILABILITY.pop(attendance_key, None)
        is_attendance_submission = True

    if incoming_text == "🚀 \u05d4\u05d6\u05e0\u05e7\u05ea \u05e8\u05d7\u05e4\u05df":
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
        await deps.telegram_client.send_text(
            chat_id,
            "🚨 \u05dc\u05d4\u05d6\u05e0\u05e7\u05ea \u05db\u05d5\u05d7\u05d5\u05ea \u05d7\u05d9\u05e8\u05d5\u05dd \u05d5\u05d1\u05d9\u05d8\u05d7\u05d5\u05df, \u05e9\u05dc\u05d7 \u05d4\u05d5\u05d3\u05e2\u05d4 \u05e2\u05dd \u05e1\u05d5\u05d2 \u05d4\u05db\u05d5\u05d7 \u05d5\u05d4\u05d9\u05e2\u05d3:\n"
            "• \u05dc\u05d3\u05d5\u05d2\u05de\u05d4: '\u05d4\u05d6\u05e0\u05e7 \u05de\u05e9\u05d8\u05e8\u05d4 \u05dc\u05e9\u05e2\u05e8 \u05e6\u05e4\u05d5\u05df'\n"
            "• \u05dc\u05d3\u05d5\u05d2\u05de\u05d4: '\u05d4\u05d6\u05e0\u05e7 \u05de\u05d3\"\u05d0 \u05dc\u05d2\u05d6\u05e8\u05d4 \u05d3\u05e8\u05d5\u05de\u05d9\u05ea'\n"
            "• \u05dc\u05d3\u05d5\u05d2\u05de\u05d4: '\u05d4\u05d6\u05e0\u05e7 \u05db\u05d9\u05d1\u05d5\u05d9 \u05d0\u05e9 \u05dc\u05d7\u05de\"\u05dc'\n"
            "• \u05dc\u05d3\u05d5\u05d2\u05de\u05d4: '\u05d4\u05d6\u05e0\u05e7 \u05db\u05d5\u05d7 \u05e6\u05d1\u05d0\u05d9 \u05dc\u05d2\u05d3\u05e8 \u05de\u05d6\u05e8\u05d7\u05d9\u05ea'",
        )
        return

    protocol_hint = BUTTON_PROTOCOL_HINTS.get(incoming_text)
    if is_attendance_submission:
        # The multi-turn workflow already collected every required field.
        # Pin it to the attendance protocol so the model cannot misroute the
        # write as a generic event that waits for commander approval.
        protocol_hint = "record_attendance_response"
    if incoming_text in BUTTON_PROMPTS:
        incoming_text = BUTTON_PROMPTS[incoming_text]

    thread_id = getattr(update.message, "message_thread_id", None)
    conversation_id = f"telegram:{chat_id}:{thread_id if thread_id is not None else 'main'}"
    if is_attendance_submission:
        # Attendance is an independent workflow.  It must never be consumed as
        # the answer to an unrelated operational event-data hold in this chat.
        conversation_id = f"telegram:{chat_id}:attendance:{telegram_identity}"

    event_data_event_id = None
    replied_to = getattr(update.message, "reply_to_message", None)
    replied_to_message_id = getattr(replied_to, "message_id", None) if replied_to is not None else None
    if isinstance(replied_to_message_id, (str, int)):
        event_data_event_id = interactions.event_data_event_for_reply(chat_id, str(replied_to_message_id))

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
            event_data_event_id,
            protocol_hint=protocol_hint,
            telegram_chat_type=chat_type,
        )
    finally:
        activity_task.cancel()


async def _on_attendance_callback(deps: BotDeps, update, choice: str) -> None:
    """`attend:available` / `attend:unavailable` buttons under a group attendance prompt.

    Runs the same two flows the private-chat keyboard buttons run in
    `_on_text_message` — an immediate `record_attendance_response` submission
    for "available", and a pending reason/days follow-up (keyed by chat *and*
    identity, so members in one group never complete each other's report) for
    "unavailable"."""

    query = update.callback_query
    telegram_identity, chat_id = _identity_and_chat_id(update)
    messages = interactions.message_catalog_for(deps)

    resolution = await _resolve_caller_cached(deps.api_client, telegram_identity, messages)
    if resolution.status == "unregistered":
        await deps.telegram_client.send_text(chat_id, resolution.refusal_message)
        return

    attendance_key = (chat_id, telegram_identity)
    if choice == "unavailable":
        _PENDING_UNAVAILABILITY[attendance_key] = {"reason": None}
        user = getattr(update, "effective_user", None)
        display_name = getattr(user, "full_name", None) or getattr(user, "first_name", None) or telegram_identity
        await deps.telegram_client.send_text(chat_id, messages.text("bot.unavailability_prompt_group", name=display_name))
        return

    if choice != "available":
        logger.warning("unrecognized attendance callback choice: %s", choice, extra={"event": "bot_unknown_callback"})
        return

    _PENDING_UNAVAILABILITY.pop(attendance_key, None)
    prompt_message = getattr(query, "message", None)
    prompt_message_id = getattr(prompt_message, "message_id", None) if prompt_message is not None else None
    await present_incoming_message(
        deps,
        chat_id,
        telegram_identity,
        messages.text("bot.availability_report_available", identity=telegram_identity),
        f"{prompt_message_id if prompt_message_id is not None else query.id}:{telegram_identity}",
        f"telegram:{chat_id}:attendance:{telegram_identity}",
        protocol_hint="record_attendance_response",
        telegram_chat_type=_chat_type(update),
    )


async def _on_callback_query(update, context) -> None:
    deps: BotDeps = context.bot_data["deps"]
    query = update.callback_query
    if query is None:
        return
    telegram_identity, chat_id = _identity_and_chat_id(update)

    try:
        await deps.telegram_client.answer_callback_query(query.id)
    except Exception as exc:
        logger.warning("could not acknowledge callback query: %s", exc, extra={"event": "bot_callback_ack_failed"})

    if not query.data:
        return

    if not await _group_is_handled(deps, update):
        return

    namespace = query.data.split(":", 1)[0]
    try:
        if namespace == ATTENDANCE_CALLBACK_PREFIX:
            _prefix, _sep, choice = query.data.partition(":")
            await _on_attendance_callback(deps, update, choice)
            return

        if namespace == interactions.CLARIFICATION_CALLBACK_PREFIX:
            event_id, choice = interactions.parse_clarification_callback_data(query.data)
            outcome = await interactions.handle_clarification_answer(deps, chat_id, telegram_identity, event_id, choice)
            if outcome and outcome.status == "resolved" and getattr(query, "message", None):
                messages = interactions.message_catalog_for(deps)
                status_text = f"\u2705 {messages.text('bot.queue_resolved')}"
                try:
                    await deps.telegram_client.edit_status(
                        chat_id, str(query.message.message_id), status_text
                    )
                except Exception as exc:
                    logger.warning("could not edit clarification card: %s", exc)
            return

        if namespace == interactions.CALLBACK_PREFIX:
            event_id, choice = interactions.parse_callback_data(query.data)
            outcome = await interactions.handle_approval_answer(deps, chat_id, telegram_identity, event_id, choice)
            if outcome and outcome.status in ("approved", "rejected") and getattr(query, "message", None):
                messages = interactions.message_catalog_for(deps)
                status_text = (
                    f"\u2705 {messages.text('bot.queue_approved')}"
                    if outcome.status == "approved"
                    else f"\u274c {messages.text('bot.queue_rejected')}"
                )
                try:
                    await deps.telegram_client.edit_status(
                        chat_id, str(query.message.message_id), status_text
                    )
                except Exception as exc:
                    logger.warning("could not edit approval card: %s", exc)
            return

        logger.warning("unrecognized callback namespace: %s", namespace, extra={"event": "bot_unknown_callback"})
    except ApiRequestError as exc:
        messages = interactions.message_catalog_for(deps)
        if exc.status_code == 403:
            await deps.telegram_client.send_text(chat_id, messages.text("bot.refused", message=exc.message))
        else:
            await deps.telegram_client.send_text(chat_id, messages.text("error.request_failed", reason=exc.message))


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
    if not await _group_is_handled(deps, update):
        return

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
    if not await _group_is_handled(deps, update):
        return

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


_JOINED_MEMBER_STATUSES = frozenset({"member", "administrator", "restricted"})


async def _on_my_chat_member(update, context) -> None:
    """The bot was added to (or promoted in) a group: if that group has no binding yet, post its chat ID once so a commander can register it."""

    deps: BotDeps = context.bot_data["deps"]
    change = getattr(update, "my_chat_member", None)
    if change is None:
        return
    chat = getattr(change, "chat", None)
    chat_type = str(getattr(chat, "type", None) or "")
    if chat_type not in GROUP_CHAT_TYPES:
        return
    old_status = str(getattr(getattr(change, "old_chat_member", None), "status", "") or "")
    new_status = str(getattr(getattr(change, "new_chat_member", None), "status", "") or "")
    if new_status not in _JOINED_MEMBER_STATUSES or old_status in _JOINED_MEMBER_STATUSES:
        return  # left/kicked, or a status change between two joined states

    chat_id = str(chat.id)
    logger.info("bot added to telegram group", extra={"event": "bot_group_joined", "chat_id": chat_id, "chat_type": chat_type})
    if await _group_binding_cached(deps.api_client, chat_id) is not None:
        return
    messages = interactions.message_catalog_for(deps)
    await deps.telegram_client.send_text(chat_id, messages.text("bot.group_added_hint", chat_id=chat_id))


def register_handlers(application, deps: BotDeps) -> None:
    from telegram.ext import CallbackQueryHandler, ChatMemberHandler, CommandHandler, MessageHandler, filters

    application.bot_data["deps"] = deps

    assert REGISTERED_COMMANDS == ("profile", "settings")
    application.add_handler(CommandHandler("start", _guarded(_on_start_command)))
    application.add_handler(CommandHandler("menu", _guarded(_on_start_command)))
    application.add_handler(CommandHandler(REGISTERED_COMMANDS[0], _guarded(_on_profile_command)))
    application.add_handler(CommandHandler(REGISTERED_COMMANDS[1], _guarded(_on_settings_command)))
    application.add_handler(CallbackQueryHandler(_guarded(_on_callback_query)))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _guarded(_on_text_message)))
    application.add_handler(
        ChatMemberHandler(_guarded(_on_my_chat_member, require_full_name=False), ChatMemberHandler.MY_CHAT_MEMBER)
    )

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
        poll_task = asyncio.create_task(
            run_notification_poll_loop(deps, NOTIFICATION_POLL_INTERVAL_SECONDS, cursor_store=cursor_store)
        )
        started_application.bot_data["notification_task"] = poll_task
        attendance_task = asyncio.create_task(run_attendance_check_loop(deps, ATTENDANCE_CHECK_INTERVAL_SECONDS))
        started_application.bot_data["attendance_task"] = attendance_task

    application.post_init = _post_init

    async def _post_shutdown(_stopped_application) -> None:
        for task_key in ("notification_task", "attendance_task"):
            background_task = _stopped_application.bot_data.get(task_key)
            if background_task is not None and not background_task.done():
                background_task.cancel()
                try:
                    await background_task
                except (asyncio.CancelledError, Exception):
                    pass
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
