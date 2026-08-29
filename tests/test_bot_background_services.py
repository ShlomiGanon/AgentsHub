"""Bot notification polling and background delivery."""

import asyncio

from bot.api_client import BotNotification, EventDataNeededNotice, FailureNotice
from bot.deps import BotDeps
from bot.notifications import deliver_failure_notification
from tests.bot_fakes import FakeBotApiClient, FakeTelegramClient


def _run(coro):
    return asyncio.run(coro)


def test_names_failed_step_and_reason_and_includes_prior_successes():
    telegram = FakeTelegramClient()
    deps = BotDeps(loaded_profile=None, telegram_client=telegram, api_client=FakeBotApiClient())

    notice = FailureNotice(
        event_id="e1",
        failed_step_agent_name="reference_agent",
        failure_reason="exhausted retries after 3 attempts",
        steps_completed_before_failure=("checked status",),
    )
    notification = BotNotification(kind="job_failed", target_chat_ids=("chat-1",), payload=notice, reply_to_message_id="msg-1")

    _run(deliver_failure_notification(deps, notification))

    text = telegram.sent[0].text
    assert "reference_agent" in text
    assert "exhausted retries" in text
    assert "checked status" in text
    assert telegram.sent[0].reply_to_message_id == "msg-1"


def test_failed_run_is_distinguishable_from_a_declined_or_uncertain_one():
    from bot.formatting import format_header

    assert format_header("failed") != format_header("declined")
    assert format_header("failed") != format_header("uncertain_verdict")

"""bot/formatting.py (work_plan.md §8.10)."""

import pytest

from bot.api_client import FailureNotice, JobResult
from bot.formatting import TELEGRAM_MESSAGE_LIMIT, format_failure_notice, format_header, format_job_result, split_message


def test_short_text_is_returned_as_one_chunk():
    assert split_message("hello") == ["hello"]


def test_empty_text_returns_one_empty_chunk():
    assert split_message("") == [""]


def test_splits_at_paragraph_boundary_when_it_fits():
    paragraph_a = "a" * 3000
    paragraph_b = "b" * 3000
    text = f"{paragraph_a}\n\n{paragraph_b}"

    chunks = split_message(text, limit=4096)

    assert len(chunks) == 2
    assert chunks[0] == paragraph_a
    assert chunks[1] == paragraph_b
    assert all(len(c) <= 4096 for c in chunks)


def test_falls_back_to_sentence_boundary_when_no_paragraph_breaks_fit():
    sentence = "x" * 100 + ". "
    text = sentence * 60  # one giant paragraph, well past a small limit

    chunks = split_message(text, limit=500)

    assert len(chunks) > 1
    assert all(len(c) <= 500 for c in chunks)
    assert all(chunk for chunk in chunks)  # no degenerate empty chunk
    # Every "x" from the original text survives somewhere in some chunk —
    # a split point may drop the separator glue between two sentences
    # (each new message needs no leading punctuation of its own, since it
    # is sent as a distinct Telegram message), but it never drops actual
    # sentence content, and no 100-x sentence is torn in half: each
    # chunk's length, minus its internal ". " glue, is a multiple of 100.
    assert sum(chunk.count("x") for chunk in chunks) == text.count("x")
    for chunk in chunks:
        assert chunk.count("x") % 100 == 0


def test_falls_back_to_hard_cut_when_a_single_run_exceeds_the_limit():
    text = "a" * 10000  # no separators at all

    chunks = split_message(text, limit=4096)

    assert len(chunks) == 3
    assert all(len(c) <= 4096 for c in chunks)
    assert "".join(chunks) == text


def test_never_produces_a_chunk_over_the_limit_default():
    text = ("paragraph one. " * 400) + "\n\n" + ("paragraph two. " * 400)
    chunks = split_message(text)
    assert all(len(c) <= TELEGRAM_MESSAGE_LIMIT for c in chunks)


@pytest.mark.parametrize(
    "kind_a,kind_b",
    [
        ("clarification_needed", "approval_needed"),
        ("approval_needed", "precedent_closure"),
        ("precedent_closure", "uncertain_verdict"),
        ("uncertain_verdict", "result"),
        ("result", "failed"),
        ("failed", "declined"),
    ],
)
def test_every_message_kind_has_a_visually_distinct_header(kind_a, kind_b):
    assert format_header(kind_a) != format_header(kind_b)


@pytest.mark.parametrize("kind", ["clarification_needed", "approval_needed"])
def test_headers_needing_a_reply_say_so(kind):
    assert "reply" in format_header(kind).lower()


@pytest.mark.parametrize("kind", ["precedent_closure", "uncertain_verdict", "no_match"])
def test_headers_needing_no_reply_say_so(kind):
    assert "no reply needed" in format_header(kind).lower()


