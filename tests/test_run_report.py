"""orchestrator/run_report.py — RunSummary builder and its deterministic renderer."""

import pytest

from dataclasses import replace

from history.contracts import InitialEventEnvelope, StepExecutionEnvelope
from history.event_pipeline import record_event_outcome, record_initial_event, record_step_execution
from messages import get_catalog
from orchestrator.holds import create_approval_hold, create_clarification_hold, create_event_data_hold
from orchestrator.reasoning import ProtocolSelectionResult, RiskAssessment
from orchestrator.run_report import build_run_summary, render_summary, resolve_audience
from persistence import open_persistence


@pytest.fixture
def persistence(tmp_path):
    """Persistence."""
    store = open_persistence(str(tmp_path / "run_report_test.db"))
    yield store
    store.close()


def _new_event(persistence, **overrides) -> str:
    """New event."""
    envelope = InitialEventEnvelope(
        raw_text="a fire was seen near the north gate",
        source="telegram",
        received_at="2026-08-24T10:00:00",
        sender_identity="viewer-1",
        sender_permission_level=overrides.pop("sender_permission_level", "viewer"),
        telegram_chat_type=overrides.pop("telegram_chat_type", None),
    )
    event_id = record_initial_event(persistence, envelope)
    if overrides:
        persistence.update_event(event_id, overrides)
    return event_id


# -- build_run_summary --------------------------------------------------------


def test_build_run_summary_reads_the_understood_fields_from_the_event(persistence):
    """Build run summary reads the understood fields from the event."""
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
    """Build run summary raises for an unknown event id."""
    with pytest.raises(ValueError):
        build_run_summary(persistence, "does-not-exist")


def test_build_run_summary_includes_every_persisted_step_in_order(persistence):
    """Build run summary includes every persisted step in order."""
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
    """Build run summary picks up an unresolved approval hold."""
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
    """Build run summary picks up an unresolved clarification hold."""
    event_id = _new_event(persistence)
    create_clarification_hold(persistence, event_id, "raw text")

    summary = build_run_summary(persistence, event_id)

    assert summary.pending is not None
    assert summary.pending.kind == "clarification"
    assert summary.pending.unresolved_field == "classification"


def test_build_run_summary_picks_up_an_unresolved_event_data_hold(persistence):
    """Build run summary picks up an unresolved event data hold."""
    event_id = _new_event(persistence)
    create_event_data_hold(persistence, event_id, ("availability_start",), "When do you become available?", ())

    summary = build_run_summary(persistence, event_id)

    assert summary.pending is not None
    assert summary.pending.kind == "event_data"
    assert summary.pending.missing_fields == ("availability_start",)
    assert summary.pending.question == "When do you become available?"


def test_build_run_summary_has_no_pending_once_the_hold_is_resolved(persistence):
    """Build run summary has no pending once the hold is resolved."""
    event_id = _new_event(persistence)
    hold_id = create_clarification_hold(persistence, event_id, "raw text")
    persistence.resolve_held_event("clarification", hold_id, {"resolved_by": "commander-1", "chosen_classification": "fire"})

    summary = build_run_summary(persistence, event_id)

    assert summary.pending is None


# -- render_summary ------------------------------------------------------------


def _summary_with_steps_and_protocol(persistence):
    """Summary with steps and protocol."""
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
    """Render summary commander includes protocol agent task and result."""
    summary = _summary_with_steps_and_protocol(persistence)

    text = render_summary(summary, "commander", get_catalog("en"))

    assert "smoke near the gate" in text
    assert "report_fire_incident" in text
    assert "surveillance_agent" in text
    assert "dispatch a drone to the north gate" in text
    assert "drone Eagle-1 dispatched" in text
    assert "high" in text


def test_render_summary_viewer_omits_protocol_agent_task_and_risk(persistence):
    """Render summary viewer omits protocol agent task and risk."""
    summary = _summary_with_steps_and_protocol(persistence)

    text = render_summary(summary, "viewer", get_catalog("en"))

    assert "smoke near the gate" in text
    assert "report_fire_incident" not in text
    assert "surveillance_agent" not in text
    assert "dispatch a drone to the north gate" not in text
    assert "active flame" not in text


def test_render_summary_shows_the_resource_unavailable_fact_to_a_viewer(persistence):
    # Unlike insight_text (commander-only), this fact must reach every audience.
    """Render summary shows the resource unavailable fact to a viewer."""
    summary = _summary_with_steps_and_protocol(persistence)
    summary = replace(summary, resource_unavailable_fact="a drone could not be dispatched to the north gate")

    text = render_summary(summary, "viewer", get_catalog("en"))

    assert "a drone could not be dispatched to the north gate" in text


def test_render_summary_shows_the_resource_unavailable_fact_to_a_commander_too(persistence):
    """Render summary shows the resource unavailable fact to a commander too."""
    summary = _summary_with_steps_and_protocol(persistence)
    summary = replace(summary, resource_unavailable_fact="a drone could not be dispatched to the north gate")

    text = render_summary(summary, "commander", get_catalog("en"))

    assert "a drone could not be dispatched to the north gate" in text


