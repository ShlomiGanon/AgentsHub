import json
import sqlite3

import pytest

from persistence import open_persistence, open_surveillance_persistence, open_team_status_persistence
from persistence.runtime_cleanup import clean_unified_test_runtime


def _fixture_root(tmp_path):
    root = tmp_path / "data" / "unified_test"
    root.mkdir(parents=True)

    history = open_persistence(str(root / "unified_history.db"))
    history.write_user("seed-user", "viewer", "Seed User")
    history.write_group("-1", "main_agent", "Seed Group")
    history.append_event(
        {
            "event_id": "runtime-event",
            "received_at": "2026-09-17T08:00:00+00:00",
            "source": "simulator",
            "sender_identity": "seed-user",
            "source_message_id": "runtime-message",
            "raw_text": "runtime message",
            "steps": [
                {
                    "step_index": 0,
                    "agent_name": "agent",
                    "task_text": "runtime task",
                    "allowed_tools": [],
                }
            ],
        }
    )
    history.append_conversation_message(
        "conversation-1", "user", "runtime message", ttl_hours=24, max_turns=6, event_id="runtime-event"
    )
    history.close()

    surveillance = open_surveillance_persistence(str(root / "unified_surveillance.db"))
    surveillance.dispatch_drone(target_area="north_gate", incident_description="runtime mission")

    team = open_team_status_persistence(str(root / "unified_team_status.db"))
    team.register_member("member", "Seed Member", "2026-09-17T08:00:00+00:00")
    team.approve_roster("commander", "2026-09-17T08:00:00+00:00")
    team.open_cycle("seed-cycle", "2026-09-17T08:00:00+00:00", "2026-09-18T08:00:00+00:00")
    team.open_cycle("runtime-cycle", "2026-09-18T08:00:00+00:00", "2026-09-19T08:00:00+00:00")
    team.record_response(
        telegram_identity="member",
        source_message_id="attendance-message",
        availability="available",
        original_text="available",
        received_at="2026-09-17T08:01:00+00:00",
    )

    runs = root / "simulation_runs" / "run-1"
    runs.mkdir(parents=True)
    (runs / "baseline.json").write_text(json.dumps({"created_from_runtime_db": False}), encoding="utf-8")
    (runs / "run.json").write_text(json.dumps({"status": "active"}), encoding="utf-8")
    (root / "unified_history.db.notification_cursor").write_text("12", encoding="ascii")
    (root / "unified_history.db.settings.json").write_text("{\"risk_threshold\": 3}", encoding="utf-8")
    return root


def test_cleanup_preserves_seed_and_clears_runtime_state(tmp_path):
    root = _fixture_root(tmp_path)

    report = clean_unified_test_runtime(root)

    assert report.removed["events"] == 1
    assert report.removed["event_steps"] == 1
    assert report.removed["conversation_messages"] == 1
    assert report.removed["surveillance.drone_missions"] == 1
    assert report.removed["team_status.attendance_responses"] == 1
    assert report.removed["team_status.runtime_cycles_removed"] == 1
    assert report.after["users"] == report.before["users"] == 1
    assert report.after["telegram_groups"] == report.before["telegram_groups"] == 1
    assert report.after["team_status.team_members"] == report.before["team_status.team_members"] == 1
    assert report.after["team_status.roster_approval"] == report.before["team_status.roster_approval"] == 1
    assert report.after["surveillance.cameras"] == report.before["surveillance.cameras"] == 5
    assert report.after["surveillance.drones"] == report.before["surveillance.drones"] == 3
    assert (root / "simulation_runs" / "run-1" / "baseline.json").exists()
    assert not (root / "simulation_runs" / "run-1" / "run.json").exists()
    assert (root / "unified_history.db.settings.json").exists()

    with sqlite3.connect(root / "unified_team_status.db") as connection:
        assert connection.execute("SELECT COUNT(*) FROM attendance_cycles").fetchone()[0] == 1


def test_cleanup_is_idempotent_and_scope_limited(tmp_path):
    root = _fixture_root(tmp_path)
    clean_unified_test_runtime(root)
    second = clean_unified_test_runtime(root)

    assert all(value == 0 for value in second.removed.values())
    with pytest.raises(ValueError, match="restricted"):
        clean_unified_test_runtime(root, profile_module="profiles.other")


def test_cleanup_dry_run_does_not_mutate(tmp_path):
    root = _fixture_root(tmp_path)
    report = clean_unified_test_runtime(root, dry_run=True)

    assert report.removed == {}
    assert (root / "simulation_runs" / "run-1" / "run.json").exists()
    with sqlite3.connect(root / "unified_history.db") as connection:
        assert connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1
