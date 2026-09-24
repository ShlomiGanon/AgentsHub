"""Verifies profiles/response_team.py (docs/responce_improve.md) and profiles/fire_station.py
(docs/bar_improves.md Stage 4): each profile loads and validates through
profiles.loader.load_profile, exposes exactly its declared content, has no cross-organization
leakage, and every side-effecting tool declares idempotency as specified."""

import pytest

from config.base import TierModel
from persistence.sqlite_store import SQLitePersistence
from profiles.loader import load_profile

CORE_MODEL = TierModel(model="openai/test-core-model", api_key="test-key")
SUB_MODEL = TierModel(model="openai/test-sub-model", api_key="test-key")


@pytest.fixture(autouse=True)
def _bot_tokens(monkeypatch):
    monkeypatch.setenv("RESPONSE_TEAM_BOT_TOKEN", "response-team-test-token")
    monkeypatch.setenv("FIRE_STATION_BOT_TOKEN", "fire-station-test-token")
    monkeypatch.setenv("RESPONSE_TEAM_SIM_BOT_TOKEN", "response-team-sim-test-token")
    monkeypatch.setenv("FIRE_STATION_SIM_BOT_TOKEN", "fire-station-sim-test-token")


@pytest.fixture
def sec_profile():
    return load_profile("profiles.response_team", CORE_MODEL, SUB_MODEL)


@pytest.fixture
def fire_profile():
    return load_profile("profiles.fire_station", CORE_MODEL, SUB_MODEL)


def _tools_by_name(loaded_profile):
    tools = {}
    for agent in loaded_profile.agents:
        for tool_info in agent.exposed_tools():
            tools[tool_info.name] = tool_info
    return tools


# -- Loading and validation -------------------------------------------------


def test_response_team_profile_loads_and_validates(sec_profile):
    assert sec_profile.profile_name == "Response Team"
    assert sec_profile.db_path
    assert sec_profile.api_port == 8907


def test_fire_station_profile_loads_and_validates(fire_profile):
    assert fire_profile.profile_name == "Fire and Rescue Station"
    assert fire_profile.db_path
    assert fire_profile.api_port == 8908


# -- Exact declared content ---------------------------------------------


def test_response_team_exposes_exactly_its_declared_event_types(sec_profile):
    assert set(sec_profile.event_types) == {
        "attendance",
        "camera_status",
        "security_incident",
        "force_dispatch",
        "team_movement",
        "situational_query",
        "incident_summary",
        "human_activation",  # injected automatically by every profile
    }


def test_response_team_exposes_exactly_its_declared_areas(sec_profile):
    assert set(sec_profile.areas) == {
        "west_gate", "east_gate", "east_fence", "east_orchards", "expansion_neighborhood",
        "old_public_building", "south_corner", "access_road", "drones_warehouse",
    }


def test_response_team_exposes_exactly_its_declared_agents(sec_profile):
    assert {agent.name for agent in sec_profile.agents} == {
        "roster_agent", "surveillance_agent", "neighboring_forces_agent",
    }


def test_response_team_exposes_exactly_its_declared_protocols(sec_profile):
    assert {protocol.name for protocol in sec_profile.protocols} == {
        "record_attendance", "update_camera_status", "report_security_incident",
        "dispatch_neighboring_force", "report_team_movement", "query_situational_picture",
        "query_incident_summary",
    }


def test_fire_station_exposes_exactly_its_declared_event_types(fire_profile):
    assert set(fire_profile.event_types) == {
        "structure_fire", "hazmat_fire", "rescue", "attendance", "human_activation",
    }


def test_fire_station_exposes_exactly_its_declared_areas(fire_profile):
    assert set(fire_profile.areas) == {
        "district_north", "district_south", "district_center", "industrial_zone",
    }


def test_fire_station_exposes_exactly_its_declared_agents(fire_profile):
    assert {agent.name for agent in fire_profile.agents} == {"dispatch_agent", "hazmat_agent", "roster_agent"}


