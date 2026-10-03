"""Telegram transport implementations and HTTP client re-exports."""

import logging
from abc import ABC, abstractmethod
from typing import Callable, Sequence

from bot.http_api_client import HttpApiClient, _do_request, telegram_request_context
from bot.interactions import split_message
from tools import stage_context

logger = logging.getLogger(__name__)


# --- telegram ---

class TelegramClient(ABC):
    """Outbound Telegram operations the bot handlers depend on."""

    async def send_activity(self, chat_id: str, action: str) -> None:
        """Show non-text activity feedback when the transport supports it."""

    @abstractmethod
    async def validate_token(self) -> bool:
        """True if Telegram accepts the configured token, False if it rejects it outright."""

    @abstractmethod
    async def send_text(self, chat_id: str, text: str, keyboard: Sequence[Sequence[str]] | None = None) -> None:
        """Send a plain text message, optionally with a reply keyboard."""

        ...

    @abstractmethod
    async def send_status(self, chat_id: str, text: str, reply_to_message_id: str | None = None) -> str:
        """Send a temporary status and return its transport-specific message ID.

        When given, `reply_to_message_id` threads the status (and later edits) as a
        reply to the original message — important in a busy group chat.
        """

    @abstractmethod
    async def edit_status(self, chat_id: str, message_id: str, text: str) -> None:
        """Replace a previously sent status message."""

    @abstractmethod
    async def delete_status(self, chat_id: str, message_id: str) -> None:
        """Delete a stale status message on a best-effort recovery path."""

    @abstractmethod
    async def send_with_buttons(self, chat_id: str, text: str, buttons: Sequence[tuple[str, str]]) -> None:
        """Send `buttons` as one (label, callback_data) pair per row for hold choices, never free text."""

    @abstractmethod
    async def send_reply(self, chat_id: str, text: str, reply_to_message_id: str | None) -> str | None:
        """Like `send_text`, but referencing the original message when one is given."""

    @abstractmethod
    async def answer_callback_query(self, callback_query_id: str, text: str | None = None) -> None:
        """Acknowledge a button press so Telegram clears its spinner."""

    @abstractmethod
    def run_polling(self, register_handlers: Callable[[object], None]) -> None:
        """Register handlers on the underlying application, then block polling until stopped."""


class PTBTelegramClient(TelegramClient):
    """python-telegram-bot implementation of TelegramClient."""

    def __init__(self, token: str):
        """Build the PTB Application around this bot token."""

        from telegram.ext import ApplicationBuilder

        self._application = ApplicationBuilder().token(token).build()

    async def validate_token(self) -> bool:
        """True when Telegram accepts getMe for this token."""

        from telegram.error import TelegramError

        try:
            await self._application.bot.get_me()
            return True
        except TelegramError:
            return False

    async def send_text(self, chat_id: str, text: str, keyboard: Sequence[Sequence[str]] | None = None) -> None:
        """Send a text message through PTB, splitting when over the length limit."""

        from telegram import ReplyKeyboardMarkup

        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True) if keyboard is not None else None
        with stage_context("telegram_send"):
            chunks = split_message(text)
            if not chunks:
                return
            for chunk in chunks[:-1]:
                await self._application.bot.send_message(chat_id=chat_id, text=chunk)
            if reply_markup is not None:
                await self._application.bot.send_message(chat_id=chat_id, text=chunks[-1], reply_markup=reply_markup)
            else:
                await self._application.bot.send_message(chat_id=chat_id, text=chunks[-1])

    async def send_status(self, chat_id: str, text: str, reply_to_message_id: str | None = None) -> str:
        """Send a status message and return its Telegram message id."""

        with stage_context("telegram_send"):
            message = await self._application.bot.send_message(
                chat_id=chat_id, text=text,
                reply_to_message_id=int(reply_to_message_id) if reply_to_message_id is not None else None,
            )
        return str(message.message_id)

    async def edit_status(self, chat_id: str, message_id: str, text: str) -> None:
        """Edit a previously sent status message."""

        with stage_context("telegram_edit"):
            await self._application.bot.edit_message_text(
                chat_id=chat_id,
                message_id=int(message_id),
                text=text,
            )

    async def delete_status(self, chat_id: str, message_id: str) -> None:
        """Delete a previously sent status message."""

        with stage_context("telegram_delete"):
            await self._application.bot.delete_message(chat_id=chat_id, message_id=int(message_id))

    async def send_activity(self, chat_id: str, action: str) -> None:
        """Show a Telegram chat action such as typing."""

        await self._application.bot.send_chat_action(chat_id=chat_id, action=action)

    async def send_with_buttons(self, chat_id: str, text: str, buttons: Sequence[tuple[str, str]]) -> None:
        """Send text with one inline button per row."""

        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        markup = InlineKeyboardMarkup([[InlineKeyboardButton(label, callback_data=callback_data)] for label, callback_data in buttons])

        with stage_context("telegram_send"):
            chunks = split_message(text)
            for chunk in chunks[:-1]:
                await self._application.bot.send_message(chat_id=chat_id, text=chunk)
            await self._application.bot.send_message(chat_id=chat_id, text=chunks[-1], reply_markup=markup)

    async def send_reply(self, chat_id: str, text: str, reply_to_message_id: str | None) -> str | None:
        """Send text as a reply to the original message when an id is given."""

        with stage_context("telegram_send"):
            chunks = split_message(text)
            first_message_id = None
            if chunks:
                try:
                    sent = await self._application.bot.send_message(
                        chat_id=chat_id, text=chunks[0], reply_to_message_id=reply_to_message_id
                    )
                except Exception:
                    if reply_to_message_id is not None:
                        logger.warning(
                            "telegram reply_to %s failed; sending without reference",
                            reply_to_message_id,
                            extra={"event": "telegram_reply_fallback", "chat_id": chat_id},
                            exc_info=True,
                        )
                        sent = await self._application.bot.send_message(chat_id=chat_id, text=chunks[0])
                    else:
                        raise
                first_message_id = str(sent.message_id)
            for chunk in chunks[1:]:
                await self._application.bot.send_message(chat_id=chat_id, text=chunk)
            return first_message_id

    async def answer_callback_query(self, callback_query_id: str, text: str | None = None) -> None:
        """Acknowledge a button press so Telegram clears its spinner."""

        await self._application.bot.answer_callback_query(callback_query_id=callback_query_id, text=text)

    def run_polling(self, register_handlers: Callable[[object], None]) -> None:
        """Register handlers and block polling Telegram until the process stops."""

        register_handlers(self._application)
        self._application.run_polling()
