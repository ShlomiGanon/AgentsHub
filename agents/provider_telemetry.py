"""CrewAI provider-call telemetry using the framework's public event bus."""

from dataclasses import dataclass
from datetime import datetime
from contextlib import contextmanager
from contextvars import ContextVar
import threading
from collections import OrderedDict
from typing import Any

from agents.invocation_context import (current_invocation_agent, current_invocation_id,
    current_parent_agent, current_parent_invocation_id, last_invocation_tool)
from tools import get_current_protocol, get_current_stage, get_trace_id
from tools.log_events import provider_request_finished


@dataclass(frozen=True)
class _CallStart:
    trace_id: str
    stage: str
    model: str
    agent: str | None
    started_at: datetime
    finish_reasons: list[str] | None
    invocation_id: str | None
    parent_agent: str | None
    parent_invocation_id: str | None
    purpose: str
    sequence_number: int | None
    tool_name: str | None
    protocol_name: str | None


_STAGE_PURPOSES = {
    "intent_classification": "intent_classification",
    "extraction": "event_extraction",
    "question_direct_lookup_classification": "agent_selection",
    "question_routing": "agent_selection",
    "question_routing_repair": "retry",
    "question_subagent": "specialist_reasoning",
    "question_composition": "response_composition",
    "question_history_query": "planning",
    "question_direct_lookup": "specialist_reasoning",
    "risk_assessment": "risk_assessment",
    "protocol_selection": "protocol_selection",
    "task_formulation": "planning",
    "task_formulation_repair": "retry",
    "task_rewrite": "planning",
    "success_judgment": "outcome_evaluation",
    "insight_generation": "outcome_evaluation",
    "event_data_question": "planning",
    "event_data_update": "tool_result_interpretation",
    "picture_planning": "planning",
    "picture_recent_events": "planning",
    "picture_specialist": "specialist_reasoning",
    "picture_composition": "synthesis",
    "report_composition": "response_composition",
    "operational_decision": "operational_decision",
    "step_execution": "tool_decision",
    "task_execution": "tool_decision",
}


_lock = threading.Lock()
_starts: dict[str, _CallStart] = {}
_pending_finishes: dict[str, tuple[Any, str, str]] = {}
_terminal_call_ids: "OrderedDict[str, None]" = OrderedDict()
_TERMINAL_CALL_ID_LIMIT = 4096
_installed = False
_active_finish_reasons: ContextVar[list[str] | None] = ContextVar("provider_finish_reasons", default=None)
_trace_sequences: dict[str, int] = {}


def _purpose_for_event(stage: str, call_type: Any) -> str:
    """Resolve purpose from explicit stage context and provider event type, never prompt text."""
    kind = str(call_type or "").lower()
    if "tool_call" in kind:
        return "tool_decision"
    if "llm_call" in kind and stage in {"question_subagent", "picture_specialist", "step_execution", "task_execution"}:
        if last_invocation_tool(current_invocation_id()):
            return "tool_result_interpretation"
    return _STAGE_PURPOSES.get(stage, "unattributed")


def _next_sequence(trace_id: str) -> int | None:
    if not trace_id:
        return None
    with _lock:
        value = _trace_sequences.get(trace_id, 0) + 1
        _trace_sequences[trace_id] = value
        if len(_trace_sequences) > 2048:
            _trace_sequences.pop(next(iter(_trace_sequences)))
        return value


@contextmanager
def track_provider_finish_reasons():
    """Keep final provider termination local to one agent invocation, including parallel agents."""

    reasons: list[str] = []
    token = _active_finish_reasons.set(reasons)
    try:
        yield reasons
    finally:
        _active_finish_reasons.reset(token)


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
    if start.finish_reasons is not None and finish_reason is not None:
        start.finish_reasons.append(str(finish_reason))
    finished_at = event.timestamp
    provider_request_finished(
        call_id=event.call_id,
        provider_request_id=getattr(event, "response_id", None) or event.call_id,
        telemetry_call_id=event.call_id,
        invocation_id=start.invocation_id,
        agent=start.agent,
        agent_name=start.agent,
        agent_invocation_id=start.invocation_id,
        parent_agent=start.parent_agent,
        parent_invocation_id=start.parent_invocation_id,
        provider=_provider_name(start.model),
        model=start.model,
        stage=start.stage,
        purpose=start.purpose,
        protocol_name=start.protocol_name,
        tool_name=start.tool_name,
        sequence_number=start.sequence_number,
        started_at=start.started_at.isoformat(),
        finished_at=finished_at.isoformat(),
        attempt=1,
        status="error" if failed else "success",
        error_detail=_provider_error_detail(event) if failed else None,
        termination_reason=(
            type(getattr(event, "error", None)).__name__
            if failed and not isinstance(getattr(event, "error", None), str)
            else ("provider_error" if failed else (finish_reason or "completed"))
        ),
        latency_ms=round(elapsed_ms, 3),
        input_tokens=_usage_value(usage, "prompt_tokens", "input_tokens"),
        output_tokens=_usage_value(usage, "completion_tokens", "output_tokens"),
        cache_tokens=_cache_tokens(usage),
        total_tokens=_usage_value(usage, "total_tokens"),
        finish_reason=finish_reason,
        response_id=getattr(event, "response_id", None),
        call_type=str(getattr(event, "call_type", "")) or None,
        trace_id=start.trace_id or None,
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
        agent=current_invocation_agent() or "unattributed",
        started_at=event.timestamp,
        finish_reasons=_active_finish_reasons.get(),
        invocation_id=current_invocation_id(),
        parent_agent=current_parent_agent() or ("Orchestrator" if current_invocation_id() else None),
        parent_invocation_id=current_parent_invocation_id(),
        purpose=_purpose_for_event(get_current_stage(), getattr(event, "call_type", None)),
        sequence_number=_next_sequence(get_trace_id()),
        tool_name=last_invocation_tool(current_invocation_id()),
        protocol_name=get_current_protocol(),
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
