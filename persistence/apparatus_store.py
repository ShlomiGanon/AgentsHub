"""Dedicated SQLite persistence for a fire station's own apparatus (engines/vehicles) --
minimal, deliberately narrow: a create-if-missing registry plus a status/area update, mirroring
`persistence/surveillance_store.py`'s `ensure_camera`/`ensure_drone` idiom, added during this
session's simulation-data-alignment audit (docs/Admin_Tables_Plan.md) once FIRE_002's own
simulation text was found to name real apparatus (two named engines, "Ashed 3" and "Carmel 1")
with no registry anywhere to back them. Connection-per-operation, matching every other store in
this codebase."""

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
    return datetime.now(timezone.utc).isoformat()


class ApparatusStore:
    def __init__(self, db_path: str):
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def ensure_apparatus(
        self, *, apparatus_id: str, callsign: str, status: str = "operational", current_area: str | None = None,
    ) -> None:
        """Create-if-missing only -- never overwrites an existing row, the same idiom
        response_team.py's own OPERATIONAL_SEED uses for cameras/drones."""

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
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM apparatus ORDER BY callsign").fetchall()
        return [dict(row) for row in rows]

    def get_apparatus(self, identifier: str) -> dict | None:
        """Matches by apparatus_id or callsign, case-insensitively -- the same lookup shape
        `persistence/surveillance_store.py::get_drone` and friends already use."""

        cleaned = identifier.strip().casefold()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM apparatus WHERE lower(apparatus_id) = ? OR lower(callsign) = ?",
                (cleaned, cleaned),
            ).fetchone()
        return dict(row) if row is not None else None

    def update_status(self, identifier: str, status: str, current_area: str | None = None) -> dict:
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
        """Admin-panel edit of one apparatus row -- every column plain-editable, same
        immediate-effect convention as docs/Admin_Tables_Plan.md's other admin_update_* methods.
        Not yet wired into any profile's ADMIN_TABLES; left available for that follow-up."""

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
    return ApparatusStore(db_path)
