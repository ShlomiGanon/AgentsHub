"""Burning/extinguished fire rows with one active fire per area and lazy TTL expiry."""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

FIRE_ACTIVE_TTL = timedelta(days=2)
STATUSES = ("burning", "extinguished")
EXPIRY_REASONS = ("reported", "stale", "admin")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS fires (
    fire_id TEXT PRIMARY KEY,
    area TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('burning', 'extinguished')),
    source_event_id TEXT,
    last_updated TEXT NOT NULL,
    extinguished_at TEXT,
    expiry_reason TEXT CHECK(expiry_reason IS NULL OR expiry_reason IN ('reported', 'stale', 'admin'))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_fires_one_burning_per_area
    ON fires(area) WHERE status = 'burning';
"""


class FireStoreError(Exception):
    """A fires-registry operation could not be completed."""


def _utc_now() -> str:
    """Return the current UTC time as an ISO-8601 string."""

    return datetime.now(timezone.utc).isoformat()


def _as_aware(value: str) -> datetime:
    """Parse an ISO timestamp, treating a missing offset as UTC."""

    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _new_fire_id() -> str:
    """Return a new FIRE-xxxxxxxx identifier."""

    return f"FIRE-{uuid.uuid4().hex[:8].upper()}"


class FireStore:
    """Connection-per-operation COP table for known fires."""

    def __init__(self, db_path: str):
        """Open the DB file and create the fires table if it is missing."""

        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        """Open a row-factory connection for one operation."""

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _expire_stale(self, conn: sqlite3.Connection, now: str) -> None:
        """Mark burning rows extinguished when last_updated is older than FIRE_ACTIVE_TTL."""

        cutoff = (_as_aware(now) - FIRE_ACTIVE_TTL).isoformat()
        conn.execute(
            "UPDATE fires SET status = 'extinguished', extinguished_at = ?, expiry_reason = 'stale' "
            "WHERE status = 'burning' AND last_updated <= ?",
            (now, cutoff),
        )

    def upsert_burning(
        self, *, area: str, source_event_id: str | None = None, now: str | None = None,
    ) -> dict:
        """Refresh the burning fire in this area, or insert one if none is active."""

        cleaned = area.strip()
        if not cleaned:
            raise FireStoreError("area is required.")
        stamp = now or _utc_now()
        event_id = (source_event_id or "").strip() or None
        with self._connect() as conn:
            self._expire_stale(conn, stamp)
            existing = conn.execute(
                "SELECT * FROM fires WHERE area = ? AND status = 'burning'", (cleaned,)
            ).fetchone()
            if existing is not None:
                conn.execute(
                    "UPDATE fires SET last_updated = ?, source_event_id = COALESCE(?, source_event_id) "
                    "WHERE fire_id = ?",
                    (stamp, event_id, existing["fire_id"]),
                )
                fire_id = existing["fire_id"]
            else:
                fire_id = _new_fire_id()
                try:
                    conn.execute(
                        "INSERT INTO fires (fire_id, area, status, source_event_id, last_updated, "
                        "extinguished_at, expiry_reason) VALUES (?, ?, 'burning', ?, ?, NULL, NULL)",
                        (fire_id, cleaned, event_id, stamp),
                    )
                except sqlite3.IntegrityError as exc:
                    raise FireStoreError(
                        f"A burning fire is already on record for area {cleaned!r}."
                    ) from exc
            row = conn.execute("SELECT * FROM fires WHERE fire_id = ?", (fire_id,)).fetchone()
        return dict(row)

    def touch(self, area: str, *, now: str | None = None) -> dict | None:
        """Refresh last_updated on the burning fire in this area without creating a row."""

        cleaned = area.strip()
        if not cleaned:
            raise FireStoreError("area is required.")
        stamp = now or _utc_now()
        with self._connect() as conn:
            self._expire_stale(conn, stamp)
            existing = conn.execute(
                "SELECT * FROM fires WHERE area = ? AND status = 'burning'", (cleaned,)
            ).fetchone()
            if existing is None:
                return None
            conn.execute(
                "UPDATE fires SET last_updated = ? WHERE fire_id = ?",
                (stamp, existing["fire_id"]),
            )
            row = conn.execute(
                "SELECT * FROM fires WHERE fire_id = ?", (existing["fire_id"],)
            ).fetchone()
        return dict(row)

    def extinguish(
        self, area: str, *, reason: str = "reported", now: str | None = None,
    ) -> dict:
        """Mark the burning fire in this area extinguished, or record an extinguished-only row."""

        cleaned = area.strip()
        if not cleaned:
            raise FireStoreError("area is required.")
        if reason not in EXPIRY_REASONS:
            raise FireStoreError(f"reason must be one of {sorted(EXPIRY_REASONS)}, got {reason!r}")
        stamp = now or _utc_now()
        with self._connect() as conn:
            self._expire_stale(conn, stamp)
            existing = conn.execute(
                "SELECT * FROM fires WHERE area = ? AND status = 'burning'", (cleaned,)
            ).fetchone()
            if existing is not None:
                conn.execute(
                    "UPDATE fires SET status = 'extinguished', last_updated = ?, extinguished_at = ?, "
                    "expiry_reason = ? WHERE fire_id = ?",
                    (stamp, stamp, reason, existing["fire_id"]),
                )
                fire_id = existing["fire_id"]
            else:
                fire_id = _new_fire_id()
                conn.execute(
                    "INSERT INTO fires (fire_id, area, status, source_event_id, last_updated, "
                    "extinguished_at, expiry_reason) VALUES (?, ?, 'extinguished', NULL, ?, ?, ?)",
                    (fire_id, cleaned, stamp, stamp, reason),
                )
            row = conn.execute("SELECT * FROM fires WHERE fire_id = ?", (fire_id,)).fetchone()
        return dict(row)

    def list_fires(self, *, status: str | None = None, now: str | None = None) -> list[dict]:
        """Expire stale rows, then return fires rows optionally filtered by status."""

        stamp = now or _utc_now()
        with self._connect() as conn:
            self._expire_stale(conn, stamp)
            query = "SELECT * FROM fires WHERE 1=1"
            params: list[object] = []
            if status:
                if status not in STATUSES:
                    raise FireStoreError(f"status must be one of {sorted(STATUSES)}, got {status!r}")
                query += " AND status = ?"
                params.append(status)
            query += " ORDER BY last_updated DESC"
            rows = conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def list_active(self, *, area: str | None = None, now: str | None = None) -> list[dict]:
        """Return currently burning fires, optionally limited to one area."""

        rows = self.list_fires(status="burning", now=now)
        cleaned = (area or "").strip()
        if cleaned:
            rows = [row for row in rows if row["area"] == cleaned]
        return rows

    def get_fire(self, fire_id: str, *, now: str | None = None) -> dict | None:
        """Expire stale rows, then return one fires row, or None."""

        stamp = now or _utc_now()
        with self._connect() as conn:
            self._expire_stale(conn, stamp)
            row = conn.execute("SELECT * FROM fires WHERE fire_id = ?", (fire_id,)).fetchone()
        return dict(row) if row is not None else None

    def admin_update_fire(self, fire_id: str, **fields) -> dict:
        """Overwrite area/status on one fire row, restarting or closing the TTL as needed."""

        editable_columns = ("area", "status")
        stamp = _utc_now()
        with self._connect() as conn:
            self._expire_stale(conn, stamp)
            current = conn.execute("SELECT * FROM fires WHERE fire_id = ?", (fire_id,)).fetchone()
            if current is None:
                raise FireStoreError(f"Fire '{fire_id}' not found.")
            updates = {column: fields[column] for column in editable_columns if column in fields}
            if "area" in updates:
                cleaned = str(updates["area"]).strip()
                if not cleaned:
                    raise FireStoreError("area is required.")
                updates["area"] = cleaned
            if "status" in updates:
                status = str(updates["status"]).strip()
                if status not in STATUSES:
                    raise FireStoreError(f"status must be one of {sorted(STATUSES)}, got {status!r}")
                updates["status"] = status
                if status == "extinguished":
                    updates["extinguished_at"] = stamp
                    updates["expiry_reason"] = "admin"
                else:
                    updates["extinguished_at"] = None
                    updates["expiry_reason"] = None
            if updates:
                updates["last_updated"] = stamp
                assignments = ", ".join(f"{column} = ?" for column in updates)
                try:
                    conn.execute(
                        f"UPDATE fires SET {assignments} WHERE fire_id = ?",
                        (*updates.values(), fire_id),
                    )
                except sqlite3.IntegrityError as exc:
                    raise FireStoreError(
                        f"A burning fire is already on record for area {updates.get('area', current['area'])!r}."
                    ) from exc
            row = conn.execute("SELECT * FROM fires WHERE fire_id = ?", (fire_id,)).fetchone()
        return dict(row)


def open_fire_store(db_path: str) -> FireStore:
    """Construct the fires store for this database path."""

    return FireStore(db_path)
