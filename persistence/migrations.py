"""Schema migrations (work_plan.md §2.10).

Creates the full schema from an empty file, and is safe to invoke on an
already-current database — it applies nothing and exits cleanly. The
applied version is recorded inside the database itself via SQLite's
built-in `PRAGMA user_version`, so no separate bookkeeping table is
needed.
"""

import sqlite3

from persistence.schema import (
    DAILY_SUMMARIES_TABLE_DDL,
    EVENT_STEPS_TABLE_DDL,
    EVENTS_TABLE_DDL,
    INDEXES_DDL,
    MONTHLY_SUMMARIES_TABLE_DDL,
    USERS_TABLE_DDL,
    YEARLY_SUMMARIES_TABLE_DDL,
)

# Numbered, ordered, one entry per migration. Never edit a past entry's
# SQL after it has shipped — add a new numbered migration instead, the
# same discipline any other schema-migration system requires.
MIGRATIONS: list[tuple[int, str, str]] = [
    (1, "create users table", USERS_TABLE_DDL),
    (2, "create events table", EVENTS_TABLE_DDL),
    (3, "create event_steps table", EVENT_STEPS_TABLE_DDL),
    (4, "create summary tables", DAILY_SUMMARIES_TABLE_DDL + MONTHLY_SUMMARIES_TABLE_DDL + YEARLY_SUMMARIES_TABLE_DDL),
    (5, "create indexes", INDEXES_DDL),
]


def run_migrations(db_path: str) -> None:
    connection = sqlite3.connect(db_path)

    try:
        current_version = connection.execute("PRAGMA user_version").fetchone()[0]

        for version, _description, sql in MIGRATIONS:
            if version <= current_version:
                continue

            connection.executescript(sql)
            connection.execute(f"PRAGMA user_version = {version}")
            connection.commit()
    finally:
        connection.close()