def test_fire_station_exposes_exactly_its_declared_protocols(fire_profile):
    assert {protocol.name for protocol in fire_profile.protocols} == {
        "structure_fire_response", "hazmat_response", "mutual_aid_request", "rescue_response", "attendance_update",
    }


# -- No cross-organization leakage ---------------------------------------


def test_no_fire_protocol_or_tool_name_exists_in_the_sec_profile(sec_profile, fire_profile):
    sec_protocol_names = {protocol.name for protocol in sec_profile.protocols}
    sec_tool_names = set(_tools_by_name(sec_profile))
    fire_protocol_names = {protocol.name for protocol in fire_profile.protocols}
    fire_tool_names = set(_tools_by_name(fire_profile))

    # docs/responce_improve.md: profiles/response_team.py's roster agent is now its own
    # profile-owned ResponseTeamRosterAgent, not the shared agents.RosterAgent
    # profiles/fire_station.py still uses -- the two profiles no longer share any
    # protocol or tool name at all (only the agent *name* "roster_agent" coincides;
    # see test_sec_and_fire_agent_names_do_not_collide_outside_the_shared_roster_agent).
    assert fire_protocol_names.isdisjoint(sec_protocol_names)
    assert fire_tool_names.isdisjoint(sec_tool_names)


def test_no_sec_protocol_or_tool_name_exists_in_the_fire_profile(sec_profile, fire_profile):
    # Same check, the other direction — kept as a separate test so a future asymmetric
    # regression (added only on one side) is caught either way.
    test_no_fire_protocol_or_tool_name_exists_in_the_sec_profile(sec_profile, fire_profile)


def test_sec_and_fire_agent_names_do_not_collide_outside_the_shared_roster_agent(sec_profile, fire_profile):
    sec_agent_names = {agent.name for agent in sec_profile.agents}
    fire_agent_names = {agent.name for agent in fire_profile.agents}

    assert sec_agent_names & fire_agent_names == {"roster_agent"}


# -- idempotent declared correctly for every side-effecting tool ----------


_EXPECTED_IDEMPOTENCY = {
    # SEC (Response Team)
    "record_attendance_response": True,
    "start_daily_attendance_check": True,
    "report_team_movement": True,
    "update_camera_status": True,
    "recall_drone": True,
    "dispatch_drone_to_area": False,
    "dispatch_neighboring_force": False,
    # FIRE
    "dispatch_station_crew": False,
    "request_mutual_aid": False,
    "request_hazmat_assessment": False,
}


def test_every_side_effecting_tool_declares_idempotent_as_specified(sec_profile, fire_profile):
    all_tools = {**_tools_by_name(sec_profile), **_tools_by_name(fire_profile)}
    assert set(_EXPECTED_IDEMPOTENCY) <= set(all_tools)
    for tool_name, expected_idempotent in _EXPECTED_IDEMPOTENCY.items():
        tool_info = all_tools[tool_name]
        assert tool_info.side_effecting is True, f"{tool_name} must be side_effecting"
        assert tool_info.idempotent is expected_idempotent, f"{tool_name} idempotent mismatch"


# -- request_mutual_aid refuses an unrecognized resource name -------------


def test_request_mutual_aid_refuses_a_name_not_in_mutual_aid_resources(fire_profile):
    from profiles.fire_station import MUTUAL_AID_RESOURCES

    dispatch_agent = next(agent for agent in fire_profile.agents if agent.name == "dispatch_agent")

    for recognized in MUTUAL_AID_RESOURCES:
        result = dispatch_agent.request_mutual_aid(recognized, area="district_north")
        assert "refused" not in result
        assert recognized in result

    refusal = dispatch_agent.request_mutual_aid("HELICOPTER_9", area="district_north")
    assert "refused" in refusal
    assert "HELICOPTER_9" in refusal
    # The refusal made no new record — exactly one recorded request per recognized name above.
    assert dispatch_agent.mutual_aid_requests == [f"{name} to district_north" for name in MUTUAL_AID_RESOURCES]


# -- Two deployments, separate databases and ports -------------------------


