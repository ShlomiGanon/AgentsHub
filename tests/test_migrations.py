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
    for expected in ("users", "events", "event_steps", "daily_summaries", "monthly_summaries", "yearly_summaries", "held_events"):
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

from config.settings_store import SettingsStore


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
