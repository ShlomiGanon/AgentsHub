from persistence import OperationalScope, open_persistence, open_surveillance_persistence, open_team_status_persistence
from profiles import unified_test


def test_unified_bootstrap_keeps_live_operational_state_empty_but_preserves_service_identity(tmp_path, monkeypatch):
    history_path = str(tmp_path / "history.db")
    surveillance_path = str(tmp_path / "surveillance.db")
    team_path = str(tmp_path / "team.db")
    monkeypatch.setattr(unified_test, "DB_PATH", history_path)
    monkeypatch.setattr(unified_test, "UNIFIED_SURVEILLANCE_DB_PATH", surveillance_path)
    monkeypatch.setattr(unified_test, "UNIFIED_TEAM_STATUS_DB_PATH", team_path)

    unified_test.ensure_seed_data()

    history = open_persistence(history_path)
    surveillance = open_surveillance_persistence(surveillance_path, seed_demo_data=False)
    team = open_team_status_persistence(team_path)
    assert history.read_user("bot-service") is not None
    assert team.list_members(approved_only=False, scope=OperationalScope.live()) == []
    assert surveillance.list_cameras(scope=OperationalScope.live()) == []
    assert surveillance.list_drones(scope=OperationalScope.live()) == []


def test_simulation_scope_still_gets_surveillance_and_roster_baseline(tmp_path):
    surveillance = open_surveillance_persistence(str(tmp_path / "surveillance.db"), seed_demo_data=False)
    team = open_team_status_persistence(str(tmp_path / "team.db"))
    scope = OperationalScope.simulation("SEC_001_PHASE_1", "run-69b")
    surveillance.ensure_scope(scope)
    team.ensure_scope(scope, baseline={"team": {"members": [{"telegram_identity": "sim-1", "full_name": "Sim"}]}})
    assert surveillance.list_cameras(scope=scope)
    assert surveillance.list_drones(scope=scope)
    assert team.list_members(scope=scope)
    assert surveillance.list_cameras(scope=OperationalScope.live()) == []
    assert team.list_members(approved_only=False, scope=OperationalScope.live()) == []
