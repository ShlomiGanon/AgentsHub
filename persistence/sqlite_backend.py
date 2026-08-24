"""SQLite implementation of the persistence interface (work_plan.md §2.9).

Every write goes through one serialized queue with a single background
writer thread — SQLite locks the whole database on write, and this system
writes on every incoming event while insights and summaries write
concurrently, so without serialization lock errors appear under exactly
the load the demonstration will produce. Reads open their own short-lived
connection per call and run freely; WAL mode (set once, persists in the
database file) lets them do that without blocking on an in-flight write.

All schema knowledge — table names, column lists, JSON encoding of list
columns — stays confined to this module and persistence/schema.py; nothing
above the interface ever sees a column name or a SQL string.

Held-event operations (§6.7, Mission 6) are generic across both hold
kinds — `kind` is just a column value, and nothing here treats
"clarification" specially. Only the *orchestration logic* for
clarification holds (§6.2) is unbuilt; the storage they'll use is already
complete.
"""

import json
import sqlite3
import threading
import uuid
from concurrent.futures import Future
from datetime import datetime, timezone
from queue import SimpleQueue

from persistence.exceptions import NotFoundError, PersistenceError
from persistence.interface import PersistenceInterface
from persistence.migrations import run_migrations
from persistence.schema import SUMMARY_TABLE_NAMES

_STOP = object()

_EVENT_COLUMNS = (
    "event_id",
    "received_at",
    "source",
    "sender_identity",
    "source_message_id",
    "occurred_at",
    "occurred_at_is_fallback",
    "raw_text",
    "classification",
    "area",
    "entities",
    "description",
    "severity",
    "risk_level",
    "risk_reason",
    "selected_protocol",
    "protocol_reason",
    "clarification_held",
    "clarification_unresolved_field",
    "clarification_resolved_by",
    "clarification_chosen_classification",
    "approval_held",
    "approval_reason",
    "approval_answered_by",
    "approval_answered_at",
    "precedent_matched_event_ids",
    "precedent_closed_by_event_id",
    "insight_text",
    "outcome",
    "outcome_failure_reason",
)

_EVENT_JSON_COLUMNS = {"entities", "precedent_matched_event_ids"}
_EVENT_BOOL_COLUMNS = {"occurred_at_is_fallback", "clarification_held", "approval_held"}

# Envelope fields written once by append_event — never touched by
# update_event (§2.5 for raw_text; the rest of the envelope for the same
# reason: it describes where the event came from, not what happened to it).
_EVENT_IMMUTABLE_COLUMNS = {"event_id", "received_at", "source", "sender_identity", "source_message_id", "raw_text"}
_UPDATABLE_EVENT_COLUMNS = frozenset(_EVENT_COLUMNS) - _EVENT_IMMUTABLE_COLUMNS

_HELD_EVENT_RESERVED_KEYS = {"hold_id", "event_id", "created_at"}
_HELD_EVENT_RESOLUTION_RESERVED_KEYS = {"resolved_by", "resolved_at"}

# An event's outcome, once set, fans out to one or two notification_log
# rows — one per audience a real delivery path (bot/notifications.py's
# dispatch_notification) treats separately. "uncertain" and
# "closed_on_precedent" both still owe the original submitter a
# "job_finished"-shaped result (bot.api_client.BotOutcome includes both
# values as valid JobResult.outcome values) *in addition to* the
# commander-facing push §8.5/§8.6 already document. "declined" reaches the
# submitter through the same job_finished path — nothing else ever notifies
# them a declined run happened, since the reject itself already answers the
# commander who declined it synchronously, in the API response.
_OUTCOME_TO_NOTIFICATION_KINDS: dict[str, tuple[str, ...]] = {
    "succeeded": ("job_finished",),
    "declined": ("job_finished",),
    "failed": ("job_failed",),
    "uncertain": ("job_finished", "uncertain_verdict"),
    "closed_on_precedent": ("job_finished", "precedent_closure"),
}


def _encode_event_value(column: str, value):
    if column in _EVENT_JSON_COLUMNS and value is not None:
        return json.dumps(value)
    if column in _EVENT_BOOL_COLUMNS:
        return 1 if value else 0
    return value


def _decode_event_row(row: sqlite3.Row) -> dict:
    decoded = dict(row)
    for column in _EVENT_JSON_COLUMNS:
        if decoded.get(column) is not None:
            decoded[column] = json.loads(decoded[column])
    for column in _EVENT_BOOL_COLUMNS:
        decoded[column] = bool(decoded[column])
    return decoded


def _decode_step_row(row: sqlite3.Row) -> dict:
    decoded = dict(row)
    if decoded.get("allowed_tools") is not None:
        decoded["allowed_tools"] = json.loads(decoded["allowed_tools"])
    return decoded


