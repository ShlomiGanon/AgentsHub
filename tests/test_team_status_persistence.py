from datetime import datetime, timedelta, timezone
import sqlite3

import pytest

from persistence import TeamStatusPersistenceError, open_team_status_persistence
from persistence.sqlite_store import SQLitePersistence


def _timestamp(day: int = 3, hour: int = 5, minute: int = 0) -> str:
    return datetime(2026, 9, day, hour, minute, tzinfo=timezone.utc).isoformat()


def test_empty_roster_cannot_be_approved(tmp_path):
    store = open_team_status_persistence(str(tmp_path / "team-status.db"))

    with pytest.raises(TeamStatusPersistenceError, match="empty roster"):
        store.approve_roster("commander-1", _timestamp())


def test_attendance_cycle_cannot_open_before_whole_roster_approval(tmp_path):
    store = open_team_status_persistence(str(tmp_path / "team-status.db"))
    store.register_member("101", "Alex Cohen", _timestamp())

    with pytest.raises(TeamStatusPersistenceError, match="approve the roster"):
        store.open_cycle("2026-09-03", _timestamp(), _timestamp(hour=6))


def test_member_added_after_roster_approval_cannot_submit_until_next_approval(tmp_path):
    store = open_team_status_persistence(str(tmp_path / "team-status.db"))
    store.register_member("101", "Alex Cohen", _timestamp())
    store.approve_roster("commander-1", _timestamp())
    store.register_member("999", "Unapproved Member", _timestamp())
    store.open_cycle("2026-09-03", _timestamp(), _timestamp(hour=6))

    with pytest.raises(TeamStatusPersistenceError, match="approved roster"):
        store.record_response(
            telegram_identity="999",
            source_message_id="message-999",
            availability="available",
            original_text="Available",
            received_at=_timestamp(minute=10),
        )


def test_replayed_telegram_message_is_idempotent(tmp_path):
    store = open_team_status_persistence(str(tmp_path / "team-status.db"))
    store.register_member("101", "Alex Cohen", _timestamp())
    store.approve_roster("commander-1", _timestamp())
    store.open_cycle("2026-09-03", _timestamp(), _timestamp(hour=6))

    first = store.record_response(
        telegram_identity="101",
        source_message_id="telegram-message-42",
        availability="available",
        original_text="Available",
        received_at=_timestamp(minute=10),
    )
    replay = store.record_response(
        telegram_identity="101",
        source_message_id="telegram-message-42",
        availability="available",
        original_text="Available",
        received_at=_timestamp(minute=10),
    )

    assert replay["response_id"] == first["response_id"]
    assert replay["source_message_id"] == "telegram-message-42"


def test_late_response_is_pending_and_does_not_change_snapshot(tmp_path):
    store = open_team_status_persistence(str(tmp_path / "team-status.db"))
    store.register_member("101", "Alex Cohen", _timestamp())
    store.approve_roster("commander-1", _timestamp())
    store.open_cycle("2026-09-03", _timestamp(), _timestamp(hour=6))

    late = store.record_response(
        telegram_identity="101",
        source_message_id="late-message",
        availability="available",
        original_text="Available now",
        received_at=_timestamp(hour=7),
    )

    assert late["approval_status"] == "pending"
    assert store.availability_snapshot(_timestamp(hour=7))[0]["availability"] == "awaiting_response"


def test_operational_history_and_team_status_use_different_schemas(tmp_path):
    operational_path = tmp_path / "operational-history.db"
    status_path = tmp_path / "team-status.db"
    operational = SQLitePersistence(str(operational_path))
    open_team_status_persistence(str(status_path))
    operational.close()

    with sqlite3.connect(operational_path) as connection:
        operational_tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    with sqlite3.connect(status_path) as connection:
        status_tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}

    assert "events" in operational_tables
    assert "team_members" not in operational_tables
    assert "team_members" in status_tables
    assert "events" not in status_tables
