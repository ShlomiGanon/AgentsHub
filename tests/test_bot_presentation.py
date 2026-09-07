"""Shared Telegram/CLI status replacement behavior."""

import asyncio

from bot.presentation import replace_status
from tests.bot_fakes import FakeTelegramClient


def _run(coro):
    return asyncio.run(coro)


def test_replace_status_edits_once_and_sends_only_overflow_chunks():
    client = FakeTelegramClient()
    long_reply = "x" * 5000

    _run(replace_status(client, "chat-1", "status-1", long_reply))

    assert client.status_events == [("edit", "chat-1", "status-1", "x" * 4096)]
    assert [message.text for message in client.sent] == ["x" * 904]


def test_replace_status_sends_final_and_deletes_stale_status_when_edit_fails():
    class EditFailingClient(FakeTelegramClient):
        async def edit_status(self, chat_id: str, message_id: str, text: str) -> None:
            raise RuntimeError("edit unavailable")

    client = EditFailingClient()

    _run(replace_status(client, "chat-1", "status-1", "final answer"))

    assert [message.text for message in client.sent] == ["final answer"]
    assert client.status_events == [("delete", "chat-1", "status-1")]


def test_replace_status_does_not_hide_final_when_cleanup_also_fails():
    class RecoveryFailingClient(FakeTelegramClient):
        async def edit_status(self, chat_id: str, message_id: str, text: str) -> None:
            raise RuntimeError("edit unavailable")

        async def delete_status(self, chat_id: str, message_id: str) -> None:
            raise RuntimeError("delete unavailable")

    client = RecoveryFailingClient()

    _run(replace_status(client, "chat-1", "status-1", "final answer"))

    assert [message.text for message in client.sent] == ["final answer"]
