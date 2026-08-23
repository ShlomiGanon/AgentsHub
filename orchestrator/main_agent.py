"""The Main Agent's AI agent and risk assessment (work_plan.md §6.1, §6.3).

One agent, used for every judgment the Main Agent makes — message intent,
risk assessment, protocol selection, task writing, success judgment —
rather than a separate agent per decision, so the reasoning stays
consistent and the cost stays visible in one place. It has no tools of
its own (no `@tool`-decorated methods below): it reasons over what it's
handed, the specialists act. Each decision gets its own distinct prompt,
built and parsed by its own module (orchestrator.selection,
orchestrator.formulation, orchestrator.judgment) — this module holds the
agent itself plus risk assessment, the simplest of the five.

`construct_core_agents` is the corrected core-agent-construction seam —
see profiles/loader.py's docstring for why it can't live there.
"""

import re
from dataclasses import dataclass
from typing import Literal

from agents.base import Agent
from config.base import BaseConfig
from orchestrator.errors import OrchestrationParseError


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


_RISK_SCORE_PATTERN = re.compile(r"RISK_SCORE:\s*([0-9]*\.?[0-9]+)", re.IGNORECASE)
_REASON_PATTERN = re.compile(r"REASON:\s*(.+)", re.IGNORECASE | re.DOTALL)


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
    reason_match = _REASON_PATTERN.search(raw_text)

    if score_match is None or reason_match is None:
        raise OrchestrationParseError(f"could not parse risk assessment response: {raw_text!r}")

    score = float(score_match.group(1))
    if not (0.0 <= score <= 1.0):
        raise OrchestrationParseError(f"risk score out of range [0.0, 1.0]: {score}")

    return score, reason_match.group(1).strip()


def assess_risk(main_agent: MainAgent, classification: str | None, area: str | None, description: str | None, severity: str | None, risk_threshold: float) -> RiskAssessment:
    prompt = _build_risk_assessment_prompt(classification, area, description, severity)
    result = main_agent.process(prompt, [])

    if result.status != "success":
        raise OrchestrationParseError(f"risk assessment did not produce a usable response: {result.text}")

    score, reason = _parse_risk_assessment_response(result.text)
    level: Literal["high", "low"] = "high" if score >= risk_threshold else "low"
    return RiskAssessment(score=score, level=level, reason=reason)


def construct_core_agents(base_config: BaseConfig) -> dict[str, Agent]:
    """The real, correctly-layered core-agent seam (see
    profiles/loader.py's docstring). Returns the Main Agent today;
    extended to the History and Insights Agents once §5.3/§6.9 exist.
    Called by whichever future startup code assembles the running system
    (§7/§9, not yet built) — never by profiles.loader, which may not
    import anything from orchestrator/.
    """

    return {"main_agent": MainAgent(model=base_config.main_agent_model)}
