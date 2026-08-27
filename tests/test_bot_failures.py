"""bot/notifications.py (work_plan.md §8.11)."""

import asyncio

from bot.api_client import BotNotification, FailureNotice
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
