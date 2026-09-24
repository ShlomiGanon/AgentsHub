"""Response Team (SEC_001) profile-only persistence (docs/responce_improve.md).

Three small SQLite stores, all opened against the *same* `db_path` as the
profile's core `events`/`users` database (`profiles.response_team.DB_PATH`) --
never against a second file, and never added to `persistence/schema.py` (that
module is shared by every profile, including Fire and Rescue, and this
architecture's own rule forbids adding profile-only domain tables there).
Each store below only ever runs its own `CREATE TABLE IF NOT EXISTS` DDL
against whatever connection it is given, so it coexists safely in one SQLite
file alongside the core tables and the other two stores here.

Imported only by `profiles/response_team.py` and its profile-only agent
classes (via this module's `persistence` facade re-export) -- never by a
shared agent module.

`ResponseTeamRosterStore` mirrors `persistence/team_status_store.py`'s shape
(same table names/columns) plus one addition: `team_members.current_area`.
`ResponseTeamSurveillanceStore` mirrors `persistence/surveillance_store.py`'s
shape (same table names/columns), minus that module's own hardcoded demo-data
seed -- this profile's own camera/drone catalog and ETA arithmetic are
supplied by the caller (`profiles/response_team.py`), never hardcoded here.
`NeighboringForceStore` is wholly new: one dispatch-log table with no
existing contract to mirror.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from persistence.surveillance_contracts import (
    SurveillancePersistenceError,
    SurveillancePersistenceInterface,
)
from persistence.team_status_contracts import (
    AttendanceCycle,
    TeamStatusPersistenceError,
    TeamStatusPersistenceInterface,
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_timestamp(value: str) -> datetime:
    """A timestamp with no offset is assumed UTC rather than rejected -- the
    same convention `persistence/team_status_store.py`'s own `_parse_timestamp`
    already uses."""

    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise TeamStatusPersistenceError(f"invalid ISO timestamp: {value!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


# == Roster / attendance (+ current_area) ====================================

_ROSTER_SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS team_members (
    telegram_identity TEXT PRIMARY KEY,
    full_name TEXT NOT NULL,
    registered_at TEXT NOT NULL,
    approved INTEGER NOT NULL DEFAULT 0 CHECK (approved IN (0, 1)),
    current_area TEXT
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

CREATE INDEX IF NOT EXISTS idx_rt_attendance_responses_member_time
ON attendance_responses(telegram_identity, received_at DESC);

CREATE INDEX IF NOT EXISTS idx_rt_attendance_responses_cycle
ON attendance_responses(cycle_id, telegram_identity, received_at DESC);
"""


class ResponseTeamRosterStore(TeamStatusPersistenceInterface):
    """Same contract as `persistence.team_status_contracts
    .TeamStatusPersistenceInterface` (so it can be used anywhere that
    interface is expected -- including `profiles.simulation.SimulationRoster
    .open`), plus `set_current_area`/`get_member` for `report_team_movement`.
    """

    def __init__(self, db_path: str):
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(_ROSTER_SCHEMA)

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
                f"SELECT telegram_identity, full_name, registered_at, approved, current_area "
                f"FROM team_members {where} ORDER BY full_name"
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
                    "current_area": member["current_area"],
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

    def get_member(self, telegram_identity: str) -> dict | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT telegram_identity, full_name, registered_at, approved, current_area "
                "FROM team_members WHERE telegram_identity = ?",
                (telegram_identity,),
            ).fetchone()
        return dict(row) if row is not None else None

    def set_current_area(self, telegram_identity: str, area: str, updated_at: str | None = None) -> dict:
        area = area.strip()
        if not area:
            raise TeamStatusPersistenceError("area is required")
        with self._connect() as connection:
            member = connection.execute(
                "SELECT * FROM team_members WHERE telegram_identity = ?", (telegram_identity,)
            ).fetchone()
            if member is None or not member["approved"]:
                raise TeamStatusPersistenceError(
                    "current area can only be recorded for an approved roster member"
                )
            connection.execute(
                "UPDATE team_members SET current_area = ? WHERE telegram_identity = ?",
                (area, telegram_identity),
            )
            updated = connection.execute(
                "SELECT telegram_identity, full_name, registered_at, approved, current_area "
                "FROM team_members WHERE telegram_identity = ?",
                (telegram_identity,),
            ).fetchone()
        return dict(updated)


