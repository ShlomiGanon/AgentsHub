"""Telegram handler registration and command / callback / text handlers."""

import asyncio
import importlib
import logging
import re
import sys
import time
from pathlib import Path
from typing import Awaitable, Callable

from auth.permissions import InvalidFullNameError, PermissionLevel, RequestedOperation, normalize_full_name
from bot import interactions
from bot.background_services import (
    ATTENDANCE_CALLBACK_PREFIX,
    NotificationCursorStore,
    run_attendance_check_loop,
    run_notification_poll_loop,
)
from bot.contracts import ApiNotImplementedError, BotDeps
from bot.dispatch import (
    _chat_type,
    _group_binding_cached,
    _group_is_handled,
    _resolve_caller_cached,
    present_incoming_message,
)
from bot.interactions import check_permission, resolve_caller
from bot.runtime_state import (
    ATTENDANCE_CHECK_INTERVAL_SECONDS,
    GROUP_CHAT_TYPES,
    NOTIFICATION_POLL_INTERVAL_SECONDS,
    PENDING_NAME_ACTIONS,
    PendingNameAction,
    REGISTERED_COMMANDS,
    _PENDING_NAME_ACTION_TTL_SECONDS,
    _PENDING_UNAVAILABILITY,
    _USER_ROLE_CACHE,
    clear_caller_cache,
)
from bot.transports import telegram_request_context
from tools import get_trace_id, new_trace_id, set_trace_id

logger = logging.getLogger(__name__)

_JOINED_MEMBER_STATUSES = frozenset({"member", "administrator", "restricted"})

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
    "\u23f3 \u05ea\u05d5\u05e8 \u05d0\u05d9\u05e9\u05d5\u05e8\u05d9\u05dd",
    "\u05ea\u05d5\u05e8 \u05d0\u05d9\u05e9\u05d5\u05e8\u05d9\u05dd",
    "\u05d0\u05d9\u05e9\u05d5\u05e8\u05d9\u05dd",
    "\u05de\u05d4 \u05de\u05de\u05ea\u05d9\u05df \u05dc\u05d0\u05d9\u05e9\u05d5\u05e8",
    "approvals queue",
    "approvals",
    "pending approvals",
}

def _identity_and_chat_id(update) -> tuple[str, str]:
    """Telegram user id and chat id from one update."""

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
    """Welcome the caller and show the role-specific keyboard."""

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

    pending_key = (telegram_identity, chat_id)
    pending = PENDING_NAME_ACTIONS.get(pending_key)
    if pending is not None and time.monotonic() - pending.created_at >= _PENDING_NAME_ACTION_TTL_SECONDS:
        PENDING_NAME_ACTIONS.pop(pending_key, None)
        pending = None
    candidate = getattr(getattr(update, "message", None), "text", None)
    if pending is not None and isinstance(candidate, str):
        try:
            full_name = normalize_full_name(candidate)
        except InvalidFullNameError:
            await deps.telegram_client.send_text(chat_id, messages.text("bot.full_name_invalid"))
            return True
        await deps.api_client.update_own_full_name(telegram_identity, full_name)
        PENDING_NAME_ACTIONS.pop(pending_key, None)
        _USER_ROLE_CACHE.pop((id(deps.api_client), telegram_identity), None)
        await deps.telegram_client.send_text(chat_id, messages.text("bot.full_name_saved", name=full_name))
        await pending.handler(pending.update, pending.context)
        return True

    if pending is None:
        PENDING_NAME_ACTIONS[pending_key] = PendingNameAction(handler, update, context)
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
        """Admit the update, collect a missing name if needed, then run the handler."""

        deps = context.bot_data["deps"]
        messages = interactions.message_catalog_for(deps)
        try:
            telegram_identity, chat_id = _identity_and_chat_id(update)
            chat_type = _chat_type(update)
            chat = getattr(update, "effective_chat", None)
            chat_label = str(getattr(chat, "title", None) or "")
            with telegram_request_context(chat_id, chat_type):
                admission = await deps.api_client.admit_telegram_update(
                    telegram_identity,
                    chat_id,
                    chat_type,
                    chat_label,
                )
                if not admission.allowed:
                    PENDING_NAME_ACTIONS.pop((telegram_identity, chat_id), None)
                    logger.info(
                        "telegram update blocked by admission policy",
                        extra={
                            "event": "bot_telegram_admission_blocked",
                            "telegram_identity": telegram_identity,
                            "chat_id": chat_id,
                            "reason": admission.reason,
                        },
                    )
                    if chat_type == "private":
                        await deps.telegram_client.send_text(chat_id, messages.text("auth.safe_mode_blocked"))
                    return
                if admission.reason == "auto_registered":
                    clear_caller_cache()
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

async def _on_text_message(update, context) -> None:
    """Handle free-text: queue shortcut, attendance unavailability, or a new /Msg."""

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
        """Show typing while the report is being handled."""

        while True:
            await deps.telegram_client.send_activity(chat_id, "typing")
            await asyncio.sleep(4.0)

    trace_id = get_trace_id()

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
            trace_id=trace_id,
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
    """Route an inline-button press to attendance, approval, or clarification."""

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
    """Handle /profile view, diff, and protocol write subcommands."""

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
    """Handle /settings view and change subcommands."""

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
    """Attach command, callback, text, and chat-member handlers plus background loops."""

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
        """Open the API client, hydrate holds, set commands, and start background loops."""

        await deps.api_client.start()
        await interactions.hydrate_open_approval_holds(deps.api_client)
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
        """Cancel background loops and close the API client."""

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
