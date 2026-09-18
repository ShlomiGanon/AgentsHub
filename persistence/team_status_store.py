"""Scoped SQLite persistence for team roster, attendance, and manpower state."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

from persistence.operational_scope import OperationalScope, resolve_operational_scope, scope_created_at
from persistence.team_status_contracts import AttendanceCycle, TeamStatusPersistenceError, TeamStatusPersistenceInterface


_SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS operational_scopes (
    scope_key TEXT PRIMARY KEY,
    scope_kind TEXT NOT NULL CHECK (scope_kind IN ('LIVE', 'SIMULATION_RUN')),
    scenario_id TEXT,
    scenario_run_id TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (scenario_id, scenario_run_id)
);
CREATE TABLE IF NOT EXISTS team_members (
    scope_key TEXT NOT NULL REFERENCES operational_scopes(scope_key),
    telegram_identity TEXT NOT NULL,
    full_name TEXT NOT NULL,
    registered_at TEXT NOT NULL,
    approved INTEGER NOT NULL DEFAULT 0 CHECK (approved IN (0, 1)),
    PRIMARY KEY (scope_key, telegram_identity)
);
CREATE TABLE IF NOT EXISTS roster_approval (
    scope_key TEXT PRIMARY KEY REFERENCES operational_scopes(scope_key),
    singleton_id INTEGER NOT NULL DEFAULT 1,
    approved_by TEXT NOT NULL,
    approved_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS attendance_cycles (
    scope_key TEXT NOT NULL REFERENCES operational_scopes(scope_key),
    cycle_id TEXT NOT NULL,
    cycle_key TEXT NOT NULL,
    opened_at TEXT NOT NULL,
    deadline_at TEXT NOT NULL,
    PRIMARY KEY (scope_key, cycle_id),
    UNIQUE (scope_key, cycle_key)
);
CREATE TABLE IF NOT EXISTS attendance_responses (
    scope_key TEXT NOT NULL,
    response_id TEXT NOT NULL,
    source_message_id TEXT NOT NULL,
    cycle_id TEXT NOT NULL,
    telegram_identity TEXT NOT NULL,
    availability TEXT NOT NULL CHECK (availability IN ('available', 'unavailable')),
    reason TEXT,
    availability_start TEXT,
    availability_end TEXT,
    unavailable_until TEXT,
    original_text TEXT NOT NULL,
    received_at TEXT NOT NULL,
    approval_status TEXT NOT NULL CHECK (approval_status IN ('accepted', 'pending', 'rejected')),
    reviewed_by TEXT,
    reviewed_at TEXT,
    PRIMARY KEY (scope_key, response_id),
    UNIQUE (scope_key, source_message_id),
    FOREIGN KEY (scope_key, cycle_id) REFERENCES attendance_cycles(scope_key, cycle_id),
    FOREIGN KEY (scope_key, telegram_identity) REFERENCES team_members(scope_key, telegram_identity)
);
CREATE INDEX IF NOT EXISTS idx_attendance_responses_scope_member_time ON attendance_responses(scope_key, telegram_identity, received_at DESC);
CREATE INDEX IF NOT EXISTS idx_attendance_responses_scope_cycle ON attendance_responses(scope_key, cycle_id, telegram_identity, received_at DESC);
CREATE TABLE IF NOT EXISTS operational_team_state (
    scope_key TEXT PRIMARY KEY REFERENCES operational_scopes(scope_key),
    manpower_count INTEGER NOT NULL,
    resources_json TEXT NOT NULL,
    source_event_id TEXT,
    received_at TEXT NOT NULL,
    scenario_id TEXT,
    scenario_run_id TEXT,
    scenario_time TEXT
);
"""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise TeamStatusPersistenceError(f"invalid ISO timestamp: {value!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


class SQLiteTeamStatusPersistence(TeamStatusPersistenceInterface):
    def __init__(self, db_path: str):
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("PRAGMA foreign_keys = OFF")
            self._migrate_legacy_tables(connection)
            connection.executescript(_SCHEMA)
            connection.execute("PRAGMA foreign_keys = ON")
            self.ensure_scope(OperationalScope.live(), connection=connection)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _migrate_legacy_tables(self, connection: sqlite3.Connection) -> None:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        connection.execute("CREATE TABLE IF NOT EXISTS operational_scopes (scope_key TEXT PRIMARY KEY, scope_kind TEXT NOT NULL, scenario_id TEXT, scenario_run_id TEXT, created_at TEXT NOT NULL, UNIQUE(scenario_id,scenario_run_id))")
        connection.execute("INSERT OR IGNORE INTO operational_scopes VALUES ('LIVE','LIVE',NULL,NULL,?)", (scope_created_at(),))
        if "team_members" in tables and "scope_key" not in {row[1] for row in connection.execute("PRAGMA table_info(team_members)")}: 
            connection.execute("ALTER TABLE team_members RENAME TO team_members_legacy")
            connection.execute("CREATE TABLE team_members_new (scope_key TEXT NOT NULL, telegram_identity TEXT NOT NULL, full_name TEXT NOT NULL, registered_at TEXT NOT NULL, approved INTEGER NOT NULL, PRIMARY KEY(scope_key,telegram_identity))")
            connection.execute("INSERT INTO team_members_new SELECT 'LIVE',telegram_identity,full_name,registered_at,approved FROM team_members_legacy")
            connection.execute("DROP TABLE team_members_legacy")
            connection.execute("ALTER TABLE team_members_new RENAME TO team_members")
        if "roster_approval" in tables and "scope_key" not in {row[1] for row in connection.execute("PRAGMA table_info(roster_approval)")}: 
            connection.execute("ALTER TABLE roster_approval RENAME TO roster_approval_legacy")
            connection.execute("CREATE TABLE roster_approval_new (scope_key TEXT PRIMARY KEY, singleton_id INTEGER NOT NULL DEFAULT 1, approved_by TEXT NOT NULL, approved_at TEXT NOT NULL)")
            connection.execute("INSERT INTO roster_approval_new(scope_key,approved_by,approved_at) SELECT 'LIVE',approved_by,approved_at FROM roster_approval_legacy")
            connection.execute("DROP TABLE roster_approval_legacy")
            connection.execute("ALTER TABLE roster_approval_new RENAME TO roster_approval")
        elif "roster_approval" in tables and "singleton_id" not in {row[1] for row in connection.execute("PRAGMA table_info(roster_approval)")}: 
            connection.execute("ALTER TABLE roster_approval ADD COLUMN singleton_id INTEGER NOT NULL DEFAULT 1")
        if "attendance_cycles" in tables and "scope_key" not in {row[1] for row in connection.execute("PRAGMA table_info(attendance_cycles)")}: 
            connection.execute("ALTER TABLE attendance_cycles RENAME TO attendance_cycles_legacy")
            connection.execute("CREATE TABLE attendance_cycles_new (scope_key TEXT NOT NULL, cycle_id TEXT NOT NULL, cycle_key TEXT NOT NULL, opened_at TEXT NOT NULL, deadline_at TEXT NOT NULL, PRIMARY KEY(scope_key,cycle_id))")
            connection.execute("INSERT INTO attendance_cycles_new SELECT 'LIVE',cycle_id,cycle_key,opened_at,deadline_at FROM attendance_cycles_legacy")
            connection.execute("DROP TABLE attendance_cycles_legacy")
            connection.execute("ALTER TABLE attendance_cycles_new RENAME TO attendance_cycles")
        if "attendance_responses" in tables and "scope_key" not in {row[1] for row in connection.execute("PRAGMA table_info(attendance_responses)")}: 
            connection.execute("ALTER TABLE attendance_responses RENAME TO attendance_responses_legacy")
            connection.execute("CREATE TABLE attendance_responses_new (scope_key TEXT NOT NULL, response_id TEXT NOT NULL, source_message_id TEXT NOT NULL, cycle_id TEXT NOT NULL, telegram_identity TEXT NOT NULL, availability TEXT NOT NULL, reason TEXT, availability_start TEXT, availability_end TEXT, unavailable_until TEXT, original_text TEXT NOT NULL, received_at TEXT NOT NULL, approval_status TEXT NOT NULL, reviewed_by TEXT, reviewed_at TEXT, PRIMARY KEY(scope_key,response_id))")
            columns = {row[1] for row in connection.execute("PRAGMA table_info(attendance_responses_legacy)")}
            start = "availability_start" if "availability_start" in columns else "NULL"
            end = "availability_end" if "availability_end" in columns else "NULL"
            connection.execute(f"INSERT INTO attendance_responses_new SELECT 'LIVE',response_id,source_message_id,cycle_id,telegram_identity,availability,reason,{start},{end},unavailable_until,original_text,received_at,approval_status,reviewed_by,reviewed_at FROM attendance_responses_legacy")
            connection.execute("DROP TABLE attendance_responses_legacy")
            connection.execute("ALTER TABLE attendance_responses_new RENAME TO attendance_responses")
        if "operational_team_state" in tables and "scope_key" not in {row[1] for row in connection.execute("PRAGMA table_info(operational_team_state)")}: 
            connection.execute("ALTER TABLE operational_team_state RENAME TO operational_team_state_legacy")
            connection.execute("CREATE TABLE operational_team_state_new (scope_key TEXT PRIMARY KEY, manpower_count INTEGER NOT NULL, resources_json TEXT NOT NULL, source_event_id TEXT, received_at TEXT NOT NULL, scenario_id TEXT, scenario_run_id TEXT, scenario_time TEXT)")
            connection.execute("INSERT INTO operational_team_state_new SELECT 'LIVE',manpower_count,resources_json,source_event_id,received_at,scenario_id,scenario_run_id,scenario_time FROM operational_team_state_legacy")
            connection.execute("DROP TABLE operational_team_state_legacy")
            connection.execute("ALTER TABLE operational_team_state_new RENAME TO operational_team_state")

    def _scope_key(self, scope):
        return resolve_operational_scope(scope).key

    def ensure_scope(self, scope: OperationalScope, baseline: dict | None = None, *, connection=None) -> None:
        own = connection is None
        conn = connection or self._connect()
        try:
            if conn.execute("SELECT 1 FROM operational_scopes WHERE scope_key=?", (scope.key,)).fetchone() is None:
                conn.execute("INSERT INTO operational_scopes VALUES (?,?,?,?,?)", (scope.key, scope.kind, scope.scenario_id, scope.scenario_run_id, scope_created_at()))
                if scope.is_simulation:
                    self._seed_scope(conn, scope.key, baseline or {})
            if own:
                conn.commit()
        finally:
            if own:
                conn.close()

    def _require_scope(self, scope):
        key = self._scope_key(scope)
        with self._connect() as conn:
            if conn.execute("SELECT 1 FROM operational_scopes WHERE scope_key=?", (key,)).fetchone() is None:
                raise TeamStatusPersistenceError("operational scope has not been initialized")
        return key

    def _seed_scope(self, conn, scope_key: str, baseline: dict) -> None:
        team = baseline.get("team", {}) if isinstance(baseline, Mapping) else {}
        members = team.get("members", ()) if isinstance(team, Mapping) else ()
        for member in members:
            if isinstance(member, Mapping):
                identity = str(member.get("telegram_identity") or "").strip(); name = str(member.get("full_name") or identity)
            else:
                identity = str(member).strip(); name = identity
            if identity:
                conn.execute("INSERT OR IGNORE INTO team_members(scope_key,telegram_identity,full_name,registered_at,approved) VALUES (?,?,?,?,1)", (scope_key, identity, name, _utc_now()))
        if members and team.get("approve", True):
            conn.execute("INSERT OR IGNORE INTO roster_approval(scope_key,approved_by,approved_at) VALUES (?,?,?)", (scope_key, "simulation-provisioning", _utc_now()))
        state = team.get("operational_state") if isinstance(team, Mapping) else None
        if state is None and isinstance(team, Mapping) and "manpower_count" in team:
            state = team
        if isinstance(state, Mapping) and type(state.get("manpower_count")) is int and state["manpower_count"] >= 0:
            conn.execute(
                "INSERT OR IGNORE INTO operational_team_state(scope_key,manpower_count,resources_json,source_event_id,received_at,scenario_id,scenario_run_id,scenario_time) VALUES (?,?,?,?,?,?,?,?)",
                (scope_key, state["manpower_count"], json.dumps(state.get("resources", ()), ensure_ascii=False, sort_keys=True), state.get("source_event_id"), state.get("received_at") or _utc_now(), state.get("scenario_id"), state.get("scenario_run_id"), state.get("scenario_time")),
            )

    def register_member(self, telegram_identity, full_name, registered_at=None, *, scope=None):
        key = self._require_scope(scope); identity = telegram_identity.strip(); name = " ".join(full_name.split())
        if not identity or not name: raise TeamStatusPersistenceError("telegram identity and full name are required")
        registered_at = registered_at or _utc_now(); _parse_timestamp(registered_at)
        with self._connect() as connection:
            connection.execute("INSERT INTO team_members(scope_key,telegram_identity,full_name,registered_at,approved) VALUES (?,?,?,?,0) ON CONFLICT(scope_key,telegram_identity) DO UPDATE SET full_name=excluded.full_name", (key, identity, name, registered_at))

    def approve_roster(self, approved_by, approved_at=None, *, scope=None):
        key = self._require_scope(scope)
        if not approved_by.strip(): raise TeamStatusPersistenceError("approving commander identity is required")
        approved_at = approved_at or _utc_now(); _parse_timestamp(approved_at)
        with self._connect() as connection:
            count = connection.execute("SELECT COUNT(*) FROM team_members WHERE scope_key=?", (key,)).fetchone()[0]
            if count == 0: raise TeamStatusPersistenceError("cannot approve an empty roster")
            connection.execute("UPDATE team_members SET approved=1 WHERE scope_key=?", (key,))
            connection.execute("INSERT INTO roster_approval(scope_key,approved_by,approved_at) VALUES (?,?,?) ON CONFLICT(scope_key) DO UPDATE SET approved_by=excluded.approved_by,approved_at=excluded.approved_at", (key, approved_by, approved_at))
        return int(count)

    def roster_is_approved(self, *, scope=None):
        key = self._require_scope(scope)
        with self._connect() as connection:
            return connection.execute("SELECT 1 FROM roster_approval WHERE scope_key=?", (key,)).fetchone() is not None

    def list_members(self, *, approved_only=True, scope=None):
        key = self._require_scope(scope); where = " AND approved=1" if approved_only else ""
        with self._connect() as connection:
            return [dict(row) for row in connection.execute(f"SELECT * FROM team_members WHERE scope_key=?{where} ORDER BY full_name", (key,)).fetchall()]

    def open_cycle(self, cycle_key, opened_at, deadline_at, *, scope=None):
        key = self._require_scope(scope)
        if not self.roster_is_approved(scope=scope): raise TeamStatusPersistenceError("the commander must approve the roster before attendance checks begin")
        opened = _parse_timestamp(opened_at); deadline = _parse_timestamp(deadline_at)
        if deadline <= opened: raise TeamStatusPersistenceError("attendance deadline must be after the cycle opens")
        cycle_id = f"attendance-{uuid.uuid4().hex}"; created = True
        with self._connect() as connection:
            try: connection.execute("INSERT INTO attendance_cycles(scope_key,cycle_id,cycle_key,opened_at,deadline_at) VALUES (?,?,?,?,?)", (key, cycle_id, cycle_key, opened_at, deadline_at))
            except sqlite3.IntegrityError: created = False
            row = connection.execute("SELECT cycle_id,cycle_key,opened_at,deadline_at FROM attendance_cycles WHERE scope_key=? AND cycle_key=?", (key, cycle_key)).fetchone()
        return AttendanceCycle(**dict(row), created=created)

    def latest_cycle(self, *, scope=None):
        key = self._require_scope(scope)
        with self._connect() as connection:
            row = connection.execute("SELECT cycle_id,cycle_key,opened_at,deadline_at FROM attendance_cycles WHERE scope_key=? ORDER BY opened_at DESC LIMIT 1", (key,)).fetchone()
        return dict(row) if row is not None else None

    def clear_runtime_state(self, *, scope=None):
        key = self._require_scope(scope)
        with self._connect() as connection:
            responses = int(connection.execute("SELECT COUNT(*) FROM attendance_responses WHERE scope_key=?", (key,)).fetchone()[0])
            cycles = int(connection.execute("SELECT COUNT(*) FROM attendance_cycles WHERE scope_key=?", (key,)).fetchone()[0])
            state = int(connection.execute("SELECT COUNT(*) FROM operational_team_state WHERE scope_key=?", (key,)).fetchone()[0])
            connection.execute("DELETE FROM attendance_responses WHERE scope_key=?", (key,)); connection.execute("DELETE FROM attendance_cycles WHERE scope_key=?", (key,)); connection.execute("DELETE FROM operational_team_state WHERE scope_key=?", (key,))
        return {"attendance_responses": responses, "attendance_cycles": cycles, "runtime_cycles_removed": cycles, "operational_team_state": state}

    def record_operational_state(self, *, manpower_count, resources, source_event_id, received_at, scenario_id=None, scenario_run_id=None, scenario_time=None, scope=None):
        key = self._require_scope(scope)
        if type(manpower_count) is not int or manpower_count < 0: raise TeamStatusPersistenceError("manpower count must be a non-negative integer")
        _parse_timestamp(received_at); payload = json.dumps(resources, ensure_ascii=False, sort_keys=True)
        with self._connect() as connection:
            connection.execute("INSERT INTO operational_team_state(scope_key,manpower_count,resources_json,source_event_id,received_at,scenario_id,scenario_run_id,scenario_time) VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(scope_key) DO UPDATE SET manpower_count=excluded.manpower_count,resources_json=excluded.resources_json,source_event_id=excluded.source_event_id,received_at=excluded.received_at,scenario_id=excluded.scenario_id,scenario_run_id=excluded.scenario_run_id,scenario_time=excluded.scenario_time", (key, manpower_count, payload, source_event_id, received_at, scenario_id, scenario_run_id, scenario_time))
            row = connection.execute("SELECT * FROM operational_team_state WHERE scope_key=?", (key,)).fetchone()
        result = dict(row); result["resources"] = json.loads(result.pop("resources_json")); return result

    def operational_state(self, *, scope=None):
        key = self._require_scope(scope)
        with self._connect() as connection: row = connection.execute("SELECT * FROM operational_team_state WHERE scope_key=?", (key,)).fetchone()
        if row is None: return None
        result = dict(row); result["resources"] = json.loads(result.pop("resources_json")); return result

    def record_response(self, *, telegram_identity, source_message_id, availability, original_text, received_at, reason=None, unavailable_until=None, availability_start=None, availability_end=None, scope=None):
        key = self._require_scope(scope)
        if availability not in {"available", "unavailable"}: raise TeamStatusPersistenceError("availability must be 'available' or 'unavailable'")
        if availability == "unavailable" and not (reason or "").strip(): raise TeamStatusPersistenceError("an unavailable response requires a reason")
        if availability == "available" and any(value is not None for value in (reason, unavailable_until, availability_start, availability_end)): raise TeamStatusPersistenceError("an available response cannot include unavailable-period fields")
        received = _parse_timestamp(received_at); normalized_start = _parse_timestamp(availability_start).isoformat() if availability_start else None; normalized_end = _parse_timestamp(availability_end).isoformat() if availability_end else None; normalized_until = _parse_timestamp(unavailable_until).isoformat() if unavailable_until else None
        if availability == "unavailable":
            if (normalized_start is None) != (normalized_end is None): raise TeamStatusPersistenceError("an unavailable response requires both availability_start and availability_end")
            if normalized_start and normalized_end <= normalized_start: raise TeamStatusPersistenceError("availability_end must be after availability_start")
            if normalized_end and normalized_until and normalized_end != normalized_until: raise TeamStatusPersistenceError("unavailable_until must equal availability_end")
            if normalized_end: normalized_until = normalized_end
        cycle = self.latest_cycle(scope=scope)
        if cycle is None: raise TeamStatusPersistenceError("no attendance cycle is open")
        approval_status = "accepted" if received <= _parse_timestamp(cycle["deadline_at"]) else "pending"; response_id = f"response-{uuid.uuid4().hex}"
        with self._connect() as connection:
            member = connection.execute("SELECT approved FROM team_members WHERE scope_key=? AND telegram_identity=?", (key, telegram_identity)).fetchone()
            if member is None or not member["approved"]: raise TeamStatusPersistenceError("attendance responses are accepted only from the approved roster")
            try:
                connection.execute("INSERT INTO attendance_responses(scope_key,response_id,source_message_id,cycle_id,telegram_identity,availability,reason,availability_start,availability_end,unavailable_until,original_text,received_at,approval_status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", (key,response_id,source_message_id,cycle["cycle_id"],telegram_identity,availability,reason.strip() if reason else None,normalized_start,normalized_end,normalized_until,original_text,received_at,approval_status))
            except sqlite3.IntegrityError:
                row = connection.execute("SELECT * FROM attendance_responses WHERE scope_key=? AND source_message_id=?", (key, source_message_id)).fetchone(); return dict(row)
            return dict(connection.execute("SELECT * FROM attendance_responses WHERE scope_key=? AND response_id=?", (key, response_id)).fetchone())

    def review_late_response(self, response_id, *, approved, reviewed_by, reviewed_at=None, scope=None):
        key = self._require_scope(scope); reviewed_at = reviewed_at or _utc_now(); _parse_timestamp(reviewed_at)
        with self._connect() as connection:
            row = connection.execute("SELECT approval_status FROM attendance_responses WHERE scope_key=? AND response_id=?", (key,response_id)).fetchone()
            if row is None: raise TeamStatusPersistenceError("late response was not found")
            if row["approval_status"] != "pending": raise TeamStatusPersistenceError("response is not awaiting commander review")
            connection.execute("UPDATE attendance_responses SET approval_status=?,reviewed_by=?,reviewed_at=? WHERE scope_key=? AND response_id=?", ("accepted" if approved else "rejected", reviewed_by, reviewed_at, key, response_id))
            return dict(connection.execute("SELECT * FROM attendance_responses WHERE scope_key=? AND response_id=?", (key,response_id)).fetchone())

    def pending_late_responses(self, *, scope=None):
        key = self._require_scope(scope)
        with self._connect() as connection:
            rows = connection.execute("SELECT r.*,m.full_name FROM attendance_responses r JOIN team_members m ON r.scope_key=m.scope_key AND r.telegram_identity=m.telegram_identity WHERE r.scope_key=? AND r.approval_status='pending' ORDER BY r.received_at", (key,)).fetchall()
        return [dict(row) for row in rows]

    def availability_snapshot(self, as_of, *, scope=None):
        key = self._require_scope(scope); instant = _parse_timestamp(as_of); members = self.list_members(scope=scope)
        snapshot = []
        with self._connect() as connection:
            latest_cycle = connection.execute(
                "SELECT cycle_id FROM attendance_cycles WHERE scope_key=? AND opened_at<=? ORDER BY opened_at DESC LIMIT 1",
                (key, instant.isoformat()),
            ).fetchone()
            cycle_id = latest_cycle["cycle_id"] if latest_cycle is not None else None
            for member in members:
                accepted = connection.execute(
                    "SELECT * FROM attendance_responses WHERE scope_key=? AND telegram_identity=? AND approval_status='accepted' ORDER BY received_at DESC LIMIT 1",
                    (key, member["telegram_identity"]),
                ).fetchone()
                entry = {"scope_key": key, "telegram_identity": member["telegram_identity"], "full_name": member["full_name"], "availability": "awaiting_response", "reason": None, "availability_start": None, "availability_end": None, "unavailable_until": None, "original_text": None, "received_at": None}
                if accepted is not None:
                    response = dict(accepted)
                    active = response["availability"] == "unavailable" and (response["availability_end"] is None or instant < _parse_timestamp(response["availability_end"]))
                    is_current_cycle = cycle_id is not None and response["cycle_id"] == cycle_id
                    if response["availability"] == "unavailable" and active:
                        entry.update(response)
                    elif is_current_cycle:
                        entry.update(response); entry["availability"] = "available"
                snapshot.append(entry)
        return snapshot
