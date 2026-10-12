"""Dispatch-log table for neighboring-force requests on a profile database."""

from __future__ import annotations

import sqlite3
import uuid
from datetime import timedelta
from pathlib import Path

from persistence.response_team_support import _parse_timestamp, _utc_now

# --- schema ---

_NEIGHBORING_FORCE_SCHEMA = """
CREATE TABLE IF NOT EXISTS neighboring_force_dispatches (
    request_id TEXT PRIMARY KEY,
    force_kind TEXT NOT NULL,
    unit_count INTEGER NOT NULL,
    origin_area TEXT NOT NULL,
    target_area TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('en_route', 'arrived')),
    dispatched_at TEXT NOT NULL,
    eta_seconds INTEGER NOT NULL,
    arrived_at TEXT,
    note TEXT NOT NULL DEFAULT '',
    event_id TEXT,
    incident_id TEXT,
    released_at TEXT,
    release_reason TEXT
);

CREATE INDEX IF NOT EXISTS idx_rt_dispatches_status ON neighboring_force_dispatches(status);
"""

# Columns added after the first release of this table. The status CHECK only allows
# en_route/arrived and SQLite cannot alter a CHECK in place, so a release is recorded in its
# own columns instead of a new status value.
_ADDED_COLUMNS = (
    ("event_id", "TEXT"),
    ("incident_id", "TEXT"),
    ("released_at", "TEXT"),
    ("release_reason", "TEXT"),
)

