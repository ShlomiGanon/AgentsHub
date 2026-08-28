"""The new-event flow and the package's declared entry point (work_plan.md §6.11, §6.14)."""

import functools
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Literal

from history import (
    ExtractionExecutionError,
    InitialEventEnvelope,
    StepExecutionEnvelope,
    extract_event,
    record_event_outcome,
    record_event_state,
    record_extracted_fields,
    record_initial_event,
    record_step_execution,
)
from orchestrator.holds import (
    UNRESOLVED_FIELD,
    answer_approval_hold,
    answer_clarification_hold,
    create_approval_hold,
    create_clarification_hold,
    determine_approval_hold,
    determine_clarification_hold,
)
from orchestrator.reasoning import build_insight, construct_insights_agent
from orchestrator.reasoning import (
    OrchestrationParseError,
    answer_conversationally,
    assess_risk,
    classify_intent,
    construct_core_agents as construct_main_agent,
    formulate_tasks,
    judge_success,
    rewrite_task,
    select_protocol,
)
from orchestrator.reasoning import answer_question, determine_closure, look_up_precedent
from orchestrator.event_queue import SerialEventQueue
from profiles import HUMAN_ACTIVATION_TYPE
from protocols.executor import execute_steps
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

FlowOutcome = Literal[
    "closed_on_precedent", "declined", "succeeded", "failed", "uncertain", "no_match_protocol",
    "held_for_clarification", "held_for_approval",
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


@dataclass(frozen=True)
class FlowResult:
    event_id: str
    outcome: FlowOutcome
    detail: str = ""


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
) -> str:
    """The synchronous prefix of a report: write the raw text and return the event ID, before any model call runs (§7.2's own requirement — "before any processing begins")."""

    event_id = record_initial_event(
        deps.persistence,
        InitialEventEnvelope(
            raw_text=raw_text, source=source, received_at=received_at, sender_identity=sender_identity,
            source_message_id=source_message_id,
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

    if determine_clarification_hold(extraction_result):
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


def begin_request(deps: FlowDeps, raw_text: str, received_at: str, sender_identity: str, source_message_id: str | None = None) -> str:
    """The synchronous prefix of a request: write the raw text, already classified `human_activation` (§6.13 — there is nothing to extract), and return the event ID."""

    event_id = record_initial_event(
        deps.persistence,
        InitialEventEnvelope(
            raw_text=raw_text, source="telegram", received_at=received_at, sender_identity=sender_identity,
            source_message_id=source_message_id, occurred_at=received_at, occurred_at_is_fallback=False,
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
) -> tuple[Literal["question", "report", "request", "conversational"], object]:
    """Route a person's message by intent (§6.13)."""

    intent = classify_intent(main_agent, deps.protocol_set.all(), message_text)

    if intent.intent == "conversational":
        return "conversational", answer_conversationally(main_agent, message_text)

    if intent.intent == "question":
        return "question", answer_question(main_agent, message_text, deps.registry, deps.history_query_service)

    if intent.intent == "report":
        return "report", process_report(deps, main_agent, insights_agent, message_text, "telegram", received_at, sender_identity)

    return "request", process_request(deps, main_agent, insights_agent, message_text, received_at, sender_identity, is_commander)


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
    """Resume at risk assessment, not extraction — the other extracted fields are still valid and re-running extraction would discard the commander's decision (§6.2's own rule)."""

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
    if event["occurred_at"] is None or event["classification"] is None or event["area"] is None:
        return ()
    return look_up_precedent(deps.history_query_service, event_id, event["classification"], event["area"], event["occurred_at"])


def continue_from_risk_assessment(deps: FlowDeps, event_id: str, main_agent: "MainAgent", insights_agent: "InsightsAgent", originated_from_commander: bool) -> FlowResult:
    event = deps.persistence.fetch_event(event_id)
    raw_text, classification, area = event["raw_text"], event["classification"], event["area"]
    description, severity = event["description"], event["severity"]

    risk_assessment = assess_risk(main_agent, classification, area, description, severity, deps.settings_store.get_risk_threshold())
    record_event_state(deps.persistence, event_id, {"risk_level": risk_assessment.level, "risk_reason": risk_assessment.reason})
    logger.info(
        "risk assessed",
        extra={
            "event": "risk_assessed", "event_id": event_id, "risk_level": risk_assessment.level,
            "risk_score": risk_assessment.score, "risk_reason": risk_assessment.reason, "trace_id": get_trace_id(),
        },
    )

    try:
        selection = select_protocol(main_agent, raw_text, classification, area, description, deps.protocol_set.all(), risk_assessment.level)
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

    closing_event_id = determine_closure(risk_assessment.level, classification, precedent_matches)
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
    formulation = formulate_tasks(main_agent, protocol, deps.registry, raw_text, classification, area, description, precedent_context=precedent_matches)
    if not formulation.success:
        formulation = formulate_tasks(main_agent, protocol, deps.registry, raw_text, classification, area, description, precedent_context=precedent_matches)

    if not formulation.success:
        record_event_outcome(deps.persistence, event_id, "failed", failure_reason=formulation.failure_reason)
        _log_event_outcome(event_id, "failed", failure_reason=formulation.failure_reason, stage="formulation")
        return FlowResult(event_id, "failed", formulation.failure_reason or "")

    agents_by_name = {name: deps.registry.get(name) for name in protocol.participating_agents}
    task_rewriter = functools.partial(rewrite_task, main_agent)

    run_result = execute_steps(list(formulation.steps), agents_by_name, deps.settings_store, task_rewriter=task_rewriter)

    for index, outcome in enumerate(run_result.step_outcomes):
        record_step_execution(
            deps.persistence, event_id,
            StepExecutionEnvelope(
                step_index=index, agent_name=outcome.step.agent_name, task_text=outcome.step.task_text,
                allowed_tools=list(outcome.step.allowed_tools), result_text=outcome.result_text, attempt_count=outcome.attempt_count,
            ),
        )

    if not run_result.completed:
        record_event_outcome(deps.persistence, event_id, "failed", failure_reason=run_result.failure_cause)
        _log_event_outcome(event_id, "failed", failure_reason=run_result.failure_cause, stage="execution", failed_step_agent=run_result.failed_step_agent)
        return FlowResult(event_id, "failed", run_result.failure_cause or "")

    insight_text = build_insight(insights_agent, protocol, run_result.step_outcomes, comparable_history=precedent_matches)
    logger.info(
        "insight generated",
        extra={"event": "insight_generated", "event_id": event_id, "protocol": protocol.name, "insight_text": insight_text, "trace_id": get_trace_id()},
    )

    try:
        verdict = judge_success(main_agent, protocol, run_result.step_outcomes, insight_text=insight_text)
    except OrchestrationParseError:
        try:
            verdict = judge_success(main_agent, protocol, run_result.step_outcomes, insight_text=insight_text)
        except OrchestrationParseError as exc:
            record_event_outcome(deps.persistence, event_id, "failed", failure_reason=f"success judgment failed: {exc}", insight_text=insight_text)
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
