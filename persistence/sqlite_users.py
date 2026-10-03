"""User rows for SQLitePersistence."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from persistence.contracts import NotFoundError, PersistenceError
from persistence.sqlite_support import _GROUP_COLUMNS, _group_record


class SqliteUsersMixin:
    """User reads/writes for SQLitePersistence."""
    def read_user(self, telegram_identity: str) -> dict | None:
        """Return the users row for this Telegram identity, or None."""

        connection = self._read_connection()
        try:
            user_row = connection.execute(
                "SELECT telegram_identity, permission_level, full_name, auto_register FROM users WHERE telegram_identity = ?",
                (telegram_identity,),
            ).fetchone()
            if user_row is None:
                return None
            result = dict(user_row)
            result["auto_register"] = bool(result["auto_register"])
            return result
        finally:
            connection.close()

    def write_user(self, telegram_identity: str, permission_level: str, full_name: str | None = None) -> None:
        """Insert or update a users row's permission level and optional full name."""

        def _do(connection: sqlite3.Connection) -> None:
            """Insert or update this users row's permission level and optional full name."""

            try:
                connection.execute(
                    "INSERT INTO users (telegram_identity, permission_level, full_name, auto_register) "
                    "VALUES (?, ?, COALESCE(?, ''), 0) "
                    "ON CONFLICT(telegram_identity) DO UPDATE SET "
                    "permission_level = excluded.permission_level, "
                    "full_name = CASE WHEN ? IS NULL THEN users.full_name ELSE excluded.full_name END",
                    (telegram_identity, permission_level, full_name, full_name),
                )
                connection.commit()
            except sqlite3.Error as exc:
                connection.rollback()
                raise PersistenceError(f"failed to write user '{telegram_identity}': {exc}") from exc

        self._submit_write(_do)

    def register_telegram_user_if_missing(self, telegram_identity: str) -> dict:
        """Insert an auto-registered viewer users row when the identity is new."""

        def _do(connection: sqlite3.Connection) -> None:
            """Insert an auto-registered viewer row when this identity is new."""

            connection.execute(
                "INSERT INTO users (telegram_identity, permission_level, full_name, auto_register) "
                "VALUES (?, 'viewer', '', 1) ON CONFLICT(telegram_identity) DO NOTHING",
                (telegram_identity,),
            )
            connection.commit()

        self._submit_write(_do)
        result = self.read_user(telegram_identity)
        if result is None:
            raise PersistenceError(f"failed to register telegram user '{telegram_identity}'")
        return result

    def ensure_user_exists(self, telegram_identity: str, permission_level: str, full_name: str) -> bool:
        """True if this users row was just inserted; False if it already existed."""

        def _do(connection: sqlite3.Connection) -> bool:
            """Insert this users row only when the identity is new."""

            try:
                cursor = connection.execute(
                    "INSERT INTO users (telegram_identity, permission_level, full_name, auto_register) "
                    "VALUES (?, ?, ?, 0) ON CONFLICT(telegram_identity) DO NOTHING",
                    (telegram_identity, permission_level, full_name),
                )
                connection.commit()
                return cursor.rowcount > 0
            except sqlite3.Error as exc:
                connection.rollback()
                raise PersistenceError(f"failed to ensure user '{telegram_identity}' exists: {exc}") from exc

        return self._submit_write(_do)

    def admit_telegram_update(
        self,
        telegram_identity: str,
        group_chat_id: str | None,
        group_label: str,
        allow_registration: bool,
    ) -> dict:
        """Insert missing users/groups rows for this Telegram update and return both."""

        created_at = datetime.now(timezone.utc).isoformat()

        def _do(connection: sqlite3.Connection) -> dict:
            """Read, and when allowed create, the user and optional group for one Telegram update."""

            try:
                if allow_registration:
                    connection.execute(
                        "INSERT INTO users (telegram_identity, permission_level, full_name, auto_register) "
                        "VALUES (?, 'viewer', '', 1) ON CONFLICT(telegram_identity) DO NOTHING",
                        (telegram_identity,),
                    )
                    if group_chat_id is not None:
                        connection.execute(
                            "INSERT INTO telegram_groups (chat_id, agent_name, label, created_at, auto_register) "
                            "VALUES (?, 'main_agent', ?, ?, 1) ON CONFLICT(chat_id) DO NOTHING",
                            (group_chat_id, group_label or "", created_at),
                        )
                user_row = connection.execute(
                    "SELECT telegram_identity, permission_level, full_name, auto_register "
                    "FROM users WHERE telegram_identity = ?",
                    (telegram_identity,),
                ).fetchone()
                group_row = None
                if group_chat_id is not None:
                    group_row = connection.execute(
                        f"SELECT {_GROUP_COLUMNS} FROM telegram_groups WHERE chat_id = ?",
                        (group_chat_id,),
                    ).fetchone()
                connection.commit()
                return {
                    "user": dict(user_row) if user_row is not None else None,
                    "group": dict(group_row) if group_row is not None else None,
                }
            except sqlite3.Error as exc:
                connection.rollback()
                raise PersistenceError(f"failed to admit telegram update for '{telegram_identity}': {exc}") from exc

        result = self._submit_write(_do)
        if result["user"] is not None:
            result["user"]["auto_register"] = bool(result["user"]["auto_register"])
        if result["group"] is not None:
            result["group"] = _group_record(result["group"])
        return result

    def approve_user(self, telegram_identity: str) -> dict:
        """Clear auto_register on this users row."""

        def _do(connection: sqlite3.Connection) -> None:
            """Clear auto_register on this users row."""

            cursor = connection.execute(
                "UPDATE users SET auto_register = 0 WHERE telegram_identity = ?",
                (telegram_identity,),
            )
            connection.commit()
            if cursor.rowcount == 0:
                raise NotFoundError(f"no such user: '{telegram_identity}'")

        self._submit_write(_do)
        result = self.read_user(telegram_identity)
        if result is None:
            raise NotFoundError(f"no such user: '{telegram_identity}'")
        return result

    def update_user_full_name(self, telegram_identity: str, full_name: str) -> None:
        """Set full_name on this users row."""

        def _do(connection: sqlite3.Connection) -> None:
            """Set full_name on this users row."""

            cursor = connection.execute(
                "UPDATE users SET full_name = ? WHERE telegram_identity = ?",
                (full_name, telegram_identity),
            )
            connection.commit()
            if cursor.rowcount == 0:
                raise NotFoundError(f"no such user: '{telegram_identity}'")

        self._submit_write(_do)

    def delete_user(self, telegram_identity: str) -> None:
        """Delete this users row."""

        def _do(connection: sqlite3.Connection) -> None:
            """Delete this users row."""

            cursor = connection.execute("DELETE FROM users WHERE telegram_identity = ?", (telegram_identity,))
            connection.commit()
            if cursor.rowcount == 0:
                raise NotFoundError(f"no such user: '{telegram_identity}'")

        self._submit_write(_do)

    def list_users(self) -> list[dict]:
        """Every users row."""

        connection = self._read_connection()
        try:
            user_rows = connection.execute(
                "SELECT telegram_identity, permission_level, full_name, auto_register FROM users"
            ).fetchall()
            results = [dict(user_row) for user_row in user_rows]
            for result in results:
                result["auto_register"] = bool(result["auto_register"])
            return results
        finally:
            connection.close()