def open_response_team_roster_store(db_path: str) -> ResponseTeamRosterStore:
    return ResponseTeamRosterStore(db_path)


# == Surveillance (cameras / drones / drone_missions) ========================

_SURVEILLANCE_SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS cameras (
    camera_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    area TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('active', 'offline', 'degraded')),
    azimuth_degrees INTEGER NOT NULL DEFAULT 0,
    feed_summary TEXT NOT NULL,
    last_updated TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS drones (
    drone_id TEXT PRIMARY KEY,
    callsign TEXT NOT NULL UNIQUE,
    model TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('ready', 'in_flight', 'charging', 'maintenance')),
    battery_percent INTEGER NOT NULL CHECK (battery_percent BETWEEN 0 AND 100),
    current_area TEXT NOT NULL,
    assigned_mission_id TEXT,
    last_updated TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS drone_missions (
    mission_id TEXT PRIMARY KEY,
    drone_id TEXT NOT NULL REFERENCES drones(drone_id),
    target_area TEXT NOT NULL,
    mission_type TEXT NOT NULL,
    incident_description TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('dispatched', 'en_route', 'on_station', 'completed', 'aborted')),
    dispatched_by TEXT NOT NULL,
    dispatched_at TEXT NOT NULL,
    eta_seconds INTEGER NOT NULL,
    notes TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_rt_cameras_area ON cameras(area);
