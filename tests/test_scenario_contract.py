"""Canonical scenario/entity/time contract tests (Task 47)."""

from datetime import datetime, timezone

import pytest

from api.simulations import materialize_simulation
from history import record_initial_event
from history.contracts import InitialEventEnvelope
from history.contracts import ExtractionResult
from persistence import open_persistence
from persistence.surveillance_store import DEMO_CAMERA_SEED
from profiles import (
    SimulationGroup,
    SimulationPersona,
    SimulationScenario,
    SimulationStepContext,
    resolve_simulation_entity,
    resolve_simulation_step,
)
from profiles.loader import load_profile, validate_profile
from config import TierModel
import profiles.unified_test as unified_test
from bot.transports import HttpApiClient, simulation_request_context, current_simulation_context


def test_official_unified_scenarios_preserve_metadata_and_step_times():
    scenarios = {scenario.scenario_id: scenario for scenario in unified_test.SIMULATIONS}
    assert set(scenarios) >= {
        "SEC_001_PHASE_1", "SEC_001_PHASE_2", "SEC_001_PHASE_3",
        "FIRE_002_PHASE_1", "FIRE_002_PHASE_2", "FIRE_002_PHASE_3",
    }
    phase_one = scenarios["SEC_001_PHASE_1"].canonical_raw()
    assert {item["action_type"] for item in phase_one["scenario"]["expected_agent_actions"]} == {
        "DAILY_SUMMARY", "ROUTINE_SITREP"
    }
    assert phase_one["steps"][0]["timestamp"] == "2026-09-06T07:30:00Z"
    assert phase_one["steps"][0]["target_agent"] == "personnel_agent"
    assert phase_one["steps"][0]["source_chat"] == "TELEGRAM_GROUP_RESPONSE_TEAM"
    assert phase_one["event_stream"][0]["payload"]["message"]


def test_materialization_keeps_canonical_metadata_and_reserved_ids():
    scenario = next(item for item in unified_test.SIMULATIONS if item.key == "sec001_phase1")
    materialized = materialize_simulation(scenario, tuple(unified_test.SIMULATION_USERS), tuple(unified_test.SIMULATION_GROUPS))
    assert materialized["scenario"]["id"] == "SEC_001_PHASE_1"
    assert materialized["scenario"]["domain"] == "FIRST_RESPONDERS_TEAM"
    assert materialized["scenario"]["expected_agent_actions"]
    assert materialized["steps"][0]["timestamp"] == "2026-09-06T07:30:00Z"
    assert materialized["steps"][0]["sender_identity"].isdigit()


def test_duplicate_and_invalid_official_timestamps_are_rejected():
    base = {
        "scenario": {"id": "bad"},
        "chats": [{"key": "dm", "kind": "message", "telegram_chat_type": "private"}],
        "steps": [{"step": 1, "chat": "dm", "sender_identity": "p", "text": "x"}],
    }
    scenario = SimulationScenario(
        key="bad", title="bad", raw=base,
        official_metadata={
            "scenario_id": "bad", "event_stream": (
                {"step": 1, "timestamp": "not-a-time", "source_chat": "dm", "payload": {"message": "x"}},
            )
        },
    )
    failures = validate_profile(
        type("Profile", (), {
            "profile_name": "test", "default_language": "en", "max_iter": 1,
            "model_timeout_seconds": 1.0, "agents": (), "protocols": (), "areas": ("x",),
            "simulation_users": (SimulationPersona("p", 0),), "simulation_groups": (),
            "simulations": (scenario,), "simulation_rosters": (),
        })(),
        declared_event_types=["fire"],
    )
    assert any("invalid timestamp" in failure for failure in failures)


def test_simulation_step_context_uses_declared_timestamp_only():
    persona = SimulationPersona("p", 0)
    group = SimulationGroup("g", 0, agent_name="team_status_agent")
    scenario = SimulationScenario(
        key="s", title="s",
        raw={
            "scenario": {"id": "S"},
            "chats": [{"key": "gchat", "kind": "message", "telegram_chat_type": "supergroup", "telegram_chat_id": "g"}],
            "steps": [{"step": 1, "chat": "gchat", "sender_identity": "p", "text": "hello"}],
        },
        official_metadata={
            "scenario_id": "S", "event_stream": (
                {"step": 1, "timestamp": "2026-09-06T07:30:00Z", "target_agent": "personnel_agent", "source_chat": "group", "payload": {"message": "hello"}},
            )
        },
    )
    context = resolve_simulation_step(
        (scenario,), (group,), users=(persona,), scenario_id="S", scenario_step=1,
        sender_identity="9000000000000000", chat_id="-9000000000000000", chat_type="supergroup",
    )
    assert context == SimulationStepContext("S", 1, "2026-09-06T07:30:00Z")

    with pytest.raises(ValueError, match="does not match"):
        resolve_simulation_step(
            (scenario,), (group,), users=(persona,), scenario_id="S", scenario_step=1,
            sender_identity="9000000000000001", chat_id="-9000000000000000", chat_type="supergroup",
        )


