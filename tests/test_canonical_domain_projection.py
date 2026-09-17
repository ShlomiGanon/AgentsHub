"""Canonical typed report projection and domain-boundary regressions (Task 48)."""

from agents import FriendlyForcesAgent, SurveillanceAgent


def _event(**overrides):
    value = {
        "event_id": "event-1",
        "source": "telegram",
        "source_message_id": "message-1",
        "classification": "surveillance_report",
        "area": "south_sector",
        "entities": ("CAM-08",),
        "description": "Intermittent reception interference.",
        "occurred_at": None,
        "scenario_id": "SEC_001_PHASE_1",
        "scenario_step": 2,
        "scenario_time": "2026-09-06T08:15:00Z",
        "business_fields": {"camera_status": "degraded"},
    }
    value.update(overrides)
    return value


def test_friendly_report_returns_typed_projection_without_action_receipt():
    agent = FriendlyForcesAgent(model="mock")
    result = agent.ingest_report(
        _event(
            classification="friendly_forces_report",
            entities=("ATV-7",),
            description="Stolen vehicle reported by the external-forces group.",
            business_fields={"incident_kind": "stolen_vehicle"},
        )
    )

    assert result.status == "committed"
    assert result.projection is not None
    assert result.projection.domain == "friendly_forces"
    assert result.projection.status == "committed"
    assert result.projection.projection_kind == "operational_fact"
    assert result.projection.scenario_id == "SEC_001_PHASE_1"
    assert result.projection.scenario_step == 2
    assert result.projection.scenario_time == "2026-09-06T08:15:00Z"
    assert result.projection.facts == {"incident_kind": "stolen_vehicle"}


def test_surveillance_projection_resolves_declared_camera_aliases(tmp_path):
    SurveillanceAgent.surveillance_db_path = str(tmp_path / "surveillance.db")
    agent = SurveillanceAgent(model="mock")
    result = agent.ingest_report(
        _event(
            entities=(),
            business_fields={"camera_id": "08", "camera_status": "degraded"},
        )
    )

    assert result.status == "committed"
    assert result.projection is not None
    assert result.projection.facts["camera_id"] == "CAM-08"
    assert result.projection.facts["camera_status"] == "degraded"
    assert agent.surveillance_store.get_camera("CAM-08")["status"] == "degraded"


def test_surveillance_unknown_camera_is_rejected_without_mutating_seed(tmp_path):
    SurveillanceAgent.surveillance_db_path = str(tmp_path / "surveillance.db")
    agent = SurveillanceAgent(model="mock")
    result = agent.ingest_report(
        _event(
            entities=(),
            business_fields={"camera_id": "CAM-99", "camera_status": "degraded"},
        )
    )

    assert result.status == "rejected"
    assert result.projection is None
    assert agent.surveillance_store.get_camera("CAM-08")["status"] == "active"
