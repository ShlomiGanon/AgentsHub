"""SQLite implementation of the persistence contract."""

import json
import sqlite3
import threading
import uuid
from concurrent.futures import Future
from datetime import datetime, timedelta, timezone
from queue import SimpleQueue

from persistence.contracts import EventSearchCriteria, NotFoundError, PersistenceError, PersistenceInterface
from persistence.schema import SUMMARY_TABLE_NAMES, run_migrations
from tools import telemetry_span

_STOP = object()


class _ReadConnectionLease:
    def __init__(self, connection: sqlite3.Connection):
        self._connection = connection

    def execute(self, *args, **kwargs):
        with telemetry_span("sqlite_read", operation="execute"):
            return self._connection.execute(*args, **kwargs)

    def close(self) -> None:
        return None

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
    "trace_id",
    "conversation_id",
    "deadline_at",
    "ingestion_key",
)

_EVENT_JSON_COLUMNS = {"entities", "precedent_matched_event_ids"}
_EVENT_BOOL_COLUMNS = {"occurred_at_is_fallback", "clarification_held", "approval_held"}

_EVENT_IMMUTABLE_COLUMNS = {
    "event_id", "received_at", "source", "sender_identity", "source_message_id", "raw_text",
    "trace_id", "conversation_id", "deadline_at", "ingestion_key",
}
_UPDATABLE_EVENT_COLUMNS = frozenset(_EVENT_COLUMNS) - _EVENT_IMMUTABLE_COLUMNS

_HELD_EVENT_RESERVED_KEYS = {"hold_id", "event_id", "created_at"}
_HELD_EVENT_RESOLUTION_RESERVED_KEYS = {"resolved_by", "resolved_at"}

_OUTCOME_TO_NOTIFICATION_KINDS: dict[str, tuple[str, ...]] = {
    "succeeded": ("job_finished",),
    "declined": ("job_finished",),
    "failed": ("job_failed",),
    "uncertain": ("job_finished", "uncertain_verdict"),
    "closed_on_precedent": ("job_finished", "precedent_closure"),
    "no_match_protocol": ("job_finished", "no_match_notice"),
}


def _encode_event_value(column: str, value):
    if column in _EVENT_JSON_COLUMNS and value is not None:
        return json.dumps(value)
    if column in _EVENT_BOOL_COLUMNS:
        return 1 if value else 0
    return value


def _decode_event_row(event_row: sqlite3.Row) -> dict:
    decoded = dict(event_row)
    for column in _EVENT_JSON_COLUMNS:
        if decoded.get(column) is not None:
            decoded[column] = json.loads(decoded[column])
    for column in _EVENT_BOOL_COLUMNS:
        decoded[column] = bool(decoded[column])
    return decoded


def _decode_step_row(step_row: sqlite3.Row) -> dict:
    decoded = dict(step_row)
    if decoded.get("allowed_tools") is not None:
        decoded["allowed_tools"] = json.loads(decoded["allowed_tools"])
    if decoded.get("depends_on") is not None:
        decoded["depends_on"] = json.loads(decoded["depends_on"])
    for column in ("required_event_fields", "missing_event_fields"):
        if decoded.get(column) is not None:
            decoded[column] = json.loads(decoded[column])
    return decoded


def _decode_summary_row(summary_row: sqlite3.Row) -> dict:
    decoded = dict(summary_row)
    if decoded.get("event_index") is not None:
        decoded["event_index"] = json.loads(decoded["event_index"])
    return decoded


