"""Durable, explicit Telegram-to-simulation-run bindings."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from persistence.operational_scope import OperationalScope


class TelegramSimulationBindingError(RuntimeError):
    """A trusted Telegram simulation binding could not be written or read."""


class SQLiteTelegramSimulationBindingStore:
    def __init__(self, db_path: str):
        self.db_path = str(db_path)
        with sqlite3.connect(self.db_path) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS simulation_runs (
                    scenario_id TEXT NOT NULL,
                    scenario_run_id TEXT NOT NULL,
                    scope_key TEXT NOT NULL UNIQUE,
                    operational_profile TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('active', 'closed')),
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (scenario_id, scenario_run_id)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS telegram_simulation_bindings (
                    telegram_identity TEXT PRIMARY KEY,
                    scenario_id TEXT NOT NULL,
                    scenario_run_id TEXT NOT NULL,
                    scope_key TEXT NOT NULL,
                    bound_by TEXT NOT NULL,
                    bound_at TEXT NOT NULL
                )
                """
            )

    def register_run(self, *, scenario_id: str, scenario_run_id: str, operational_profile: str) -> dict:
        scenario = str(scenario_id).strip()
        run_id = str(scenario_run_id).strip()
        profile = str(operational_profile).strip()
        if not scenario or not run_id or not profile:
            raise TelegramSimulationBindingError("scenario, exact run, and operational profile are required")
        scope = OperationalScope.simulation(scenario, run_id)
        with sqlite3.connect(self.db_path) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute(
                """
                INSERT INTO simulation_runs
                    (scenario_id, scenario_run_id, scope_key, operational_profile, status, created_at)
                VALUES (?, ?, ?, ?, 'active', ?)
                ON CONFLICT(scenario_id, scenario_run_id) DO UPDATE SET
                    operational_profile = excluded.operational_profile,
                    status = 'active'
                """,
                (scenario, run_id, scope.key, profile, self._now()),
            )
            row = connection.execute(
                "SELECT * FROM simulation_runs WHERE scenario_id = ? AND scenario_run_id = ?",
                (scenario, run_id),
            ).fetchone()
            if row is None:
                raise TelegramSimulationBindingError("simulation run write could not be verified")
            return dict(row)

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def get_binding(self, telegram_identity: str) -> dict | None:
        with sqlite3.connect(self.db_path) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                "SELECT * FROM telegram_simulation_bindings WHERE telegram_identity = ?",
                (str(telegram_identity),),
            ).fetchone()
            return dict(row) if row is not None else None

    def scope_is_provisioned(self, scenario_id: str, scenario_run_id: str) -> bool:
        scope = OperationalScope.simulation(str(scenario_id), str(scenario_run_id))
        with sqlite3.connect(self.db_path) as connection:
            try:
                return connection.execute(
                    "SELECT 1 FROM simulation_runs WHERE scenario_id = ? AND scenario_run_id = ? AND status = 'active'",
                    (str(scenario_id), str(scenario_run_id)),
                ).fetchone() is not None
            except sqlite3.OperationalError as exc:
                if "no such table" in str(exc).lower():
                    return False
                raise TelegramSimulationBindingError("could not verify the exact simulation run") from exc

    def list_bindings(self) -> list[dict]:
        with sqlite3.connect(self.db_path) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                "SELECT * FROM telegram_simulation_bindings ORDER BY telegram_identity"
            ).fetchall()
            return [dict(row) for row in rows]

    def bind(
        self,
        *,
        telegram_identity: str,
        scenario_id: str,
        scenario_run_id: str,
        bound_by: str,
    ) -> dict:
        identity = str(telegram_identity).strip()
        scenario = str(scenario_id).strip()
        run_id = str(scenario_run_id).strip()
        actor = str(bound_by).strip()
        if not identity or not scenario or not run_id or not actor:
            raise TelegramSimulationBindingError("telegram identity, exact scenario/run, and actor are required")

        scope = OperationalScope.simulation(scenario, run_id)
        with sqlite3.connect(self.db_path) as connection:
            connection.row_factory = sqlite3.Row
            try:
                scope_row = connection.execute(
                    "SELECT 1 FROM simulation_runs WHERE scenario_id = ? AND scenario_run_id = ? AND status = 'active'",
                    (scenario, run_id),
                ).fetchone()
            except sqlite3.OperationalError as exc:
                if "no such table" in str(exc).lower():
                    scope_row = None
                else:
                    raise TelegramSimulationBindingError("could not verify the exact simulation run") from exc
            if scope_row is None:
                raise TelegramSimulationBindingError("exact simulation run is not provisioned")
            connection.execute(
                """
                INSERT INTO telegram_simulation_bindings
                    (telegram_identity, scenario_id, scenario_run_id, scope_key, bound_by, bound_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(telegram_identity) DO UPDATE SET
                    scenario_id = excluded.scenario_id,
                    scenario_run_id = excluded.scenario_run_id,
                    scope_key = excluded.scope_key,
                    bound_by = excluded.bound_by,
                    bound_at = excluded.bound_at
                """,
                (identity, scenario, run_id, scope.key, actor, self._now()),
            )
            row = connection.execute(
                "SELECT * FROM telegram_simulation_bindings WHERE telegram_identity = ?",
                (identity,),
            ).fetchone()
            if row is None:
                raise TelegramSimulationBindingError("simulation binding write could not be verified")
            return dict(row)

    def unbind(self, telegram_identity: str) -> bool:
        with sqlite3.connect(self.db_path) as connection:
            cursor = connection.execute(
                "DELETE FROM telegram_simulation_bindings WHERE telegram_identity = ?",
                (str(telegram_identity).strip(),),
            )
            return cursor.rowcount > 0


def open_telegram_simulation_binding_store(db_path: str) -> SQLiteTelegramSimulationBindingStore:
    return SQLiteTelegramSimulationBindingStore(db_path)
