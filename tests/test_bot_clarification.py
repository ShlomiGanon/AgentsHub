"""bot/holds.py (work_plan.md §8.4)."""

import asyncio

from bot.api_client import HeldClarificationNotice, HoldAnswerOutcome
from bot.holds import (
    build_clarification_callback_data as build_callback_data,
    format_clarification_prompt,
    handle_clarification_answer,
    parse_clarification_callback_data as parse_callback_data,
    push_clarification_prompt,
)
from bot.deps import BotDeps
from tests.bot_fakes import FakeBotApiClient, FakeTelegramClient


def _run(coro):
    return asyncio.run(coro)


NOTICE = HeldClarificationNotice(
    hold_id="hold-1",
    event_id="event-1",
    raw_text="saw smoke near the depot",
    unresolved_field="classification",
    available_classifications=("fire", "medical"),
)


def test_callback_data_round_trips():
    data = build_callback_data("event-1", "fire")
    assert parse_callback_data(data) == ("event-1", "fire")


def test_prompt_shows_raw_text_and_unresolved_field():
    text = format_clarification_prompt(NOTICE)
    assert NOTICE.raw_text in text
    assert "classification" in text
    assert "[CLARIFICATION NEEDED" in text


def test_pushed_to_every_commander_with_buttons_not_free_text():
    api = FakeBotApiClient(commander_chat_ids=("c1", "c2"))
    telegram = FakeTelegramClient()
    deps = BotDeps(loaded_profile=None, telegram_client=telegram, api_client=api)

    _run(push_clarification_prompt(deps, NOTICE))

    assert {m.chat_id for m in telegram.sent} == {"c1", "c2"}
    for message in telegram.sent:
        assert message.buttons == (("fire", "clarify:event-1:fire"), ("medical", "clarify:event-1:medical"))


def test_pushed_buttons_encode_event_id_not_hold_id():
    # NOTICE.hold_id ("hold-1") and NOTICE.event_id ("event-1") deliberately
    # differ, so this fails loudly if the callback data ever regresses to
    # encoding the orchestrator's internal hold ID again — api/operations.py's
    # POST /Clarify/<event_id> (§7.11) only ever accepts an event ID.
    api = FakeBotApiClient(commander_chat_ids=("c1",))
    telegram = FakeTelegramClient()
    deps = BotDeps(loaded_profile=None, telegram_client=telegram, api_client=api)

    _run(push_clarification_prompt(deps, NOTICE))

    for _label, callback_data in telegram.sent[0].buttons:
        assert NOTICE.event_id in callback_data
        assert NOTICE.hold_id not in callback_data


def test_unregistered_answerer_is_refused():
    api = FakeBotApiClient(users={})
    telegram = FakeTelegramClient()
    deps = BotDeps(loaded_profile=None, telegram_client=telegram, api_client=api)

    _run(handle_clarification_answer(deps, "chat-1", "stranger", "event-1", "fire"))

    assert "not a registered user" in telegram.sent[-1].text


def test_viewer_cannot_resolve_a_hold():
    api = FakeBotApiClient(users={"v1": "viewer"})
    telegram = FakeTelegramClient()
    deps = BotDeps(loaded_profile=None, telegram_client=telegram, api_client=api)

    _run(handle_clarification_answer(deps, "chat-1", "v1", "event-1", "fire"))

    assert "resolve_hold" in telegram.sent[-1].text
    assert not api.calls or api.calls[-1][0] != "answer_clarification_hold"


def test_commander_answer_resumes_and_confirms():
    api = FakeBotApiClient(
        users={"c1": "commander"},
        clarification_answer_outcome=HoldAnswerOutcome(status="resolved"),
    )
    telegram = FakeTelegramClient()
    deps = BotDeps(loaded_profile=None, telegram_client=telegram, api_client=api)

    _run(handle_clarification_answer(deps, "chat-1", "c1", "event-1", "fire"))

    assert api.calls[-1] == ("answer_clarification_hold", "event-1", "fire", "c1")
    assert "resumed" in telegram.sent[-1].text.lower()


def test_second_answer_to_an_already_resolved_hold_names_who_resolved_it():
    api = FakeBotApiClient(
        users={"c2": "commander"},
        clarification_answer_outcome=HoldAnswerOutcome(status="not_found", resolved_by="c1", message="already resolved"),
    )
    telegram = FakeTelegramClient()
    deps = BotDeps(loaded_profile=None, telegram_client=telegram, api_client=api)

    _run(handle_clarification_answer(deps, "chat-2", "c2", "event-1", "medical"))

    assert "already resolved by c1" in telegram.sent[-1].text.lower()
