"""The question flow (work_plan.md §6.12)."""

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from agents import HistoryAgent
from history.query import HistoryQueryError
from orchestrator.decisions import OrchestrationParseError
from tools import stage_context

if TYPE_CHECKING:
    from agents.runtime import AgentDescriptor
    from agents.registry import AgentRegistry
    from history.query import HistoryQueryService
    from orchestrator.decisions import MainAgent


_DIRECT_LOOKUP_PATTERN = re.compile(r"DIRECT_LOOKUP:\s*(\S+)", re.IGNORECASE)


def _build_direct_lookup_prompt(question: str) -> str:
    return (
        "Decide whether this question can be answered by directly looking up the single most recent "
        "event in the historical record — questions like \"what is the last event\", \"what just "
        "happened\", or \"what was the most recent report\" — as opposed to a question needing "
        "broader reasoning, filtering by area or classification, comparison across multiple events, "
        "or an agent-specific action.\n\n"
        f"Question: {question}\n\n"
        "If this is a direct \"most recent event\" lookup, respond in exactly this format, one line:\n"
        "DIRECT_LOOKUP: most_recent\n\n"
        "Otherwise, respond in exactly this format, one line:\n"
        "ROUTE: normal"
    )


def _is_direct_most_recent_lookup(raw_text: str) -> bool:
    return _DIRECT_LOOKUP_PATTERN.search(raw_text) is not None


@dataclass(frozen=True)
class AgentSelectionResult:
    status: Literal["selected", "none"]
    chosen_tasks: dict[str, str] = field(default_factory=dict)
    reason: str = ""


_AGENT_TASK_PATTERN = re.compile(r"AGENT:\s*(\S+)\s*\n\s*TASK:\s*(.+?)(?=\nAGENT:|\Z)", re.IGNORECASE | re.DOTALL)
_NONE_PATTERN = re.compile(r"NONE:\s*(.+)", re.IGNORECASE | re.DOTALL)


def _build_agent_selection_prompt(question: str, descriptors: list["AgentDescriptor"]) -> str:
    agents_block = "\n".join(f"- {d.name}: {d.role}" for d in descriptors)
    return (
        "Decide which of the following agents, if any, are needed to answer this question, and what "
        "to ask each. Send questions about the past to whichever agent handles history; send "
        "questions about current state to whichever agent can check it. You may choose more than one "
        "agent for a single question.\n\n"
        f"Question: {question}\n\n"
        f"Available agents:\n{agents_block}\n\n"
        "Respond with one block per agent you need, in exactly this format:\n"
        "AGENT: <agent name>\nTASK: <what to ask that agent>\n\n"
        "If none of these agents can genuinely answer this question — it doesn't match what any of "
        "them are for — do not force it onto the closest-sounding one; respond in exactly this "
        "format instead, one line:\n"
        "NONE: <why no agent can answer>"
    )


def _parse_agent_selection_response(raw_text: str) -> AgentSelectionResult:
    matches = list(_AGENT_TASK_PATTERN.finditer(raw_text))
    if matches:
        return AgentSelectionResult(status="selected", chosen_tasks={m.group(1): m.group(2).strip() for m in matches})

    none_match = _NONE_PATTERN.search(raw_text)
    if none_match:
        return AgentSelectionResult(status="none", reason=none_match.group(1).strip())

    raise OrchestrationParseError(f"question routing did not produce a usable response: {raw_text!r}")


def _build_compose_prompt(question: str, sub_answers: dict[str, str]) -> str:
    answers_block = "\n".join(f"- {name}: {text}" for name, text in sub_answers.items())
    return (
        f"Compose a single, coherent answer to this question from what each agent found — not a list "
        f"of separate replies.\n\nQuestion: {question}\n\nWhat each agent found:\n{answers_block}\n\n"
        "Respond with only the final composed answer, nothing else."
    )


def _cant_answer_reply(reason: str) -> str:
    reason = reason.strip()
    return f"I don't have a way to answer that.{' ' + reason if reason else ''}"


def answer_question(main_agent: "MainAgent", question: str, registry: "AgentRegistry", history_query_service: "HistoryQueryService") -> str:
    with stage_context("question_direct_lookup_classification"):
        lookup_result = main_agent.process(_build_direct_lookup_prompt(question), [])

    if lookup_result.status == "success" and _is_direct_most_recent_lookup(lookup_result.text):
        try:
            with stage_context("question_direct_lookup"):
                return history_query_service.answer_most_recent_event(question).answer
        except HistoryQueryError as exc:
            return _cant_answer_reply(str(exc))

    descriptors = [agent.descriptor for agent in registry.all()]
    with stage_context("question_routing"):
        selection_result = main_agent.process(_build_agent_selection_prompt(question, descriptors), [])

    if selection_result.status != "success":
        raise OrchestrationParseError(f"question routing did not produce a usable response: {selection_result.text}")

    selection = _parse_agent_selection_response(selection_result.text)
    if selection.status == "none":
        return _cant_answer_reply(selection.reason)

    chosen_tasks = selection.chosen_tasks

    sub_answers: dict[str, str] = {}
    for agent_name, task_text in chosen_tasks.items():
        agent = registry.get(agent_name)

        if isinstance(agent, HistoryAgent):
            try:
                with stage_context("question_history_query"):
                    sub_answers[agent_name] = history_query_service.query(task_text).answer
            except HistoryQueryError as exc:
                sub_answers[agent_name] = f"(no usable answer: {exc})"
            continue

        read_only_tools = [tool.name for tool in agent.exposed_tools() if not tool.side_effecting]
        with stage_context("question_subagent"):
            result = agent.process(task_text, read_only_tools)

        if result.status == "unclear_task" and len(chosen_tasks) == 1:
            return _cant_answer_reply(f"{agent_name} doesn't have a way to help with this question.")

        sub_answers[agent_name] = result.text if result.status == "success" else f"(no usable answer: {result.text})"

    if len(sub_answers) == 1:
        return next(iter(sub_answers.values()))

    with stage_context("question_composition"):
        compose_result = main_agent.process(_build_compose_prompt(question, sub_answers), [])
    if compose_result.status != "success":
        raise OrchestrationParseError(f"answer composition did not produce a usable response: {compose_result.text}")

    return compose_result.text
