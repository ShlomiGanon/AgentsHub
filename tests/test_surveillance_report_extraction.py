"""Task 51 regression coverage for the canonical surveillance report contract."""

import json

import pytest

from agents import AgentRegistry, AgentResult, SurveillanceAgent
from history import extract_event
from orchestrator.flows import FlowDeps, begin_report, run_report_extraction
from orchestrator.reasoning import OrchestrationParseError, make_operational_intake
from persistence import open_persistence
from profiles import AreaRegistry, EventTypeRegistry, unified_test
from protocols import ProtocolSet


RECEIVED_AT = "2026-09-17T10:00:00+00:00"
STEP_2_MESSAGE = unified_test.get_catalog("he").text("unified.simulation.sec001.phase1.step2.text")


class _ScriptedAgent:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def process(self, prompt, allowed_tools, **kwargs):
        self.calls.append((prompt, tuple(allowed_tools), kwargs))
        return AgentResult(status="success", text=json.dumps(self.payload, ensure_ascii=False))


def _intake_payload(message=STEP_2_MESSAGE, *, business_fields=None):
    return {
        "intent": {
            "value": "report",
            "confident": True,
            "asks_for_information": False,
            "reports_occurrence": True,
            "requests_action": False,
            "social_only": False,
            "is_quoted": False,
            "is_hypothetical": False,
            "evidence": message,
        },
        "classification": {
            "name": "surveillance_report",
            "confident": True,
            "area": "south_sector",
            "entities": ["CAM-08"],
            "description": message,
            "severity": "low",
            "occurred_at": None,
        },
        "business_fields": business_fields or {
            "camera_id": "CAM-08",
            "camera_status": "degraded",
            "shutdown_type": None,
            "downtime_duration_hours": None,
            "reason": None,
            "sector": None,
            "cause_status": "unverified",
            "possible_cause": "branch obstruction or focus problem",
        },
        "risk": {"risk_score": 0.1, "risk_reason": "Reported camera degradation only."},
        "protocol": {
            "status": "selected",
            "name": "query_surveillance_overview",
            "candidate_names": [],
            "reason": "The report belongs to the surveillance domain.",
        },
        "temporal": {"expression": None, "availability_start": None, "availability_end": None},
    }


def test_exact_sec_step_2_passes_single_intake_with_scalar_uncertainty_fields():
    agent = _ScriptedAgent(_intake_payload())

    intake = make_operational_intake(
        agent,
        STEP_2_MESSAGE,
        RECEIVED_AT,
        tuple(unified_test.EVENT_TYPES),
        tuple(unified_test.AREAS),
        tuple(unified_test.PROTOCOLS),
        unified_test.RISK_THRESHOLD,
        unified_test.EVENT_TYPE_BUSINESS_FIELDS,
    )

    assert intake.confident is True
    assert intake.intent.requests_action is False
    assert intake.extraction.classification == "surveillance_report"
    assert intake.extraction.business_fields == {
        "camera_id": "CAM-08",
        "camera_status": "degraded",
        "cause_status": "unverified",
        "possible_cause": "branch obstruction or focus problem",
    }
    assert all(
        value is None or type(value) in {str, int, float, bool}
        for value in intake.extraction.business_fields.values()
    )
    assert len(agent.calls) == 1


def test_nested_or_array_possible_cause_is_rejected_by_the_canonical_schema():
    payload = _intake_payload(
        business_fields={
            "camera_id": "CAM-08",
            "camera_status": "degraded",
            "cause_status": "unverified",
            "possible_cause": ["branch", "focus problem"],
        }
    )

    with pytest.raises(OrchestrationParseError, match="business_fields.possible_cause"):
        make_operational_intake(
            _ScriptedAgent(payload),
            STEP_2_MESSAGE,
            RECEIVED_AT,
            tuple(unified_test.EVENT_TYPES),
            tuple(unified_test.AREAS),
            tuple(unified_test.PROTOCOLS),
            unified_test.RISK_THRESHOLD,
            unified_test.EVENT_TYPE_BUSINESS_FIELDS,
        )


def test_surveillance_ingestion_resolves_exact_aliases_and_preserves_uncertainty(tmp_path, monkeypatch):
    monkeypatch.setattr(SurveillanceAgent, "surveillance_db_path", str(tmp_path / "surveillance.db"))
    agent = SurveillanceAgent(model="mock")
    event = {
        "event_id": "event-step-2",
        "source": "telegram",
        "source_message_id": "step-2",
        "classification": "surveillance_report",
        "area": "south_sector",
        "entities": ("CAM-08",),
        "description": STEP_2_MESSAGE,
        "occurred_at": None,
        "received_at": RECEIVED_AT,
        "business_fields": _intake_payload()["business_fields"],
        "scenario_id": "SEC_001_PHASE_1",
        "scenario_step": 2,
        "scenario_time": "2026-09-06T08:15:00Z",
    }

    result = agent.ingest_report(event)

    assert result.status == "committed"
    assert result.projection is not None
    assert result.projection.projection_kind == "authoritative_state"
    assert result.projection.facts["camera_id"] == "CAM-08"
    assert result.projection.facts["camera_status"] == "degraded"
    assert result.projection.facts["cause_status"] == "unverified"
    assert result.projection.facts["possible_cause"] == "branch obstruction or focus problem"
    assert result.projection.scenario_step == 2
    assert agent.surveillance_store.get_camera("CAM-08")["status"] == "degraded"

    for alias in ("מצלמה 08", "camera 08", "CAM-08"):
        assert agent._resolve_camera_reference(alias) == "CAM-08"


