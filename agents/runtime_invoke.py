"""CrewAI kickoff, tool wrapping, and agent-pool helpers for one invocation.

Public callers still go through `agents.runtime.invoke` and the package facade.
"""

from __future__ import annotations

import inspect
import json
import logging
import threading
import time
from collections import OrderedDict
from contextvars import ContextVar
from typing import Callable

from agents.contracts import (
    AgentDescriptor,
    AgentModelError,
    AgentOutputParseError,
    AgentTimeoutError,
    AgentToolConstructionError,
    AgentWarmupError,
    InvocationPolicy,
    ToolInfo,
    UNCLEAR_TASK_PROMPT_INSTRUCTION,
)
from agents.invocation_context import current_invocation_id
from agents.provider_telemetry import track_provider_finish_reasons
from agents.runtime_llm import _build_or_reuse_llm, _llm_options
from tools import deep_debug_enabled, get_current_stage, get_trace_id, log_ai_interaction, stage_context, trace_context
from tools.log_events import (
    model_invocation_finished,
    model_warmup_finished,
    model_warmup_started,
)

logger = logging.getLogger(__name__)

_tool_class_cache: dict[tuple[type, str, str, int], type] = {}
_tool_class_cache_lock = threading.Lock()
_AGENT_CACHE_MAX_SIZE = 32
_agent_pool: "OrderedDict[tuple, list]" = OrderedDict()
_agent_pool_lock = threading.Lock()
_AGENT_POOL_PER_KEY = 4
_provider_semaphore = threading.BoundedSemaphore(8)
_max_iter = 8
_model_timeout_seconds = 30.0
_invocation_deadline: ContextVar[float | None] = ContextVar("invocation_deadline", default=None)


def _runtime_get_crewai():
    """Call `agents.runtime._get_crewai` so test monkeypatches on that module apply."""
    from agents import runtime
    return runtime._get_crewai()


def configure_provider_concurrency(limit: int) -> None:
    """Cap how many provider calls may run at once in this process."""

    global _provider_semaphore
    if not 1 <= limit <= 64:
        raise ValueError("provider concurrency must be between 1 and 64")
    _provider_semaphore = threading.BoundedSemaphore(limit)


def configure_invocation_limits(max_iter: int, model_timeout_seconds: float) -> None:
    """Set the profile-owned CrewAI iteration cap and provider timeout."""

    global _max_iter, _model_timeout_seconds
    if type(max_iter) is not int or not 1 <= max_iter <= 100:
        raise ValueError("max_iter must be an integer between 1 and 100")
    if not 0 < float(model_timeout_seconds) <= 600:
        raise ValueError("model_timeout_seconds must be between 0 and 600")
    _max_iter = max_iter
    _model_timeout_seconds = float(model_timeout_seconds)


def set_invocation_deadline(deadline_monotonic: float | None) -> None:
    """Bind the remaining shared request deadline for the current invocation."""

    _invocation_deadline.set(deadline_monotonic)


def _agent_cache_key(
    descriptor: AgentDescriptor,
    tool_names: tuple[str, ...],
    crewai_timeout_seconds: int,
    invocation_policy: InvocationPolicy | None,
    llm: object,
) -> tuple:
    """Identity for one reusable CrewAI agent, including the LLM instance id."""

    policy_key = ()
    if invocation_policy is not None:
        schema = invocation_policy.response_schema
        policy_key = (
            invocation_policy.max_output_tokens,
            invocation_policy.timeout_seconds,
            invocation_policy.reasoning_effort,
            json.dumps(schema, sort_keys=True, default=str) if schema is not None else None,
        )
    return (
        descriptor.name,
        descriptor.model,
        tool_names,
        crewai_timeout_seconds,
        policy_key,
        id(llm),
    )


def _checkout_crewai_agent(cache_key: tuple, factory):
    """Take a pooled CrewAI agent or build a new one when the pool is empty."""

    with _agent_pool_lock:
        pool = _agent_pool.get(cache_key)
        if pool:
            agent = pool.pop()
            _agent_pool.move_to_end(cache_key)
            return agent, True
    return factory(), False


def _checkin_crewai_agent(cache_key: tuple, agent) -> None:
    """Return a CrewAI agent to the bounded pool, evicting the oldest key if full."""

    with _agent_pool_lock:
        pool = _agent_pool.setdefault(cache_key, [])
        if len(pool) < _AGENT_POOL_PER_KEY:
            pool.append(agent)
        _agent_pool.move_to_end(cache_key)
        while len(_agent_pool) > _AGENT_CACHE_MAX_SIZE:
            _agent_pool.popitem(last=False)


def _clear_agent_cache() -> None:
    """Drop pooled CrewAI agents; used by tests and process teardown."""

    with _agent_pool_lock:
        _agent_pool.clear()


