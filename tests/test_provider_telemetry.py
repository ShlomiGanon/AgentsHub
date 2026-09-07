"""Per-provider-call telemetry emitted from CrewAI's public LLM events."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from agents import provider_telemetry
from agents.provider_telemetry import (
    handle_provider_call_finished,
    handle_provider_call_started,
)
from tools import stage_context, trace_context


def _event(call_id: str, event_type: str, timestamp: datetime, **values):
    return SimpleNamespace(
        call_id=call_id,
        type=event_type,
        timestamp=timestamp,
        model=values.pop("model", "openai/test-model"),
        agent_role=values.pop("agent_role", "Main Agent"),
        usage=values.pop("usage", None),
        finish_reason=values.pop("finish_reason", None),
        response_id=values.pop("response_id", None),
        call_type=values.pop("call_type", None),
        **values,
    )


def test_four_crewai_provider_calls_produce_four_correlated_records(caplog):
    started_at = datetime.now(timezone.utc)
    with caplog.at_level("INFO"), trace_context("trace-four"), stage_context("tool_loop"):
        for index in range(4):
            call_id = f"call-{index}"
            handle_provider_call_started(
                None,
                _event(call_id, "llm_call_started", started_at + timedelta(seconds=index)),
            )
            handle_provider_call_finished(
                None,
                _event(
                    call_id,
                    "llm_call_completed",
                    started_at + timedelta(seconds=index, milliseconds=125),
                    usage={
                        "prompt_tokens": 10 + index,
                        "completion_tokens": 5,
                        "total_tokens": 15 + index,
                    },
                    finish_reason="stop",
                ),
            )

    records = [
        record for record in caplog.records
        if getattr(record, "event", None) == "provider_request_finished"
    ]
    assert len(records) == 4
    assert [record.call_id for record in records] == [f"call-{index}" for index in range(4)]
    assert all(record.trace_id == "trace-four" for record in records)
    assert all(record.stage == "tool_loop" for record in records)
    assert all(record.latency_ms == 125.0 for record in records)
    assert sum(record.total_tokens for record in records) == 66


def test_provider_failure_has_no_invented_usage(caplog):
    started_at = datetime.now(timezone.utc)
    with caplog.at_level("INFO"), trace_context("trace-failure"), stage_context("risk"):
        handle_provider_call_started(None, _event("failed-call", "llm_call_started", started_at))
        handle_provider_call_finished(
            None,
            _event(
                "failed-call",
                "llm_call_failed",
                started_at + timedelta(milliseconds=40),
                error="provider unavailable",
            ),
        )

    [record] = [
        record for record in caplog.records
        if getattr(record, "event", None) == "provider_request_failed"
    ]
    assert record.status == "error"
    assert record.input_tokens is None
    assert record.output_tokens is None
    assert record.total_tokens is None
    assert record.error_detail == "provider unavailable"


def test_duplicate_terminal_failure_event_is_ignored_without_becoming_pending(caplog):
    started_at = datetime.now(timezone.utc)
    call_id = "duplicate-failure"
    failure = _event(
        call_id,
        "llm_call_failed",
        started_at + timedelta(milliseconds=40),
        error="provider unavailable",
    )

    with caplog.at_level("INFO"), trace_context("trace-duplicate"), stage_context("warmup"):
        handle_provider_call_started(None, _event(call_id, "llm_call_started", started_at))
        handle_provider_call_finished(None, failure)
        handle_provider_call_finished(None, failure)

    records = [
        record for record in caplog.records
        if getattr(record, "event", None) == "provider_request_failed"
        and record.call_id == call_id
    ]
    assert len(records) == 1
    assert call_id not in provider_telemetry._pending_finishes


def test_out_of_order_handler_execution_is_correlated_by_call_id(caplog):
    started_at = datetime.now(timezone.utc)
    completed = _event(
        "raced-call",
        "llm_call_completed",
        started_at + timedelta(milliseconds=10),
        usage={"total_tokens": 3},
    )
    started = _event("raced-call", "llm_call_started", started_at)

    with caplog.at_level("INFO"), trace_context("trace-race"), stage_context("question"):
        handle_provider_call_finished(None, completed)
        handle_provider_call_started(None, started)

    [record] = [
        record for record in caplog.records
        if getattr(record, "event", None) == "provider_request_finished"
        and record.call_id == "raced-call"
    ]
    assert record.trace_id == "trace-race"
    assert record.latency_ms == 10.0
