import threading
import time
import types

import pytest

from agents import adapter
from api.app import build_app
from api.jobs import job_status
from history.interface import record_event_outcome, record_event_state, record_initial_event, record_step_execution
from history.interface import InitialEventEnvelope, StepExecutionEnvelope
from orchestrator.holds import create_approval_hold, create_clarification_hold
from orchestrator.main_agent import RiskAssessment
from orchestrator.selection import ProtocolSelectionResult
from tests.api_fakes import VIEWER_IDENTITY, auth_headers, build_context


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


@pytest.fixture
def ctx(tmp_path):
    context = build_context(tmp_path)
    yield context
    context.queue.stop()
    context.deps.persistence.close()


def _new_event(ctx, **overrides) -> str:
    envelope = InitialEventEnvelope(raw_text="text", source="telegram", received_at="2026-08-24T10:00:00", sender_identity="viewer-1")
    event_id = record_initial_event(ctx.deps.persistence, envelope)
    if overrides:
        record_event_state(ctx.deps.persistence, event_id, overrides)
    return event_id


def test_job_status_is_queued_for_an_event_not_yet_picked_up(ctx):
    ctx.queue.stop()  # nothing draining the queue
    event_id = _new_event(ctx)
    ctx.queue.submit((event_id, lambda: None))

    assert job_status(ctx, event_id) == {"event_id": event_id, "status": "queued"}


def test_job_status_is_running_while_the_worker_is_on_it(ctx):
    event_id = _new_event(ctx)
    release = threading.Event()

    ctx.queue.submit((event_id, release.wait))
    for _ in range(200):
        if ctx.queue.currently_processing() is not None:
            break
        time.sleep(0.01)

    assert job_status(ctx, event_id) == {"event_id": event_id, "status": "running"}
    release.set()


def test_job_status_reports_held_for_clarification(ctx):
    event_id = _new_event(ctx)
    create_clarification_hold(ctx.deps.persistence, event_id, "raw text")
    record_event_state(ctx.deps.persistence, event_id, {"clarification_held": True})

    status = job_status(ctx, event_id)

    assert status["status"] == "held_for_clarification"
    assert status["unresolved_field"] == "classification"


def test_job_status_reports_held_for_approval_with_its_reason(ctx):
    event_id = _new_event(ctx)
    selection = ProtocolSelectionResult(status="selected", protocol_name="dispatch_response", reason="matches")
    risk = RiskAssessment(score=0.9, level="high", reason="active fire")
    create_approval_hold(ctx.deps.persistence, event_id, "flagged_protocol", selection, risk)
    record_event_state(ctx.deps.persistence, event_id, {"approval_held": True})

    status = job_status(ctx, event_id)

    assert status["status"] == "held_for_approval"
    assert status["reason"] == "flagged_protocol"


def test_job_status_reports_a_resolved_hold_as_no_longer_held(ctx):
    # A resolved hold must not be reported as still held, even though the
    # event's own clarification_held/approval_held columns never clear —
    # this is exactly what fetch_held_event (§2.13) exists to distinguish.
    event_id = _new_event(ctx)
    hold_id = create_clarification_hold(ctx.deps.persistence, event_id, "raw text")
    record_event_state(ctx.deps.persistence, event_id, {"clarification_held": True})
    ctx.deps.persistence.resolve_held_event("clarification", hold_id, {"resolved_by": "commander-1", "chosen_classification": "fire"})

    status = job_status(ctx, event_id)

    assert status["status"] != "held_for_clarification"


@pytest.mark.parametrize(
    "outcome,expected_detail_key",
    [("succeeded", None), ("failed", "outcome_failure_reason"), ("uncertain", None), ("declined", None), ("closed_on_precedent", "precedent_closed_by_event_id")],
)
def test_job_status_reports_every_terminal_outcome(ctx, outcome, expected_detail_key):
    event_id = _new_event(ctx)
    kwargs = {}
    if outcome == "failed":
        kwargs["failure_reason"] = "the model timed out"
    if outcome == "closed_on_precedent":
        record_event_state(ctx.deps.persistence, event_id, {"precedent_closed_by_event_id": "evt-old"})
    record_event_outcome(ctx.deps.persistence, event_id, outcome, **kwargs)

    status = job_status(ctx, event_id)

    assert status["status"] == outcome
    if expected_detail_key:
        assert "detail" in status


