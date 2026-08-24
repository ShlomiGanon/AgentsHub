import time
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


def _wait_for_a_background_pass(scheduler, attempts=200):
    for _ in range(attempts):
        if scheduler.last_run_status()["last_run_at"] is not None:
            return
        time.sleep(0.01)
    raise AssertionError("background scheduler never completed a pass")


def test_last_run_status_is_unset_before_the_background_thread_ever_runs(tmp_path):
    store = open_persistence(str(tmp_path / "unset.db"))
    try:
        scheduler = SummaryScheduler(store, FakeHistoryAgent())

        assert scheduler.last_run_status() == {"last_run_at": None, "last_run_ok": None, "last_run_error": None}
    finally:
        store.close()


def test_last_run_status_reports_success_after_a_background_pass(tmp_path):
    store = open_persistence(str(tmp_path / "last_run_ok.db"))
    try:
        scheduler = SummaryScheduler(
            store,
            FakeHistoryAgent(),
            clock=lambda: datetime(2026, 2, 1, tzinfo=timezone.utc),
            poll_interval_seconds=0.01,
        )

        scheduler.start()
        _wait_for_a_background_pass(scheduler)
        scheduler.stop()

        status = scheduler.last_run_status()
        assert status["last_run_ok"] is True
        assert status["last_run_error"] is None
        assert status["last_run_at"] is not None
    finally:
        store.close()


def test_last_run_status_reports_failure_without_stopping_the_scheduler(tmp_path):
    store = open_persistence(str(tmp_path / "last_run_fail.db"))
    try:
        scheduler = SummaryScheduler(store, FakeHistoryAgent(), poll_interval_seconds=0.01)
        scheduler.reconcile = lambda: (_ for _ in ()).throw(RuntimeError("boom"))

        scheduler.start()
        _wait_for_a_background_pass(scheduler)
        scheduler.stop()

        status = scheduler.last_run_status()
        assert status["last_run_ok"] is False
        assert "boom" in status["last_run_error"]
    finally:
        store.close()
