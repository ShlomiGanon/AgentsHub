from types import SimpleNamespace

from persistence import (
    OperationalScope,
    open_operational_unit_persistence,
    open_persistence,
    resolve_runtime_context,
)
from profiles.operational_profile import FIRE_STATION, RESPONSE_TEAM, operational_profile


def _profile():
    return SimpleNamespace(live_operational_profile=RESPONSE_TEAM, simulations=())


def test_live_context_resolves_user_membership_unit_and_profile(tmp_path):
    users = open_persistence(str(tmp_path / "main.db"))
    units = open_operational_unit_persistence(str(tmp_path / "team.db"))
    users.write_user("live-user", "viewer", "Live User")
    unit = units.create_unit("Fire station", FIRE_STATION, status="active", unit_id="fire-1")
    units.assign_membership("live-user", unit.unit_id, "firefighter", full_name="Live User")

    context = resolve_runtime_context(
        identity_id="live-user",
        users_persistence=users,
        unit_store=units,
        loaded_profile=_profile(),
    )

    assert context.is_resolved
    assert context.operational_scope == OperationalScope.live()
    assert context.operational_unit.unit_id == "fire-1"
    assert context.operational_profile.profile_id == FIRE_STATION
    assert context.role == "firefighter"


def test_ambiguous_live_membership_is_explicit(tmp_path):
    users = open_persistence(str(tmp_path / "main.db"))
    units = open_operational_unit_persistence(str(tmp_path / "team.db"))
    users.write_user("ambiguous", "viewer")
    first = units.create_unit("Response", RESPONSE_TEAM, status="active", unit_id="response-1")
    second = units.create_unit("Fire", FIRE_STATION, status="active", unit_id="fire-1")
    units.assign_membership("ambiguous", first.unit_id, "responder")
    units.assign_membership("ambiguous", second.unit_id, "firefighter")

    context = resolve_runtime_context(
        identity_id="ambiguous", users_persistence=users, unit_store=units, loaded_profile=_profile()
    )

    assert context.status == "ambiguous_active_membership"
    assert context.operational_profile is None


def test_simulation_context_does_not_require_live_membership(tmp_path):
    users = open_persistence(str(tmp_path / "main.db"))
    users.write_user("simulation-user", "viewer")
    loaded = SimpleNamespace(
        live_operational_profile=RESPONSE_TEAM,
        simulations=(SimpleNamespace(scenario_id="FIRE_002", official_metadata={"operational_profile": FIRE_STATION}),),
    )
    simulation = SimpleNamespace(scenario_id="FIRE_002", scenario_run_id="run-1")

    context = resolve_runtime_context(
        identity_id="simulation-user",
        users_persistence=users,
        unit_store=None,
        loaded_profile=loaded,
        simulation_context=simulation,
    )

    assert context.is_resolved
    assert context.operational_scope == OperationalScope.simulation("FIRE_002", "run-1")
    assert context.operational_profile.profile_id == FIRE_STATION
    assert context.membership is None


def test_incomplete_simulation_context_never_falls_back_to_live(tmp_path):
    users = open_persistence(str(tmp_path / "main.db"))
    users.write_user("simulation-user", "viewer")

    context = resolve_runtime_context(
        identity_id="simulation-user",
        users_persistence=users,
        loaded_profile=_profile(),
        simulation_context=SimpleNamespace(scenario_id="SEC_001", scenario_run_id=None),
    )

    assert context.status == "invalid_simulation_context"
    assert context.operational_scope is None


def test_event_scope_reuses_exact_simulation_run_without_live_membership(tmp_path):
    users = open_persistence(str(tmp_path / "main.db"))
    users.write_user("event-user", "viewer")
    loaded = SimpleNamespace(
        live_operational_profile=RESPONSE_TEAM,
        simulations=(SimpleNamespace(scenario_id="SEC_001", official_metadata={"operational_profile": RESPONSE_TEAM}),),
    )

    context = resolve_runtime_context(
        identity_id="event-user",
        users_persistence=users,
        loaded_profile=loaded,
        operational_scope=OperationalScope.simulation("SEC_001", "run-7"),
    )

    assert context.is_resolved
    assert context.operational_scope == OperationalScope.simulation("SEC_001", "run-7")
    assert context.membership is None
