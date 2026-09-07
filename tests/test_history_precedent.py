from config.live_settings import SettingsStore
from history.query import HistoryQueryService
from persistence.interface import open_persistence


class UnusedAgent:
    def process(self, text, allowed_tools):
        raise AssertionError("precedent lookup must not call the model")


def test_precedent_search_uses_summary_candidates_and_raw_gaps(tmp_path):
    db_path = str(tmp_path / "precedent.db")
    store = open_persistence(db_path)
    try:
        for event_id, occurred_at, outcome in (
            ("resolved", "2026-08-18T09:00:00", "succeeded"),
            ("unresolved", "2026-08-19T09:00:00", None),
        ):
            store.append_event({
                "event_id": event_id, "received_at": occurred_at, "source": "sensor",
                "sender_identity": "s", "occurred_at": occurred_at, "raw_text": event_id,
                "classification": "fire", "area": "north", "outcome": outcome,
                "selected_protocol": "basic", "steps": [],
            })
        store.write_summary("daily", {
            "summary_text": "resolved fire",
            "period_start": "2026-08-18T00:00:00",
            "period_end": "2026-08-19T00:00:00",
            "generated_at": "2026-08-19T00:05:00",
            "event_index": [{"event_id": "resolved", "classification": "fire", "area": "north", "occurred_at": "2026-08-18T09:00:00", "outcome": "succeeded", "resolved": True}],
        })
        settings = SettingsStore(db_path, 1, 0.5, 30)
        service = HistoryQueryService(store, UnusedAgent(), settings)

        matches = service.search_precedents("target", "fire", "north", "2026-08-20T12:00:00")

        assert {match.event_id for match in matches} == {"resolved", "unresolved"}
        assert next(match for match in matches if match.event_id == "resolved").resolved is True
        assert next(match for match in matches if match.event_id == "unresolved").resolved is False
    finally:
        store.close()

def test_precedent_search_logs_the_window_and_the_matches(tmp_path, caplog):
    db_path = str(tmp_path / "precedent_log.db")
    store = open_persistence(db_path)
    try:
        store.append_event({
            "event_id": "resolved", "received_at": "2026-08-18T09:00:00", "source": "sensor",
            "sender_identity": "s", "occurred_at": "2026-08-18T09:00:00", "raw_text": "resolved",
            "classification": "fire", "area": "north", "outcome": "succeeded",
            "selected_protocol": "basic", "steps": [],
        })
        settings = SettingsStore(db_path, 1, 0.5, 30)
        service = HistoryQueryService(store, UnusedAgent(), settings)

        # DEBUG, not INFO — this is the search's own internal detail (the
        # window it computed, the raw candidate list); the outcome an
        # operator needs is logged separately, at INFO, once closure is
        # evaluated (orchestrator.flows's "precedent_closure" event).
        with caplog.at_level("DEBUG"):
            matches = service.search_precedents("target", "fire", "north", "2026-08-20T12:00:00")

        lookups = [r for r in caplog.records if getattr(r, "event", None) == "precedent_lookup"]
        assert len(lookups) == 1
        assert lookups[0].levelname == "DEBUG"
        assert lookups[0].target_event_id == "target"
        assert lookups[0].classification == "fire"
        assert lookups[0].window_start
        assert lookups[0].window_end
        assert lookups[0].matched_event_ids == [m.event_id for m in matches]
    finally:
        store.close()

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
