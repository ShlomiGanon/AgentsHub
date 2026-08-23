"""The CrewAI adapter (work_plan.md §3.5, §3.6, §3.10).

Constructs individual CrewAI Agents only — no Crew, no Task, no CrewAI
orchestration between agents. Orchestration lives in the protocol executor
(§4); a second orchestrator competing with it is exactly the failure this
decision avoids. `crewai.Agent.kickoff(text)` runs a single agent directly
with no Task/Crew object needed at all — confirmed against docs.crewai.com
(checked 2026-08-23, roughly the v1.13–1.15 series; see docs/progress.md's
§3.5 entry for what was verified and what wasn't).

`crewai` is not installed in this environment yet (confirmed with the
user). `_get_crewai()` is the *only* function that imports it, and the
only function tests monkeypatch — everything else here is real,
structurally-accurate glue against the library's documented API, built and
tested through a fake standing in for the real module.

Model routing (§3.6): every invocation routes to `descriptor.model`, read
fresh from the descriptor on this call — there is no shared client
configured once with a single model, so changing one agent's model can
never affect another. Credentials aren't read here at all: litellm reads
provider environment variables directly from the process environment,
which `profiles.loader` already populated at load time (Mission 1) —
nothing further is needed for §3.6's credential-resolution requirement.

Timeout (§3.10): CrewAI's own `max_execution_time` constructor parameter
*is* the per-invocation timeout — no separate thread-based watchdog is
needed.
"""

from typing import Callable

from agents.descriptor import AgentDescriptor
from agents.errors import AgentFrameworkNotReadyError, AgentModelError, AgentOutputParseError, AgentTimeoutError
from agents.results import UNCLEAR_TASK_PROMPT_INSTRUCTION
from agents.tooling import ToolInfo
from tools.tracing import get_trace_id


def _get_crewai():
    try:
        import crewai
        import crewai.tools
    except ImportError as exc:
        raise AgentFrameworkNotReadyError(
            "framework",
            "crewai is not installed in this environment yet — see requirements.txt",
            trace_id=get_trace_id(),
            cause=exc,
        ) from exc

    return crewai


def _build_crewai_tools(crewai_module, wrapped_tools: dict[str, Callable], tool_infos: tuple[ToolInfo, ...]) -> list:
    base_tool_class = crewai_module.tools.BaseTool
    built = []

    for info in tool_infos:
        wrapped = wrapped_tools[info.name]

        def _run(self, *args, _wrapped=wrapped, **kwargs):
            return _wrapped(*args, **kwargs)

        # Built dynamically per tool, never derived from a docstring —
        # description always comes from our own ToolInfo (§3.3), not
        # CrewAI's docstring-inference convention.
        tool_class = type(
            f"_{info.name}_tool",
            (base_tool_class,),
            {"name": info.name, "description": info.description, "_run": _run},
        )
        built.append(tool_class())

    return built


def invoke(descriptor: AgentDescriptor, wrapped_tools: dict[str, Callable], text: str, timeout_seconds: int) -> str:
    crewai_module = _get_crewai()
    crewai_tools = _build_crewai_tools(crewai_module, wrapped_tools, descriptor.tools)

    backstory = f"{descriptor.system_prompt}\n\n{UNCLEAR_TASK_PROMPT_INSTRUCTION}"

    crewai_agent = crewai_module.Agent(
        role=descriptor.role,
        goal="Complete the task given, or state clearly what is missing if it cannot be completed.",
        backstory=backstory,
        llm=descriptor.model,
        tools=crewai_tools,
        max_execution_time=timeout_seconds,
    )

    try:
        output = crewai_agent.kickoff(text)
    except TimeoutError as exc:
        raise AgentTimeoutError(
            descriptor.name, f"timed out after {timeout_seconds}s", trace_id=get_trace_id(), cause=exc
        ) from exc
    except Exception as exc:
        raise AgentModelError(descriptor.name, "the model call failed", trace_id=get_trace_id(), cause=exc) from exc

    raw_text = getattr(output, "raw", None)
    if raw_text is None:
        raise AgentOutputParseError(
            descriptor.name, f"could not extract text from CrewAI output: {output!r}", trace_id=get_trace_id()
        )

    return raw_text
