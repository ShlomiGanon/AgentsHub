"""Task 52 coverage for planned surveillance downtime projection."""

import json

from agents import AgentRegistry, AgentResult, SurveillanceAgent
from orchestrator.flows import FlowDeps, begin_report, run_report_extraction
from orchestrator.reasoning import make_operational_intake
from persistence import open_persistence
from profiles import AreaRegistry, EventTypeRegistry, unified_test
from protocols import ProtocolSet


RECEIVED_AT = "2026-09-18T10:00:00+00:00"
STEP_7_MESSAGE = unified_test.get_catalog("he").text(
    "unified.simulation.sec001.phase1.step7.text"
)


def _maintenance_fields(camera_id="CAM-03", *, duration=2, reason="periodic version update", sector="eastern fence, segment 4"):
    return {
        "camera_id": camera_id,
        "camera_status": "offline",
        "shutdown_type": "planned_maintenance",
        "downtime_duration_hours": duration,
        "reason": reason,
        "sector": sector,
        "cause_status": None,
        "possible_cause": None,
    }


def _intake_payload(message=STEP_7_MESSAGE):
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
            "area": "east_fence",
            "entities": ["CAM-03"],
            "description": message,
            "severity": "low",
            "occurred_at": None,
        },
        "business_fields": _maintenance_fields(),
        "risk": {"risk_score": 0.1, "risk_reason": "Planned camera maintenance."},
        "protocol": {
            "status": "selected",
            "name": "query_surveillance_overview",
            "candidate_names": [],
            "reason": "The message reports a surveillance state change.",
        },
        "temporal": {
            "expression": None,
            "availability_start": None,
            "availability_end": None,
        },
    }


class _ScriptedAgent:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def process(self, prompt, allowed_tools, **kwargs):
        self.calls.append((prompt, tuple(allowed_tools), kwargs))
        return AgentResult(status="success", text=json.dumps(self.payload, ensure_ascii=False))


def _event(camera_id="CAM-03", *, description=STEP_7_MESSAGE, **overrides):
    value = {
        "event_id": "task52-maintenance-event",
        "source": "telegram",
        "source_message_id": "task52-step7",
        "classification": "surveillance_report",
        "area": "east_fence",
        "entities": (camera_id,),
        "description": description,
        "occurred_at": None,
        "received_at": RECEIVED_AT,
        "business_fields": _maintenance_fields(camera_id),
        "scenario_id": "SEC_001_PHASE_1",
        "scenario_step": 7,
        "scenario_time": "2026-09-06T10:15:00Z",
    }
    value.update(overrides)
    return value


def test_exact_step_7_single_intake_is_scalar_and_planned_not_action():
    agent = _ScriptedAgent(_intake_payload())

    intake = make_operational_intake(
        agent,
        STEP_7_MESSAGE,
        RECEIVED_AT,
        tuple(unified_test.EVENT_TYPES),
        tuple(unified_test.AREAS),
        tuple(unified_test.PROTOCOLS),
        unified_test.RISK_THRESHOLD,
        unified_test.EVENT_TYPE_BUSINESS_FIELDS,
    )

    assert intake.confident is True
    assert intake.intent.requests_action is False
    assert intake.extraction.business_fields == {
        key: value
        for key, value in _maintenance_fields().items()
        if value is not None
    }
    assert all(value is None or type(value) in {str, int, float, bool} for value in intake.extraction.business_fields.values())
    assert len(agent.calls) == 1


def test_exact_step_7_projection_commits_offline_planned_state_without_action_lifecycle(tmp_path, monkeypatch):
    monkeypatch.setattr(SurveillanceAgent, "surveillance_db_path", str(tmp_path / "surveillance.db"))
    agent = SurveillanceAgent(model="mock")
    before = agent.surveillance_store.get_camera("CAM-03")

    result = agent.ingest_report(_event())
    after = agent.surveillance_store.get_camera("CAM-03")

    assert before["status"] == "active"
    assert result.status == "committed"
    assert result.projection is not None
    assert result.projection.domain == "surveillance"
    assert result.projection.projection_kind == "authoritative_state"
    assert result.projection.scenario_id == "SEC_001_PHASE_1"
    assert result.projection.scenario_step == 7
    assert result.projection.scenario_time == "2026-09-06T10:15:00Z"
    assert result.projection.facts["camera_id"] == "CAM-03"
    assert result.projection.facts["camera_status"] == "offline"
    assert result.projection.facts["shutdown_type"] == "planned_maintenance"
    assert result.projection.facts["downtime_duration_hours"] == 2
    assert result.projection.facts["reason"] == "periodic version update"
    assert result.projection.facts["sector"] == "eastern fence, segment 4"
    assert after["status"] == "offline"
    assert after["area"] == before["area"] == "east_fence"
    assert after["name"] == before["name"]
    assert after["feed_summary"] == STEP_7_MESSAGE


