"""Create-if-missing registry for a fire station's own apparatus rows."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS apparatus (
    apparatus_id TEXT PRIMARY KEY,
    callsign TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL CHECK(status IN ('operational', 'dispatched', 'unavailable', 'maintenance')),
    current_area TEXT,
    last_updated TEXT NOT NULL
);
"""


class ApparatusStoreError(Exception):
    """An apparatus registry operation could not be completed."""


def _utc_now() -> str:
    """Return the current UTC time as an ISO-8601 string."""

    return datetime.now(timezone.utc).isoformat()


class ApparatusStore:
    """Connection-per-operation store for apparatus id, callsign, status, and area."""

    def __init__(self, db_path: str):
        """Open the DB file and create the apparatus table if it is missing."""

        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        """Open a row-factory connection for one operation."""

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def ensure_apparatus(
        self, *, apparatus_id: str, callsign: str, status: str = "operational", current_area: str | None = None,
    ) -> None:
        """Insert an apparatus row only when apparatus_id is new."""

        with self._connect() as conn:
            existing = conn.execute(
                "SELECT 1 FROM apparatus WHERE apparatus_id = ?", (apparatus_id,)
            ).fetchone()
            if existing is None:
                conn.execute(
                    "INSERT INTO apparatus (apparatus_id, callsign, status, current_area, last_updated) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (apparatus_id, callsign, status, current_area, _utc_now()),
                )

    def list_apparatus(self) -> list[dict]:
        """Return every apparatus row ordered by callsign."""

        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM apparatus ORDER BY callsign").fetchall()
        return [dict(row) for row in rows]

    def get_apparatus(self, identifier: str) -> dict | None:
        """Return the apparatus row matching id or callsign, case-insensitively."""

        cleaned = identifier.strip().casefold()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM apparatus WHERE lower(apparatus_id) = ? OR lower(callsign) = ?",
                (cleaned, cleaned),
            ).fetchone()
        return dict(row) if row is not None else None

    def update_status(self, identifier: str, status: str, current_area: str | None = None) -> dict:
        """Write status and optional current_area on the matched apparatus row."""

        valid = {"operational", "dispatched", "unavailable", "maintenance"}
        if status not in valid:
            raise ApparatusStoreError(f"status must be one of {sorted(valid)}, got {status!r}")
        existing = self.get_apparatus(identifier)
        if existing is None:
            raise ApparatusStoreError(f"Apparatus '{identifier}' not found.")
        with self._connect() as conn:
            if current_area:
                conn.execute(
                    "UPDATE apparatus SET status = ?, current_area = ?, last_updated = ? WHERE apparatus_id = ?",
                    (status, current_area, _utc_now(), existing["apparatus_id"]),
                )
            else:
                conn.execute(
                    "UPDATE apparatus SET status = ?, last_updated = ? WHERE apparatus_id = ?",
                    (status, _utc_now(), existing["apparatus_id"]),
                )
            updated = conn.execute(
                "SELECT * FROM apparatus WHERE apparatus_id = ?", (existing["apparatus_id"],)
            ).fetchone()
        return dict(updated)

    def admin_update_apparatus(self, apparatus_id: str, **fields) -> dict:
        """Overwrite editable apparatus columns and stamp last_updated."""

        editable_columns = ("callsign", "status", "current_area")
        with self._connect() as conn:
            current = conn.execute(
                "SELECT 1 FROM apparatus WHERE apparatus_id = ?", (apparatus_id,)
            ).fetchone()
            if current is None:
                raise ApparatusStoreError(f"Apparatus '{apparatus_id}' not found.")
            updates = {column: fields[column] for column in editable_columns if column in fields}
            if updates:
                assignments = ", ".join(f"{column} = ?" for column in updates)
                conn.execute(
                    f"UPDATE apparatus SET {assignments}, last_updated = ? WHERE apparatus_id = ?",
                    (*updates.values(), _utc_now(), apparatus_id),
                )
            updated = conn.execute(
                "SELECT * FROM apparatus WHERE apparatus_id = ?", (apparatus_id,)
            ).fetchone()
        return dict(updated)


def open_apparatus_store(db_path: str) -> ApparatusStore:
    """Construct the apparatus store for this database path."""

    return ApparatusStore(db_path)
