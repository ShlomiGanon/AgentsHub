"""Named operational log events. Callers never pass extra={} dicts."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any

from tools.observability import (
    get_current_event_id,
    get_current_protocol,
    get_current_stage,
    get_profile_name,
    get_trace_id,
)

_logger = logging.getLogger("agentshub.events")


def _drop_nones(fields: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in fields.items() if value is not None}


def _present(value: str | None) -> str | None:
    return value or None


def _tools(value: Iterable[str] | None) -> list[str] | None:
    if value is None:
        return None
    return list(value)


def event_id_from_queue_payload(payload: object) -> str | None:
    """Return the event_id from a report/request queue payload; never a callable."""

    if isinstance(payload, tuple) and payload and isinstance(payload[0], str) and not callable(payload[0]):
        return payload[0]
    return None


def emit(event: str, *, level: int = logging.INFO, telemetry_only: bool = False, exc_info: bool = False, **fields: Any) -> None:
    """Inject trace_id, stage, protocol_name, event_id, and profile_name; drop unset context values."""

    extra = _drop_nones({
        "event": event,
        "trace_id": _present(get_trace_id()),
        "stage": _present(get_current_stage()),
        "protocol_name": _present(get_current_protocol()),
        "event_id": _present(get_current_event_id()),
        "profile_name": _present(get_profile_name()),
    })
    extra.update(fields)
    if telemetry_only:
        extra["telemetry_only"] = True
    _logger.log(level, event.replace("_", " "), extra=extra, exc_info=exc_info)


def specialist_started(*, agent: str, parent_agent: str = "main_agent", allowed_tools: Iterable[str] | None = None) -> None:
    if not agent:
        raise ValueError("agent is required")
    emit("specialist_started", agent=agent, parent_agent=parent_agent, allowed_tools=_tools(allowed_tools))


def specialist_finished(
    *,
    agent: str,
    status: str,
    duration_ms: float,
    parent_agent: str = "main_agent",
    invocation_id: str | None = None,
    allowed_tools: Iterable[str] | None = None,
) -> None:
    if not agent:
        raise ValueError("agent is required")
    emit(
        "specialist_finished",
        agent=agent,
        parent_agent=parent_agent,
        status=status,
        duration_ms=duration_ms,
        invocation_id=invocation_id,
        allowed_tools=_tools(allowed_tools),
    )


def specialist_failed(*, agent: str, cause: str | None = None) -> None:
    if not agent:
        raise ValueError("agent is required")
    emit("specialist_failed", level=logging.WARNING, agent=agent, cause=cause)


def specialist_timeout(*, agent: str, timeout_seconds: float) -> None:
    if not agent:
        raise ValueError("agent is required")
    emit("specialist_timeout", level=logging.WARNING, agent=agent, timeout_seconds=timeout_seconds)


def queue_started(
    *,
    queue_wait_seconds: float,
    event_id: str | None = None,
    concurrency_keys: Iterable[str] = (),
    payload: object = None,
) -> None:
    resolved = event_id if event_id is not None else event_id_from_queue_payload(payload)
    keys = list(concurrency_keys) or None
    emit(
        "queue_started",
        event_id=resolved,
        queue_wait_seconds=queue_wait_seconds,
        concurrency_keys=keys,
        telemetry_only=True,
    )


def queue_processing_failed(*, payload: object) -> None:
    emit(
        "queue_processing_failed",
        level=logging.ERROR,
        event_id=event_id_from_queue_payload(payload),
        exc_info=True,
    )


def queue_deadline_expired(*, payload: object) -> None:
    emit(
        "queue_deadline_expired",
        level=logging.WARNING,
        event_id=event_id_from_queue_payload(payload),
    )


def queue_stop_timeout(*, queue_name: str, timeout_seconds: float) -> None:
    emit(
        "queue_stop_timeout",
        level=logging.WARNING,
        queue_name=queue_name,
        timeout_seconds=timeout_seconds,
    )


def _emit_safely(event: str, **fields: Any) -> None:
    try:
        emit(event, **fields)
    except Exception:
        try:
            _logger.exception("%s log failed", event.replace("_", " "))
        except Exception:
            pass


def agent_invocation_started(
    *,
    agent: str,
    invocation_id: str,
    allowed_tools: Iterable[str],
    task_summary: str,
    parent_agent: str | None = None,
    parent_invocation_id: str | None = None,
) -> None:
    _emit_safely(
        "agent_invocation_started",
        agent=agent,
        agent_name=agent,
        invocation_id=invocation_id,
        allowed_tools=list(allowed_tools),
        task_summary=task_summary,
        parent_agent=parent_agent,
        parent_invocation_id=parent_invocation_id,
        telemetry_only=True,
    )


def agent_invocation_finished(
    *,
    agent: str,
    invocation_id: str,
    status: str,
    duration_ms: float,
    result_chars: int | None = None,
    error_type: str | None = None,
    parent_agent: str | None = None,
    parent_invocation_id: str | None = None,
) -> None:
    _emit_safely(
        "agent_invocation_finished",
        agent=agent,
        agent_name=agent,
        invocation_id=invocation_id,
        status=status,
        duration_ms=duration_ms,
        result_chars=result_chars,
        error_type=error_type,
        parent_agent=parent_agent,
        parent_invocation_id=parent_invocation_id,
        telemetry_only=True,
    )


def model_invocation_finished(
    *,
    agent: str,
    status: str,
    termination_reason: str,
    latency_ms: float,
    invocation_id: str | None = None,
    model: str | None = None,
    provider: str | None = None,
    attempt: int = 1,
    timeout_seconds: float | None = None,
    ttft_seconds: float | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    cache_tokens: int | None = None,
    total_tokens: int | None = None,
    runtime_import_seconds: float | None = None,
    runtime_tools_seconds: float | None = None,
    runtime_llm_seconds: float | None = None,
    runtime_agent_seconds: float | None = None,
    runtime_kickoff_seconds: float | None = None,
) -> None:
    emit(
        "model_invocation_finished",
        agent=agent,
        agent_name=agent,
        invocation_id=invocation_id,
        model=model,
        provider=provider,
        attempt=attempt,
        status=status,
        termination_reason=termination_reason,
        timeout_seconds=timeout_seconds,
        ttft_seconds=ttft_seconds,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_tokens=cache_tokens,
        total_tokens=total_tokens,
        latency_ms=latency_ms,
        runtime_import_seconds=runtime_import_seconds,
        runtime_tools_seconds=runtime_tools_seconds,
        runtime_llm_seconds=runtime_llm_seconds,
        runtime_agent_seconds=runtime_agent_seconds,
        runtime_kickoff_seconds=runtime_kickoff_seconds,
        telemetry_only=True,
    )


def model_warmup_started(*, provider: str, model: str) -> None:
    emit("model_warmup_started", provider=provider, model=model, telemetry_only=True)


def model_warmup_finished(
    *,
    provider: str,
    model: str,
    status: str,
    termination_reason: str,
    latency_ms: float,
    level: int = logging.INFO,
) -> None:
    emit(
        "model_warmup_finished",
        level=level,
        provider=provider,
        model=model,
        status=status,
        termination_reason=termination_reason,
        latency_ms=latency_ms,
        telemetry_only=True,
    )


def tool_blocked(*, agent: str, tool: str, invocation_id: str | None = None) -> None:
    emit("tool_blocked", agent=agent, tool=tool, invocation_id=invocation_id)


def tool_call(
    *,
    agent: str,
    tool: str,
    invocation_id: str | None,
    side_effecting: bool,
    status: str,
    duration_seconds: float,
    result_summary: str | None = None,
    exc_info: bool = False,
) -> None:
    level = logging.ERROR if exc_info else logging.INFO
    emit(
        "tool_call",
        level=level,
        agent=agent,
        invocation_id=invocation_id,
        tool=tool,
        side_effecting=side_effecting,
        status=status,
        duration_seconds=duration_seconds,
        result_summary=result_summary,
        exc_info=exc_info,
    )


def provider_request_finished(**fields: Any) -> None:
    failed = fields.get("status") == "error" or fields.get("event") == "provider_request_failed"
    event = "provider_request_failed" if failed else "provider_request_finished"
    fields.pop("event", None)
    emit(event, telemetry_only=True, **fields)


def step_started(
    *,
    agent: str,
    step_index: int,
    task_text: str,
    step_id: str | None = None,
    event_id: str | None = None,
    allowed_tools: Iterable[str] = (),
) -> None:
    emit(
        "step_start",
        agent=agent,
        step_index=step_index,
        task_text=task_text,
        step_id=step_id,
        event_id=event_id,
        allowed_tools=_tools(allowed_tools),
    )


def step_result(
    *,
    agent: str,
    step_index: int,
    succeeded: bool,
    attempt_count: int,
    result_text: str | None = None,
    invocation_id: str | None = None,
    step_id: str | None = None,
    event_id: str | None = None,
) -> None:
    emit(
        "step_result",
        agent=agent,
        step_index=step_index,
        succeeded=succeeded,
        attempt_count=attempt_count,
        result_text=result_text,
        invocation_id=invocation_id,
        step_id=step_id,
        event_id=event_id,
    )


def step_failed(*, agent: str, attempt: int, cause: str) -> None:
    emit("step_failed", agent=agent, attempt=attempt, cause=cause)


def step_retry(*, agent: str, attempt: int, cause: str) -> None:
    emit("step_retry", agent=agent, attempt=attempt, cause=cause)


def step_unclear(*, agent: str, attempt: int, missing: str) -> None:
    emit("step_unclear", agent=agent, attempt=attempt, missing=missing)


def direct_tool_step_error(*, agent: str, tool: str, cause: str) -> None:
    emit("direct_tool_step_error", agent=agent, tool=tool, cause=cause)


def stage_finished(*, stage: str, status: str, termination_reason: str, duration_seconds: float) -> None:
    emit(
        "stage_finished",
        stage=stage,
        status=status,
        termination_reason=termination_reason,
        duration_seconds=duration_seconds,
        telemetry_only=True,
    )


def report_received(*, event_id: str, source: str, sender_identity: str, raw_text: str) -> None:
    emit("report_received", event_id=event_id, source=source, sender_identity=sender_identity, raw_text=raw_text)


def request_received(*, event_id: str, sender_identity: str, raw_text: str) -> None:
    emit("request_received", event_id=event_id, sender_identity=sender_identity, raw_text=raw_text)


def extraction_result(
    *,
    event_id: str,
    classification: str | None,
    area: str | None,
    missing_fields: Iterable[str],
    occurred_at_is_fallback: bool,
) -> None:
    emit(
        "extraction_result",
        event_id=event_id,
        classification=classification,
        area=area,
        missing_fields=list(missing_fields),
        occurred_at_is_fallback=occurred_at_is_fallback,
    )


def extraction_retry(*, cause: str) -> None:
    emit("extraction_retry", cause=cause)


def hold_created(
    *,
    hold_kind: str,
    event_id: str,
    unresolved_field: str | None = None,
    missing_fields: Iterable[str] | None = None,
    classification: str | None = None,
    reason: str | None = None,
) -> None:
    emit(
        "hold_created",
        hold_kind=hold_kind,
        event_id=event_id,
        unresolved_field=unresolved_field,
        missing_fields=list(missing_fields) if missing_fields is not None else None,
        classification=classification,
        reason=reason,
    )


def hold_resolved(
    *,
    hold_kind: str,
    event_id: str,
    resolved_by: str,
    chosen_classification: str | None = None,
    decision: str | None = None,
    status: str | None = None,
    selected_protocol: str | None = None,
) -> None:
    emit(
        "hold_resolved",
        hold_kind=hold_kind,
        event_id=event_id,
        resolved_by=resolved_by,
        chosen_classification=chosen_classification,
        decision=decision,
        status=status,
        selected_protocol=selected_protocol,
    )


def hold_reminder_sent(*, hold_kind: str, event_id: str, hold_id: str) -> None:
    emit("hold_reminder_sent", hold_kind=hold_kind, event_id=event_id, hold_id=hold_id)


def hold_escalated(*, hold_kind: str, event_id: str, hold_id: str) -> None:
    emit("hold_escalated", hold_kind=hold_kind, event_id=event_id, hold_id=hold_id)


def hold_sweep_failed() -> None:
    emit("hold_sweep_failed", level=logging.ERROR, exc_info=True)


def risk_assessed(*, event_id: str, risk_level: str, risk_score: float, risk_reason: str) -> None:
    emit("risk_assessed", event_id=event_id, risk_level=risk_level, risk_score=risk_score, risk_reason=risk_reason)


def protocol_selection(
    *,
    event_id: str,
    status: str,
    protocol_name: str | None,
    candidate_names: Iterable[str],
    reason: str | None,
) -> None:
    emit(
        "protocol_selection",
        event_id=event_id,
        status=status,
        protocol_name=protocol_name,
        candidate_names=list(candidate_names),
        reason=reason,
    )


def precedent_closure(
    *,
    event_id: str,
    matched_event_ids: Iterable[str],
    closed: bool,
    closing_event_id: str | None,
) -> None:
    emit(
        "precedent_closure",
        event_id=event_id,
        matched_event_ids=list(matched_event_ids),
        closed=closed,
        closing_event_id=closing_event_id,
    )


def event_outcome(*, event_id: str, outcome: str, **detail: Any) -> None:
    emit("event_outcome", event_id=event_id, outcome=outcome, **detail)


def reply_latency(*, event_id: str, elapsed_seconds: float) -> None:
    emit("reply_latency", event_id=event_id, elapsed_seconds=elapsed_seconds, telemetry_only=True)


def insight_generated(*, event_id: str, protocol: str, insight_text: str) -> None:
    emit("insight_generated", event_id=event_id, protocol=protocol, insight_text=insight_text)


def final_verdict(*, event_id: str, verdict: str, reasoning: str) -> None:
    emit("final_verdict", event_id=event_id, verdict=verdict, reasoning=reasoning)


def event_correction_recorded(*, event_id: str, corrects_event_id: str) -> None:
    emit("event_correction_recorded", event_id=event_id, corrects_event_id=corrects_event_id)


def protocol_waiting_for_event_data(*, event_id: str, missing_event_fields: Iterable[str]) -> None:
    emit("protocol_waiting_for_event_data", event_id=event_id, missing_event_fields=list(missing_event_fields))


def direct_lane_declined(*, event_id: str, reason: str) -> None:
    emit("direct_lane_declined", event_id=event_id, reason=reason)


def direct_lane_accepted(*, event_id: str, actions: Iterable[str]) -> None:
    emit("direct_lane_accepted", event_id=event_id, actions=list(actions))


def operational_decision_invalid(*, mode: str, reason: str) -> None:
    emit("operational_decision_invalid", level=logging.WARNING, mode=mode, reason=reason)


def final_assessment_invalid(*, reason: str) -> None:
    emit("final_assessment_invalid", level=logging.WARNING, reason=reason)


def synthesis_failed(*, cause: str) -> None:
    emit("synthesis_failed", level=logging.WARNING, cause=cause)


def resource_unavailable_description_failed(*, resource_kind: str, reason: str) -> None:
    emit("resource_unavailable_description_failed", level=logging.WARNING, resource_kind=resource_kind, reason=reason)


def resource_unavailable_alert(*, event_id: str, resource_kind: str, area: str, reason: str) -> None:
    emit("resource_unavailable_alert", event_id=event_id, resource_kind=resource_kind, area=area, reason=reason)


def agent_selection(*, status: str, chosen_agents: Iterable[str], reason: str | None) -> None:
    emit("agent_selection", status=status, chosen_agents=list(chosen_agents), reason=reason)
