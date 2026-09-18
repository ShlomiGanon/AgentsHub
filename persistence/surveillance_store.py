"""Scoped SQLite persistence for cameras, drones, and tactical missions."""

from __future__ import annotations

import logging
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

from persistence.operational_scope import OperationalScope, resolve_operational_scope, scope_created_at
from persistence.surveillance_contracts import (
    CameraStatus,
    DroneStatus,
    MissionStatus,
    SeedReconciliationResult,
    SurveillancePersistenceError,
    SurveillancePersistenceInterface,
)


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

CREATE TABLE IF NOT EXISTS cameras (
    scope_key TEXT NOT NULL REFERENCES operational_scopes(scope_key),
    camera_id TEXT NOT NULL,
    name TEXT NOT NULL,
    area TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('active', 'offline', 'degraded')),
    azimuth_degrees INTEGER NOT NULL DEFAULT 0,
    feed_summary TEXT NOT NULL,
    last_updated TEXT NOT NULL,
    PRIMARY KEY (scope_key, camera_id)
);

CREATE TABLE IF NOT EXISTS drones (
    scope_key TEXT NOT NULL REFERENCES operational_scopes(scope_key),
    drone_id TEXT NOT NULL,
    callsign TEXT NOT NULL,
    model TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('ready', 'in_flight', 'charging', 'maintenance')),
    battery_percent INTEGER NOT NULL CHECK (battery_percent BETWEEN 0 AND 100),
    current_area TEXT NOT NULL,
    assigned_mission_id TEXT,
    last_updated TEXT NOT NULL,
    PRIMARY KEY (scope_key, drone_id),
    UNIQUE (scope_key, callsign)
);

CREATE TABLE IF NOT EXISTS drone_missions (
    scope_key TEXT NOT NULL,
    mission_id TEXT NOT NULL,
    drone_id TEXT NOT NULL,
    target_area TEXT NOT NULL,
    mission_type TEXT NOT NULL,
    incident_description TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('dispatched', 'en_route', 'on_station', 'completed', 'aborted')),
    dispatched_by TEXT NOT NULL,
    dispatched_at TEXT NOT NULL,
    eta_seconds INTEGER NOT NULL,
    notes TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL,
    PRIMARY KEY (scope_key, mission_id),
    FOREIGN KEY (scope_key, drone_id) REFERENCES drones(scope_key, drone_id)
);

