"""bot/simulator_transport.py: FakeBotRequest, SimulatorTelegramClient, and
synthetic-Update construction (docs/bot_simulation_mode_design.md §4.1/§8).
"""

import asyncio

import telegram
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters

from bot.simulator_transport import (
    FakeBotRequest,
    SimulatorTelegramClient,
    _stable_message_id,
    build_synthetic_text_update,
)


def _run(coro):
    return asyncio.run(coro)


def _fake_bot() -> telegram.Bot:
    return telegram.Bot(token="simulator", request=FakeBotRequest(), get_updates_request=FakeBotRequest())


# -- FakeBotRequest: no real network, satisfies PTB's own bootstrap calls -----------------


def test_fake_bot_request_satisfies_application_initialize_with_no_real_network():
    """`Application.initialize()` unconditionally calls `Bot.initialize()` ->
    `get_me()` (docs/bot_simulation_mode_design.md §1.2) — this must succeed
    without ever constructing a real HTTPXRequest or touching a network."""

    async def scenario():
        bot = _fake_bot()
        application = ApplicationBuilder().bot(bot).build()
        await application.initialize()
        assert application.bot.id is not None
        await application.shutdown()

    _run(scenario())


def test_fake_bot_request_satisfies_set_my_commands():
    """`register_handlers()`'s `post_init` hook (`bot/app.py`, reused unmodified)
    calls `set_my_commands()` — a second, different Bot-API call that must also
    succeed harmlessly, or reusing `register_handlers()` as-is would break."""

    async def scenario():
        bot = _fake_bot()
        application = ApplicationBuilder().bot(bot).build()
        await application.initialize()
        result = await application.bot.set_my_commands([("start", "start")])
        assert result is True
        await application.shutdown()

    _run(scenario())


def test_fake_bot_request_get_me_returns_a_plausible_bot_user():
    async def scenario():
        bot = _fake_bot()
        user = await bot.get_me()
        assert user.is_bot is True
        assert isinstance(user.id, int)

    _run(scenario())


# -- SimulatorTelegramClient: the outbound stub -------------------------------------------


def test_simulator_telegram_client_implements_the_full_telegram_client_abc():
    """Must be instantiable — a missing abstract method would raise TypeError here."""

    client = SimulatorTelegramClient()
    assert client.sent == []
    assert client.status_events == []


def test_run_polling_is_refused():
    client = SimulatorTelegramClient()
    try:
        client.run_polling(lambda application: None)
        assert False, "expected NotImplementedError"
    except NotImplementedError:
        pass


def test_validate_token_is_always_true():
    """No real token exists to validate in simulation mode — trivially true."""

    assert _run(SimulatorTelegramClient().validate_token()) is True


def test_reply_since_captures_the_final_edited_status_not_the_placeholder():
    """`present_incoming_message`'s normal lifecycle: send_status("thinking..."),
    then edit_status(final_text). A real Telegram user only ever sees the final
    text — reply_since must replay to that, not concatenate both."""

    async def scenario():
        client = SimulatorTelegramClient()
        mark = client.mark()
        message_id = await client.send_status("chat-1", "The model is thinking...")
        await client.edit_status("chat-1", message_id, "42 events")
        return client.reply_since(mark, "chat-1")

    assert _run(scenario()) == "42 events"


def test_reply_since_ignores_a_deleted_status_message():
    """`replace_status`'s send-and-delete failure-recovery path: the placeholder
    gets deleted, a fresh send_text carries the real reply instead."""

    async def scenario():
        client = SimulatorTelegramClient()
        mark = client.mark()
        message_id = await client.send_status("chat-1", "thinking...")
        await client.delete_status("chat-1", message_id)
        await client.send_text("chat-1", "recovered reply")
        return client.reply_since(mark, "chat-1")

    assert _run(scenario()) == "recovered reply"


def test_reply_since_is_none_when_nothing_was_sent_to_that_chat():
    async def scenario():
        client = SimulatorTelegramClient()
        mark = client.mark()
        await client.send_status("chat-1", "thinking...")
        return client.reply_since(mark, "chat-2")

    assert _run(scenario()) is None


