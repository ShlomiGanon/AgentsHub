import json
from pathlib import Path

from agents import AgentRegistry, AgentResult, FriendlyForcesAgent
from agents.surveillance_agent import SurveillanceAgent
from agents.team_status_agent import TeamStatusAgent
from history import ExtractionResult
from orchestrator.flows import (
    FlowDeps,
    _normalize_group_owned_extraction,
    begin_report,
    continue_fast_path_report,
    prepare_fast_path_report,
)
from persistence import OperationalScope
from profiles import AreaRegistry, EventTypeRegistry, OptimizationPolicy, unified_test
from protocols import ProtocolSet
import pytest


def _fixture_message(scenario_id: str, step: int) -> str:
    filename = unified_test._OFFICIAL_FIXTURES[scenario_id]
    payload = json.loads((Path("fixtures") / "admin_scenarios" / filename).read_text(encoding="utf-8"))
    item = next(entry for entry in payload["event_stream"] if entry["step"] == step)
    return item["payload"]["message"]


def _extraction(classification: str, message: str, fields: dict[str, object], *, status: str = "resolved"):
    return ExtractionResult(
        classification=classification,
        classification_status=status,
        area=None,
        entities=(),
        description=message,
        severity="high",
        occurred_at="2026-09-09T13:00:00+00:00",
        occurred_at_is_fallback=False,
        missing_fields=(),
        business_fields=fields,
    )


def _normalize_deps(owner_name: str, owner) -> FlowDeps:
    return FlowDeps(
        persistence=None,
        settings_store=None,
        registry=AgentRegistry({owner_name: owner}),
        protocol_set=None,
        event_type_registry=EventTypeRegistry(tuple(unified_test.EVENT_TYPES)),
        area_registry=AreaRegistry(tuple(unified_test.AREAS)),
        history_query_service=None,
        event_type_business_fields=unified_test.EVENT_TYPE_BUSINESS_FIELDS,
        group_owner=owner_name,
    )


def test_team_operational_fixture_reports_cannot_become_attendance():
    owner = TeamStatusAgent.__new__(TeamStatusAgent)
    message = _fixture_message("SEC_001_PHASE_3", 1)
    result = _normalize_group_owned_extraction(
        _normalize_deps("team_status_agent", owner),
        _extraction(
            "team_attendance_report",
            message,
            {"availability": "unavailable", "reason": "reported incident", "uncertainty": "unverified"},
        ),
    )

    assert result.classification == "team_operational_report"
    assert result.classification != "team_attendance_report"
    assert result.business_fields == {"uncertainty": "unverified"}


class _DeclaredOwner:
    def __init__(self, name, owned_report_types, default_report_type):
        self.name = name
        self.owned_report_types = owned_report_types
        self.default_report_type = default_report_type


@pytest.mark.parametrize(
    ("scenario_id", "step", "owner_name", "owned_types", "expected"),
    (
        ("SEC_001_PHASE_1", 2, "surveillance_agent", ("surveillance_report", "operational_condition_report"), "surveillance_report"),
        ("SEC_001_PHASE_2", 2, "friendly_forces_agent", ("friendly_forces_report",), "friendly_forces_report"),
        ("SEC_001_PHASE_3", 1, "team_status_agent", ("team_operational_report", "team_attendance_report"), "team_operational_report"),
        ("FIRE_002_PHASE_1", 6, "friendly_forces_agent", ("friendly_forces_report",), "friendly_forces_report"),
        ("FIRE_002_PHASE_2", 5, "team_status_agent", ("team_operational_report", "team_attendance_report"), "team_operational_report"),
        ("FIRE_002_PHASE_3", 1, "team_status_agent", ("team_operational_report", "team_attendance_report"), "team_operational_report"),
    ),
)
def test_official_cross_domain_reports_are_not_attendance(
    scenario_id, step, owner_name, owned_types, expected
):
    owner = _DeclaredOwner(owner_name, owned_types, expected)
    result = _normalize_group_owned_extraction(
        _normalize_deps(owner_name, owner),
        _extraction(
            "team_attendance_report",
            _fixture_message(scenario_id, step),
            {"availability": "unavailable", "reason": "classifier drift"},
        ),
    )

    assert result.classification == expected
    assert result.classification != "team_attendance_report" or owner_name == "team_status_agent" and expected == "team_attendance_report"


def test_fire_phase_two_team_report_is_a_fact_without_attendance_mutation(tmp_path, monkeypatch):
    monkeypatch.setattr(TeamStatusAgent, "status_db_path", str(tmp_path / "team.db"))
    team = TeamStatusAgent("mock")
    message = _fixture_message("FIRE_002_PHASE_2", 5)
    event = {
        "event_id": "fire-phase2-step5",
        "classification": "team_operational_report",
        "description": message,
        "business_fields": {"uncertainty": "reported", "location": "קו החורש"},
        "raw_text": message,
        "received_at": "2026-09-09T12:42:00+00:00",
    }

    result = team.ingest_report(event, scope=OperationalScope.live())

    assert result.status == "committed"
    assert result.projection is not None
    assert result.projection.projection_kind == "operational_fact"
    assert team.status_store.availability_snapshot("2026-09-09T12:50:00+00:00", scope=OperationalScope.live()) == []