def _upsert_steps(connection: sqlite3.Connection, event_id: str, steps: list[dict]) -> None:
    for step in steps:
        requested_status = step.get("status", "auto")
        status = (
            "succeeded" if step.get("result_text") is not None else "failed"
        ) if requested_status == "auto" else requested_status
        payload = {
            "event_id": event_id,
            "step_index": step["step_index"],
            "agent_name": step["agent_name"],
            "task_text": step["task_text"],
            "allowed_tools": json.dumps(step.get("allowed_tools", [])),
            "result_text": step.get("result_text"),
            "attempt_count": step.get("attempt_count", 0),
            "step_id": step.get("step_id") or str(step["step_index"]),
            "depends_on": json.dumps(step.get("depends_on", [])),
            "required_event_fields": json.dumps(step.get("required_event_fields", [])),
            "missing_event_fields": json.dumps(step.get("missing_event_fields", [])),
            "status": status,
            "failure_reason": step.get("failure_reason"),
        }
        connection.execute(
            """
            INSERT INTO event_steps (
                event_id, step_index, agent_name, task_text, allowed_tools, result_text, attempt_count,
                step_id, depends_on, required_event_fields, missing_event_fields, status, failure_reason
            )
            VALUES (
                :event_id, :step_index, :agent_name, :task_text, :allowed_tools, :result_text, :attempt_count,
                :step_id, :depends_on, :required_event_fields, :missing_event_fields, :status, :failure_reason
            )
            ON CONFLICT(event_id, step_index) DO UPDATE SET
                agent_name = excluded.agent_name,
                task_text = excluded.task_text,
                allowed_tools = excluded.allowed_tools,
                result_text = excluded.result_text,
                attempt_count = excluded.attempt_count,
                step_id = excluded.step_id,
                depends_on = excluded.depends_on,
                required_event_fields = excluded.required_event_fields,
                missing_event_fields = excluded.missing_event_fields,
                status = excluded.status,
                failure_reason = excluded.failure_reason
            """,
            payload,
        )


def _insert_notification(connection: sqlite3.Connection, kind: str, event_id: str) -> None:
    connection.execute(
        "INSERT INTO notification_log (kind, event_id, created_at) VALUES (?, ?, ?)",
        (kind, event_id, datetime.now(timezone.utc).isoformat()),
    )


def _decode_held_event_row(hold_row: sqlite3.Row) -> dict:
    decoded = dict(hold_row)
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


_SEARCH_FILTER_COLUMNS = {
    "classifications": "classification",
    "areas": "area",
    "outcomes": "outcome",
    "protocol_names": "selected_protocol",
    "event_ids": "event_id",
    "risk_levels": "risk_level",
}
_AGGREGATE_EXPRESSIONS = {
    "classification": "classification",
    "area": "area",
    "outcome": "outcome",
    "protocol": "selected_protocol",
    "day": "substr(occurred_at, 1, 10)",
    "month": "substr(occurred_at, 1, 7)",
}


def _search_where(criteria: EventSearchCriteria) -> tuple[str, list[object], str]:
    if criteria.time_basis not in {"occurred_at", "received_at"}:
        raise PersistenceError(f"unsupported event time basis: {criteria.time_basis!r}")

    clauses: list[str] = []
    parameters: list[object] = []
    time_column = criteria.time_basis
    clauses.append(f"{time_column} IS NOT NULL")

    if criteria.time_start is not None:
        clauses.append(f"{time_column} >= ?")
        parameters.append(criteria.time_start)
    if criteria.time_end is not None:
        clauses.append(f"{time_column} < ?")
        parameters.append(criteria.time_end)

    for attribute_name, column_name in _SEARCH_FILTER_COLUMNS.items():
        values = tuple(getattr(criteria, attribute_name))
        if not values:
            continue
        placeholders = ", ".join("?" for _ in values)
        clauses.append(f"{column_name} IN ({placeholders})")
        parameters.extend(values)

    if criteria.sender_identity is not None:
        clauses.append("sender_identity = ?")
        parameters.append(criteria.sender_identity)

    return (" AND ".join(clauses) if clauses else "1 = 1"), parameters, time_column


