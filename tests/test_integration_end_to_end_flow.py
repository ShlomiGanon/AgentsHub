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

import urllib.error
import urllib.request

import config.base as base_config
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

    # §1.8's own named events — each must fire, carry the same trace ID, and
    # carry the specific values this run actually produced, not just any
    # value (the coverage audit found these entirely missing before this fix).
    by_event = {r["event"]: r for r in records if "event" in r}

    assert by_event["extraction_result"]["classification"] == "fire"
    assert by_event["extraction_result"]["area"] == "north_sector"

    assert by_event["risk_assessed"]["risk_level"] == event["risk_level"]
    assert by_event["risk_assessed"]["risk_reason"] == event["risk_reason"]

    assert by_event["protocol_selection"]["protocol_name"] == "status_check"
    assert by_event["protocol_selection"]["reason"] == event["protocol_reason"]

    assert by_event["precedent_closure"]["closed"] is False

    assert by_event["insight_generated"]["insight_text"] == event["insight_text"]

    assert by_event["final_verdict"]["verdict"] == "success"

    outcome_records = [r for r in records if r.get("event") == "event_outcome"]
    assert outcome_records[-1]["outcome"] == "succeeded"

    for name in ("extraction_result", "risk_assessed", "protocol_selection", "precedent_closure", "insight_generated", "final_verdict"):
        assert by_event[name]["trace_id"] == the_trace_id, f"{name} did not carry the run's own trace ID"


