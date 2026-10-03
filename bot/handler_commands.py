"""Slash-command and free-text Telegram handlers."""

import asyncio
import importlib
import logging
import re
import sys

from auth.permissions import PermissionLevel, RequestedOperation
from bot import interactions
from bot.contracts import BotDeps
from bot.dispatch import (
    _chat_type,
    _group_binding_cached,
    _group_is_handled,
    _resolve_caller_cached,
    present_incoming_message,
)
from bot.handler_protocol import _parse_protocol_write_command
from bot.interactions import check_permission, resolve_caller
from bot.runtime_state import (
    GROUP_CHAT_TYPES,
    _PENDING_UNAVAILABILITY,
)
from tools import get_trace_id

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
    "📋 \u05d9\u05d5\u05de\u05df \u05d0\u05d9\u05e8\u05d5\u05e2\u05d9\u05dd \u05d5\u05ea\u05d7\u05e7\u05d5\u05e8": "\u05de\u05d4\u05dd \u05d4\u05d0\u05d9\u05e8\u05d5\u05e2\u05d9\u05dd \u05d4\u05d0\u05d7\u05e8\u05d5\u05e0\u05d9\u05dd \u05e9\u05e0\u05e8\u05e9\u05de\u05d5 \u05d1\u05d9\u05d5\u05de\u05df \u05d4\u05de\u05d1\u05e6\u05e2\u05d9? \u05d4\u05e9\u05d1 \u05d1\u05e2\u05d1\u05e8\u05d9\u05ea \u05e7\u05e6\u05e8\u05d4 \u05d5\u05de\u05d1\u05e6\u05e2\u05d9\u05ea \u05d1\u05dc\u05d35e2\u05d9\u05ea \u05d1\u05dc\u05d1\u05d3 (\u05e2\u05d3 3-4 \u05e9\u05d5\u05e8\u05d5\u05ea).",
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

# --- shared ---

def _identity_and_chat_id(update) -> tuple[str, str]:
    """Telegram user id and chat id from one update."""

    return str(update.effective_user.id), str(update.effective_chat.id)


def _bot_commands(catalog) -> list[tuple[str, str]]:
    """Command-menu pairs, including /start so it appears in Telegram's picker."""

    return [
        ("start", catalog.text("command.menu_start")),
        ("profile", catalog.text("command.menu_profile")),
        ("settings", catalog.text("command.menu_settings")),
    ]


async def _resolve_caller_or_refuse(deps: BotDeps, chat_id: str, telegram_identity: str):
    """Resolved caller, or None after sending the unregistered refusal."""

    resolution = await resolve_caller(
        deps.api_client, telegram_identity, interactions.message_catalog_for(deps)
    )
    if resolution.status == "unregistered":
        await deps.telegram_client.send_text(chat_id, resolution.refusal_message)
        return None

    return resolution.caller


# --- commands ---

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
    """Post the group chat id once when the bot is added to an unbound group."""

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


# --- text ---

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
