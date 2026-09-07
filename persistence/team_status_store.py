"""Dedicated persistence for readiness-team roster and attendance state.

This database is intentionally separate from the operational event/history
database owned by :class:`PersistenceInterface`.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from persistence.team_status_contracts import (
    AttendanceCycle,
    TeamStatusPersistenceError,
    TeamStatusPersistenceInterface,
)


_SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS team_members (
    telegram_identity TEXT PRIMARY KEY,
    full_name TEXT NOT NULL,
    registered_at TEXT NOT NULL,
    approved INTEGER NOT NULL DEFAULT 0 CHECK (approved IN (0, 1))
);

CREATE TABLE IF NOT EXISTS roster_approval (
    singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
    approved_by TEXT NOT NULL,
    approved_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS attendance_cycles (
    cycle_id TEXT PRIMARY KEY,
    cycle_key TEXT NOT NULL UNIQUE,
    opened_at TEXT NOT NULL,
    deadline_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS attendance_responses (
    response_id TEXT PRIMARY KEY,
    source_message_id TEXT NOT NULL UNIQUE,
    cycle_id TEXT NOT NULL REFERENCES attendance_cycles(cycle_id),
    telegram_identity TEXT NOT NULL REFERENCES team_members(telegram_identity),
    availability TEXT NOT NULL CHECK (availability IN ('available', 'unavailable')),
    reason TEXT,
    unavailable_until TEXT,
    original_text TEXT NOT NULL,
    received_at TEXT NOT NULL,
    approval_status TEXT NOT NULL CHECK (approval_status IN ('accepted', 'pending', 'rejected')),
    reviewed_by TEXT,
    reviewed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_attendance_responses_member_time
ON attendance_responses(telegram_identity, received_at DESC);

CREATE INDEX IF NOT EXISTS idx_attendance_responses_cycle
ON attendance_responses(cycle_id, telegram_identity, received_at DESC);
"""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise TeamStatusPersistenceError(f"invalid ISO timestamp: {value!r}") from exc
    if parsed.tzinfo is None:
        raise TeamStatusPersistenceError("timestamps must include a timezone")
    return parsed.astimezone(timezone.utc)