def test_render_summary_omits_the_resource_unavailable_line_when_not_set(persistence):
    """Render summary omits the resource unavailable line when not set."""
    summary = _summary_with_steps_and_protocol(persistence)
    assert summary.resource_unavailable_fact is None

    text = render_summary(summary, "viewer", get_catalog("en"))

    assert text  # unaffected, no crash, no stray blank section


def test_render_summary_always_works_without_any_step_or_protocol(persistence):
    """Render summary always works without any step or protocol."""
    event_id = _new_event(persistence)
    record_event_outcome(persistence, event_id, "declined")
    summary = build_run_summary(persistence, event_id)

    text = render_summary(summary, "viewer", get_catalog("en"))

    assert text  # never empty, never raises, even with almost nothing to say


def test_render_summary_includes_failure_reason_only_when_failed(persistence):
    """Render summary includes failure reason only when failed."""
    event_id = _new_event(persistence)
    record_event_outcome(persistence, event_id, "failed", failure_reason="attempt limit exhausted")
    summary = build_run_summary(persistence, event_id)

    text = render_summary(summary, "commander", get_catalog("en"))

    assert "attempt limit exhausted" in text


def test_render_summary_commander_pending_approval_includes_risk_detail(persistence):
    """Render summary commander pending approval includes risk detail."""
    event_id = _new_event(persistence)
    selection = ProtocolSelectionResult(status="selected", protocol_name="dispatch_mutual_aid", reason="matches")
    risk = RiskAssessment(score=0.9, level="high", reason="active fire")
    create_approval_hold(persistence, event_id, "flagged_protocol", selection, risk)
    summary = build_run_summary(persistence, event_id)

    text = render_summary(summary, "commander", get_catalog("en"))

    assert "active fire" in text


def test_render_summary_viewer_pending_approval_omits_risk_detail(persistence):
    """Render summary viewer pending approval omits risk detail."""
    event_id = _new_event(persistence)
    selection = ProtocolSelectionResult(status="selected", protocol_name="dispatch_mutual_aid", reason="matches")
    risk = RiskAssessment(score=0.9, level="high", reason="active fire")
    create_approval_hold(persistence, event_id, "flagged_protocol", selection, risk)
    summary = build_run_summary(persistence, event_id)

    text = render_summary(summary, "viewer", get_catalog("en"))

    assert "active fire" not in text


def test_render_summary_event_data_pending_shows_the_question_to_both_audiences(persistence):
    """Render summary event data pending shows the question to both audiences."""
    event_id = _new_event(persistence)
    create_event_data_hold(persistence, event_id, ("availability_start",), "When do you become available?", ())
    summary = build_run_summary(persistence, event_id)

    for audience in ("viewer", "commander"):
        text = render_summary(summary, audience, get_catalog("en"))
        assert "When do you become available?" in text


def test_render_summary_works_in_hebrew_too(persistence):
    """Render summary works in hebrew too."""
    event_id = _new_event(persistence)
    record_event_outcome(persistence, event_id, "succeeded")
    summary = build_run_summary(persistence, event_id)

    text = render_summary(summary, "viewer", get_catalog("he"))

    assert "הצליח" in text


# -- resolve_audience ----------------------------------------------------------


def test_resolve_audience_is_viewer_for_a_group_chat_even_when_the_sender_is_a_commander(persistence):
    """Resolve audience is viewer for a group chat even when the sender is a commander."""
    event_id = _new_event(persistence, sender_permission_level="commander", telegram_chat_type="supergroup")
    summary = build_run_summary(persistence, event_id)

    assert resolve_audience(summary) == "viewer"


def test_resolve_audience_is_viewer_for_a_plain_group_chat_type_too(persistence):
    """Resolve audience is viewer for a plain group chat type too."""
    event_id = _new_event(persistence, sender_permission_level="commander", telegram_chat_type="group")
    summary = build_run_summary(persistence, event_id)

    assert resolve_audience(summary) == "viewer"


def test_resolve_audience_is_commander_for_a_private_chat_with_a_commander(persistence):
    """Resolve audience is commander for a private chat with a commander."""
    event_id = _new_event(persistence, sender_permission_level="commander", telegram_chat_type="private")
    summary = build_run_summary(persistence, event_id)

    assert resolve_audience(summary) == "commander"


def test_resolve_audience_is_viewer_for_a_private_chat_with_a_viewer(persistence):
    """Resolve audience is viewer for a private chat with a viewer."""
    event_id = _new_event(persistence, sender_permission_level="viewer", telegram_chat_type="private")
    summary = build_run_summary(persistence, event_id)

    assert resolve_audience(summary) == "viewer"


def test_resolve_audience_falls_back_to_sender_level_when_chat_type_is_unknown(persistence):
    # No telegram_chat_type recorded at all (e.g. a non-Telegram "sensor" source, or an event
    # that predates this column) — treated like a private chat, not a group.
    """Resolve audience falls back to sender level when chat type is unknown."""
    event_id = _new_event(persistence, sender_permission_level="commander")
    summary = build_run_summary(persistence, event_id)

    assert resolve_audience(summary) == "commander"