def test_friendly_normalization_drops_unknown_fields_but_keeps_canonical_context():
    message = _fixture_message("FIRE_002_PHASE_1", 6)
    result = _normalize_group_owned_extraction(
        _normalize_deps("friendly_forces_agent", FriendlyForcesAgent("mock")),
        _extraction(
            "friendly_forces_report",
            message,
            {
                "incident_kind": "brush_fire",
                "location": "Route 444",
                "cause_status": "unverified",
                "resource_mention": "police patrol",
                "generated_model_field": {"not": "scalar"},
            },
        ),
    )

    assert result.classification == "friendly_forces_report"
    assert result.business_fields == {
        "incident_kind": "brush_fire",
        "location": "Route 444",
        "cause_status": "unverified",
        "resource_mention": "police patrol",
    }
    assert "generated_model_field" not in result.business_fields


def test_surveillance_status_alias_is_normalized_to_canonical_camera_status(tmp_path, monkeypatch):
    monkeypatch.setattr(SurveillanceAgent, "surveillance_db_path", str(tmp_path / "surveillance.db"))
    surveillance = SurveillanceAgent("mock")
    result = _normalize_group_owned_extraction(
        _normalize_deps("surveillance_agent", surveillance),
        _extraction(
            "surveillance_report",
            _fixture_message("SEC_001_PHASE_1", 2),
            {
                "camera_id": "CAM-08",
                "status": "degraded",
                "cause_status": "unverified",
                "possible_cause": "branch obstruction",
                "generated_model_field": "discard",
            },
        ),
    )

    assert result.classification == "surveillance_report"
    assert result.business_fields["camera_status"] == "degraded"
    assert "status" not in result.business_fields
    assert "generated_model_field" not in result.business_fields


class _ScriptedIntakeAgent:
    def __init__(self, payload):
        self.payload = payload

    def process(self, prompt, allowed_tools, *, invocation_policy=None):
        return AgentResult("success", json.dumps(self.payload, ensure_ascii=False))


class _Settings:
    def get_risk_threshold(self):
        return 0.6


def _wrong_attendance_payload(message: str):
    return {
        "intent": {
            "value": "report", "confident": True, "asks_for_information": False,
            "reports_occurrence": True, "requests_action": False, "social_only": False,
            "is_quoted": False, "is_hypothetical": False, "evidence": message,
        },
        "classification": {
            "name": "team_attendance_report", "confident": True, "area": "readiness_team",
            "entities": [], "description": message, "severity": "high", "occurred_at": None,
        },
        "business_fields": {"availability": "unavailable", "reason": "operational report"},
        "risk": {"risk_score": 0.2, "risk_reason": "reported fact"},
        "protocol": {
            "status": "selected", "name": "record_attendance_response",
            "candidate_names": [], "reason": "classifier selected attendance",
        },
        "temporal": {"expression": None, "availability_start": None, "availability_end": None},
    }


def test_group_owned_report_is_normalized_before_protocol_execution(tmp_path, monkeypatch):
    monkeypatch.setattr(TeamStatusAgent, "status_db_path", str(tmp_path / "team.db"))
    team = TeamStatusAgent("mock")
    message = _fixture_message("FIRE_002_PHASE_3", 1)
    deps = FlowDeps(
        persistence=None,
        settings_store=_Settings(),
        registry=AgentRegistry({"team_status_agent": team}),
        protocol_set=ProtocolSet(tuple(unified_test.PROTOCOLS)),
        event_type_registry=EventTypeRegistry(tuple(unified_test.EVENT_TYPES)),
        area_registry=AreaRegistry(tuple(unified_test.AREAS)),
        history_query_service=None,
        optimization_policy=OptimizationPolicy(operational_intake_mode="single", deterministic_execution_mode="direct"),
        event_type_business_fields=unified_test.EVENT_TYPE_BUSINESS_FIELDS,
        group_owner="team_status_agent",
    )

    plan = prepare_fast_path_report(
        deps,
        _ScriptedIntakeAgent(_wrong_attendance_payload(message)),
        message,
        "2026-09-09T13:00:00+00:00",
        False,
    )

    assert plan is not None
    assert plan.domain_only is True
    assert plan.extraction.classification == "team_operational_report"
    assert plan.extraction.classification != "team_attendance_report"


def test_group_owned_question_and_action_do_not_become_passive_reports(tmp_path, monkeypatch):
    monkeypatch.setattr(TeamStatusAgent, "status_db_path", str(tmp_path / "team.db"))
    team = TeamStatusAgent("mock")
    message = _fixture_message("SEC_001_PHASE_1", 2)
    deps = FlowDeps(
        persistence=None,
        settings_store=_Settings(),
        registry=AgentRegistry({"team_status_agent": team}),
        protocol_set=ProtocolSet(tuple(unified_test.PROTOCOLS)),
        event_type_registry=EventTypeRegistry(tuple(unified_test.EVENT_TYPES)),
        area_registry=AreaRegistry(tuple(unified_test.AREAS)),
        history_query_service=None,
        optimization_policy=OptimizationPolicy(operational_intake_mode="single", deterministic_execution_mode="direct"),
        event_type_business_fields=unified_test.EVENT_TYPE_BUSINESS_FIELDS,
        group_owner="team_status_agent",
    )

    question = _wrong_attendance_payload(message)
    question["intent"].update(
        value="question", asks_for_information=True, reports_occurrence=False, requests_action=False
    )
    assert prepare_fast_path_report(
        deps, _ScriptedIntakeAgent(question), message, "2026-09-09T08:15:00+00:00", False
    ) is None

    action = _wrong_attendance_payload(message)
    action["intent"].update(
        value="request", asks_for_information=False, reports_occurrence=False, requests_action=True
    )
    assert prepare_fast_path_report(
        deps, _ScriptedIntakeAgent(action), message, "2026-09-09T08:15:00+00:00", False
    ) is None