class SQLiteTeamStatusPersistence(TeamStatusPersistenceInterface):
    """Small connection-per-operation store for one dedicated status DB."""

    def __init__(self, db_path: str):
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def register_member(self, telegram_identity: str, full_name: str, registered_at: str | None = None) -> None:
        identity = telegram_identity.strip()
        name = " ".join(full_name.split())
        if not identity or not name:
            raise TeamStatusPersistenceError("telegram identity and full name are required")
        registered_at = registered_at or _utc_now()
        _parse_timestamp(registered_at)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO team_members(telegram_identity, full_name, registered_at, approved)
                VALUES (?, ?, ?, 0)
                ON CONFLICT(telegram_identity) DO UPDATE SET full_name = excluded.full_name
                """,
                (identity, name, registered_at),
            )

    def approve_roster(self, approved_by: str, approved_at: str | None = None) -> int:
        if not approved_by.strip():
            raise TeamStatusPersistenceError("approving commander identity is required")
        approved_at = approved_at or _utc_now()
        _parse_timestamp(approved_at)
        with self._connect() as connection:
            count = connection.execute("SELECT COUNT(*) FROM team_members").fetchone()[0]
            if count == 0:
                raise TeamStatusPersistenceError("cannot approve an empty roster")
            connection.execute("UPDATE team_members SET approved = 1")
            connection.execute(
                """
                INSERT INTO roster_approval(singleton_id, approved_by, approved_at)
                VALUES (1, ?, ?)
                ON CONFLICT(singleton_id) DO UPDATE SET
                    approved_by = excluded.approved_by,
                    approved_at = excluded.approved_at
                """,
                (approved_by, approved_at),
            )
        return int(count)

    def roster_is_approved(self) -> bool:
        with self._connect() as connection:
            return connection.execute("SELECT 1 FROM roster_approval WHERE singleton_id = 1").fetchone() is not None

    def list_members(self, *, approved_only: bool = True) -> list[dict]:
        where = "WHERE approved = 1" if approved_only else ""
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT telegram_identity, full_name, registered_at, approved FROM team_members {where} ORDER BY full_name"
            ).fetchall()
        return [dict(row) for row in rows]

    def open_cycle(self, cycle_key: str, opened_at: str, deadline_at: str) -> AttendanceCycle:
        if not self.roster_is_approved():
            raise TeamStatusPersistenceError("the commander must approve the roster before attendance checks begin")
        opened = _parse_timestamp(opened_at)
        deadline = _parse_timestamp(deadline_at)
        if deadline <= opened:
            raise TeamStatusPersistenceError("attendance deadline must be after the cycle opens")
        cycle_id = f"attendance-{uuid.uuid4().hex}"
        created = True
        with self._connect() as connection:
            try:
                connection.execute(
                    "INSERT INTO attendance_cycles(cycle_id, cycle_key, opened_at, deadline_at) VALUES (?, ?, ?, ?)",
                    (cycle_id, cycle_key, opened_at, deadline_at),
                )
            except sqlite3.IntegrityError:
                created = False
            row = connection.execute(
                "SELECT cycle_id, cycle_key, opened_at, deadline_at FROM attendance_cycles WHERE cycle_key = ?",
                (cycle_key,),
            ).fetchone()
        return AttendanceCycle(**dict(row), created=created)

    def latest_cycle(self) -> dict | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT cycle_id, cycle_key, opened_at, deadline_at FROM attendance_cycles ORDER BY opened_at DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row is not None else None

    def record_response(
        self,
        *,
        telegram_identity: str,
        source_message_id: str,
        availability: str,
        original_text: str,
        received_at: str,
        reason: str | None = None,
        unavailable_until: str | None = None,
    ) -> dict:
        if availability not in {"available", "unavailable"}:
            raise TeamStatusPersistenceError("availability must be 'available' or 'unavailable'")
        if availability == "unavailable" and not (reason or "").strip():
            raise TeamStatusPersistenceError("an unavailable response requires a reason")
        if availability == "available" and (reason is not None or unavailable_until is not None):
            raise TeamStatusPersistenceError("an available response cannot include an unavailable reason or end time")
        received = _parse_timestamp(received_at)
        if unavailable_until is not None and _parse_timestamp(unavailable_until) <= received:
            raise TeamStatusPersistenceError("unavailable_until must be after received_at")

        cycle = self.latest_cycle()
        if cycle is None:
            raise TeamStatusPersistenceError("no attendance cycle is open")
        deadline = _parse_timestamp(cycle["deadline_at"])
        approval_status = "accepted" if received <= deadline else "pending"
        response_id = f"response-{uuid.uuid4().hex}"

        with self._connect() as connection:
            member = connection.execute(
                "SELECT approved FROM team_members WHERE telegram_identity = ?",
                (telegram_identity,),
            ).fetchone()
            if member is None or not member["approved"]:
                raise TeamStatusPersistenceError("attendance responses are accepted only from the approved roster")
            try:
                connection.execute(
                    """
                    INSERT INTO attendance_responses(
                        response_id, source_message_id, cycle_id, telegram_identity,
                        availability, reason, unavailable_until, original_text,
                        received_at, approval_status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        response_id,
                        source_message_id,
                        cycle["cycle_id"],
                        telegram_identity,
                        availability,
                        reason.strip() if reason else None,
                        unavailable_until,
                        original_text,
                        received_at,
                        approval_status,
                    ),
                )
            except sqlite3.IntegrityError:
                row = connection.execute(
                    "SELECT * FROM attendance_responses WHERE source_message_id = ?",
                    (source_message_id,),
                ).fetchone()
                return dict(row)
            row = connection.execute(
                "SELECT * FROM attendance_responses WHERE response_id = ?",
                (response_id,),
            ).fetchone()
        return dict(row)

    def review_late_response(
        self, response_id: str, *, approved: bool, reviewed_by: str, reviewed_at: str | None = None
    ) -> dict:
        reviewed_at = reviewed_at or _utc_now()
        _parse_timestamp(reviewed_at)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT approval_status FROM attendance_responses WHERE response_id = ?",
                (response_id,),
            ).fetchone()
            if row is None:
                raise TeamStatusPersistenceError("late response was not found")
            if row["approval_status"] != "pending":
                raise TeamStatusPersistenceError("response is not awaiting commander review")
            connection.execute(
                """
                UPDATE attendance_responses
                SET approval_status = ?, reviewed_by = ?, reviewed_at = ?
                WHERE response_id = ?
                """,
                ("accepted" if approved else "rejected", reviewed_by, reviewed_at, response_id),
            )
            updated = connection.execute(
                "SELECT * FROM attendance_responses WHERE response_id = ?",
                (response_id,),
            ).fetchone()
        return dict(updated)

    def pending_late_responses(self) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT r.*, m.full_name
                FROM attendance_responses r
                JOIN team_members m USING (telegram_identity)
                WHERE r.approval_status = 'pending'
                ORDER BY r.received_at
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def availability_snapshot(self, as_of: str) -> list[dict]:
        instant = _parse_timestamp(as_of)
        cycle = self.latest_cycle()
        members = self.list_members()
        snapshot: list[dict] = []
        with self._connect() as connection:
            for member in members:
                accepted = connection.execute(
                    """
                    SELECT * FROM attendance_responses
                    WHERE telegram_identity = ? AND approval_status = 'accepted'
                    ORDER BY received_at DESC LIMIT 1
                    """,
                    (member["telegram_identity"],),
                ).fetchone()
                entry = {
                    "telegram_identity": member["telegram_identity"],
                    "full_name": member["full_name"],
                    "availability": "awaiting_response",
                    "reason": None,
                    "unavailable_until": None,
                    "original_text": None,
                    "received_at": None,
                }
                if accepted is not None:
                    response = dict(accepted)
                    active_unavailability = (
                        response["availability"] == "unavailable"
                        and response["unavailable_until"] is not None
                        and _parse_timestamp(response["unavailable_until"]) > instant
                    )
                    belongs_to_current_cycle = cycle is not None and response["cycle_id"] == cycle["cycle_id"]
                    if active_unavailability or belongs_to_current_cycle:
                        entry.update({key: response[key] for key in (
                            "availability", "reason", "unavailable_until", "original_text", "received_at"
                        )})
                snapshot.append(entry)
        return snapshot
