import sqlite3

from persistence.migrations import MIGRATIONS, run_migrations


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
