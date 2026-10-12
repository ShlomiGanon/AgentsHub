"""CrewAI import and LLM client cache used by the agent runtime.

Cache keys hash the API key so tests and logs never see the raw secret.
"""

from __future__ import annotations

import hashlib
import json
import threading
from collections import OrderedDict
from functools import lru_cache

from agents.contracts import (
    AgentDescriptor,
    AgentFrameworkNotReadyError,
    AgentModelError,
    InvocationPolicy,
)
from tools import get_trace_id

_llm_cache: "OrderedDict[tuple[str, str, str], object]" = OrderedDict()
_llm_cache_lock = threading.Lock()
_LLM_CACHE_MAX_SIZE = 32
_structured_output_mode = "off"


@lru_cache(maxsize=1)
def _import_crewai():
    """Import CrewAI once, or raise a framework-not-ready error if it is missing."""

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


def _runtime_provider_capabilities(model: str):
    """Call `agents.runtime.provider_capabilities` so test monkeypatches apply."""

    from agents import runtime
    return runtime.provider_capabilities(model)


def _get_crewai():
    """Return the CrewAI module with console output suppressed."""

    crewai = _import_crewai()
    from crewai.events.utils.console_formatter import set_suppress_console_output

    set_suppress_console_output(True)
    return crewai


def configure_structured_output_mode(mode: str) -> None:
    """Set whether provider JSON-schema output is off, auto, or required."""

    global _structured_output_mode
    if mode not in {"off", "auto", "required"}:
        raise ValueError("structured output mode must be off, auto, or required")
    _structured_output_mode = mode


# Claude generations whose extended thinking is always on: a request without a reasoning
# setting runs at the model's default effort rather than with thinking off.
_ALWAYS_ON_THINKING_MARKERS = ("claude-sonnet-5", "claude-opus-5", "claude-haiku-5", "claude-fable", "claude-mythos")
_ALWAYS_ON_THINKING_HEADROOM_TOKENS = 1024


def _thinking_always_on(model: str) -> bool:
    """True for a model whose thinking cannot be disabled, so 'no reasoning' must mean 'low'."""

    lowered = (model or "").lower()
    return any(marker in lowered for marker in _ALWAYS_ON_THINKING_MARKERS)


def _llm_options(
    descriptor: AgentDescriptor,
    *,
    timeout_seconds: float,
    invocation_policy: InvocationPolicy | None = None,
) -> dict:
    """Build request-safe CrewAI LLM options in one place."""

    options = {
        "model": descriptor.model,
        "timeout": timeout_seconds,
        # CrewAI's native OpenAI-compatible providers configure retries on the
        # SDK client. Putting this in additional_params would forward it to
        # Completions.create as an invalid request parameter.
        "max_retries": 0,
    }
    if descriptor.api_key:
        options["api_key"] = descriptor.api_key
    if invocation_policy is not None and invocation_policy.max_output_tokens is not None:
        options["max_tokens"] = invocation_policy.max_output_tokens
    if invocation_policy is not None and invocation_policy.reasoning_effort != "none":
        options["reasoning_effort"] = invocation_policy.reasoning_effort
    elif invocation_policy is not None and _thinking_always_on(descriptor.model):
        # These models think by default and cannot switch it off, so "none" (send nothing)
        # meant their default effort, whose thinking used up a small answer budget and came
        # back empty. Ask for the least thinking instead, and give that thinking its own room.
        options["reasoning_effort"] = "low"
        if "max_tokens" in options:
            options["max_tokens"] = options["max_tokens"] + _ALWAYS_ON_THINKING_HEADROOM_TOKENS
    if invocation_policy is not None and invocation_policy.response_schema is not None and _structured_output_mode != "off":
        capabilities = _runtime_provider_capabilities(descriptor.model)
        if capabilities.strict_json_schema:
            schema_name = str(invocation_policy.response_schema.get("name", "agentshub_output"))
            schema = invocation_policy.response_schema.get("schema", invocation_policy.response_schema)
            options["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": schema_name, "strict": True, "schema": schema},
            }
        elif _structured_output_mode == "required":
            raise AgentModelError(
                descriptor.name,
                f"provider for {descriptor.model!r} does not support strict structured output",
                trace_id=get_trace_id(),
            )
    return options


def _llm_cache_key(descriptor: AgentDescriptor, options: dict) -> tuple[str, str, str]:
    """Return a non-rendered key containing no reversible credential value."""

    secret_identity = hashlib.sha256((descriptor.api_key or "").encode("utf-8")).hexdigest()
    public_options = {key: value for key, value in options.items() if key != "api_key"}
    option_identity = json.dumps(public_options, sort_keys=True, separators=(",", ":"), default=str)
    return descriptor.model, secret_identity, option_identity


def _build_or_reuse_llm(crewai_module, descriptor: AgentDescriptor, options: dict):
    """Construct an isolated LLM unless its provider explicitly opts into reuse."""

    if not _runtime_provider_capabilities(descriptor.model).thread_safe_client:
        return crewai_module.LLM(**options)

    cache_key = _llm_cache_key(descriptor, options)
    with _llm_cache_lock:
        cached = _llm_cache.get(cache_key)
        if cached is not None:
            _llm_cache.move_to_end(cache_key)
            return cached
        llm = crewai_module.LLM(**options)
        _llm_cache[cache_key] = llm
        _llm_cache.move_to_end(cache_key)
        while len(_llm_cache) > _LLM_CACHE_MAX_SIZE:
            _llm_cache.popitem(last=False)
        return llm


def _clear_llm_cache() -> None:
    """Drop cached LLM clients; used by tests and process teardown."""

    with _llm_cache_lock:
        _llm_cache.clear()
