"""The CrewAI adapter (work_plan.md §3.5, §3.6, §3.10)."""

import inspect
import json
import logging
from contextvars import ContextVar
from functools import wraps
from typing import Callable

from agents.contracts import (
    AgentDescriptor,
    AgentFrameworkNotReadyError,
    AgentInvocationError,
    AgentModelError,
    AgentOutputParseError,
    AgentResult,
    AgentTimeoutError,
    AgentToolConstructionError,
    ToolInfo,
    UNCLEAR_TASK_PROMPT_INSTRUCTION,
    exposed_tools_for,
    parse_agent_output,
    tool,
    tool_info_of,
)
from tools import get_current_stage, get_trace_id, log_ai_interaction, verbose_logging_enabled

logger = logging.getLogger(__name__)

_REQUIRED_CLASS_ATTRS = ("name", "role", "system_prompt")
_current_allowed_tools: ContextVar[frozenset | None] = ContextVar("current_allowed_tools", default=None)


def _wrap_tool(agent_name: str, bound_method: Callable, tool_info: ToolInfo) -> Callable:
    @wraps(bound_method)
    def _wrapped(*args, **kwargs):
        allowed = _current_allowed_tools.get()
        if allowed is None or tool_info.name not in allowed:
            logger.info(
                "tool call blocked: not in this call's allowed_tools",
                extra={"event": "tool_blocked", "agent": agent_name, "tool": tool_info.name, "trace_id": get_trace_id()},
            )
            return f"Tool '{tool_info.name}' is not permitted for this task."

        tool_result = bound_method(*args, **kwargs)
        logger.debug(
            "tool call",
            extra={"event": "tool_call", "agent": agent_name, "tool": tool_info.name, "trace_id": get_trace_id()},
        )
        return tool_result

    return _wrapped


class Agent:
    name: str = ""
    role: str = ""
    system_prompt: str = ""
    timeout_seconds: int = 60

    def __init__(self, model: str, api_key: str | None = None):
        missing = [attribute for attribute in _REQUIRED_CLASS_ATTRS if not getattr(type(self), attribute, "")]
        if missing:
            raise TypeError(f"{type(self).__name__} must set class-level {', '.join(missing)}")

        self.model = model
        self.api_key = api_key
        self._wrapped_tools: dict[str, Callable] = {}

        tool_infos = exposed_tools_for(self)
        for attribute_name in dir(type(self)):
            method = getattr(type(self), attribute_name, None)
            tool_info = tool_info_of(method)
            if tool_info is not None:
                self._wrapped_tools[tool_info.name] = _wrap_tool(self.name, getattr(self, attribute_name), tool_info)

        self.descriptor = AgentDescriptor(
            name=self.name,
            role=self.role,
            system_prompt=self.system_prompt,
            tools=tool_infos,
            model=model,
            api_key=api_key,
        )

    def exposed_tools(self) -> tuple[ToolInfo, ...]:
        return self.descriptor.tools

    def process(self, text: str, allowed_tools: list[str]) -> AgentResult:
        token = _current_allowed_tools.set(frozenset(allowed_tools))
        try:
            return parse_agent_output(invoke(self.descriptor, self._wrapped_tools, text, self.timeout_seconds))
        finally:
            _current_allowed_tools.reset(token)


def _get_crewai():
    try:
        import crewai
        import crewai.tools
        from crewai.events.utils.console_formatter import set_suppress_console_output
    except ImportError as exc:
        raise AgentFrameworkNotReadyError(
            "framework",
            "crewai is not installed in this environment yet — see requirements.txt",
            trace_id=get_trace_id(),
            cause=exc,
        ) from exc

    # CrewAI's console flag is context-local, so set it on the invocation thread.
    set_suppress_console_output(True)

    return crewai


