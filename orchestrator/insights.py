"""The Insights Agent (work_plan.md §6.9).

A core agent, same pattern as `MainAgent`/`HistoryAgent`: loaded on every
run, model from the base configuration, zero tools — the strictest
reading of "read-only tools only" (same choice `agents/history.py` made
for the History Agent in Mission 5), since it concludes, it never acts.

`comparable_history` is meant to be the *same* `PrecedentMatch` tuple
§6.5's `orchestrator.precedent.look_up_precedent` already produced for
this event, not a second, separate history query — work_plan.md §9.20
names exactly this pair ("the two separate history reads per event — one
in precedent lookup and one in the Insights Agent's comparison") as
overlapping ground to merge. Built merged from the start rather than
needing that fix later.

Holding both halves — what each agent was asked and what it returned —
is what lets one conclusion distinguish a failed agent from one that was
asked the wrong question; both reach the prompt together, never just the
results. The conclusion itself is not delivered directly — `judge_success`
(§6.10) assesses it as an input, not an answer to accept — this module
returns a plain string, matching that function's existing `insight_text`
parameter exactly.

**The insight prompt has no structured response format to verify** —
free text is the whole point — but the framing itself (what context it's
given, in what shape) is still an unverified-against-a-live-model design
choice, same status as every other Main Agent decision in this mission.
"""

from typing import TYPE_CHECKING

from agents.base import Agent
from config.base import BaseConfig
from orchestrator.errors import OrchestrationParseError
from protocols.model import Protocol

if TYPE_CHECKING:
    from history.precedent import PrecedentMatch
    from protocols.retry import StepOutcome


class InsightsAgent(Agent):
    name = "insights_agent"
    role = (
        "Synthesizes the end of every protocol run: given what each sub-agent was asked and what it "
        "returned, plus comparable prior events, forms one conclusion setting this run against history. "
        "Concludes; does not act."
    )
    system_prompt = (
        "You are the Insights Agent. You are given the task text and result for every step of a "
        "protocol run, plus comparable prior events from the historical record. Hold both halves — "
        "the task and the result — together: this is what lets you distinguish an agent that failed "
        "from an agent that was asked the wrong question. Return one conclusion covering both the "
        "current run and how it compares to history, not two separate observations."
    )


def _build_insight_prompt(protocol: Protocol, step_outcomes: tuple["StepOutcome", ...], comparable_history: tuple["PrecedentMatch", ...]) -> str:
    steps_block = "\n".join(
        f"- {outcome.step.agent_name} was asked: {outcome.step.task_text!r}\n"
        f"  and {'succeeded' if outcome.succeeded else 'failed'}, returning: {outcome.result_text!r}"
        for outcome in step_outcomes
    )
    history_block = (
        "\n".join(
            f"- {p.occurred_at}: classification={p.classification}, protocol={p.protocol_name}, "
            f"outcome={p.outcome}, resolved={p.resolved}"
            for p in comparable_history
        )
        or "(no comparable prior events found)"
    )

    return (
        f"Form one conclusion about this run of the '{protocol.name}' protocol, setting it against "
        "comparable prior events — not two separate observations.\n\n"
        f"What happened in this run:\n{steps_block}\n\n"
        f"Comparable prior events:\n{history_block}"
    )


def build_insight(insights_agent: InsightsAgent, protocol: Protocol, step_outcomes: tuple["StepOutcome", ...], comparable_history: tuple["PrecedentMatch", ...] = ()) -> str:
    prompt = _build_insight_prompt(protocol, step_outcomes, comparable_history)
    result = insights_agent.process(prompt, [])

    if result.status != "success":
        raise OrchestrationParseError(f"insight generation did not produce a usable response: {result.text}")

    return result.text


def construct_core_agents(base_config: BaseConfig) -> dict[str, Agent]:
    return {"insights_agent": InsightsAgent(model=base_config.insights_agent_model)}