def test_job_status_reports_steps_completed_on_a_successful_run(ctx):
    event_id = _new_event(ctx)
    record_step_execution(ctx.deps.persistence, event_id, StepExecutionEnvelope(0, "reference_agent", "check gate 3", ["check_status"], "gate 3 is nominal", 1))
    record_step_execution(ctx.deps.persistence, event_id, StepExecutionEnvelope(1, "dispatch_agent", "dispatch", ["record_action"], "dispatched", 1))
    record_event_outcome(ctx.deps.persistence, event_id, "succeeded", insight_text="all clear")

    status = job_status(ctx, event_id)

    assert status["steps_completed"] == ["reference_agent: gate 3 is nominal", "dispatch_agent: dispatched"]


def test_job_status_reports_the_failed_step_agent_and_steps_completed_before_it(ctx):
    event_id = _new_event(ctx)
    record_step_execution(ctx.deps.persistence, event_id, StepExecutionEnvelope(0, "reference_agent", "check gate 3", ["check_status"], "gate 3 is nominal", 1))
    record_step_execution(ctx.deps.persistence, event_id, StepExecutionEnvelope(1, "dispatch_agent", "dispatch", ["record_action"], None, 3))
    record_event_outcome(ctx.deps.persistence, event_id, "failed", failure_reason="attempt limit exhausted")

    status = job_status(ctx, event_id)

    assert status["failed_step_agent_name"] == "dispatch_agent"
    assert status["steps_completed"] == ["reference_agent: gate 3 is nominal"]
    assert status["detail"] == "attempt limit exhausted"


def test_job_status_omits_steps_completed_and_failed_agent_when_no_step_ever_ran(ctx):
    # closed_on_precedent never reaches the executor — nothing to report.
    event_id = _new_event(ctx)
    record_event_state(ctx.deps.persistence, event_id, {"precedent_closed_by_event_id": "evt-old"})
    record_event_outcome(ctx.deps.persistence, event_id, "closed_on_precedent")

    status = job_status(ctx, event_id)

    assert "steps_completed" not in status
    assert "failed_step_agent_name" not in status


def test_get_job_route_includes_steps_completed_in_the_response_body(ctx):
    client = build_app(ctx).test_client()
    event_id = _new_event(ctx)
    record_step_execution(ctx.deps.persistence, event_id, StepExecutionEnvelope(0, "reference_agent", "check gate 3", ["check_status"], "gate 3 is nominal", 1))
    record_event_outcome(ctx.deps.persistence, event_id, "succeeded", insight_text="all clear")

    resp = client.get(f"/Job/{event_id}", headers=auth_headers(VIEWER_IDENTITY))

    assert resp.get_json()["steps_completed"] == ["reference_agent: gate 3 is nominal"]


def test_job_status_returns_none_for_an_unknown_event_id(ctx):
    assert job_status(ctx, "does-not-exist") is None


def test_get_job_route_returns_404_for_an_unknown_event(ctx):
    client = build_app(ctx).test_client()

    resp = client.get("/Job/does-not-exist", headers=auth_headers(VIEWER_IDENTITY))

    assert resp.status_code == 404
    assert resp.get_json()["error_class"] == "invalid_input"


def test_get_job_route_requires_authentication(ctx):
    client = build_app(ctx).test_client()
    event_id = _new_event(ctx)

    resp = client.get(f"/Job/{event_id}")

    assert resp.status_code == 401


def test_get_job_route_returns_the_status_body(ctx):
    client = build_app(ctx).test_client()
    event_id = _new_event(ctx)
    record_event_outcome(ctx.deps.persistence, event_id, "succeeded", insight_text="all clear")

    resp = client.get(f"/Job/{event_id}", headers=auth_headers(VIEWER_IDENTITY))

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "succeeded"
    assert body["insight_text"] == "all clear"
