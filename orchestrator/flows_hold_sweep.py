"""Unresolved-hold reminder, escalation, expiry, and scheduler."""

import functools
import json
import threading
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Callable, Literal

from history import (
    ExtractionExecutionError,
    InitialEventEnvelope,
    StepExecutionEnvelope,
    extract_event,
    parse_timestamp,
    record_event_data_update,
    record_event_outcome,
    record_event_state,
    record_extracted_fields,
    record_initial_event,
    record_step_executions,
    storage_timestamp,
)
from orchestrator.holds import (
    UNRESOLVED_FIELD,
    answer_approval_hold,
    answer_clarification_hold,
    create_approval_hold,
    create_clarification_hold,
    create_event_data_hold,
    determine_approval_hold,
    determine_clarification_hold,
    protocol_requires_approval,
)
from orchestrator.capabilities import CapabilityDescriptor, build_role_aware_system_context, visible_capabilities
from orchestrator.reasoning import build_insight, construct_insights_agent
from orchestrator.report_composer import ReportComposerAgent, compose_report  # re-exported: api may only import orchestrator.flows
from orchestrator.run_report import build_run_summary, resolve_audience
from orchestrator.reasoning import (
    OrchestrationParseError,
    answer_conversationally,
    answer_question_from_plan,
    assess_final_once,
    assess_risk,
    classify_intent,
    construct_core_agents as construct_main_agent,
    formulate_tasks,
    formulate_event_data_question,
    judge_success,
    make_operational_decision,
    plan_message,
    rewrite_task,
    extract_and_decide,
    extract_event_data_update,
    select_protocol,
    _unwrap_json_code_fence,
    ProtocolSelectionResult,
    RiskAssessment,
    SpecialistFailure,
    SpecialistResult,
    run_parallel_specialists,
)
from orchestrator.reasoning import answer_question, determine_closure, look_up_precedent
from orchestrator.situational_picture import (  # re-exported: api may only import orchestrator.flows
    SituationalPicture,
    build_situational_picture,
    compose_picture_from_step_outcomes,
)
from orchestrator.event_queue import PolicyAwareEventQueue, SerialEventQueue, WorkItem
from orchestrator.group_routing import (  # re-exported: api may only import orchestrator.flows
    GROUP_CHAT_TYPES,
    MAIN_AGENT_TARGET,
    GroupBinding,
    GroupNotRegisteredError,
    GroupRoutingTable,
    InvalidRoutingTargetError,
    is_scoped_target,
    resolve_scope,
    scope_deps,
)
from profiles import HUMAN_ACTIVATION_TYPE, OptimizationPolicy, UNCLASSIFIED_TYPE
from protocols import CriticalityLevel, EVENT_DATA_FIELDS, ResourceUnavailable, Step, StepOutcome
from protocols.executor import execute_steps
from agents import AgentModelError, AgentTimeoutError, InvocationPolicy, authenticated_request_identity, is_retryable_invocation_error
from messages import get_catalog
from tools import event_id_context, get_trace_id, protocol_context, stage_context
from tools.log_events import (
    direct_lane_accepted,
    direct_lane_declined,
    event_correction_recorded,
    event_outcome,
    extraction_result as log_extraction_result,
    extraction_retry,
    final_assessment_invalid,
    final_verdict,
    hold_created,
    hold_escalated,
    hold_reminder_sent,
    hold_resolved,
    hold_sweep_failed,
    insight_generated,
    operational_decision_invalid,
    precedent_closure,
    protocol_selection,
    protocol_waiting_for_event_data,
    reply_latency,
    report_received,
    request_received,
    resource_unavailable_alert,
    resource_unavailable_description_failed,
    risk_assessed,
    synthesis_failed,
)

if TYPE_CHECKING:
    from agents import Agent
    from agents.runtime import AgentRegistry
    from auth.permissions import PermissionLevel
    from config import BaseConfig, SettingsStore
    from history.query import HistoryQueryService
    from orchestrator.holds import HoldAnswerResult, HoldReason
    from orchestrator.reasoning import InsightsAgent, MainAgent
    from persistence import PersistenceInterface
    from profiles.loader import LoadedProfile
    from protocols import Protocol, ProtocolSet
    from profiles import AreaRegistry, EventTypeRegistry
    from messages import MessageCatalog

from orchestrator.flows import FlowDeps, FlowResult, _log_event_outcome, _now, _record_outcome_with_report

_HOLD_DETAIL_FIELDS: dict[str, str] = {
    "clarification": "raw_text",
    "approval": "reason",
    "event_data": "question",
}

def _hold_detail_text(kind: str, hold: dict) -> str:
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
    alert_text = deps.message_catalog.text(
        "orchestrator.hold_escalation.commander_alert",
        hold_kind=kind, age_minutes=int(age_minutes), detail=_hold_detail_text(kind, hold),
    )
    record_event_state(deps.persistence, hold["event_id"], {"hold_escalation_alert_text": alert_text})
    deps.persistence.insert_notification("hold_escalation", hold["event_id"])
    deps.persistence.mark_held_event_escalated(kind, hold["hold_id"], _now())
    hold_escalated(hold_kind=kind, event_id=hold["event_id"], hold_id=hold["hold_id"])

def _expire_unresolved_hold(deps: FlowDeps, kind: str, hold: dict) -> None:
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
        self._deps = deps
        self._poll_interval_seconds = poll_interval_seconds
        self._wake_event = threading.Event()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_run_at: str | None = None
        self._last_run_ok: bool | None = None
        self._last_run_error: str | None = None

    def last_run_status(self) -> dict:
        return {
            "last_run_at": self._last_run_at,
            "last_run_ok": self._last_run_ok,
            "last_run_error": self._last_run_error,
        }

    def _run(self) -> None:
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
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="hold-sweep-scheduler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._thread is None:
            return
        self._stop_event.set()
        self._wake_event.set()
        self._thread.join()
        self._thread = None