def test_exact_step_7_terminal_flow_has_no_approval_action_or_tool_receipt(tmp_path, monkeypatch):
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
    extracted = {
        "classification": "surveillance_report",
        "area": "east_fence",
        "entities": ["CAM-03"],
        "description": STEP_7_MESSAGE,
        "severity": "low",
        "occurred_at": None,
        "business_fields": _maintenance_fields(),
    }

    try:
        event_id = begin_report(
            deps,
            STEP_7_MESSAGE,
            "telegram",
            RECEIVED_AT,
            "surveillance-operator",
            source_message_id="task52-step7-flow",
        )
        result = run_report_extraction(deps, event_id, _ScriptedAgent(extracted), None)
        event = persistence.fetch_event(event_id)

        assert result.outcome == "succeeded"
        assert event["outcome"] == "succeeded"
        assert event["action_state"] is None
        assert event["steps"] == []
        assert not event["action_tool_receipts"]
        assert persistence.list_held_events("approval") == []
        assert persistence.list_held_events("event_data") == []
        assert surveillance.surveillance_store.get_camera("CAM-03")["status"] == "offline"
    finally:
        persistence.close()


def test_planned_maintenance_is_generic_and_does_not_infer_identity_from_sector(tmp_path, monkeypatch):
    monkeypatch.setattr(SurveillanceAgent, "surveillance_db_path", str(tmp_path / "surveillance.db"))
    agent = SurveillanceAgent(model="mock")
    before_cam03 = agent.surveillance_store.get_camera("CAM-03")

    result = agent.ingest_report(
        _event(
            camera_id="CAM-04",
            business_fields=_maintenance_fields(
                "CAM-04", duration=1, reason="periodic service", sector="east_fence segment 4"
            ),
            area="east_fence",
            entities=("CAM-04",),
        )
    )

    assert result.status == "committed"
    assert result.projection.facts["camera_id"] == "CAM-04"
    assert agent.surveillance_store.get_camera("CAM-04")["status"] == "offline"
    assert agent.surveillance_store.get_camera("CAM-03") == before_cam03


def test_unexpected_offline_remains_distinguishable_from_planned_maintenance(tmp_path, monkeypatch):
    monkeypatch.setattr(SurveillanceAgent, "surveillance_db_path", str(tmp_path / "surveillance.db"))
    agent = SurveillanceAgent(model="mock")
    result = agent.ingest_report(
        _event(
            camera_id="CAM-05",
            description="Camera 05 lost signal unexpectedly.",
            entities=("CAM-05",),
            business_fields={
                **_maintenance_fields("CAM-05"),
                "camera_status": "offline",
                "shutdown_type": None,
                "downtime_duration_hours": None,
                "reason": "unexpected signal loss",
                "sector": None,
            },
        )
    )

    assert result.status == "committed"
    assert result.projection.facts["camera_status"] == "offline"
    assert "shutdown_type" not in result.projection.facts
    assert result.projection.facts["reason"] == "unexpected signal loss"


def test_maintenance_does_not_collapse_degraded_or_active_reports(tmp_path, monkeypatch):
    monkeypatch.setattr(SurveillanceAgent, "surveillance_db_path", str(tmp_path / "surveillance.db"))
    agent = SurveillanceAgent(model="mock")

    degraded = agent.ingest_report(
        _event(
            camera_id="CAM-08",
            area="south_sector",
            entities=("CAM-08",),
            description="Intermittent reception remains reported.",
            business_fields={
                "camera_id": "CAM-08", "camera_status": "degraded",
                "shutdown_type": None, "downtime_duration_hours": None,
                "reason": None, "sector": None,
                "cause_status": "unverified", "possible_cause": "unknown interference",
            },
        )
    )
    active = agent.ingest_report(
        _event(
            camera_id="CAM-01",
            area="north_gate",
            entities=("CAM-01",),
            description="Camera 01 is operational.",
            business_fields={
                "camera_id": "CAM-01", "camera_status": "active",
                "shutdown_type": None, "downtime_duration_hours": None,
                "reason": None, "sector": None,
                "cause_status": None, "possible_cause": None,
            },
        )
    )

    assert degraded.status == "committed"
    assert active.status == "committed"
    assert agent.surveillance_store.get_camera("CAM-08")["status"] == "degraded"
    assert agent.surveillance_store.get_camera("CAM-01")["status"] == "active"


def test_unknown_or_unsupported_maintenance_fields_are_rejected_without_mutation(tmp_path, monkeypatch):
    monkeypatch.setattr(SurveillanceAgent, "surveillance_db_path", str(tmp_path / "surveillance.db"))
    agent = SurveillanceAgent(model="mock")
    before = tuple(agent.surveillance_store.list_cameras())

    unknown_camera = agent.ingest_report(
        _event(
            camera_id="CAM-99",
            entities=("CAM-99",),
            business_fields=_maintenance_fields("CAM-99"),
        )
    )
    unsupported = agent.ingest_report(
        _event(
            business_fields={**_maintenance_fields(), "arbitrary_field": "reject me"},
        )
    )

    assert unknown_camera.status == "rejected"
    assert unsupported.status == "rejected"
    assert tuple(agent.surveillance_store.list_cameras()) == before
