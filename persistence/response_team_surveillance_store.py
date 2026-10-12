"""Camera, drone, and mission tables for the response-team profile database."""

from __future__ import annotations

import sqlite3
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Callable

from persistence.response_team_support import _parse_timestamp, _utc_now
from persistence.surveillance_contracts import (
    SurveillancePersistenceError,
    SurveillancePersistenceInterface,
)
from persistence.team_status_contracts import TeamStatusPersistenceError

# --- schema ---

_SURVEILLANCE_SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS cameras (
    camera_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    area TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('active', 'offline', 'degraded')),
    azimuth_degrees INTEGER NOT NULL DEFAULT 0,
    feed_summary TEXT NOT NULL,
    last_updated TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'fixed'
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
    updated_at TEXT NOT NULL,
    event_id TEXT,
    incident_id TEXT
);

CREATE INDEX IF NOT EXISTS idx_rt_cameras_area ON cameras(area);
CREATE INDEX IF NOT EXISTS idx_rt_drones_status ON drones(status);
CREATE INDEX IF NOT EXISTS idx_rt_missions_status ON drone_missions(status);
"""

# Columns added after the first release of these tables. CREATE TABLE IF NOT EXISTS never
# alters an existing table, so a database created earlier gets them through ALTER TABLE.
_ADDED_COLUMNS = {
    "cameras": (("kind", "TEXT NOT NULL DEFAULT 'fixed'"),),
    "drone_missions": (("event_id", "TEXT"), ("incident_id", "TEXT")),
}

# Indexes on added columns can only be created once the migration above has run.
_POST_MIGRATION_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_rt_missions_event ON drone_missions(event_id);
CREATE INDEX IF NOT EXISTS idx_rt_missions_incident ON drone_missions(incident_id);
"""

# A fixed camera watches one sector; a mast camera exists only once the mast is raised; a drone
# camera is live only while a drone is overhead. Summaries need the difference so temporary
# feeds are never counted as permanent fence coverage.
CAMERA_KINDS = ("fixed", "mast", "drone")

_ACTIVE_MISSION_SQL = "('dispatched', 'en_route', 'on_station')"
_MISSION_END_OUTCOMES = ("completed", "aborted")

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


def _add_missing_columns(conn: sqlite3.Connection) -> None:
    """Add every column in _ADDED_COLUMNS that an older database file does not have yet."""

    for table, columns in _ADDED_COLUMNS.items():
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        for name, ddl in columns:
            if name in existing:
                continue
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")
            except sqlite3.OperationalError as exc:
                # Another process opening the same file may have added it a moment earlier.
                if "duplicate column" not in str(exc).lower():
                    raise


