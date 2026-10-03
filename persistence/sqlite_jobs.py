"""Holds, notifications, log entries, and conversation rows for SQLitePersistence."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone

from persistence.contracts import NotFoundError, PersistenceError
from persistence.sqlite_support import (
    _HELD_EVENT_RESERVED_KEYS,
    _HELD_EVENT_RESOLUTION_RESERVED_KEYS,
    _decode_held_event_row,
    _insert_notification,
)


class SqliteJobsMixin:
    """Holds, notifications, logs, and conversation writes for SQLitePersistence."""
    def write_log_entry(self, trace_id: str | None, details: dict) -> None:
        """Insert one log_entries row for this trace."""

        timestamp = datetime.now(timezone.utc).isoformat()
        payload = json.dumps(details, default=str, ensure_ascii=False)

        def _do(connection: sqlite3.Connection) -> None:
            """Insert one log_entries row and wake log waiters."""

            try:
                connection.execute(
                    "INSERT INTO log_entries (trace_id, timestamp, details) VALUES (?, ?, ?)",
                    (trace_id, timestamp, payload),
                )
                connection.commit()
                self._wake_log_waiters()
            except sqlite3.Error as exc:
                connection.rollback()
                raise PersistenceError(f"failed to write log entry: {exc}") from exc

        self._submit_write(_do)

    def fetch_log_entries(self, trace_id: str) -> list[dict]:
        """All log_entries rows for this trace, oldest first."""

        return self.fetch_log_entries_since(trace_id, 0)

    def fetch_log_entries_since(self, trace_id: str, since: int) -> list[dict]:
        """log_entries rows for this trace after the given cursor."""

        connection = self._read_connection()
        try:
            log_entry_rows = connection.execute(
                "SELECT id, trace_id, timestamp, details FROM log_entries "
                "WHERE trace_id = ? AND id > ? ORDER BY id",
                (trace_id, since),
            ).fetchall()

            entries = []
            for log_entry_row in log_entry_rows:
                entry = json.loads(log_entry_row["details"])
                entry["id"] = log_entry_row["id"]
                entry["trace_id"] = log_entry_row["trace_id"]
                entry["timestamp"] = log_entry_row["timestamp"]
                entries.append(entry)
            return entries
        finally:
            connection.close()

    def wait_for_log_entries_since(
        self, trace_id: str, since: int, timeout_seconds: float
    ) -> list[dict]:
        """Block until a later log_entries row exists, then return the new rows."""

        timeout_seconds = max(0.0, min(float(timeout_seconds), 30.0))
        with self._log_condition:
            generation = self._log_generation

        rows = self.fetch_log_entries_since(trace_id, since)
        if rows or timeout_seconds == 0:
            return rows

        with self._log_condition:
            if generation == self._log_generation:
                self._log_condition.wait(timeout_seconds)

        return self.fetch_log_entries_since(trace_id, since)

    def store_held_event(self, kind: str, hold: dict) -> str:
        """Insert a held_events row and return its hold_id."""

        hold_id = hold.get("hold_id") or uuid.uuid4().hex
        event_id = hold["event_id"]
        payload = {key: value for key, value in hold.items() if key not in _HELD_EVENT_RESERVED_KEYS}
        created_at = hold.get("created_at") or datetime.now(timezone.utc).isoformat()

        hold_row = {
            "hold_id": hold_id,
            "kind": kind,
            "event_id": event_id,
            "payload": json.dumps(payload),
            "created_at": created_at,
        }

        def _do(connection: sqlite3.Connection) -> str:
            """Insert a held_events row and emit the matching hold notification."""

            try:
                connection.execute(
                    "INSERT INTO held_events (hold_id, kind, event_id, payload, created_at) "
                    "VALUES (:hold_id, :kind, :event_id, :payload, :created_at)",
                    hold_row,
                )
                _insert_notification(connection, f"{kind}_hold", event_id)
                connection.commit()
                self._wake_notification_waiters()
                return hold_id
            except sqlite3.Error as exc:
                connection.rollback()
                raise PersistenceError(f"failed to store held event '{hold_id}': {exc}") from exc

        return self._submit_write(_do)

    def list_held_events(self, kind: str) -> list[dict]:
        """Unresolved held_events rows of this kind."""

        connection = self._read_connection()
        try:
            hold_rows = connection.execute(
                "SELECT * FROM held_events WHERE kind = ? AND resolved = 0 ORDER BY created_at",
                (kind,),
            ).fetchall()
            return [_decode_held_event_row(hold_row) for hold_row in hold_rows]
        finally:
            connection.close()

    def fetch_held_event(self, kind: str, event_id: str) -> dict | None:
        """The held_events row for this event id and kind, if any."""

        connection = self._read_connection()
        try:
            hold_row = connection.execute(
                "SELECT * FROM held_events WHERE kind = ? AND event_id = ? ORDER BY created_at DESC LIMIT 1",
                (kind, event_id),
            ).fetchone()
            if hold_row is None:
                return None
            return _decode_held_event_row(hold_row)
        finally:
            connection.close()

    def resolve_held_event(self, kind: str, hold_id: str, resolution: dict) -> None:
        """Mark a held_events row resolved and store the resolution payload."""

        resolved_by = resolution.get("resolved_by")
        resolved_at = resolution.get("resolved_at") or datetime.now(timezone.utc).isoformat()
        resolution_payload = {key: value for key, value in resolution.items() if key not in _HELD_EVENT_RESOLUTION_RESERVED_KEYS}

        def _do(connection: sqlite3.Connection) -> None:
            """Mark this held_events row resolved and store the resolution payload."""

            existing_hold = connection.execute(
                "SELECT resolved FROM held_events WHERE hold_id = ? AND kind = ?", (hold_id, kind)
            ).fetchone()
            if existing_hold is None:
                raise NotFoundError(f"no such {kind} hold: '{hold_id}'")
            if existing_hold["resolved"]:
                raise NotFoundError(f"{kind} hold '{hold_id}' is already resolved")

            try:
                connection.execute(
                    "UPDATE held_events SET resolved = 1, resolved_by = :resolved_by, "
                    "resolved_at = :resolved_at, resolution = :resolution "
                    "WHERE hold_id = :hold_id AND kind = :kind",
                    {
                        "resolved_by": resolved_by,
                        "resolved_at": resolved_at,
                        "resolution": json.dumps(resolution_payload),
                        "hold_id": hold_id,
                        "kind": kind,
                    },
                )
                connection.commit()
            except sqlite3.Error as exc:
                connection.rollback()
                raise PersistenceError(f"failed to resolve held event '{hold_id}': {exc}") from exc

        self._submit_write(_do)

    def mark_held_event_reminded(self, kind: str, hold_id: str, reminded_at: str) -> None:
        """Set reminded_at on this held_events row."""

        def _do(connection: sqlite3.Connection) -> None:
            """Set reminded_at on this unresolved held_events row."""

            connection.execute(
                "UPDATE held_events SET reminded_at = ? WHERE hold_id = ? AND kind = ? AND resolved = 0",
                (reminded_at, hold_id, kind),
            )
            connection.commit()

        self._submit_write(_do)

    def mark_held_event_escalated(self, kind: str, hold_id: str, escalated_at: str) -> None:
        """Set escalated_at on this held_events row."""

        def _do(connection: sqlite3.Connection) -> None:
            """Set escalated_at on this unresolved held_events row."""

            connection.execute(
                "UPDATE held_events SET escalated_at = ? WHERE hold_id = ? AND kind = ? AND resolved = 0",
                (escalated_at, hold_id, kind),
            )
            connection.commit()

        self._submit_write(_do)

    def insert_notification(self, kind: str, event_id: str) -> None:
        """Re-insert a notification_log row so a hold reminder can be sent again."""

        def _do(connection: sqlite3.Connection) -> None:
            """Insert one notification_log row and wake notification waiters."""

            _insert_notification(connection, kind, event_id)
            connection.commit()
            self._wake_notification_waiters()

        self._submit_write(_do)

    def fetch_notifications_since(self, since: int) -> list[dict]:
        """notification_log rows after the given cursor."""

        connection = self._read_connection()
        try:
            notification_rows = connection.execute(
                "SELECT sequence_id, kind, event_id, created_at FROM notification_log "
                "WHERE sequence_id > ? ORDER BY sequence_id",
                (since,),
            ).fetchall()
            return [dict(notification_row) for notification_row in notification_rows]
        finally:
            connection.close()

    def wait_for_notifications_since(self, since: int, timeout_seconds: float) -> list[dict]:
        """Block until a later notification_log row exists, then return the new rows."""

        timeout_seconds = max(0.0, min(float(timeout_seconds), 30.0))
        with self._notification_condition:
            generation = self._notification_generation

        rows = self.fetch_notifications_since(since)
        if rows or timeout_seconds == 0:
            return rows

        with self._notification_condition:
            if generation == self._notification_generation:
                self._notification_condition.wait(timeout_seconds)

        return self.fetch_notifications_since(since)

    def append_conversation_message(
        self,
        conversation_id: str,
        role: str,
        content: str,
        *,
        ttl_hours: int,
        max_turns: int,
        event_id: str | None = None,
    ) -> None:
        """Insert a conversation_messages row and prune old turns."""

        if not conversation_id or role not in {"user", "assistant"} or not content:
            raise PersistenceError("conversation message requires a conversation_id, valid role, and content")
        if ttl_hours <= 0 or max_turns <= 0:
            return

        now = datetime.now(timezone.utc)
        cutoff = (now - timedelta(hours=ttl_hours)).isoformat()
        keep_messages = max_turns * 2

        def _do(connection: sqlite3.Connection) -> None:
            """Insert one conversation message and prune expired or excess turns."""

            try:
                connection.execute(
                    "DELETE FROM conversation_messages WHERE conversation_id = ? AND created_at < ?",
                    (conversation_id, cutoff),
                )
                connection.execute(
                    "INSERT INTO conversation_messages (conversation_id, role, content, created_at, event_id) VALUES (?, ?, ?, ?, ?)",
                    (conversation_id, role, content, now.isoformat(), event_id),
                )
                connection.execute(
                    "DELETE FROM conversation_messages WHERE conversation_id = ? AND message_id NOT IN "
                    "(SELECT message_id FROM conversation_messages WHERE conversation_id = ? ORDER BY message_id DESC LIMIT ?)",
                    (conversation_id, conversation_id, keep_messages),
                )
                connection.commit()
            except sqlite3.Error as exc:
                connection.rollback()
                raise PersistenceError(f"failed to append conversation message: {exc}") from exc

        self._submit_write(_do)

    def fetch_conversation_messages(self, conversation_id: str, limit: int) -> list[dict]:
        """The newest conversation_messages rows for this conversation_id."""

        if not 1 <= limit <= 200:
            raise PersistenceError("conversation message limit must be between 1 and 200")
        connection = self._read_connection()
        try:
            rows = connection.execute(
                "SELECT message_id, conversation_id, role, content, created_at, event_id FROM "
                "(SELECT * FROM conversation_messages WHERE conversation_id = ? ORDER BY message_id DESC LIMIT ?) "
                "ORDER BY message_id",
                (conversation_id, limit),
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            connection.close()