CREATE INDEX IF NOT EXISTS idx_rt_drones_status ON drones(status);
CREATE INDEX IF NOT EXISTS idx_rt_missions_status ON drone_missions(status);
"""

_DRONE_STATUS_SYNONYMS = {
    "ready": "ready",
    "available": "ready",
    "standby": "ready",
    "idle": "ready",
    "free": "ready",
    "in_flight": "in_flight",
    "in flight": "in_flight",
    "flight": "in_flight",
    "flying": "in_flight",
    "active": "in_flight",
    "on_mission": "in_flight",
    "on mission": "in_flight",
    "dispatched": "in_flight",
    "charging": "charging",
    "maintenance": "maintenance",
    "offline": "maintenance",
}


class ResponseTeamSurveillanceStore(SurveillancePersistenceInterface):
    """Same contract/shape as `persistence.surveillance_store
    .SQLiteSurveillancePersistence`, with no hardcoded demo-data seed (the
    profile supplies its own camera/drone catalog via `ensure_camera`/
    `ensure_drone`) and a pluggable `eta_fn`/`home_area` instead of the
    standby-sector-specific ETA table and `'central_hub'` recall target.
    """

    def __init__(
        self,
        db_path: str,
        *,
        eta_fn: Callable[[str, str], int] | None = None,
        home_area: str = "home_base",
    ):
        self.db_path = str(db_path)
        self._eta_fn = eta_fn
        self.home_area = home_area
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SURVEILLANCE_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        return conn

    def _calculate_eta(self, origin_area: str, target_area: str) -> int:
        if origin_area == target_area:
            return 45
        if self._eta_fn is not None:
            return self._eta_fn(origin_area, target_area)
        return 180

    # -- create-if-missing seeding (profile provisioning) --------------------

    def ensure_camera(
        self,
        camera_id: str,
        *,
        name: str,
        area: str,
        status: str = "active",
        azimuth_degrees: int = 0,
        feed_summary: str = "",
        now_iso: str | None = None,
    ) -> bool:
        """Inserts the camera row only if `camera_id` doesn't already exist;
        an existing row is never overwritten. Returns True if inserted."""

        now = now_iso or _utc_now()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO cameras (camera_id, name, area, status, azimuth_degrees, feed_summary, last_updated)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (camera_id, name, area, status, azimuth_degrees, feed_summary, now),
            )
            return cursor.rowcount > 0

    def ensure_drone(
        self,
        drone_id: str,
        *,
        callsign: str,
        model: str,
        status: str = "ready",
        battery_percent: int = 100,
        current_area: str,
        now_iso: str | None = None,
    ) -> bool:
        """Inserts the drone row only if `drone_id` doesn't already exist; an
        existing row is never overwritten. Returns True if inserted."""

        now = now_iso or _utc_now()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO drones (drone_id, callsign, model, status, battery_percent, current_area, assigned_mission_id, last_updated)
                VALUES (?, ?, ?, ?, ?, ?, NULL, ?)
                """,
                (drone_id, callsign, model, status, battery_percent, current_area, now),
            )
            return cursor.rowcount > 0

    # -- SurveillancePersistenceInterface -------------------------------------

    def list_cameras(self, area: str | None = None, status: str | None = None) -> list[dict]:
        query = "SELECT * FROM cameras WHERE 1=1"
        params: list[object] = []
        if area:
            query += " AND area = ?"
            params.append(area.strip().lower())
        if status:
            query += " AND status = ?"
            params.append(status.strip().lower())
        query += " ORDER BY camera_id ASC"

        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
            return [dict(row) for row in rows]

    def get_camera(self, camera_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM cameras WHERE camera_id = ?", (camera_id.strip(),)).fetchone()
            return dict(row) if row is not None else None

    def update_camera_feed(
        self, camera_id: str, feed_summary: str, status: str | None = None, updated_at: str | None = None
    ) -> dict:
        now = updated_at or _utc_now()
        with self._connect() as conn:
            camera = conn.execute("SELECT * FROM cameras WHERE camera_id = ?", (camera_id.strip(),)).fetchone()
            if camera is None:
                raise SurveillancePersistenceError(f"Camera '{camera_id}' not found.")

            new_status = status.strip().lower() if status else camera["status"]
            conn.execute(
                """
                UPDATE cameras
                SET feed_summary = ?, status = ?, last_updated = ?
                WHERE camera_id = ?
                """,
                (feed_summary.strip(), new_status, now, camera_id.strip()),
            )
            updated = conn.execute("SELECT * FROM cameras WHERE camera_id = ?", (camera_id.strip(),)).fetchone()
            return dict(updated)

    def list_drones(self, status: str | None = None) -> list[dict]:
        query = "SELECT * FROM drones WHERE 1=1"
        params: list[object] = []
        if status:
            cleaned = status.strip().lower()
            if cleaned not in {"all", "*"}:
                mapped = _DRONE_STATUS_SYNONYMS.get(cleaned, cleaned)
                query += " AND status = ?"
                params.append(mapped)
        query += " ORDER BY callsign ASC"

        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
            return [dict(row) for row in rows]

    def get_drone(self, drone_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM drones WHERE drone_id = ?", (drone_id.strip(),)).fetchone()
            return dict(row) if row is not None else None

    def dispatch_drone(
        self,
        *,
        target_area: str,
        incident_description: str,
        mission_type: str = "recon",
        dispatched_by: str = "commander",
        specific_drone_id: str | None = None,
        now_iso: str | None = None,
    ) -> dict:
        now = now_iso or _utc_now()
        with self._connect() as conn:
            if specific_drone_id:
                drone = conn.execute(
                    "SELECT * FROM drones WHERE drone_id = ? AND status = 'ready'",
                    (specific_drone_id.strip(),),
                ).fetchone()
                if drone is None:
                    raise SurveillancePersistenceError(
                        f"Requested drone '{specific_drone_id}' is not currently available for dispatch."
                    )
            else:
                drones = conn.execute(
                    "SELECT * FROM drones WHERE status = 'ready' ORDER BY battery_percent DESC"
                ).fetchall()
                if not drones:
                    raise SurveillancePersistenceError("No ready drones available in fleet for immediate dispatch.")
                same_area = [d for d in drones if d["current_area"].lower() == target_area.lower()]
                drone = same_area[0] if same_area else drones[0]

            mission_id = f"MSN-{uuid.uuid4().hex[:8].upper()}"
            eta_seconds = self._calculate_eta(drone["current_area"], target_area)

            conn.execute(
                """
                INSERT INTO drone_missions (
                    mission_id, drone_id, target_area, mission_type, incident_description,
                    status, dispatched_by, dispatched_at, eta_seconds, notes, updated_at
                )
                VALUES (?, ?, ?, ?, ?, 'dispatched', ?, ?, ?, '', ?)
                """,
                (
                    mission_id,
                    drone["drone_id"],
                    target_area.strip(),
                    mission_type.strip(),
                    incident_description.strip(),
                    dispatched_by.strip(),
                    now,
                    eta_seconds,
                    now,
                ),
            )

            conn.execute(
                """
                UPDATE drones
                SET status = 'in_flight', current_area = ?, assigned_mission_id = ?, last_updated = ?
                WHERE drone_id = ?
                """,
                (target_area.strip(), mission_id, now, drone["drone_id"]),
            )

            mission = conn.execute("SELECT * FROM drone_missions WHERE mission_id = ?", (mission_id,)).fetchone()
            updated_drone = conn.execute("SELECT * FROM drones WHERE drone_id = ?", (drone["drone_id"],)).fetchone()
            result = dict(mission)
            result["drone"] = dict(updated_drone)
            return result

    def get_active_missions(self) -> list[dict]:
        query = """
            SELECT m.*, d.callsign, d.model, d.battery_percent
            FROM drone_missions m
            JOIN drones d ON m.drone_id = d.drone_id
            WHERE m.status IN ('dispatched', 'en_route', 'on_station')
            ORDER BY m.dispatched_at DESC
        """
        with self._connect() as conn:
            rows = conn.execute(query).fetchall()
            return [dict(row) for row in rows]

    def recall_drone(self, identifier: str | None = None, now_iso: str | None = None) -> dict:
        """Recall exactly one active drone, never selecting arbitrarily among multiple missions."""

        now = now_iso or _utc_now()
        requested = (identifier or "").strip().casefold()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                """
                SELECT m.*, d.callsign, d.model, d.battery_percent
                FROM drone_missions m
                JOIN drones d ON m.drone_id = d.drone_id
                WHERE m.status IN ('dispatched', 'en_route', 'on_station')
                ORDER BY d.callsign ASC, m.dispatched_at ASC
                """
            ).fetchall()
            missions = [dict(row) for row in rows]

            if not missions:
                return {"status": "no_active", "missions": []}

            if requested:
                matches = [
                    mission
                    for mission in missions
                    if requested
                    in {
                        mission["mission_id"].casefold(),
                        mission["drone_id"].casefold(),
                        mission["callsign"].casefold(),
                    }
                ]
                if len(matches) != 1:
                    return {"status": "not_found", "requested": identifier, "missions": missions}
                selected = matches[0]
            elif len(missions) == 1:
                selected = missions[0]
            else:
                return {"status": "selection_required", "missions": missions}

            note = f"Operator recall: returned to {self.home_area}."
            conn.execute(
                "UPDATE drone_missions SET status = 'aborted', notes = ?, updated_at = ? WHERE mission_id = ?",
                (note, now, selected["mission_id"]),
            )
            conn.execute(
                """
                UPDATE drones
                SET status = 'ready', current_area = ?, assigned_mission_id = NULL, last_updated = ?
                WHERE drone_id = ?
                """,
                (self.home_area, now, selected["drone_id"]),
            )
            updated_mission = dict(
                conn.execute("SELECT * FROM drone_missions WHERE mission_id = ?", (selected["mission_id"],)).fetchone()
            )
            updated_drone = dict(conn.execute("SELECT * FROM drones WHERE drone_id = ?", (selected["drone_id"],)).fetchone())
            return {"status": "returned", "mission": updated_mission, "drone": updated_drone}

    def recall_all_drones(self, now_iso: str | None = None) -> dict:
        """Recall every active drone in one atomic operation."""

        now = now_iso or _utc_now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                """
                SELECT m.*, d.callsign
                FROM drone_missions m
                JOIN drones d ON m.drone_id = d.drone_id
                WHERE m.status IN ('dispatched', 'en_route', 'on_station')
                ORDER BY d.callsign ASC, m.dispatched_at ASC
                """
            ).fetchall()
            missions = [dict(row) for row in rows]
            if not missions:
                return {"status": "no_active", "missions": []}

            note = f"Operator recall: returned to {self.home_area}."
            for mission in missions:
                conn.execute(
                    "UPDATE drone_missions SET status = 'aborted', notes = ?, updated_at = ? WHERE mission_id = ?",
                    (note, now, mission["mission_id"]),
                )
                conn.execute(
                    """
                    UPDATE drones
                    SET status = 'ready', current_area = ?, assigned_mission_id = NULL, last_updated = ?
                    WHERE drone_id = ?
                    """,
                    (self.home_area, now, mission["drone_id"]),
                )
            return {"status": "returned_all", "missions": missions}

    def update_mission_status(
        self,
        mission_id: str,
        status: str,
        notes: str | None = None,
        updated_at: str | None = None,
    ) -> dict:
        now = updated_at or _utc_now()
        with self._connect() as conn:
            mission = conn.execute("SELECT * FROM drone_missions WHERE mission_id = ?", (mission_id,)).fetchone()
            if mission is None:
                raise SurveillancePersistenceError(f"Mission '{mission_id}' not found.")

            conn.execute(
                """
                UPDATE drone_missions
                SET status = ?, notes = COALESCE(?, notes), updated_at = ?
                WHERE mission_id = ?
                """,
                (status, notes, now, mission_id),
            )

            if status in ("completed", "aborted"):
                conn.execute(
                    """
                    UPDATE drones
                    SET status = 'ready', assigned_mission_id = NULL, last_updated = ?
                    WHERE drone_id = ?
                    """,
                    (now, mission["drone_id"]),
                )

            updated = conn.execute("SELECT * FROM drone_missions WHERE mission_id = ?", (mission_id,)).fetchone()
            return dict(updated)

    def surveillance_overview(self, area: str | None = None) -> dict:
        cameras = self.list_cameras(area=area)
        drones = self.list_drones()
        active_missions = self.get_active_missions()
        if area:
            active_missions = [m for m in active_missions if m["target_area"].lower() == area.lower()]

        return {
            "area": area or "all_sectors",
            "cameras": cameras,
            "drones": drones,
            "active_missions": active_missions,
            "active_camera_count": sum(1 for c in cameras if c["status"] == "active"),
            "ready_drone_count": sum(1 for d in drones if d["status"] == "ready"),
            "in_flight_drone_count": sum(1 for d in drones if d["status"] == "in_flight"),
            "as_of": _utc_now(),
        }


def open_response_team_surveillance_store(
    db_path: str, *, eta_fn: Callable[[str, str], int] | None = None, home_area: str = "home_base"
) -> ResponseTeamSurveillanceStore:
    return ResponseTeamSurveillanceStore(db_path, eta_fn=eta_fn, home_area=home_area)


# == Neighboring forces (new) =================================================

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
    """`neighboring_force_dispatches` -- request/response log only; force
    kinds and home bases stay profile constants (`profiles.response_team
    .FORCE_BASES`), never a standing-units table.

    The ETA ticker (docs/responce_improve.md's documented exception to "a
    tool result proves only the tool's own effect") is implemented the same
    way `persistence/team_status_store.py`'s own `availability_snapshot`
    already computes derived state at query time rather than via a separate
    background writer: every read (`list_dispatches`) and every write
    (`dispatch`) first advances any `en_route` row whose ETA has elapsed to
    `arrived`, with no human report involved in that specific transition.
    """

    def __init__(self, db_path: str):
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_NEIGHBORING_FORCE_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _advance(self, conn: sqlite3.Connection, now_iso: str) -> None:
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


def open_neighboring_force_store(db_path: str) -> NeighboringForceStore:
    return NeighboringForceStore(db_path)
