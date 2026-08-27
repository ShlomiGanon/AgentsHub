"""bot/approval.py (work_plan.md §8.5)."""

import asyncio

import pytest

from bot.api_client import HeldApprovalNotice, HoldAnswerOutcome, NoMatchNotice, UncertainVerdictNotice
from bot.approval import (
    build_callback_data,
    format_approval_prompt,
    handle_approval_answer,
    notify_no_match,
    notify_uncertain_verdict,
    parse_callback_data,
    push_approval_prompt,
)
from bot.deps import BotDeps
from tests.bot_fakes import FakeBotApiClient, FakeTelegramClient


def _run(coro):
    return asyncio.run(coro)


FLAGGED_NOTICE = HeldApprovalNotice(
    hold_id="hold-1", event_id="e1", reason="flagged_protocol", risk_level="high", risk_reason="side-effecting action",
    selected_protocol_name="dispatch_response",
)

AMBIGUOUS_NOTICE = HeldApprovalNotice(
    hold_id="hold-2", event_id="e2", reason="ambiguous_selection", risk_level="low", risk_reason="tie between candidates",
    candidate_protocol_names=("minor_incident_review", "routine_check"),
)


def test_flagged_protocol_prompt_asks_yes_no():
    text, buttons = format_approval_prompt(FLAGGED_NOTICE)

    assert "Should this run?" in text
    assert "dispatch_response" in text
    assert [label for label, _ in buttons] == ["Approve", "Reject"]


def test_ambiguous_selection_prompt_shows_candidates_as_buttons():
    text, buttons = format_approval_prompt(AMBIGUOUS_NOTICE)

    assert "minor_incident_review" in text
    assert "routine_check" in text
    assert [label for label, _ in buttons] == ["minor_incident_review", "routine_check"]


def test_an_unrecognized_reason_raises_instead_of_rendering_as_ambiguous():
    # Found live: a stale "no_match"-reason relic (no_match's old,
    # since-removed hold-based design — see orchestrator.holds
    # .determine_approval_hold's own docstring) silently fell through to
    # the ambiguous_selection rendering, showing "Multiple protocols fit
    # equally well: (none)" for a hold that was never actually ambiguous.
    # format_approval_prompt must now fail loudly on any reason value
    # that isn't one of the two it actually knows how to render, rather
    # than quietly mis-rendering it as the other one.
    stale_notice = HeldApprovalNotice(hold_id="hold-3", event_id="e3", reason="no_match", risk_level="low", risk_reason="r")

    with pytest.raises(ValueError):
        format_approval_prompt(stale_notice)


def test_the_two_reasons_produce_different_prompt_text():
    flagged_text, _ = format_approval_prompt(FLAGGED_NOTICE)
    ambiguous_text, _ = format_approval_prompt(AMBIGUOUS_NOTICE)
    assert flagged_text != ambiguous_text


def test_prompt_buttons_encode_event_id_not_hold_id():
    # FLAGGED_NOTICE.hold_id ("hold-1") and .event_id ("e1") deliberately
    # differ, so this fails loudly if the callback data ever regresses to
    # encoding the orchestrator's internal hold ID again — api/holds.py's
    # POST /Approve/<event_id> (§7.11) only ever accepts an event ID.
    _text, flagged_buttons = format_approval_prompt(FLAGGED_NOTICE)
    for _label, callback_data in flagged_buttons:
        assert FLAGGED_NOTICE.event_id in callback_data
        assert FLAGGED_NOTICE.hold_id not in callback_data

    _text, ambiguous_buttons = format_approval_prompt(AMBIGUOUS_NOTICE)
    for _label, callback_data in ambiguous_buttons:
        assert AMBIGUOUS_NOTICE.event_id in callback_data
        assert AMBIGUOUS_NOTICE.hold_id not in callback_data


def test_pushed_to_every_commander():
    api = FakeBotApiClient(commander_chat_ids=("c1", "c2"))
    telegram = FakeTelegramClient()
    deps = BotDeps(loaded_profile=None, telegram_client=telegram, api_client=api)

    _run(push_approval_prompt(deps, FLAGGED_NOTICE))

    assert {m.chat_id for m in telegram.sent} == {"c1", "c2"}


def test_uncertain_verdict_is_not_phrased_as_a_question_and_has_no_buttons():
    api = FakeBotApiClient(commander_chat_ids=("c1",))
    telegram = FakeTelegramClient()
    deps = BotDeps(loaded_profile=None, telegram_client=telegram, api_client=api)

    _run(notify_uncertain_verdict(deps, UncertainVerdictNotice(event_id="e1", insight_text="mixed signal")))

    assert telegram.sent[-1].buttons is None
    assert "?" not in telegram.sent[-1].text
    assert "no reply needed" in telegram.sent[-1].text.lower()


