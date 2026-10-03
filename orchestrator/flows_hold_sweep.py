"""Hold answers, event-data replies, and the unresolved-hold sweep."""

import threading
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Literal

from history import parse_timestamp, record_event_data_update, record_event_outcome, record_event_state, storage_timestamp
from orchestrator.flows_execution import (
    DroneSelectionReplyResult,
    EventDataReplyResult,
    FlowDeps,
    FlowResult,
    _log_event_outcome,
    _now,
    _record_outcome_with_report,
)
from orchestrator.flows_protocol import (
    _apply_required_fields_gate,
    continue_after_approval,
    continue_from_risk_assessment,
    decline,
)
from orchestrator.holds import answer_approval_hold, answer_clarification_hold
from orchestrator.reasoning import OrchestrationParseError, extract_event_data_update
from tools import stage_context
from tools.log_events import hold_escalated, hold_reminder_sent, hold_resolved, hold_sweep_failed

if TYPE_CHECKING:
    from auth.permissions import PermissionLevel
    from orchestrator.holds import HoldAnswerResult
    from orchestrator.reasoning import InsightsAgent, MainAgent

_DRONE_RECALL_TOOLS = ("return_drone_to_base", "return_all_drones_to_base")

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


# --- hold answers and conversational resumes ---


def resolve_clarification(
    deps: FlowDeps,
    hold_id: str,
    answering_identity: str,
    answering_level: "PermissionLevel",
    chosen_classification: str,
) -> "HoldAnswerResult":
    """Validate and record a clarification answer without resuming orchestration."""

    answer = answer_clarification_hold(deps.persistence, hold_id, answering_identity, answering_level, chosen_classification, deps.event_type_registry)
    # Only a resolved hold may resume orchestration side effects.
    if answer.status != "resolved":
        return answer

    event_id = answer.hold["event_id"]
    record_event_state(
        deps.persistence, event_id,
        {"classification": chosen_classification, "clarification_resolved_by": answering_identity, "clarification_chosen_classification": chosen_classification},
    )

    hold_resolved(
        hold_kind="clarification", event_id=event_id,
        resolved_by=answering_identity, chosen_classification=chosen_classification,
    )

    return answer


def continue_after_clarification(deps: FlowDeps, event_id: str, main_agent: "MainAgent", insights_agent: "InsightsAgent") -> FlowResult:
    """Resume at risk assessment after a commander chooses a classification."""

    event = deps.persistence.fetch_event(event_id)
    gate_result = _apply_required_fields_gate(deps, event_id, main_agent, event.get("classification"))
    if gate_result is not None:
        return gate_result

    return continue_from_risk_assessment(deps, event_id, main_agent, insights_agent)


def resume_after_clarification(
    deps: FlowDeps,
    main_agent: "MainAgent",
    insights_agent: "InsightsAgent",
    hold_id: str,
    answering_identity: str,
    answering_level: "PermissionLevel",
    chosen_classification: str,
):
    """Answer a clarification hold and resume from start to finish."""

    answer = resolve_clarification(deps, hold_id, answering_identity, answering_level, chosen_classification)
    # Only a resolved hold may resume orchestration side effects.
    if answer.status != "resolved":
        return answer

    return continue_after_clarification(deps, answer.hold["event_id"], main_agent, insights_agent)


def resolve_approval(
    deps: FlowDeps,
    hold_id: str,
    answering_identity: str,
    answering_level: "PermissionLevel",
    decision: Literal["approved", "rejected"] | str,
) -> "HoldAnswerResult":
    """Validate and record an approval answer without resuming orchestration."""

    answer = answer_approval_hold(deps.persistence, hold_id, answering_identity, answering_level, decision)
    if answer.status not in ("approved", "rejected"):
        return answer

    event_id = answer.hold["event_id"]
    record_event_state(
        deps.persistence, event_id,
        {
            "approval_answered_by": answering_identity,
            "approval_answered_at": _now(),
            "selected_protocol": answer.hold["selected_protocol_name"],
        },
    )

    hold_resolved(
        hold_kind="approval", event_id=event_id, resolved_by=answering_identity,
        decision=decision, status=answer.status, selected_protocol=answer.hold["selected_protocol_name"],
    )

    return answer


def resume_after_approval(
    deps: FlowDeps,
    main_agent: "MainAgent",
    insights_agent: "InsightsAgent",
    hold_id: str,
    answering_identity: str,
    answering_level: "PermissionLevel",
    decision: Literal["approved", "rejected"] | str,
):
    """Answer an approval hold and resume, or decline when the hold is rejected."""

    answer = resolve_approval(deps, hold_id, answering_identity, answering_level, decision)
    if answer.status not in ("approved", "rejected"):
        return answer

    event_id = answer.hold["event_id"]

    if answer.status == "rejected":
        return decline(deps, event_id)

    return continue_after_approval(deps, event_id, main_agent, insights_agent, answer.hold["selected_protocol_name"])


