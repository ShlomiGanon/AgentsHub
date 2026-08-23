"""Task formulation (work_plan.md §6.8).

One call writes a task for every participating agent at once — a call
per agent can't divide the work between them coherently, since none of
them would see what the others are being asked. Each step's
`allowed_tools` is the protocol's approved list filtered to what that
specific agent actually exposes, not the full approved list handed to
everyone — a protocol naming several agents may approve tools that belong
to different agents among them.

`precedent_context` defaults to `()` — the §6.5 seam (precedent lookup is
deferred, blocked on §5.8). `rewrite_task` matches
`protocols.executor.execute_steps`'s `task_rewriter: Callable[[Step, str],
str]` signature exactly once `main_agent` is bound (e.g.
`functools.partial(rewrite_task, main_agent)`), so it plugs in directly.

**Both response formats below are unverified prompt conventions**, same
status as Mission 3's `UNCLEAR_TASK:` sentinel.
"""

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from orchestrator.errors import OrchestrationParseError
from protocols.model import Protocol, Step

if TYPE_CHECKING:
    from agents.descriptor import AgentDescriptor
    from agents.registry import AgentRegistry
    from orchestrator.main_agent import MainAgent


@dataclass(frozen=True)
class FormulationResult:
    steps: tuple[Step, ...] = ()
    failed_agent_name: str | None = None
    failure_reason: str | None = None

    @property
    def success(self) -> bool:
        return self.failure_reason is None


_AGENT_TASK_PATTERN = re.compile(r"AGENT:\s*(\S+)\s*\n\s*TASK:\s*(.+?)(?=\nAGENT:|\Z)", re.IGNORECASE | re.DOTALL)


def _build_formulation_prompt(
    protocol: Protocol,
    descriptors: list["AgentDescriptor"],
    raw_text: str,
    classification: str | None,
    area: str | None,
    description: str | None,
    precedent_context: tuple,
) -> str:
    agents_block = "\n".join(f"- {d.name}: {d.role}" for d in descriptors)
    precedent_block = ""
    if precedent_context:
        precedent_block = "\nRelevant precedent (what was tried before and what came of it):\n" + "\n".join(str(p) for p in precedent_context) + "\n"

    return (
        f"Write a specific task for each agent participating in the '{protocol.name}' protocol, given this event. "
        "Each task should say what that agent in particular should determine or do — write for their role, not "
        "a generic instruction copied to everyone.\n\n"
        f"Event raw text: {raw_text}\n"
        f"Classification: {classification or '(unresolved)'}\n"
        f"Area: {area or '(unresolved)'}\n"
        f"Description: {description or '(none provided)'}\n"
        f"{precedent_block}\n"
        f"Participating agents:\n{agents_block}\n\n"
        "Respond with one block per agent, in exactly this format, in the same order as listed above:\n"
        "AGENT: <agent name>\nTASK: <the task for that agent>"
    )


def _parse_formulation_response(raw_text: str) -> dict[str, str]:
    return {match.group(1): match.group(2).strip() for match in _AGENT_TASK_PATTERN.finditer(raw_text)}


def formulate_tasks(
    main_agent: "MainAgent",
    protocol: Protocol,
    registry: "AgentRegistry",
    raw_text: str,
    classification: str | None,
    area: str | None,
    description: str | None,
    precedent_context: tuple = (),
) -> FormulationResult:
    descriptors = [registry.descriptor_for(name) for name in protocol.participating_agents]
    prompt = _build_formulation_prompt(protocol, descriptors, raw_text, classification, area, description, precedent_context)
    result = main_agent.process(prompt, [])

    if result.status != "success":
        return FormulationResult(failure_reason=f"formulation did not produce a usable response: {result.text}")

    tasks_by_agent = _parse_formulation_response(result.text)

    steps = []
    for descriptor in descriptors:
        task_text = tasks_by_agent.get(descriptor.name)
        if task_text is None:
            return FormulationResult(failed_agent_name=descriptor.name, failure_reason=f"model did not produce a task for '{descriptor.name}'")

        exposed_names = {tool.name for tool in descriptor.tools}
        allowed_tools = tuple(tool_name for tool_name in protocol.approved_tools if tool_name in exposed_names)
        steps.append(Step(agent_name=descriptor.name, task_text=task_text, allowed_tools=allowed_tools))

    return FormulationResult(steps=tuple(steps))


def _build_rewrite_prompt(step: Step, missing: str) -> str:
    return (
        f"The task below was given to agent '{step.agent_name}', who reported it unclear or unactionable, "
        f"stating what was missing: {missing}\n\n"
        f"Original task: {step.task_text}\n\n"
        "Rewrite the task to address exactly what's missing. Respond with only the rewritten task text, nothing else."
    )


def rewrite_task(main_agent: "MainAgent", step: Step, missing: str) -> str:
    prompt = _build_rewrite_prompt(step, missing)
    result = main_agent.process(prompt, [])

    if result.status != "success":
        raise OrchestrationParseError(f"task rewrite did not produce a usable response: {result.text}")

    return result.text.strip()
