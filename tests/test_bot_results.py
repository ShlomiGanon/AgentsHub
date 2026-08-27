"""bot/notifications.py (work_plan.md §8.9)."""

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
