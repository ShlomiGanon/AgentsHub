"""bot/entrypoint.py (work_plan.md §8.3)."""

import asyncio

from bot.api_client import MessageSubmissionResult
from bot.entrypoint import handle_incoming_message
from tests.bot_fakes import FakeBotApiClient


def _run(coro):
    return asyncio.run(coro)


def test_unregistered_sender_is_refused_before_anything_is_submitted():
    api = FakeBotApiClient(users={})

    reply = _run(handle_incoming_message(_deps(api), "stranger", "there is a fire", "m1"))

    assert "not a registered user" in reply
    assert ("submit_message",) not in [c[:1] for c in api.calls]


def test_question_returns_the_answer_directly():
    api = FakeBotApiClient(
        users={"v1": "viewer"},
        message_submission_result=MessageSubmissionResult(kind="question", answer_text="12 events last week."),
    )

    reply = _run(handle_incoming_message(_deps(api), "v1", "how many events last week?", "m1"))

    assert reply == "12 events last week."


def test_report_acknowledges_with_job_id_and_kind():
    api = FakeBotApiClient(
        users={"v1": "viewer"},
        message_submission_result=MessageSubmissionResult(kind="report", job_id="job-42"),
    )

    reply = _run(handle_incoming_message(_deps(api), "v1", "there is smoke near the depot", "m1"))

    assert "report" in reply
    assert "job-42" in reply


def test_request_awaiting_approval_says_so():
    api = FakeBotApiClient(
        users={"c1": "commander"},
        message_submission_result=MessageSubmissionResult(kind="request", awaiting_approval=True),
    )

    reply = _run(handle_incoming_message(_deps(api), "c1", "dispatch a response", "m1"))

    assert "request" in reply
    assert "approval" in reply.lower()


def test_the_real_message_id_is_forwarded_to_submit_message():
    api = FakeBotApiClient(
        users={"v1": "viewer"},
        message_submission_result=MessageSubmissionResult(kind="report", job_id="job-42"),
    )

    _run(handle_incoming_message(_deps(api), "v1", "there is smoke near the depot", "9988"))

    assert ("submit_message", "there is smoke near the depot", "v1", "9988") in api.calls


def _deps(api):
    from bot.deps import BotDeps

    return BotDeps(loaded_profile=None, telegram_client=None, api_client=api)
