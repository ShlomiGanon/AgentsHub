"""bot/telegram_client.py (work_plan.md §8.1, §8.10).

Exercises `PTBTelegramClient` against the real, installed
`python-telegram-bot` `Application`/`Bot` classes, with only the network-
performing methods (`Bot.get_me`, `Bot.send_message`,
`Bot.answer_callback_query`) replaced by `AsyncMock`s — everything else
(building the `Application`, routing through `bot.formatting.split_message`)
is exercised for real. See that module's docstring for what remains
unverified against a live bot token.
"""

import asyncio
from unittest.mock import AsyncMock

import pytest
import telegram.error

from bot.telegram_client import PTBTelegramClient


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def client():
    return PTBTelegramClient("123456:fake-token-for-testing")


def test_validate_token_true_when_telegram_accepts_it(client, monkeypatch):
    monkeypatch.setattr(type(client._application.bot), "get_me", AsyncMock(return_value=object()))
    assert _run(client.validate_token()) is True


def test_validate_token_false_when_telegram_rejects_it(client, monkeypatch):
    monkeypatch.setattr(type(client._application.bot), "get_me", AsyncMock(side_effect=telegram.error.InvalidToken()))
    assert _run(client.validate_token()) is False


def test_send_text_sends_one_message_when_short(client, monkeypatch):
    send = AsyncMock()
    monkeypatch.setattr(type(client._application.bot), "send_message", send)

    _run(client.send_text("chat-1", "hello"))

    send.assert_awaited_once_with(chat_id="chat-1", text="hello")


def test_send_text_splits_long_text_into_multiple_messages(client, monkeypatch):
    send = AsyncMock()
    monkeypatch.setattr(type(client._application.bot), "send_message", send)

    long_text = "a" * 9000
    _run(client.send_text("chat-1", long_text))

    assert send.await_count == 3


def test_send_with_buttons_attaches_an_inline_keyboard(client, monkeypatch):
    send = AsyncMock()
    monkeypatch.setattr(type(client._application.bot), "send_message", send)

    _run(client.send_with_buttons("chat-1", "choose", [("fire", "clarify:h1:fire"), ("medical", "clarify:h1:medical")]))

    send.assert_awaited_once()
    _, kwargs = send.await_args
    assert kwargs["chat_id"] == "chat-1"
    assert kwargs["text"] == "choose"
    markup = kwargs["reply_markup"]
    labels = [button.text for row in markup.inline_keyboard for button in row]
    assert labels == ["fire", "medical"]


def test_send_reply_references_the_original_message(client, monkeypatch):
    send = AsyncMock()
    monkeypatch.setattr(type(client._application.bot), "send_message", send)

    _run(client.send_reply("chat-1", "here's your result", "msg-42"))

    send.assert_awaited_once_with(chat_id="chat-1", text="here's your result", reply_to_message_id="msg-42")


def test_answer_callback_query_acknowledges_the_button_press(client, monkeypatch):
    answer = AsyncMock()
    monkeypatch.setattr(type(client._application.bot), "answer_callback_query", answer)

    _run(client.answer_callback_query("cbq-1", text="got it"))

    answer.assert_awaited_once_with(callback_query_id="cbq-1", text="got it")


def test_run_polling_registers_handlers_then_polls(client, monkeypatch):
    calls = []
    monkeypatch.setattr(type(client._application), "run_polling", lambda self: calls.append("polled"))

    client.run_polling(lambda application: calls.append(("registered", application is client._application)))

    assert calls == [("registered", True), "polled"]