def test_no_match_notice_is_not_phrased_as_a_question_and_has_no_buttons():
    # NO_MATCH is a real terminal outcome plus a one-way notification, not
    # a hold — same shape as notify_uncertain_verdict, never routed through
    # format_approval_prompt/push_approval_prompt at all.
    api = FakeBotApiClient(commander_chat_ids=("c1",))
    telegram = FakeTelegramClient()
    deps = BotDeps(loaded_profile=None, telegram_client=telegram, api_client=api)

    notice = NoMatchNotice(
        event_id="e1", raw_text="tell the viewer they need to come to me",
        reason="no loaded protocol handles this kind of request", risk_level="low", risk_reason="informational",
    )
    _run(notify_no_match(deps, notice))

    assert telegram.sent[-1].buttons is None
    assert "?" not in telegram.sent[-1].text
    assert "no reply needed" in telegram.sent[-1].text.lower()
    assert notice.reason in telegram.sent[-1].text


def test_no_match_notice_reaches_every_commander():
    api = FakeBotApiClient(commander_chat_ids=("c1", "c2"))
    telegram = FakeTelegramClient()
    deps = BotDeps(loaded_profile=None, telegram_client=telegram, api_client=api)

    notice = NoMatchNotice(event_id="e1", raw_text="raw", reason="no match", risk_level="low", risk_reason="informational")
    _run(notify_no_match(deps, notice))

    assert {m.chat_id for m in telegram.sent} == {"c1", "c2"}


def test_callback_data_round_trips():
    assert parse_callback_data(build_callback_data("e1", "approved")) == ("e1", "approved")


def test_viewer_cannot_approve():
    api = FakeBotApiClient(users={"v1": "viewer"})
    telegram = FakeTelegramClient()
    deps = BotDeps(loaded_profile=None, telegram_client=telegram, api_client=api)

    _run(handle_approval_answer(deps, "chat-1", "v1", "e1", "approved"))

    assert "approve_run" in telegram.sent[-1].text
    assert not any(c[0] == "answer_approval_hold" for c in api.calls)


def test_commander_approves_and_gets_confirmation():
    api = FakeBotApiClient(users={"c1": "commander"}, approval_answer_outcome=HoldAnswerOutcome(status="approved"))
    telegram = FakeTelegramClient()
    deps = BotDeps(loaded_profile=None, telegram_client=telegram, api_client=api)

    _run(handle_approval_answer(deps, "chat-1", "c1", "e1", "approved"))

    assert api.calls[-1] == ("answer_approval_hold", "e1", "approved", "c1")
    assert "resumed" in telegram.sent[-1].text.lower()


def test_commander_rejects_and_gets_confirmation():
    api = FakeBotApiClient(users={"c1": "commander"}, approval_answer_outcome=HoldAnswerOutcome(status="rejected"))
    telegram = FakeTelegramClient()
    deps = BotDeps(loaded_profile=None, telegram_client=telegram, api_client=api)

    _run(handle_approval_answer(deps, "chat-1", "c1", "e1", "rejected"))

    assert "declined" in telegram.sent[-1].text.lower()


def test_an_invalid_candidate_name_reports_the_apis_message():
    # HoldAnswerStatus gained "invalid_candidate" (§7.12) for a candidate
    # protocol name outside an ambiguous-selection hold's own list — this
    # confirms the bot side has somewhere real to render it, not just a
    # type that accepts the value.
    api = FakeBotApiClient(
        users={"c1": "commander"},
        approval_answer_outcome=HoldAnswerOutcome(status="invalid_candidate", message="'bogus' is not one of this hold's candidates: ['a', 'b']"),
    )
    telegram = FakeTelegramClient()
    deps = BotDeps(loaded_profile=None, telegram_client=telegram, api_client=api)

    _run(handle_approval_answer(deps, "chat-1", "c1", "e2", "bogus"))

    assert "not one of this hold's candidates" in telegram.sent[-1].text


def test_second_answer_to_an_already_answered_hold_names_who_answered_it():
    api = FakeBotApiClient(
        users={"c2": "commander"},
        approval_answer_outcome=HoldAnswerOutcome(status="not_found", resolved_by="c1", message="already answered"),
    )
    telegram = FakeTelegramClient()
    deps = BotDeps(loaded_profile=None, telegram_client=telegram, api_client=api)

    _run(handle_approval_answer(deps, "chat-2", "c2", "e1", "approved"))

    assert "already answered by c1" in telegram.sent[-1].text.lower()
