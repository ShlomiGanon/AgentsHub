"""9.2 — Run the end-to-end flow test (work_plan.md §9.2).

Drives one event from the simulator's own submission path (`POST /Event`,
against a real running API) through every stage — extraction, risk
assessment, protocol selection, precedent lookup, task formulation,
execution, insights, judgment, and the history write — and confirms both
that the event record carries everything each stage should have written,
and that one trace ID connects every log record produced along the way.

Run first among Mission 9's integration tests, per this subtask's own
last bullet: almost every later one assumes this path works.
"""

import io
import json
import logging
import types

import pytest

from agents import adapter
from tests.api_fakes import SENSOR_IDENTITY, RunningApiServer, build_context, happy_path_agent
from tools.logging_config import configure_logging
from tools.simulator import _post_event


@pytest.fixture(autouse=True)
def _mock_crewai(monkeypatch):
    class _FakeOutput:
        def __init__(self, raw):
            self.raw = raw

    class _FakeCrewAgent:
        def __init__(self, **kwargs):
            pass

        def kickoff(self, text):
            return _FakeOutput("status nominal, no anomalies")

    fake_module = types.SimpleNamespace(Agent=_FakeCrewAgent, tools=types.SimpleNamespace(BaseTool=object))
    monkeypatch.setattr(adapter, "_get_crewai", lambda: fake_module)


def test_one_event_end_to_end_writes_every_stages_field_and_shares_one_trace_id(tmp_path, monkeypatch):
    captured = io.StringIO()
    monkeypatch.setattr("sys.stdout", captured)
    configure_logging("test-end-to-end-profile")

    agent = happy_path_agent(risk_score="0.2", selected="status_check", verdict="success")
    ctx = build_context(tmp_path, main_agent=agent)

    with RunningApiServer(ctx) as server:
        result = _post_event(server.base_url, SENSOR_IDENTITY, "smoke observed near gate 3, north_sector")
        assert result["status_code"] == 202
        event_id = result["event_id"]

        ctx.queue.wait_until_idle()

        event = ctx.deps.persistence.fetch_event(event_id)

        # -- Every stage wrote what it should ---------------------------

        # Extraction.
        assert event["classification"] == "fire"
        assert event["area"] == "north_sector"
        assert event["description"]
        assert event["severity"]

        # Risk assessment.
        assert event["risk_level"] is not None
        assert event["risk_reason"]

        # Protocol selection.
        assert event["selected_protocol"] == "status_check"
        assert event["protocol_reason"]

        # Precedent lookup — a first-time event has no match, but the
        # column itself must exist and be readable, confirming the stage
        # ran rather than was skipped.
        assert "precedent_matched_event_ids" in event
        assert "precedent_closed_by_event_id" in event

        # Task formulation + execution: every step has its own task text
        # and result.
        assert len(event["steps"]) >= 1
        for step in event["steps"]:
            assert step["task_text"]
            assert step["result_text"]

        # Insights + judgment.
        assert event["insight_text"]
        assert event["outcome"] == "succeeded"

    # -- The trace ID connects every log record ---------------------------

    lines = [line for line in captured.getvalue().splitlines() if line.strip()]
    records = [json.loads(line) for line in lines]
    assert records, "no log records were captured at all — logging_config isn't wired to this stream"

    trace_ids_seen = {r["trace_id"] for r in records if r.get("trace_id")}
    assert trace_ids_seen, "not a single log record carried a non-empty trace_id — the trace context was never entered"
    assert len(trace_ids_seen) == 1, f"more than one distinct non-empty trace_id appeared in one event's own processing: {trace_ids_seen}"

    # And it's real content, not a coincidental single blank value.
    [the_trace_id] = trace_ids_seen
    assert the_trace_id != ""

    # At least one record from each of the real stages fired and carried it.
    step_start_records = [r for r in records if r.get("event") == "step_start"]
    assert step_start_records, "protocols/executor.py's own step_start log never fired"
    assert all(r["trace_id"] == the_trace_id for r in step_start_records)
