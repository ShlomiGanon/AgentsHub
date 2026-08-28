"""SQLite schema (work_plan.md §2.9 owns this module in full)."""

import sqlite3

USERS_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS users (
    telegram_identity TEXT PRIMARY KEY,
    permission_level TEXT NOT NULL
);
"""


EVENTS_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS events (
    event_id TEXT PRIMARY KEY,
    received_at TEXT NOT NULL,
    source TEXT NOT NULL,
    sender_identity TEXT NOT NULL,
    source_message_id TEXT,

    occurred_at TEXT,
    occurred_at_is_fallback INTEGER NOT NULL DEFAULT 0,

    raw_text TEXT NOT NULL,

    classification TEXT,
    area TEXT,

    entities TEXT,
    description TEXT,
    severity TEXT,

    risk_level TEXT,
    risk_reason TEXT,
    selected_protocol TEXT,
    protocol_reason TEXT,

    clarification_held INTEGER NOT NULL DEFAULT 0,
    clarification_unresolved_field TEXT,
    clarification_resolved_by TEXT,
    clarification_chosen_classification TEXT,

    approval_held INTEGER NOT NULL DEFAULT 0,
    approval_reason TEXT,
    approval_answered_by TEXT,
    approval_answered_at TEXT,

    precedent_matched_event_ids TEXT,
    precedent_closed_by_event_id TEXT,

    insight_text TEXT,
    outcome TEXT,
    outcome_failure_reason TEXT
);
"""

EVENT_STEPS_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS event_steps (
    event_id TEXT NOT NULL REFERENCES events(event_id),
    step_index INTEGER NOT NULL,
    agent_name TEXT NOT NULL,
    task_text TEXT NOT NULL,
    allowed_tools TEXT NOT NULL,
    result_text TEXT,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (event_id, step_index)
);
"""


SUMMARY_TABLE_NAMES = {
    "daily": "daily_summaries",
    "monthly": "monthly_summaries",
    "yearly": "yearly_summaries",
}


def _summary_table_ddl(table_name: str) -> str:
    return f"""
CREATE TABLE IF NOT EXISTS {table_name} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    summary_text TEXT NOT NULL,
    period_start TEXT NOT NULL,
        period_end TEXT NOT NULL,
        generated_at TEXT NOT NULL,
        event_index TEXT,
        UNIQUE (period_start, period_end)
);
"""


DAILY_SUMMARIES_TABLE_DDL = _summary_table_ddl(SUMMARY_TABLE_NAMES["daily"])
MONTHLY_SUMMARIES_TABLE_DDL = _summary_table_ddl(SUMMARY_TABLE_NAMES["monthly"])
YEARLY_SUMMARIES_TABLE_DDL = _summary_table_ddl(SUMMARY_TABLE_NAMES["yearly"])


INDEXES_DDL = """
CREATE INDEX IF NOT EXISTS idx_events_occurred_at ON events(occurred_at);
CREATE INDEX IF NOT EXISTS idx_events_classification_area ON events(classification, area);
CREATE INDEX IF NOT EXISTS idx_event_steps_event_id ON event_steps(event_id);
"""

# Keep migration-era DDL immutable; newer indexes stay separate.
LOG_ENTRIES_INDEXES_DDL = """
CREATE INDEX IF NOT EXISTS idx_log_entries_trace_id ON log_entries(trace_id);
"""

HISTORY_QUERY_INDEXES_DDL = """
CREATE INDEX IF NOT EXISTS idx_events_classification_area_occurred_at
    ON events(classification, area, occurred_at);
CREATE INDEX IF NOT EXISTS idx_events_classification_occurred_at
    ON events(classification, occurred_at);
CREATE INDEX IF NOT EXISTS idx_events_area_occurred_at
    ON events(area, occurred_at);
CREATE INDEX IF NOT EXISTS idx_events_outcome_occurred_at
    ON events(outcome, occurred_at);
CREATE INDEX IF NOT EXISTS idx_events_protocol_occurred_at
    ON events(selected_protocol, occurred_at);
CREATE INDEX IF NOT EXISTS idx_events_received_at ON events(received_at);
"""


# Kind-specific hold data is JSON stored at the persistence boundary.
HELD_EVENTS_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS held_events (
    hold_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    event_id TEXT NOT NULL,
    payload TEXT NOT NULL,
    resolved INTEGER NOT NULL DEFAULT 0,
    resolved_by TEXT,
    resolved_at TEXT,
    resolution TEXT,
    created_at TEXT NOT NULL
);
"""


# State changes and their notification cursor advance in one transaction.
NOTIFICATION_LOG_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS notification_log (
    sequence_id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    event_id TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""

# Open-ended structured log details are serialized as JSON.
LOG_ENTRIES_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS log_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trace_id TEXT,
    timestamp TEXT NOT NULL,
    details TEXT NOT NULL
);
"""

CONVERSATION_MESSAGES_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS conversation_messages (
    message_id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    created_at TEXT NOT NULL,
    event_id TEXT REFERENCES events(event_id)
);
CREATE INDEX IF NOT EXISTS idx_conversation_messages_lookup
    ON conversation_messages(conversation_id, created_at DESC, message_id DESC);
"""


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
    (7, "create held_events table", HELD_EVENTS_TABLE_DDL),
    (8, "create notification_log table", NOTIFICATION_LOG_TABLE_DDL),
    (9, "add source_message_id to events", "ALTER TABLE events ADD COLUMN source_message_id TEXT;"),
    (10, "create log_entries table", LOG_ENTRIES_TABLE_DDL + LOG_ENTRIES_INDEXES_DDL),
    (11, "add indexed history query paths", HISTORY_QUERY_INDEXES_DDL),
    (
        12,
        "add trace conversation deadline and ingestion identity to events",
        "ALTER TABLE events ADD COLUMN trace_id TEXT;"
        "ALTER TABLE events ADD COLUMN conversation_id TEXT;"
        "ALTER TABLE events ADD COLUMN deadline_at TEXT;"
        "ALTER TABLE events ADD COLUMN ingestion_key TEXT;"
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_events_ingestion_key ON events(ingestion_key) WHERE ingestion_key IS NOT NULL;",
    ),
    (13, "create conversation message history", CONVERSATION_MESSAGES_TABLE_DDL),
    (
        14,
        "add protocol step dependency metadata",
        "ALTER TABLE event_steps ADD COLUMN step_id TEXT;"
        "ALTER TABLE event_steps ADD COLUMN depends_on TEXT NOT NULL DEFAULT '[]';",
    ),
]


def run_migrations(db_path: str) -> None:
    connection = sqlite3.connect(db_path)
    try:
        current_version = connection.execute("PRAGMA user_version").fetchone()[0]
        migrated = False
        for version, _description, sql in MIGRATIONS:
            if version <= current_version:
                continue

            if version == 6:
                for table_name in ("daily_summaries", "monthly_summaries", "yearly_summaries"):
                    columns = {row[1] for row in connection.execute(f"PRAGMA table_info({table_name})").fetchall()}
                    if "event_index" not in columns:
                        connection.execute(f"ALTER TABLE {table_name} ADD COLUMN event_index TEXT")
            elif version == 9:
                columns = {row[1] for row in connection.execute("PRAGMA table_info(events)").fetchall()}
                if "source_message_id" not in columns:
                    connection.execute("ALTER TABLE events ADD COLUMN source_message_id TEXT")
            else:
                connection.executescript(sql)

            connection.execute(f"PRAGMA user_version = {version}")
            connection.commit()
            migrated = True
        if migrated:
            connection.execute("ANALYZE")
            connection.commit()
    finally:
        connection.close()