def test_reply_since_only_covers_activity_since_the_mark():
    """Real usage pairs one `mark()`/`reply_since()` per request, read back
    immediately after that request's `process_update()` — not batched at the
    end, which would (correctly) also pick up later activity in the same chat."""

    async def scenario():
        client = SimulatorTelegramClient()

        first_mark = client.mark()
        message_id = await client.send_status("chat-1", "first thinking...")
        await client.edit_status("chat-1", message_id, "first reply")
        first = client.reply_since(first_mark, "chat-1")

        second_mark = client.mark()
        message_id2 = await client.send_status("chat-1", "second thinking...")
        await client.edit_status("chat-1", message_id2, "second reply")
        second = client.reply_since(second_mark, "chat-1")

        return first, second

    first, second = _run(scenario())
    assert first == "first reply"
    assert second == "second reply"


def test_reply_since_joins_multiple_distinct_messages_in_order():
    """A status edit plus a separate send_with_buttons (e.g. the attendance
    prompt) are two distinct messages a real user would see, in send order."""

    async def scenario():
        client = SimulatorTelegramClient()
        mark = client.mark()
        message_id = await client.send_status("chat-1", "thinking...")
        await client.edit_status("chat-1", message_id, "the answer")
        await client.send_with_buttons("chat-1", "pick one", [("Yes", "yes"), ("No", "no")])
        return client.reply_since(mark, "chat-1")

    assert _run(scenario()) == "the answer\npick one"


# -- build_synthetic_text_update: real PTB filters classify it correctly ------------------


def test_synthetic_update_is_classified_as_plain_text_not_a_command():
    """No `entities` on the synthetic Message -> filters.COMMAND never matches
    (docs/bot_simulation_mode_design.md §1.2's precise finding), so every
    scenario message routes to the plain-text handler, never a CommandHandler."""

    bot = _fake_bot()
    update = build_synthetic_text_update(
        update_id=1, source_message_id="s1", sender_identity="9000000000000002",
        chat_id="9000000000000002", chat_type="private", text="not a command, just text", bot=bot,
    )
    assert filters.TEXT.check_update(update)
    assert not filters.COMMAND.check_update(update)
    assert (filters.TEXT & ~filters.COMMAND).check_update(update)


def test_synthetic_update_never_matches_a_command_handler():
    async def scenario():
        bot = _fake_bot()
        application = ApplicationBuilder().bot(bot).build()
        calls = []

        async def cmd_handler(update, context):
            calls.append("cmd")

        async def text_handler(update, context):
            calls.append("text")

        application.add_handler(CommandHandler("start", cmd_handler))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
        await application.initialize()
        await application.start()

        update = build_synthetic_text_update(
            update_id=1, source_message_id="s1", sender_identity="9000000000000002",
            chat_id="9000000000000002", chat_type="private", text="hello", bot=bot,
        )
        await application.process_update(update)

        await application.stop()
        await application.shutdown()
        return calls

    assert _run(scenario()) == ["text"]


def test_synthetic_update_carries_the_right_identities_and_text():
    bot = _fake_bot()
    update = build_synthetic_text_update(
        update_id=7, source_message_id="s1", sender_identity="9000000000000002",
        chat_id="-9000000000000001", chat_type="supergroup", text="hi team", bot=bot,
    )
    assert update.update_id == 7
    assert update.effective_user.id == 9000000000000002
    assert update.effective_chat.id == -9000000000000001
    assert update.effective_chat.type == "supergroup"
    assert update.message.text == "hi team"


def test_stable_message_id_is_deterministic_and_positive():
    first = _stable_message_id("sim-step-3")
    second = _stable_message_id("sim-step-3")
    other = _stable_message_id("sim-step-4")
    assert first == second
    assert first > 0
    assert first != other


def test_synthetic_update_reuses_the_same_message_id_for_the_same_source_message_id():
    """A re-run with the same source_message_id must produce the same synthetic
    message_id, preserving /Msg's dedup-on-source_message_id behavior once the
    real handler re-derives str(update.message.message_id) as its own
    source_message_id (docs/bot_simulation_mode_design.md §10)."""

    bot = _fake_bot()
    first = build_synthetic_text_update(
        update_id=1, source_message_id="sim-step-3", sender_identity="9000000000000002",
        chat_id="9000000000000002", chat_type="private", text="a", bot=bot,
    )
    second = build_synthetic_text_update(
        update_id=2, source_message_id="sim-step-3", sender_identity="9000000000000002",
        chat_id="9000000000000002", chat_type="private", text="b", bot=bot,
    )
    assert first.message.message_id == second.message.message_id
