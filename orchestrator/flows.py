"""The new-event flow and the package's declared entry point (work_plan.md §6.11, §6.14)."""

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
from orchestrator.attendance_schedule import (  # re-exported: api may only import orchestrator.flows
    AttendanceDispatch,
    attendance_dispatch,
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

def _deadline_failure(deps: "FlowDeps", event_id: str, next_stage: str) -> "FlowResult | None":
    """Failed FlowResult when the event deadline has already passed, else None."""

    event = deps.persistence.fetch_event(event_id)
    # Approval is an explicit asynchronous pause.  Its original queue deadline
    # must not invalidate the approved continuation while a commander is
    # reviewing the hold; otherwise a legitimate approval can never reach task
    # formulation or the approved tool.
    if event is not None and event.get("approval_answered_at"):
        return None
    deadline_at = event.get("deadline_at") if event is not None else None
    if not deadline_at:
        return None
    try:
        deadline = datetime.fromisoformat(deadline_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) < deadline:
        return None
    reason = f"event deadline exceeded before {next_stage}"
    _record_outcome_with_report(deps, event_id, "failed", failure_reason=reason)
    _log_event_outcome(event_id, "failed", failure_reason=reason, stage=next_stage)
    return FlowResult(event_id, "failed", reason)

FlowOutcome = Literal[
    "closed_on_precedent", "declined", "succeeded", "failed", "uncertain", "no_match_protocol",
    "held_for_clarification", "held_for_approval", "waiting_for_event_data", "waiting_for_drone_selection",
    "handled_resource_unavailable",
]

_VERDICT_TO_OUTCOME: dict[str, FlowOutcome] = {
    "success": "succeeded",
    "failure": "failed",
    "uncertain": "uncertain",
}

def assemble_core_agents(loaded_profile: "LoadedProfile", base_config: "BaseConfig") -> dict[str, "Agent"]:
    """The merge point for core-agent construction — see module docstring."""

    return {
        **loaded_profile.core_agents,
        **construct_main_agent(base_config),
        **construct_insights_agent(base_config),
    }

@dataclass(frozen=True)
class FlowDeps:
    """Persistence, registries, and settings one orchestration run needs."""

    persistence: "PersistenceInterface"
    settings_store: "SettingsStore"
    registry: "AgentRegistry"
    protocol_set: "ProtocolSet"
    event_type_registry: "EventTypeRegistry"
    area_registry: "AreaRegistry"
    history_query_service: "HistoryQueryService"
    optimization_policy: OptimizationPolicy = OptimizationPolicy()
    conversation_history_turns: int = 0
    conversation_history_ttl_hours: int = 24
    # Rich run-report composition (docs/responce_improve.md): the SUB-tier agent that writes
    # report_text, and the deployment's own message catalog for its language/fallback rendering.
    # `report_composer_agent=None` (the default) means "compose_report always falls back" —
    # every FlowDeps built without opting in behaves exactly as before this feature existed.
    report_composer_agent: "ReportComposerAgent | None" = None
    message_catalog: "MessageCatalog" = field(default_factory=lambda: get_catalog("en"))
    # orchestrator/group_routing.py::scope_deps: the Telegram group's bound agent, carried
    # through as a context hint/priority for protocol_selection's prompt -- never a hard
    # filter. None for an unscoped message (private chat, or a group bound to main_agent).
    preferred_agent_hint: str | None = None
    # Profile-supplied resource-unavailable describer (profiles.contracts.LoadedProfile
    # .resource_unavailable_description) -- (resource_kind, area, reason, registry) ->
    # (fact_sentence, alternatives_text). None means a profile hasn't supplied one; core then
    # falls back to its own generic phrasing rather than raising.
    resource_unavailable_description: "Callable[[str, str, str, object], tuple[str, str]] | None" = None

@dataclass(frozen=True)
class FlowResult:
    """Terminal or held outcome of one orchestration run."""

    event_id: str
    outcome: FlowOutcome
    detail: str = ""

@dataclass(frozen=True)
class EventDataReplyResult:
    """Parsed extra-data reply, or the event ids that made it ambiguous."""

    event_id: str
    updates: dict[str, object]
    message: str
    # Populated instead of the fields above when more than one pending
    # event-data hold matches the same (conversation_id, sender_identity) —
    # see the ambiguity check in `apply_event_data_reply`. Empty otherwise.
    ambiguous_event_ids: tuple[str, ...] = ()

@dataclass(frozen=True)
class DroneSelectionReplyResult:
    """Result of applying a drone-selection reply to a waiting event."""

    event_id: str
    message: str
    status: Literal["waiting_for_drone_selection", "succeeded"]

_DRONE_RECALL_TOOLS = ("return_drone_to_base", "return_all_drones_to_base")

def _now() -> str:
    """Current UTC timestamp in ISO-8601 storage form."""

    return datetime.now(timezone.utc).isoformat()

def _log_event_outcome(event_id: str, outcome: str, **detail) -> None:
    """One place every terminal outcome (§1.8's "final verdict") is logged — closed on precedent, declined, failed, succeeded, or uncertain — so a run can be reassembled by querying it..."""

    event_outcome(event_id=event_id, outcome=outcome, **detail)

def _log_reply_latency(deps: FlowDeps, event_id: str) -> None:
    """End-to-end latency, received_at -> the moment the reply that answers this event is ready
    (this write is what the notification poll loop then delivers to the chat, typically within
    one poll interval -- see run_notification_poll_loop). Called from the one place every
    terminal outcome is recorded, so this covers every report regardless of which stage it
    finished at, without threading a start time through every intermediate function."""

    event = deps.persistence.fetch_event(event_id)
    received_at = event.get("received_at") if event else None
    if not received_at:
        return
    try:
        elapsed_seconds = (datetime.now(timezone.utc) - parse_timestamp(received_at)).total_seconds()
    except (TypeError, ValueError):
        return
    reply_latency(event_id=event_id, elapsed_seconds=elapsed_seconds)

def _record_outcome_with_report(
    deps: FlowDeps,
    event_id: str,
    outcome: str,
    failure_reason: str | None = None,
    insight_text: str | None = None,
    resource_unavailable_fact: str | None = None,
    commander_alert_text: str | None = None,
    force_compose: bool = False,
) -> None:
    """The one place every terminal outcome is persisted — wraps `record_event_outcome` to also
    compose `report_text` exactly once, before the `job_finished`/`job_failed` notification this
    same write inserts, so no read path (`/Job`, `/Notifications`) ever triggers a model call.

    `report_text` stays `None` (old bot-side rendering, unchanged) unless rich reporting is on;
    when it is on, `compose_report` itself never raises and always returns usable text — the
    model's reply, or its own deterministic fallback — so a stored `report_text` is never partial.

    `resource_unavailable_fact`, when given (`_finish_with_resource_unavailable` only), forces
    composition regardless of the rich-reports toggle — the reporter must be told concretely
    what could not be dispatched every time, not only when that optional setting is on — and is
    fed to the composer (and its deterministic fallback) as an ordinary summary field, visible
    to every audience, never a raw internal identifier. `commander_alert_text` is never part of
    `report_text` or any composer input; it is persisted on its own column, read only by the
    separate, commander-only `resource_unavailable_alert` notification (api/routes.py) — the
    reporter's own reply must never carry it, and the model must never paraphrase it.

    `force_compose` (item 9's direct lane only): the reply is composed unconditionally, the same
    way `resource_unavailable_fact` already forces it -- the direct lane's own point is "a small
    model writes the reply", not something the rich-reports toggle should be able to switch off.
    """

    report_text = None
    if deps.settings_store.get_rich_reports_enabled() or resource_unavailable_fact is not None or force_compose:
        summary = build_run_summary(deps.persistence, event_id)
        summary = replace(
            summary,
            outcome=outcome,
            outcome_failure_reason=failure_reason,
            insight_text=insight_text if insight_text is not None else summary.insight_text,
            resource_unavailable_fact=resource_unavailable_fact,
        )
        if summary.selected_protocol in {"query_situational_picture", "overall_situational_picture"} and summary.insight_text:
            # The picture composer already produced the user-facing snapshot. Do not send it
            # through the generic commander report composer, which exposes internal step tasks
            # and can reintroduce Markdown/tables into the operational picture.
            report_text = summary.insight_text.strip()
        else:
            report_text = compose_report(deps.report_composer_agent, summary, resolve_audience(summary), deps.message_catalog)

    record_event_outcome(
        deps.persistence, event_id, outcome,
        failure_reason=failure_reason, insight_text=insight_text, report_text=report_text,
        commander_alert_text=commander_alert_text,
    )
    _log_reply_latency(deps, event_id)

# == Direct lane (item 9) =====================================================================
#
# A fast path for simple, low-stakes actions -- attendance/absence, movement, camera/equipment
# status, shift status, a read-only lookup -- that skips the full protocol pipeline entirely:
# one cheap model call identifies the action(s) and their parameters (or signals the message
# needs the full pipeline instead), each tool is then called directly (protocols.executor's own
# existing direct_tool execution -- no crewai turn for the action itself), and the existing
# outcome-recording path writes a small, model-composed reply exactly the same way any other
# succeeded run does. Only protocols the profile has explicitly opted in
# (Protocol.direct_lane_eligible=True) are ever considered, and only tools already in that
# protocol's own approved_tools are ever callable this way -- the direct lane never widens what
# a protocol allows, and a commander_only or approval_flag protocol is never eligible (checked
# once here, defensively, even though no profile currently marks one eligible).

def _apply_required_fields_gate(
    deps: "FlowDeps", event_id: str, main_agent: "MainAgent", classification: str
) -> "FlowResult | None":
    """Item #6's early, event-type-level required-field gate — checked
    against whatever classification the event holds *right now*, before
    risk assessment or protocol selection run against it. Protocol selection
    itself reads whatever fields extraction produced, so a missing required
    field at this point could otherwise silently commit to the wrong
    protocol. Additional to, not a replacement for, the existing
    per-protocol-step required-field check in `protocols/executor.py`
    (`_execute_protocol_plan`), which still fires unchanged for a step that
    needs a field of its own, and the formulation-time floor merged into
    every step's own `required_event_fields` (`orchestrator.reasoning.
    formulate_tasks`). Returns None when there is nothing to wait for (no
    required fields declared, or every required field is already present).

    Called from two places, not just one: `run_report_extraction` (the
    fresh path, right after extraction resolves an event type — profile-
    defined or the built-in UNCLASSIFIED_TYPE fallback), and
    `continue_after_clarification` (the resumed path, right after a
    clarification hold resolves a *different* classification than the
    fixed UNCLASSIFIED_TYPE floor already checked). The second call site
    exists because a classification chosen by clarification can carry its
    own required fields that were never checked for this event — the fresh
    path's single gate call only ever validated UNCLASSIFIED_TYPE's fixed
    `("area",)` floor, not whatever the newly-chosen real type declares."""

    required = deps.event_type_registry.required_fields_for(classification)
    if not required:
        return None

    event = deps.persistence.fetch_event(event_id)
    missing = tuple(name for name in required if not event.get(name))
    if not missing:
        return None

    conversation_messages: tuple[dict, ...] = ()
    if event.get("conversation_id") and deps.conversation_history_turns > 0:
        conversation_messages = tuple(
            deps.persistence.fetch_conversation_messages(event["conversation_id"], deps.conversation_history_turns * 2)
        )

    question = formulate_event_data_question(main_agent, event, missing, conversation_messages, deps.message_catalog)

    if event.get("conversation_id") and deps.conversation_history_turns > 0:
        deps.persistence.append_conversation_message(
            event["conversation_id"], "assistant", question,
            ttl_hours=deps.conversation_history_ttl_hours, max_turns=deps.conversation_history_turns,
            event_id=event_id,
        )

    # waiting_step_ids=() — no protocol has been selected yet. This is what
    # lets resume_after_event_data tell this hold apart from a per-step one.
    create_event_data_hold(deps.persistence, event_id, missing, question, ())
    hold_created(
        hold_kind="event_data", event_id=event_id, missing_fields=missing, classification=classification,
    )
    return FlowResult(event_id, "waiting_for_event_data", question)

def _continue_after_required_fields(
    deps: "FlowDeps", event_id: str, main_agent: "MainAgent", insights_agent: "InsightsAgent",
    raw_text: str, classification: str,
    operational_decision: "OperationalDecision | None" = None,
) -> "FlowResult":
    """What extraction would have done next, had the event type's required
    fields already been present — shared by the fresh path
    (run_report_extraction) and the resumed path (resume_after_event_data),
    so both stay in sync with exactly one copy of this branching logic."""

    if classification == UNCLASSIFIED_TYPE:
        create_clarification_hold(deps.persistence, event_id, raw_text)
        record_event_state(deps.persistence, event_id, {"clarification_held": True, "clarification_unresolved_field": UNRESOLVED_FIELD})
        hold_created(
            hold_kind="clarification", event_id=event_id, unresolved_field=UNRESOLVED_FIELD,
        )
        return FlowResult(event_id, "held_for_clarification")

    return continue_from_risk_assessment(
        deps, event_id, main_agent, insights_agent, operational_decision=operational_decision,
    )

def resolve_clarification(
    deps: FlowDeps,
    hold_id: str,
    answering_identity: str,
    answering_level: "PermissionLevel",
    chosen_classification: str,
) -> "HoldAnswerResult":
    """The synchronous prefix of answering a clarification hold: validate and record the answer, nothing more."""

    answer = answer_clarification_hold(deps.persistence, hold_id, answering_identity, answering_level, chosen_classification, deps.event_type_registry)
    # Only a resolved hold may resume orchestration side effects.
    if answer.status != "resolved":
        return answer  # unauthorized / not_found / invalid_classification — nothing to resume

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
    """Resume at risk assessment, not extraction — the other extracted fields are still valid and re-running extraction would discard the commander's decision (§6.2's own rule).

    First re-applies the required-fields gate for the classification the
    commander just chose. `resolve_clarification` only ever validated
    UNCLASSIFIED_TYPE's fixed `("area",)` floor before this hold existed —
    resolving into a real, profile-declared type can introduce required
    fields of its own that were never checked for this event. If any are
    missing, this creates the same kind of event_data hold the fresh-
    extraction path creates (`_apply_required_fields_gate`), asking
    immediately rather than letting the event proceed into risk assessment
    with a required field still unresolved."""

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
    """Answer a clarification hold and resume, synchronously start to finish — `resolve_clarification` + `continue_after_clarification` composed back into one call."""

    answer = resolve_clarification(deps, hold_id, answering_identity, answering_level, chosen_classification)
    # Only a resolved hold may resume orchestration side effects.
    if answer.status != "resolved":
        return answer  # unauthorized / not_found / invalid_classification — nothing to resume

    return continue_after_clarification(deps, answer.hold["event_id"], main_agent, insights_agent)

def resolve_approval(
    deps: FlowDeps,
    hold_id: str,
    answering_identity: str,
    answering_level: "PermissionLevel",
    decision: Literal["approved", "rejected"] | str,
) -> "HoldAnswerResult":
    """The synchronous prefix of answering an approval hold: validate and record the answer, nothing more."""

    answer = answer_approval_hold(deps.persistence, hold_id, answering_identity, answering_level, decision)
    if answer.status not in ("approved", "rejected"):
        return answer  # unauthorized / not_found / invalid_candidate — nothing to resume

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

def decline(deps: FlowDeps, event_id: str) -> FlowResult:
    """Record a rejected approval hold's outcome as declined — the synchronous, no-continuation-needed branch `resume_after_approval` and `api.operations`'s deny path (§7.11) both share."""

    _record_outcome_with_report(deps, event_id, "declined")
    _log_event_outcome(event_id, "declined")
    return FlowResult(event_id, "declined")

def continue_after_approval(deps: FlowDeps, event_id: str, main_agent: "MainAgent", insights_agent: "InsightsAgent", selected_protocol_name: str) -> FlowResult:
    """Resume execution from task formulation through protocol execution — the approved branch only."""

    protocol = deps.protocol_set.get(selected_protocol_name)
    event = deps.persistence.fetch_event(event_id)
    precedent_matches = _look_up_precedent_if_possible(deps, event_id, event)

    return _run_protocol(
        deps, event_id, main_agent, insights_agent, protocol, precedent_matches,
        event["raw_text"], event["classification"], event["area"], event["description"], event=event,
    )

def resume_after_approval(
    deps: FlowDeps,
    main_agent: "MainAgent",
    insights_agent: "InsightsAgent",
    hold_id: str,
    answering_identity: str,
    answering_level: "PermissionLevel",
    decision: Literal["approved", "rejected"] | str,
):
    """Answer an approval hold and resume, synchronously start to finish — `resolve_approval` + (on approval only) `continue_after_approval` composed back into one call."""

    answer = resolve_approval(deps, hold_id, answering_identity, answering_level, decision)
    if answer.status not in ("approved", "rejected"):
        return answer  # unauthorized / not_found — nothing to resume

    event_id = answer.hold["event_id"]

    if answer.status == "rejected":
        return decline(deps, event_id)

    return continue_after_approval(deps, event_id, main_agent, insights_agent, answer.hold["selected_protocol_name"])

def _look_up_precedent_if_possible(deps: FlowDeps, event_id: str, event: dict) -> tuple:
    # Precedent-lookback fix (same root cause as the recency fix,
    # DIAGNOSTIC_FINDINGS.MD A.2): an unresolved occurred_at used to skip
    # precedent lookup for this event entirely. Fall back to received_at as
    # the lookback window's anchor instead — occurred_at is no longer
    # required for this event to be checked against precedent.
    """Comparable prior events for this classification and area, or empty if lookup cannot run."""

    if event["classification"] is None or event["area"] is None:
        return ()
    anchor_time = event["occurred_at"] or event["received_at"]
    return look_up_precedent(deps.history_query_service, event_id, event["classification"], event["area"], anchor_time)

def continue_from_risk_assessment(
    deps: FlowDeps,
    event_id: str,
    main_agent: "MainAgent",
    insights_agent: "InsightsAgent",
    originated_from_commander: bool | None = None,
    selected_protocol: "Protocol | None" = None,
    operational_decision: "OperationalDecision | None" = None,
) -> FlowResult:
    """Run risk, protocol selection, holds, and execution from a persisted event."""

    deadline_failure = _deadline_failure(deps, event_id, "risk_assessment")
    if deadline_failure is not None:
        return deadline_failure
    event = deps.persistence.fetch_event(event_id)
    if originated_from_commander is None:
        # Authorization belongs to the original authenticated submitter.  It
        # is persisted with the event so delayed extraction and resumptions do
        # not accidentally inherit the role of a hold/event-data answerer.
        originated_from_commander = event.get("sender_permission_level") == "commander"
    raw_text, classification, area = event["raw_text"], event["classification"], event["area"]
    description, severity = event["description"], event["severity"]

    if selected_protocol is not None:
        risk_level = "high" if selected_protocol.criticality == CriticalityLevel.HIGH else "low"
        risk_assessment = RiskAssessment(
            level=risk_level,
            score=0.8 if risk_level == "high" else 0.2,
            reason="Deterministic button protocol selection",
        )
        selection = ProtocolSelectionResult(
            status="selected",
            protocol_name=selected_protocol.name,
            candidate_names=(selected_protocol.name,),
            reason="Deterministic button protocol mapping",
        )
    else:
        operational_mode = deps.optimization_policy.operational_decision_mode
        combined_decision = operational_decision if operational_mode == "merged" else None
        if combined_decision is None and operational_mode in {"shadow", "merged"}:
            try:
                combined_decision = make_operational_decision(
                    main_agent, raw_text, classification, area, description, severity,
                    deps.protocol_set.all(), deps.settings_store.get_risk_threshold(),
                    preferred_agent_hint=deps.preferred_agent_hint,
                )
            except OrchestrationParseError as exc:
                operational_decision_invalid(mode=operational_mode, reason=str(exc))
                if operational_mode == "merged":
                    _record_outcome_with_report(deps, event_id, "failed", failure_reason=str(exc))
                    return FlowResult(event_id, "failed", str(exc))

        try:
            risk_assessment = (
                combined_decision.risk
                if operational_mode == "merged" and combined_decision is not None
                else assess_risk(
                    main_agent, classification, area, description, severity,
                    deps.settings_store.get_risk_threshold(),
                )
            )
        except OrchestrationParseError as exc:
            _record_outcome_with_report(deps, event_id, "failed", failure_reason=str(exc))
            _log_event_outcome(event_id, "failed", failure_reason=str(exc), stage="risk_assessment")
            return FlowResult(event_id, "failed", str(exc))
        risk_assessed(
            event_id=event_id, risk_level=risk_assessment.level,
            risk_score=risk_assessment.score, risk_reason=risk_assessment.reason,
        )

        deadline_failure = _deadline_failure(deps, event_id, "protocol_selection")
        if deadline_failure is not None:
            return deadline_failure
        try:
            selection = (
                combined_decision.selection
                if operational_mode == "merged" and combined_decision is not None
                else select_protocol(
                    main_agent, raw_text, classification, area, description, deps.protocol_set.all(),
                    risk_assessment.level, preferred_agent_hint=deps.preferred_agent_hint,
                )
            )
        except OrchestrationParseError as exc:
            _record_outcome_with_report(deps, event_id, "failed", failure_reason=str(exc))
            _log_event_outcome(event_id, "failed", failure_reason=str(exc), stage="protocol_selection")
            return FlowResult(event_id, "failed", str(exc))

    protocol_selection(
        event_id=event_id, status=selection.status, protocol_name=selection.protocol_name,
        candidate_names=selection.candidate_names, reason=selection.reason,
    )

    precedent_matches = _look_up_precedent_if_possible(deps, event_id, event)
    state_updates = {
        "risk_level": risk_assessment.level,
        "risk_reason": risk_assessment.reason,
    }
    if selection.status == "selected":
        state_updates["selected_protocol"] = selection.protocol_name
        state_updates["protocol_reason"] = selection.reason
    if precedent_matches:
        state_updates["precedent_matched_event_ids"] = [precedent_match.event_id for precedent_match in precedent_matches]
    record_event_state(deps.persistence, event_id, state_updates)
    event.update(state_updates)

    # A precedent can answer an informational report, but it cannot stand in for
    # executing a fresh attendance write.  Repeated availability reports must
    # still reach TeamStatusAgent so the authenticated member's current response
    # is persisted and acknowledged.
    precedent_closure_blocked = (
        selection.status == "selected" and selection.protocol_name == "record_attendance_response"
    )
    closing_event_id = (
        None
        if precedent_closure_blocked
        else determine_closure(risk_assessment.level, classification, precedent_matches)
    )
    precedent_closure(
        event_id=event_id,
        matched_event_ids=[precedent_match.event_id for precedent_match in precedent_matches],
        closed=closing_event_id is not None,
        closing_event_id=closing_event_id,
    )
    if closing_event_id is not None:
        record_event_state(deps.persistence, event_id, {"precedent_closed_by_event_id": closing_event_id})
        _record_outcome_with_report(deps, event_id, "closed_on_precedent")
        _log_event_outcome(event_id, "closed_on_precedent", precedent_event_id=closing_event_id)
        return FlowResult(event_id, "closed_on_precedent", f"closed against resolved precedent '{closing_event_id}'")

    # No-match is terminal because there is no actionable hold to resolve.
    if selection.status == "no_match":
        _record_outcome_with_report(deps, event_id, "no_match_protocol", failure_reason=selection.reason)
        _log_event_outcome(event_id, "no_match_protocol", reason=selection.reason)
        return FlowResult(event_id, "no_match_protocol", selection.reason)

    protocols_by_name = {protocol.name: protocol for protocol in deps.protocol_set.all()}
    hold_reason: "HoldReason | None" = determine_approval_hold(selection, protocols_by_name, originated_from_commander)

    if hold_reason is not None:
        create_approval_hold(deps.persistence, event_id, hold_reason, selection, risk_assessment)
        record_event_state(deps.persistence, event_id, {"approval_held": True, "approval_reason": hold_reason})
        hold_created(hold_kind="approval", event_id=event_id, reason=hold_reason)
        return FlowResult(event_id, "held_for_approval", hold_reason)

    protocol = protocols_by_name[selection.protocol_name]
    return _run_protocol(
        deps, event_id, main_agent, insights_agent, protocol, precedent_matches,
        raw_text, classification, area, description, event=event,
    )

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
        # More than one report is waiting on this same sender/conversation for
        # missing details. Previously this silently applied the reply to the
        # single most-recently-created one, which could misapply a correction
        # meant for an earlier report (CRITICAL_FIXES_PLAN.MD item 7). Fail
        # loudly instead of guessing — the caller (api/routes.py) turns this
        # into a clarification response rather than a model-parsed update, so
        # no extraction call is made against an ambiguous target.
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

    # Stage 3, docs/bar_improves.md: same timestamp normalization as
    # occurred_at above, plus the one cross-field check this pair needs —
    # an end before its own start is never accepted. Both go through the
    # existing "invalid reply" mechanism (OrchestrationParseError), exactly
    # like an invalid occurred_at already does — the caller (api/routes.py)
    # abandons the stuck hold and re-routes the message rather than looping.
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
    """Forward a drone-choice hold reply to the surveillance specialist.

    The specialist decides whether the reply names one drone or asks to return
    every active drone, and calls `return_drone_to_base` or
    `return_all_drones_to_base`. Core does not inspect the reply wording.
    """

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

def resume_after_event_data(
    deps: FlowDeps,
    event_id: str,
    main_agent: "MainAgent",
    insights_agent: "InsightsAgent",
) -> FlowResult:
    """Continue a run after missing event fields have been filled in."""

    event = deps.persistence.fetch_event(event_id)

    if event.get("selected_protocol") is None:
        # No protocol was ever selected — this hold came from item #6's
        # early, event-type-level gate (`_apply_required_fields_gate`), not
        # a per-protocol-step one (which always resumes with a protocol
        # already selected). Continue exactly where extraction would have,
        # had the required fields already been present.
        return _continue_after_required_fields(
            deps, event_id, main_agent, insights_agent, event.get("raw_text", ""), event.get("classification")
        )

    protocol = deps.protocol_set.get(event.get("selected_protocol"))
    if protocol is None:
        reason = "the selected protocol is no longer available"
        _record_outcome_with_report(deps, event_id, "failed", failure_reason=reason)
        return FlowResult(event_id, "failed", reason)
    if protocol.direct_tool_binder is not None:
        # Re-bind from the event's own now-more-complete fields rather than reconstructing
        # from the persisted row (_step_from_row doesn't round-trip kind/direct_tool_name/
        # direct_tool_kwargs — and re-binding is the more correct choice anyway: the whole
        # point of resuming is that the previously-missing field just arrived).
        steps = protocol.direct_tool_binder(event)
    else:
        rows = event.get("steps", [])
        steps = tuple(_step_from_row(row) for row in rows)
    precedent_matches = _look_up_precedent_if_possible(deps, event_id, event)
    return _execute_protocol_plan(
        deps, event_id, main_agent, insights_agent, protocol, steps, precedent_matches,
        resumed=True, event=event,
    )

from orchestrator.flows_ingest import (
    begin_report,
    begin_request,
    process_message,
    process_report,
    process_request,
    run_report_extraction,
    _model_invoker_for,
)
from orchestrator.flows_hold_sweep import HoldSweepScheduler, sweep_unresolved_holds
from orchestrator.flows_protocol import (
    _execute_protocol_plan,
    _finish_protocol_assessment,
    _finish_with_resource_unavailable,
    _persist_step_outcomes,
    _persist_step_plan,
    _prior_outcomes,
    _run_protocol,
    _step_from_row,
)
from orchestrator.flows_direct_lane import (
    DirectLaneAction,
    DirectLaneResult,
    attempt_direct_lane,
    classify_direct_lane,
    run_direct_lane,
)
