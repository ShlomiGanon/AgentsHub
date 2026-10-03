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
    event_id TEXT
);

CREATE INDEX IF NOT EXISTS idx_rt_dispatches_status ON neighboring_force_dispatches(status);
"""


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

    def _connect(self) -> sqlite3.Connection:
        """Open a row-factory connection for one operation."""

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _advance(self, conn: sqlite3.Connection, now_iso: str) -> None:
        """Flip en_route rows to arrived once their ETA has elapsed."""

        now = _parse_timestamp(now_iso)
        rows = conn.execute(
            "SELECT request_id, dispatched_at, eta_seconds FROM neighboring_force_dispatches WHERE status = 'en_route'"
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
                    status, dispatched_at, eta_seconds, arrived_at, note, event_id
                ) VALUES (?, ?, ?, ?, ?, 'en_route', ?, ?, NULL, ?, ?)
                """,
                (request_id, force_kind, unit_count, origin_area, target_area, now, eta_seconds, note, event_id),
            )
            row = conn.execute(
                "SELECT * FROM neighboring_force_dispatches WHERE request_id = ?", (request_id,)
            ).fetchone()
            return dict(row)

    def list_dispatches(self, *, status: str | None = None, now_iso: str | None = None) -> list[dict]:
        """Advance elapsed rows, then return neighboring_force_dispatches rows."""

        now = now_iso or _utc_now()
        with self._connect() as conn:
            self._advance(conn, now)
            query = "SELECT * FROM neighboring_force_dispatches WHERE 1=1"
            params: list[object] = []
            if status:
                query += " AND status = ?"
                params.append(status)
            query += " ORDER BY dispatched_at DESC"
            rows = conn.execute(query, params).fetchall()
            return [dict(row) for row in rows]

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
