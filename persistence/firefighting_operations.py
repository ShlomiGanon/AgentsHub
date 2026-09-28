"""Profile-owned structured operational state for the FIRE simulation."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


INCIDENT_ID = "EVT-FIRE-444-BRUSH"

_SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS incident_state (
    incident_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    area TEXT NOT NULL,
    spread_status TEXT NOT NULL,
    hazard_status TEXT NOT NULL,
    last_summary TEXT NOT NULL,
    last_updated TEXT NOT NULL,
    run_started_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS incident_updates (
    update_id TEXT PRIMARY KEY,
    source_message_id TEXT NOT NULL UNIQUE,
    event_id TEXT,
    incident_id TEXT NOT NULL,
    update_kind TEXT NOT NULL,
    verification_status TEXT NOT NULL,
    summary TEXT NOT NULL,
    facts_json TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    received_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS external_force_state (
    force_id TEXT PRIMARY KEY,
    force_kind TEXT NOT NULL,
    count INTEGER,
    status TEXT NOT NULL,
    location TEXT NOT NULL,
    notes TEXT NOT NULL,
    last_updated TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_fire_updates_incident_time
    ON incident_updates(incident_id, occurred_at);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _scenario_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo("Asia/Jerusalem"))
    return parsed.astimezone(timezone.utc)


def _receipt_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


class FirefightingOperationsStore:
    """Connection-per-operation store with source-message idempotency."""

    def __init__(self, db_path: str):
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as connection:
            connection.executescript(_SCHEMA)
            columns = {row[1] for row in connection.execute("PRAGMA table_info(incident_state)").fetchall()}
            if "run_started_at" not in columns:
                connection.execute("ALTER TABLE incident_state ADD COLUMN run_started_at TEXT")
                connection.execute("UPDATE incident_state SET run_started_at = last_updated WHERE run_started_at IS NULL")
            force_columns = {row[1]: row for row in connection.execute("PRAGMA table_info(external_force_state)")}
            if force_columns.get("count", (None, None, None, 0))[3]:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    "CREATE TABLE external_force_state_new ("
                    "force_id TEXT PRIMARY KEY, force_kind TEXT NOT NULL, count INTEGER, "
                    "status TEXT NOT NULL, location TEXT NOT NULL, notes TEXT NOT NULL, last_updated TEXT NOT NULL)"
                )
                connection.execute(
                    "INSERT INTO external_force_state_new SELECT force_id, force_kind, count, status, location, notes, last_updated FROM external_force_state"
                )
                connection.execute("DROP TABLE external_force_state")
                connection.execute("ALTER TABLE external_force_state_new RENAME TO external_force_state")
            connection.commit()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    def ensure_initial_state(self, *, now: str | None = None) -> None:
        timestamp = now or _now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO incident_state
                    (incident_id, status, area, spread_status, hazard_status, last_summary, last_updated, run_started_at)
                VALUES (?, 'unknown', 'unknown', 'unknown', 'unknown', ?, ?, ?)
                ON CONFLICT(incident_id) DO NOTHING
                """,
                (INCIDENT_ID, "Initial FIRE simulation incident state.", timestamp, timestamp),
            )

    def reset_current_state(self, *, now: str | None = None, run_started_at: str | None = None) -> None:
        """Start a clean demo state while retaining the append-only update log."""

        timestamp = now or _now()
        with self._connect() as connection:
            connection.execute("DELETE FROM incident_state")
            connection.execute("DELETE FROM external_force_state")
        # ``now`` is the scenario/event timestamp shown to the commander;
        # ``run_started_at`` is the receipt boundary used to isolate this
        # execution from earlier executions with the same scenario timestamps.
        self.ensure_initial_state(now=timestamp)
        if run_started_at:
            with self._connect() as connection:
                connection.execute(
                    "UPDATE incident_state SET run_started_at = ? WHERE incident_id = ?",
                    (run_started_at, INCIDENT_ID),
                )

    def record_incident_update(
        self, *, source_message_id: str, event_id: str, update_kind: str, summary: str,
        verification_status: str = "reported", facts: dict | None = None,
        incident_id: str = INCIDENT_ID, occurred_at: str = "", received_at: str = "",
        status: str | None = None, area: str | None = None,
        spread_status: str | None = None, hazard_status: str | None = None,
        external_force: dict | None = None,
    ) -> dict:
        self.ensure_initial_state(now=received_at or None)
        occurred = occurred_at or received_at or _now()
        received = received_at or occurred
        scoped_source_id = f"{event_id}:{source_message_id}" if event_id else source_message_id
        update_id = f"fire-update-{scoped_source_id}"
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT * FROM incident_updates WHERE source_message_id = ?", (scoped_source_id,)
            ).fetchone()
            if existing is not None:
                return {"inserted": False, "update": dict(existing)}
            connection.execute(
                """
                INSERT INTO incident_updates
                    (update_id, source_message_id, event_id, incident_id, update_kind,
                     verification_status, summary, facts_json, occurred_at, received_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (update_id, scoped_source_id, event_id, incident_id, update_kind,
                 verification_status, summary.strip(), json.dumps(facts or {}, ensure_ascii=False, sort_keys=True),
                 occurred, received),
            )
            if update_kind != "fire_incident":
                current_state_applied = True
                if external_force is not None:
                    count = external_force.get("count")
                    if count is not None and (type(count) is not int or count < 0):
                        raise ValueError("external-force count must be a non-negative integer or unknown")
                    current_force = connection.execute(
                        "SELECT last_updated FROM external_force_state WHERE force_id = ?",
                        (external_force["force_id"],),
                    ).fetchone()
                    current_state_applied = (
                        current_force is None
                        or _scenario_time(occurred) >= _scenario_time(current_force["last_updated"])
                    )
                    if current_state_applied:
                        connection.execute(
                            """
                            INSERT INTO external_force_state
                                (force_id, force_kind, count, status, location, notes, last_updated)
                            VALUES (?, ?, ?, ?, ?, ?, ?)
                            ON CONFLICT(force_id) DO UPDATE SET
                                force_kind = excluded.force_kind, count = excluded.count,
                                status = excluded.status, location = excluded.location,
                                notes = excluded.notes, last_updated = excluded.last_updated
                            """,
                            (
                                external_force["force_id"], external_force["force_kind"], count,
                                external_force["status"], external_force["location"],
                                external_force["notes"], occurred,
                            ),
                        )
                row = connection.execute(
                    "SELECT * FROM incident_updates WHERE update_id = ?", (update_id,)
                ).fetchone()
                return {"inserted": True, "update": dict(row), "current_state_applied": current_state_applied}
            current = connection.execute(
                "SELECT * FROM incident_state WHERE incident_id = ?", (incident_id,)
            ).fetchone()
            if current is not None and _scenario_time(occurred) < _scenario_time(current["last_updated"]):
                row = connection.execute(
                    "SELECT * FROM incident_updates WHERE update_id = ?", (update_id,)
                ).fetchone()
                return {"inserted": True, "update": dict(row), "current_state_applied": False}
            if current is None:
                root = connection.execute(
                    "SELECT run_started_at FROM incident_state WHERE incident_id = ?", (INCIDENT_ID,)
                ).fetchone()
                run_started = root["run_started_at"] if root is not None else received
                connection.execute(
                    """
                    INSERT INTO incident_state
                        (incident_id, status, area, spread_status, hazard_status,
                         last_summary, last_updated, run_started_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (incident_id, status or "open", area or "unknown", spread_status or "unknown",
                     hazard_status or "unknown", summary.strip(), occurred, run_started),
                )
                current = connection.execute(
                    "SELECT * FROM incident_state WHERE incident_id = ?", (incident_id,)
                ).fetchone()
            next_status = status or current["status"]
            next_area = area or current["area"]
            next_spread = spread_status or current["spread_status"]
            next_hazard = hazard_status or current["hazard_status"]
            connection.execute(
                """
                UPDATE incident_state
                SET status = ?, area = ?, spread_status = ?, hazard_status = ?,
                    last_summary = ?, last_updated = ?
                WHERE incident_id = ?
                """,
                (next_status, next_area, next_spread, next_hazard, summary.strip(), occurred, incident_id),
            )
            row = connection.execute(
                "SELECT * FROM incident_updates WHERE update_id = ?", (update_id,)
            ).fetchone()
            return {"inserted": True, "update": dict(row)}

    def get_incident(self, incident_id: str = INCIDENT_ID) -> dict | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM incident_state WHERE incident_id = ?", (incident_id,)
            ).fetchone()
        return dict(row) if row is not None else None

    def list_incidents(self) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM incident_state ORDER BY last_updated, incident_id"
            ).fetchall()
        return [dict(row) for row in rows]

    def list_updates(self, incident_id: str | None = None) -> list[dict]:
        with self._connect() as connection:
            if incident_id is None:
                rows = connection.execute(
                    "SELECT * FROM incident_updates ORDER BY occurred_at, update_id"
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM incident_updates WHERE incident_id = ? ORDER BY occurred_at, update_id",
                    (incident_id,),
                ).fetchall()
        return [dict(row) for row in rows]

    def list_external_forces_as_of(
        self, *, as_of_iso: str, run_started_at: str, event_ids: set[str] | None = None,
    ) -> list[dict]:
        """Rebuild the external-force view from this run's append-only reports."""

        cutoff = _scenario_time(as_of_iso)
        run_start = _receipt_time(run_started_at)
        allowed_event_ids = set(event_ids) if event_ids is not None else None
        latest: dict[str, dict] = {}
        for row in self.list_updates():
            if row["update_kind"] != "external_force":
                continue
            if _receipt_time(row["received_at"]) < run_start:
                continue
            if allowed_event_ids is not None and row["event_id"] not in allowed_event_ids:
                continue
            occurred = _scenario_time(row["occurred_at"])
            if occurred > cutoff:
                continue
            try:
                force = json.loads(row["facts_json"]).get("external_force")
            except (TypeError, ValueError):
                continue
            if not isinstance(force, dict) or not force.get("force_id"):
                continue
            previous = latest.get(force["force_id"])
            if previous is None or occurred >= _scenario_time(previous["last_updated"]):
                latest[force["force_id"]] = {**force, "last_updated": row["occurred_at"]}
        return [latest[force_id] for force_id in sorted(latest)]

    def list_external_forces(self) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM external_force_state ORDER BY force_id").fetchall()
        return [dict(row) for row in rows]
