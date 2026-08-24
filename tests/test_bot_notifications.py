"""bot/notifications.py — the shared proactive-push dispatcher introduced
for work_plan.md §8.4 and reused by §8.5, §8.6, §8.9, §8.11.
"""

import asyncio

import pytest

from bot.api_client import (
    BotNotification,
    FailureNotice,
    HeldApprovalNotice,
    HeldClarificationNotice,
    JobResult,
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
        ("job_finished", JobResult(job_id="j1", outcome="succeeded"), ("chat-9",)),
        ("job_failed", FailureNotice(event_id="e1", failed_step_agent_name="a1", failure_reason="boom"), ("chat-9",)),
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
