"""CrewAI provider-call telemetry using the framework's public event bus."""

from dataclasses import dataclass
from datetime import datetime
import logging
import threading
from collections import OrderedDict
from typing import Any

from tools import get_current_stage, get_trace_id

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _CallStart:
    trace_id: str
    stage: str
    model: str
    agent: str | None
    started_at: datetime


_lock = threading.Lock()
_starts: dict[str, _CallStart] = {}
_pending_finishes: dict[str, tuple[Any, str, str]] = {}
_terminal_call_ids: "OrderedDict[str, None]" = OrderedDict()
_TERMINAL_CALL_ID_LIMIT = 4096
_installed = False


def _provider_name(model: str) -> str:
    return model.split("/", 1)[0] if "/" in model else model


def _usage_value(usage: dict[str, Any] | None, *names: str) -> Any:
    if not usage:
        return None
    for name in names:
        if usage.get(name) is not None:
            return usage[name]
    return None


def _cache_tokens(usage: dict[str, Any] | None) -> Any:
    direct = _usage_value(usage, "cached_tokens", "cache_read_tokens")
    if direct is not None:
        return direct
    details = (usage or {}).get("prompt_tokens_details")
    return details.get("cached_tokens") if isinstance(details, dict) else None


def _provider_error_detail(event: Any) -> str | None:
    error = getattr(event, "error", None)
    if error is None:
        return None
    detail = str(error)
    for prefix in (
        "OpenAI API call failed:",
        "OpenAI Responses API call failed:",
        "Failed to connect to OpenAI API:",
    ):
        if detail.startswith(prefix):
            return detail.removeprefix(prefix).strip()
    return detail


def _write_finish(start: _CallStart, event: Any) -> None:
    usage = getattr(event, "usage", None)
    usage = usage if isinstance(usage, dict) else None
    failed = getattr(event, "type", "") == "llm_call_failed"
    elapsed_ms = max(0.0, (event.timestamp - start.started_at).total_seconds() * 1000)
    finish_reason = getattr(event, "finish_reason", None)
    logger.info(
        "provider request failed" if failed else "provider request finished",
        extra={
            "event": "provider_request_failed" if failed else "provider_request_finished",
            "call_id": event.call_id,
            "agent": start.agent,
            "provider": _provider_name(start.model),
            "model": start.model,
            "stage": start.stage,
            "attempt": 1,
            "status": "error" if failed else "success",
            "error_detail": _provider_error_detail(event) if failed else None,
            "termination_reason": (
                type(getattr(event, "error", None)).__name__
                if failed and not isinstance(getattr(event, "error", None), str)
                else ("provider_error" if failed else (finish_reason or "completed"))
            ),
            "latency_ms": round(elapsed_ms, 3),
            "input_tokens": _usage_value(usage, "prompt_tokens", "input_tokens"),
            "output_tokens": _usage_value(usage, "completion_tokens", "output_tokens"),
            "cache_tokens": _cache_tokens(usage),
            "total_tokens": _usage_value(usage, "total_tokens"),
            "finish_reason": finish_reason,
            "response_id": getattr(event, "response_id", None),
            "call_type": str(getattr(event, "call_type", "")) or None,
            "trace_id": start.trace_id,
            "telemetry_only": True,
        },
    )


def _remember_terminal_call(call_id: str) -> None:
    """Bound duplicate suppression without retaining request data forever."""

    _terminal_call_ids[call_id] = None
    _terminal_call_ids.move_to_end(call_id)
    while len(_terminal_call_ids) > _TERMINAL_CALL_ID_LIMIT:
        _terminal_call_ids.popitem(last=False)


def handle_provider_call_started(_source: Any, event: Any) -> None:
    """Capture request context from one CrewAI LLM start event."""

    start = _CallStart(
        trace_id=get_trace_id(),
        stage=get_current_stage(),
        model=event.model or "unknown",
        agent=getattr(event, "agent_role", None),
        started_at=event.timestamp,
    )
    with _lock:
        if event.call_id in _terminal_call_ids:
            return
        pending = _pending_finishes.pop(event.call_id, None)
        if pending is None:
            _starts[event.call_id] = start
        else:
            _starts.pop(event.call_id, None)
            _remember_terminal_call(event.call_id)
    if pending is not None:
        pending_event, _trace_id, _stage = pending
        _write_finish(start, pending_event)


def handle_provider_call_finished(_source: Any, event: Any) -> None:
    """Persist one correlated CrewAI LLM completion or failure event."""

    with _lock:
        if event.call_id in _terminal_call_ids:
            return
        start = _starts.pop(event.call_id, None)
        if start is None:
            _pending_finishes.setdefault(event.call_id, (event, get_trace_id(), get_current_stage()))
            return
        _remember_terminal_call(event.call_id)
    _write_finish(start, event)


def install_crewai_provider_telemetry() -> None:
    """Register process-wide CrewAI handlers exactly once."""

    global _installed
    with _lock:
        if _installed:
            return
        from crewai.events import (
            LLMCallCompletedEvent,
            LLMCallFailedEvent,
            LLMCallStartedEvent,
            crewai_event_bus,
        )

        crewai_event_bus.on(LLMCallStartedEvent)(handle_provider_call_started)
        crewai_event_bus.on(LLMCallCompletedEvent)(handle_provider_call_finished)
        crewai_event_bus.on(LLMCallFailedEvent)(handle_provider_call_finished)
        _installed = True
