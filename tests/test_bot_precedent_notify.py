"""bot/precedent_notify.py (work_plan.md §8.6)."""

import asyncio

from bot.api_client import PrecedentClosureNotice
from bot.deps import BotDeps
from bot.precedent_notify import format_precedent_closure_notice, notify_precedent_closure
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