def _decode_summary_row(row: sqlite3.Row) -> dict:
    decoded = dict(row)
    if decoded.get("event_index") is not None:
        decoded["event_index"] = json.loads(decoded["event_index"])
    return decoded


def _upsert_steps(connection: sqlite3.Connection, event_id: str, steps: list[dict]) -> None:
    for step in steps:
        payload = {
            "event_id": event_id,
            "step_index": step["step_index"],
            "agent_name": step["agent_name"],
            "task_text": step["task_text"],
            "allowed_tools": json.dumps(step.get("allowed_tools", [])),
            "result_text": step.get("result_text"),
            "attempt_count": step.get("attempt_count", 0),
        }
        connection.execute(
            """
            INSERT INTO event_steps (event_id, step_index, agent_name, task_text, allowed_tools, result_text, attempt_count)
            VALUES (:event_id, :step_index, :agent_name, :task_text, :allowed_tools, :result_text, :attempt_count)
            ON CONFLICT(event_id, step_index) DO UPDATE SET
                agent_name = excluded.agent_name,
                task_text = excluded.task_text,
                allowed_tools = excluded.allowed_tools,
                result_text = excluded.result_text,
                attempt_count = excluded.attempt_count
            """,
            payload,
        )


def _insert_notification(connection: sqlite3.Connection, kind: str, event_id: str) -> None:
    connection.execute(
        "INSERT INTO notification_log (kind, event_id, created_at) VALUES (?, ?, ?)",
        (kind, event_id, datetime.now(timezone.utc).isoformat()),
    )


def _decode_held_event_row(row: sqlite3.Row) -> dict:
    decoded = dict(row)
    payload = json.loads(decoded.pop("payload"))
    resolution_raw = decoded.pop("resolution")
    decoded["resolved"] = bool(decoded["resolved"])
    decoded["resolution"] = json.loads(resolution_raw) if resolution_raw is not None else None
    decoded.update(payload)
    return decoded


def _summary_table_name(level: str) -> str:
    table_name = SUMMARY_TABLE_NAMES.get(level)
    if table_name is None:
        raise PersistenceError(f"unknown summary level: '{level}' (expected one of {sorted(SUMMARY_TABLE_NAMES)})")
    return table_name


