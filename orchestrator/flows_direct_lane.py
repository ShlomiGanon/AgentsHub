"""Direct-lane classification and execution."""

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

from orchestrator.flows import FlowDeps, FlowResult, _log_event_outcome, _record_outcome_with_report
from orchestrator.flows_protocol import _persist_step_outcomes, _persist_step_plan

_DIRECT_LANE_CLASSIFY_POLICY = InvocationPolicy(max_output_tokens=400, reasoning_effort="none")

@dataclass(frozen=True)
class DirectLaneAction:
    protocol_name: str
    agent_name: str
    tool_name: str
    parameters: dict

@dataclass(frozen=True)
class DirectLaneResult:
    eligible: bool
    actions: tuple[DirectLaneAction, ...] = ()
    reason: str = ""

def _direct_lane_eligible_protocols(protocols: "tuple[Protocol, ...]") -> "tuple[Protocol, ...]":
    return tuple(
        protocol for protocol in protocols
        if protocol.direct_lane_eligible and not protocol.commander_only and not protocol.approval_flag
    )

def _build_direct_lane_prompt(protocols: "tuple[Protocol, ...]", registry: "AgentRegistry", raw_text: str) -> tuple[str, dict]:
    tool_lines: list[str] = []
    tool_owner: dict[str, tuple[str, str]] = {}
    for protocol in protocols:
        agent_name = protocol.participating_agents[0]
        tools_by_name = {tool_info.name: tool_info for tool_info in registry.descriptor_for(agent_name).tools}
        for tool_name in protocol.approved_tools:
            tool_info = tools_by_name.get(tool_name)
            if tool_info is None or tool_name in tool_owner:
                continue
            tool_owner[tool_name] = (protocol.name, agent_name)
            tool_lines.append(f"- {tool_name}: {tool_info.description}")

    prompt = (
        "You triage one incoming message for a fast lane that handles ONLY simple, low-stakes, "
        "already-unambiguous actions. Available tools (the only ones you may name):\n"
        + "\n".join(tool_lines)
        + "\n\nIf this message clearly matches one or more of the tools above, with every "
        "parameter each chosen tool needs explicitly stated in the message (never guessed), "
        "return exactly one JSON object: "
        '{"eligible": true, "actions": [{"tool_name": "...", "parameters": {...}}, ...]}. '
        "A message with more than one such intent may return more than one action. Otherwise -- "
        "if the message involves any threat, hostile/suspicious activity, an emergency, any other "
        "risk indicator, any ambiguity, a parameter a chosen tool needs but the message does not "
        "state, or does not clearly match any tool above -- return exactly "
        '{"eligible": false, "reason": "..."}. When in doubt, return not eligible: the full '
        "pipeline handles everything this lane does not.\n\n"
        f"Message: {json.dumps(raw_text, ensure_ascii=False)}"
    )
    return prompt, tool_owner

