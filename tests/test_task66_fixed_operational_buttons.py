"""Task 66 regression coverage for deterministic fixed operational-state controls."""

from dataclasses import dataclass, replace

import pytest

from agents.runtime import AgentRegistry
from api.app import build_app
from history.query import HistoryQueryService
from messages import get_catalog, set_current_catalog
from orchestrator.fixed_state import read_fixed_operational_state
from persistence import OperationalScope, current_operational_scope
from protocols import CriticalityLevel, Protocol
from protocols.loader import ProtocolSet
from tests.api_fakes import VIEWER_IDENTITY, auth_headers, build_context, happy_path_agent


LIVE = OperationalScope.live()
SIMULATION = OperationalScope.simulation("SEC_001_PHASE_1", "task66-run")


class _State:
    def __init__(self):
        self.team = {LIVE.key: "team-live-a", SIMULATION.key: "team-sim-a"}
        self.cameras = {
            LIVE.key: [{"camera_id": "CAM-L", "area": "north", "status": "active"}],
            SIMULATION.key: [{"camera_id": "CAM-S", "area": "south", "status": "offline"}],
        }
        self.drones = {
            LIVE.key: [{"drone_id": "DR-L", "status": "ready", "assigned_mission_id": None}],
            SIMULATION.key: [{"drone_id": "DR-S", "status": "charging", "assigned_mission_id": None}],
        }
        self.missions = {LIVE.key: [], SIMULATION.key: []}
        self.events = {
            LIVE.key: [{"description": "live event A", "received_at": "2026-09-22T10:00:00+00:00"}],
            SIMULATION.key: [{"description": "simulation event A", "received_at": "2026-09-22T10:00:00+00:00"}],
        }


class _TeamStore:
    def __init__(self, state):
        self.state = state
        self.scopes = []

    def availability_snapshot(self, _as_of, *, scope=None):
        resolved = scope or current_operational_scope()
        self.scopes.append(resolved.key)
        return [{"telegram_identity": resolved.key, "availability": "available"}]

    def operational_state(self, *, scope=None):
        return None


class _SurveillanceStore:
    def __init__(self, state):
        self.state = state
        self.scopes = []

    def list_cameras(self, area=None, *, scope=None):
        resolved = scope or current_operational_scope()
        self.scopes.append(resolved.key)
        return list(self.state.cameras[resolved.key])

    def list_drones(self, *, scope=None):
        resolved = scope or current_operational_scope()
        self.scopes.append(resolved.key)
        return list(self.state.drones[resolved.key])

    def get_active_missions(self, *, scope=None):
        resolved = scope or current_operational_scope()
        self.scopes.append(resolved.key)
        return list(self.state.missions[resolved.key])


class _TeamAgent:
    name = "team_status_agent"

    def __init__(self, state, store):
        self.state = state
        self.status_store = store
        self.calls = 0

    def report_team_availability(self, *, view):
        assert view == "summary"
        self.calls += 1
        return self.state.team[current_operational_scope().key]

    def process(self, *_args, **_kwargs):
        raise AssertionError("fixed team button must not call Agent.process")


class _SurveillanceAgent:
    name = "surveillance_agent"

    def __init__(self, state, store):
        self.state = state
        self.surveillance_store = store
        self.calls = []

    def get_camera_feeds(self):
        scope = current_operational_scope().key
        self.calls.append(("camera", scope))
        return ",".join(camera["status"] for camera in self.state.cameras[scope])

    def get_drone_fleet_status(self):
        scope = current_operational_scope().key
        self.calls.append(("drone", scope))
        return ",".join(drone["status"] for drone in self.state.drones[scope])

    def get_active_missions(self):
        scope = current_operational_scope().key
        self.calls.append(("mission", scope))
        return f"missions={len(self.state.missions[scope])}"

    def process(self, *_args, **_kwargs):
        raise AssertionError("fixed surveillance button must not call Agent.process")


class _History:
    def __init__(self, state):
        self.state = state
        self.calls = []

    def recent_committed_events(self, *, scenario_id=None, scenario_run_id=None, **_kwargs):
        scope = SIMULATION if scenario_id and scenario_run_id else LIVE
        self.calls.append(scope.key)
        return tuple(self.state.events[scope.key])


@dataclass
class _Fixture:
    state: _State
    registry: AgentRegistry
    history: _History
    team_store: _TeamStore
    surveillance_store: _SurveillanceStore


@pytest.fixture
def fixed_fixture():
    set_current_catalog(get_catalog("en"))
    state = _State()
    team_store = _TeamStore(state)
    surveillance_store = _SurveillanceStore(state)
    registry = AgentRegistry(
        {
            "team_status_agent": _TeamAgent(state, team_store),
            "surveillance_agent": _SurveillanceAgent(state, surveillance_store),
        }
    )
    return _Fixture(state, registry, _History(state), team_store, surveillance_store)


@pytest.fixture
def teardown_ctx():
    contexts = []
    yield contexts
    for ctx in contexts:
        ctx.queue.stop()
        ctx.deps.persistence.close()


def _read(fixture, protocol_name, scope=LIVE):
    return read_fixed_operational_state(
        protocol_name,
        registry=fixture.registry,
        history_query_service=fixture.history,
        messages=get_catalog("en"),
        operational_scope=scope,
        scenario_time=None,
        sender_identity_filter=None,
    )


