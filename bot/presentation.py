"""Shared status-message lifecycle for Telegram and terminal transports."""

import logging

from bot.interactions import split_message
from bot.transports import TelegramClient

logger = logging.getLogger(__name__)


async def replace_status(
    client: TelegramClient,
    chat_id: str,
    status_message_id: str,
    final_text: str,
) -> None:
    """Edit the status once, with a send-and-delete fallback if editing fails."""

    chunks = split_message(final_text)
    first_chunk = chunks[0] if chunks else ""
    try:
        await client.edit_status(chat_id, status_message_id, first_chunk)
    except Exception:
        logger.exception(
            "status edit failed; sending the final response as a new message",
            extra={"event": "status_edit_failed"},
        )
        await client.send_text(chat_id, final_text)
        try:
            await client.delete_status(chat_id, status_message_id)
        except Exception:
            logger.exception(
                "stale status deletion failed after edit recovery",
                extra={"event": "status_delete_failed"},
            )
        return

    for chunk in chunks[1:]:
        await client.send_text(chat_id, chunk)