def test_job_result_orders_verdict_then_what_was_done_then_insight():
    result = JobResult(job_id="j1", outcome="succeeded", insight_text="all clear", steps_completed=("checked status", "dispatched response"))

    text = format_job_result(result)

    verdict_pos = text.index("Verdict:")
    steps_pos = text.index("What was done:")
    insight_pos = text.index("Insight:")

    assert verdict_pos < steps_pos < insight_pos
    assert "checked status" in text
    assert "dispatched response" in text
    assert "all clear" in text


def test_declined_job_result_uses_the_declined_header():
    result = JobResult(job_id="j1", outcome="declined")
    text = format_job_result(result)
    assert format_header("declined") in text


def test_no_match_job_result_includes_the_failure_reason_text():
    result = JobResult(job_id="j1", outcome="no_match_protocol", failure_reason="no loaded protocol handles this kind of request")
    text = format_job_result(result)
    assert "no loaded protocol handles this kind of request" in text


def test_job_result_with_no_failure_reason_adds_no_extra_line():
    result = JobResult(job_id="j1", outcome="succeeded")
    text = format_job_result(result)
    assert text == "\n".join([format_header("result"), "", "Verdict: succeeded"])


def test_failure_notice_names_step_and_reason_and_prior_successes():
    notice = FailureNotice(
        event_id="e1",
        failed_step_agent_name="reference_agent",
        failure_reason="exhausted retries",
        steps_completed_before_failure=("checked status",),
    )

    text = format_failure_notice(notice)

    assert "reference_agent" in text
    assert "exhausted retries" in text
    assert "checked status" in text


def test_failure_notice_with_nothing_completed_says_so():
    notice = FailureNotice(event_id="e1", failed_step_agent_name="a1", failure_reason="boom", steps_completed_before_failure=())
    text = format_failure_notice(notice)
    assert "Nothing completed before the failure." in text


import asyncio

import pytest

from bot.api_client import (
    BotNotification,
    FailureNotice,
    HeldApprovalNotice,
    HeldClarificationNotice,
    JobResult,
    NoMatchNotice,
    PrecedentClosureNotice,
    UncertainVerdictNotice,
)
from bot.deps import BotDeps
from bot.notifications import dispatch_notification, run_notification_poll_loop, run_notification_poll_once
from tests.bot_fakes import FakeBotApiClient, FakeTelegramClient


def _run(coro):
    return asyncio.run(coro)


def _deps(api, commander_chat_ids=("c1",)):
    api.commander_chat_ids = commander_chat_ids
    return BotDeps(loaded_profile=None, telegram_client=FakeTelegramClient(), api_client=api)


@pytest.mark.parametrize(
    "kind,payload,target_chat_ids",
    [
        (
            "clarification_hold",
            HeldClarificationNotice(hold_id="h1", event_id="e1", raw_text="x", unresolved_field="classification", available_classifications=("fire",)),
            (),  # push_clarification_prompt fetches its own commander list
        ),
        (
            "approval_hold",
            HeldApprovalNotice(hold_id="h1", event_id="e1", reason="flagged_protocol", risk_level="high", risk_reason="r", selected_protocol_name="p1"),
            (),
        ),
        ("uncertain_verdict", UncertainVerdictNotice(event_id="e1", insight_text="mixed"), ()),
        ("precedent_closure", PrecedentClosureNotice(event_id="e1", raw_text="x", matched_precedent_event_id="e0", precedent_ending="succeeded"), ()),
        ("no_match_notice", NoMatchNotice(event_id="e1", raw_text="x", reason="no match", risk_level="low", risk_reason="r"), ()),
        ("job_finished", JobResult(job_id="j1", outcome="succeeded"), ("chat-9",)),
        ("job_failed", FailureNotice(event_id="e1", failed_step_agent_name="a1", failure_reason="boom"), ("chat-9",)),
        ("event_data_hold", EventDataNeededNotice(hold_id="h2", event_id="e2", question="Where?", missing_fields=("area",)), ("chat-9",)),
    ],
)
def test_dispatch_routes_every_kind_to_a_delivered_message(kind, payload, target_chat_ids):
    api = FakeBotApiClient()
    deps = _deps(api)
    notification = BotNotification(kind=kind, target_chat_ids=target_chat_ids, payload=payload, reply_to_message_id="m1")

    _run(dispatch_notification(deps, notification))

    assert len(deps.telegram_client.sent) >= 1


def test_dispatch_rejects_an_unknown_kind():
    api = FakeBotApiClient()
    deps = _deps(api)
    bad = BotNotification(kind="not_a_real_kind", target_chat_ids=(), payload=None)  # type: ignore[arg-type]

    with pytest.raises(ValueError):
        _run(dispatch_notification(deps, bad))


