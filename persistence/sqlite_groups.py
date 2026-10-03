"""Telegram group rows for SQLitePersistence."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from persistence.contracts import NotFoundError, PersistenceError
from persistence.sqlite_support import _GROUP_COLUMNS, _group_record, _normalized_attendance_hour


class SqliteGroupsMixin:
    """Telegram group reads/writes for SQLitePersistence."""
    def read_group(self, chat_id: str) -> dict | None:
        """Return the telegram_groups row for this chat id, or None."""

        connection = self._read_connection()
        try:
            group_row = connection.execute(
                f"SELECT {_GROUP_COLUMNS} FROM telegram_groups WHERE chat_id = ?",
                (chat_id,),
            ).fetchone()
            if group_row is None:
                return None
            return _group_record(group_row)
        finally:
            connection.close()

    def write_group(
        self,
        chat_id: str,
        agent_name: str,
        label: str = "",
        *,
        attendance_check_enabled: bool | None = None,
        attendance_check_hour: int | None = None,
    ) -> None:
        """Insert or update a telegram_groups binding."""

        created_at = datetime.now(timezone.utc).isoformat()
        hour = _normalized_attendance_hour(attendance_check_hour)

        def _do(connection: sqlite3.Connection) -> None:
            """Insert or update this telegram_groups binding and optional attendance fields."""

            try:
                connection.execute(
                    "INSERT INTO telegram_groups (chat_id, agent_name, label, created_at, auto_register) VALUES (?, ?, ?, ?, 0) "
                    "ON CONFLICT(chat_id) DO UPDATE SET agent_name = excluded.agent_name, label = excluded.label",
                    (chat_id, agent_name, label, created_at),
                )
                assignments = []
                values: list = []
                if attendance_check_enabled is not None:
                    assignments.append("attendance_check_enabled = ?")
                    values.append(1 if attendance_check_enabled else 0)
                if hour is not None:
                    assignments.append("attendance_check_hour = ?")
                    values.append(hour)
                if assignments:
                    values.append(chat_id)
                    connection.execute(
                        f"UPDATE telegram_groups SET {', '.join(assignments)} WHERE chat_id = ?",
                        values,
                    )
                connection.commit()
            except sqlite3.Error as exc:
                connection.rollback()
                raise PersistenceError(f"failed to write telegram group '{chat_id}': {exc}") from exc

        self._submit_write(_do)

    def register_telegram_group_if_missing(self, chat_id: str, label: str = "") -> dict:
        """Insert an auto-registered telegram_groups row when the chat is new."""

        created_at = datetime.now(timezone.utc).isoformat()

        def _do(connection: sqlite3.Connection) -> None:
            """Insert an auto-registered telegram_groups row when this chat is new."""

            connection.execute(
                "INSERT INTO telegram_groups (chat_id, agent_name, label, created_at, auto_register) "
                "VALUES (?, 'main_agent', ?, ?, 1) ON CONFLICT(chat_id) DO NOTHING",
                (chat_id, label or "", created_at),
            )
            connection.commit()

        self._submit_write(_do)
        result = self.read_group(chat_id)
        if result is None:
            raise PersistenceError(f"failed to register telegram group '{chat_id}'")
        return result

    def ensure_group_exists(self, chat_id: str, agent_name: str, label: str) -> bool:
        """True if this telegram_groups row was just inserted; False if it already existed."""

        created_at = datetime.now(timezone.utc).isoformat()

        def _do(connection: sqlite3.Connection) -> bool:
            """Insert this telegram_groups row only when the chat id is new."""

            try:
                cursor = connection.execute(
                    "INSERT INTO telegram_groups (chat_id, agent_name, label, created_at, auto_register) "
                    "VALUES (?, ?, ?, ?, 0) ON CONFLICT(chat_id) DO NOTHING",
                    (chat_id, agent_name, label, created_at),
                )
                connection.commit()
                return cursor.rowcount > 0
            except sqlite3.Error as exc:
                connection.rollback()
                raise PersistenceError(f"failed to ensure telegram group '{chat_id}' exists: {exc}") from exc

        return self._submit_write(_do)

    def rename_group(self, old_chat_id: str, new_chat_id: str) -> dict:
        """Change a telegram_groups chat_id, refusing if the new id already exists."""

        def _do(connection: sqlite3.Connection) -> None:
            """Change this telegram_groups chat_id, refusing a colliding new id."""

            try:
                cursor = connection.execute(
                    "UPDATE telegram_groups SET chat_id = ? WHERE chat_id = ?",
                    (new_chat_id, old_chat_id),
                )
                connection.commit()
            except sqlite3.IntegrityError as exc:
                connection.rollback()
                raise PersistenceError(f"telegram group '{new_chat_id}' already exists") from exc
            except sqlite3.Error as exc:
                connection.rollback()
                raise PersistenceError(f"failed to rename telegram group '{old_chat_id}': {exc}") from exc
            if cursor.rowcount == 0:
                raise NotFoundError(f"no such telegram group: '{old_chat_id}'")

        self._submit_write(_do)
        result = self.read_group(new_chat_id)
        if result is None:
            raise PersistenceError(f"failed to rename telegram group '{old_chat_id}' to '{new_chat_id}'")
        return result

    def approve_group(self, chat_id: str) -> dict:
        """Clear auto_register on this telegram_groups row."""

        def _do(connection: sqlite3.Connection) -> None:
            """Clear auto_register on this telegram_groups row."""

            cursor = connection.execute(
                "UPDATE telegram_groups SET auto_register = 0 WHERE chat_id = ?",
                (chat_id,),
            )
            connection.commit()
            if cursor.rowcount == 0:
                raise NotFoundError(f"no such telegram group: '{chat_id}'")

        self._submit_write(_do)
        result = self.read_group(chat_id)
        if result is None:
            raise NotFoundError(f"no such telegram group: '{chat_id}'")
        return result

    def delete_group(self, chat_id: str) -> None:
        """Delete this telegram_groups row."""

        def _do(connection: sqlite3.Connection) -> None:
            """Delete this telegram_groups row."""

            cursor = connection.execute("DELETE FROM telegram_groups WHERE chat_id = ?", (chat_id,))
            connection.commit()
            if cursor.rowcount == 0:
                raise NotFoundError(f"no such telegram group: '{chat_id}'")

        self._submit_write(_do)

    def list_groups(self) -> list[dict]:
        """Every telegram_groups row."""

        connection = self._read_connection()
        try:
            group_rows = connection.execute(
                f"SELECT {_GROUP_COLUMNS} FROM telegram_groups ORDER BY chat_id"
            ).fetchall()
            return [_group_record(group_row) for group_row in group_rows]
        finally:
            connection.close()
