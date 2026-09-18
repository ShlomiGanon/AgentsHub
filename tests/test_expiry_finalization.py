from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import time

from agents import ToolReceipt
from history import SummaryScheduler
from orchestrator.event_queue import SerialEventQueue, WorkItem
from orchestrator.flows import finalize_expired_event, finalize_expired_events
from persistence.sqlite_store import SQLitePersistence


NOW = datetime(2026, 9, 17, 16, 0, tzinfo=timezone.utc)


def _event(**overrides):
    value = {
        "event_id": overrides.pop("event_id", None),
        "received_at": "2026-09-17T09:00:00",
        "source": "telegram",
        "sender_identity": "viewer-1",
        "raw_text": "test event",
        "deadline_at": "2026-09-17T10:00:00",
    }
    value.update(overrides)
    if value["event_id"] is None:
        value.pop("event_id")
    return value


def _deps(store):
    return SimpleNamespace(persistence=store)


def test_expired_event_is_finalized_once_and_notification_is_not_duplicated(tmp_path):
    store = SQLitePersistence(str(tmp_path / "expiry.db"))
    try:
        event_id = store.append_event(_event())
        first = finalize_expired_event(_deps(store), event_id, NOW)
        second = finalize_expired_event(_deps(store), event_id, NOW)

        event = store.fetch_event(event_id)
        assert first.status == "finalized"
        assert first.finalization_reason == "deadline_expired"
        assert second.status == "skipped"
        assert second.skip_reason == "already_reconciled"
        assert event["outcome"] == "failed"
        assert event["outcome_failure_reason"] == "deadline_expired"
        assert [row["kind"] for row in store.fetch_notifications_since(0)] == ["job_failed"]
    finally:
        store.close()


def test_hold_expiry_resolves_hold_and_uses_hold_specific_reason(tmp_path):
    store = SQLitePersistence(str(tmp_path / "hold-expiry.db"))
    try:
        event_id = store.append_event(_event(action_state="pending_approval"))
        hold_id = store.store_held_event("approval", {"event_id": event_id, "reason": "flagged_protocol"})

        result = finalize_expired_event(_deps(store), event_id, NOW, recovery_mode=True)
        hold = store.fetch_held_event("approval", event_id)
        event = store.fetch_event(event_id)

        assert result.status == "finalized"
        assert result.hold_kinds == ("approval",)
        assert result.resolved_hold_ids == (hold_id,)
        assert event["outcome_failure_reason"] == "approval_expired"
        assert event["action_state"] == "failed"
        assert hold["resolved"] is True
        assert hold["resolved_by"] == "system:expiry_finalizer"
        assert hold["resolution"]["decision"] == "expired"
        assert [row["kind"] for row in store.fetch_notifications_since(0)] == ["approval_hold"]
    finally:
        store.close()


def test_successful_receipt_reconciles_expired_executing_event_without_replaying_side_effect(tmp_path):
    store = SQLitePersistence(str(tmp_path / "receipt-expiry.db"))
    try:
        receipt = ToolReceipt(
            tool_name="dispatch_unit",
            status="succeeded",
            success=True,
            started_at="2026-09-17T09:55:00",
            completed_at="2026-09-17T09:56:00",
            event_id="receipt-event",
            side_effecting=True,
            state_verified=True,
            verification_source="test",
            receipt_id="receipt-1",
        )
        event_id = store.append_event(
            _event(
                event_id="receipt-event",
                action_state="executing",
                action_tool_receipts=[receipt.__dict__],
            )
        )

        result = finalize_expired_event(_deps(store), event_id, NOW, recovery_mode=True)
        event = store.fetch_event(event_id)

        assert result.status == "finalized"
        assert result.outcome == "succeeded"
        assert result.recovery_evidence == ("tool:dispatch_unit:receipt-1",)
        assert event["action_state"] == "executed"
        assert len(event["action_tool_receipts"]) == 1
        assert store.fetch_notifications_since(0) == []
    finally:
        store.close()


def test_future_event_is_not_a_recovery_candidate(tmp_path):
    store = SQLitePersistence(str(tmp_path / "future.db"))
    try:
        event_id = store.append_event(_event(deadline_at="2026-09-17T17:00:00"))
        assert finalize_expired_events(_deps(store), NOW) == ()
        assert store.fetch_event(event_id)["outcome"] is None
    finally:
        store.close()


def test_expired_queue_item_calls_finalizer_and_does_not_run_work(tmp_path):
    store = SQLitePersistence(str(tmp_path / "queue-expiry.db"))
    queue = None
    try:
        event_id = store.append_event(_event())
        processed = []

        def on_expired(payload):
            finalize_expired_event(_deps(store), payload[0], NOW)

        queue = SerialEventQueue(lambda _payload: processed.append(True), expired_item_callback=on_expired)
        queue.submit(WorkItem((event_id, lambda: processed.append(True)), deadline_monotonic=0.0))
        assert queue.active_event_ids() == (event_id,)
        queue.start()
        queue.wait_until_idle()

        assert processed == []
        assert store.fetch_event(event_id)["outcome"] == "failed"
    finally:
        if queue is not None:
            queue.stop()
        store.close()


