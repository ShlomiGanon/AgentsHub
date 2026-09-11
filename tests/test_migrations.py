import sqlite3

from persistence.schema import MIGRATIONS, run_migrations


def _table_names(db_path):
    connection = sqlite3.connect(db_path)
    try:
        rows = connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        return {row[0] for row in rows}
    finally:
        connection.close()


def test_fresh_file_gets_the_full_schema(tmp_path):
    db_path = str(tmp_path / "fresh.db")

    run_migrations(db_path)

    tables = _table_names(db_path)
    for expected in (
        "users", "events", "event_steps", "daily_summaries", "monthly_summaries", "yearly_summaries",
        "held_events", "telegram_groups",
    ):
        assert expected in tables


def test_user_version_reflects_the_latest_migration(tmp_path):
    db_path = str(tmp_path / "fresh.db")

    run_migrations(db_path)

    connection = sqlite3.connect(db_path)
    try:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
    finally:
        connection.close()

    assert version == MIGRATIONS[-1][0]


def test_event_steps_include_resumable_event_data_wait_columns(tmp_path):
    db_path = str(tmp_path / "event-data-waits.db")
    run_migrations(db_path)

    connection = sqlite3.connect(db_path)
    try:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(event_steps)")}
    finally:
        connection.close()

    assert {"required_event_fields", "missing_event_fields", "status", "failure_reason"} <= columns


def test_migration_seventeen_adds_safe_sender_permission_snapshot_to_legacy_events(tmp_path):
    db_path = str(tmp_path / "version-sixteen.db")
    connection = sqlite3.connect(db_path)
    try:
        connection.execute(
            "CREATE TABLE events ("
            "event_id TEXT PRIMARY KEY, received_at TEXT NOT NULL, source TEXT NOT NULL, "
            "sender_identity TEXT NOT NULL, raw_text TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO events(event_id, received_at, source, sender_identity, raw_text) "
            "VALUES ('legacy', '2026-01-01', 'sensor', 'sensor-1', 'smoke')"
        )
        connection.execute("PRAGMA user_version = 16")
        connection.commit()
    finally:
        connection.close()

    run_migrations(db_path)

    connection = sqlite3.connect(db_path)
    try:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(events)")}
        snapshot = connection.execute(
            "SELECT sender_permission_level FROM events WHERE event_id = 'legacy'"
        ).fetchone()[0]
    finally:
        connection.close()

    assert "sender_permission_level" in columns
    assert snapshot == "viewer"


def test_migration_eighteen_adds_empty_full_name_without_losing_users(tmp_path):
    db_path = str(tmp_path / "version-seventeen.db")
    connection = sqlite3.connect(db_path)
    try:
        connection.execute("CREATE TABLE users (telegram_identity TEXT PRIMARY KEY, permission_level TEXT NOT NULL)")
        connection.execute("INSERT INTO users VALUES ('42', 'viewer')")
        connection.execute("PRAGMA user_version = 17")
        connection.commit()
    finally:
        connection.close()

    run_migrations(db_path)
    connection = sqlite3.connect(db_path)
    try:
        row = connection.execute("SELECT telegram_identity, permission_level, full_name FROM users").fetchone()
    finally:
        connection.close()
    assert row == ("42", "viewer", "")


def test_migration_nineteen_marks_existing_users_and_groups_as_manually_approved(tmp_path):
    db_path = str(tmp_path / "version-eighteen.db")
    connection = sqlite3.connect(db_path)
    try:
        connection.execute(
            "CREATE TABLE users (telegram_identity TEXT PRIMARY KEY, permission_level TEXT NOT NULL, "
            "full_name TEXT NOT NULL DEFAULT '')"
        )
        connection.execute("INSERT INTO users VALUES ('42', 'viewer', 'Dana Levi')")
        connection.execute(
            "CREATE TABLE telegram_groups (chat_id TEXT PRIMARY KEY, agent_name TEXT NOT NULL, "
            "label TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL)"
        )
        connection.execute("INSERT INTO telegram_groups VALUES ('-1001', 'main_agent', 'Ops', '2026-01-01')")
        connection.execute("PRAGMA user_version = 18")
        connection.commit()
    finally:
        connection.close()

    run_migrations(db_path)
    connection = sqlite3.connect(db_path)
    try:
        user = connection.execute("SELECT full_name, auto_register FROM users WHERE telegram_identity='42'").fetchone()
        group = connection.execute("SELECT agent_name, auto_register FROM telegram_groups WHERE chat_id='-1001'").fetchone()
    finally:
        connection.close()
    assert user == ("Dana Levi", 0)
    assert group == ("main_agent", 0)


