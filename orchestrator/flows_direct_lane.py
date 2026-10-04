"""Direct-lane classification and execution."""

import inspect
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

from orchestrator.reasoning import _unwrap_json_code_fence
from protocols import Step
from protocols.executor import execute_steps
from agents import InvocationPolicy, authenticated_request_identity
from tools import stage_context
from tools.log_events import direct_lane_accepted, direct_lane_declined

if TYPE_CHECKING:
    from agents.runtime import AgentRegistry
    from orchestrator.reasoning import MainAgent
    from protocols import Protocol

from orchestrator.flows_execution import FlowDeps, FlowResult, _log_event_outcome, _record_outcome_with_report
from orchestrator.flows_protocol import _persist_step_outcomes, _persist_step_plan

_DIRECT_LANE_CLASSIFY_POLICY = InvocationPolicy(max_output_tokens=400, reasoning_effort="none")

@dataclass(frozen=True)
class DirectLaneAction:
    """One tool call the direct lane will execute."""

    protocol_name: str
    agent_name: str
    tool_name: str
    parameters: dict

@dataclass(frozen=True)
class DirectLaneResult:
    """Whether the direct lane accepted the message, and which actions to run."""

    eligible: bool
    actions: tuple[DirectLaneAction, ...] = ()
    reason: str = ""

def _direct_lane_eligible_protocols(protocols: "tuple[Protocol, ...]") -> "tuple[Protocol, ...]":
    """Protocols the profile marked eligible for the direct lane."""

    return tuple(
        protocol for protocol in protocols
        if protocol.direct_lane_eligible and not protocol.commander_only and not protocol.approval_flag
    )

def _build_direct_lane_prompt(protocols: "tuple[Protocol, ...]", registry: "AgentRegistry", raw_text: str) -> tuple[str, dict]:
    """Prompt asking the cheap model to pick a direct-lane action or decline."""

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


def _parameters_match_tool(method, parameters: dict) -> bool:
    """False when the model named a parameter the tool does not accept."""

    try:
        signature = inspect.signature(method)
    except (TypeError, ValueError):
        return True
    allowed: set[str] = set()
    for name, param in signature.parameters.items():
        if name == "self":
            continue
        if param.kind == inspect.Parameter.VAR_KEYWORD:
            return True
        if param.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY):
            allowed.add(name)
    return set(parameters).issubset(allowed)


def classify_direct_lane(
    main_agent: "MainAgent", protocols: "tuple[Protocol, ...]", registry: "AgentRegistry", raw_text: str
) -> DirectLaneResult:
    """Ask whether this message can skip the full pipeline. Returns the lane result."""

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
        method = getattr(registry.get(agent_name), tool_name, None)
        if method is not None and not _parameters_match_tool(method, parameters):
            return DirectLaneResult(eligible=False, reason="direct lane parameters do not match the tool")
        actions.append(
            DirectLaneAction(protocol_name=protocol_name, agent_name=agent_name, tool_name=tool_name, parameters=parameters)
        )

    return DirectLaneResult(eligible=True, actions=tuple(actions))

def run_direct_lane(
    deps: FlowDeps, event_id: str, sender_identity: str, result: DirectLaneResult,
) -> FlowResult:
    """Call each identified tool directly, then record the same succeeded or failed outcome as a queued run."""

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
    """Try the fast lane after begin_report; return None so the caller can queue the full pipeline."""

    result = classify_direct_lane(main_agent, deps.protocol_set.all(), deps.registry, raw_text)
    if not result.eligible:
        direct_lane_declined(event_id=event_id, reason=result.reason)
        return None

    direct_lane_accepted(event_id=event_id, actions=[action.tool_name for action in result.actions])
    return run_direct_lane(deps, event_id, sender_identity, result)
