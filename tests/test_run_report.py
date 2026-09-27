"""orchestrator/run_report.py — RunSummary builder and its deterministic renderer."""

import pytest

from history.contracts import InitialEventEnvelope, StepExecutionEnvelope
from history.event_pipeline import record_event_outcome, record_initial_event, record_step_execution
from messages import get_catalog
from orchestrator.holds import create_approval_hold, create_clarification_hold, create_event_data_hold
from orchestrator.reasoning import ProtocolSelectionResult, RiskAssessment
from orchestrator.run_report import build_run_summary, render_summary
from persistence import open_persistence


@pytest.fixture
def persistence(tmp_path):
    store = open_persistence(str(tmp_path / "run_report_test.db"))
    yield store
    store.close()


def _new_event(persistence, **overrides) -> str:
    envelope = InitialEventEnvelope(
        raw_text="a fire was seen near the north gate",
        source="telegram",
        received_at="2026-08-24T10:00:00",
        sender_identity="viewer-1",
        sender_permission_level=overrides.pop("sender_permission_level", "viewer"),
    )
    event_id = record_initial_event(persistence, envelope)
    if overrides:
        persistence.update_event(event_id, overrides)
    return event_id


# -- build_run_summary --------------------------------------------------------


def test_build_run_summary_reads_the_understood_fields_from_the_event(persistence):
    event_id = _new_event(
        persistence,
        classification="fire",
        area="north_sector",
        description="smoke visible near the gate",
        severity="high",
        selected_protocol="report_fire_incident",
        protocol_reason="matches a fire report",
        risk_level="high",
        risk_reason="active flame",
    )

    summary = build_run_summary(persistence, event_id)

    assert summary.event_id == event_id
    assert summary.raw_text == "a fire was seen near the north gate"
    assert summary.sender_permission_level == "viewer"
    assert summary.classification == "fire"
    assert summary.area == "north_sector"
    assert summary.description == "smoke visible near the gate"
    assert summary.severity == "high"
    assert summary.selected_protocol == "report_fire_incident"
    assert summary.protocol_reason == "matches a fire report"
    assert summary.risk_level == "high"
    assert summary.risk_reason == "active flame"


def test_build_run_summary_raises_for_an_unknown_event_id(persistence):
    with pytest.raises(ValueError):
        build_run_summary(persistence, "does-not-exist")


def test_build_run_summary_includes_every_persisted_step_in_order(persistence):
    event_id = _new_event(persistence)
    record_step_execution(
        persistence, event_id,
        StepExecutionEnvelope(0, "surveillance_agent", "check the north gate camera", ["check_status"], "camera nominal", 1),
    )
    record_step_execution(
        persistence, event_id,
        StepExecutionEnvelope(1, "friendly_forces_agent", "dispatch mutual aid", ["dispatch_water_tankers"], None, 2, status="failed", failure_reason="unit unavailable"),
    )
    record_event_outcome(persistence, event_id, "failed", failure_reason="attempt limit exhausted")

    summary = build_run_summary(persistence, event_id)

    assert [step.agent_name for step in summary.steps] == ["surveillance_agent", "friendly_forces_agent"]
    assert summary.steps[0].task_text == "check the north gate camera"
    assert summary.steps[0].result_text == "camera nominal"
    assert summary.steps[1].failure_reason == "unit unavailable"
    assert summary.outcome == "failed"
    assert summary.outcome_failure_reason == "attempt limit exhausted"


def test_build_run_summary_picks_up_an_unresolved_approval_hold(persistence):
    event_id = _new_event(persistence)
    selection = ProtocolSelectionResult(status="selected", protocol_name="dispatch_mutual_aid", reason="matches")
    risk = RiskAssessment(score=0.9, level="high", reason="active fire")
    create_approval_hold(persistence, event_id, "flagged_protocol", selection, risk)

    summary = build_run_summary(persistence, event_id)

    assert summary.pending is not None
    assert summary.pending.kind == "approval"
    assert summary.pending.risk_level == "high"
    assert summary.pending.risk_reason == "active fire"


def test_build_run_summary_picks_up_an_unresolved_clarification_hold(persistence):
    event_id = _new_event(persistence)
    create_clarification_hold(persistence, event_id, "raw text")

    summary = build_run_summary(persistence, event_id)

    assert summary.pending is not None
    assert summary.pending.kind == "clarification"
    assert summary.pending.unresolved_field == "classification"


def test_build_run_summary_picks_up_an_unresolved_event_data_hold(persistence):
    event_id = _new_event(persistence)
    create_event_data_hold(persistence, event_id, ("availability_start",), "When do you become available?", ())

    summary = build_run_summary(persistence, event_id)

    assert summary.pending is not None
    assert summary.pending.kind == "event_data"
    assert summary.pending.missing_fields == ("availability_start",)
    assert summary.pending.question == "When do you become available?"


