"""Unresolved-hold reminder, escalation, expiry, and scheduler."""

import threading
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from history import parse_timestamp, record_event_state
from tools.log_events import hold_escalated, hold_reminder_sent, hold_sweep_failed

from orchestrator.flows import (
    FlowDeps,
    _log_event_outcome,
    _now,
    _record_outcome_with_report,
)

_HOLD_DETAIL_FIELDS: dict[str, str] = {
    "clarification": "raw_text",
    "approval": "reason",
    "event_data": "question",
}

def _hold_detail_text(kind: str, hold: dict) -> str:
    """Short description of an unresolved hold for reminder and alert text."""

    field_name = _HOLD_DETAIL_FIELDS[kind]
    return str(hold.get(field_name) or "")

def _remind_unresolved_hold(deps: FlowDeps, kind: str, hold: dict) -> None:
    """Re-sends the exact same original prompt, via the exact same notification kind
    (`{kind}_hold`) the hold's own creation already used -- the payload builder re-reads the
    still-unresolved hold fresh, so no new bot-side rendering is needed at all."""

    deps.persistence.insert_notification(f"{kind}_hold", hold["event_id"])
    deps.persistence.mark_held_event_reminded(kind, hold["hold_id"], _now())
    hold_reminder_sent(hold_kind=kind, event_id=hold["event_id"], hold_id=hold["hold_id"])

def _escalate_unresolved_hold(deps: FlowDeps, kind: str, hold: dict, age_minutes: float) -> None:
    """Notify commanders that this hold sat unanswered past the escalation window."""

    alert_text = deps.message_catalog.text(
        "orchestrator.hold_escalation.commander_alert",
        hold_kind=kind, age_minutes=int(age_minutes), detail=_hold_detail_text(kind, hold),
    )
    record_event_state(deps.persistence, hold["event_id"], {"hold_escalation_alert_text": alert_text})
    deps.persistence.insert_notification("hold_escalation", hold["event_id"])
    deps.persistence.mark_held_event_escalated(kind, hold["hold_id"], _now())
    hold_escalated(hold_kind=kind, event_id=hold["event_id"], hold_id=hold["hold_id"])

def _expire_unresolved_hold(deps: FlowDeps, kind: str, hold: dict) -> None:
    """Close an unanswered hold as failed once its expiry window has passed."""

    event_id = hold["event_id"]
    deps.persistence.resolve_held_event(kind, hold["hold_id"], {"resolved_by": "system", "decision": "expired"})
    _record_outcome_with_report(
        deps, event_id, "expired",
        failure_reason=f"no response was received to the {kind} request within the configured expiry window",
    )
    _log_event_outcome(event_id, "expired", hold_kind=kind, hold_id=hold["hold_id"])

def sweep_unresolved_holds(deps: FlowDeps) -> dict:
    """Item 8: reminder after `get_hold_reminder_minutes`, escalation to commanders after
    `get_hold_escalation_minutes`, automatic expiry after `get_hold_expiry_hours` -- all three
    live-configurable. Called periodically, in-process, by `HoldSweepScheduler` (below) on the
    API server -- never the bot process: a pure sweep over already-persisted holds, no model
    call, writing only to persistence/notification_log; the existing notification poll loop
    delivers whatever it inserts, unchanged. Thresholds are cumulative -- a hold old enough to
    expire has normally already been reminded and escalated first, so expiry is checked first
    and, once applied, skips the rest for that hold (it is no longer open)."""

    now = datetime.now(timezone.utc)
    reminder_delta = timedelta(minutes=deps.settings_store.get_hold_reminder_minutes())
    escalation_delta = timedelta(minutes=deps.settings_store.get_hold_escalation_minutes())
    expiry_delta = timedelta(hours=deps.settings_store.get_hold_expiry_hours())

    counts = {"reminded": 0, "escalated": 0, "expired": 0}
    for kind in ("clarification", "approval", "event_data"):
        for hold in deps.persistence.list_held_events(kind):
            try:
                created_at = parse_timestamp(hold["created_at"])
            except (TypeError, ValueError):
                continue
            age = now - created_at

            if age >= expiry_delta:
                _expire_unresolved_hold(deps, kind, hold)
                counts["expired"] += 1
                continue

            if age >= escalation_delta and not hold.get("escalated_at"):
                _escalate_unresolved_hold(deps, kind, hold, age.total_seconds() / 60.0)
                counts["escalated"] += 1

            if age >= reminder_delta and not hold.get("reminded_at"):
                _remind_unresolved_hold(deps, kind, hold)
                counts["reminded"] += 1

    return counts

class HoldSweepScheduler:
    """Runs `sweep_unresolved_holds` periodically on a background thread -- same shape as
    `history.summaries.SummaryScheduler`, in-process on the API server (never the bot process:
    the sweep only writes persistence/notification_log, delivery is the existing notification
    poll loop's job, unchanged)."""

    def __init__(self, deps: FlowDeps, poll_interval_seconds: float = 60.0):
        """Remember deps and the sweep interval."""

        self._deps = deps
        self._poll_interval_seconds = poll_interval_seconds
        self._wake_event = threading.Event()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_run_at: str | None = None
        self._last_run_ok: bool | None = None
        self._last_run_error: str | None = None

    def last_run_status(self) -> dict:
        """Outcome of the most recent sweep, for admin/status surfaces."""

        return {
            "last_run_at": self._last_run_at,
            "last_run_ok": self._last_run_ok,
            "last_run_error": self._last_run_error,
        }

    def _run(self) -> None:
        """Loop: sleep, sweep, record status, until cancelled."""

        while not self._stop_event.is_set():
            self._wake_event.wait(self._poll_interval_seconds)
            self._wake_event.clear()
            if self._stop_event.is_set():
                return
            try:
                sweep_unresolved_holds(self._deps)
                self._last_run_ok = True
                self._last_run_error = None
            except Exception as exc:
                self._last_run_ok = False
                self._last_run_error = str(exc)
                hold_sweep_failed()
            finally:
                self._last_run_at = datetime.now(timezone.utc).isoformat()

    def start(self) -> None:
        """Start the background sweep thread if it is not already running."""

        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="hold-sweep-scheduler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop the background sweep thread."""

        if self._thread is None:
            return
        self._stop_event.set()
        self._wake_event.set()
        self._thread.join()
        self._thread = None