def initialize_agent_runtime(agents: tuple["Agent", ...] | list["Agent"]) -> tuple[str, ...]:
    """Import CrewAI and verify each unique configured provider/model.

    The verification is one real, deterministic, tool-free request per model.
    It is called before queue workers and the HTTP listener start. Secrets are
    never included in the returned identifiers, logs, or raised message.
    """

    crewai_module = _runtime_get_crewai()
    unique_descriptors: dict[str, AgentDescriptor] = {}
    for agent in agents:
        unique_descriptors.setdefault(agent.descriptor.model, agent.descriptor)

    warmed_models: list[str] = []
    with trace_context() as startup_trace_id:
        for model, descriptor in unique_descriptors.items():
            provider = model.split("/", 1)[0]
            started = time.monotonic()
            model_warmup_started(provider=provider, model=model)
            try:
                with stage_context("warmup"):
                    warmup_options = _llm_options(descriptor, timeout_seconds=_model_timeout_seconds)
                    warmup_options.update({"max_tokens": 8, "temperature": 0})
                    llm = _build_or_reuse_llm(crewai_module, descriptor, warmup_options)
                    response = llm.call([{"role": "user", "content": "Reply with OK."}])
                if not isinstance(response, str) or not response.strip():
                    raise ValueError("provider returned an empty or non-text warmup response")
            except Exception as exc:
                model_warmup_finished(
                    provider=provider,
                    model=model,
                    status="error",
                    termination_reason=type(exc).__name__,
                    latency_ms=round((time.monotonic() - started) * 1000, 3),
                    level=logging.ERROR,
                )
                raise AgentWarmupError(
                    "runtime",
                    f"startup verification failed for configured model {model!r}",
                    trace_id=startup_trace_id,
                    cause=exc,
                ) from exc
            model_warmup_finished(
                provider=provider,
                model=model,
                status="success",
                termination_reason="completed",
                latency_ms=round((time.monotonic() - started) * 1000, 3),
            )
            warmed_models.append(model)
    return tuple(warmed_models)


def _build_crewai_tools(crewai_module, agent_name: str, wrapped_tools: dict[str, Callable], tool_infos: tuple[ToolInfo, ...]) -> list:
    """Build CrewAI BaseTool subclasses that forward to already-wrapped Python tools."""

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
    """Run one CrewAI kickoff for this descriptor and return the captured result text."""

    setup_started = time.monotonic()
    crewai_module = _runtime_get_crewai()
    imported_at = time.monotonic()
    crewai_tools = _build_crewai_tools(crewai_module, descriptor.name, wrapped_tools, descriptor.tools)
    tools_built_at = time.monotonic()

    backstory = f"{descriptor.system_prompt}\n\n{UNCLEAR_TASK_PROMPT_INSTRUCTION}"

    effective_timeout = timeout_seconds
    effective_timeout = min(effective_timeout, _model_timeout_seconds)
    if invocation_policy is not None and invocation_policy.timeout_seconds is not None:
        effective_timeout = min(effective_timeout, invocation_policy.timeout_seconds)
    request_deadline = _invocation_deadline.get()
    if request_deadline is not None:
        remaining_seconds = request_deadline - time.monotonic()
        if remaining_seconds <= 0:
            raise AgentTimeoutError(
                descriptor.name, "shared request deadline was exhausted before invocation", trace_id=get_trace_id()
            )
        effective_timeout = min(effective_timeout, remaining_seconds)

    # CrewAI validates max_execution_time as an integer. Keep the precise
    # floating-point timeout for deadline and semaphore accounting, but give
    # CrewAI a whole number that never exceeds the remaining budget.
    if effective_timeout < 1:
        raise AgentTimeoutError(
            descriptor.name,
            "less than one second remains before the invocation deadline",
            trace_id=get_trace_id(),
        )
    crewai_timeout_seconds = int(effective_timeout)

    llm_options = _llm_options(
        descriptor,
        timeout_seconds=_model_timeout_seconds,
        invocation_policy=invocation_policy,
    )
    llm = _build_or_reuse_llm(crewai_module, descriptor, llm_options)
    llm_built_at = time.monotonic()

    cache_key = _agent_cache_key(
        descriptor,
        tuple(sorted(wrapped_tools)),
        crewai_timeout_seconds,
        invocation_policy,
        llm,
    )

    def _build_crewai_agent():
        return crewai_module.Agent(
            role=descriptor.role,
            goal="Complete the task given, or state clearly what is missing if it cannot be completed.",
            backstory=backstory,
            llm=llm,
            tools=crewai_tools,
            max_iter=_max_iter,
            max_retry_limit=0,
            max_execution_time=crewai_timeout_seconds,
            verbose=False,
        )

    crewai_agent, _cache_hit = _checkout_crewai_agent(cache_key, _build_crewai_agent)
    agent_built_at = time.monotonic()

    try:
        return _run_crewai_kickoff(
            descriptor,
            wrapped_tools,
            text,
            crewai_agent,
            backstory,
            effective_timeout,
            imported_at,
            setup_started,
            tools_built_at,
            llm_built_at,
            agent_built_at,
        )
    finally:
        _checkin_crewai_agent(cache_key, crewai_agent)


