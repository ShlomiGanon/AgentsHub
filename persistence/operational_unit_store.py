"""Canonical LIVE OperationalUnit and Membership persistence.

The existing ``team_members`` table remains the single membership source of
truth.  This module owns only the unit catalogue and the unit-aware operations
on that table; simulation rows remain scoped and are never considered LIVE.
"""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

RESPONSE_TEAM = "response_team"
FIRE_STATION = "fire_station"


class OperationalUnitError(ValueError):
    """A unit or membership operation violates the canonical contract."""


@dataclass(frozen=True)
class OperationalUnit:
    unit_id: str
    name: str
    profile_id: str
    status: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class LiveOperationalContext:
    user_id: str
    unit: OperationalUnit | None
    membership: dict | None
    profile_id: str | None
    status: str


ROLE_CATALOGUE = {
    RESPONSE_TEAM: ("responder", "commander"),
    FIRE_STATION: ("firefighter", "shift_commander"),
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SQLiteOperationalUnitPersistence:
    """Persist units and LIVE memberships in the existing team-status DB."""

    def __init__(self, db_path: str):
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS operational_units (
                    unit_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    profile_id TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'active', 'retired')),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )"""
            )
            if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='team_members'").fetchone() is None:
                from persistence.team_status_contracts import open_team_status_persistence
                open_team_status_persistence(self.db_path)
            columns = {row[1] for row in connection.execute("PRAGMA table_info(team_members)")}
            if "unit_id" not in columns:
                connection.execute("ALTER TABLE team_members ADD COLUMN unit_id TEXT")
            if "role" not in columns:
                connection.execute("ALTER TABLE team_members ADD COLUMN role TEXT")

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _unit(row: sqlite3.Row | None) -> OperationalUnit | None:
        return OperationalUnit(**dict(row)) if row is not None else None

    @staticmethod
    def _validate_profile(profile_id: str) -> str:
        profile_id = str(profile_id or "").strip()
        from profiles import operational_profile
        try:
            operational_profile(profile_id)
        except Exception as exc:
            raise OperationalUnitError(f"invalid operational profile: {profile_id}") from exc
        return profile_id

    def create_unit(self, name: str, profile_id: str, *, status: str = "pending", unit_id: str | None = None) -> OperationalUnit:
        clean_name = " ".join(str(name or "").split())
        if not clean_name:
            raise OperationalUnitError("unit name is required")
        if status not in {"pending", "active", "retired"}:
            raise OperationalUnitError("unit status is invalid")
        profile_id = self._validate_profile(profile_id)
        unit_id = unit_id or f"unit-{uuid.uuid4().hex}"
        now = _now()
        with self._connect() as connection:
            try:
                connection.execute(
                    "INSERT INTO operational_units(unit_id,name,profile_id,status,created_at,updated_at) VALUES (?,?,?,?,?,?)",
                    (unit_id, clean_name, profile_id, status, now, now),
                )
            except sqlite3.IntegrityError as exc:
                raise OperationalUnitError(f"unit already exists: {unit_id}") from exc
        return self.get_unit(unit_id)

    def get_unit(self, unit_id: str) -> OperationalUnit | None:
        with self._connect() as connection:
            row = connection.execute("SELECT unit_id,name,profile_id,status,created_at,updated_at FROM operational_units WHERE unit_id=?", (unit_id,)).fetchone()
        return self._unit(row)

    def list_units(self, *, include_retired: bool = False) -> list[OperationalUnit]:
        where = "" if include_retired else " WHERE status <> 'retired'"
        with self._connect() as connection:
            rows = connection.execute("SELECT unit_id,name,profile_id,status,created_at,updated_at FROM operational_units" + where + " ORDER BY name, unit_id").fetchall()
        return [self._unit(row) for row in rows]

    def update_unit(self, unit_id: str, *, name: str | None = None, profile_id: str | None = None, status: str | None = None) -> OperationalUnit:
        current = self.get_unit(unit_id)
        if current is None:
            raise OperationalUnitError(f"no such operational unit: {unit_id}")
        next_name = " ".join(str(name).split()) if name is not None else current.name
        if not next_name:
            raise OperationalUnitError("unit name is required")
        next_profile = self._validate_profile(profile_id) if profile_id is not None else current.profile_id
        next_status = status or current.status
        if next_status not in {"pending", "active", "retired"}:
            raise OperationalUnitError("unit status is invalid")
        if next_profile != current.profile_id:
            with self._connect() as connection:
                active = connection.execute(
                    "SELECT 1 FROM team_members WHERE scope_key='LIVE' AND unit_id=? AND approved=1 AND membership_status='active' LIMIT 1",
                    (unit_id,),
                ).fetchone()
            if active is not None:
                raise OperationalUnitError("cannot change profile while active memberships exist")
        now = _now()
        with self._connect() as connection:
            connection.execute("UPDATE operational_units SET name=?,profile_id=?,status=?,updated_at=? WHERE unit_id=?", (next_name, next_profile, next_status, now, unit_id))
        return self.get_unit(unit_id)

    def compatible_roles(self, unit_id: str) -> tuple[str, ...]:
        unit = self.get_unit(unit_id)
        if unit is None:
            raise OperationalUnitError(f"no such operational unit: {unit_id}")
        return ROLE_CATALOGUE[unit.profile_id]

    def assign_membership(self, user_id: str, unit_id: str, role: str, *, status: str = "active", full_name: str = "", registered_at: str | None = None) -> dict:
        unit = self.get_unit(unit_id)
        if unit is None or unit.status == "retired":
            raise OperationalUnitError("operational unit is unavailable")
        if role not in ROLE_CATALOGUE[unit.profile_id]:
            raise OperationalUnitError(f"role '{role}' is not compatible with {unit.profile_id}")
        if status not in {"active", "pending", "retired"}:
            raise OperationalUnitError("membership status is invalid")
        approved = 1 if status == "active" else 0
        registered_at = registered_at or _now()
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT membership_id FROM team_members WHERE scope_key='LIVE' AND telegram_identity=? AND unit_id=?",
                (user_id, unit_id),
            ).fetchone()
            if existing is None:
                connection.execute(
                    "INSERT INTO team_members(scope_key,telegram_identity,full_name,registered_at,approved,membership_status,unit_id,role) VALUES ('LIVE',?,?,?,?,?,?,?)",
                    (user_id, full_name or user_id, registered_at, approved, "active" if status == "pending" else status, unit_id, role),
                )
            else:
                connection.execute(
                    "UPDATE team_members SET full_name=?,approved=?,membership_status=?,role=? WHERE membership_id=?",
                    (full_name or user_id, approved, "active" if status == "pending" else status, role, existing["membership_id"]),
                )
            row = connection.execute("SELECT * FROM team_members WHERE scope_key='LIVE' AND telegram_identity=?", (user_id,)).fetchone()
        return dict(row)

    def list_memberships(self, *, unit_id: str | None = None, include_inactive: bool = True) -> list[dict]:
        clauses = ["scope_key='LIVE'", "unit_id IS NOT NULL"]
        args: list[str] = []
        if unit_id:
            clauses.append("unit_id=?")
            args.append(unit_id)
        if not include_inactive:
            clauses.append("approved=1 AND membership_status='active'")
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM team_members WHERE " + " AND ".join(clauses) + " ORDER BY full_name, telegram_identity", args).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            if not item.get("approved") and item.get("membership_status") == "active":
                item["membership_status"] = "pending"
            result.append(item)
        return result

    def resolve_live(self, user_id: str) -> LiveOperationalContext:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT m.*, u.unit_id AS joined_unit_id, u.name AS unit_name, u.profile_id AS unit_profile_id,
                          u.status AS unit_status, u.created_at AS unit_created_at, u.updated_at AS unit_updated_at
                   FROM team_members m JOIN operational_units u ON u.unit_id=m.unit_id
                   WHERE m.scope_key='LIVE' AND m.telegram_identity=? AND m.approved=1
                     AND m.membership_status='active' AND u.status='active'""",
                (user_id,),
            ).fetchall()
        if not rows:
            return LiveOperationalContext(user_id, None, None, None, "no_active_membership")
        if len(rows) > 1:
            return LiveOperationalContext(user_id, None, None, None, "ambiguous_active_membership")
        row = rows[0]
        unit = OperationalUnit(row["joined_unit_id"], row["unit_name"], row["unit_profile_id"], row["unit_status"], row["unit_created_at"], row["unit_updated_at"])
        membership = {key: row[key] for key in row.keys() if key not in {"joined_unit_id", "unit_name", "unit_profile_id", "unit_status", "unit_created_at", "unit_updated_at"}}
        return LiveOperationalContext(user_id, unit, membership, unit.profile_id, "resolved")

    def pending_memberships(self) -> list[dict]:
        return [item for item in self.list_memberships(include_inactive=True) if not item.get("approved") or item.get("membership_status") != "active"]


def open_operational_unit_persistence(db_path: str) -> SQLiteOperationalUnitPersistence:
    return SQLiteOperationalUnitPersistence(db_path)


def resolve_live_operational_context(user_id: str, users_persistence, unit_store: SQLiteOperationalUnitPersistence) -> LiveOperationalContext:
    """Resolve User -> approved LIVE Membership -> Unit -> trusted profile."""

    user = users_persistence.read_user(str(user_id)) if users_persistence is not None else None
    if user is None:
        return LiveOperationalContext(str(user_id), None, None, None, "unknown_user")
    if users_persistence.is_simulation_identity(str(user_id)):
        return LiveOperationalContext(str(user_id), None, None, None, "simulation_identity")
    return unit_store.resolve_live(str(user_id))


__all__ = [
    "FIRE_STATION",
    "RESPONSE_TEAM",
    "ROLE_CATALOGUE",
    "LiveOperationalContext",
    "OperationalUnit",
    "OperationalUnitError",
    "SQLiteOperationalUnitPersistence",
    "open_operational_unit_persistence",
    "resolve_live_operational_context",
]
