"""Inline-button callback handlers for attendance, approval, and clarification."""

import logging

from bot import interactions
from bot.background_services import ATTENDANCE_CALLBACK_PREFIX
from bot.contracts import ApiRequestError, BotDeps
from bot.dispatch import _chat_type, _group_is_handled, _resolve_caller_cached, present_incoming_message
from bot.handler_commands import _identity_and_chat_id
from bot.runtime_state import _PENDING_UNAVAILABILITY

logger = logging.getLogger(__name__)


async def _on_attendance_callback(deps: BotDeps, update, choice: str) -> None:
    """Run the same available/unavailable flows as the private-chat attendance buttons."""

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