def test_poll_once_dispatches_every_pending_notification():
    api = FakeBotApiClient(
        commander_chat_ids=("c1",),
        pending_notifications=(
            BotNotification(kind="job_finished", target_chat_ids=("chat-1",), payload=JobResult(job_id="j1", outcome="succeeded")),
            BotNotification(kind="job_failed", target_chat_ids=("chat-2",), payload=FailureNotice(event_id="e1", failed_step_agent_name="a1", failure_reason="boom")),
        ),
    )
    deps = BotDeps(loaded_profile=None, telegram_client=FakeTelegramClient(), api_client=api)

    count, next_cursor = _run(run_notification_poll_once(deps))

    assert count == 2
    assert next_cursor == 2  # FakeBotApiClient bumps since by however many it returned
    assert len(deps.telegram_client.sent) == 2


def test_poll_loop_survives_an_unimplemented_api_and_stops_after_max_iterations():
    from bot.api_client import UnimplementedApiClient

    deps = BotDeps(loaded_profile=None, telegram_client=FakeTelegramClient(), api_client=UnimplementedApiClient())

    # Must return (not raise) after exactly 3 iterations, even though
    # every single poll fails with ApiNotImplementedError — §7.2 doesn't
    # exist yet, and that must degrade gracefully, not crash the bot.
    _run(run_notification_poll_loop(deps, poll_interval_seconds=0, max_iterations=3))


class _RaisingApiClient(FakeBotApiClient):
    """Raises a caller-supplied, non-ApiNotImplementedError exception from
    poll_pending_notifications on every call, to exercise the poll loop's
    generic `except Exception` branch (as opposed to its dedicated
    ApiNotImplementedError branch, already covered above) — a real bug in
    the client (a bad response shape, a connection error) rather than the
    known "§7.2 doesn't exist yet" case.
    """

    def __init__(self, exc: Exception, **kwargs):
        super().__init__(**kwargs)
        self._exc = exc
        self.poll_call_count = 0

    async def poll_pending_notifications(self, since: int):
        self.poll_call_count += 1
        raise self._exc


def test_poll_loop_logs_and_continues_past_a_non_api_not_implemented_error():
    # run_notification_poll_loop's `except Exception` branch (as opposed to
    # its dedicated ApiNotImplementedError branch) must log and keep
    # looping — not re-raise, not stop early — for any other exception a
    # real client implementation could raise.
    api = _RaisingApiClient(RuntimeError("connection reset"))
    deps = BotDeps(loaded_profile=None, telegram_client=FakeTelegramClient(), api_client=api)

    _run(run_notification_poll_loop(deps, poll_interval_seconds=0, max_iterations=3))

    # The loop actually ran all 3 iterations rather than stopping after the
    # first failure — the real, observable behavior, not just "no exception
    # escaped the test."
    assert api.poll_call_count == 3


import asyncio

from bot.api_client import PrecedentClosureNotice
from bot.deps import BotDeps
from bot.notifications import format_precedent_closure_notice, notify_precedent_closure
from tests.bot_fakes import FakeBotApiClient, FakeTelegramClient


def _run(coro):
    return asyncio.run(coro)


NOTICE = PrecedentClosureNotice(
    event_id="e2", raw_text="smoke reported again near the depot", matched_precedent_event_id="e1", precedent_ending="succeeded"
)


def test_notice_includes_event_precedent_and_ending():
    text = format_precedent_closure_notice(NOTICE)
    assert NOTICE.raw_text in text
    assert NOTICE.matched_precedent_event_id in text
    assert "succeeded" in text


def test_notice_is_informational_and_needs_no_reply():
    text = format_precedent_closure_notice(NOTICE)
    assert "no reply needed" in text.lower()
    assert "?" not in text


def test_pushed_to_every_commander_individually():
    api = FakeBotApiClient(commander_chat_ids=("c1", "c2"))
    telegram = FakeTelegramClient()
    deps = BotDeps(loaded_profile=None, telegram_client=telegram, api_client=api)

    _run(notify_precedent_closure(deps, NOTICE))

    assert {m.chat_id for m in telegram.sent} == {"c1", "c2"}
    assert len(telegram.sent) == 2


import asyncio

from bot.api_client import BotNotification, JobResult
from bot.deps import BotDeps
from bot.notifications import deliver_job_result
from tests.bot_fakes import FakeBotApiClient, FakeTelegramClient


def _run(coro):
    return asyncio.run(coro)


def test_delivers_to_the_original_chat_referencing_the_original_message():
    telegram = FakeTelegramClient()
    deps = BotDeps(loaded_profile=None, telegram_client=telegram, api_client=FakeBotApiClient())

    result = JobResult(job_id="job-1", outcome="succeeded", insight_text="all clear", steps_completed=("checked status",))
    notification = BotNotification(kind="job_finished", target_chat_ids=("chat-9",), payload=result, reply_to_message_id="msg-123")

    _run(deliver_job_result(deps, notification))

    assert len(telegram.sent) == 1
    sent = telegram.sent[0]
    assert sent.chat_id == "chat-9"
    assert sent.reply_to_message_id == "msg-123"
    assert "Verdict: succeeded" in sent.text
    assert "checked status" in sent.text
    assert "all clear" in sent.text