def test_known_camera_degradation_is_generic_and_unknown_camera_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(SurveillanceAgent, "surveillance_db_path", str(tmp_path / "surveillance.db"))
    agent = SurveillanceAgent(model="mock")

    known = agent.ingest_report(
        {
            "classification": "surveillance_report",
            "entities": ("CAM-02",),
            "description": "Camera 02 shows intermittent reception.",
            "business_fields": {
                "camera_id": "camera 02",
                "camera_status": "degraded",
                "cause_status": "unverified",
                "possible_cause": "unknown interference",
            },
        }
    )
    assert known.status == "committed"
    assert agent.surveillance_store.get_camera("CAM-02")["status"] == "degraded"

    before = tuple(agent.surveillance_store.list_cameras())
    unknown = agent.ingest_report(
        {
            "classification": "surveillance_report",
            "entities": ("CAM-99",),
            "description": "Camera 99 shows intermittent reception.",
            "business_fields": {
                "camera_id": "CAM-99",
                "camera_status": "degraded",
                "cause_status": "unverified",
                "possible_cause": None,
            },
        }
    )
    assert unknown.status == "rejected"
    assert agent.surveillance_store.get_camera("CAM-99") is None
    assert tuple(agent.surveillance_store.list_cameras()) == before


def test_exact_sec_step_2_is_a_terminal_report_without_action_lifecycle(tmp_path, monkeypatch):
    monkeypatch.setattr(SurveillanceAgent, "surveillance_db_path", str(tmp_path / "surveillance.db"))
    surveillance = SurveillanceAgent(model="mock")
    persistence = open_persistence(str(tmp_path / "history.db"))
    deps = FlowDeps(
        persistence=persistence,
        settings_store=None,
        registry=AgentRegistry({"surveillance_agent": surveillance}),
        protocol_set=ProtocolSet(tuple(unified_test.PROTOCOLS)),
        event_type_registry=EventTypeRegistry(tuple(unified_test.EVENT_TYPES)),
        area_registry=AreaRegistry(tuple(unified_test.AREAS)),
        history_query_service=None,
        group_owner="surveillance_agent",
        event_type_business_fields=unified_test.EVENT_TYPE_BUSINESS_FIELDS,
    )
    extractor = _ScriptedAgent(
        {
            "classification": "surveillance_report",
            "area": "south_sector",
            "entities": ["CAM-08"],
            "description": STEP_2_MESSAGE,
            "severity": "low",
            "occurred_at": None,
            "business_fields": _intake_payload()["business_fields"],
        }
    )

    try:
        event_id = begin_report(deps, STEP_2_MESSAGE, "telegram", RECEIVED_AT, "surveillance-operator")
        result = run_report_extraction(deps, event_id, extractor, None)
        event = persistence.fetch_event(event_id)

        assert result.outcome == "succeeded"
        assert event["classification"] == "surveillance_report"
        assert event["outcome"] == "succeeded"
        assert event["action_state"] is None
        assert event["steps"] == []
        assert persistence.list_held_events("approval") == []
        assert persistence.list_held_events("event_data") == []
        assert surveillance.surveillance_store.get_camera("CAM-08")["status"] == "degraded"
    finally:
        persistence.close()


def test_legacy_extraction_uses_the_same_declared_scalar_business_fields():
    result = extract_event(
        STEP_2_MESSAGE,
        "telegram",
        RECEIVED_AT,
        EventTypeRegistry(("surveillance_report",)),
        AreaRegistry(("south_sector",)),
        lambda prompt: json.dumps(
            {
                "classification": "surveillance_report",
                "area": "south_sector",
                "entities": ["CAM-08"],
                "description": STEP_2_MESSAGE,
                "severity": "low",
                "occurred_at": None,
                "business_fields": _intake_payload()["business_fields"],
            },
            ensure_ascii=False,
        ),
        event_type_business_fields=unified_test.EVENT_TYPE_BUSINESS_FIELDS,
    )

    assert result.business_fields["cause_status"] == "unverified"
    assert isinstance(result.business_fields["possible_cause"], str)


def test_declared_hebrew_camera_alias_is_exactly_resolved(tmp_path, monkeypatch):
    monkeypatch.setattr(SurveillanceAgent, "surveillance_db_path", str(tmp_path / "surveillance.db"))
    agent = SurveillanceAgent(model="mock")
    assert agent._resolve_camera_reference("\u05de\u05e6\u05dc\u05de\u05d4 08") == "CAM-08"
