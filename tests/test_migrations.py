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
    assert {"direct_tool_name", "direct_tool_arguments"} <= columns


def test_events_include_fast_path_business_fields(tmp_path):
    db_path = str(tmp_path / "fast-path-fields.db")
    run_migrations(db_path)

    connection = sqlite3.connect(db_path)
    try:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(events)")}
    finally:
        connection.close()

    assert "business_fields" in columns


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


def test_migration_twenty_adds_attendance_interval_columns_to_existing_events(tmp_path):
    db_path = str(tmp_path / "version-nineteen.db")
    connection = sqlite3.connect(db_path)
    try:
        connection.execute(
            "CREATE TABLE events (event_id TEXT PRIMARY KEY, received_at TEXT NOT NULL, source TEXT NOT NULL, "
            "sender_identity TEXT NOT NULL, raw_text TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO events(event_id, received_at, source, sender_identity, raw_text) "
            "VALUES ('legacy-attendance', '2026-01-01', 'telegram', '42', 'legacy report')"
        )
        connection.execute("PRAGMA user_version = 19")
        connection.commit()
    finally:
        connection.close()

    run_migrations(db_path)

    connection = sqlite3.connect(db_path)
    try:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(events)")}
        legacy_event = connection.execute(
            "SELECT event_id, raw_text FROM events WHERE event_id = 'legacy-attendance'"
        ).fetchone()
    finally:
        connection.close()

    assert {"availability_start", "availability_end"} <= columns
    assert legacy_event == ("legacy-attendance", "legacy report")


def test_schema_integrity_repairs_version_twenty_one_drift_without_losing_events(tmp_path):
    from persistence import open_persistence

    db_path = str(tmp_path / "drifted-version-twenty-one.db")
    run_migrations(db_path)

    connection = sqlite3.connect(db_path)
    try:
        connection.execute(
            "INSERT INTO events(event_id, received_at, source, sender_identity, raw_text) "
            "VALUES ('existing-event', '2026-01-01', 'telegram', '42', 'preserve me')"
        )
        connection.execute("ALTER TABLE events DROP COLUMN availability_start")
        connection.execute("ALTER TABLE events DROP COLUMN availability_end")
        connection.execute("PRAGMA user_version = 21")
        connection.commit()

        drifted_columns = {row[1] for row in connection.execute("PRAGMA table_info(events)")}
        assert "business_fields" in drifted_columns
        assert "availability_start" not in drifted_columns
        assert "availability_end" not in drifted_columns
    finally:
        connection.close()

    run_migrations(db_path)
    run_migrations(db_path)

    connection = sqlite3.connect(db_path)
    try:
        repaired_version = connection.execute("PRAGMA user_version").fetchone()[0]
        repaired_columns = {row[1] for row in connection.execute("PRAGMA table_info(events)")}
        existing_event = connection.execute(
            "SELECT event_id, raw_text FROM events WHERE event_id = 'existing-event'"
        ).fetchone()
    finally:
        connection.close()

    assert repaired_version == MIGRATIONS[-1][0]
    assert {"availability_start", "availability_end", "business_fields"} <= repaired_columns
    assert {"action_state", "action_state_updated_at", "action_failure_reason", "action_tool_receipts"} <= repaired_columns
    assert existing_event == ("existing-event", "preserve me")

    persistence = open_persistence(db_path)
    try:
        new_event_id = persistence.append_event({
            "received_at": "2026-01-02T10:00:00+00:00",
            "source": "telegram",
            "sender_identity": "43",
            "raw_text": "new report",
            "availability_start": "2026-01-04T00:00:00+02:00",
            "availability_end": "2026-01-06T20:00:00+02:00",
            "business_fields": {"availability": "unavailable"},
        })
        inserted = persistence.fetch_event(new_event_id)
    finally:
        persistence.close()

    assert inserted is not None
    assert inserted["availability_start"] == "2026-01-04T00:00:00+02:00"
    assert inserted["availability_end"] == "2026-01-06T20:00:00+02:00"
    assert inserted["business_fields"] == {"availability": "unavailable"}


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
