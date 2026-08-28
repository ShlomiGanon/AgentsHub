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


def _wrap_tool(agent_name: str, bound_method: Callable, info: ToolInfo) -> Callable:
    @wraps(bound_method)
    def _wrapped(*args, **kwargs):
        allowed = _current_allowed_tools.get()
        if allowed is None or info.name not in allowed:
            logger.info(
                "tool call blocked: not in this call's allowed_tools",
                extra={"event": "tool_blocked", "agent": agent_name, "tool": info.name, "trace_id": get_trace_id()},
            )
            return f"Tool '{info.name}' is not permitted for this task."

        result = bound_method(*args, **kwargs)
        logger.debug(
            "tool call",
            extra={"event": "tool_call", "agent": agent_name, "tool": info.name, "trace_id": get_trace_id()},
        )
        return result

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
            info = tool_info_of(method)
            if info is not None:
                self._wrapped_tools[info.name] = _wrap_tool(self.name, getattr(self, attribute_name), info)

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

    # Display only — nothing about invocation changes below. crewai's own
    # "🤖 LiteAgent Started/Completed" panels are printed by a process-wide
    # `EventListener` singleton whose `ConsoleFormatter` is constructed with
    # `verbose=True` unconditionally (crewai/events/event_listener.py) —
    # independent of, and not affected by, the `verbose` flag on any
    # individual `Agent` instance (see `invoke()` below, which sets its own
    # to False for the same reason but can't reach this). This is crewai's
    # own supported switch for that output (crewai_core.printer, re-exported
    # here), not a log level — the panels are Rich console prints, never
    # routed through Python's `logging` module, so there is no logger to
    # raise the level on instead.
    #
    # It's a contextvars.ContextVar, which does not propagate across a
    # plain `threading.Thread` boundary (confirmed empirically) — setting it
    # once at process startup would not reach `orchestrator.queue
    # .SerialEventQueue`'s dedicated worker thread, where every real
    # `kickoff()` call actually runs. Setting it here instead, inside the one
    # function every `invoke()` call goes through immediately before that
    # `kickoff()` call, guarantees it's set on whichever thread is about to
    # need it, every time.
    set_suppress_console_output(True)

    return crewai


def _build_crewai_tools(crewai_module, agent_name: str, wrapped_tools: dict[str, Callable], tool_infos: tuple[ToolInfo, ...]) -> list:
    base_tool_class = crewai_module.tools.BaseTool
    built = []

    for info in tool_infos:
        wrapped = wrapped_tools[info.name]

        def _run(self, *args, _wrapped=wrapped, **kwargs):
            return _wrapped(*args, **kwargs)

        # CrewAI's BaseTool auto-generates args_schema — the function-
        # calling schema the model actually sees and is validated against
        # (crewai.tools.base_tool.BaseTool._default_args_schema) — by
        # introspecting *this* _run's own signature via `inspect.signature`,
        # never the wrapped tool method's. Left alone, that signature is
        # `(self, *args, _wrapped=..., **kwargs)`: `*args`/`**kwargs` are
        # explicitly skipped by that introspection, and the one named
        # parameter it would otherwise see, `_wrapped`, is silently dropped
        # by Pydantic's `create_model` for its leading underscore — the net
        # result, confirmed live, is a tool schema with zero parameters, so
        # a tool like check_status(location) is invisible to the model as
        # taking any argument at all, and a real call fails with "missing
        # required positional argument: 'location'".
        #
        # Fixed by overriding what `inspect.signature()` reports for this
        # exact function object — a standard, supported mechanism (the same
        # one `functools.partial` and `inspect.Signature.from_callable`
        # itself rely on: an explicit `__signature__` attribute takes
        # priority over introspecting `__code__`) — to the real tool
        # method's own signature. That's already recoverable accurately:
        # `agents.base._wrap_tool` builds `wrapped` with
        # `@functools.wraps(bound_method)`, and `inspect.signature(wrapped)`
        # follows the resulting `__wrapped__` chain to `bound_method` (the
        # real, bound tool method — `self` already applied), giving back
        # its exact parameters, annotations, and defaults. This changes
        # nothing about how `_run` actually executes — the call below still
        # forwards through the generic `*args, **kwargs` unchanged, and
        # CrewAI's own `BaseTool.run()` calls `_run(*args, **kwargs)` with
        # whatever was validated against `args_schema`, which now lands on
        # the right keyword names — it only changes what CrewAI's
        # schema-builder sees when deciding what parameters to expose.
        _run.__signature__ = inspect.Signature(
            [inspect.Parameter("self", inspect.Parameter.POSITIONAL_OR_KEYWORD), *inspect.signature(wrapped).parameters.values()]
        )

        try:
            tool_class = type(
                f"_{info.name}_tool",
                (base_tool_class,),
                {
                    "__annotations__": {"name": str, "description": str},
                    "name": info.name,
                    "description": info.description,
                    "_run": _run,
                },
            )
            built.append(tool_class())
        except Exception as exc:
            raise AgentToolConstructionError(
                agent_name, f"failed to build CrewAI tool '{info.name}'", trace_id=get_trace_id(), cause=exc
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
