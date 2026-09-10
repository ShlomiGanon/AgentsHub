"""The new-event flow and the package's declared entry point (work_plan.md §6.11, §6.14)."""

import functools
import json
import logging
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Literal

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
    record_step_execution,
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
)
from orchestrator.capabilities import CapabilityDescriptor, build_role_aware_system_context, visible_capabilities
from orchestrator.reasoning import build_insight, construct_insights_agent
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
    extract_event_data_update,
    select_protocol,
    synthesize_operational_picture,
    ProtocolSelectionResult,
    RiskAssessment,
    run_parallel_specialists,
)
from orchestrator.reasoning import answer_question, determine_closure, look_up_precedent
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
from protocols import CriticalityLevel, Step, StepOutcome
from protocols.executor import execute_steps
from agents import authenticated_request_identity
from tools import get_trace_id

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

logger = logging.getLogger(__name__)


def _deadline_failure(deps: "FlowDeps", event_id: str, next_stage: str) -> "FlowResult | None":
    event = deps.persistence.fetch_event(event_id)
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
    record_event_outcome(deps.persistence, event_id, "failed", failure_reason=reason)
    _log_event_outcome(event_id, "failed", failure_reason=reason, stage=next_stage)
    return FlowResult(event_id, "failed", reason)

FlowOutcome = Literal[
    "closed_on_precedent", "declined", "succeeded", "failed", "uncertain", "no_match_protocol",
    "held_for_clarification", "held_for_approval", "waiting_for_event_data", "waiting_for_drone_selection",
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


@dataclass(frozen=True)
class FlowResult:
    event_id: str
    outcome: FlowOutcome
    detail: str = ""


@dataclass(frozen=True)
class EventDataReplyResult:
    event_id: str
    updates: dict[str, object]
    message: str
    # Populated instead of the fields above when more than one pending
    # event-data hold matches the same (conversation_id, sender_identity) —
    # see the ambiguity check in `apply_event_data_reply`. Empty otherwise.
    ambiguous_event_ids: tuple[str, ...] = ()


def _model_invoker_for(main_agent: "MainAgent"):
    def _invoke(prompt: str) -> str:
        agent_result = main_agent.process(prompt, [])
        if agent_result.status != "success":
            raise ExtractionExecutionError(f"main agent could not produce a usable extraction response: {agent_result.text}")
        return agent_result.text

    return _invoke


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _log_event_outcome(event_id: str, outcome: str, **detail) -> None:
    """One place every terminal outcome (§1.8's "final verdict") is logged — closed on precedent, declined, failed, succeeded, or uncertain — so a run can be reassembled by querying it..."""

    logger.info(
        "event outcome",
        extra={"event": "event_outcome", "event_id": event_id, "outcome": outcome, "trace_id": get_trace_id(), **detail},
    )


def begin_report(
    deps: FlowDeps,
    raw_text: str,
    source: Literal["sensor", "telegram"],
    received_at: str,
    sender_identity: str,
    source_message_id: str | None = None,
    conversation_id: str | None = None,
    deadline_at: str | None = None,
) -> str:
    """The synchronous prefix of a report: write the raw text and return the event ID, before any model call runs (§7.2's own requirement — "before any processing begins")."""

    event_id = record_initial_event(
        deps.persistence,
        InitialEventEnvelope(
            raw_text=raw_text, source=source, received_at=received_at, sender_identity=sender_identity,
            source_message_id=source_message_id,
            trace_id=get_trace_id() or None, conversation_id=conversation_id, deadline_at=deadline_at,
        ),
    )

    logger.info(
        "report received",
        extra={
            "event": "report_received", "event_id": event_id, "source": source,
            "sender_identity": sender_identity, "raw_text": raw_text, "trace_id": get_trace_id(),
        },
    )

    return event_id


def run_report_extraction(deps: FlowDeps, event_id: str, main_agent: "MainAgent", insights_agent: "InsightsAgent") -> FlowResult:
    """The rest of a report: extraction through outcome."""

    deadline_failure = _deadline_failure(deps, event_id, "extraction")
    if deadline_failure is not None:
        return deadline_failure

    event = deps.persistence.fetch_event(event_id)
    raw_text, source, received_at = event["raw_text"], event["source"], event["received_at"]

    try:
        extraction_result = extract_event(
            raw_text, source, received_at, deps.event_type_registry, deps.area_registry,
            model_invoker=_model_invoker_for(main_agent),
        )
    except ExtractionExecutionError as exc:
        record_event_outcome(deps.persistence, event_id, "failed", failure_reason=str(exc))
        _log_event_outcome(event_id, "failed", failure_reason=str(exc), stage="extraction")
        return FlowResult(event_id, "failed", str(exc))

    logger.info(
        "extraction result",
        extra={
            "event": "extraction_result",
            "event_id": event_id,
            "classification": extraction_result.classification,
            "area": extraction_result.area,
            "missing_fields": list(extraction_result.missing_fields),
            "occurred_at_is_fallback": extraction_result.occurred_at_is_fallback,
            "trace_id": get_trace_id(),
        },
    )

    record_extracted_fields(deps.persistence, event_id, extraction_result)

    # A report that doesn't match any event type the active profile declares
    # resolves to the built-in UNCLASSIFIED_TYPE fallback rather than staying
    # `None` — a real, storable classification with its own (core-declared)
    # required fields, distinct from `HUMAN_ACTIVATION_TYPE` (a source label,
    # not an event type) (REQUIRED_FIELDS_AND_CLOSED_DECISIONS.md Part 1 /
    # item #6). `determine_clarification_hold` still keys off the *original*
    # extraction result, unchanged — this only affects what gets persisted.
    if determine_clarification_hold(extraction_result):
        record_event_state(deps.persistence, event_id, {"classification": UNCLASSIFIED_TYPE})
    resolved_classification = extraction_result.classification or UNCLASSIFIED_TYPE

    gate_result = _apply_required_fields_gate(deps, event_id, main_agent, resolved_classification)
    if gate_result is not None:
        return gate_result

    return _continue_after_required_fields(deps, event_id, main_agent, insights_agent, raw_text, resolved_classification)


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

    question = formulate_event_data_question(main_agent, event, missing, conversation_messages)

    if event.get("conversation_id") and deps.conversation_history_turns > 0:
        deps.persistence.append_conversation_message(
            event["conversation_id"], "assistant", question,
            ttl_hours=deps.conversation_history_ttl_hours, max_turns=deps.conversation_history_turns,
            event_id=event_id,
        )

    # waiting_step_ids=() — no protocol has been selected yet. This is what
    # lets resume_after_event_data tell this hold apart from a per-step one.
    create_event_data_hold(deps.persistence, event_id, missing, question, ())
    logger.info(
        "hold created",
        extra={
            "event": "hold_created", "hold_kind": "event_data", "event_id": event_id,
            "missing_fields": list(missing), "classification": classification, "trace_id": get_trace_id(),
        },
    )
    return FlowResult(event_id, "waiting_for_event_data", question)


def _continue_after_required_fields(
    deps: "FlowDeps", event_id: str, main_agent: "MainAgent", insights_agent: "InsightsAgent",
    raw_text: str, classification: str,
) -> "FlowResult":
    """What extraction would have done next, had the event type's required
    fields already been present — shared by the fresh path
    (run_report_extraction) and the resumed path (resume_after_event_data),
    so both stay in sync with exactly one copy of this branching logic."""

    if classification == UNCLASSIFIED_TYPE:
        create_clarification_hold(deps.persistence, event_id, raw_text)
        record_event_state(deps.persistence, event_id, {"clarification_held": True, "clarification_unresolved_field": UNRESOLVED_FIELD})
        logger.info(
            "hold created",
            extra={"event": "hold_created", "hold_kind": "clarification", "event_id": event_id, "unresolved_field": UNRESOLVED_FIELD, "trace_id": get_trace_id()},
        )
        return FlowResult(event_id, "held_for_clarification")

    return continue_from_risk_assessment(deps, event_id, main_agent, insights_agent, originated_from_commander=False)


def process_report(
    deps: FlowDeps,
    main_agent: "MainAgent",
    insights_agent: "InsightsAgent",
    raw_text: str,
    source: Literal["sensor", "telegram"],
    received_at: str,
    sender_identity: str,
) -> FlowResult:
    """A report of something that happened, run synchronously start to finish — `begin_report` + `run_report_extraction` composed back into one call."""

    event_id = begin_report(deps, raw_text, source, received_at, sender_identity)
    return run_report_extraction(deps, event_id, main_agent, insights_agent)


def begin_request(
    deps: FlowDeps,
    raw_text: str,
    received_at: str,
    sender_identity: str,
    source_message_id: str | None = None,
    conversation_id: str | None = None,
    deadline_at: str | None = None,
) -> str:
    """The synchronous prefix of a request: write the raw text, already classified `human_activation` (§6.13 — there is nothing to extract), and return the event ID."""

    event_id = record_initial_event(
        deps.persistence,
        InitialEventEnvelope(
            raw_text=raw_text, source="telegram", received_at=received_at, sender_identity=sender_identity,
            source_message_id=source_message_id, occurred_at=received_at, occurred_at_is_fallback=False,
            trace_id=get_trace_id() or None, conversation_id=conversation_id, deadline_at=deadline_at,
        ),
    )
    record_event_state(deps.persistence, event_id, {"classification": HUMAN_ACTIVATION_TYPE})

    logger.info(
        "request received",
        extra={
            "event": "request_received", "event_id": event_id,
            "sender_identity": sender_identity, "raw_text": raw_text, "trace_id": get_trace_id(),
        },
    )

    return event_id


def process_request(
    deps: FlowDeps,
    main_agent: "MainAgent",
    insights_agent: "InsightsAgent",
    raw_text: str,
    received_at: str,
    sender_identity: str,
    originated_from_commander: bool,
) -> FlowResult:
    """A person's request for an action, run synchronously start to finish — `begin_request` + `continue_from_risk_assessment` composed back into one call."""

    event_id = begin_request(deps, raw_text, received_at, sender_identity)
    return continue_from_risk_assessment(deps, event_id, main_agent, insights_agent, originated_from_commander)


def process_message(
    deps: FlowDeps,
    main_agent: "MainAgent",
    insights_agent: "InsightsAgent",
    message_text: str,
    sender_identity: str,
    received_at: str,
    is_commander: bool,
) -> tuple[Literal["question", "report", "request", "conversational", "clarification"], object]:
    """Route a person's message by intent (§6.13)."""

    intent = classify_intent(main_agent, deps.protocol_set.all(), message_text)

    if intent.intent == "needs_clarification":
        return "clarification", intent.clarification_question or "Could you clarify what you want me to do?"

    if intent.intent == "conversational":
        return "conversational", answer_conversationally(main_agent, message_text)

    if intent.intent == "question":
        return "question", answer_question(main_agent, message_text, deps.registry, deps.history_query_service)

    if intent.intent == "report":
        return "report", process_report(deps, main_agent, insights_agent, message_text, "telegram", received_at, sender_identity)

    if intent.intent == "request":
        return "request", process_request(deps, main_agent, insights_agent, message_text, received_at, sender_identity, is_commander)

    raise OrchestrationParseError(f"unsupported message intent: {intent.intent!r}")


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

    logger.info(
        "clarification hold resolved",
        extra={
            "event": "hold_resolved", "hold_kind": "clarification", "event_id": event_id,
            "resolved_by": answering_identity, "chosen_classification": chosen_classification, "trace_id": get_trace_id(),
        },
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

    return continue_from_risk_assessment(deps, event_id, main_agent, insights_agent, originated_from_commander=False)


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

    logger.info(
        "approval hold resolved",
        extra={
            "event": "hold_resolved", "hold_kind": "approval", "event_id": event_id, "resolved_by": answering_identity,
            "decision": decision, "status": answer.status, "selected_protocol": answer.hold["selected_protocol_name"],
            "trace_id": get_trace_id(),
        },
    )

    return answer


def decline(deps: FlowDeps, event_id: str) -> FlowResult:
    """Record a rejected approval hold's outcome as declined — the synchronous, no-continuation-needed branch `resume_after_approval` and `api.operations`'s deny path (§7.11) both share."""

    record_event_outcome(deps.persistence, event_id, "declined")
    _log_event_outcome(event_id, "declined")
    return FlowResult(event_id, "declined")


def continue_after_approval(deps: FlowDeps, event_id: str, main_agent: "MainAgent", insights_agent: "InsightsAgent", selected_protocol_name: str) -> FlowResult:
    """Resume execution from task formulation through protocol execution — the approved branch only."""

    protocol = deps.protocol_set.get(selected_protocol_name)
    event = deps.persistence.fetch_event(event_id)
    precedent_matches = _look_up_precedent_if_possible(deps, event_id, event)

    return _run_protocol(deps, event_id, main_agent, insights_agent, protocol, precedent_matches, event["raw_text"], event["classification"], event["area"], event["description"])


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
    if event["classification"] is None or event["area"] is None:
        return ()
    anchor_time = event["occurred_at"] or event["received_at"]
    return look_up_precedent(deps.history_query_service, event_id, event["classification"], event["area"], anchor_time)


def continue_from_risk_assessment(
    deps: FlowDeps,
    event_id: str,
    main_agent: "MainAgent",
    insights_agent: "InsightsAgent",
    originated_from_commander: bool,
    selected_protocol: "Protocol | None" = None,
) -> FlowResult:
    deadline_failure = _deadline_failure(deps, event_id, "risk_assessment")
    if deadline_failure is not None:
        return deadline_failure
    event = deps.persistence.fetch_event(event_id)
    raw_text, classification, area = event["raw_text"], event["classification"], event["area"]
    description, severity = event["description"], event["severity"]

    if selected_protocol is not None:
        risk_level = "high" if selected_protocol.criticality == CriticalityLevel.HIGH else "low"
        risk_assessment = RiskAssessment(
            level=risk_level,
            score=0.8 if risk_level == "high" else 0.2,
            reason="Deterministic button protocol selection",
        )
        record_event_state(deps.persistence, event_id, {"risk_level": risk_assessment.level, "risk_reason": risk_assessment.reason})
        selection = ProtocolSelectionResult(
            status="selected",
            protocol_name=selected_protocol.name,
            candidate_names=(selected_protocol.name,),
            reason="Deterministic button protocol mapping",
        )
        record_event_state(deps.persistence, event_id, {"selected_protocol": selection.protocol_name, "protocol_reason": selection.reason})
    else:
        operational_mode = deps.optimization_policy.operational_decision_mode
        combined_decision = None
        if operational_mode in {"shadow", "merged"}:
            try:
                combined_decision = make_operational_decision(
                    main_agent, raw_text, classification, area, description, severity,
                    deps.protocol_set.all(), deps.settings_store.get_risk_threshold(),
                )
            except OrchestrationParseError as exc:
                logger.warning(
                    "combined operational decision failed validation",
                    extra={"event": "operational_decision_invalid", "mode": operational_mode, "reason": str(exc), "trace_id": get_trace_id()},
                )
                if operational_mode == "merged":
                    record_event_outcome(deps.persistence, event_id, "failed", failure_reason=str(exc))
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
            record_event_outcome(deps.persistence, event_id, "failed", failure_reason=str(exc))
            _log_event_outcome(event_id, "failed", failure_reason=str(exc), stage="risk_assessment")
            return FlowResult(event_id, "failed", str(exc))
        record_event_state(deps.persistence, event_id, {"risk_level": risk_assessment.level, "risk_reason": risk_assessment.reason})
        logger.info(
            "risk assessed",
            extra={
                "event": "risk_assessed", "event_id": event_id, "risk_level": risk_assessment.level,
                "risk_score": risk_assessment.score, "risk_reason": risk_assessment.reason, "trace_id": get_trace_id(),
            },
        )

        deadline_failure = _deadline_failure(deps, event_id, "protocol_selection")
        if deadline_failure is not None:
            return deadline_failure
        try:
            selection = (
                combined_decision.selection
                if operational_mode == "merged" and combined_decision is not None
                else select_protocol(main_agent, raw_text, classification, area, description, deps.protocol_set.all(), risk_assessment.level)
            )
        except OrchestrationParseError as exc:
            record_event_outcome(deps.persistence, event_id, "failed", failure_reason=str(exc))
            _log_event_outcome(event_id, "failed", failure_reason=str(exc), stage="protocol_selection")
            return FlowResult(event_id, "failed", str(exc))

        if selection.status == "selected":
            record_event_state(deps.persistence, event_id, {"selected_protocol": selection.protocol_name, "protocol_reason": selection.reason})

    logger.info(
        "protocol selection",
        extra={
            "event": "protocol_selection", "event_id": event_id, "status": selection.status,
            "protocol_name": selection.protocol_name, "candidate_names": list(selection.candidate_names),
            "reason": selection.reason, "trace_id": get_trace_id(),
        },
    )

    precedent_matches = _look_up_precedent_if_possible(deps, event_id, event)
    if precedent_matches:
        record_event_state(
            deps.persistence,
            event_id,
            {"precedent_matched_event_ids": [precedent_match.event_id for precedent_match in precedent_matches]},
        )

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
    logger.info(
        "precedent closure decision",
        extra={
            "event": "precedent_closure", "event_id": event_id,
            "matched_event_ids": [precedent_match.event_id for precedent_match in precedent_matches],
            "closed": closing_event_id is not None, "closing_event_id": closing_event_id, "trace_id": get_trace_id(),
        },
    )
    if closing_event_id is not None:
        record_event_state(deps.persistence, event_id, {"precedent_closed_by_event_id": closing_event_id})
        record_event_outcome(deps.persistence, event_id, "closed_on_precedent")
        _log_event_outcome(event_id, "closed_on_precedent", precedent_event_id=closing_event_id)
        return FlowResult(event_id, "closed_on_precedent", f"closed against resolved precedent '{closing_event_id}'")

    # No-match is terminal because there is no actionable hold to resolve.
    if selection.status == "no_match":
        record_event_outcome(deps.persistence, event_id, "no_match_protocol", failure_reason=selection.reason)
        _log_event_outcome(event_id, "no_match_protocol", reason=selection.reason)
        return FlowResult(event_id, "no_match_protocol", selection.reason)

    protocols_by_name = {protocol.name: protocol for protocol in deps.protocol_set.all()}
    selected_proto = protocols_by_name.get(selection.protocol_name)
    normalized_report = str(raw_text).strip().casefold()
    observational_report = any(
        marker in normalized_report
        for marker in ("\u05d0\u05e0\u05d9 \u05e8\u05d5\u05d0\u05d4", "\u05e8\u05d0\u05d9\u05ea\u05d9", "\u05d6\u05d9\u05d4\u05d9\u05ea\u05d9", "\u05d0\u05e0\u05d9 \u05de\u05d3\u05d5\u05d5\u05d7", "\u05d9\u05e9 \u05d0\u05e9", "\u05d9\u05e9 \u05e2\u05e9\u05df")
    )
    if (
        selected_proto is not None
        and not originated_from_commander
        and getattr(selected_proto, "commander_only", False)
        and not observational_report
    ):
        record_event_outcome(
            deps.persistence,
            event_id,
            "declined",
            failure_reason="\u05d4\u05d1\u05e7\u05e9\u05d4 \u05e0\u05d3\u05d7\u05ea\u05d4: \u05d4\u05e4\u05e2\u05dc\u05ea \u05d4\u05e4\u05e8\u05d5\u05d8\u05d5\u05e7\u05d5\u05dc \u05d3\u05d5\u05e8\u05e9\u05ea \u05d4\u05e8\u05e9\u05d0\u05ea \u05de\u05e4\u05e7\u05d3.",
        )
        _log_event_outcome(event_id, "declined", reason="commander permission required")
        return FlowResult(
            event_id,
            "unauthorized_for_viewer",
            "\u05d4\u05d1\u05e7\u05e9\u05d4 \u05e0\u05d3\u05d7\u05ea\u05d4: \u05d4\u05e4\u05e2\u05dc\u05ea \u05d4\u05e4\u05e8\u05d5\u05d8\u05d5\u05e7\u05d5\u05dc \u05d3\u05d5\u05e8\u05e9\u05ea \u05d4\u05e8\u05e9\u05d0\u05ea \u05de\u05e4\u05e7\u05d3.",
        )

    hold_reason: "HoldReason | None" = determine_approval_hold(selection, protocols_by_name, originated_from_commander)

    if hold_reason is not None:
        create_approval_hold(deps.persistence, event_id, hold_reason, selection, risk_assessment)
        record_event_state(deps.persistence, event_id, {"approval_held": True, "approval_reason": hold_reason})
        logger.info(
            "hold created",
            extra={"event": "hold_created", "hold_kind": "approval", "event_id": event_id, "reason": hold_reason, "trace_id": get_trace_id()},
        )
        return FlowResult(event_id, "held_for_approval", hold_reason)

    protocol = protocols_by_name[selection.protocol_name]
    return _run_protocol(deps, event_id, main_agent, insights_agent, protocol, precedent_matches, raw_text, classification, area, description)


def _run_protocol(
    deps: FlowDeps,
    event_id: str,
    main_agent: "MainAgent",
    insights_agent: "InsightsAgent",
    protocol: "Protocol",
    precedent_matches: tuple,
    raw_text: str,
    classification: str | None,
    area: str | None,
    description: str | None,
) -> FlowResult:
    deadline_failure = _deadline_failure(deps, event_id, "formulation")
    if deadline_failure is not None:
        return deadline_failure
    formulation = formulate_tasks(
        main_agent, protocol, deps.registry, raw_text, classification, area, description,
        precedent_context=precedent_matches, event_data=deps.persistence.fetch_event(event_id),
        # The event type's statically-declared required fields (item #6's
        # EVENT_TYPE_REQUIRED_FIELDS), unioned into every formulated step's own
        # required_event_fields regardless of what the model declares — see
        # formulate_tasks' docstring.
        required_fields_floor=deps.event_type_registry.required_fields_for(classification),
    )
    if not formulation.success:
        record_event_outcome(deps.persistence, event_id, "failed", failure_reason=formulation.failure_reason)
        _log_event_outcome(event_id, "failed", failure_reason=formulation.failure_reason, stage="formulation")
        return FlowResult(event_id, "failed", formulation.failure_reason or "")
    return _execute_protocol_plan(
        deps, event_id, main_agent, insights_agent, protocol, formulation.steps, precedent_matches,
    )


def _persist_step_plan(deps: FlowDeps, event_id: str, steps: tuple[Step, ...]) -> None:
    for index, step in enumerate(steps):
        record_step_execution(
            deps.persistence,
            event_id,
            StepExecutionEnvelope(
                step_index=index,
                agent_name=step.agent_name,
                task_text=step.task_text,
                allowed_tools=list(step.allowed_tools),
                result_text=None,
                attempt_count=0,
                step_id=step.step_id,
                depends_on=step.depends_on,
                required_event_fields=step.required_event_fields,
                status="pending",
            ),
        )


def _step_from_row(row: dict) -> Step:
    return Step(
        agent_name=row["agent_name"],
        task_text=row["task_text"],
        allowed_tools=tuple(row.get("allowed_tools") or ()),
        step_id=row.get("step_id") or str(row["step_index"]),
        depends_on=tuple(row.get("depends_on") or ()),
        required_event_fields=tuple(row.get("required_event_fields") or ()),
    )


def _prior_outcomes(rows: list[dict], steps: tuple[Step, ...]) -> tuple[StepOutcome, ...]:
    by_index = {row["step_index"]: row for row in rows}
    outcomes: list[StepOutcome] = []
    for index, step in enumerate(steps):
        row = by_index.get(index, {})
        outcomes.append(
            StepOutcome(
                step=step,
                result_text=row.get("result_text"),
                attempt_count=row.get("attempt_count", 0),
                succeeded=row.get("status") == "succeeded",
                failure_reason=row.get("failure_reason"),
                status=row.get("status", "pending"),
                missing_event_fields=tuple(row.get("missing_event_fields") or ()),
            )
        )
    return tuple(outcomes)


def _persist_step_outcomes(
    deps: FlowDeps,
    event_id: str,
    steps: tuple[Step, ...],
    outcomes: tuple[StepOutcome, ...],
) -> None:
    """Match each outcome back to the step it belongs to, by `step_id` where
    one exists. `outcome.step` is not necessarily `is`-identical to its
    entry in `steps` — this function receives the *original* `steps`, but
    `_execute_protocol_plan` runs a derived copy where any step with a
    non-empty `required_event_fields` has its `task_text` rewritten first
    (the "Current validated event data JSON" injection, above) — so an
    outcome's step can differ from the original by value once that
    injection applies.

    A previous version of this function fell back to matching by full
    value-equality (`step == outcome.step`) whenever a step's `step_id` was
    empty, on the unstated assumption that a step_id-less step's fields
    never get mutated after formulation — true only by accident, and it
    broke the moment a step_id-less step also had a non-empty
    `required_event_fields`.

    Every step's `step_id` is empty only on one production path today: the
    legacy AGENT:/TASK: fallback in `orchestrator.reasoning.formulate_tasks`
    (the JSON formulation path always assigns each step a unique, non-empty
    id, and reloading a persisted plan via `_step_from_row` always
    substitutes `str(step_index)` for an absent one) — and a legacy-parsed
    plan, having no step_id or depends_on on any step, can only ever run
    through `protocols.executor.execute_steps`' plain sequential branch
    (never the dependency-graph one), which is guaranteed to produce
    `outcomes` in exactly the same order and position as `steps`, truncated
    at most (a blocked or failed run stops partway through) but never
    reordered or skipped over. So when `step_id` is empty, this outcome's
    own position *is* its step's position — no value comparison needed, and
    nothing about it changes if the step was mutated in the meantime."""

    index_by_step_id = {step.step_id: index for index, step in enumerate(steps) if step.step_id}
    for position, outcome in enumerate(outcomes):
        step_key = outcome.step.step_id
        index = index_by_step_id[step_key] if step_key else position
        persisted_step = steps[index]
        record_step_execution(
            deps.persistence,
            event_id,
            StepExecutionEnvelope(
                step_index=index,
                agent_name=persisted_step.agent_name,
                task_text=persisted_step.task_text,
                allowed_tools=list(persisted_step.allowed_tools),
                result_text=outcome.result_text,
                attempt_count=outcome.attempt_count,
                step_id=persisted_step.step_id,
                depends_on=persisted_step.depends_on,
                required_event_fields=persisted_step.required_event_fields,
                missing_event_fields=outcome.missing_event_fields,
                status=outcome.status,
                failure_reason=outcome.failure_reason,
            ),
        )


def _execute_protocol_plan(
    deps: FlowDeps,
    event_id: str,
    main_agent: "MainAgent",
    insights_agent: "InsightsAgent",
    protocol: "Protocol",
    steps: tuple[Step, ...],
    precedent_matches: tuple,
    *,
    resumed: bool = False,
) -> FlowResult:
    event = deps.persistence.fetch_event(event_id)
    if not resumed:
        deadline_failure = _deadline_failure(deps, event_id, "execution")
        if deadline_failure is not None:
            return deadline_failure

    agents_by_name = {name: deps.registry.get(name) for name in protocol.participating_agents}
    persisted_rows = event.get("steps", [])
    prior = _prior_outcomes(persisted_rows, steps)
    execution_steps = tuple(
        replace(
            step,
            task_text=(
                f"{step.task_text}\n\nCurrent validated event data JSON (use this as the source of truth): "
                f"{json.dumps({name: event.get(name) for name in step.required_event_fields}, ensure_ascii=False, sort_keys=True)}"
            ),
        )
        if step.required_event_fields
        else step
        for step in steps
    )
    with authenticated_request_identity(event["sender_identity"]):
        run_result = execute_steps(
            list(execution_steps),
            agents_by_name,
            deps.settings_store,
            task_rewriter=functools.partial(rewrite_task, main_agent),
            event_data=event,
            prior_outcomes=prior,
        )
    if run_result.waiting_for_event_data and not persisted_rows:
        _persist_step_plan(deps, event_id, steps)
    _persist_step_outcomes(deps, event_id, steps, run_result.step_outcomes)

    if run_result.waiting_for_event_data:
        latest_event = deps.persistence.fetch_event(event_id)
        conversation_messages: tuple[dict, ...] = ()
        if latest_event.get("conversation_id"):
            conversation_messages = tuple(
                deps.persistence.fetch_conversation_messages(latest_event["conversation_id"], 12)
            )
        question = formulate_event_data_question(
            main_agent, latest_event, run_result.missing_event_fields, conversation_messages
        )
        waiting_step_ids = tuple(
            outcome.step.step_id for outcome in run_result.step_outcomes
            if outcome.status == "waiting_for_event_data"
        )
        if latest_event.get("conversation_id") and deps.conversation_history_turns > 0:
            deps.persistence.append_conversation_message(
                latest_event["conversation_id"],
                "assistant",
                question,
                ttl_hours=deps.conversation_history_ttl_hours,
                max_turns=deps.conversation_history_turns,
                event_id=event_id,
            )
        create_event_data_hold(
            deps.persistence, event_id, run_result.missing_event_fields, question, waiting_step_ids
        )
        logger.info(
            "protocol waiting for event data",
            extra={
                "event": "protocol_waiting_for_event_data",
                "event_id": event_id,
                "missing_event_fields": list(run_result.missing_event_fields),
                "trace_id": get_trace_id(),
            },
        )
        return FlowResult(event_id, "waiting_for_event_data", question)

    if not run_result.completed:
        record_event_outcome(deps.persistence, event_id, "failed", failure_reason=run_result.failure_cause)
        _log_event_outcome(
            event_id, "failed", failure_reason=run_result.failure_cause, stage="execution",
            failed_step_agent=run_result.failed_step_agent,
        )
        return FlowResult(event_id, "failed", run_result.failure_cause or "")

    recall_selection = next(
        (
            outcome.result_text
            for outcome in run_result.step_outcomes
            if outcome.result_text and outcome.result_text.startswith("DRONE_SELECTION_REQUIRED:")
        ),
        None,
    )
    if protocol.name in {"return_drone_to_base", "recall_drone_to_base"} and recall_selection is not None:
        create_event_data_hold(
            deps.persistence,
            event_id,
            ("drone_selection",),
            recall_selection,
            tuple(outcome.step.step_id for outcome in run_result.step_outcomes),
        )
        return FlowResult(event_id, "waiting_for_drone_selection", recall_selection)

    return _finish_protocol_assessment(
        deps, event_id, main_agent, insights_agent, protocol, run_result.step_outcomes, precedent_matches,
        enforce_deadline=not resumed,
    )


def _finish_protocol_assessment(
    deps: FlowDeps,
    event_id: str,
    main_agent: "MainAgent",
    insights_agent: "InsightsAgent",
    protocol: "Protocol",
    step_outcomes: tuple[StepOutcome, ...],
    precedent_matches: tuple,
    *,
    enforce_deadline: bool,
) -> FlowResult:
    if enforce_deadline:
        deadline_failure = _deadline_failure(deps, event_id, "final_assessment")
        if deadline_failure is not None:
            return deadline_failure
    final_assessment = None
    persisted_event = deps.persistence.fetch_event(event_id)
    if deps.optimization_policy.final_assessment_mode == "low_risk_merged" and persisted_event.get("risk_level") == "low":
        try:
            final_assessment = assess_final_once(main_agent, protocol, step_outcomes, precedent_matches)
        except OrchestrationParseError as exc:
            logger.warning(
                "merged final assessment failed; using separate verifiers",
                extra={"event": "final_assessment_invalid", "reason": str(exc), "trace_id": get_trace_id()},
            )

    insight_text = (
        final_assessment.insight
        if final_assessment is not None
        else build_insight(insights_agent, protocol, step_outcomes, comparable_history=precedent_matches)
    )
    if protocol.name == "overall_situational_picture" or len(protocol.participating_agents) > 1:
        valid_outcomes = tuple(o for o in step_outcomes if o.result_text and o.succeeded)
        if len(valid_outcomes) > 1:
            try:
                synthesis = synthesize_operational_picture(
                    main_agent, protocol, valid_outcomes, persisted_event.get("raw_text", "")
                )
                if synthesis:
                    insight_text = synthesis
            except Exception as exc:
                logger.warning(
                    "multi-agent synthesis failed: %s", exc, extra={"event": "synthesis_failed", "trace_id": get_trace_id()}
                )
    logger.info(
        "insight generated",
        extra={
            "event": "insight_generated", "event_id": event_id, "protocol": protocol.name,
            "insight_text": insight_text, "trace_id": get_trace_id(),
        },
    )

    if enforce_deadline:
        deadline_failure = _deadline_failure(deps, event_id, "judgment")
        if deadline_failure is not None:
            return deadline_failure
    if final_assessment is not None:
        verdict = final_assessment.verdict
    else:
        try:
            verdict = judge_success(main_agent, protocol, step_outcomes, insight_text=insight_text)
        except OrchestrationParseError:
            try:
                verdict = judge_success(main_agent, protocol, step_outcomes, insight_text=insight_text)
            except OrchestrationParseError as exc:
                record_event_outcome(
                    deps.persistence, event_id, "failed",
                    failure_reason=f"success judgment failed: {exc}", insight_text=insight_text,
                )
                _log_event_outcome(event_id, "failed", failure_reason=str(exc), stage="judgment")
                return FlowResult(event_id, "failed", str(exc))

    outcome = _VERDICT_TO_OUTCOME[verdict.verdict]
    record_event_outcome(deps.persistence, event_id, outcome, insight_text=insight_text)
    logger.info(
        "final verdict",
        extra={
            "event": "final_verdict", "event_id": event_id, "verdict": verdict.verdict,
            "reasoning": verdict.reasoning, "trace_id": get_trace_id(),
        },
    )
    _log_event_outcome(event_id, outcome, reasoning=verdict.reasoning)
    return FlowResult(event_id, outcome, verdict.reasoning)


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
    record_event_data_update(deps.persistence, event["event_id"], updates)
    deps.persistence.resolve_held_event(
        "event_data", hold["hold_id"], {"resolved_by": sender_identity, "updated_fields": sorted(updates)}
    )
    return EventDataReplyResult(
        event["event_id"], updates,
        parsed.reply_text,
    )


def resume_after_event_data(
    deps: FlowDeps,
    event_id: str,
    main_agent: "MainAgent",
    insights_agent: "InsightsAgent",
) -> FlowResult:
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
        record_event_outcome(deps.persistence, event_id, "failed", failure_reason=reason)
        return FlowResult(event_id, "failed", reason)
    rows = event.get("steps", [])
    steps = tuple(_step_from_row(row) for row in rows)
    precedent_matches = _look_up_precedent_if_possible(deps, event_id, event)
    return _execute_protocol_plan(
        deps, event_id, main_agent, insights_agent, protocol, steps, precedent_matches, resumed=True,
    )