def _run_crewai_kickoff(
    descriptor,
    wrapped_tools,
    text,
    crewai_agent,
    backstory,
    effective_timeout,
    imported_at,
    setup_started,
    tools_built_at,
    llm_built_at,
    agent_built_at,
) -> str:
    """Kick off one CrewAI agent and map timeouts, cutoffs, and usage onto runtime errors."""

    invocation_started_at = time.monotonic()
    try:
        acquired = _provider_semaphore.acquire(timeout=effective_timeout)
        if not acquired:
            raise TimeoutError("provider concurrency wait exceeded the invocation timeout")
        try:
            with track_provider_finish_reasons() as finish_reasons:
                crewai_output = crewai_agent.kickoff(text)
        finally:
            _provider_semaphore.release()
    except TimeoutError as exc:
        model_invocation_finished(
            agent=descriptor.name,
            invocation_id=current_invocation_id(),
            model=descriptor.model,
            provider=descriptor.model.split("/", 1)[0],
            status="error",
            termination_reason="timeout",
            timeout_seconds=effective_timeout,
            latency_ms=round((time.monotonic() - invocation_started_at) * 1000, 3),
        )
        raise AgentTimeoutError(
            descriptor.name, f"timed out after {effective_timeout}s", trace_id=get_trace_id(), cause=exc
        ) from exc
    except Exception as exc:
        model_invocation_finished(
            agent=descriptor.name,
            invocation_id=current_invocation_id(),
            model=descriptor.model,
            provider=descriptor.model.split("/", 1)[0],
            status="error",
            termination_reason=type(exc).__name__,
            timeout_seconds=effective_timeout,
            latency_ms=round((time.monotonic() - invocation_started_at) * 1000, 3),
        )
        raise AgentModelError(descriptor.name, "the model call failed", trace_id=get_trace_id(), cause=exc) from exc

    # A write-capable specialist may already have committed its tool result.
    # Do not turn that verified write into an apparent failed action merely
    # because CrewAI's final prose was cut short. The read-only answer paths
    # can safely reject incomplete text and use their existing fallback.
    has_write_tool = any(info.side_effecting for info in descriptor.tools if info.name in wrapped_tools)
    if finish_reasons and finish_reasons[-1] == "length" and not has_write_tool:
        model_invocation_finished(
            agent=descriptor.name,
            invocation_id=current_invocation_id(),
            model=descriptor.model,
            provider=descriptor.model.split("/", 1)[0],
            status="error",
            termination_reason="length",
            latency_ms=round((time.monotonic() - invocation_started_at) * 1000, 3),
        )
        raise AgentOutputParseError(
            descriptor.name, "the model's final response was cut off at its output limit", trace_id=get_trace_id()
        )

    raw_text = getattr(crewai_output, "raw", None)
    if raw_text is None:
        raise AgentOutputParseError(
            descriptor.name, f"could not extract text from CrewAI output: {crewai_output!r}", trace_id=get_trace_id()
        )

    if deep_debug_enabled():
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
        """Read one token-usage field from either an object or a dict."""

        for name in names:
            value = getattr(usage, name, None)
            if value is not None:
                return value
            if isinstance(usage, dict) and name in usage:
                return usage[name]
        return None

    model_invocation_finished(
        agent=descriptor.name,
        invocation_id=current_invocation_id(),
        model=descriptor.model,
        provider=descriptor.model.split("/", 1)[0],
        status="success",
        termination_reason="completed",
        timeout_seconds=effective_timeout,
        ttft_seconds=getattr(crewai_output, "ttft_seconds", None),
        input_tokens=_usage_value("prompt_tokens", "input_tokens"),
        output_tokens=_usage_value("completion_tokens", "output_tokens"),
        cache_tokens=_usage_value("cached_tokens", "cache_read_tokens"),
        total_tokens=_usage_value("total_tokens"),
        latency_ms=round((time.monotonic() - invocation_started_at) * 1000, 3),
        runtime_import_seconds=imported_at - setup_started,
        runtime_tools_seconds=tools_built_at - imported_at,
        runtime_llm_seconds=llm_built_at - tools_built_at,
        runtime_agent_seconds=agent_built_at - llm_built_at,
        runtime_kickoff_seconds=time.monotonic() - agent_built_at,
    )
    return raw_text
