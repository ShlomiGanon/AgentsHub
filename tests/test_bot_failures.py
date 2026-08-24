"""bot/failures.py (work_plan.md §8.11)."""

import asyncio

from bot.api_client import BotNotification, FailureNotice
from bot.deps import BotDeps
from bot.failures import deliver_failure_notification
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
