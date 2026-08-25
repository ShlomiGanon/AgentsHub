"""The question flow (work_plan.md §6.12).

A question is answered by the same machinery as an event — one call
chooses which loaded agents are needed and what to ask each (the exact
`AGENT:`/`TASK:` format `orchestrator.formulation` already uses for task
formulation; reused directly, not reinvented, since the shape is
identical) — with one restriction: every tool passed to every chosen
agent is filtered to read-only first, whatever the question and whoever
asked. The History Agent needs no special-casing to be *chosen* for
"about the past" questions — it's simply one more agent in the registry
the selection prompt can pick, the same as any other — but it does need
special-casing once chosen: it must be asked through
`history.query.HistoryQueryService.query`, never through a bare
`agent.process()` call like every other agent here. `HistoryQueryService`
is the enforcement point for "never answer from memory" (§5.7) — it
retrieves persisted material first and gives the History Agent only that
retrieved context; calling `.process()` on it directly would hand it a
bare question with no retrieval step at all, defeating the entire reason
that service exists.

Writes nothing to persistence — a question is not an event.
"""

from typing import TYPE_CHECKING

from agents.history import HistoryAgent
from history.query import HistoryQueryError
from orchestrator.errors import OrchestrationParseError
from orchestrator.formulation import _parse_formulation_response
from tools.tracing import stage_context

if TYPE_CHECKING:
    from agents.descriptor import AgentDescriptor
    from agents.registry import AgentRegistry
    from history.query import HistoryQueryService
    from orchestrator.main_agent import MainAgent


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
        "AGENT: <agent name>\nTASK: <what to ask that agent>"
    )


def _build_compose_prompt(question: str, sub_answers: dict[str, str]) -> str:
    answers_block = "\n".join(f"- {name}: {text}" for name, text in sub_answers.items())
    return (
        f"Compose a single, coherent answer to this question from what each agent found — not a list "
        f"of separate replies.\n\nQuestion: {question}\n\nWhat each agent found:\n{answers_block}\n\n"
        "Respond with only the final composed answer, nothing else."
    )


def answer_question(main_agent: "MainAgent", question: str, registry: "AgentRegistry", history_query_service: "HistoryQueryService") -> str:
    descriptors = [agent.descriptor for agent in registry.all()]
    with stage_context("question_routing"):
        selection_result = main_agent.process(_build_agent_selection_prompt(question, descriptors), [])

    if selection_result.status != "success":
        raise OrchestrationParseError(f"question routing did not produce a usable response: {selection_result.text}")

    chosen_tasks = _parse_formulation_response(selection_result.text)
    if not chosen_tasks:
        raise OrchestrationParseError(f"no agent was chosen to answer the question: {selection_result.text!r}")

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
        sub_answers[agent_name] = result.text if result.status == "success" else f"(no usable answer: {result.text})"

    if len(sub_answers) == 1:
        return next(iter(sub_answers.values()))

    with stage_context("question_composition"):
        compose_result = main_agent.process(_build_compose_prompt(question, sub_answers), [])
    if compose_result.status != "success":
        raise OrchestrationParseError(f"answer composition did not produce a usable response: {compose_result.text}")

    return compose_result.text