def test_history_query_indexes_are_present_on_a_fresh_database(tmp_path):
    import sqlite3

    db_path = str(tmp_path / "history-indexes.db")
    run_migrations(db_path)
    connection = sqlite3.connect(db_path)
    try:
        index_names = {row[1] for row in connection.execute("PRAGMA index_list(events)").fetchall()}
    finally:
        connection.close()

    assert {
        "idx_events_classification_area_occurred_at",
        "idx_events_classification_occurred_at",
        "idx_events_area_occurred_at",
        "idx_events_outcome_occurred_at",
        "idx_events_protocol_occurred_at",
        "idx_events_received_at",
    } <= index_names


def test_running_again_on_an_up_to_date_database_applies_nothing(tmp_path):
    db_path = str(tmp_path / "fresh.db")

    run_migrations(db_path)
    tables_before = _table_names(db_path)

    run_migrations(db_path)  # must not raise, must not change anything
    tables_after = _table_names(db_path)

    assert tables_before == tables_after


def test_migration_six_adds_event_index_to_an_existing_version_five_database(tmp_path):
    db_path = str(tmp_path / "version-five.db")
    connection = sqlite3.connect(db_path)
    try:
        for version, _description, sql in MIGRATIONS[:5]:
            connection.executescript(sql)
            connection.execute(f"PRAGMA user_version = {version}")
        connection.commit()
    finally:
        connection.close()

    run_migrations(db_path)

    connection = sqlite3.connect(db_path)
    try:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(daily_summaries)")}
    finally:
        connection.close()

    assert "event_index" in columns

import json

from config.live_settings import SettingsStore


def test_first_run_takes_starting_values_from_profile_and_writes_file(tmp_path):
    db_path = str(tmp_path / "deployment.db")

    store = SettingsStore(db_path, starting_retry_count=3, starting_risk_threshold=0.5, starting_lookback_window_days=30)

    assert store.get_retry_count() == 3
    assert store.get_risk_threshold() == 0.5
    assert store.get_lookback_window_days() == 30
    assert (tmp_path / "deployment.db.settings.json").exists()


def test_later_run_prefers_the_settings_file_over_profile_starting_values(tmp_path):
    db_path = str(tmp_path / "deployment.db")

    first = SettingsStore(db_path, starting_retry_count=3, starting_risk_threshold=0.5, starting_lookback_window_days=30)
    first.set_risk_threshold(0.9)

    second = SettingsStore(db_path, starting_retry_count=3, starting_risk_threshold=0.5, starting_lookback_window_days=30)

    assert second.get_risk_threshold() == 0.9


def test_change_is_written_before_it_is_considered_confirmed(tmp_path):
    db_path = str(tmp_path / "deployment.db")
    settings_path = tmp_path / "deployment.db.settings.json"

    store = SettingsStore(db_path, starting_retry_count=3, starting_risk_threshold=0.5, starting_lookback_window_days=30)
    store.set_retry_count(7)

    on_disk = json.loads(settings_path.read_text(encoding="utf-8"))
    assert on_disk["retry_count"] == 7


def test_settings_file_lives_beside_the_database_not_the_profile(tmp_path):
    db_path = str(tmp_path / "sub" / "deployment.db")
    (tmp_path / "sub").mkdir()

    SettingsStore(db_path, starting_retry_count=1, starting_risk_threshold=0.1, starting_lookback_window_days=1)

    assert (tmp_path / "sub" / "deployment.db.settings.json").exists()
