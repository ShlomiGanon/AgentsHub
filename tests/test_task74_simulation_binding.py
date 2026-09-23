from types import SimpleNamespace

import pytest

from persistence import (
    OperationalScope,
    open_persistence,
    open_team_status_persistence,
    open_telegram_simulation_binding_store,
    resolve_runtime_context,
)
from profiles.simulation import SimulationScenario


def _provision_scope(db_path, scope):
    team_store = open_team_status_persistence(db_path)
    team_store.ensure_scope(scope)
    return team_store


def _profile_fixture():
    sec = SimulationScenario(
        key="SEC_RUN",
        title="SEC",
        raw={"scenario": {"id": "SEC_RUN"}, "chats": [], "steps": []},
        official_metadata={"operational_profile": "response_team"},
    )
    fire = SimulationScenario(
        key="FIRE_RUN",
        title="FIRE",
        raw={"scenario": {"id": "FIRE_RUN"}, "chats": [], "steps": []},
        official_metadata={"operational_profile": "fire_station"},
    )
    return SimpleNamespace(simulations=(sec, fire), live_operational_profile="response_team")


def test_binding_is_exact_durable_switchable_and_unbindable(tmp_path):
    db_path = str(tmp_path / "binding.sqlite")
    persistence = open_persistence(db_path)
    persistence.write_user("bar-moshe", "commander", "בר משה")
    persistence.close()
    sec_scope = OperationalScope.simulation("SEC_RUN", "sec-a")
    fire_scope = OperationalScope.simulation("FIRE_RUN", "fire-b")
    _provision_scope(db_path, sec_scope)
    _provision_scope(db_path, fire_scope)

    store = open_telegram_simulation_binding_store(db_path)
    store.register_run(scenario_id="SEC_RUN", scenario_run_id="sec-a", operational_profile="response_team")
    store.register_run(scenario_id="FIRE_RUN", scenario_run_id="fire-b", operational_profile="fire_station")
    sec = store.bind(
        telegram_identity="bar-moshe",
        scenario_id="SEC_RUN",
        scenario_run_id="sec-a",
        bound_by="commander",
    )
    assert sec["scope_key"] == sec_scope.key

    reopened = open_telegram_simulation_binding_store(db_path)
    assert reopened.get_binding("bar-moshe")["scenario_run_id"] == "sec-a"
    switched = reopened.bind(
        telegram_identity="bar-moshe",
        scenario_id="FIRE_RUN",
        scenario_run_id="fire-b",
        bound_by="commander",
    )
    assert switched["scope_key"] == fire_scope.key
    assert reopened.get_binding("bar-moshe")["scenario_id"] == "FIRE_RUN"
    assert reopened.unbind("bar-moshe") is True
    assert reopened.get_binding("bar-moshe") is None


def test_binding_rejects_unprovisioned_exact_run(tmp_path):
    db_path = str(tmp_path / "binding-invalid.sqlite")
    persistence = open_persistence(db_path)
    persistence.write_user("bar-moshe", "commander", "בר משה")
    persistence.close()
    store = open_telegram_simulation_binding_store(db_path)
    store.register_run(scenario_id="SEC_RUN", scenario_run_id="sec-a", operational_profile="response_team")
    with pytest.raises(RuntimeError, match="not provisioned"):
        store.bind(
            telegram_identity="bar-moshe",
            scenario_id="SEC_RUN",
            scenario_run_id="missing",
            bound_by="commander",
        )


def test_same_identity_resolves_two_profiles_from_exact_bound_runs(tmp_path):
    db_path = str(tmp_path / "binding-context.sqlite")
    persistence = open_persistence(db_path)
    persistence.write_user("bar-moshe", "commander", "בר משה")
    persistence.close()
    _provision_scope(db_path, OperationalScope.simulation("SEC_RUN", "sec-a"))
    _provision_scope(db_path, OperationalScope.simulation("FIRE_RUN", "fire-b"))
    store = open_telegram_simulation_binding_store(db_path)
    store.register_run(scenario_id="SEC_RUN", scenario_run_id="sec-a", operational_profile="response_team")
    store.register_run(scenario_id="FIRE_RUN", scenario_run_id="fire-b", operational_profile="fire_station")
    profile = _profile_fixture()

    store.bind(telegram_identity="bar-moshe", scenario_id="SEC_RUN", scenario_run_id="sec-a", bound_by="commander")
    persistence = open_persistence(db_path)
    sec_context = resolve_runtime_context(
        identity_id="bar-moshe",
        users_persistence=persistence,
        loaded_profile=profile,
        operational_scope=OperationalScope.simulation("SEC_RUN", "sec-a"),
    )
    assert sec_context.operational_profile.profile_id == "response_team"
    persistence.close()

    store.bind(telegram_identity="bar-moshe", scenario_id="FIRE_RUN", scenario_run_id="fire-b", bound_by="commander")
    persistence = open_persistence(db_path)
    fire_context = resolve_runtime_context(
        identity_id="bar-moshe",
        users_persistence=persistence,
        loaded_profile=profile,
        operational_scope=OperationalScope.simulation("FIRE_RUN", "fire-b"),
    )
    assert fire_context.operational_profile.profile_id == "fire_station"
    assert fire_context.operational_scope.key != sec_context.operational_scope.key
    persistence.close()