def test_intent_decision_is_logged_with_the_request_trace_id(tmp_path, monkeypatch):
    captured = io.StringIO()
    monkeypatch.setattr("sys.stdout", captured)
    configure_logging("test-intent-logging-profile")

    agent = happy_path_agent(risk_score="0.2", selected="status_check", verdict="success", intent="question")
    agent._dispatch["Decide which of the following agents"] = "AGENT: reference_agent\nTASK: check gate 3"
    ctx = build_context(tmp_path, main_agent=agent)

    with RunningApiServer(ctx) as server:
        body = json.dumps({"text": "what's the status at gate 3?", "sender_identity": "viewer-1"}).encode("utf-8")
        request = urllib.request.Request(
            f"{server.base_url}/Msg", data=body, method="POST",
            headers={"X-Identity": "viewer-1", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=10.0) as response:
            payload = json.loads(response.read())

        assert payload["taken_as"] == "question"

    lines = [line for line in captured.getvalue().splitlines() if line.strip()]
    records = [json.loads(line) for line in lines]

    intent_records = [r for r in records if r.get("event") == "intent_classified"]
    assert intent_records, "the intent decision was never logged"
    assert intent_records[0]["intent"] == "question"
    assert intent_records[0]["reason"]
    assert intent_records[0]["trace_id"] != ""


def test_model_io_is_not_logged_when_the_debug_flag_is_off(tmp_path, monkeypatch):
    """docs/server_report.md Finding 1 follow-up (DEBUG_VERBOSE_LOGGING).

    The normal case: no such variable set. The INFO decision log (Finding
    1's own guarantee) must be completely unaffected — an operator
    following docs/operator_guide.md in normal operation must still see
    the full decision journey with nothing missing.
    """

    monkeypatch.setattr(base_config, "DEBUG_FLAG", False)

    captured = io.StringIO()
    monkeypatch.setattr("sys.stdout", captured)
    configure_logging("test-model-io-off-profile")

    agent = happy_path_agent(risk_score="0.2", selected="status_check", verdict="success")
    ctx = build_context(tmp_path, main_agent=agent)

    with RunningApiServer(ctx) as server:
        result = _post_event(server.base_url, SENSOR_IDENTITY, "smoke observed near gate 3, north_sector")
        assert result["status_code"] == 202
        ctx.queue.wait_until_idle()

    lines = [line for line in captured.getvalue().splitlines() if line.strip()]
    records = [json.loads(line) for line in lines]

    assert not [r for r in records if r.get("event") == "model_io"], "model I/O was logged even though the debug flag is off"

    # Every decision event Finding 1 added is still present, untouched.
    present = {r["event"] for r in records if "event" in r}
    for name in ("extraction_result", "risk_assessed", "protocol_selection", "insight_generated", "final_verdict", "event_outcome"):
        assert name in present, f"{name} is missing — the debug-gating must not have touched the always-on decision log"


def test_model_io_is_logged_with_prompt_response_stage_and_trace_id_when_the_debug_flag_is_on(tmp_path, monkeypatch):
    # happy_path_agent() (used by every other test in this file) is a fake
    # that never reaches agents/adapter.py::invoke — the one real choke
    # point model I/O is logged from — so it can't exercise this. Needs a
    # *real* MainAgent/InsightsAgent, with the mocked crewai kickoff
    # dispatching by prompt keyword (mirroring ScriptedAgent's own
    # technique, but one layer lower, at the crewai boundary itself) so
    # every real decision call actually goes through the choke point.
    from orchestrator.insights import InsightsAgent
    from orchestrator.main_agent import MainAgent
    from tests.api_fakes import extraction_response

    monkeypatch.setattr(base_config, "DEBUG_FLAG", True)

    dispatch = {
        "Extract this operational event": extraction_response(),
        "RISK_SCORE": "RISK_SCORE: 0.2\nREASON: assessed",
        "Choose the protocol": "SELECTED: status_check\nREASON: fits",
        "participating in the": "AGENT: reference_agent\nTASK: check gate 3",
        "VERDICT:": "VERDICT: success\nREASONING: matches expected output",
        "Form one conclusion": "no notable precedent",
        "": "status nominal, no anomalies",  # catch-all: the reference agent's own step text ("check gate 3"), matched last
    }

    class _DispatchedOutput:
        def __init__(self, raw):
            self.raw = raw

    class _DispatchedCrewAgent:
        def __init__(self, **kwargs):
            pass

        def kickoff(self, text):
            for keyword, response_text in dispatch.items():
                if keyword in text:
                    return _DispatchedOutput(response_text)
            raise AssertionError(f"no scripted response for prompt starting: {text[:150]!r}")

    dispatched_module = types.SimpleNamespace(Agent=_DispatchedCrewAgent, tools=types.SimpleNamespace(BaseTool=object))
    monkeypatch.setattr(adapter, "_get_crewai", lambda: dispatched_module)

    captured = io.StringIO()
    monkeypatch.setattr("sys.stdout", captured)
    configure_logging("test-model-io-on-profile")

    ctx = build_context(tmp_path, main_agent=MainAgent(model="m"), insights_agent=InsightsAgent(model="m"))

    with RunningApiServer(ctx) as server:
        result = _post_event(server.base_url, SENSOR_IDENTITY, "smoke observed near gate 3, north_sector")
        assert result["status_code"] == 202
        ctx.queue.wait_until_idle()

    lines = [line for line in captured.getvalue().splitlines() if line.strip()]
    records = [json.loads(line) for line in lines]

    model_io_records = [r for r in records if r.get("event") == "model_io"]
    assert model_io_records, "no model_io records were logged even though the debug flag was on"
    assert all(r["level"] == "DEBUG" for r in model_io_records)

    for record in model_io_records:
        assert record["prompt"], "the full prompt must be present, not summarized or truncated"
        assert record["response"], "the full raw response must be present"
        assert record["agent"]

    stages_seen = {r["stage"] for r in model_io_records}
    assert "risk_assessment" in stages_seen
    assert "protocol_selection" in stages_seen
    assert "task_formulation" in stages_seen
    assert "success_judgment" in stages_seen

    # Every record in this run — decision-level and model-I/O alike —
    # shares one trace ID, so raw model traffic can be correlated with the
    # decision log for the same run.
    trace_ids_seen = {r["trace_id"] for r in records if r.get("trace_id")}
    assert len(trace_ids_seen) == 1
    [the_trace_id] = trace_ids_seen
    assert all(r["trace_id"] == the_trace_id for r in model_io_records)