def test_summary_scheduler_runs_maintenance_callback_before_reconcile(tmp_path):
    store = SQLitePersistence(str(tmp_path / "scheduler.db"))
    calls = []
    scheduler = None
    try:
        scheduler = SummaryScheduler(
            store,
            SimpleNamespace(process=lambda _prompt, allowed_tools: SimpleNamespace(status="success", text="ok")),
            clock=lambda: NOW,
            poll_interval_seconds=0.01,
            maintenance_callback=lambda now: calls.append(now),
        )
        scheduler.start()
        scheduler._wake_event.set()
        for _ in range(20):
            if calls:
                break
            time.sleep(0.01)
        assert calls
    finally:
        if scheduler is not None:
            scheduler.stop()
        store.close()


def test_runtime_sweeper_finalizes_event_data_hold_without_restart(tmp_path):
    store = SQLitePersistence(str(tmp_path / "runtime-expiry.db"))
    scheduler = None
    try:
        event_id = store.append_event(
            _event(deadline_at=(datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat())
        )
        hold_id = store.store_held_event(
            "event_data",
            {"event_id": event_id, "missing_fields": ["entities"]},
        )
        deps = _deps(store)
        queue = SimpleNamespace(active_event_ids=lambda: ())
        calls = []

        def sweep(now):
            calls.extend(
                finalize_expired_events(
                    deps,
                    now,
                    active_event_ids=queue.active_event_ids(),
                )
            )

        scheduler = SummaryScheduler(
            store,
            SimpleNamespace(process=lambda _prompt, _allowed_tools: SimpleNamespace(status="success", text="ok")),
            poll_interval_seconds=0.02,
            maintenance_callback=sweep,
        )
        scheduler.start()
        scheduler._wake_event.set()

        deadline = time.monotonic() + 4
        while time.monotonic() < deadline and store.fetch_event(event_id)["outcome"] is None:
            time.sleep(0.02)

        event = store.fetch_event(event_id)
        hold = store.fetch_held_event("event_data", event_id)
        failed_notifications = [
            item for item in store.fetch_notifications_since(0)
            if item["event_id"] == event_id and item["kind"] == "job_failed"
        ]

        assert calls
        assert scheduler._thread is not None and scheduler._thread.is_alive()
        assert scheduler.last_run_status()["last_run_ok"] is True
        assert event["outcome"] == "failed"
        assert event["outcome_failure_reason"] == "required_event_data_expired"
        assert event["action_tool_receipts"] is None
        assert hold["resolved"] is True
        assert hold["hold_id"] == hold_id
        assert hold["resolved_by"] == "system:expiry_finalizer"
        assert len(failed_notifications) == 1

        time.sleep(0.08)
        failed_notifications_after = [
            item for item in store.fetch_notifications_since(0)
            if item["event_id"] == event_id and item["kind"] == "job_failed"
        ]
        assert len(failed_notifications_after) == 1
    finally:
        if scheduler is not None:
            scheduler.stop()
        store.close()


def test_expiry_sweep_preserves_active_event_then_finalizes_after_release(tmp_path):
    store = SQLitePersistence(str(tmp_path / "active-expiry.db"))
    try:
        event_id = store.append_event(_event())
        deps = _deps(store)

        protected = finalize_expired_events(deps, NOW, active_event_ids=(event_id,))
        assert protected[0].status == "skipped"
        assert protected[0].skip_reason == "active_processing"
        assert store.fetch_event(event_id)["outcome"] is None

        released = finalize_expired_events(deps, NOW)
        assert released[0].status == "finalized"
        assert store.fetch_event(event_id)["outcome"] == "failed"
    finally:
        store.close()


def test_startup_recovery_pass_is_silent_and_idempotent(tmp_path):
    store = SQLitePersistence(str(tmp_path / "startup-expiry.db"))
    try:
        event_id = store.append_event(_event())
        store.store_held_event("event_data", {"event_id": event_id, "missing_fields": ["entities"]})
        before = len(store.fetch_notifications_since(0))

        first = finalize_expired_events(_deps(store), NOW, recovery_mode=True)
        second = finalize_expired_events(_deps(store), NOW, recovery_mode=True)

        assert first[0].status == "finalized"
        assert second == ()
        assert len(store.fetch_notifications_since(0)) == before
        assert [item["kind"] for item in store.fetch_notifications_since(0)] == ["event_data_hold"]
        assert store.fetch_event(event_id)["outcome"] == "failed"
    finally:
        store.close()


def test_runtime_sweeper_survives_one_maintenance_exception(tmp_path):
    store = SQLitePersistence(str(tmp_path / "sweeper-retry.db"))
    scheduler = None
    try:
        event_id = store.append_event(_event())
        deps = _deps(store)
        attempts = []

        def flaky_sweep(now):
            attempts.append(now)
            if len(attempts) == 1:
                raise RuntimeError("transient maintenance failure")
            finalize_expired_events(deps, now)

        scheduler = SummaryScheduler(
            store,
            SimpleNamespace(process=lambda _prompt, _allowed_tools: SimpleNamespace(status="success", text="ok")),
            poll_interval_seconds=0.02,
            maintenance_callback=flaky_sweep,
        )
        scheduler.start()
        scheduler._wake_event.set()

        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and store.fetch_event(event_id)["outcome"] is None:
            time.sleep(0.02)

        assert len(attempts) >= 2
        assert scheduler._thread is not None and scheduler._thread.is_alive()
        assert scheduler.last_run_status()["last_run_ok"] is True
        assert store.fetch_event(event_id)["outcome"] == "failed"
    finally:
        if scheduler is not None:
            scheduler.stop()
        store.close()
