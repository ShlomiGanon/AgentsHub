"""SQLite schema (work_plan.md §2.9 owns this module in full).

Implements the users table (§1.10 slice, Mission 1), the events and
event_steps tables (§2.3, §2.5, §2.8), the three summary tables (§2.6,
§2.8), and the held_events table (§6.7, Mission 6 — owned by
orchestrator holds per work_plan.md's branch grouping, not the Data
Layer, but built here since that's where table DDL lives).

Schema notes:
- `entities` and `precedent_matched_event_ids` are JSON-encoded TEXT —
  SQLite has no native array type, and nothing above persistence.interface
  ever sees the encoding (persistence.sqlite_backend decodes/encodes at
  the boundary).
- `raw_text` (§2.5) is written once by `append_event` and never appears in
  any UPDATE this module builds — see persistence/sqlite_backend.py's
  update-column whitelist.
- `occurred_at` is nullable, unlike the rest of the envelope. §6.11 writes
  the event (raw text + envelope) *before* extraction runs, so a
  Telegram-sourced event has no occurrence timestamp yet at `append_event`
  time — extraction fills it in later via `update_event`. A sensor-sourced
  event can have it set at append time, since 7.3 sets it equal to
  `received_at` immediately with nothing to extract.
- `event_steps` uses a composite primary key `(event_id, step_index)`,
  which is also its own uniqueness guarantee and its index (§2.8).
- Each summary table's `UNIQUE(period_start, period_end)` is both the §2.8
  index on period boundaries and what lets `write_summary` upsert instead
  of duplicating a row for a period that already has one (§5.5).
"""

USERS_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS users (
    telegram_identity TEXT PRIMARY KEY,
    permission_level TEXT NOT NULL
);
"""

# -- Events (§2.3, §2.5) -------------------------------------------------

EVENTS_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS events (
    event_id TEXT PRIMARY KEY,
    received_at TEXT NOT NULL,
    source TEXT NOT NULL,
    sender_identity TEXT NOT NULL,

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

# One record per executed step (§2.3). `allowed_tools` is JSON-encoded TEXT.
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

# -- Summaries (§2.6) -----------------------------------------------------

# The name persistence.sqlite_backend maps a `level` argument to.
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
    UNIQUE (period_start, period_end)
);
"""


DAILY_SUMMARIES_TABLE_DDL = _summary_table_ddl(SUMMARY_TABLE_NAMES["daily"])
MONTHLY_SUMMARIES_TABLE_DDL = _summary_table_ddl(SUMMARY_TABLE_NAMES["monthly"])
YEARLY_SUMMARIES_TABLE_DDL = _summary_table_ddl(SUMMARY_TABLE_NAMES["yearly"])

# -- Indexes (§2.8) ---------------------------------------------------------

INDEXES_DDL = """
CREATE INDEX IF NOT EXISTS idx_events_occurred_at ON events(occurred_at);
CREATE INDEX IF NOT EXISTS idx_events_classification_area ON events(classification, area);
CREATE INDEX IF NOT EXISTS idx_event_steps_event_id ON event_steps(event_id);
"""


# -- Held events (§6.7; both hold kinds share one table) --------------------

# `kind` distinguishes "clarification" from "approval" rows — an event is in
# at most one at a time (docs/vocabulary.md#hold-states), but the table
# itself doesn't enforce that; the orchestration layer does. `payload` and
# `resolution` are JSON-encoded TEXT, kind-specific (an approval hold's
# payload carries the selected protocol or candidates + assessed risk; a
# future clarification hold's would carry the unresolved field) — decoded
# only at the persistence.sqlite_backend boundary, same as the events
# table's JSON columns. This table is distinct from the events table's own
# clarification_*/approval_* columns: those record the *resolved outcome*
# on the event itself; this table is the operational queue of currently-open
# holds, read by whoever prompts a commander and writes an answer back.
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
