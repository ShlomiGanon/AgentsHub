"""Success judgment (work_plan.md §6.10).

Compares the run's output against the protocol's declared success output
as a judgment of meaning, not a string match — the insight is an input to
be assessed, not an answer to be accepted (once §6.9's Insights Agent
exists; `insight_text` defaults to `""` until then, the §6.9 seam).

This function does not retry. When the judgment call itself fails (a
model error, unparseable output — `main_agent.process` raising or
returning something `_parse_judgment_response` can't parse), that's the
caller's cue to retry *only this call*, never the agents that already ran
and may have acted on the world — `judge_success` just does one call and
either returns a verdict or raises; the retry policy is an orchestration
decision that belongs to §6.11 (deferred), not here.

**The `VERDICT:`/`REASONING:` response format is an unverified prompt
convention**, same status as Mission 3's `UNCLEAR_TASK:` sentinel.
"""

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from orchestrator.errors import OrchestrationParseError
from protocols.model import Protocol
from tools.tracing import stage_context

if TYPE_CHECKING:
    from orchestrator.main_agent import MainAgent
    from protocols.retry import StepOutcome


@dataclass(frozen=True)
class SuccessVerdict:
    verdict: Literal["success", "failure", "uncertain"]
    reasoning: str


_VERDICT_PATTERN = re.compile(r"VERDICT:\s*(success|failure|uncertain)", re.IGNORECASE)
_REASONING_PATTERN = re.compile(r"REASONING:\s*(.+)", re.IGNORECASE | re.DOTALL)


def _build_judgment_prompt(protocol: Protocol, step_outcomes: tuple["StepOutcome", ...], insight_text: str) -> str:
    steps_block = "\n".join(
        f"- {outcome.step.agent_name} was asked: {outcome.step.task_text!r}\n"
        f"  and {'succeeded' if outcome.succeeded else 'failed'}, "
        f"returning: {outcome.result_text!r}"
        for outcome in step_outcomes
    )
    insight_block = f"\nInsight from comparing this run to history: {insight_text}\n" if insight_text else ""

    return (
        f"Judge whether this protocol run succeeded, given what success looks like for this protocol:\n"
        f"{protocol.expected_success_output}\n\n"
        f"What actually happened:\n{steps_block}\n"
        f"{insight_block}\n"
        "Compare the meaning of what happened against what success looks like — do not require exact "
        "wording. Respond in exactly this format, two lines:\n"
        "VERDICT: <success | failure | uncertain>\n"
        "REASONING: <why>"
    )


def _parse_judgment_response(raw_text: str) -> SuccessVerdict:
    verdict_match = _VERDICT_PATTERN.search(raw_text)
    reasoning_match = _REASONING_PATTERN.search(raw_text)

    if verdict_match is None or reasoning_match is None:
        raise OrchestrationParseError(f"could not parse success judgment response: {raw_text!r}")

    return SuccessVerdict(verdict=verdict_match.group(1).lower(), reasoning=reasoning_match.group(1).strip())


def judge_success(main_agent: "MainAgent", protocol: Protocol, step_outcomes: tuple["StepOutcome", ...], insight_text: str = "") -> SuccessVerdict:
    prompt = _build_judgment_prompt(protocol, step_outcomes, insight_text)
    with stage_context("success_judgment"):
        result = main_agent.process(prompt, [])

    if result.status != "success":
        raise OrchestrationParseError(f"success judgment did not produce a usable response: {result.text}")

    return _parse_judgment_response(result.text)