class ResponseTeamSurveillanceStore(SurveillancePersistenceInterface):
    """Surveillance contract without a demo seed; the profile supplies cameras, drones, and ETA."""

    def __init__(
        self,
        db_path: str,
        *,
        eta_fn: Callable[[str, str], int] | None = None,
        home_area: str = "home_base",
        mission_loiter_seconds: int | None = None,
        clock: Callable[[], str] | None = None,
    ):
        """Open the DB file, create or migrate the tables, and keep the mission lifecycle settings."""

        self.db_path = str(db_path)
        self._eta_fn = eta_fn
        self.home_area = home_area
        # None keeps open-ended missions (no lifecycle, no area reuse) for a profile that has
        # not declared a loiter window; a mission that never ends cannot meaningfully be reused.
        self.mission_loiter_seconds = mission_loiter_seconds
        self._clock = clock or _utc_now
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SURVEILLANCE_SCHEMA)
            _add_missing_columns(conn)
            conn.executescript(_POST_MIGRATION_INDEXES)

    def _connect(self) -> sqlite3.Connection:
        """Open a row-factory connection with foreign keys enabled."""

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        return conn

    def _now(self, now_iso: str | None) -> str:
        """Return the caller's timestamp, or the injected clock's current time."""

        return now_iso or self._clock()

    def _advance_missions(self, conn: sqlite3.Connection, now_iso: str) -> None:
        """Move active missions along their timeline: on_station after the ETA, completed after loitering."""

        if self.mission_loiter_seconds is None:
            return
        now = _parse_timestamp(now_iso)
        rows = conn.execute(
            "SELECT mission_id, drone_id, status, dispatched_at, eta_seconds FROM drone_missions "
            f"WHERE status IN {_ACTIVE_MISSION_SQL}"
        ).fetchall()
        for row in rows:
            # Transition times derive from dispatched_at, the ETA, and the loiter window, never
            # from the read time, so the same history always produces the same rows.
            try:
                arrival = _parse_timestamp(row["dispatched_at"]) + timedelta(seconds=row["eta_seconds"])
            except TeamStatusPersistenceError:
                # A hand-edited, unparseable timestamp leaves that one mission as it is rather
                # than failing every read of the fleet.
                continue
            completion = arrival + timedelta(seconds=self.mission_loiter_seconds)
            if now >= completion:
                completed_at = completion.isoformat()
                conn.execute(
                    "UPDATE drone_missions SET status = 'completed', notes = ?, updated_at = ? WHERE mission_id = ?",
                    (f"Loiter window elapsed; returned to {self.home_area}.", completed_at, row["mission_id"]),
                )
                # Free the drone only if an admin edit has not already moved it elsewhere.
                conn.execute(
                    """
                    UPDATE drones
                    SET status = 'ready', current_area = ?, assigned_mission_id = NULL, last_updated = ?
                    WHERE drone_id = ? AND assigned_mission_id = ?
                    """,
                    (self.home_area, completed_at, row["drone_id"], row["mission_id"]),
                )
            elif now >= arrival and row["status"] != "on_station":
                conn.execute(
                    "UPDATE drone_missions SET status = 'on_station', updated_at = ? WHERE mission_id = ?",
                    (arrival.isoformat(), row["mission_id"]),
                )

    def _covering_mission(
        self, conn: sqlite3.Connection, target_area: str, specific_drone_id: str | None
    ) -> sqlite3.Row | None:
        """Return the newest active mission over target_area, honoring an explicitly requested drone."""

        row = conn.execute(
            f"""
            SELECT * FROM drone_missions
            WHERE status IN {_ACTIVE_MISSION_SQL} AND lower(trim(target_area)) = ?
            ORDER BY dispatched_at DESC LIMIT 1
            """,
            (target_area.strip().lower(),),
        ).fetchone()
        if row is None:
            return None
        if specific_drone_id and row["drone_id"].casefold() != specific_drone_id.strip().casefold():
            # The caller named a particular drone, which is a request for that drone, not coverage.
            return None
        return row

    def _calculate_eta(self, origin_area: str, target_area: str) -> int:
        """Return seconds of travel, using the injected table or a same-area / default fallback."""

        if origin_area == target_area:
            return 45
        if self._eta_fn is not None:
            return self._eta_fn(origin_area, target_area)
        return 180

    # --- seed ---

    def ensure_camera(
        self,
        camera_id: str,
        *,
        name: str,
        area: str,
        status: str = "active",
        azimuth_degrees: int = 0,
        feed_summary: str = "",
        kind: str = "fixed",
        now_iso: str | None = None,
    ) -> bool:
        """Insert a cameras row only when camera_id is new; True if inserted."""

        if kind not in CAMERA_KINDS:
            raise SurveillancePersistenceError(f"Unknown camera kind '{kind}'; expected one of {CAMERA_KINDS}.")
        now = now_iso or _utc_now()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO cameras (camera_id, name, area, status, azimuth_degrees, feed_summary, last_updated, kind)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (camera_id, name, area, status, azimuth_degrees, feed_summary, now, kind),
            )
            inserted = cursor.rowcount > 0
            # Name and kind are declared metadata, refreshed every seed; status and feed are
            # live operator state and stay as they are on an existing row.
            conn.execute("UPDATE cameras SET name = ?, kind = ? WHERE camera_id = ?", (name, kind, camera_id))
            return inserted

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
        """Insert a drones row only when drone_id is new; True if inserted."""

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

    # --- cameras / drones ---

    def list_cameras(self, area: str | None = None, status: str | None = None) -> list[dict]:
        """Return cameras rows, optionally filtered by area and status."""

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
        """Return one cameras row, or None."""

        with self._connect() as conn:
            row = conn.execute("SELECT * FROM cameras WHERE camera_id = ?", (camera_id.strip(),)).fetchone()
            return dict(row) if row is not None else None

    def update_camera_feed(
        self, camera_id: str, feed_summary: str, status: str | None = None, updated_at: str | None = None
    ) -> dict:
        """Write feed_summary and optional status on a cameras row."""

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

    def list_drones(self, status: str | None = None, *, now_iso: str | None = None) -> list[dict]:
        """Return drones rows, mapping informal status words onto stored values."""

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
            self._advance_missions(conn, self._now(now_iso))
            rows = conn.execute(query, params).fetchall()
            return [dict(row) for row in rows]

    def get_drone(self, drone_id: str, *, now_iso: str | None = None) -> dict | None:
        """Return one drones row, or None."""

        with self._connect() as conn:
            self._advance_missions(conn, self._now(now_iso))
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
        event_id: str | None = None,
        incident_id: str | None = None,
    ) -> dict:
        """Reuse a mission already over target_area, or create one and mark the chosen ready drone in_flight.

        The result carries `reused` so callers can say the area is already covered instead of
        reporting a new launch.
        """

        now = self._now(now_iso)
        with self._connect() as conn:
            self._advance_missions(conn, now)
            if self.mission_loiter_seconds is not None:
                covering = self._covering_mission(conn, target_area, specific_drone_id)
                if covering is not None:
                    # An unlinked mission adopts the first event that relies on it; an already
                    # linked one keeps its original event so its release stays with that event.
                    conn.execute(
                        "UPDATE drone_missions SET event_id = COALESCE(event_id, ?), "
                        "incident_id = COALESCE(incident_id, ?) WHERE mission_id = ?",
                        (event_id, incident_id, covering["mission_id"]),
                    )
                    mission = conn.execute(
                        "SELECT * FROM drone_missions WHERE mission_id = ?", (covering["mission_id"],)
                    ).fetchone()
                    covering_drone = conn.execute(
                        "SELECT * FROM drones WHERE drone_id = ?", (covering["drone_id"],)
                    ).fetchone()
                    result = dict(mission)
                    result["drone"] = dict(covering_drone)
                    result["reused"] = True
                    return result

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
                    status, dispatched_by, dispatched_at, eta_seconds, notes, updated_at,
                    event_id, incident_id
                )
                VALUES (?, ?, ?, ?, ?, 'dispatched', ?, ?, ?, '', ?, ?, ?)
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
                    event_id or None,
                    incident_id or None,
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
            result["reused"] = False
            return result

    def get_active_missions(self, *, now_iso: str | None = None) -> list[dict]:
        """Return in-progress drone_missions rows joined with drone details."""

        query = """
            SELECT m.*, d.callsign, d.model, d.battery_percent
            FROM drone_missions m
            JOIN drones d ON m.drone_id = d.drone_id
            WHERE m.status IN ('dispatched', 'en_route', 'on_station')
            ORDER BY m.dispatched_at DESC
        """
        with self._connect() as conn:
            self._advance_missions(conn, self._now(now_iso))
            rows = conn.execute(query).fetchall()
            return [dict(row) for row in rows]

    def end_missions(
        self,
        *,
        event_id: str | None = None,
        incident_id: str | None = None,
        outcome: str = "completed",
        reason: str = "",
        now_iso: str | None = None,
    ) -> list[dict]:
        """End every active mission linked to event_id or incident_id and return those drones home.

        At least one reference is required so a missing id can never end the whole fleet's work.
        """

        if not (event_id or incident_id):
            raise SurveillancePersistenceError("event_id or incident_id is required to end missions.")
        if outcome not in _MISSION_END_OUTCOMES:
            raise SurveillancePersistenceError(f"Unknown mission outcome '{outcome}'.")
        now = self._now(now_iso)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._advance_missions(conn, now)
            rows = conn.execute(
                f"""
                SELECT m.*, d.callsign
                FROM drone_missions m
                JOIN drones d ON m.drone_id = d.drone_id
                WHERE m.status IN {_ACTIVE_MISSION_SQL}
                  AND ((? IS NOT NULL AND m.event_id = ?) OR (? IS NOT NULL AND m.incident_id = ?))
                ORDER BY d.callsign ASC, m.dispatched_at ASC
                """,
                (event_id or None, event_id or None, incident_id or None, incident_id or None),
            ).fetchall()
            ended = [dict(row) for row in rows]
            note = reason.strip() or f"Released with its event; returned to {self.home_area}."
            for mission in ended:
                conn.execute(
                    "UPDATE drone_missions SET status = ?, notes = ?, updated_at = ? WHERE mission_id = ?",
                    (outcome, note, now, mission["mission_id"]),
                )
                conn.execute(
                    """
                    UPDATE drones
                    SET status = 'ready', current_area = ?, assigned_mission_id = NULL, last_updated = ?
                    WHERE drone_id = ? AND assigned_mission_id = ?
                    """,
                    (self.home_area, now, mission["drone_id"], mission["mission_id"]),
                )
                mission["status"] = outcome
            return ended

    def recall_drone(self, identifier: str | None = None, now_iso: str | None = None) -> dict:
        """Abort exactly one active mission and return that drone to home_area."""

        now = self._now(now_iso)
        requested = (identifier or "").strip().casefold()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._advance_missions(conn, now)
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
        """Abort every active mission and return those drones to home_area."""

        now = self._now(now_iso)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._advance_missions(conn, now)
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
        """Write a drone_missions status and free the drone when the mission ends."""

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

    def admin_update_drone(self, drone_id: str, now_iso: str | None = None, **fields) -> dict:
        """Overwrite editable drone columns, aborting an active mission when the drone leaves it."""

        now = self._now(now_iso)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            # Settle elapsed missions first so an edit never "aborts" a mission that already ended.
            self._advance_missions(conn, now)
            current = conn.execute("SELECT * FROM drones WHERE drone_id = ?", (drone_id.strip(),)).fetchone()
            if current is None:
                raise SurveillancePersistenceError(f"Drone '{drone_id}' not found.")
            current = dict(current)

            new_status = fields.get("status", current["status"])
            new_mission_id = fields.get("assigned_mission_id", current["assigned_mission_id"])
            leaving_active_mission = (
                current["assigned_mission_id"] is not None
                and (new_status != "in_flight" or new_mission_id != current["assigned_mission_id"])
            )
            if leaving_active_mission:
                conn.execute(
                    "UPDATE drone_missions SET status = 'aborted', "
                    "notes = 'Admin edit: status changed away from in_flight.', updated_at = ? "
                    "WHERE mission_id = ? AND status IN ('dispatched', 'en_route', 'on_station')",
                    (now, current["assigned_mission_id"]),
                )
                fields.setdefault("assigned_mission_id", None)
                new_mission_id = fields["assigned_mission_id"]

            if new_status == "in_flight" and new_mission_id:
                mission = conn.execute(
                    "SELECT 1 FROM drone_missions WHERE mission_id = ?", (new_mission_id,)
                ).fetchone()
                if mission is None:
                    raise SurveillancePersistenceError(
                        f"assigned_mission_id {new_mission_id!r} does not reference an existing mission."
                    )

            editable_columns = ("callsign", "model", "status", "battery_percent", "current_area", "assigned_mission_id")
            updates = {column: fields[column] for column in editable_columns if column in fields}
            if updates:
                assignments = ", ".join(f"{column} = ?" for column in updates)
                conn.execute(
                    f"UPDATE drones SET {assignments}, last_updated = ? WHERE drone_id = ?",
                    (*updates.values(), now, drone_id.strip()),
                )
            updated = conn.execute("SELECT * FROM drones WHERE drone_id = ?", (drone_id.strip(),)).fetchone()
            return dict(updated)

    def surveillance_overview(self, area: str | None = None, *, now_iso: str | None = None) -> dict:
        """Return cameras, drones, and active missions plus counts for one area or all.

        One area filter applies to all three lists, so the counts always describe the same
        scope; `fleet_ready_drone_count` still tells how many drones could be sent there.
        """

        now = self._now(now_iso)
        cameras = self.list_cameras(area=area)
        drones = self.list_drones(now_iso=now)
        active_missions = self.get_active_missions(now_iso=now)
        fleet_ready_drone_count = sum(1 for d in drones if d["status"] == "ready")
        if area:
            wanted = area.strip().lower()
            active_missions = [m for m in active_missions if m["target_area"].strip().lower() == wanted]
            mission_drone_ids = {m["drone_id"] for m in active_missions}
            drones = [
                d for d in drones
                if d["current_area"].strip().lower() == wanted or d["drone_id"] in mission_drone_ids
            ]

        return {
            "area": area or "all_sectors",
            "cameras": cameras,
            "drones": drones,
            "active_missions": active_missions,
            "active_camera_count": sum(1 for c in cameras if c["status"] == "active"),
            "ready_drone_count": sum(1 for d in drones if d["status"] == "ready"),
            "in_flight_drone_count": sum(1 for d in drones if d["status"] == "in_flight"),
            "fleet_ready_drone_count": fleet_ready_drone_count,
            "as_of": now,
        }


def open_response_team_surveillance_store(
    db_path: str,
    *,
    eta_fn: Callable[[str, str], int] | None = None,
    home_area: str = "home_base",
    mission_loiter_seconds: int | None = None,
    clock: Callable[[], str] | None = None,
) -> ResponseTeamSurveillanceStore:
    """Construct the response-team surveillance store for this database path."""

    return ResponseTeamSurveillanceStore(
        db_path, eta_fn=eta_fn, home_area=home_area, mission_loiter_seconds=mission_loiter_seconds, clock=clock,
    )