def test_the_two_profiles_run_side_by_side_as_two_deployments_with_separate_databases_and_ports(
    sec_profile, fire_profile, tmp_path,
):
    assert sec_profile.db_path != fire_profile.db_path
    assert sec_profile.api_port != fire_profile.api_port

    # Two independent SQLitePersistence instances against each profile's own DB_PATH,
    # reusing the pattern of tests/test_integration_profile_isolation.py's own
    # cross-profile isolation checks — real databases, real writes, never shared state.
    sec_store = SQLitePersistence(str(tmp_path / "sec.db"))
    fire_store = SQLitePersistence(str(tmp_path / "fire.db"))
    try:
        sec_event_id = sec_store.append_event({
            "received_at": "2026-08-24T10:00:00", "source": "sensor", "sender_identity": "sensor-1",
            "occurred_at": "2026-08-24T10:00:00", "raw_text": "heavy equipment near west gate",
            "classification": "perimeter_observation", "area": "west_gate",
        })

        fire_rows = fire_store.fetch_events_range("2000-01-01T00:00:00", "2100-01-01T00:00:00")
        assert fire_rows == []

        sec_rows = sec_store.fetch_events_range("2000-01-01T00:00:00", "2100-01-01T00:00:00")
        assert {row["event_id"] for row in sec_rows} == {sec_event_id}
    finally:
        sec_store.close()
        fire_store.close()


# -- Simulation as a separate deployment (Stage 5, docs/bar_improves.md) --------
#
# profiles.response_team has no separate `..._sim` twin (docs/responce_improve.md):
# a simulation of it is just another deployment of the very same profile module,
# with different DB_PATH/API_PORT/BOT_TOKEN_ENV values supplied at the process
# level -- there is no second profile module to load and compare here the way
# profiles.fire_station_sim still is, below.


def test_fire_station_sim_loads_and_validates_with_the_same_declared_content(fire_profile):
    sim = load_profile("profiles.fire_station_sim", CORE_MODEL, SUB_MODEL)

    assert sim.profile_name == "Fire and Rescue Station (Simulation)"
    assert {agent.name for agent in sim.agents} == {agent.name for agent in fire_profile.agents}
    assert {protocol.name for protocol in sim.protocols} == {protocol.name for protocol in fire_profile.protocols}
    assert sim.db_path != fire_profile.db_path
    assert sim.api_port != fire_profile.api_port
    assert sim.profile_file_hash != fire_profile.profile_file_hash


def test_live_and_simulation_deployments_share_no_events_precedents_notifications_or_users(tmp_path):
    live_store = SQLitePersistence(str(tmp_path / "live.db"))
    sim_store = SQLitePersistence(str(tmp_path / "sim.db"))
    try:
        live_store.write_user("commander-1", "commander")
        sim_store.write_user("sim-commander-1", "commander")
        assert live_store.read_user("sim-commander-1") is None
        assert sim_store.read_user("commander-1") is None

        live_event_id = live_store.append_event({
            "received_at": "2026-08-24T10:00:00", "source": "sensor", "sender_identity": "sensor-1",
            "occurred_at": "2026-08-24T10:00:00", "raw_text": "heavy equipment near west gate",
            "classification": "perimeter_observation", "area": "west_gate",
        })
        sim_event_id = sim_store.append_event({
            "received_at": "2026-08-24T10:05:00", "source": "sensor", "sender_identity": "sensor-1",
            "occurred_at": "2026-08-24T10:05:00", "raw_text": "rehearsal: heavy equipment near west gate",
            "classification": "perimeter_observation", "area": "west_gate",
        })

        live_events = {
            row["event_id"] for row in live_store.fetch_events_range("2000-01-01T00:00:00", "2100-01-01T00:00:00")
        }
        sim_events = {
            row["event_id"] for row in sim_store.fetch_events_range("2000-01-01T00:00:00", "2100-01-01T00:00:00")
        }
        assert live_events == {live_event_id}
        assert sim_events == {sim_event_id}

        # Precedent search: the live store's own candidate window must never surface the
        # simulation's event, even though it shares classification/area (which would be a
        # genuine match if the databases were shared).
        live_candidates = live_store.fetch_events_by_type_area_window(
            "perimeter_observation", "west_gate", "2000-01-01T00:00:00", "2100-01-01T00:00:00",
        )
        assert sim_event_id not in {row["event_id"] for row in live_candidates}

        # Notifications: setting an outcome writes a notification-log row as a side
        # effect (persistence/sqlite_store.py's update_event); each store's own log is
        # independent too.
        live_store.update_event(live_event_id, {"outcome": "succeeded"})
        sim_store.update_event(sim_event_id, {"outcome": "succeeded"})
        live_notifications = live_store.fetch_notifications_since(0)
        sim_notifications = sim_store.fetch_notifications_since(0)
        assert {n["event_id"] for n in live_notifications} == {live_event_id}
        assert {n["event_id"] for n in sim_notifications} == {sim_event_id}
    finally:
        live_store.close()
        sim_store.close()


