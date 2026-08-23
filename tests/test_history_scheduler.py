from datetime import datetime, timezone

from agents.results import AgentResult
from history.interface import SummaryScheduler
from persistence.interface import open_persistence


class FakeHistoryAgent:
    def process(self, text, allowed_tools):
        return AgentResult("success", text)


def test_reconciliation_builds_bottom_up_and_is_idempotent(tmp_path):
    store = open_persistence(str(tmp_path / "scheduler.db"))
    try:
        store.append_event({
            "event_id": "e1", "received_at": "2025-08-15T10:00:00", "source": "sensor",
            "sender_identity": "s", "occurred_at": "2025-08-15T10:00:00", "raw_text": "fire",
            "classification": "fire", "area": "north", "outcome": "succeeded",
        })
        scheduler = SummaryScheduler(
            store,
            FakeHistoryAgent(),
            clock=lambda: datetime(2026, 2, 1, tzinfo=timezone.utc),
            poll_interval_seconds=0.01,
        )

        first = scheduler.reconcile()
        second = scheduler.reconcile()

        assert [action.split(":")[0] for action in first] == ["daily", "monthly", "yearly"]
        assert second == []
        scheduler.start()
        scheduler.stop()
    finally:
        store.close()


def test_late_telegram_notification_wakes_only_for_existing_stale_day(tmp_path):
    store = open_persistence(str(tmp_path / "late.db"))
    try:
        store.write_summary("daily", {
            "summary_text": "old",
            "period_start": "2025-08-15T00:00:00",
            "period_end": "2025-08-16T00:00:00",
            "generated_at": "2025-08-16T00:01:00",
            "event_index": [],
        })
        scheduler = SummaryScheduler(store, FakeHistoryAgent())
        scheduler.notify_event_written("e", "telegram", "2025-08-15T10:00:00", "2025-08-17T10:00:00")

        assert scheduler._wake_event.is_set()
    finally:
        store.close()