def test_team_camera_drone_and_history_buttons_reread_authoritative_state(fixed_fixture):
    fixture = fixed_fixture

    assert _read(fixture, "report_team_availability").answer == "team-live-a"
    fixture.state.team[LIVE.key] = "team-live-b"
    assert _read(fixture, "report_team_availability").answer == "team-live-b"

    assert _read(fixture, "query_camera_status").answer == "active"
    fixture.state.cameras[LIVE.key][0]["status"] = "degraded"
    assert _read(fixture, "query_camera_status").answer == "degraded"
    fixture.state.cameras[LIVE.key][0]["status"] = "maintenance"
    assert _read(fixture, "query_camera_status").answer == "maintenance"

    assert "ready" in _read(fixture, "query_drone_fleet_status").answer
    fixture.state.drones[LIVE.key][0]["status"] = "charging"
    assert "charging" in _read(fixture, "query_drone_fleet_status").answer

    assert "live event A" in _read(fixture, "query_historical_incidents").answer
    fixture.state.events[LIVE.key].append(
        {"description": "live event B", "received_at": "2026-09-22T11:00:00+00:00"}
    )
    assert "live event B" in _read(fixture, "query_historical_incidents").answer


def test_overall_button_rebuilds_current_typed_snapshot_without_model_prose(fixed_fixture):
    fixture = fixed_fixture

    first = _read(fixture, "overall_situational_picture")
    assert "Cameras: 1/1 active" in first.answer
    assert "Drones: 1 ready; 0 airborne; 0 charging" in first.answer

    fixture.state.cameras[LIVE.key][0]["status"] = "offline"
    fixture.state.drones[LIVE.key][0]["status"] = "charging"
    fixture.state.events[LIVE.key].append(
        {
            "description": "new current report",
            "received_at": "2026-09-22T11:00:00+00:00",
            "classification": "external_report",
            "outcome": "succeeded",
            "event_id": "task66-current-report",
        }
    )

    second = _read(fixture, "overall_situational_picture")
    assert "Cameras: 0/1 active" in second.answer
    assert "1 offline" in second.answer
    assert "Drones: 0 ready; 0 airborne; 1 charging" in second.answer
    assert "new current report" in second.answer
    assert second.provenance is not None


def test_every_fixed_button_uses_the_trusted_scope_without_cross_scope_reads(fixed_fixture):
    fixture = fixed_fixture

    for protocol_name in (
        "report_team_availability",
        "query_camera_status",
        "query_drone_fleet_status",
        "query_historical_incidents",
        "overall_situational_picture",
    ):
        answer = _read(fixture, protocol_name, SIMULATION).answer
        assert "live" not in answer

    assert _read(fixture, "report_team_availability", SIMULATION).answer == "team-sim-a"
    assert _read(fixture, "query_camera_status", SIMULATION).answer == "offline"
    assert "charging" in _read(fixture, "query_drone_fleet_status", SIMULATION).answer
    assert "simulation event A" in _read(fixture, "query_historical_incidents", SIMULATION).answer
    assert SIMULATION.key in fixture.team_store.scopes
    assert SIMULATION.key in fixture.surveillance_store.scopes
    assert fixture.history.calls.count(SIMULATION.key) >= 2


def test_api_fixed_button_hint_bypasses_conversation_and_generic_model_routing(tmp_path, teardown_ctx):
    state = _State()
    team_store = _TeamStore(state)
    team_agent = _TeamAgent(state, team_store)
    main_agent = happy_path_agent(intent="question")
    ctx = build_context(tmp_path, main_agent=main_agent, conversation_history_turns=6)
    ctx.deps.persistence.write_user("viewer-1", "viewer")
    protocol = Protocol(
        name="report_team_availability",
        description="team status",
        participating_agents=("team_status_agent",),
        approved_tools=("report_team_availability",),
        expected_success_output="team status",
        criticality=CriticalityLevel.LOW,
        approval_flag=False,
    )
    registry = AgentRegistry({"team_status_agent": team_agent})
    ctx = replace(
        ctx,
        deps=replace(
            ctx.deps,
            registry=registry,
            protocol_set=ProtocolSet(ctx.deps.protocol_set.all() + (protocol,)),
        ),
    )
    teardown_ctx.append(ctx)
    client = build_app(ctx).test_client()

    first = client.post(
        "/Msg",
        headers=auth_headers(VIEWER_IDENTITY),
        json={
            "text": "synthetic prompt that must not be routed",
            "sender_identity": VIEWER_IDENTITY,
            "source_message_id": "task66-button-1",
            "conversation_id": "task66-conversation",
            "protocol_hint": "report_team_availability",
            "fixed_state_button": True,
        },
    )
    state.team[LIVE.key] = "team-live-b"
    second = client.post(
        "/Msg",
        headers=auth_headers(VIEWER_IDENTITY),
        json={
            "text": "synthetic prompt that must not be routed",
            "sender_identity": VIEWER_IDENTITY,
            "source_message_id": "task66-button-2",
            "conversation_id": "task66-conversation",
            "protocol_hint": "report_team_availability",
            "fixed_state_button": True,
        },
    )

    assert first.get_json()["answer"] == "team-live-a"
    assert second.get_json()["answer"] == "team-live-b"
    assert team_agent.calls == 2
    assert main_agent.calls == []
    assert ctx.deps.persistence.fetch_conversation_messages("task66-conversation", 12) == []