def _build_crewai_tools(crewai_module, agent_name: str, wrapped_tools: dict[str, Callable], tool_infos: tuple[ToolInfo, ...]) -> list:
    base_tool_class = crewai_module.tools.BaseTool
    built = []

    for tool_info in tool_infos:
        wrapped = wrapped_tools[tool_info.name]

        def _run(self, *args, _wrapped=wrapped, **kwargs):
            return _wrapped(*args, **kwargs)

        # CrewAI derives tool schemas from this dynamic wrapper signature.
        _run.__signature__ = inspect.Signature(
            [inspect.Parameter("self", inspect.Parameter.POSITIONAL_OR_KEYWORD), *inspect.signature(wrapped).parameters.values()]
        )

        try:
            tool_class = type(
                f"_{tool_info.name}_tool",
                (base_tool_class,),
                {
                    "__annotations__": {"name": str, "description": str},
                    "name": tool_info.name,
                    "description": tool_info.description,
                    "_run": _run,
                },
            )
            built.append(tool_class())
        except Exception as exc:
            raise AgentToolConstructionError(
                agent_name, f"failed to build CrewAI tool '{tool_info.name}'", trace_id=get_trace_id(), cause=exc
            ) from exc

    return built


def invoke(descriptor: AgentDescriptor, wrapped_tools: dict[str, Callable], text: str, timeout_seconds: int) -> str:
    crewai_module = _get_crewai()
    crewai_tools = _build_crewai_tools(crewai_module, descriptor.name, wrapped_tools, descriptor.tools)

    backstory = f"{descriptor.system_prompt}\n\n{UNCLEAR_TASK_PROMPT_INSTRUCTION}"

    llm = crewai_module.LLM(model=descriptor.model, api_key=descriptor.api_key) if descriptor.api_key else descriptor.model

    crewai_agent = crewai_module.Agent(
        role=descriptor.role,
        goal="Complete the task given, or state clearly what is missing if it cannot be completed.",
        backstory=backstory,
        llm=llm,
        tools=crewai_tools,
        max_execution_time=timeout_seconds,
        verbose=False,
    )

    try:
        crewai_output = crewai_agent.kickoff(text)
    except TimeoutError as exc:
        raise AgentTimeoutError(
            descriptor.name, f"timed out after {timeout_seconds}s", trace_id=get_trace_id(), cause=exc
        ) from exc
    except Exception as exc:
        raise AgentModelError(descriptor.name, "the model call failed", trace_id=get_trace_id(), cause=exc) from exc

    raw_text = getattr(crewai_output, "raw", None)
    if raw_text is None:
        raise AgentOutputParseError(
            descriptor.name, f"could not extract text from CrewAI output: {crewai_output!r}", trace_id=get_trace_id()
        )

    if verbose_logging_enabled():
        interaction_payload = json.dumps(
            {
                "role": descriptor.role,
                "goal": "Complete the task given, or state clearly what is missing if it cannot be completed.",
                "backstory": backstory,
                "model": descriptor.model,
                "tools": [info.name for info in descriptor.tools],
                "kickoff_text": text,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        log_ai_interaction(descriptor.name, interaction_payload, raw_text, stage=get_current_stage(), trace_id=get_trace_id())

    return raw_text


class DuplicateAgentNameError(Exception):
    """Raised when two runtime agents share a registry name."""


class AgentRegistry:
    def __init__(self, agents: dict[str, Agent]):
        self._agents = agents

    def get(self, name: str) -> Agent:
        try:
            return self._agents[name]
        except KeyError:
            raise KeyError(f"no agent registered under '{name}'") from None

    def all(self) -> tuple[Agent, ...]:
        return tuple(self._agents.values())

    def descriptor_for(self, name: str) -> AgentDescriptor:
        return self.get(name).descriptor


def build_agent_registry(core_agents: dict[str, Agent], profile_agents: list[Agent]) -> AgentRegistry:
    agents: dict[str, Agent] = {}

    for agent in [*core_agents.values(), *profile_agents]:
        if agent.name in agents:
            raise DuplicateAgentNameError(f"agent name '{agent.name}' is registered more than once")
        agents[agent.name] = agent

    return AgentRegistry(agents)