def classify_direct_lane(
    main_agent: "MainAgent", protocols: "tuple[Protocol, ...]", registry: "AgentRegistry", raw_text: str
) -> DirectLaneResult:
    eligible_protocols = _direct_lane_eligible_protocols(protocols)
    if not eligible_protocols:
        return DirectLaneResult(eligible=False, reason="no direct-lane-eligible protocols declared")

    prompt, tool_owner = _build_direct_lane_prompt(eligible_protocols, registry, raw_text)
    try:
        with stage_context("direct_lane_classification"):
            agent_result = main_agent.process(prompt, [], invocation_policy=_DIRECT_LANE_CLASSIFY_POLICY)
    except Exception as exc:
        return DirectLaneResult(eligible=False, reason=f"direct lane classification failed: {exc}")
    if agent_result.status != "success":
        return DirectLaneResult(eligible=False, reason="direct lane classification was unclear")

    try:
        payload = json.loads(_unwrap_json_code_fence(agent_result.text))
    except (json.JSONDecodeError, TypeError):
        return DirectLaneResult(eligible=False, reason="direct lane classification returned invalid JSON")
    if not isinstance(payload, dict):
        return DirectLaneResult(eligible=False, reason="direct lane classification returned a non-object")
    if not payload.get("eligible"):
        return DirectLaneResult(eligible=False, reason=str(payload.get("reason") or ""))

    raw_actions = payload.get("actions")
    if not isinstance(raw_actions, list) or not raw_actions:
        return DirectLaneResult(eligible=False, reason="no actions returned despite eligible=true")

    actions: list[DirectLaneAction] = []
    for raw_action in raw_actions:
        if not isinstance(raw_action, dict):
            return DirectLaneResult(eligible=False, reason="malformed action entry")
        tool_name = raw_action.get("tool_name")
        owner = tool_owner.get(tool_name)
        if owner is None:
            # Never trust a tool name the model invented -- fall back to the full pipeline
            # rather than calling something outside this protocol's own approved_tools.
            return DirectLaneResult(eligible=False, reason=f"unknown or unapproved tool: {tool_name!r}")
        parameters = raw_action.get("parameters")
        if not isinstance(parameters, dict):
            parameters = {}
        protocol_name, agent_name = owner
        actions.append(
            DirectLaneAction(protocol_name=protocol_name, agent_name=agent_name, tool_name=tool_name, parameters=parameters)
        )

    return DirectLaneResult(eligible=True, actions=tuple(actions))

def run_direct_lane(
    deps: FlowDeps, event_id: str, sender_identity: str, result: DirectLaneResult,
) -> FlowResult:
    """Calls each identified tool directly (no crewai turn), then finishes exactly like any
    other succeeded/failed run -- `_record_outcome_with_report` composes the small reply
    (item 6's actions_taken makes it describe what was actually done) and inserts the same
    job_finished/job_failed notification the queue-based pipeline already uses, so delivery is
    entirely unchanged."""

    agents_by_name = {action.agent_name: deps.registry.get(action.agent_name) for action in result.actions}
    steps = tuple(
        Step(
            agent_name=action.agent_name,
            task_text=f"Direct lane action: {action.tool_name}",
            allowed_tools=(action.tool_name,),
            step_id=str(index + 1),
            kind="direct_tool",
            direct_tool_name=action.tool_name,
            direct_tool_kwargs=action.parameters,
        )
        for index, action in enumerate(result.actions)
    )
    _persist_step_plan(deps, event_id, steps)
    with authenticated_request_identity(sender_identity):
        run_result = execute_steps(list(steps), agents_by_name, deps.settings_store)
    _persist_step_outcomes(deps, event_id, steps, run_result.step_outcomes)

    if not run_result.completed:
        _record_outcome_with_report(
            deps, event_id, "failed", failure_reason=run_result.failure_cause, force_compose=True,
        )
        _log_event_outcome(event_id, "failed", failure_reason=run_result.failure_cause, stage="direct_lane")
        return FlowResult(event_id, "failed", run_result.failure_cause or "")

    _record_outcome_with_report(deps, event_id, "succeeded", insight_text="", force_compose=True)
    _log_event_outcome(event_id, "succeeded", stage="direct_lane")
    return FlowResult(event_id, "succeeded", "")

def attempt_direct_lane(
    deps: FlowDeps, main_agent: "MainAgent", event_id: str, sender_identity: str, raw_text: str,
) -> "FlowResult | None":
    """Entry point for the direct lane, called synchronously from the request handler (item 9:
    "runs outside the serial queue") right after `begin_report` -- the event is already saved
    either way. Returns None (never a FlowResult) when the message is not eligible, so the
    caller falls back to the ordinary queued full-pipeline path unchanged; returns a real
    FlowResult, already terminal, when the direct lane handled it."""

    result = classify_direct_lane(main_agent, deps.protocol_set.all(), deps.registry, raw_text)
    if not result.eligible:
        direct_lane_declined(event_id=event_id, reason=result.reason)
        return None

    direct_lane_accepted(event_id=event_id, actions=[action.tool_name for action in result.actions])
    return run_direct_lane(deps, event_id, sender_identity, result)