def test_entity_resolution_is_exact_and_never_fuzzy():
    aliases = {"03": "CAM-03", "camera 03": "CAM-03", "cam-03": "CAM-03"}
    assert resolve_simulation_entity("Camera 03", domain="surveillance", canonical_ids=("CAM-03",), aliases=aliases).canonical_id == "CAM-03"
    assert resolve_simulation_entity("CAM-99", domain="surveillance", canonical_ids=("CAM-03",), aliases=aliases).status == "unresolved"


def test_official_camera_seed_resolves_cam08_without_substitution():
    camera_ids = tuple(row[0] for row in DEMO_CAMERA_SEED)
    assert {resolve_simulation_entity(f"CAM-{number:02d}", domain="surveillance", canonical_ids=camera_ids).canonical_id
            for number in (2, 3, 4, 5, 8)} == {"CAM-02", "CAM-03", "CAM-04", "CAM-05", "CAM-08"}
    assert resolve_simulation_entity("CAM-99", domain="surveillance", canonical_ids=camera_ids).canonical_id is None


def test_unknown_official_source_chat_is_rejected():
    base = {
        "scenario": {"id": "bad-source"},
        "chats": [{"key": "dm", "kind": "message", "telegram_chat_type": "private"}],
        "steps": [{"step": 1, "chat": "dm", "sender_identity": "p", "text": "x"}],
    }
    scenario = SimulationScenario(
        key="bad-source", title="bad", raw=base,
        official_metadata={
            "scenario_id": "bad-source", "event_stream": (
                {"step": 1, "timestamp": "2026-09-06T07:30:00Z", "source_chat": "UNKNOWN_CHAT",
                 "payload": {"message": "x"}},
            )
        },
    )
    failures = validate_profile(
        type("Profile", (), {
            "profile_name": "test", "default_language": "en", "max_iter": 1,
            "model_timeout_seconds": 1.0, "agents": (), "protocols": (), "areas": ("x",),
            "simulation_users": (SimulationPersona("p", 0),), "simulation_groups": (),
            "simulations": (scenario,), "simulation_rosters": (),
        })(),
        declared_event_types=["fire"],
    )
    assert any("unknown source_chat" in failure for failure in failures)


def test_event_persists_simulation_metadata_without_changing_received_at(tmp_path):
    persistence = open_persistence(str(tmp_path / "events.db"))
    try:
        received = "2026-09-17T12:00:00+00:00"
        event_id = record_initial_event(
            persistence,
            InitialEventEnvelope(
                raw_text="report", source="telegram", received_at=received,
                sender_identity="9000000000000000", source_message_id="sim-1",
                scenario_id="SEC_001_PHASE_1", scenario_step=1,
                scenario_time="2026-09-06T07:30:00Z",
            ),
        )
        event = persistence.fetch_event(event_id)
        assert event["received_at"] == received
        assert event["scenario_id"] == "SEC_001_PHASE_1"
        assert event["scenario_step"] == 1
        assert event["scenario_time"] == "2026-09-06T07:30:00Z"
    finally:
        persistence.close()


def test_simulation_context_adds_trusted_headers_but_is_absent_normally():
    class Response:
        status_code = 200
        content = b"{}"

        @staticmethod
        def json():
            return {}

    class Client:
        def __init__(self):
            self.headers = None

        async def request(self, method, path, *, headers, timeout, **kwargs):
            self.headers = headers
            return Response()

    context = SimulationStepContext("SEC_001_PHASE_1", 1, "2026-09-06T07:30:00Z")
    client = Client()
    api = HttpApiClient("http://simulator")
    api._client = client

    import asyncio

    async def invoke():
        assert current_simulation_context() is None
        await api._call("POST", "/Msg", "sender", {"text": "hello"})
        normal_headers = dict(client.headers)
        with simulation_request_context(context):
            await api._call("POST", "/Msg", "sender", {"text": "hello"})
        return normal_headers, dict(client.headers)

    normal_headers, simulation_headers = asyncio.run(invoke())
    assert "X-Simulation-ID" not in normal_headers
    assert simulation_headers["X-Simulation-Mode"] == "true"
    assert simulation_headers["X-Simulation-ID"] == "SEC_001_PHASE_1"
    assert simulation_headers["X-Simulation-Step"] == "1"
    assert simulation_headers["X-Simulation-Time"] == "2026-09-06T07:30:00Z"


def test_attendance_temporal_resolution_uses_trusted_scenario_reference(monkeypatch):
    import orchestrator.flows as flows

    seen = {}

    def resolver(raw_text, reference_time, timezone_name):
        seen.update(raw_text=raw_text, reference_time=reference_time, timezone_name=timezone_name)
        return type("Period", (), {"availability_start": "start", "availability_end": "end"})()

    monkeypatch.setattr(flows, "resolve_availability_period", resolver)
    extraction = ExtractionResult(
        classification="team_attendance_report", classification_status="resolved", area=None,
        entities=(), description="unavailable", severity=None, occurred_at=None,
        occurred_at_is_fallback=False, missing_fields=(),
    )
    result = flows._apply_attendance_temporal_fields(
        extraction, "attendance", "wall-clock", "Asia/Jerusalem", "scenario-clock"
    )
    assert seen == {
        "raw_text": "attendance", "reference_time": "scenario-clock", "timezone_name": "Asia/Jerusalem"
    }
    assert result.availability_start == "start"
    assert result.availability_end == "end"
