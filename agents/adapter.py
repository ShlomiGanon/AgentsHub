"""The CrewAI adapter (work_plan.md §3.5, §3.6, §3.10).

Constructs individual CrewAI Agents only — no Crew, no Task, no CrewAI
orchestration between agents. Orchestration lives in the protocol executor
(§4); a second orchestrator competing with it is exactly the failure this
decision avoids. `crewai.Agent.kickoff(text)` runs a single agent directly
with no Task/Crew object needed at all — confirmed against docs.crewai.com
(checked 2026-08-23, roughly the v1.13–1.15 series; see docs/progress.md's
§3.5 entry for what was verified and what wasn't).

`crewai` is now installed (1.15.17). `_get_crewai()` is the *only*
function that imports it, and the only function the automated test suite
monkeypatches (still fully mocked, unchanged) — everything else here is
real, structurally-accurate glue against the library's documented API. A
real, manual, non-mocked sanity check lives at
`tests/sanity_check_real_model_call.py` (not part of the automated suite).

Model routing (§3.6): every invocation routes to `descriptor.model`, read
fresh from the descriptor on this call — there is no shared client
configured once with a single model, so changing one agent's model can
never affect another.

Credentials: when `descriptor.api_key` is set (the normal case for any
agent constructed through `config.base.build_tier_model` — see
`docs/profile_spec.md`'s "Model tiers" section), it is passed explicitly
as `crewai.LLM(model=..., api_key=...)` — a real, documented crewai/
LiteLLM constructor, confirmed against docs.crewai.com/en/learn/llm-
connections and docs.litellm.ai. This is what lets two agents on the same
provider use two genuinely independent API keys: litellm's implicit,
provider-named env lookup (e.g. `OPENROUTER_API_KEY`) can only hold one
value process-wide and can't express that, for any provider — nothing
here ever branches on provider name to work around it. When
`descriptor.api_key` is `None` (an agent constructed the old way, with a
bare model string and no key), `llm=descriptor.model` is passed as a
plain string, unchanged from before `api_key` existed — litellm's
implicit env lookup still applies for that agent.

Timeout (§3.10): CrewAI's own `max_execution_time` constructor parameter
*is* the per-invocation timeout — no separate thread-based watchdog is
needed.
"""

import inspect
import json
from typing import Callable

from agents.descriptor import AgentDescriptor
from agents.errors import (
    AgentFrameworkNotReadyError,
    AgentModelError,
    AgentOutputParseError,
    AgentTimeoutError,
    AgentToolConstructionError,
)
from agents.results import UNCLEAR_TASK_PROMPT_INSTRUCTION
from agents.tooling import ToolInfo
from tools.logging_config import log_ai_interaction, verbose_logging_enabled
from tools.tracing import get_current_stage, get_trace_id


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

        # Built dynamically per tool, never derived from a docstring —
        # description always comes from our own ToolInfo (§3.3), not
        # CrewAI's docstring-inference convention. This dynamic type()
        # construction against a pydantic-based BaseTool was the
        # unverified-pending-real-crewai risk noted in docs/progress.md's
        # §3.10 entry — confirmed against the real, installed crewai
        # (1.15.17) to fail unconditionally without the explicit
        # `__annotations__` below: crewai.tools.BaseTool is a Pydantic v2
        # model with `name`/`description` declared as annotated fields, and
        # Pydantic v2 requires a subclass overriding an inherited field's
        # value to redeclare that field's type annotation too — a bare,
        # unannotated class attribute (what a plain `{"name": ...}` class
        # dict produces) is rejected at class-creation time with
        # `PydanticUserError`, before this tool (or any tool — this is
        # universal, not tool-specific) is ever instantiated. Supplying
        # `__annotations__` explicitly is what makes the override valid.
        # Still wrapped in a try/except so any other future construction
        # failure surfaces as a typed, logged error naming the offending
        # tool rather than a raw exception bypassing every other
        # error-translation path below.
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

    # An explicit api_key (config.base.build_tier_model's normal output)
    # is passed via a real crewai.LLM object, not folded into the model
    # string — the only documented way to give two agents on the same
    # provider two genuinely independent keys (see module docstring). No
    # api_key at all (legacy construction) keeps the old bare-string
    # behavior, relying on litellm's implicit env lookup as before.
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

    # This is the one real choke point every agent call passes through
    # (agents.base.Agent.process -> here), and the one place model I/O is
    # logged (work_plan.md §1.8 follow-up, debug-gated). Building
    # `interaction_payload` is real work over data that can be large (the
    # full original event/message text ends up in `kickoff_text`) — guard
    # it with an explicit check first, rather than build it unconditionally
    # and let `log_ai_interaction`'s own internal check discard it.
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
