from types import SimpleNamespace
import sqlite3

from persistence.operational_scope import OperationalScope
from persistence.sqlite_store import SQLitePersistence
from persistence.team_status_store import SQLiteTeamStatusPersistence
from profiles.simulation import SimulationPersona, SimulationRoster, simulation_user_telegram_id
from profiles.simulation_provisioning import repair_legacy_live_simulation_memberships


def test_simulation_identity_is_not_a_live_admin_identity(tmp_path):
    store = SQLitePersistence(str(tmp_path / "history.db"))
    store.ensure_user_exists("9000000000000001", "viewer", "Sim Person", identity_kind="SIMULATION")
    store.write_user("real-1", "viewer", "Real Person")

    assert store.read_user("9000000000000001")["auto_register"] is False
    assert [row["telegram_identity"] for row in store.list_live_users()] == ["real-1"]
    assert {row["telegram_identity"] for row in store.list_users()} == {"9000000000000001", "real-1"}
    store.close()


def test_membership_reads_are_scope_bound_and_retirement_preserves_history(tmp_path):
    store = SQLiteTeamStatusPersistence(str(tmp_path / "team.db"))
    live = OperationalScope.live()
    sec = OperationalScope.simulation("SEC_001", "run-sec")
    fire = OperationalScope.simulation("FIRE_002", "run-fire")
    store.ensure_scope(sec, baseline={"team": {"members": [{"telegram_identity": "same", "full_name": "Same Name"}]}})
    store.ensure_scope(fire, baseline={"team": {"members": [{"telegram_identity": "same", "full_name": "Same Name"}]}})
    store.register_member("same", "Same Name", scope=live)
    store.approve_roster("live-commander", scope=live)
    store.open_cycle("2026-09-20", "2026-09-20T07:00:00+00:00", "2026-09-20T08:00:00+00:00", scope=live)
    store.record_response(
        telegram_identity="same",
        source_message_id="live-response",
        availability="available",
        original_text="available",
        received_at="2026-09-20T07:30:00+00:00",
        scope=live,
    )

    assert [m["telegram_identity"] for m in store.get_members(scope=sec)] == ["same"]
    assert [m["telegram_identity"] for m in store.get_members(scope=fire)] == ["same"]
    assert [m["telegram_identity"] for m in store.get_members(scope=live)] == ["same"]

    assert store.retire_member("same", scope=live) is True
    assert store.get_members(scope=live) == []
    assert store.get_member_state("same", scope=live) is None
    assert store.retire_member("same", scope=live) is False
    with sqlite3.connect(tmp_path / "team.db") as connection:
        assert connection.execute("SELECT COUNT(*) FROM attendance_responses").fetchone()[0] == 1


def test_official_legacy_repair_is_identity_based_and_idempotent(tmp_path):
    history = SQLitePersistence(str(tmp_path / "history.db"))
    team_path = str(tmp_path / "team.db")
    team = SQLiteTeamStatusPersistence(team_path)
    live = OperationalScope.live()
    identity = simulation_user_telegram_id(7)
    history.ensure_user_exists(identity, "viewer", "Synthetic One", identity_kind="LIVE")
    team.register_member(identity, "Synthetic One", scope=live)
    team.approve_roster("commander", scope=live)

    roster = SimulationRoster(key="team_status", open=SQLiteTeamStatusPersistence, db_path=team_path)
    profile = SimpleNamespace(
        simulation_users=(SimulationPersona(key="synthetic", offset=7, full_name="Synthetic One"),),
        simulation_rosters=(roster,),
    )

    first = repair_legacy_live_simulation_memberships(history, profile)
    second = repair_legacy_live_simulation_memberships(history, profile)

    assert first.retired_live_memberships == (("team_status", identity),)
    assert second.retired_live_memberships == ()
    assert identity in {row["telegram_identity"] for row in history.list_users()}
    assert identity not in {row["telegram_identity"] for row in history.list_live_users()}
    assert team.get_members(scope=live) == []
    history.close()
