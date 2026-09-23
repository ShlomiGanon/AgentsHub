"""Scoped persistence for operational external-force dispatch requests."""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone

from persistence.operational_scope import OperationalScope, resolve_operational_scope
from persistence.schema import OPERATIONAL_DISPATCHES_TABLE_DDL


class DispatchPersistenceError(RuntimeError):
    """An operational dispatch could not be persisted or verified."""


class SQLiteOperationalDispatchStore:
    def __init__(self, db_path: str):
        self.db_path = db_path
        connection = sqlite3.connect(self.db_path)
        try:
            connection.executescript(OPERATIONAL_DISPATCHES_TABLE_DDL)
            connection.commit()
        finally:
            connection.close()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _scope_key(scope: OperationalScope | None) -> str:
        return resolve_operational_scope(scope).key

    def create_dispatch(
        self,
        *,
        force_type: str,
        quantity: int,
        target: str,
        requested_by: str,
        event_id: str | None,
        protocol_name: str | None,
        operational_profile: str | None,
        scope: OperationalScope,
        metadata: str = "",
    ) -> dict:
        if not force_type.strip() or not target.strip() or quantity < 1:
            raise DispatchPersistenceError("dispatch force_type, target, and positive quantity are required")

        dispatch_id = f"DSP-{uuid.uuid4().hex[:12].upper()}"
        now = self._now()
        row = {
            "dispatch_id": dispatch_id,
            "scope_key": self._scope_key(scope),
            "operational_profile": operational_profile or "",
            "force_type": force_type.strip(),
            "quantity": int(quantity),
            "target": target.strip(),
            "requested_by": requested_by.strip() or "system:unknown",
            "event_id": event_id,
            "protocol_name": protocol_name,
            "status": "dispatched",
            "requested_at": now,
            "updated_at": now,
            "metadata": metadata or "",
            "verification_status": "verified",
        }

        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute(
                """
                INSERT INTO operational_dispatches (
                    dispatch_id, scope_key, operational_profile, force_type, quantity,
                    target, requested_by, event_id, protocol_name, status,
                    requested_at, updated_at, metadata, verification_status
                ) VALUES (
                    :dispatch_id, :scope_key, :operational_profile, :force_type, :quantity,
                    :target, :requested_by, :event_id, :protocol_name, :status,
                    :requested_at, :updated_at, :metadata, :verification_status
                )
                """,
                row,
            )
            connection.commit()
            verified = connection.execute(
                "SELECT * FROM operational_dispatches WHERE dispatch_id = ? AND scope_key = ?",
                (dispatch_id, row["scope_key"]),
            ).fetchone()
            if verified is None or verified["status"] != "dispatched":
                raise DispatchPersistenceError("dispatch write could not be verified")
            return dict(verified)
        except sqlite3.Error as exc:
            connection.rollback()
            raise DispatchPersistenceError(f"failed to persist dispatch: {exc}") from exc
        finally:
            connection.close()

    def fetch_dispatch(self, dispatch_id: str, *, scope: OperationalScope) -> dict | None:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        try:
            row = connection.execute(
                "SELECT * FROM operational_dispatches WHERE dispatch_id = ? AND scope_key = ?",
                (dispatch_id, self._scope_key(scope)),
            ).fetchone()
            return dict(row) if row is not None else None
        finally:
            connection.close()

    def list_dispatches(self, *, scope: OperationalScope) -> list[dict]:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        try:
            rows = connection.execute(
                "SELECT * FROM operational_dispatches WHERE scope_key = ? ORDER BY requested_at, dispatch_id",
                (self._scope_key(scope),),
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            connection.close()


def open_operational_dispatch_store(db_path: str) -> SQLiteOperationalDispatchStore:
    return SQLiteOperationalDispatchStore(db_path)
