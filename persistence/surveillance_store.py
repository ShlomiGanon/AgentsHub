"""Dedicated SQLite persistence for cameras, drones, and tactical surveillance missions."""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from persistence.surveillance_contracts import (
    CameraStatus,
    DroneStatus,
    MissionStatus,
    SurveillancePersistenceError,
    SurveillancePersistenceInterface,
)

_SCHEMA = """
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

CREATE INDEX IF NOT EXISTS idx_cameras_area ON cameras(area);
CREATE INDEX IF NOT EXISTS idx_drones_status ON drones(status);
CREATE INDEX IF NOT EXISTS idx_missions_status ON drone_missions(status);
"""

# Realistic travel times (seconds) between sectors for demo simulation
_SECTOR_BASE_ETA = {
    "central_hub": 45,
    "east_fence": 90,
    "north_gate": 120,
    "south_sector": 150,
    "west_hill": 210,
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _calculate_eta(origin_area: str, target_area: str) -> int:
    if origin_area == target_area:
        return 45
    target_eta = _SECTOR_BASE_ETA.get(target_area.lower(), 180)
    origin_eta = _SECTOR_BASE_ETA.get(origin_area.lower(), 60)
    return max(60, int((target_eta + origin_eta) / 1.5))


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


class SQLiteSurveillancePersistence(SurveillancePersistenceInterface):
    def __init__(self, db_path: str):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
            self._seed_demo_data_if_empty(conn)

    def _seed_demo_data_if_empty(self, conn: sqlite3.Connection) -> None:
        cursor = conn.execute("SELECT COUNT(*) FROM cameras")
        if cursor.fetchone()[0] == 0:
            now = _utc_now()
            cameras_seed = [
                ("CAM-01", "Gate North Optical PTZ", "north_gate", "active", 15, "Clear view of North Perimeter Gate and approach road. Gate closed, perimeter fence secure. No suspicious activity detected.", now),
                ("CAM-02", "South Sector Long-Range Thermal", "south_sector", "active", 180, "Thermal sweep active across southern tree line. Stationary agricultural vehicles identified with low heat signatures; normal operational picture.", now),
                ("CAM-03", "East Fence Line Starlight", "east_fence", "active", 90, "Optimal visibility along eastern security fence sensor line. Zero breach or perimeter vibration alerts reported.", now),
                ("CAM-04", "Central Compound Dome", "central_hub", "active", 270, "Wide-angle surveillance of HQ depot and vehicle parking zone. Logistics vehicles parked, regular security personnel patrols visible.", now),
                ("CAM-05", "West Hill High Overlook", "west_hill", "active", 285, "Panoramic overlook of western wadi and approach trail. Visibility excellent (8km). No unauthorized movements detected.", now),
            ]
            conn.executemany(
                """
                INSERT INTO cameras (camera_id, name, area, status, azimuth_degrees, feed_summary, last_updated)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                cameras_seed,
            )

        drone_cursor = conn.execute("SELECT COUNT(*) FROM drones")
        if drone_cursor.fetchone()[0] == 0:
            now = _utc_now()
            drones_seed = [
                ("DRONE-01", "Eagle-1", "Matrice 350 RTK", "ready", 96, "central_hub", None, now),
                ("DRONE-02", "Falcon-2", "Skydio X2D Autonomous", "ready", 84, "north_gate", None, now),
                ("DRONE-03", "Hawk-3", "Mavic 3 Thermal Tac", "charging", 42, "central_hub", None, now),
            ]
            conn.executemany(
                """
                INSERT INTO drones (drone_id, callsign, model, status, battery_percent, current_area, assigned_mission_id, last_updated)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                drones_seed,
            )

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
                # Pick the ready drone with highest battery, prioritizing any already stationed at target area
                drones = conn.execute(
                    "SELECT * FROM drones WHERE status = 'ready' ORDER BY battery_percent DESC"
                ).fetchall()
                if not drones:
                    raise SurveillancePersistenceError("No ready drones available in fleet for immediate dispatch.")
                # If one is already at the target area, prioritize it
                same_area = [d for d in drones if d["current_area"].lower() == target_area.lower()]
                drone = same_area[0] if same_area else drones[0]

            mission_id = f"MSN-{uuid.uuid4().hex[:8].upper()}"
            eta_seconds = _calculate_eta(drone["current_area"], target_area)

            # Insert mission
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

            # Update drone state: in_flight, target area, assigned mission
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

    def update_mission_status(
        self,
        mission_id: str,
        status: MissionStatus,
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

            # If completed or aborted, return drone to ready status and clear assigned mission
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
