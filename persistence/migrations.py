"""Schema migrations (work_plan.md §2.10).

Creates the full schema from an empty file, and is safe to invoke on an
already-current database — it applies nothing and exits cleanly. The
applied version is recorded inside the database itself via SQLite's
built-in `PRAGMA user_version`, so no separate bookkeeping table is
needed.
"""

import sqlite3

from persistence.schema import (
    EVENT_STEPS_TABLE_DDL,
    EVENTS_TABLE_DDL,
    HELD_EVENTS_TABLE_DDL,
    INDEXES_DDL,
    NOTIFICATION_LOG_TABLE_DDL,
    USERS_TABLE_DDL,
)


def _summary_v4_ddl(table_name: str) -> str:
    return f"""
CREATE TABLE IF NOT EXISTS {table_name} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    summary_text TEXT NOT NULL,
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,
    generated_at TEXT NOT NULL,
    UNIQUE (period_start, period_end)
);
"""


SUMMARY_TABLES_V4_DDL = (
    _summary_v4_ddl("daily_summaries")
    + _summary_v4_ddl("monthly_summaries")
    + _summary_v4_ddl("yearly_summaries")
)

# Numbered, ordered, one entry per migration. Never edit a past entry's
# SQL after it has shipped — add a new numbered migration instead, the
# same discipline any other schema-migration system requires.
MIGRATIONS: list[tuple[int, str, str]] = [
    (1, "create users table", USERS_TABLE_DDL),
    (2, "create events table", EVENTS_TABLE_DDL),
    (3, "create event_steps table", EVENT_STEPS_TABLE_DDL),
    (4, "create summary tables", SUMMARY_TABLES_V4_DDL),
    (5, "create indexes", INDEXES_DDL),
    (
        6,
        "add event indexes to summaries",
        "ALTER TABLE daily_summaries ADD COLUMN event_index TEXT;"
        "ALTER TABLE monthly_summaries ADD COLUMN event_index TEXT;"
        "ALTER TABLE yearly_summaries ADD COLUMN event_index TEXT;",
    ),
    # Was also numbered 6 — a merge collision between two branches that
    # each added their own migration 6. run_migrations skips any entry
    # whose version is <= the current one, so the second "6" was silently
    # never applied and the held_events table never got created. Fixed by
    # renumbering to 7; never renumber a migration a real database may
    # already have applied under its old number — this one never actually
    # ran, so renumbering it is safe (see docs/progress.md).
    (7, "create held_events table", HELD_EVENTS_TABLE_DDL),
    (8, "create notification_log table", NOTIFICATION_LOG_TABLE_DDL),
    (9, "add source_message_id to events", "ALTER TABLE events ADD COLUMN source_message_id TEXT;"),
]


def run_migrations(db_path: str) -> None:
    connection = sqlite3.connect(db_path)

    try:
        current_version = connection.execute("PRAGMA user_version").fetchone()[0]

        for version, _description, sql in MIGRATIONS:
            if version <= current_version:
                continue

            if version == 6:
                for table_name in ("daily_summaries", "monthly_summaries", "yearly_summaries"):
                    columns = {
                        row[1]
                        for row in connection.execute(f"PRAGMA table_info({table_name})").fetchall()
                    }
                    if "event_index" not in columns:
                        connection.execute(f"ALTER TABLE {table_name} ADD COLUMN event_index TEXT")
            elif version == 9:
                # A fresh database's migration 2 already creates `events`
                # with this column (persistence/schema.py's EVENTS_TABLE_DDL
                # was updated directly, the same choice migration 4 made for
                # the summary tables before migration 6 needed the same
                # idempotency check for a pre-existing database) — only an
                # already-migrated database, created before this column
                # existed, actually needs the ALTER.
                columns = {row[1] for row in connection.execute("PRAGMA table_info(events)").fetchall()}
                if "source_message_id" not in columns:
                    connection.execute("ALTER TABLE events ADD COLUMN source_message_id TEXT")
            else:
                connection.executescript(sql)

            connection.execute(f"PRAGMA user_version = {version}")
            connection.commit()
    finally:
        connection.close()
