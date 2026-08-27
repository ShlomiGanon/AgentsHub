"""The question flow (work_plan.md §6.12).

A question is answered by the same machinery as an event — one call
chooses which loaded agents are needed and what to ask each — with one
restriction: every tool passed to every chosen agent is filtered to
read-only first, whatever the question and whoever asked. The History
Agent needs no special-casing to be *chosen* for "about the past"
questions — it's simply one more agent in the registry the selection
prompt can pick, the same as any other — but it does need special-casing
once chosen: it must be asked through
`history.query.HistoryQueryService.query`, never through a bare
`agent.process()` call like every other agent here. `HistoryQueryService`
is the enforcement point for "never answer from memory" (§5.7) — it
retrieves persisted material first and gives the History Agent only that
retrieved context; calling `.process()` on it directly would hand it a
bare question with no retrieval step at all, defeating the entire reason
that service exists.

Two gaps found via live manual testing, both fixed here:

- The agent-selection prompt used to offer exactly one response shape
  (`AGENT:`/`TASK:` blocks), reused directly from
  `orchestrator.formulation._parse_formulation_response` — with no
  legitimate way to say "none of these agents can answer this," worse off
  than protocol selection's own pre-NO_MATCH state (which at least had two
  shapes). A question with no genuine match (e.g. "do I have any tasks?",
  which matches nothing any loaded agent's role covers) got forced onto
  the closest-sounding agent, which then correctly reported it couldn't
  act — but that raw, agent-internal confusion (e.g. "please specify a
  location") was surfaced verbatim as the final answer to someone who
  never mentioned a location. Fixed with this module's own dedicated
  `NONE:` response shape and parser (deliberately *not* reusing
  `orchestrator.formulation`'s — its "every named agent must get a task"
  rule is an unrelated failure mode that has no business being entangled
  with this one), plus routing a single-agent `unclear_task` result
  through the same clean "I don't have a way to answer that" presentation
  a true `NONE` selection gets.
- A "what is the last event"-shaped question could crash the whole request
  with a 422: given only agent *roles* (never any actual data) at
  selection time, the Main Agent could try to answer such a question
  inline instead of routing it, producing free text that matches neither
  `AGENT:`/`TASK:` nor `NONE:` and fails to parse. Adding `NONE:` alone
  doesn't fix this — it's not a clean decline, it's the model not using
  the structured format at all. Fixed with a narrow, structured
  classification step ahead of agent-selection (`DIRECT_LOOKUP:` vs.
  `ROUTE:`, the same shape of structured outcome NO_MATCH already proved
  out for protocol selection) that, when it recognizes a direct
  "most recent event" lookup, bypasses agent-selection entirely and calls
  `history_query_service.answer_most_recent_event` — the same
  direct-persistence-query pattern `orchestrator/precedent.py
  ::look_up_precedent` already uses in production, so retrieval never
  goes through a model at all; the History Agent still always does the
  interpreting, preserving "never answer from memory." Never raises on its
  own: an unparseable or ambiguous classification response falls through
  to the normal routing path unchanged, so this step can only ever remove
  a crash, never introduce a new one.

Writes nothing to persistence — a question is not an event.
"""

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from agents.history import HistoryAgent
from history.query import HistoryQueryError
from orchestrator.errors import OrchestrationParseError
from tools.tracing import stage_context

if TYPE_CHECKING:
    from agents.descriptor import AgentDescriptor
    from agents.registry import AgentRegistry
    from history.query import HistoryQueryService
    from orchestrator.main_agent import MainAgent


# -- Direct-lookup classification (bypasses agent-selection entirely when
# recognized) ---------------------------------------------------------------

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
    # Deliberately permissive in only one direction: recognizing the
    # DIRECT_LOOKUP line is enough to take the fast path; anything else —
    # a clean "ROUTE: normal", free text, an empty response — falls
    # through to the normal routing path unchanged. This step must never
    # raise on its own; it can only remove a crash (a question that used
    # to reach agent-selection's own parse failure), never add one.
    return _DIRECT_LOOKUP_PATTERN.search(raw_text) is not None


# -- Agent selection ---------------------------------------------------------


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
            # The one agent asked couldn't act on this — the same "forced
            # onto a wrong-fit agent" outcome NONE exists to prevent,
            # discovered one step later. Same clean presentation as a true
            # NONE selection; never the agent's own raw internal wording
            # (e.g. "please specify a location") verbatim, which reads as
            # confusing rather than clarifying to someone who asked about
            # something else entirely.
            return _cant_answer_reply(f"{agent_name} doesn't have a way to help with this question.")

        sub_answers[agent_name] = result.text if result.status == "success" else f"(no usable answer: {result.text})"

    if len(sub_answers) == 1:
        return next(iter(sub_answers.values()))

    with stage_context("question_composition"):
        compose_result = main_agent.process(_build_compose_prompt(question, sub_answers), [])
    if compose_result.status != "success":
        raise OrchestrationParseError(f"answer composition did not produce a usable response: {compose_result.text}")

    return compose_result.text