# Indexes on added columns can only be created once the migration above has run.
_POST_MIGRATION_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_rt_dispatches_event ON neighboring_force_dispatches(event_id);
CREATE INDEX IF NOT EXISTS idx_rt_dispatches_incident ON neighboring_force_dispatches(incident_id);
"""

# Listing filters that mean "released" rather than one of the stored movement statuses.
_RELEASED_FILTERS = frozenset({"released", "cancelled", "canceled"})


def _add_missing_columns(conn: sqlite3.Connection) -> None:
    """Add every column in _ADDED_COLUMNS that an older database file does not have yet."""

    existing = {row["name"] for row in conn.execute("PRAGMA table_info(neighboring_force_dispatches)").fetchall()}
    for name, ddl in _ADDED_COLUMNS:
        if name in existing:
            continue
        try:
            conn.execute(f"ALTER TABLE neighboring_force_dispatches ADD COLUMN {name} {ddl}")
        except sqlite3.OperationalError as exc:
            # Another process opening the same file may have added it a moment earlier.
            if "duplicate column" not in str(exc).lower():
                raise


class NeighboringForceStoreError(Exception):
    """The neighboring-force dispatch request could not be completed."""


class NeighboringForceStore:
    """Request/response log only; force kinds stay profile constants, not a standing-units table."""

    def __init__(self, db_path: str):
        """Open the DB file and create the dispatch-log table if it is missing."""

        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_NEIGHBORING_FORCE_SCHEMA)
            _add_missing_columns(conn)
            conn.executescript(_POST_MIGRATION_INDEXES)

    def _connect(self) -> sqlite3.Connection:
        """Open a row-factory connection for one operation."""

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _advance(self, conn: sqlite3.Connection, now_iso: str) -> None:
        """Flip en_route rows to arrived once their ETA has elapsed; a released unit never arrives."""

        now = _parse_timestamp(now_iso)
        rows = conn.execute(
            "SELECT request_id, dispatched_at, eta_seconds FROM neighboring_force_dispatches "
            "WHERE status = 'en_route' AND released_at IS NULL"
        ).fetchall()
        for row in rows:
            dispatched_at = _parse_timestamp(row["dispatched_at"])
            if dispatched_at + timedelta(seconds=row["eta_seconds"]) <= now:
                conn.execute(
                    "UPDATE neighboring_force_dispatches SET status = 'arrived', arrived_at = ? WHERE request_id = ?",
                    (now_iso, row["request_id"]),
                )

    def dispatch(
        self,
        *,
        force_kind: str,
        origin_area: str,
        target_area: str,
        unit_count: int,
        eta_seconds: int,
        note: str = "",
        event_id: str | None = None,
        dispatched_at: str | None = None,
        incident_id: str | None = None,
    ) -> dict:
        """Advance elapsed rows, then insert one en_route neighboring_force_dispatches row."""

        if unit_count < 1:
            raise NeighboringForceStoreError("unit_count must be at least 1")
        now = dispatched_at or _utc_now()
        request_id = f"NFD-{uuid.uuid4().hex[:8].upper()}"
        with self._connect() as conn:
            self._advance(conn, now)
            conn.execute(
                """
                INSERT INTO neighboring_force_dispatches (
                    request_id, force_kind, unit_count, origin_area, target_area,
                    status, dispatched_at, eta_seconds, arrived_at, note, event_id, incident_id
                ) VALUES (?, ?, ?, ?, ?, 'en_route', ?, ?, NULL, ?, ?, ?)
                """,
                (
                    request_id, force_kind, unit_count, origin_area, target_area, now, eta_seconds, note,
                    event_id or None, incident_id or None,
                ),
            )
            row = conn.execute(
                "SELECT * FROM neighboring_force_dispatches WHERE request_id = ?", (request_id,)
            ).fetchone()
            return dict(row)

    def list_dispatches(self, *, status: str | None = None, now_iso: str | None = None) -> list[dict]:
        """Advance elapsed rows, then return neighboring_force_dispatches rows.

        Released rows stay in the log (with released_at set); filtering by 'en_route' or
        'arrived' leaves them out, and 'released'/'cancelled' returns only them.
        """

        now = now_iso or _utc_now()
        with self._connect() as conn:
            self._advance(conn, now)
            query = "SELECT * FROM neighboring_force_dispatches WHERE 1=1"
            params: list[object] = []
            if status:
                if status.strip().lower() in _RELEASED_FILTERS:
                    query += " AND released_at IS NOT NULL"
                else:
                    query += " AND status = ? AND released_at IS NULL"
                    params.append(status)
            query += " ORDER BY dispatched_at DESC"
            rows = conn.execute(query, params).fetchall()
            return [dict(row) for row in rows]

    def release_dispatches(
        self,
        *,
        event_id: str | None = None,
        incident_id: str | None = None,
        reason: str = "",
        released_at: str | None = None,
    ) -> list[dict]:
        """Mark every unreleased dispatch linked to event_id or incident_id as released and return them.

        At least one reference is required so a missing id can never release the whole log.
        """

        if not (event_id or incident_id):
            raise NeighboringForceStoreError("event_id or incident_id is required to release dispatches.")
        now = released_at or _utc_now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._advance(conn, now)
            rows = conn.execute(
                """
                SELECT request_id FROM neighboring_force_dispatches
                WHERE released_at IS NULL
                  AND ((? IS NOT NULL AND event_id = ?) OR (? IS NOT NULL AND incident_id = ?))
                ORDER BY dispatched_at ASC
                """,
                (event_id or None, event_id or None, incident_id or None, incident_id or None),
            ).fetchall()
            request_ids = [row["request_id"] for row in rows]
            for request_id in request_ids:
                conn.execute(
                    "UPDATE neighboring_force_dispatches SET released_at = ?, release_reason = ? WHERE request_id = ?",
                    (now, reason.strip(), request_id),
                )
            released = [
                dict(conn.execute(
                    "SELECT * FROM neighboring_force_dispatches WHERE request_id = ?", (request_id,)
                ).fetchone())
                for request_id in request_ids
            ]
            return released

    def get_dispatch(self, request_id: str) -> dict | None:
        """Return one neighboring_force_dispatches row, or None."""

        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM neighboring_force_dispatches WHERE request_id = ?", (request_id,)
            ).fetchone()
            return dict(row) if row is not None else None

    def admin_update_dispatch(self, request_id: str, **fields) -> dict:
        """Overwrite editable dispatch columns so later capacity reads see the new values."""

        editable_columns = ("force_kind", "unit_count", "origin_area", "target_area", "status", "eta_seconds", "arrived_at", "note")
        with self._connect() as conn:
            current = conn.execute(
                "SELECT 1 FROM neighboring_force_dispatches WHERE request_id = ?", (request_id,)
            ).fetchone()
            if current is None:
                raise NeighboringForceStoreError(f"Dispatch '{request_id}' not found.")
            updates = {column: fields[column] for column in editable_columns if column in fields}
            if updates:
                assignments = ", ".join(f"{column} = ?" for column in updates)
                conn.execute(
                    f"UPDATE neighboring_force_dispatches SET {assignments} WHERE request_id = ?",
                    (*updates.values(), request_id),
                )
            row = conn.execute(
                "SELECT * FROM neighboring_force_dispatches WHERE request_id = ?", (request_id,)
            ).fetchone()
            return dict(row)


def open_neighboring_force_store(db_path: str) -> NeighboringForceStore:
    """Construct the neighboring-force dispatch store for this database path."""

    return NeighboringForceStore(db_path)
