"""The CrewAI adapter (work_plan.md §3.5, §3.6, §3.10)."""

import inspect
import json
import logging
import threading
import time
from contextvars import ContextVar
from functools import lru_cache, wraps
from typing import Callable

from agents.contracts import (
    AgentDescriptor,
    AgentFrameworkNotReadyError,
    AgentInvocationError,
    InvocationPolicy,
    provider_capabilities,
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
_invocation_deadline: ContextVar[float | None] = ContextVar("invocation_deadline", default=None)
_tool_class_cache: dict[tuple[type, str, str, int], type] = {}
_tool_class_cache_lock = threading.Lock()
_provider_semaphore = threading.BoundedSemaphore(8)
_structured_output_mode = "off"


def configure_provider_concurrency(limit: int) -> None:
    global _provider_semaphore
    if not 1 <= limit <= 64:
        raise ValueError("provider concurrency must be between 1 and 64")
    _provider_semaphore = threading.BoundedSemaphore(limit)


def configure_structured_output_mode(mode: str) -> None:
    global _structured_output_mode
    if mode not in {"off", "auto", "required"}:
        raise ValueError("structured output mode must be off, auto, or required")
    _structured_output_mode = mode


def set_invocation_deadline(deadline_monotonic: float | None) -> None:
    _invocation_deadline.set(deadline_monotonic)


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

        started = time.monotonic()
        try:
            tool_result = bound_method(*args, **kwargs)
        except Exception:
            logger.exception(
                "tool call failed",
                extra={
                    "event": "tool_call",
                    "agent": agent_name,
                    "tool": tool_info.name,
                    "status": "error",
                    "duration_seconds": time.monotonic() - started,
                    "trace_id": get_trace_id(),
                },
            )
            raise
        logger.debug(
            "tool call",
            extra={
                "event": "tool_call",
                "agent": agent_name,
                "tool": tool_info.name,
                "status": "success",
                "duration_seconds": time.monotonic() - started,
                "trace_id": get_trace_id(),
            },
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

    def process(self, text: str, allowed_tools: list[str], *, invocation_policy: InvocationPolicy | None = None) -> AgentResult:
        token = _current_allowed_tools.set(frozenset(allowed_tools))
        try:
            if invocation_policy is None:
                raw_text = invoke(self.descriptor, self._wrapped_tools, text, self.timeout_seconds)
            else:
                raw_text = invoke(self.descriptor, self._wrapped_tools, text, self.timeout_seconds, invocation_policy)
            return parse_agent_output(raw_text)
        finally:
            _current_allowed_tools.reset(token)


@lru_cache(maxsize=1)
def _import_crewai():
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


def _get_crewai():
    crewai = _import_crewai()
    from crewai.events.utils.console_formatter import set_suppress_console_output

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
            cache_key = (base_tool_class, agent_name, tool_info.name, id(wrapped))
            with _tool_class_cache_lock:
                tool_class = _tool_class_cache.get(cache_key)
                if tool_class is None:
                    tool_class = type(
                        f"_{agent_name}_{tool_info.name}_tool",
                        (base_tool_class,),
                        {
                            "__annotations__": {"name": str, "description": str},
                            "name": tool_info.name,
                            "description": tool_info.description,
                            "_run": _run,
                        },
                    )
                    _tool_class_cache[cache_key] = tool_class
            built.append(tool_class())
        except Exception as exc:
            raise AgentToolConstructionError(
                agent_name, f"failed to build CrewAI tool '{tool_info.name}'", trace_id=get_trace_id(), cause=exc
            ) from exc

    return built


def invoke(
    descriptor: AgentDescriptor,
    wrapped_tools: dict[str, Callable],
    text: str,
    timeout_seconds: int,
    invocation_policy: InvocationPolicy | None = None,
) -> str:
    setup_started = time.monotonic()
    crewai_module = _get_crewai()
    imported_at = time.monotonic()
    crewai_tools = _build_crewai_tools(crewai_module, descriptor.name, wrapped_tools, descriptor.tools)
    tools_built_at = time.monotonic()

    backstory = f"{descriptor.system_prompt}\n\n{UNCLEAR_TASK_PROMPT_INSTRUCTION}"

    llm_options = {"model": descriptor.model}
    if descriptor.api_key:
        llm_options["api_key"] = descriptor.api_key
    if invocation_policy is not None and invocation_policy.max_output_tokens is not None:
        llm_options["max_tokens"] = invocation_policy.max_output_tokens
    if invocation_policy is not None and invocation_policy.reasoning_effort != "none":
        llm_options["reasoning_effort"] = invocation_policy.reasoning_effort
    if (
        invocation_policy is not None
        and invocation_policy.response_schema is not None
        and _structured_output_mode != "off"
    ):
        capabilities = provider_capabilities(descriptor.model)
        if capabilities.strict_json_schema:
            schema_name = str(invocation_policy.response_schema.get("name", "agentshub_output"))
            schema = invocation_policy.response_schema.get("schema", invocation_policy.response_schema)
            llm_options["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": schema_name, "strict": True, "schema": schema},
            }
        elif _structured_output_mode == "required":
            raise AgentModelError(
                descriptor.name,
                f"provider for {descriptor.model!r} does not support strict structured output",
                trace_id=get_trace_id(),
            )
    llm = crewai_module.LLM(**llm_options) if descriptor.api_key or invocation_policy is not None else descriptor.model
    llm_built_at = time.monotonic()

    effective_timeout = timeout_seconds
    if invocation_policy is not None and invocation_policy.timeout_seconds is not None:
        effective_timeout = min(timeout_seconds, invocation_policy.timeout_seconds)
    request_deadline = _invocation_deadline.get()
    if request_deadline is not None:
        remaining_seconds = request_deadline - time.monotonic()
        if remaining_seconds <= 0:
            raise AgentTimeoutError(
                descriptor.name, "shared request deadline was exhausted before invocation", trace_id=get_trace_id()
            )
        effective_timeout = min(effective_timeout, remaining_seconds)

    crewai_agent = crewai_module.Agent(
        role=descriptor.role,
        goal="Complete the task given, or state clearly what is missing if it cannot be completed.",
        backstory=backstory,
        llm=llm,
        tools=crewai_tools,
        max_execution_time=effective_timeout,
        verbose=False,
    )
    agent_built_at = time.monotonic()

    try:
        acquired = _provider_semaphore.acquire(timeout=effective_timeout)
        if not acquired:
            raise TimeoutError("provider concurrency wait exceeded the invocation timeout")
        try:
            crewai_output = crewai_agent.kickoff(text)
        finally:
            _provider_semaphore.release()
    except TimeoutError as exc:
        raise AgentTimeoutError(
            descriptor.name, f"timed out after {effective_timeout}s", trace_id=get_trace_id(), cause=exc
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

    usage = getattr(crewai_output, "token_usage", None)
    def _usage_value(*names: str):
        for name in names:
            value = getattr(usage, name, None)
            if value is not None:
                return value
            if isinstance(usage, dict) and name in usage:
                return usage[name]
        return None

    logger.info(
        "model invocation finished",
        extra={
            "event": "model_invocation_finished",
            "agent": descriptor.name,
            "model": descriptor.model,
            "provider": descriptor.model.split("/", 1)[0],
            "stage": get_current_stage(),
            "attempt": 1,
            "timeout_seconds": effective_timeout,
            "ttft_seconds": getattr(crewai_output, "ttft_seconds", None),
            "input_tokens": _usage_value("prompt_tokens", "input_tokens"),
            "output_tokens": _usage_value("completion_tokens", "output_tokens"),
            "cache_tokens": _usage_value("cached_tokens", "cache_read_tokens"),
            "trace_id": get_trace_id(),
            "runtime_import_seconds": imported_at - setup_started,
            "runtime_tools_seconds": tools_built_at - imported_at,
            "runtime_llm_seconds": llm_built_at - tools_built_at,
            "runtime_agent_seconds": agent_built_at - llm_built_at,
            "runtime_kickoff_seconds": time.monotonic() - agent_built_at,
            "telemetry_only": True,
        },
    )
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
