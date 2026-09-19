from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from persistence import (
    OperationalScope,
    current_operational_scope,
    open_surveillance_persistence,
    open_team_status_persistence,
    operational_scope_context,
    scope_from_event,
    scope_from_simulation_context,
)


def _baseline(*identities):
    return {"team": {"members": [{"telegram_identity": item, "full_name": item} for item in identities]}}


def test_partial_event_simulation_metadata_never_creates_an_invalid_scope():
    simulation = OperationalScope.simulation("fixture", "run-1")

    assert scope_from_event({"scenario_id": "fixture"}) == OperationalScope.live()
    assert scope_from_event({"scenario_run_id": "run-1"}) == OperationalScope.live()
    assert scope_from_simulation_context(SimpleNamespace(scenario_id="fixture")) == OperationalScope.live()

    with operational_scope_context(simulation):
        assert current_operational_scope() == simulation
        assert scope_from_event({"scenario_id": "fixture"}) == simulation
        assert scope_from_simulation_context(SimpleNamespace(scenario_run_id="run-1")) == simulation


@pytest.mark.parametrize(
    "scenario_id,run_id",
    [("SEC-A", "run-a"), ("FIRE-B", "run-b"), ("arbitrary-fixture", "run-17")],
)
def test_any_valid_simulation_identity_gets_an_isolated_scope(tmp_path, scenario_id, run_id):
    surveillance = open_surveillance_persistence(str(tmp_path / "surveillance.db"), seed_demo_data=True)
    team = open_team_status_persistence(str(tmp_path / "team.db"))
    scope = OperationalScope.simulation(scenario_id, run_id)

    surveillance.ensure_scope(scope)
    team.ensure_scope(scope, _baseline(f"member-{run_id}"))

    assert surveillance.list_cameras(scope=scope)
    assert [row["telegram_identity"] for row in team.list_members(scope=scope)] == [f"member-{run_id}"]
    assert surveillance.list_cameras(scope=OperationalScope.live())


def test_live_and_simulation_runs_coexist_and_resume_without_cross_contamination(tmp_path):
    surveillance = open_surveillance_persistence(str(tmp_path / "surveillance.db"), seed_demo_data=True)
    team = open_team_status_persistence(str(tmp_path / "team.db"))
    live = OperationalScope.live()
    sec_a = OperationalScope.simulation("SEC-A", "run-a")
    fire_b = OperationalScope.simulation("FIRE-B", "run-b")
    sec_c = OperationalScope.simulation("SEC-C", "run-c")

    team.register_member("live-member", "live-member", scope=live)
    team.approve_roster("live-commander", scope=live)
    live_camera_before = surveillance.get_camera("CAM-02", scope=live)
    live_members_before = team.list_members(scope=live)

    surveillance.ensure_scope(sec_a)
    team.ensure_scope(sec_a, _baseline("sec-a-member"))
    surveillance.ensure_scope(fire_b)
    team.ensure_scope(fire_b, _baseline("fire-b-member"))

    surveillance.update_camera_feed("CAM-02", "SEC-A observation", status="offline", scope=sec_a)
    mission = surveillance.dispatch_drone(
        target_area="north_gate",
        incident_description="SEC-A test mission",
        scope=sec_a,
    )
    now = datetime.now(timezone.utc).isoformat()
    team.open_cycle("scope-test", now, "2999-01-01T00:00:00+00:00", scope=sec_a)
    team.record_response(
        telegram_identity="sec-a-member",
        source_message_id="sec-a-response",
        availability="unavailable",
        original_text="unavailable",
        received_at=now,
        reason="scope test",
        availability_start=now,
        availability_end="2999-01-01T00:00:00+00:00",
        scope=sec_a,
    )

    assert surveillance.get_camera("CAM-02", scope=fire_b)["status"] != "offline"
    assert surveillance.get_camera("CAM-02", scope=live)["status"] == live_camera_before["status"]
    assert surveillance.get_active_missions(scope=fire_b) == []
    assert team.availability_snapshot(now, scope=fire_b)[0]["availability"] == "awaiting_response"
    assert team.list_members(scope=live) == live_members_before

    # Reconciliation is idempotent for a resumed run and does not reset its state.
    surveillance.ensure_scope(sec_a, baseline={"surveillance": {"cameras": []}})
    team.ensure_scope(sec_a, _baseline("different-member"))
    assert surveillance.get_camera("CAM-02", scope=sec_a)["status"] == "offline"
    assert surveillance.get_active_missions(scope=sec_a)[0]["mission_id"] == mission["mission_id"]
    assert team.list_members(scope=sec_a)[0]["telegram_identity"] == "sec-a-member"

    # A new run under the same scenario identity starts from a fresh baseline.
    surveillance.ensure_scope(sec_c)
    team.ensure_scope(sec_c, _baseline("sec-c-member"))
    assert surveillance.get_camera("CAM-02", scope=sec_c)["status"] != "offline"
    assert surveillance.get_active_missions(scope=sec_c) == []
    assert [row["telegram_identity"] for row in team.list_members(scope=sec_c)] == ["sec-c-member"]
    assert surveillance.get_camera("CAM-02", scope=live)["status"] == live_camera_before["status"]
    assert team.list_members(scope=live) == live_members_before