class SQLitePersistence(PersistenceInterface):
    def __init__(self, db_path: str):
        super().__init__(db_path)

        run_migrations(db_path)

        self._write_queue: SimpleQueue = SimpleQueue()
        self._writer_thread = threading.Thread(target=self._run_writer, daemon=True)
        self._writer_thread.start()

    def close(self) -> None:
        self._write_queue.put(_STOP)
        self._writer_thread.join()

    # -- The serialized writer (§2.9) ------------------------------------

    def _run_writer(self) -> None:
        connection = sqlite3.connect(self.db_path)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.row_factory = sqlite3.Row

        try:
            while True:
                job = self._write_queue.get()
                if job is _STOP:
                    return

                fn, future = job
                try:
                    future.set_result(fn(connection))
                except BaseException as exc:  # propagated to the caller via the future
                    future.set_exception(exc)
        finally:
            connection.close()

    def _submit_write(self, fn):
        future: Future = Future()
        self._write_queue.put((fn, future))
        return future.result()

    def _read_connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _attach_steps(self, connection: sqlite3.Connection, event: dict) -> dict:
        rows = connection.execute(
            "SELECT * FROM event_steps WHERE event_id = ? ORDER BY step_index",
            (event["event_id"],),
        ).fetchall()
        event["steps"] = [_decode_step_row(row) for row in rows]
        return event

    # -- Events (§2.3, §2.5) ----------------------------------------------

    def append_event(self, event: dict) -> str:
        event_id = event.get("event_id") or uuid.uuid4().hex
        row = {column: _encode_event_value(column, event.get(column)) for column in _EVENT_COLUMNS}
        row["event_id"] = event_id
        steps = event.get("steps") or []

        def _do(connection: sqlite3.Connection) -> str:
            try:
                columns = ", ".join(_EVENT_COLUMNS)
                placeholders = ", ".join(f":{column}" for column in _EVENT_COLUMNS)
                connection.execute(f"INSERT INTO events ({columns}) VALUES ({placeholders})", row)
                _upsert_steps(connection, event_id, steps)
                connection.commit()
                return event_id
            except sqlite3.Error as exc:
                connection.rollback()
                raise PersistenceError(f"failed to append event '{event_id}': {exc}") from exc

        return self._submit_write(_do)

    def update_event(self, event_id: str, updates: dict) -> None:
        steps = updates.get("steps") or []
        column_updates = {key: value for key, value in updates.items() if key != "steps"}

        unknown_columns = set(column_updates) - _UPDATABLE_EVENT_COLUMNS
        if unknown_columns:
            raise PersistenceError(f"cannot update event column(s): {', '.join(sorted(unknown_columns))}")

        def _do(connection: sqlite3.Connection) -> None:
            exists = connection.execute("SELECT 1 FROM events WHERE event_id = ?", (event_id,)).fetchone()
            if exists is None:
                raise NotFoundError(f"no such event: '{event_id}'")

            try:
                if column_updates:
                    encoded = {column: _encode_event_value(column, value) for column, value in column_updates.items()}
                    set_clause = ", ".join(f"{column} = :{column}" for column in encoded)
                    encoded["event_id"] = event_id
                    connection.execute(f"UPDATE events SET {set_clause} WHERE event_id = :event_id", encoded)

                if steps:
                    _upsert_steps(connection, event_id, steps)

                outcome = column_updates.get("outcome")
                if outcome is not None:
                    for notification_kind in _OUTCOME_TO_NOTIFICATION_KINDS.get(outcome, ()):
                        _insert_notification(connection, notification_kind, event_id)

                connection.commit()
            except sqlite3.Error as exc:
                connection.rollback()
                raise PersistenceError(f"failed to update event '{event_id}': {exc}") from exc

        self._submit_write(_do)

    def fetch_event(self, event_id: str) -> dict | None:
        connection = self._read_connection()
        try:
            row = connection.execute("SELECT * FROM events WHERE event_id = ?", (event_id,)).fetchone()
            if row is None:
                return None
            return self._attach_steps(connection, _decode_event_row(row))
        finally:
            connection.close()

    def fetch_events_range(self, start, end) -> list[dict]:
        connection = self._read_connection()
        try:
            rows = connection.execute(
                "SELECT * FROM events WHERE occurred_at IS NOT NULL AND occurred_at >= ? AND occurred_at < ? "
                "ORDER BY occurred_at",
                (start, end),
            ).fetchall()
            return [self._attach_steps(connection, _decode_event_row(row)) for row in rows]
        finally:
            connection.close()

    def fetch_events_by_type_area_window(self, event_type: str, area: str, window_start, window_end) -> list[dict]:
        connection = self._read_connection()
        try:
            rows = connection.execute(
                "SELECT * FROM events WHERE classification = ? AND area = ? "
                "AND occurred_at IS NOT NULL AND occurred_at >= ? AND occurred_at < ? ORDER BY occurred_at",
                (event_type, area, window_start, window_end),
            ).fetchall()
            return [self._attach_steps(connection, _decode_event_row(row)) for row in rows]
        finally:
            connection.close()

    # -- Summaries (§2.6) ---------------------------------------------------

    def write_summary(self, level: str, summary: dict) -> None:
        table_name = _summary_table_name(level)
        payload = {
            "summary_text": summary["summary_text"],
            "period_start": summary["period_start"],
            "period_end": summary["period_end"],
            "generated_at": summary["generated_at"],
            "event_index": json.dumps(summary["event_index"]) if summary.get("event_index") is not None else None,
        }

        def _do(connection: sqlite3.Connection) -> None:
            try:
                connection.execute(
                    f"""
                    INSERT INTO {table_name} (summary_text, period_start, period_end, generated_at, event_index)
                    VALUES (:summary_text, :period_start, :period_end, :generated_at, :event_index)
                    ON CONFLICT(period_start, period_end) DO UPDATE SET
                        summary_text = excluded.summary_text,
                        generated_at = excluded.generated_at,
                        event_index = excluded.event_index
                    """,
                    payload,
                )
                connection.commit()
            except sqlite3.Error as exc:
                connection.rollback()
                raise PersistenceError(f"failed to write {level} summary: {exc}") from exc

        self._submit_write(_do)

    def fetch_summaries_range(self, level: str, start, end) -> list[dict]:
        table_name = _summary_table_name(level)
        connection = self._read_connection()
        try:
            rows = connection.execute(
                f"SELECT summary_text, period_start, period_end, generated_at, event_index FROM {table_name} "
                "WHERE period_start < ? AND period_end > ? ORDER BY period_start",
                (end, start),
            ).fetchall()
            return [_decode_summary_row(row) for row in rows]
        finally:
            connection.close()

    # -- Users (§2.4, §1.10) ------------------------------------------------

    def read_user(self, telegram_identity: str) -> dict | None:
        connection = self._read_connection()
        try:
            row = connection.execute(
                "SELECT telegram_identity, permission_level FROM users WHERE telegram_identity = ?",
                (telegram_identity,),
            ).fetchone()
            return dict(row) if row is not None else None
        finally:
            connection.close()

    def write_user(self, telegram_identity: str, permission_level: str) -> None:
        def _do(connection: sqlite3.Connection) -> None:
            try:
                connection.execute(
                    "INSERT INTO users (telegram_identity, permission_level) VALUES (?, ?) "
                    "ON CONFLICT(telegram_identity) DO UPDATE SET permission_level = excluded.permission_level",
                    (telegram_identity, permission_level),
                )
                connection.commit()
            except sqlite3.Error as exc:
                connection.rollback()
                raise PersistenceError(f"failed to write user '{telegram_identity}': {exc}") from exc

        self._submit_write(_do)

    def delete_user(self, telegram_identity: str) -> None:
        def _do(connection: sqlite3.Connection) -> None:
            cursor = connection.execute("DELETE FROM users WHERE telegram_identity = ?", (telegram_identity,))
            connection.commit()
            if cursor.rowcount == 0:
                raise NotFoundError(f"no such user: '{telegram_identity}'")

        self._submit_write(_do)

    def list_users(self) -> list[dict]:
        connection = self._read_connection()
        try:
            rows = connection.execute("SELECT telegram_identity, permission_level FROM users").fetchall()
            return [dict(row) for row in rows]
        finally:
            connection.close()

    # -- Held events (§6.7) --------------------------------------------------

    def store_held_event(self, kind: str, hold: dict) -> str:
        hold_id = hold.get("hold_id") or uuid.uuid4().hex
        event_id = hold["event_id"]
        payload = {key: value for key, value in hold.items() if key not in _HELD_EVENT_RESERVED_KEYS}
        created_at = hold.get("created_at") or datetime.now(timezone.utc).isoformat()

        row = {
            "hold_id": hold_id,
            "kind": kind,
            "event_id": event_id,
            "payload": json.dumps(payload),
            "created_at": created_at,
        }

        def _do(connection: sqlite3.Connection) -> str:
            try:
                connection.execute(
                    "INSERT INTO held_events (hold_id, kind, event_id, payload, created_at) "
                    "VALUES (:hold_id, :kind, :event_id, :payload, :created_at)",
                    row,
                )
                _insert_notification(connection, f"{kind}_hold", event_id)
                connection.commit()
                return hold_id
            except sqlite3.Error as exc:
                connection.rollback()
                raise PersistenceError(f"failed to store held event '{hold_id}': {exc}") from exc

        return self._submit_write(_do)

    def list_held_events(self, kind: str) -> list[dict]:
        connection = self._read_connection()
        try:
            rows = connection.execute(
                "SELECT * FROM held_events WHERE kind = ? AND resolved = 0 ORDER BY created_at",
                (kind,),
            ).fetchall()
            return [_decode_held_event_row(row) for row in rows]
        finally:
            connection.close()

    def fetch_held_event(self, kind: str, event_id: str) -> dict | None:
        connection = self._read_connection()
        try:
            # Most-recent-first: an event carries at most one hold of a
            # given kind at a time by design (docs/vocabulary.md), but the
            # table has no constraint enforcing that — this order picks
            # the latest if that invariant is ever violated, rather than
            # an arbitrary row.
            row = connection.execute(
                "SELECT * FROM held_events WHERE kind = ? AND event_id = ? ORDER BY created_at DESC LIMIT 1",
                (kind, event_id),
            ).fetchone()
            if row is None:
                return None
            return _decode_held_event_row(row)
        finally:
            connection.close()

    def resolve_held_event(self, kind: str, hold_id: str, resolution: dict) -> None:
        resolved_by = resolution.get("resolved_by")
        resolved_at = resolution.get("resolved_at") or datetime.now(timezone.utc).isoformat()
        resolution_payload = {key: value for key, value in resolution.items() if key not in _HELD_EVENT_RESOLUTION_RESERVED_KEYS}

        def _do(connection: sqlite3.Connection) -> None:
            existing = connection.execute(
                "SELECT resolved FROM held_events WHERE hold_id = ? AND kind = ?", (hold_id, kind)
            ).fetchone()
            if existing is None:
                raise NotFoundError(f"no such {kind} hold: '{hold_id}'")
            if existing["resolved"]:
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

    # -- Notification log (§8.12) --------------------------------------------

    def fetch_notifications_since(self, since: int) -> list[dict]:
        connection = self._read_connection()
        try:
            rows = connection.execute(
                "SELECT sequence_id, kind, event_id, created_at FROM notification_log "
                "WHERE sequence_id > ? ORDER BY sequence_id",
                (since,),
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            connection.close()