# -- Stage 7, docs/bar_improves.md: a tool result proves only its own effect ----


_FORBIDDEN_UNOBSERVED_OUTCOME_WORDS = (
    "arrived", "dispatched successfully", "on scene", "completed the dispatch", "extinguished",
    "rescued", "fixed", "repaired", "responded to", "resolved the",
)


def _agent_by_name(loaded_profile, name):
    return next(agent for agent in loaded_profile.agents if agent.name == name)


def test_every_side_effecting_sec_tool_states_only_its_own_recorded_effect(sec_profile, tmp_path):
    from agents import authenticated_request_identity
    from persistence import (
        open_neighboring_force_store,
        open_response_team_roster_store,
        open_response_team_surveillance_store,
    )
    from profiles.response_team import DRONES_WAREHOUSE, eta_seconds

    roster = _agent_by_name(sec_profile, "roster_agent")
    surveillance = _agent_by_name(sec_profile, "surveillance_agent")
    neighboring_forces = _agent_by_name(sec_profile, "neighboring_forces_agent")

    # Point each agent's store at an isolated tmp_path database for this test only --
    # sec_profile's real agents otherwise open this profile's real, on-disk DB_PATH.
    roster.status_store = open_response_team_roster_store(str(tmp_path / "roster.db"))
    surveillance.surveillance_store = open_response_team_surveillance_store(
        str(tmp_path / "surveillance.db"), eta_fn=eta_seconds, home_area=DRONES_WAREHOUSE
    )
    neighboring_forces.dispatch_store = open_neighboring_force_store(str(tmp_path / "dispatch.db"))

    roster.status_store.register_member("test-fighter", "Test Fighter")
    roster.status_store.approve_roster("test-commander")
    surveillance.surveillance_store.ensure_camera(
        "CAM-TEST", name="Test Camera", area="east_fence", feed_summary="initial view"
    )

    with authenticated_request_identity("test-fighter"):
        movement_result = roster.report_team_movement(area="east_fence")

    results = [
        movement_result,
        surveillance.update_camera_status("CAM-TEST", "clear view restored"),
        neighboring_forces.dispatch_neighboring_force("ambulance", "east_fence"),
    ]

    for result in results:
        assert "recorded" in result
        lowered = result.lower()
        for forbidden in _FORBIDDEN_UNOBSERVED_OUTCOME_WORDS:
            assert forbidden not in lowered, f"{result!r} claims an unobserved outcome ({forbidden!r})"


def test_every_side_effecting_fire_tool_states_only_its_own_recorded_effect(fire_profile):
    dispatch = _agent_by_name(fire_profile, "dispatch_agent")
    hazmat = _agent_by_name(fire_profile, "hazmat_agent")

    results = [
        dispatch.dispatch_station_crew("district_north"),
        dispatch.request_mutual_aid("ASHED", area="district_north"),
        hazmat.request_hazmat_assessment("industrial_zone"),
    ]

    for result in results:
        assert "recorded" in result
        lowered = result.lower()
        for forbidden in _FORBIDDEN_UNOBSERVED_OUTCOME_WORDS:
            assert forbidden not in lowered, f"{result!r} claims an unobserved outcome ({forbidden!r})"
