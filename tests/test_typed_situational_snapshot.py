from datetime import datetime, timezone

import pytest

from agents.runtime import AgentRegistry
from messages import get_catalog, set_current_catalog
from orchestrator.response_contract import informational_response, validate_response
from orchestrator.situational_picture import build_situational_picture, build_typed_snapshot, render_typed_snapshot
from protocols import CriticalityLevel, Protocol


NOW = datetime(2026, 9, 17, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _english_catalog():
    set_current_catalog(get_catalog("en"))


class FakeSurveillanceStore:
    def __init__(self, cameras, drones, missions):
        self.cameras = cameras
        self.drones = drones
        self.missions = missions
        self.camera_calls = []

    def list_cameras(self, area=None):
        self.camera_calls.append(area)
        return [camera for camera in self.cameras if area is None or camera["area"] == area]

    def list_drones(self):
        return list(self.drones)

    def get_active_missions(self):
        return list(self.missions)


class FakeTeamStore:
    def __init__(self, entries):
        self.entries = entries
        self.as_of = None

    def availability_snapshot(self, as_of):
        self.as_of = as_of
        return list(self.entries)


class FakeSurveillanceAgent:
    def __init__(self, store):
        self.surveillance_store = store


class FakeTeamAgent:
    def __init__(self, store):
        self.status_store = store


def _registry(surveillance_store, team_store):
    return AgentRegistry({
        "surveillance_agent": FakeSurveillanceAgent(surveillance_store),
        "team_status_agent": FakeTeamAgent(team_store),
    })


def _camera(camera_id, area="north", status="active"):
    return {"camera_id": camera_id, "area": area, "status": status}


def _drone(drone_id, status="ready", mission_id=None):
    return {"drone_id": drone_id, "status": status, "assigned_mission_id": mission_id}


def _mission(mission_id, drone_id):
    return {"mission_id": mission_id, "drone_id": drone_id}


def _team(identity, availability):
    return {"telegram_identity": identity, "availability": availability}


def test_snapshot_preserves_camera_counts_and_area_scope():
    surveillance = FakeSurveillanceStore(
        [_camera(str(index)) for index in range(5)] + [_camera("south-1", "south", "offline")],
        [_drone("d-1")],
        [],
    )
    team = FakeTeamStore([_team("u-1", "available")])

    snapshot = build_typed_snapshot(_registry(surveillance, team), now=NOW, area="north")

    assert snapshot.cameras.total == 5
    assert snapshot.cameras.active == 5
    assert snapshot.cameras.inactive == 0
    assert surveillance.camera_calls == ["north"]
    assert snapshot.cameras.provenance.scope == "north"


def test_team_not_reported_is_not_available_and_counts_sum_exactly():
    surveillance = FakeSurveillanceStore([], [], [])
    team = FakeTeamStore(
        [_team("u-1", "unavailable")] + [_team(f"u-{i}", "awaiting_response") for i in range(2, 16)]
    )

    snapshot = build_typed_snapshot(_registry(surveillance, team), now=NOW)

    assert snapshot.team.total == 15
    assert (snapshot.team.available, snapshot.team.unavailable, snapshot.team.not_reported) == (0, 1, 14)
    assert "15 available" not in render_typed_snapshot(snapshot)
    assert "14 not reported" in render_typed_snapshot(snapshot)
    finding_keys = {finding.message_key for finding in snapshot.findings}
    assert "orchestrator.picture.finding.team_no_confirmed" in finding_keys
    assert "orchestrator.picture.finding.team_not_reported" in finding_keys


def test_airborne_drone_with_mission_is_present_in_snapshot():
    surveillance = FakeSurveillanceStore(
        [], [_drone("d-1", "in_flight", "m-1")], [_mission("m-1", "d-1")]
    )
    team = FakeTeamStore([])

    snapshot = build_typed_snapshot(_registry(surveillance, team), now=NOW)

    assert snapshot.drones.airborne == 1
    assert snapshot.drones.active_missions == 1
    assert snapshot.drones.status == "ok"
    assert "1 active missions" in render_typed_snapshot(snapshot)


def test_conflicting_drone_and_mission_stores_are_typed_inconsistent():
    surveillance = FakeSurveillanceStore(
        [], [_drone("d-1", "in_flight", "missing")], []
    )
    team = FakeTeamStore([])

    snapshot = build_typed_snapshot(_registry(surveillance, team), now=NOW)

    assert snapshot.drones.status == "inconsistent"
    assert snapshot.inconsistencies == ("drone and mission stores disagree",)
    assert "Drone and active-mission data are inconsistent" in render_typed_snapshot(snapshot)
    assert not any(finding.message_key == "orchestrator.picture.finding.drones_ready" for finding in snapshot.findings)


def test_snapshot_sections_have_source_provenance_and_repeat_without_mutation():
    surveillance = FakeSurveillanceStore([_camera("c-1")], [_drone("d-1")], [])
    team = FakeTeamStore([_team("u-1", "available")])
    registry = _registry(surveillance, team)

    first = build_typed_snapshot(registry, now=NOW)
    second = build_typed_snapshot(registry, now=NOW)

    assert first.provenance()["sections"]["cameras"]["source"] == "surveillance_store.list_cameras"
    assert first.provenance()["sections"]["drones"]["source"].startswith("surveillance_store.")
    assert first.provenance()["sections"]["team"]["source"] == "team_status_store.availability_snapshot"
    assert first.cameras == second.cameras
    assert first.team == second.team


def test_deterministic_renderer_ignores_model_prose_and_keeps_structured_facts():
    surveillance = FakeSurveillanceStore([_camera(str(i)) for i in range(5)], [], [])
    team = FakeTeamStore([_team("u-1", "unavailable"), _team("u-2", "awaiting_response")])

    snapshot = build_typed_snapshot(_registry(surveillance, team), now=NOW)
    model_prose = "There are zero active cameras and everyone is okay."
    rendered = render_typed_snapshot(snapshot)

    assert model_prose not in rendered
    assert "Cameras: 5/5 active" in rendered
    assert "Readiness team: 0 available; 1 unavailable; 1 not reported" in rendered


def test_production_picture_uses_typed_renderer_when_authoritative_stores_exist():
    surveillance = FakeSurveillanceStore([_camera(str(i)) for i in range(5)], [], [])
    team = FakeTeamStore([_team("u-1", "unavailable")])
    protocol = Protocol(
        name="overall_situational_picture",
        description="picture",
        participating_agents=("surveillance_agent", "team_status_agent"),
        approved_tools=(),
        expected_success_output="picture",
        criticality=CriticalityLevel.LOW,
        approval_flag=False,
    )

    class NoModel:
        def process(self, *args, **kwargs):
            raise AssertionError("typed snapshot must not invoke model composition")

    picture = build_situational_picture(
        NoModel(), protocol, _registry(surveillance, team), object(), "picture", caller_identity="u-1", sender_identity_filter=None, now=NOW
    )

    assert picture.snapshot is not None
    assert picture.reports == ()
    assert "Cameras: 5/5 active" in picture.text


def test_hebrew_and_english_renderers_preserve_the_same_counts():
    surveillance = FakeSurveillanceStore(
        [_camera(str(i)) for i in range(5)],
        [_drone("d-1"), _drone("d-2"), _drone("d-3", "charging")],
        [],
    )
    team = FakeTeamStore(
        [_team("u-1", "unavailable")] + [_team(f"u-{i}", "awaiting_response") for i in range(2, 16)]
    )
    snapshot = build_typed_snapshot(_registry(surveillance, team), now=NOW)

    set_current_catalog(get_catalog("en"))
    english = render_typed_snapshot(snapshot)
    set_current_catalog(get_catalog("he"))
    hebrew = render_typed_snapshot(snapshot)

    assert "Cameras: 5/5 active" in english
    assert "מצלמות: 5/5 פעילות" in hebrew
    assert "רחפנים: 2 מוכנים" in hebrew
    assert "כיתת כוננות: 0 זמינים; 1 לא זמינים; 14 טרם דיווחו" in hebrew
    assert "אין כרגע כוח זמין מאושר" in hebrew
    assert "מומלץ להשלים דיווחי זמינות של 14" in hebrew
    assert all(label not in hebrew for label in ("Cameras", "Drones", "Team", "Situational"))


def test_findings_have_verified_state_provenance_and_recommendations_are_not_actions():
    surveillance = FakeSurveillanceStore([_camera(str(i)) for i in range(5)], [_drone("d-1")], [])
    team = FakeTeamStore([_team("u-1", "awaiting_response")])

    snapshot = build_typed_snapshot(_registry(surveillance, team), now=NOW)
    provenance = snapshot.provenance()

    assert snapshot.findings
    assert all(finding.source_refs for finding in snapshot.findings)
    assert all(ref.startswith("state:") for finding in snapshot.findings for ref in finding.source_refs)
    assert all(item["source_refs"] for item in provenance["findings"])
    recommendation = next(finding for finding in snapshot.findings if finding.suggested_action_key)
    assert recommendation.suggested_action_key == "orchestrator.picture.recommendation.collect_availability"
    assert not hasattr(recommendation, "tool_name")
    validate_response(informational_response(
        render_typed_snapshot(snapshot),
        tuple(dict.fromkeys(ref for finding in snapshot.findings for ref in finding.source_refs)),
    ))


def test_unified_test_profile_uses_hebrew_findings_renderer(monkeypatch, tmp_path):
    from profiles import unified_test

    history_path = str(tmp_path / "history.db")
    surveillance_path = str(tmp_path / "surveillance.db")
    team_path = str(tmp_path / "team-status.db")
    monkeypatch.setattr(unified_test, "DB_PATH", history_path)
    monkeypatch.setattr(unified_test, "UNIFIED_SURVEILLANCE_DB_PATH", surveillance_path)
    monkeypatch.setattr(unified_test, "UNIFIED_TEAM_STATUS_DB_PATH", team_path)
    monkeypatch.setattr(unified_test.UnifiedSurveillanceAgent, "surveillance_db_path", surveillance_path)
    monkeypatch.setattr(unified_test.UnifiedTeamStatusAgent, "status_db_path", team_path)
    unified_test.ensure_seed_data()

    surveillance_agent = unified_test.UnifiedSurveillanceAgent(model="mock")
    team_agent = unified_test.UnifiedTeamStatusAgent(model="mock")
    team_store = team_agent.status_store
    cycle = team_store.latest_cycle()
    for index in range(4, 13):
        team_store.register_member(str(1000 + index), f"Member {index}", cycle["opened_at"])
    team_store.approve_roster("commander_user", cycle["opened_at"])
    team_store.record_response(
        telegram_identity="2077472944",
        source_message_id="baseline-unavailable",
        availability="unavailable",
        reason="reserve duty",
        original_text="unavailable",
        received_at=cycle["opened_at"],
    )

    registry = AgentRegistry({
        "surveillance_agent": surveillance_agent,
        "team_status_agent": team_agent,
    })
    protocol = next(protocol for protocol in unified_test.PROTOCOLS if protocol.name == "overall_situational_picture")

    class NoModel:
        def process(self, *args, **kwargs):
            raise AssertionError("unified_test typed picture must not invoke a model")

    set_current_catalog(get_catalog(unified_test.DEFAULT_LANGUAGE))
    picture = build_situational_picture(
        NoModel(), protocol, registry, object(), "picture", caller_identity="viewer_user", sender_identity_filter=None
    )

    assert picture.snapshot.cameras.active == 5
    assert picture.snapshot.drones.ready == 2
    assert picture.snapshot.drones.charging == 1
    assert picture.snapshot.team.available == 0
    assert picture.snapshot.team.unavailable == 1
    assert picture.snapshot.team.not_reported == 14
    assert "מצלמות: 5/5 פעילות" in picture.text
    assert "קיימת יכולת אווירית זמינה: 2 רחפנים" in picture.text
    assert "אין כרגע כוח זמין מאושר" in picture.text
    assert "14 טרם דיווחו" in picture.text
