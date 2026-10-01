"""Named operational log helpers."""

import json

import pytest

from tools.logging_config import configure_logging
from tools.log_events import emit, event_id_from_queue_payload, queue_started, specialist_started
from tools.tracing import stage_context, trace_context


def _records(capsys):
    lines = [line for line in capsys.readouterr().out.strip().splitlines() if line]
    return [json.loads(line) for line in lines]


def test_emit_injects_trace_id_and_stage(capsys):
    configure_logging("test_profile")
    with trace_context("trace-emit"), stage_context("extraction"):
        emit("test_event", foo="bar")

    by_event = {record["event"]: record for record in _records(capsys) if "event" in record}
    record = by_event["test_event"]
    assert record["trace_id"] == "trace-emit"
    assert record["stage"] == "extraction"
    assert record["foo"] == "bar"


def test_specialist_helper_refuses_a_missing_agent():
    with pytest.raises(TypeError):
        specialist_started()
    with pytest.raises(ValueError, match="agent is required"):
        specialist_started(agent="")


def test_queue_helper_never_logs_a_callable(capsys):
    configure_logging("test_profile")
    queue_started(
        queue_wait_seconds=0.25,
        payload=("evt-9", lambda: None),
        concurrency_keys=("sender:alice",),
    )
    record = next(item for item in _records(capsys) if item.get("event") == "queue_started")
    assert record["event_id"] == "evt-9"
    assert record["concurrency_keys"] == ["sender:alice"]
    assert "payload" not in record
    blob = json.dumps(record)
    assert "lambda" not in blob
    assert "<function" not in blob


def test_agent_invocation_helpers_log_when_emit_fails(monkeypatch, capsys):
    import tools.log_events as log_events
    from tools.log_events import agent_invocation_finished, agent_invocation_started

    def _boom(*args, **kwargs):
        raise RuntimeError("handler exploded")

    configure_logging("test_profile")
    monkeypatch.setattr(log_events, "emit", _boom)

    agent_invocation_started(
        agent="main_agent",
        invocation_id="inv-1",
        allowed_tools=["check_status"],
        task_summary="inspect gate",
    )
    agent_invocation_finished(
        agent="main_agent",
        invocation_id="inv-1",
        status="error",
        duration_ms=12.0,
    )

    messages = [record["message"] for record in _records(capsys)]
    assert "agent invocation started log failed" in messages
    assert "agent invocation finished log failed" in messages


def test_event_id_from_queue_payload_ignores_callables():
    assert event_id_from_queue_payload(("evt-1", lambda: None)) == "evt-1"
    assert event_id_from_queue_payload(lambda: None) is None
    assert event_id_from_queue_payload(("not-an-event", object())) == "not-an-event"


def test_step_started_includes_event_id_and_allowed_tools(capsys):
    from tools.log_events import step_started

    configure_logging("test_profile")
    with trace_context("trace-step"):
        step_started(
            agent="reference_agent",
            step_index=0,
            task_text="check gate",
            step_id="s1",
            event_id="evt-3",
            allowed_tools=["check_status"],
        )
    record = next(item for item in _records(capsys) if item.get("event") == "step_start")
    assert record["event_id"] == "evt-3"
    assert record["allowed_tools"] == ["check_status"]
    assert record["agent"] == "reference_agent"
    assert record["trace_id"] == "trace-step"
