"""Telegram handler registration and re-exports for command, callback, and text handlers."""

import asyncio
import logging
import time
from pathlib import Path
from typing import Awaitable, Callable

from auth.permissions import InvalidFullNameError, normalize_full_name
from bot import interactions
from bot.background_services import NotificationCursorStore, run_attendance_check_loop, run_notification_poll_loop
from bot.contracts import ApiNotImplementedError, BotDeps
from bot.dispatch import _chat_type, _group_is_handled, _resolve_caller_cached
from bot.handler_callbacks import _on_attendance_callback, _on_callback_query
from bot.handler_commands import (
    _bot_commands,
    _identity_and_chat_id,
    _on_my_chat_member,
    _on_profile_command,
    _on_settings_command,
    _on_start_command,
    _on_text_message,
)
from bot.handler_protocol import _parse_protocol_write_command
from bot.runtime_state import (
    ATTENDANCE_CHECK_INTERVAL_SECONDS,
    NOTIFICATION_POLL_INTERVAL_SECONDS,
    PENDING_NAME_ACTIONS,
    PendingNameAction,
    REGISTERED_COMMANDS,
    _PENDING_NAME_ACTION_TTL_SECONDS,
    _USER_ROLE_CACHE,
    clear_caller_cache,
)
from bot.transports import telegram_request_context

logger = logging.getLogger(__name__)

__all__ = [
    "_bot_commands",
    "_guarded",
    "_on_attendance_callback",
    "_on_callback_query",
    "_on_my_chat_member",
    "_on_profile_command",
    "_on_settings_command",
    "_on_start_command",
    "_on_text_message",
    "_parse_protocol_write_command",
    "register_handlers",
]


# --- gates ---

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
    """Wrap a handler so API and unexpected errors become a chat reply, never a leaked stack trace."""

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


# --- registration ---

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