CREATE INDEX IF NOT EXISTS idx_cameras_scope_area ON cameras(scope_key, area);
CREATE INDEX IF NOT EXISTS idx_drones_scope_status ON drones(scope_key, status);
CREATE INDEX IF NOT EXISTS idx_missions_scope_status ON drone_missions(scope_key, status);
"""

logger = logging.getLogger(__name__)

_SECTOR_BASE_ETA = {
    "central_hub": 45,
    "east_fence": 90,
    "north_gate": 120,
    "south_sector": 150,
    "west_hill": 210,
}

DEMO_CAMERA_SEED = (
    ("CAM-01", "Gate North Optical PTZ", "north_gate", "active", 15, "Clear view of North Perimeter Gate and approach road. Gate closed, perimeter fence secure. No suspicious activity detected."),
    ("CAM-02", "South Sector Long-Range Thermal", "south_sector", "active", 180, "Thermal sweep active across southern tree line. Stationary agricultural vehicles identified with low heat signatures; normal operational picture."),
    ("CAM-03", "East Fence Line Starlight", "east_fence", "active", 90, "Optimal visibility along eastern security fence sensor line. Zero breach or perimeter vibration alerts reported."),
    ("CAM-04", "Central Compound Dome", "central_hub", "active", 270, "Wide-angle surveillance of HQ depot and vehicle parking zone. Logistics vehicles parked, regular security personnel patrols visible."),
    ("CAM-05", "West Hill High Overlook", "west_hill", "active", 285, "Panoramic overlook of western wadi and approach trail. Visibility excellent (8km). No unauthorized movements detected."),
    ("CAM-08", "CAM-08", "south_sector", "active", 0, "Fixture reference: south sector."),
)

DEMO_DRONE_SEED = (
    ("DRONE-01", "Eagle-1", "Matrice 350 RTK", "ready", 96, "central_hub"),
    ("DRONE-02", "Falcon-2", "Skydio X2D Autonomous", "ready", 84, "north_gate"),
    ("DRONE-03", "Hawk-3", "Mavic 3 Thermal Tac", "charging", 42, "central_hub"),
)

_DRONE_STATUS_SYNONYMS = {
    "ready": "ready", "available": "ready", "standby": "ready", "idle": "ready", "free": "ready",
    "in_flight": "in_flight", "in flight": "in_flight", "flight": "in_flight", "flying": "in_flight",
    "active": "in_flight", "on_mission": "in_flight", "on mission": "in_flight", "dispatched": "in_flight",
    "charging": "charging", "maintenance": "maintenance", "offline": "maintenance",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _calculate_eta(origin_area: str, target_area: str) -> int:
    if origin_area == target_area:
        return 45
    return max(60, int((_SECTOR_BASE_ETA.get(target_area.lower(), 180) + _SECTOR_BASE_ETA.get(origin_area.lower(), 60)) / 1.5))


def _seed_cameras(value: object) -> tuple[tuple, ...]:
    if value is None:
        return DEMO_CAMERA_SEED
    result = []
    for item in value if isinstance(value, (list, tuple)) else ():
        if isinstance(item, Mapping):
            result.append((item.get("camera_id"), item.get("name"), item.get("area"), item.get("status"), item.get("azimuth_degrees", 0), item.get("feed_summary", "")))
        elif isinstance(item, (list, tuple)):
            result.append(tuple(item))
    return tuple(result)


def _seed_drones(value: object) -> tuple[tuple, ...]:
    if value is None:
        return DEMO_DRONE_SEED
    result = []
    for item in value if isinstance(value, (list, tuple)) else ():
        if isinstance(item, Mapping):
            result.append((item.get("drone_id"), item.get("callsign"), item.get("model"), item.get("status"), item.get("battery_percent", 0), item.get("current_area", "")))
        elif isinstance(item, (list, tuple)):
            result.append(tuple(item))
    return tuple(result)


class SQLiteSurveillancePersistence(SurveillancePersistenceInterface):
    def __init__(self, db_path: str, *, seed_demo_data: bool = True, seed_profile: str = ""):
        self.db_path = str(db_path)
        self.seed_demo_data = seed_demo_data
        self.seed_profile = seed_profile or "unspecified"
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute("PRAGMA foreign_keys = OFF")
            self._migrate_legacy_tables(conn)
            conn.executescript(_SCHEMA)
            conn.execute("PRAGMA foreign_keys = ON")
            self.ensure_scope(OperationalScope.live(), connection=conn)
        if self.seed_demo_data:
            # Reconcile missing canonical seed rows on an existing LIVE DB;
            # never overwrite production observations or delete state.
            self.reconcile_camera_seed(scope=OperationalScope.live())
            self._reconcile_live_drones()
        self._log_reconciliation(SeedReconciliationResult(0, 0, 0, 0))

    def _reconcile_live_drones(self) -> None:
        now = _utc_now()
        with self._connect() as conn:
            for record in DEMO_DRONE_SEED:
                conn.execute(
                    "INSERT OR IGNORE INTO drones(scope_key,drone_id,callsign,model,status,battery_percent,current_area,assigned_mission_id,last_updated) VALUES (?,?,?,?,?,?,?,?,?)",
                    ("LIVE", *record, None, now),
                )

    def _migrate_legacy_tables(self, conn: sqlite3.Connection) -> None:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "operational_scopes" not in tables:
            conn.execute("CREATE TABLE IF NOT EXISTS operational_scopes (scope_key TEXT PRIMARY KEY, scope_kind TEXT NOT NULL, scenario_id TEXT, scenario_run_id TEXT, created_at TEXT NOT NULL, UNIQUE(scenario_id, scenario_run_id))")
        conn.execute("INSERT OR IGNORE INTO operational_scopes VALUES ('LIVE','LIVE',NULL,NULL,?)", (scope_created_at(),))
        for table, columns, create_sql, insert_sql in (
            ("cameras", "camera_id,name,area,status,azimuth_degrees,feed_summary,last_updated", "CREATE TABLE cameras_new (scope_key TEXT NOT NULL, camera_id TEXT NOT NULL, name TEXT NOT NULL, area TEXT NOT NULL, status TEXT NOT NULL, azimuth_degrees INTEGER NOT NULL, feed_summary TEXT NOT NULL, last_updated TEXT NOT NULL, PRIMARY KEY(scope_key,camera_id))", "INSERT INTO cameras_new SELECT 'LIVE', camera_id,name,area,status,azimuth_degrees,feed_summary,last_updated FROM cameras"),
            ("drones", "drone_id,callsign,model,status,battery_percent,current_area,assigned_mission_id,last_updated", "CREATE TABLE drones_new (scope_key TEXT NOT NULL, drone_id TEXT NOT NULL, callsign TEXT NOT NULL, model TEXT NOT NULL, status TEXT NOT NULL, battery_percent INTEGER NOT NULL, current_area TEXT NOT NULL, assigned_mission_id TEXT, last_updated TEXT NOT NULL, PRIMARY KEY(scope_key,drone_id), UNIQUE(scope_key,callsign))", "INSERT INTO drones_new SELECT 'LIVE', drone_id,callsign,model,status,battery_percent,current_area,assigned_mission_id,last_updated FROM drones"),
        ):
            if table not in tables:
                continue
            existing_columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            if "scope_key" in existing_columns:
                continue
            conn.execute(f"ALTER TABLE {table} RENAME TO {table}_legacy")
            conn.execute(create_sql)
            conn.execute(insert_sql.replace(table, f"{table}_legacy"))
            conn.execute(f"DROP TABLE {table}_legacy")
            conn.execute(f"ALTER TABLE {table}_new RENAME TO {table}")
        if "drone_missions" in tables:
            existing_columns = {row[1] for row in conn.execute("PRAGMA table_info(drone_missions)")}
            if "scope_key" not in existing_columns:
                conn.execute("ALTER TABLE drone_missions RENAME TO drone_missions_legacy")
                conn.execute("CREATE TABLE drone_missions_new (scope_key TEXT NOT NULL, mission_id TEXT NOT NULL, drone_id TEXT NOT NULL, target_area TEXT NOT NULL, mission_type TEXT NOT NULL, incident_description TEXT NOT NULL, status TEXT NOT NULL, dispatched_by TEXT NOT NULL, dispatched_at TEXT NOT NULL, eta_seconds INTEGER NOT NULL, notes TEXT NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY(scope_key,mission_id))")
                conn.execute("INSERT INTO drone_missions_new SELECT 'LIVE', mission_id,drone_id,target_area,mission_type,incident_description,status,dispatched_by,dispatched_at,eta_seconds,notes,updated_at FROM drone_missions_legacy")
                conn.execute("DROP TABLE drone_missions_legacy")
                conn.execute("ALTER TABLE drone_missions_new RENAME TO drone_missions")

    def _scope_key(self, scope: OperationalScope | None) -> str:
        return resolve_operational_scope(scope).key

    def ensure_scope(self, scope: OperationalScope, baseline: dict | None = None, *, connection=None) -> None:
        own_connection = connection is None
        conn = connection or self._connect()
        try:
            row = conn.execute("SELECT 1 FROM operational_scopes WHERE scope_key = ?", (scope.key,)).fetchone()
            if row is None:
                conn.execute("INSERT INTO operational_scopes VALUES (?, ?, ?, ?, ?)", (scope.key, scope.kind, scope.scenario_id, scope.scenario_run_id, scope_created_at()))
                if self.seed_demo_data:
                    self._seed_scope(conn, scope.key, baseline or {})
            if own_connection:
                conn.commit()
        finally:
            if own_connection:
                conn.close()

    def _require_scope(self, scope: OperationalScope | None) -> str:
        key = self._scope_key(scope)
        with self._connect() as conn:
            if conn.execute("SELECT 1 FROM operational_scopes WHERE scope_key = ?", (key,)).fetchone() is None:
                raise SurveillancePersistenceError("operational scope has not been initialized")
        return key

    def _seed_scope(self, conn: sqlite3.Connection, scope_key: str, baseline: dict) -> None:
        surveillance = baseline.get("surveillance", {}) if isinstance(baseline, Mapping) else {}
        now = _utc_now()
        cameras = _seed_cameras(surveillance.get("cameras") if isinstance(surveillance, Mapping) else None)
        drones = _seed_drones(surveillance.get("drones") if isinstance(surveillance, Mapping) else None)
        conn.executemany("INSERT INTO cameras(scope_key,camera_id,name,area,status,azimuth_degrees,feed_summary,last_updated) VALUES (?,?,?,?,?,?,?,?)", [(scope_key, *item, now) for item in cameras if len(item) == 6])
        conn.executemany("INSERT INTO drones(scope_key,drone_id,callsign,model,status,battery_percent,current_area,assigned_mission_id,last_updated) VALUES (?,?,?,?,?,?,?,?,?)", [(scope_key, *item, None, now) for item in drones if len(item) == 6])

    def reconcile_camera_seed(self, seed=None, *, scope=None) -> SeedReconciliationResult:
        if not self.seed_demo_data:
            return SeedReconciliationResult(0, 0, 0, 1)
        resolved = resolve_operational_scope(scope)
        self.ensure_scope(resolved)
        with self._connect() as conn:
            examined = inserted = preserved = skipped = 0
            errors = []
            now = _utc_now()
            for record in seed or DEMO_CAMERA_SEED:
                examined += 1
                if not isinstance(record, (tuple, list)) or len(record) != 6 or not isinstance(record[0], str) or not record[0].strip():
                    skipped += 1
                    errors.append(f"invalid camera seed record at index {examined - 1}")
                    continue
                camera_id = record[0].strip()
                if conn.execute("SELECT 1 FROM cameras WHERE scope_key=? AND camera_id=?", (resolved.key, camera_id)).fetchone() is not None:
                    preserved += 1
                    continue
                conn.execute("INSERT INTO cameras(scope_key,camera_id,name,area,status,azimuth_degrees,feed_summary,last_updated) VALUES (?,?,?,?,?,?,?,?)", (resolved.key, *record, now))
                inserted += 1
        result = SeedReconciliationResult(examined, inserted, preserved, skipped, tuple(errors))
        self._log_reconciliation(result)
        return result

    def _log_reconciliation(self, result: SeedReconciliationResult) -> None:
        logger.info("surveillance seed reconciliation complete", extra={"event": "surveillance_seed_reconciliation", "profile": self.seed_profile, "domain": "surveillance", "examined": result.examined, "inserted": result.inserted, "preserved": result.preserved, "skipped": result.skipped, "errors": len(result.errors)})

    def clear_runtime_state(self, *, scope=None) -> dict[str, int]:
        resolved = resolve_operational_scope(scope)
        scope_key = self._require_scope(resolved)
        now = _utc_now()
        with self._connect() as conn:
            missions = int(conn.execute("SELECT COUNT(*) FROM drone_missions WHERE scope_key=?", (scope_key,)).fetchone()[0])
            conn.execute("DELETE FROM drone_missions WHERE scope_key=?", (scope_key,))
            for record in DEMO_CAMERA_SEED:
                conn.execute("UPDATE cameras SET status=?,azimuth_degrees=?,feed_summary=?,last_updated=? WHERE scope_key=? AND camera_id=?", (*record[3:6], now, scope_key, record[0]))
            for record in DEMO_DRONE_SEED:
                conn.execute("UPDATE drones SET status=?,battery_percent=?,current_area=?,assigned_mission_id=NULL,last_updated=? WHERE scope_key=? AND drone_id=?", (record[3], record[4], record[5], now, scope_key, record[0]))
        return {"drone_missions": missions}

    def list_cameras(self, area=None, status=None, *, scope=None):
        key = self._require_scope(scope)
        query = "SELECT * FROM cameras WHERE scope_key=?"
        params = [key]
        if area:
            query += " AND area=?"; params.append(area.strip().lower())
        if status:
            query += " AND status=?"; params.append(status.strip().lower())
        query += " ORDER BY camera_id"
        with self._connect() as conn:
            return [dict(row) for row in conn.execute(query, params).fetchall()]

    def get_camera(self, camera_id, *, scope=None):
        key = self._require_scope(scope)
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM cameras WHERE scope_key=? AND camera_id=?", (key, camera_id.strip())).fetchone()
            return dict(row) if row is not None else None

    def update_camera_feed(self, camera_id, feed_summary, status=None, updated_at=None, *, scope=None):
        key = self._require_scope(scope)
        now = updated_at or _utc_now()
        with self._connect() as conn:
            camera = conn.execute("SELECT * FROM cameras WHERE scope_key=? AND camera_id=?", (key, camera_id.strip())).fetchone()
            if camera is None:
                raise SurveillancePersistenceError(f"Camera '{camera_id}' not found.")
            new_status = status.strip().lower() if status else camera["status"]
            conn.execute("UPDATE cameras SET feed_summary=?,status=?,last_updated=? WHERE scope_key=? AND camera_id=?", (feed_summary.strip(), new_status, now, key, camera_id.strip()))
            return dict(conn.execute("SELECT * FROM cameras WHERE scope_key=? AND camera_id=?", (key, camera_id.strip())).fetchone())

    def list_drones(self, status=None, *, scope=None):
        key = self._require_scope(scope)
        query = "SELECT * FROM drones WHERE scope_key=?"; params = [key]
        if status:
            cleaned = status.strip().lower()
            if cleaned not in {"all", "*"}:
                query += " AND status=?"; params.append(_DRONE_STATUS_SYNONYMS.get(cleaned, cleaned))
        query += " ORDER BY callsign"
        with self._connect() as conn:
            return [dict(row) for row in conn.execute(query, params).fetchall()]

    def get_drone(self, drone_id, *, scope=None):
        key = self._require_scope(scope)
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM drones WHERE scope_key=? AND drone_id=?", (key, drone_id.strip())).fetchone()
            return dict(row) if row is not None else None

    def dispatch_drone(self, *, target_area, incident_description, mission_type="recon", dispatched_by="commander", specific_drone_id=None, now_iso=None, scope=None):
        key = self._require_scope(scope); now = now_iso or _utc_now()
        with self._connect() as conn:
            if specific_drone_id:
                drone = conn.execute("SELECT * FROM drones WHERE scope_key=? AND drone_id=? AND status='ready'", (key, specific_drone_id.strip())).fetchone()
                if drone is None: raise SurveillancePersistenceError(f"Requested drone '{specific_drone_id}' is not currently available for dispatch.")
            else:
                drones = conn.execute("SELECT * FROM drones WHERE scope_key=? AND status='ready' ORDER BY battery_percent DESC", (key,)).fetchall()
                if not drones: raise SurveillancePersistenceError("No ready drones available in fleet for immediate dispatch.")
                same_area = [d for d in drones if d["current_area"].lower() == target_area.lower()]
                drone = same_area[0] if same_area else drones[0]
            mission_id = f"MSN-{uuid.uuid4().hex[:8].upper()}"; eta = _calculate_eta(drone["current_area"], target_area)
            conn.execute("INSERT INTO drone_missions(scope_key,mission_id,drone_id,target_area,mission_type,incident_description,status,dispatched_by,dispatched_at,eta_seconds,notes,updated_at) VALUES (?,?,?,?,?,?, 'dispatched',?,?,?,'',?)", (key, mission_id, drone["drone_id"], target_area.strip(), mission_type.strip(), incident_description.strip(), dispatched_by.strip(), now, eta, now))
            conn.execute("UPDATE drones SET status='in_flight',current_area=?,assigned_mission_id=?,last_updated=? WHERE scope_key=? AND drone_id=?", (target_area.strip(), mission_id, now, key, drone["drone_id"]))
            mission = dict(conn.execute("SELECT * FROM drone_missions WHERE scope_key=? AND mission_id=?", (key, mission_id)).fetchone())
            mission["drone"] = dict(conn.execute("SELECT * FROM drones WHERE scope_key=? AND drone_id=?", (key, drone["drone_id"])).fetchone())
            return mission

    def get_active_missions(self, *, scope=None):
        key = self._require_scope(scope)
        with self._connect() as conn:
            rows = conn.execute("SELECT m.*,d.callsign,d.model,d.battery_percent FROM drone_missions m JOIN drones d ON m.scope_key=d.scope_key AND m.drone_id=d.drone_id WHERE m.scope_key=? AND m.status IN ('dispatched','en_route','on_station') ORDER BY m.dispatched_at DESC", (key,)).fetchall()
            return [dict(row) for row in rows]

    def recall_drone(self, identifier=None, now_iso=None, *, scope=None):
        key = self._require_scope(scope); now = now_iso or _utc_now(); requested = (identifier or "").strip().casefold()
        with self._connect() as conn:
            rows = [dict(row) for row in conn.execute("SELECT m.*,d.callsign,d.model,d.battery_percent FROM drone_missions m JOIN drones d ON m.scope_key=d.scope_key AND m.drone_id=d.drone_id WHERE m.scope_key=? AND m.status IN ('dispatched','en_route','on_station') ORDER BY d.callsign,m.dispatched_at", (key,)).fetchall()]
            if not rows: return {"status": "no_active", "missions": []}
            matches = [m for m in rows if requested in {m["mission_id"].casefold(), m["drone_id"].casefold(), m["callsign"].casefold()}] if requested else rows
            if requested and len(matches) != 1: return {"status": "not_found", "requested": identifier, "missions": rows}
            if not requested and len(rows) != 1: return {"status": "selection_required", "missions": rows}
            selected = matches[0]
            conn.execute("UPDATE drone_missions SET status='aborted',notes=?,updated_at=? WHERE scope_key=? AND mission_id=?", ("Operator recall: returned to central_hub.", now, key, selected["mission_id"]))
            conn.execute("UPDATE drones SET status='ready',current_area='central_hub',assigned_mission_id=NULL,last_updated=? WHERE scope_key=? AND drone_id=?", (now, key, selected["drone_id"]))
            return {"status": "returned", "mission": dict(conn.execute("SELECT * FROM drone_missions WHERE scope_key=? AND mission_id=?", (key, selected["mission_id"])).fetchone()), "drone": dict(conn.execute("SELECT * FROM drones WHERE scope_key=? AND drone_id=?", (key, selected["drone_id"])).fetchone())}

    def recall_all_drones(self, now_iso=None, *, scope=None):
        key = self._require_scope(scope); now = now_iso or _utc_now()
        with self._connect() as conn:
            missions = [dict(row) for row in conn.execute("SELECT m.*,d.callsign,d.model,d.battery_percent FROM drone_missions m JOIN drones d ON m.scope_key=d.scope_key AND m.drone_id=d.drone_id WHERE m.scope_key=? AND m.status IN ('dispatched','en_route','on_station')", (key,)).fetchall()]
            for mission in missions:
                conn.execute("UPDATE drone_missions SET status='aborted',notes=?,updated_at=? WHERE scope_key=? AND mission_id=?", ("Operator recall: returned to central_hub.", now, key, mission["mission_id"]))
                conn.execute("UPDATE drones SET status='ready',current_area='central_hub',assigned_mission_id=NULL,last_updated=? WHERE scope_key=? AND drone_id=?", (now, key, mission["drone_id"]))
            return {"status": "returned_all" if missions else "no_active", "missions": missions}

    def update_mission_status(self, mission_id, status: MissionStatus, notes=None, updated_at=None, *, scope=None):
        key = self._require_scope(scope); now = updated_at or _utc_now()
        with self._connect() as conn:
            mission = conn.execute("SELECT * FROM drone_missions WHERE scope_key=? AND mission_id=?", (key, mission_id)).fetchone()
            if mission is None: raise SurveillancePersistenceError(f"Mission '{mission_id}' not found.")
            conn.execute("UPDATE drone_missions SET status=?,notes=COALESCE(?,notes),updated_at=? WHERE scope_key=? AND mission_id=?", (status, notes, now, key, mission_id))
            if status in ("completed", "aborted"):
                conn.execute("UPDATE drones SET status='ready',assigned_mission_id=NULL,last_updated=? WHERE scope_key=? AND drone_id=?", (now, key, mission["drone_id"]))
            return dict(conn.execute("SELECT * FROM drone_missions WHERE scope_key=? AND mission_id=?", (key, mission_id)).fetchone())

    def surveillance_overview(self, area=None, *, scope=None):
        cameras = self.list_cameras(area=area, scope=scope); drones = self.list_drones(scope=scope); missions = self.get_active_missions(scope=scope)
        if area: missions = [m for m in missions if m["target_area"].lower() == area.lower()]
        return {"area": area or "all_sectors", "cameras": cameras, "drones": drones, "active_missions": missions, "active_camera_count": sum(c["status"] == "active" for c in cameras), "ready_drone_count": sum(d["status"] == "ready" for d in drones), "in_flight_drone_count": sum(d["status"] == "in_flight" for d in drones), "as_of": _utc_now()}
