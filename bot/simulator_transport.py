"""The two Telegram-network stubs `bot/simulator_app.py` uses so PTB's real
`Application`/dispatcher and the real handlers in `bot/app.py` can run for
simulation-reserved identities with zero real Telegram credentials or
network access anywhere in the process (docs/bot_simulation_mode_design.md
§4.1).

`FakeBotRequest` satisfies every Bot-API call PTB's own startup machinery
makes (`Application.initialize()`'s mandatory `get_me()`, and
`register_handlers()`'s `post_init` hook calling `set_my_commands()`).
`SimulatorTelegramClient` is the
outbound stub — an extension of `tests/bot_fakes.py`'s already-tested
`FakeTelegramClient` shape, moved here (not test-only) since it's now also
production code for this one specific, isolated purpose, plus one addition:
reading back what a single request sent to one chat, so the `/Simulator-msg`
endpoint can hand the admin simulator a reply the same way `/Msg` already
does. `build_synthetic_text_update()` turns one simulated text message into
a real `telegram.Update` via PTB's own `Update.de_json` — the same
deserialization path real webhook/getUpdates delivery uses — so PTB's real
filters/dispatch classify it exactly as they would a real one.
"""

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
_FAKE_BOT_USER = {"id": 1, "is_bot": True, "first_name": "AgentsHub Simulator"}


class FakeBotRequest(telegram.request.BaseRequest):
    """Implements PTB's own transport seam (`telegram.request.BaseRequest`,
    4 abstract methods) so every Bot-API call `python-telegram-bot`'s own
    internals make while starting up — `Bot.initialize()`'s mandatory
    `get_me()` (docs/bot_simulation_mode_design.md §1.2), and
    `register_handlers()`'s `post_init` hook calling `set_my_commands()`
    (`bot/app.py`, reused unmodified — see §4.2/§8: this file exists to make
    that reuse possible, not to change it) — succeeds with zero network I/O.

    This is deliberately permissive rather than a narrow allowlist: every
    handler and background loop in this codebase already sends real
    business traffic exclusively through `deps.telegram_client`
    (`SimulatorTelegramClient` below), never through `context.bot`/
    `self._application.bot` directly (confirmed by reading every call site
    in `bot/app.py`/`bot/background_services.py`) — so the only calls that
    can ever reach this stub are PTB's own harmless bootstrap machinery,
    not real message content. `getMe` gets a real-shaped `User` payload
    (some callers parse the result into a concrete type); everything else
    gets a generic successful `True`, which is what PTB's other bootstrap
    calls (`setMyCommands`) expect back."""

    @property
    def read_timeout(self) -> float | None:
        return None

    async def initialize(self) -> None:
        return None

    async def shutdown(self) -> None:
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
        result = _FAKE_BOT_USER if url.rstrip("/").endswith("getMe") else True
        return 200, json.dumps({"ok": True, "result": result}).encode("utf-8")


@dataclass
class SentMessage:
    chat_id: str
    text: str
    buttons: tuple[tuple[str, str], ...] | None = None
    reply_to_message_id: str | None = None


class SimulatorTelegramClient(TelegramClient):
    """Records every outbound action in memory, exactly like
    `tests/bot_fakes.py`'s `FakeTelegramClient` — plus `reply_since()`, so
    `/Simulator-msg` can read back what one request actually sent to one
    chat without needing to know which of `send_text`/`send_with_buttons`/
    `edit_status` produced it (mirrors what a real Telegram user in that
    chat would simply see)."""

    def __init__(self) -> None:
        self.sent: list[SentMessage] = []
        self.status_events: list[tuple] = []
        self.answered_callback_query_ids: list[str] = []
        self._next_status_id = 1

    async def validate_token(self) -> bool:
        return True

    async def send_text(self, chat_id: str, text: str, keyboard: Sequence[Sequence[str]] | None = None) -> None:
        self.sent.append(SentMessage(chat_id=chat_id, text=text))

    async def send_status(self, chat_id: str, text: str) -> str:
        message_id = str(self._next_status_id)
        self._next_status_id += 1
        self.status_events.append(("send", chat_id, message_id, text))
        return message_id

    async def edit_status(self, chat_id: str, message_id: str, text: str) -> None:
        self.status_events.append(("edit", chat_id, message_id, text))

    async def delete_status(self, chat_id: str, message_id: str) -> None:
        self.status_events.append(("delete", chat_id, message_id))

    async def send_with_buttons(self, chat_id: str, text: str, buttons: Sequence[tuple[str, str]]) -> None:
        self.sent.append(SentMessage(chat_id=chat_id, text=text, buttons=tuple(buttons)))

    async def send_reply(self, chat_id: str, text: str, reply_to_message_id: str | None) -> str:
        self.sent.append(SentMessage(chat_id=chat_id, text=text, reply_to_message_id=reply_to_message_id))
        message_id = str(self._next_status_id)
        self._next_status_id += 1
        return message_id

    async def answer_callback_query(self, callback_query_id: str, text: str | None = None) -> None:
        self.answered_callback_query_ids.append(callback_query_id)

    def run_polling(self, register_handlers) -> None:
        raise NotImplementedError("bot.simulator_app never polls Telegram")

    # -- reading back one request's reply --------------------------------

    def mark(self) -> tuple[int, int]:
        """A marker for `reply_since()` — call before `process_update()`."""

        return len(self.status_events), len(self.sent)

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
    """PTB's `Message.message_id` must be a Bot-API integer; a scenario's
    `source_message_id` is an arbitrary caller-chosen string (matching
    `/Msg`'s own existing contract). `crc32` gives a small, positive,
    *deterministic* integer for the same string every time — unlike
    Python's own randomized `hash()` — so re-sending the same
    `source_message_id` still produces the same synthetic numeric
    message_id, preserving `/Msg`'s existing dedup-on-source_message_id
    behavior once the real bot handler re-derives its own `source_message_id`
    as `str(update.message.message_id)` (docs/bot_simulation_mode_design.md
    §10's `source_message_id`/`conversation_id` edge case)."""

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
    """One simulated text message, as a real `telegram.Update` — built the
    same way PTB itself deserializes a real webhook/getUpdates payload
    (`telegram.Update.de_json`), so every registered handler's
    `check_update()` (PTB's real filters, not a reimplementation of them)
    classifies it exactly as it would a real message. Deliberately scoped
    to plain text only (docs/bot_simulation_mode_design.md §2 decision 1,
    §11) — no `entities`, so `filters.COMMAND` never matches and every
    scenario message routes to `_on_text_message`.
    """

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