class SQLitePersistence(PersistenceInterface):
    def __init__(self, db_path: str):
        super().__init__(db_path)

        run_migrations(db_path)

        self._write_queue: SimpleQueue = SimpleQueue()
        self._notification_condition = threading.Condition()
        self._notification_generation = 0
        self._read_local = threading.local()
        self._read_connections: list[sqlite3.Connection] = []
        self._read_connections_lock = threading.Lock()
        self._writer_thread = threading.Thread(target=self._run_writer, daemon=True)
        self._writer_thread.start()

    def close(self) -> None:
        self._write_queue.put(_STOP)
        self._writer_thread.join()
        with self._read_connections_lock:
            connections, self._read_connections = self._read_connections, []
        for connection in connections:
            connection.close()


    def _run_writer(self) -> None:
        connection = sqlite3.connect(self.db_path)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.row_factory = sqlite3.Row

        try:
            while True:
                job = self._write_queue.get()
                if job is _STOP:
                    return

                write_operation, future = job
                try:
                    future.set_result(write_operation(connection))
                except BaseException as exc:  # propagated to the caller via the future
                    future.set_exception(exc)
        finally:
            connection.close()

    def _submit_write(self, write_operation):
        # Reentrant writes would wait behind themselves forever.
        if threading.current_thread() is self._writer_thread:
            raise PersistenceError("cannot submit a write from within the persistence writer thread itself — this would deadlock")

        # A closed writer cannot service queued futures.
        if not self._writer_thread.is_alive():
            raise PersistenceError("cannot submit a write after this persistence connection has been closed")

        future: Future = Future()
        self._write_queue.put((write_operation, future))
        with telemetry_span("sqlite_write", operation="commit"):
            return future.result()

    def _wake_notification_waiters(self) -> None:
        with self._notification_condition:
            self._notification_generation += 1
            self._notification_condition.notify_all()

    def _read_connection(self) -> _ReadConnectionLease:
        connection = getattr(self._read_local, "connection", None)
        if connection is None:
            connection = sqlite3.connect(self.db_path, check_same_thread=False)
            connection.row_factory = sqlite3.Row
            self._read_local.connection = connection
            with self._read_connections_lock:
                self._read_connections.append(connection)
        return _ReadConnectionLease(connection)

    def _attach_steps(self, connection: sqlite3.Connection, event: dict) -> dict:
        step_rows = connection.execute(
            "SELECT * FROM event_steps WHERE event_id = ? ORDER BY step_index",
            (event["event_id"],),
        ).fetchall()
        event["steps"] = [_decode_step_row(step_row) for step_row in step_rows]
        return event

    def _attach_steps_many(self, connection: sqlite3.Connection, events: list[dict]) -> list[dict]:
        if not events:
            return events

        event_ids = [event["event_id"] for event in events]
        placeholders = ", ".join("?" for _ in event_ids)
        step_rows = connection.execute(
            f"SELECT * FROM event_steps WHERE event_id IN ({placeholders}) ORDER BY event_id, step_index",
            event_ids,
        ).fetchall()
        steps_by_event: dict[str, list[dict]] = {event_id: [] for event_id in event_ids}
        for step_row in step_rows:
            steps_by_event[step_row["event_id"]].append(_decode_step_row(step_row))
        for event in events:
            event["steps"] = steps_by_event[event["event_id"]]
        return events


    def append_event(self, event: dict) -> str:
        event_id = event.get("event_id") or uuid.uuid4().hex
        event_row = {column: _encode_event_value(column, event.get(column)) for column in _EVENT_COLUMNS}
        event_row["event_id"] = event_id
        if event_row.get("source_message_id") and event_row.get("ingestion_key") is None:
            event_row["ingestion_key"] = "\x1f".join(
                (str(event_row.get("source") or ""), str(event_row.get("sender_identity") or ""), str(event_row["source_message_id"]))
            )
        steps = event.get("steps") or []

        def _do(connection: sqlite3.Connection) -> str:
            try:
                if event_row.get("ingestion_key"):
                    existing = connection.execute(
                        "SELECT event_id FROM events WHERE ingestion_key = ?", (event_row["ingestion_key"],)
                    ).fetchone()
                    if existing is not None:
                        return existing["event_id"]

                columns = ", ".join(_EVENT_COLUMNS)
                placeholders = ", ".join(f":{column}" for column in _EVENT_COLUMNS)
                connection.execute(f"INSERT INTO events ({columns}) VALUES ({placeholders})", event_row)
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
            existing = connection.execute("SELECT outcome FROM events WHERE event_id = ?", (event_id,)).fetchone()
            if existing is None:
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
                notification_kinds = (
                    _OUTCOME_TO_NOTIFICATION_KINDS.get(outcome, ())
                    if outcome is not None and outcome != existing["outcome"]
                    else ()
                )
                for notification_kind in notification_kinds:
                    _insert_notification(connection, notification_kind, event_id)

                connection.commit()
                if notification_kinds:
                    self._wake_notification_waiters()
            except sqlite3.Error as exc:
                connection.rollback()
                raise PersistenceError(f"failed to update event '{event_id}': {exc}") from exc

        self._submit_write(_do)

    def fetch_event(self, event_id: str) -> dict | None:
        connection = self._read_connection()
        try:
            event_row = connection.execute("SELECT * FROM events WHERE event_id = ?", (event_id,)).fetchone()
            if event_row is None:
                return None
            return self._attach_steps(connection, _decode_event_row(event_row))
        finally:
            connection.close()

    def fetch_events_range(self, start, end) -> list[dict]:
        connection = self._read_connection()
        try:
            event_rows = connection.execute(
                "SELECT * FROM events WHERE occurred_at IS NOT NULL AND occurred_at >= ? AND occurred_at < ? "
                "ORDER BY occurred_at",
                (start, end),
            ).fetchall()
            decoded_events = [_decode_event_row(event_row) for event_row in event_rows]
            return self._attach_steps_many(connection, decoded_events)
        finally:
            connection.close()

    def fetch_events_by_type_area_window(self, event_type: str, area: str, window_start, window_end) -> list[dict]:
        connection = self._read_connection()
        try:
            event_rows = connection.execute(
                "SELECT * FROM events WHERE classification = ? AND area = ? "
                "AND occurred_at IS NOT NULL AND occurred_at >= ? AND occurred_at < ? ORDER BY occurred_at",
                (event_type, area, window_start, window_end),
            ).fetchall()
            decoded_events = [_decode_event_row(event_row) for event_row in event_rows]
            return self._attach_steps_many(connection, decoded_events)
        finally:
            connection.close()

    def fetch_event_by_source_message(self, source: str, sender_identity: str, source_message_id: str) -> dict | None:
        ingestion_key = "\x1f".join((source, sender_identity, source_message_id))
        connection = self._read_connection()
        try:
            event_row = connection.execute("SELECT * FROM events WHERE ingestion_key = ?", (ingestion_key,)).fetchone()
            if event_row is None:
                return None
            return self._attach_steps(connection, _decode_event_row(event_row))
        finally:
            connection.close()

    def search_events(self, criteria: EventSearchCriteria) -> list[dict]:
        if criteria.order not in {"newest", "oldest"}:
            raise PersistenceError(f"unsupported event order: {criteria.order!r}")
        if not 1 <= criteria.limit <= 500:
            raise PersistenceError("event search limit must be between 1 and 500")

        where_sql, parameters, time_column = _search_where(criteria)
        direction = "DESC" if criteria.order == "newest" else "ASC"
        connection = self._read_connection()
        try:
            event_rows = connection.execute(
                f"SELECT * FROM events WHERE {where_sql} "
                f"ORDER BY {time_column} {direction}, event_id {direction} LIMIT ?",
                (*parameters, criteria.limit),
            ).fetchall()
            decoded_events = [_decode_event_row(event_row) for event_row in event_rows]
            return self._attach_steps_many(connection, decoded_events)
        finally:
            connection.close()

    def count_events(self, criteria: EventSearchCriteria) -> int:
        where_sql, parameters, _time_column = _search_where(criteria)
        connection = self._read_connection()
        try:
            return int(connection.execute(f"SELECT COUNT(*) FROM events WHERE {where_sql}", parameters).fetchone()[0])
        finally:
            connection.close()

    def aggregate_events(self, criteria: EventSearchCriteria, group_by: str) -> list[dict]:
        expression = _AGGREGATE_EXPRESSIONS.get(group_by)
        if expression is None:
            raise PersistenceError(f"unsupported event aggregation: {group_by!r}")

        where_sql, parameters, _time_column = _search_where(criteria)
        connection = self._read_connection()
        try:
            rows = connection.execute(
                f"SELECT {expression} AS group_value, COUNT(*) AS event_count "
                f"FROM events WHERE {where_sql} GROUP BY {expression} ORDER BY event_count DESC, group_value",
                parameters,
            ).fetchall()
            return [{"group": row["group_value"], "count": int(row["event_count"])} for row in rows]
        finally:
            connection.close()

    def fetch_event_time_boundary(self, criteria: EventSearchCriteria, *, latest: bool) -> str | None:
        where_sql, parameters, time_column = _search_where(criteria)
        aggregate = "MAX" if latest else "MIN"
        connection = self._read_connection()
        try:
            row = connection.execute(
                f"SELECT {aggregate}({time_column}) FROM events WHERE {where_sql} AND {time_column} IS NOT NULL",
                parameters,
            ).fetchone()
            return row[0] if row is not None else None
        finally:
            connection.close()


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
            summary_rows = connection.execute(
                f"SELECT summary_text, period_start, period_end, generated_at, event_index FROM {table_name} "
                "WHERE period_start < ? AND period_end > ? ORDER BY period_start",
                (end, start),
            ).fetchall()
            return [_decode_summary_row(summary_row) for summary_row in summary_rows]
        finally:
            connection.close()


    def read_user(self, telegram_identity: str) -> dict | None:
        connection = self._read_connection()
        try:
            user_row = connection.execute(
                "SELECT telegram_identity, permission_level FROM users WHERE telegram_identity = ?",
                (telegram_identity,),
            ).fetchone()
            return dict(user_row) if user_row is not None else None
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
            user_rows = connection.execute("SELECT telegram_identity, permission_level FROM users").fetchall()
            return [dict(user_row) for user_row in user_rows]
        finally:
            connection.close()


    def write_log_entry(self, trace_id: str | None, details: dict) -> None:
        timestamp = datetime.now(timezone.utc).isoformat()
        payload = json.dumps(details, default=str, ensure_ascii=False)

        def _do(connection: sqlite3.Connection) -> None:
            try:
                connection.execute(
                    "INSERT INTO log_entries (trace_id, timestamp, details) VALUES (?, ?, ?)",
                    (trace_id, timestamp, payload),
                )
                connection.commit()
            except sqlite3.Error as exc:
                connection.rollback()
                raise PersistenceError(f"failed to write log entry: {exc}") from exc

        self._submit_write(_do)

    def fetch_log_entries(self, trace_id: str) -> list[dict]:
        connection = self._read_connection()
        try:
            log_entry_rows = connection.execute(
                "SELECT id, trace_id, timestamp, details FROM log_entries WHERE trace_id = ? ORDER BY id",
                (trace_id,),
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


    def store_held_event(self, kind: str, hold: dict) -> str:
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
        resolved_by = resolution.get("resolved_by")
        resolved_at = resolution.get("resolved_at") or datetime.now(timezone.utc).isoformat()
        resolution_payload = {key: value for key, value in resolution.items() if key not in _HELD_EVENT_RESOLUTION_RESERVED_KEYS}

        def _do(connection: sqlite3.Connection) -> None:
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


    def fetch_notifications_since(self, since: int) -> list[dict]:
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
        if not conversation_id or role not in {"user", "assistant"} or not content:
            raise PersistenceError("conversation message requires a conversation_id, valid role, and content")
        if ttl_hours <= 0 or max_turns <= 0:
            return

        now = datetime.now(timezone.utc)
        cutoff = (now - timedelta(hours=ttl_hours)).isoformat()
        keep_messages = max_turns * 2

        def _do(connection: sqlite3.Connection) -> None:
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
