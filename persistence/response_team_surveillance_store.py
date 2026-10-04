"""Camera, drone, and mission tables for the response-team profile database."""

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path
from typing import Callable

from persistence.response_team_support import _utc_now
from persistence.surveillance_contracts import (
    SurveillancePersistenceError,
    SurveillancePersistenceInterface,
)

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
    """Surveillance contract without a demo seed; the profile supplies cameras, drones, and ETA."""

    def __init__(
        self,
        db_path: str,
        *,
        eta_fn: Callable[[str, str], int] | None = None,
        home_area: str = "home_base",
    ):
        """Open the DB file and create camera/drone/mission tables if they are missing."""

        self.db_path = str(db_path)
        self._eta_fn = eta_fn
        self.home_area = home_area
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SURVEILLANCE_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        """Open a row-factory connection with foreign keys enabled."""

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        return conn

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
        now_iso: str | None = None,
    ) -> bool:
        """Insert a cameras row only when camera_id is new; True if inserted."""

        now = now_iso or _utc_now()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO cameras (camera_id, name, area, status, azimuth_degrees, feed_summary, last_updated)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (camera_id, name, area, status, azimuth_degrees, feed_summary, now),
            )
            inserted = cursor.rowcount > 0
            conn.execute("UPDATE cameras SET name = ? WHERE camera_id = ?", (name, camera_id))
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

    def list_drones(self, status: str | None = None) -> list[dict]:
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
            rows = conn.execute(query, params).fetchall()
            return [dict(row) for row in rows]

    def get_drone(self, drone_id: str) -> dict | None:
        """Return one drones row, or None."""

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
        """Create a drone_missions row and mark the chosen ready drone in_flight."""

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
        """Return in-progress drone_missions rows joined with drone details."""

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
        """Abort exactly one active mission and return that drone to home_area."""

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
        """Abort every active mission and return those drones to home_area."""

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

        now = now_iso or _utc_now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
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

    def surveillance_overview(self, area: str | None = None) -> dict:
        """Return cameras, drones, and active missions plus counts for one area or all."""

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
    """Construct the response-team surveillance store for this database path."""

    return ResponseTeamSurveillanceStore(db_path, eta_fn=eta_fn, home_area=home_area)
