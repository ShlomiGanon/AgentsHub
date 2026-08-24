"""The Telegram adapter (work_plan.md §8.1, §8.10).

`python-telegram-bot` 22.8 is installed in this environment (confirmed
available and added to requirements.txt — unlike crewai, per
docs/progress.md's §3.5 entry, this library needs no model API key to
install or to exercise its non-network code paths). Every call below is
written and introspection-checked against the real, installed
`telegram`/`telegram.ext` API (`Bot.get_me`, `Bot.send_message`,
`ApplicationBuilder`, `CommandHandler`, `MessageHandler`,
`CallbackQueryHandler` — checked directly against the installed package
on 2026-08-24, not guessed). **Not verified against a live bot token or a
real Telegram chat** — that requires a real token and network access this
environment doesn't have, the same "unverified against a live
integration" status this codebase already carries for the CrewAI adapter
and the Main Agent's prompt conventions (see docs/progress.md).

`TelegramClient` is the abstract interface every other bot module depends
on, so they can be tested against `tests/helpers.py`'s fake without
touching the network. `PTBTelegramClient` is the one real implementation,
built on `python-telegram-bot`. Every send path routes through
`bot.formatting.split_message` (§8.10) — no caller has to think about
Telegram's length limit.
"""

from abc import ABC, abstractmethod
from typing import Callable, Sequence

from bot.formatting import split_message


class TelegramClient(ABC):
    @abstractmethod
    async def validate_token(self) -> bool:
        """True if Telegram accepts the configured token, False if it
        rejects it outright (§8.1's "fail at startup ... if ... rejected").
        """

    @abstractmethod
    async def send_text(self, chat_id: str, text: str) -> None: ...

    @abstractmethod
    async def send_with_buttons(self, chat_id: str, text: str, buttons: Sequence[tuple[str, str]]) -> None:
        """`buttons` is a sequence of (label, callback_data) pairs, laid
        out one per row — used for clarification/approval choices (§8.4,
        §8.5), never for free text (§8.4's own "buttons rather than free
        text" requirement, since the registry is fixed for the run).
        """

    @abstractmethod
    async def send_reply(self, chat_id: str, text: str, reply_to_message_id: str | None) -> None:
        """Like `send_text`, but referencing the original message when
        one is given — §8.9's "reference the original message when
        delivering" (minutes may have passed; the sender may have sent
        others meanwhile).
        """

    @abstractmethod
    async def answer_callback_query(self, callback_query_id: str, text: str | None = None) -> None:
        """Acknowledge a button press so Telegram clears its spinner."""

    @abstractmethod
    def run_polling(self, register_handlers: Callable[[object], None]) -> None:
        """Register handlers on the underlying application via
        `register_handlers(application)`, then block, polling for
        updates, until the process is stopped.
        """


class PTBTelegramClient(TelegramClient):
    def __init__(self, token: str):
        from telegram.ext import ApplicationBuilder

        self._application = ApplicationBuilder().token(token).build()

    async def validate_token(self) -> bool:
        from telegram.error import TelegramError

        try:
            await self._application.bot.get_me()
            return True
        except TelegramError:
            return False

    async def send_text(self, chat_id: str, text: str) -> None:
        for chunk in split_message(text):
            await self._application.bot.send_message(chat_id=chat_id, text=chunk)

    async def send_with_buttons(self, chat_id: str, text: str, buttons: Sequence[tuple[str, str]]) -> None:
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        markup = InlineKeyboardMarkup([[InlineKeyboardButton(label, callback_data=data)] for label, data in buttons])

        chunks = split_message(text)
        for chunk in chunks[:-1]:
            await self._application.bot.send_message(chat_id=chat_id, text=chunk)
        await self._application.bot.send_message(chat_id=chat_id, text=chunks[-1], reply_markup=markup)

    async def send_reply(self, chat_id: str, text: str, reply_to_message_id: str | None) -> None:
        chunks = split_message(text)
        # Only the first chunk carries the reply-to reference — later
        # chunks exist only because the message was too long, not because
        # they are separate replies.
        if chunks:
            await self._application.bot.send_message(chat_id=chat_id, text=chunks[0], reply_to_message_id=reply_to_message_id)
        for chunk in chunks[1:]:
            await self._application.bot.send_message(chat_id=chat_id, text=chunk)

    async def answer_callback_query(self, callback_query_id: str, text: str | None = None) -> None:
        await self._application.bot.answer_callback_query(callback_query_id=callback_query_id, text=text)

    def run_polling(self, register_handlers: Callable[[object], None]) -> None:
        register_handlers(self._application)
        self._application.run_polling()
