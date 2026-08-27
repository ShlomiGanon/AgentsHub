"""Main Agent entity and every focused decision it makes."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from agents.base import Agent
from config.base import BaseConfig
from protocols.model import Protocol, Step
from tools.tracing import stage_context

if TYPE_CHECKING:
    from agents.registry import AgentRegistry
    from agents.runtime import AgentDescriptor
    from protocols.executor import StepOutcome


class OrchestrationParseError(Exception):
    """A Main Agent response could not be parsed into the expected shape."""


class MainAgent(Agent):
    name = "main_agent"
    role = (
        "The orchestrator, and the only component that makes judgment calls: risk assessment, "
        "protocol selection, task formulation, and success judgment. Reasons over what it is "
        "handed; the specialist agents act, this agent decides."
    )
    system_prompt = (
        "You are the Main Agent, the orchestrator of a field-report multi-agent system. You are "
        "given one focused judgment to make at a time, with everything relevant already provided — "
        "never assume context from a different judgment. Follow the exact response format each "
        "prompt requests precisely; your response is parsed programmatically, not read by a person."
    )


@dataclass(frozen=True)
class RiskAssessment:
    score: float
    level: Literal["high", "low"]
    reason: str


@dataclass(frozen=True)
class IntentResult:
    intent: Literal["question", "report", "request", "conversational"]
    reason: str


@dataclass(frozen=True)
class ProtocolSelectionResult:
    status: Literal["selected", "ambiguous", "no_match"]
    protocol_name: str | None = None
    candidate_names: tuple[str, ...] = ()
    reason: str = ""


@dataclass(frozen=True)
class FormulationResult:
    steps: tuple[Step, ...] = ()
    failed_agent_name: str | None = None
    failure_reason: str | None = None

    @property
    def success(self) -> bool:
        return self.failure_reason is None


@dataclass(frozen=True)
class SuccessVerdict:
    verdict: Literal["success", "failure", "uncertain"]
    reasoning: str


_RISK_SCORE_PATTERN = re.compile(r"RISK_SCORE:\s*([0-9]*\.?[0-9]+)", re.IGNORECASE)
_RISK_REASON_PATTERN = re.compile(r"REASON:\s*(.+)", re.IGNORECASE | re.DOTALL)
_INTENT_PATTERN = re.compile(r"INTENT:\s*(question|report|request|conversational)", re.IGNORECASE)
_INTENT_REASON_PATTERN = re.compile(r"REASON:\s*(.+)", re.IGNORECASE | re.DOTALL)
_SELECTED_PATTERN = re.compile(r"SELECTED:\s*(\S+)", re.IGNORECASE)
_AMBIGUOUS_PATTERN = re.compile(r"AMBIGUOUS:\s*(.+)", re.IGNORECASE)
_NO_MATCH_PATTERN = re.compile(r"NO_MATCH:\s*(.+)", re.IGNORECASE | re.DOTALL)
_SELECTION_REASON_PATTERN = re.compile(r"REASON:\s*(.+)", re.IGNORECASE | re.DOTALL)
_AGENT_TASK_PATTERN = re.compile(r"AGENT:\s*(\S+)\s*\n\s*TASK:\s*(.+?)(?=\nAGENT:|\Z)", re.IGNORECASE | re.DOTALL)
_VERDICT_PATTERN = re.compile(r"VERDICT:\s*(success|failure|uncertain)", re.IGNORECASE)
_REASONING_PATTERN = re.compile(r"REASONING:\s*(.+)", re.IGNORECASE | re.DOTALL)


def _build_risk_assessment_prompt(classification: str | None, area: str | None, description: str | None, severity: str | None) -> str:
    return (
        "Assess the risk of the following event on a scale from 0.0 (no risk) to 1.0 (extreme risk).\n"
        f"Classification: {classification or '(unresolved)'}\n"
        f"Area: {area or '(unresolved)'}\n"
        f"Description: {description or '(none provided)'}\n"
        f"Severity: {severity or '(none provided)'}\n\n"
        "Respond in exactly this format, two lines, nothing else:\n"
        "RISK_SCORE: <a number between 0.0 and 1.0>\n"
        "REASON: <one or two sentences explaining the assessment>"
    )


def _parse_risk_assessment_response(raw_text: str) -> tuple[float, str]:
    score_match = _RISK_SCORE_PATTERN.search(raw_text)
    reason_match = _RISK_REASON_PATTERN.search(raw_text)
    if score_match is None or reason_match is None:
        raise OrchestrationParseError(f"could not parse risk assessment response: {raw_text!r}")

    score = float(score_match.group(1))
    if not 0.0 <= score <= 1.0:
        raise OrchestrationParseError(f"risk score out of range [0.0, 1.0]: {score}")
    return score, reason_match.group(1).strip()


def assess_risk(main_agent: MainAgent, classification: str | None, area: str | None, description: str | None, severity: str | None, risk_threshold: float) -> RiskAssessment:
    with stage_context("risk_assessment"):
        result = main_agent.process(_build_risk_assessment_prompt(classification, area, description, severity), [])
    if result.status != "success":
        raise OrchestrationParseError(f"risk assessment did not produce a usable response: {result.text}")
    score, reason = _parse_risk_assessment_response(result.text)
    return RiskAssessment(score=score, level="high" if score >= risk_threshold else "low", reason=reason)


def _build_intent_prompt(message_text: str, protocols: tuple[Protocol, ...]) -> str:
    protocol_lines = "\n".join(f"- {protocol.name}: {protocol.description}" for protocol in protocols)
    return (
        "Decide what kind of message this is: a QUESTION (asking the system to retrieve, check, "
        "or determine something real), a REPORT (describing something that happened), a REQUEST "
        "(asking for an action to be taken), or CONVERSATIONAL (purely social, with nothing to "
        "look up, check, or act on — a greeting, thanks, or small talk). Whether something is a "
        "request depends on whether it asks for something one of the following protocols does — "
        "in practice, \"is this a request for action\" is \"does this ask for something a "
        "protocol does\":\n\n"
        f"{protocol_lines}\n\n"
        f"Message: {message_text}\n\n"
        "A message asking the system to retrieve, check, or determine something is a QUESTION "
        "even if it turns out nothing here can actually answer it — e.g. \"do I have any "
        "tasks?\" is a QUESTION (it asks the system to check something real), not "
        "CONVERSATIONAL, whatever the eventual answer is. Only classify as CONVERSATIONAL when "
        "nothing is actually being asked for at all — e.g. \"hey, how are you?\", \"thanks\", "
        "\"good morning\".\n\n"
        "Respond in exactly this format, two lines:\n"
        "INTENT: <question | report | request | conversational>\n"
        "REASON: <why>"
    )


def _parse_intent_response(raw_text: str) -> IntentResult:
    intent_match = _INTENT_PATTERN.search(raw_text)
    reason_match = _INTENT_REASON_PATTERN.search(raw_text)
    if intent_match is None or reason_match is None:
        raise OrchestrationParseError(f"could not parse message intent response: {raw_text!r}")
    return IntentResult(intent=intent_match.group(1).lower(), reason=reason_match.group(1).strip())


def classify_intent(main_agent: MainAgent, protocols: tuple[Protocol, ...], message_text: str) -> IntentResult:
    with stage_context("intent_classification"):
        result = main_agent.process(_build_intent_prompt(message_text, protocols), [])
    if result.status != "success":
        raise OrchestrationParseError(f"message intent classification did not produce a usable response: {result.text}")
    return _parse_intent_response(result.text)


def _build_conversational_prompt(message_text: str) -> str:
    return (
        "Reply naturally and directly to this message — a greeting, thanks, or other small talk "
        "with nothing to look up, check, or act on. Answer the way a person would: brief, warm, "
        f"and direct.\n\nMessage: {message_text}\n\n"
        "Do not invent facts, data, or capabilities you don't actually have. If this message "
        "turns out to ask for something real you can't honestly answer, say so plainly instead "
        "of guessing — never fabricate an answer to sound helpful. Respond with only your reply, nothing else."
    )


def answer_conversationally(main_agent: MainAgent, message_text: str) -> str:
    with stage_context("conversational_reply"):
        result = main_agent.process(_build_conversational_prompt(message_text), [])
    if result.status != "success":
        raise OrchestrationParseError(f"conversational reply did not produce a usable response: {result.text}")
    return result.text.strip()


def _build_selection_prompt(raw_text: str, classification: str | None, area: str | None, description: str | None, protocols: tuple[Protocol, ...]) -> str:
    protocol_lines = "\n".join(f"- {protocol.name}: {protocol.description}" for protocol in protocols)
    return (
        "Choose the protocol whose description best fits the following event. Selection is by "
        "description alone — do not infer a match from the classification name.\n\n"
        f"Raw report text: {raw_text}\n"
        f"Classification: {classification or '(unresolved)'}\n"
        f"Area: {area or '(unresolved)'}\n"
        f"Description: {description or '(none provided)'}\n\n"
        "Available protocols:\n"
        f"{protocol_lines}\n\n"
        "If exactly one protocol clearly fits, respond in exactly this format, two lines:\n"
        "SELECTED: <protocol name>\n"
        "REASON: <why this one fits>\n\n"
        "If more than one protocol fits equally well and you cannot discriminate between them, "
        "respond in exactly this format instead:\n"
        "AMBIGUOUS: <comma-separated protocol names>\n"
        "REASON: <why you could not discriminate>\n\n"
        "If none of the protocols genuinely apply to this event, do not force a match onto the "
        "closest-sounding one — respond in exactly this format instead, one line:\n"
        "NO_MATCH: <why no protocol applies>"
    )


def _parse_selection_response(raw_text: str) -> ProtocolSelectionResult:
    reason_match = _SELECTION_REASON_PATTERN.search(raw_text)
    reason = reason_match.group(1).strip() if reason_match else ""
    selected_match = _SELECTED_PATTERN.search(raw_text)
    if selected_match:
        return ProtocolSelectionResult(status="selected", protocol_name=selected_match.group(1), reason=reason)
    ambiguous_match = _AMBIGUOUS_PATTERN.search(raw_text)
    if ambiguous_match:
        names = tuple(name.strip() for name in ambiguous_match.group(1).split(",") if name.strip())
        return ProtocolSelectionResult(status="ambiguous", candidate_names=names, reason=reason)
    no_match_match = _NO_MATCH_PATTERN.search(raw_text)
    if no_match_match:
        return ProtocolSelectionResult(status="no_match", reason=no_match_match.group(1).strip())
    raise OrchestrationParseError(f"could not parse protocol selection response: {raw_text!r}")


def select_protocol(main_agent: MainAgent, raw_text: str, classification: str | None, area: str | None, description: str | None, protocols: tuple[Protocol, ...], risk_level: Literal["high", "low"]) -> ProtocolSelectionResult:
    with stage_context("protocol_selection"):
        result = main_agent.process(_build_selection_prompt(raw_text, classification, area, description, protocols), [])
    if result.status != "success":
        raise OrchestrationParseError(f"protocol selection did not produce a usable response: {result.text}")
    selection = _parse_selection_response(result.text)
    if selection.status == "ambiguous" and risk_level == "high":
        protocols_by_name = {protocol.name: protocol for protocol in protocols}
        candidates = [protocols_by_name[name] for name in selection.candidate_names if name in protocols_by_name]
        if candidates:
            most_critical = max(candidates, key=lambda protocol: protocol.criticality)
            return ProtocolSelectionResult(status="selected", protocol_name=most_critical.name, reason=f"high risk, ambiguous among {', '.join(selection.candidate_names)}; proceeding with the most critical candidate rather than waiting ({selection.reason})")
    return selection


def _build_formulation_prompt(protocol: Protocol, descriptors: list[AgentDescriptor], raw_text: str, classification: str | None, area: str | None, description: str | None, precedent_context: tuple) -> str:
    agents_block = "\n".join(f"- {descriptor.name}: {descriptor.role}" for descriptor in descriptors)
    precedent_block = ""
    if precedent_context:
        precedent_block = "\nRelevant precedent (what was tried before and what came of it):\n" + "\n".join(str(item) for item in precedent_context) + "\n"
    return (
        f"Write a specific task for each agent participating in the '{protocol.name}' protocol, given this event. Each task should say what that agent in particular should determine or do — write for their role, not a generic instruction copied to everyone.\n\n"
        f"Event raw text: {raw_text}\nClassification: {classification or '(unresolved)'}\nArea: {area or '(unresolved)'}\nDescription: {description or '(none provided)'}\n{precedent_block}\nParticipating agents:\n{agents_block}\n\n"
        "Respond with one block per agent, in exactly this format, in the same order as listed above:\nAGENT: <agent name>\nTASK: <the task for that agent>"
    )


def _parse_formulation_response(raw_text: str) -> dict[str, str]:
    return {match.group(1): match.group(2).strip() for match in _AGENT_TASK_PATTERN.finditer(raw_text)}


def formulate_tasks(main_agent: MainAgent, protocol: Protocol, registry: AgentRegistry, raw_text: str, classification: str | None, area: str | None, description: str | None, precedent_context: tuple = ()) -> FormulationResult:
    descriptors = [registry.descriptor_for(name) for name in protocol.participating_agents]
    with stage_context("task_formulation"):
        result = main_agent.process(_build_formulation_prompt(protocol, descriptors, raw_text, classification, area, description, precedent_context), [])
    if result.status != "success":
        return FormulationResult(failure_reason=f"formulation did not produce a usable response: {result.text}")
    tasks_by_agent = _parse_formulation_response(result.text)
    steps = []
    for descriptor in descriptors:
        task_text = tasks_by_agent.get(descriptor.name)
        if task_text is None:
            return FormulationResult(failed_agent_name=descriptor.name, failure_reason=f"model did not produce a task for '{descriptor.name}'")
        exposed_names = {tool.name for tool in descriptor.tools}
        allowed_tools = tuple(name for name in protocol.approved_tools if name in exposed_names)
        steps.append(Step(agent_name=descriptor.name, task_text=task_text, allowed_tools=allowed_tools))
    return FormulationResult(steps=tuple(steps))


def _build_rewrite_prompt(step: Step, missing: str) -> str:
    return (
        f"The task below was given to agent '{step.agent_name}', who reported it unclear or unactionable, stating what was missing: {missing}\n\n"
        f"Original task: {step.task_text}\n\nRewrite the task to address exactly what's missing. Respond with only the rewritten task text, nothing else."
    )


def rewrite_task(main_agent: MainAgent, step: Step, missing: str) -> str:
    with stage_context("task_rewrite"):
        result = main_agent.process(_build_rewrite_prompt(step, missing), [])
    if result.status != "success":
        raise OrchestrationParseError(f"task rewrite did not produce a usable response: {result.text}")
    return result.text.strip()


def _build_judgment_prompt(protocol: Protocol, step_outcomes: tuple[StepOutcome, ...], insight_text: str) -> str:
    steps_block = "\n".join(
        f"- {outcome.step.agent_name} was asked: {outcome.step.task_text!r}\n  and {'succeeded' if outcome.succeeded else 'failed'}, returning: {outcome.result_text!r}"
        for outcome in step_outcomes
    )
    insight_block = f"\nInsight from comparing this run to history: {insight_text}\n" if insight_text else ""
    return (
        f"Judge whether this protocol run succeeded, given what success looks like for this protocol:\n{protocol.expected_success_output}\n\nWhat actually happened:\n{steps_block}\n{insight_block}\n"
        "Compare the meaning of what happened against what success looks like — do not require exact wording. Respond in exactly this format, two lines:\nVERDICT: <success | failure | uncertain>\nREASONING: <why>"
    )


def _parse_judgment_response(raw_text: str) -> SuccessVerdict:
    verdict_match = _VERDICT_PATTERN.search(raw_text)
    reasoning_match = _REASONING_PATTERN.search(raw_text)
    if verdict_match is None or reasoning_match is None:
        raise OrchestrationParseError(f"could not parse success judgment response: {raw_text!r}")
    return SuccessVerdict(verdict=verdict_match.group(1).lower(), reasoning=reasoning_match.group(1).strip())


def judge_success(main_agent: MainAgent, protocol: Protocol, step_outcomes: tuple[StepOutcome, ...], insight_text: str = "") -> SuccessVerdict:
    with stage_context("success_judgment"):
        result = main_agent.process(_build_judgment_prompt(protocol, step_outcomes, insight_text), [])
    if result.status != "success":
        raise OrchestrationParseError(f"success judgment did not produce a usable response: {result.text}")
    return _parse_judgment_response(result.text)


def construct_core_agents(base_config: BaseConfig) -> dict[str, Agent]:
    return {"main_agent": MainAgent(model=base_config.core_model.model, api_key=base_config.core_model.api_key)}
