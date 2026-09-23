"""Task 69C coverage for units, canonical memberships, and trusted resolution."""

import pytest

from persistence import OperationalScope, open_operational_unit_persistence, open_persistence, open_team_status_persistence, resolve_live_operational_context
from persistence.operational_unit_store import OperationalUnitError


def test_units_and_multiple_memberships_are_persisted_without_demo_state(tmp_path):
    history = open_persistence(str(tmp_path / "history.db"))
    history.ensure_user_exists("user-a", "viewer", "User A")
    team = open_team_status_persistence(str(tmp_path / "team.db"))
    units = open_operational_unit_persistence(str(tmp_path / "team.db"))

    response = units.create_unit("Response Unit", "response_team", status="active")
    fire = units.create_unit("Fire Unit", "fire_station", status="active")
    units.assign_membership("user-a", response.unit_id, "responder", full_name="User A")
    units.assign_membership("user-a", fire.unit_id, "firefighter", full_name="User A")

    resolved = resolve_live_operational_context("user-a", history, units)
    assert resolved.status == "ambiguous_active_membership"
    assert len(units.list_memberships()) == 2
    assert len(team.list_members(approved_only=False, scope=OperationalScope.live())) == 2

    with pytest.raises(OperationalUnitError):
        units.assign_membership("user-a", response.unit_id, "firefighter")
    with pytest.raises(OperationalUnitError):
        units.create_unit("Invalid", "unknown_profile")


def test_profile_switch_is_blocked_when_active_membership_exists(tmp_path):
    units = open_operational_unit_persistence(str(tmp_path / "team.db"))
    unit = units.create_unit("Response Unit", "response_team", status="active")
    units.assign_membership("user-a", unit.unit_id, "responder")
    with pytest.raises(OperationalUnitError, match="active memberships"):
        units.update_unit(unit.unit_id, profile_id="fire_station")


def test_simulation_membership_never_resolves_as_live(tmp_path):
    history = open_persistence(str(tmp_path / "history.db"))
    history.ensure_user_exists("sim-user", "viewer", "Synthetic", identity_kind="SIMULATION")
    team = open_team_status_persistence(str(tmp_path / "team.db"))
    team.ensure_scope(OperationalScope.simulation("SEC_001_PHASE_1", "run-69c"), baseline={"team": {"members": ["sim-user"]}})
    units = open_operational_unit_persistence(str(tmp_path / "team.db"))

    resolved = resolve_live_operational_context("sim-user", history, units)
    assert resolved.status == "simulation_identity"
    assert units.list_memberships() == []