def test_build_run_summary_has_no_pending_once_the_hold_is_resolved(persistence):
    event_id = _new_event(persistence)
    hold_id = create_clarification_hold(persistence, event_id, "raw text")
    persistence.resolve_held_event("clarification", hold_id, {"resolved_by": "commander-1", "chosen_classification": "fire"})

    summary = build_run_summary(persistence, event_id)

    assert summary.pending is None


# -- render_summary ------------------------------------------------------------


def _summary_with_steps_and_protocol(persistence):
    event_id = _new_event(
        persistence,
        classification="fire",
        area="north_sector",
        description="smoke near the gate",
        severity="high",
        selected_protocol="report_fire_incident",
        protocol_reason="matches a fire report",
        risk_level="high",
        risk_reason="active flame",
    )
    record_step_execution(
        persistence, event_id,
        StepExecutionEnvelope(0, "surveillance_agent", "dispatch a drone to the north gate", ["dispatch_drone_to_area"], "drone Eagle-1 dispatched", 1),
    )
    record_event_outcome(persistence, event_id, "succeeded")
    return build_run_summary(persistence, event_id)


def test_render_summary_commander_includes_protocol_agent_task_and_result(persistence):
    summary = _summary_with_steps_and_protocol(persistence)

    text = render_summary(summary, "commander", get_catalog("en"))

    assert "smoke near the gate" in text
    assert "report_fire_incident" in text
    assert "surveillance_agent" in text
    assert "dispatch a drone to the north gate" in text
    assert "drone Eagle-1 dispatched" in text
    assert "high" in text


def test_render_summary_viewer_omits_protocol_agent_task_and_risk(persistence):
    summary = _summary_with_steps_and_protocol(persistence)

    text = render_summary(summary, "viewer", get_catalog("en"))

    assert "smoke near the gate" in text
    assert "report_fire_incident" not in text
    assert "surveillance_agent" not in text
    assert "dispatch a drone to the north gate" not in text
    assert "active flame" not in text


def test_render_summary_always_works_without_any_step_or_protocol(persistence):
    event_id = _new_event(persistence)
    record_event_outcome(persistence, event_id, "declined")
    summary = build_run_summary(persistence, event_id)

    text = render_summary(summary, "viewer", get_catalog("en"))

    assert text  # never empty, never raises, even with almost nothing to say


def test_render_summary_includes_failure_reason_only_when_failed(persistence):
    event_id = _new_event(persistence)
    record_event_outcome(persistence, event_id, "failed", failure_reason="attempt limit exhausted")
    summary = build_run_summary(persistence, event_id)

    text = render_summary(summary, "commander", get_catalog("en"))

    assert "attempt limit exhausted" in text


def test_render_summary_commander_pending_approval_includes_risk_detail(persistence):
    event_id = _new_event(persistence)
    selection = ProtocolSelectionResult(status="selected", protocol_name="dispatch_mutual_aid", reason="matches")
    risk = RiskAssessment(score=0.9, level="high", reason="active fire")
    create_approval_hold(persistence, event_id, "flagged_protocol", selection, risk)
    summary = build_run_summary(persistence, event_id)

    text = render_summary(summary, "commander", get_catalog("en"))

    assert "active fire" in text


def test_render_summary_viewer_pending_approval_omits_risk_detail(persistence):
    event_id = _new_event(persistence)
    selection = ProtocolSelectionResult(status="selected", protocol_name="dispatch_mutual_aid", reason="matches")
    risk = RiskAssessment(score=0.9, level="high", reason="active fire")
    create_approval_hold(persistence, event_id, "flagged_protocol", selection, risk)
    summary = build_run_summary(persistence, event_id)

    text = render_summary(summary, "viewer", get_catalog("en"))

    assert "active fire" not in text


def test_render_summary_event_data_pending_shows_the_question_to_both_audiences(persistence):
    event_id = _new_event(persistence)
    create_event_data_hold(persistence, event_id, ("availability_start",), "When do you become available?", ())
    summary = build_run_summary(persistence, event_id)

    for audience in ("viewer", "commander"):
        text = render_summary(summary, audience, get_catalog("en"))
        assert "When do you become available?" in text


def test_render_summary_works_in_hebrew_too(persistence):
    event_id = _new_event(persistence)
    record_event_outcome(persistence, event_id, "succeeded")
    summary = build_run_summary(persistence, event_id)

    text = render_summary(summary, "viewer", get_catalog("he"))

    assert "הצליח" in text