def apply_event_data_reply(
    deps: FlowDeps,
    main_agent: "MainAgent",
    reply_text: str,
    sender_identity: str,
    conversation_id: str | None,
    conversation_messages: tuple[dict, ...] = (),
    target_event_id: str | None = None,
) -> EventDataReplyResult | None:
    """Apply a conversational answer to the newest matching reporter-facing data request."""

    if not conversation_id:
        return None
    candidates: list[tuple[dict, dict]] = []
    for hold in deps.persistence.list_held_events("event_data"):
        if target_event_id is not None and hold["event_id"] != target_event_id:
            continue
        event = deps.persistence.fetch_event(hold["event_id"])
        if (
            event is not None
            and event.get("conversation_id") == conversation_id
            and event.get("sender_identity") == sender_identity
        ):
            candidates.append((hold, event))
    if not candidates:
        return None
    if len(candidates) > 1:
        # Guessing would misapply a correction; api/routes_messages.py asks which event instead.
        return EventDataReplyResult(
            event_id="",
            updates={},
            message="",
            ambiguous_event_ids=tuple(event["event_id"] for _hold, event in candidates),
        )

    hold, event = candidates[-1]
    requested_fields = tuple(hold.get("missing_fields") or ())
    parsed = extract_event_data_update(
        main_agent,
        event,
        reply_text,
        requested_fields,
        deps.event_type_registry.types,
        deps.area_registry.areas,
        conversation_messages,
    )
    if not parsed.addresses_request:
        return None
    if not parsed.updates:
        return EventDataReplyResult(event["event_id"], {}, hold["question"])

    updates = dict(parsed.updates)
    if "occurred_at" in updates:
        try:
            updates["occurred_at"] = storage_timestamp(parse_timestamp(str(updates["occurred_at"])))
        except (TypeError, ValueError) as exc:
            raise OrchestrationParseError("event data update returned an invalid occurred_at timestamp") from exc
        updates["occurred_at_is_fallback"] = False

    # Same timestamp rules as occurred_at; an end before its start is never accepted.
    # OrchestrationParseError lets api/routes_messages.py abandon the stuck hold and re-route.
    for availability_field in ("availability_start", "availability_end"):
        if availability_field in updates:
            try:
                updates[availability_field] = storage_timestamp(parse_timestamp(str(updates[availability_field])))
            except (TypeError, ValueError) as exc:
                raise OrchestrationParseError(
                    f"event data update returned an invalid {availability_field} timestamp"
                ) from exc
    if "availability_start" in updates or "availability_end" in updates:
        effective_start = updates.get("availability_start", event.get("availability_start"))
        effective_end = updates.get("availability_end", event.get("availability_end"))
        if effective_start and effective_end and parse_timestamp(effective_end) < parse_timestamp(effective_start):
            raise OrchestrationParseError("availability_end must not be before availability_start")

    record_event_data_update(deps.persistence, event["event_id"], updates)
    deps.persistence.resolve_held_event(
        "event_data", hold["hold_id"], {"resolved_by": sender_identity, "updated_fields": sorted(updates)}
    )
    return EventDataReplyResult(
        event["event_id"], updates,
        parsed.reply_text,
    )


def apply_drone_selection_reply(
    deps: FlowDeps,
    reply_text: str,
    hold: dict,
    *,
    resolved_by: str,
) -> DroneSelectionReplyResult:
    """Forward a drone-choice hold reply to the surveillance specialist."""

    event_id = hold["event_id"]
    try:
        agent = deps.registry.get("surveillance_agent")
    except KeyError as exc:
        raise OrchestrationParseError("surveillance_agent is not available") from exc

    exposed = {tool.name for tool in agent.exposed_tools()}
    allowed = [name for name in _DRONE_RECALL_TOOLS if name in exposed]
    if not allowed:
        raise OrchestrationParseError("surveillance_agent has no recall tools")

    task = deps.message_catalog.text(
        "orchestrator.drone_selection.task",
        choices=hold.get("question") or "",
        reply=reply_text,
    )
    with stage_context("drone_selection_reply"):
        result = agent.process(task, allowed)
    if result.status != "success" or getattr(result, "selection_required", False):
        return DroneSelectionReplyResult(event_id, result.text, "waiting_for_drone_selection")

    deps.persistence.resolve_held_event(
        "event_data",
        hold["hold_id"],
        {"resolved_by": resolved_by},
    )
    record_event_outcome(deps.persistence, event_id, "succeeded")
    return DroneSelectionReplyResult(event_id, result.text, "succeeded")
