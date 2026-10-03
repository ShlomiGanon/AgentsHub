"""Telegram-network stubs so the simulator can run real handlers with no Telegram I/O."""

from __future__ import annotations

import json
import time
import zlib
from dataclasses import dataclass
from typing import Sequence

import telegram
from telegram.request import RequestData

from bot.transports import TelegramClient

# A fixed, fake bot identity — never presented to Telegram, never checked
# against anything real. Only `Bot.initialize()`'s own `User(**this)`
# parsing needs it to look like a valid Bot API `User` object.
# --- request stub ---

_FAKE_BOT_USER = {"id": 1, "is_bot": True, "first_name": "AgentsHub Simulator"}


class FakeBotRequest(telegram.request.BaseRequest):
    """Stub PTB Bot-API transport so bootstrap calls succeed with zero network I/O."""

    @property
    def read_timeout(self) -> float | None:
        """No extra read timeout for stubbed Bot API calls."""

        return None

    async def initialize(self) -> None:
        """No-op open for the stub transport."""

        return None

    async def shutdown(self) -> None:
        """No-op close for the stub transport."""

        return None

    async def do_request(
        self,
        url: str,
        method: str,
        request_data: RequestData | None = None,
        read_timeout=None,
        write_timeout=None,
        connect_timeout=None,
        pool_timeout=None,
    ) -> tuple[int, bytes]:
        """Return a successful getMe user, or True for other bootstrap calls."""

        result = _FAKE_BOT_USER if url.rstrip("/").endswith("getMe") else True
        return 200, json.dumps({"ok": True, "result": result}).encode("utf-8")


@dataclass
class SentMessage:
    """One outbound simulator message recorded for later polling."""

    chat_id: str
    text: str
    buttons: tuple[tuple[str, str], ...] | None = None
    reply_to_message_id: str | None = None


# --- client ---

class SimulatorTelegramClient(TelegramClient):
    """Records every outbound action in memory, exactly like
    `tests/bot_fakes.py`'s `FakeTelegramClient` — plus `reply_since()`, so
    `/Simulator-msg` can read back what one request actually sent to one
    chat without needing to know which of `send_text`/`send_with_buttons`/
    `edit_status` produced it (mirrors what a real Telegram user in that
    chat would simply see)."""

    def __init__(self) -> None:
        """Start with empty sent-message and status logs."""

        self.sent: list[SentMessage] = []
        self.status_events: list[tuple] = []
        self.answered_callback_query_ids: list[str] = []
        self._next_status_id = 1

    async def validate_token(self) -> bool:
        """Always succeed; simulation mode never talks to Telegram."""

        return True

    async def send_text(self, chat_id: str, text: str, keyboard: Sequence[Sequence[str]] | None = None) -> None:
        """Record a plain outbound message."""

        self.sent.append(SentMessage(chat_id=chat_id, text=text))

    async def send_status(self, chat_id: str, text: str, reply_to_message_id: str | None = None) -> str:
        """Record a status message and return a synthetic id."""

        message_id = str(self._next_status_id)
        self._next_status_id += 1
        self.status_events.append(("send", chat_id, message_id, text))
        return message_id

    async def edit_status(self, chat_id: str, message_id: str, text: str) -> None:
        """Record a status edit."""

        self.status_events.append(("edit", chat_id, message_id, text))

    async def delete_status(self, chat_id: str, message_id: str) -> None:
        """Record a status delete."""

        self.status_events.append(("delete", chat_id, message_id))

    async def send_with_buttons(self, chat_id: str, text: str, buttons: Sequence[tuple[str, str]]) -> None:
        """Record an outbound message with inline buttons."""

        self.sent.append(SentMessage(chat_id=chat_id, text=text, buttons=tuple(buttons)))

    async def send_reply(self, chat_id: str, text: str, reply_to_message_id: str | None) -> str:
        """Record a reply and return a synthetic message id."""

        self.sent.append(SentMessage(chat_id=chat_id, text=text, reply_to_message_id=reply_to_message_id))
        message_id = str(self._next_status_id)
        self._next_status_id += 1
        return message_id

    async def answer_callback_query(self, callback_query_id: str, text: str | None = None) -> None:
        """Record that this callback query was acknowledged."""

        self.answered_callback_query_ids.append(callback_query_id)

    def run_polling(self, register_handlers) -> None:
        """Refuse; the simulator drives updates through Flask, not polling."""

        raise NotImplementedError("bot.simulator_app never polls Telegram")

    # -- reading back one request's reply --------------------------------

    def mark(self) -> tuple[int, int]:
        """A marker for `reply_since()` — call before `process_update()`."""

        return len(self.status_events), len(self.sent)

    def changes_since(self, mark: tuple[int, int], chat_id: str) -> dict:
        """Expose real send/edit events so the simulator can replace an ack in place."""
        status_mark, sent_mark = mark
        return {
            "status_updates": [
                {"kind": event[0], "message_id": event[2], "text": event[3] if len(event) > 3 else ""}
                for event in self.status_events[status_mark:]
                if event[1] == chat_id
            ],
            "sent_messages": [message.text for message in self.sent[sent_mark:] if message.chat_id == chat_id],
        }

    def reply_since(self, mark: tuple[int, int], chat_id: str) -> str | None:
        """The text a real Telegram user in `chat_id` would end up seeing
        for everything sent since `mark`. A status message that gets sent
        then edited (`present_incoming_message`'s normal lifecycle,
        `bot/presentation.py`'s `replace_status`) is replayed to its final
        surviving text — not concatenated with its own "thinking..."
        placeholder, since an edit replaces a message in place rather than
        adding a new one. A deleted status message contributes nothing (the
        send-and-delete failure-recovery path in `replace_status`). Each
        distinct message — the status message plus any separate
        `send_text`/`send_with_buttons`/`send_reply` call — is then joined
        in send order. `None` if nothing was sent to this chat_id at all
        (e.g. a refused/silent update)."""

        status_mark, sent_mark = mark
        final_by_message_id: dict[str, str] = {}
        for event in self.status_events[status_mark:]:
            kind, event_chat_id, message_id = event[0], event[1], event[2]
            if event_chat_id != chat_id:
                continue
            if kind in ("send", "edit"):
                final_by_message_id[message_id] = event[3]
            elif kind == "delete":
                final_by_message_id.pop(message_id, None)

        pieces: list[str] = list(final_by_message_id.values())
        for message in self.sent[sent_mark:]:
            if message.chat_id == chat_id:
                pieces.append(message.text)
        return "\n".join(pieces) if pieces else None


def _stable_message_id(source_message_id: str) -> int:
    """Deterministic positive int for a scenario source_message_id so Telegram dedup stays stable."""

    return zlib.crc32(source_message_id.encode("utf-8")) & 0x7FFFFFFF


def build_synthetic_text_update(
    *,
    update_id: int,
    source_message_id: str,
    sender_identity: str,
    chat_id: str,
    chat_type: str,
    text: str,
    bot: "telegram.Bot",
    date: float | None = None,
) -> telegram.Update:
    """Build a real telegram.Update for one plain-text message so PTB filters classify it normally."""

    payload = {
        "update_id": update_id,
        "message": {
            "message_id": _stable_message_id(source_message_id),
            "date": int(date if date is not None else time.time()),
            "chat": {"id": int(chat_id), "type": chat_type},
            "from": {"id": int(sender_identity), "is_bot": False, "first_name": sender_identity},
            "text": text,
        },
    }
    return telegram.Update.de_json(payload, bot)
