"""Event and summary rows for SQLitePersistence."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone

from persistence.contracts import EventSearchCriteria, NotFoundError, PersistenceError
from persistence.schema import SUMMARY_TABLE_NAMES

from persistence.sqlite_support import (
    _AGGREGATE_EXPRESSIONS,
    _EVENT_BOOL_COLUMNS,
    _EVENT_COLUMNS,
    _EVENT_JSON_COLUMNS,
    _OUTCOME_TO_NOTIFICATION_KINDS,
    _UPDATABLE_EVENT_COLUMNS,
    _decode_event_row,
    _decode_step_row,
    _decode_summary_row,
    _encode_event_value,
    _insert_notification,
    _search_where,
    _summary_table_name,
    _upsert_steps,
)


class SqliteEventsMixin:
    def _attach_steps(self, connection: sqlite3.Connection, event: dict) -> dict:
        """Load event_steps rows onto this event dict."""

        step_rows = connection.execute(
            "SELECT * FROM event_steps WHERE event_id = ? ORDER BY step_index",
            (event["event_id"],),
        ).fetchall()
        event["steps"] = [_decode_step_row(step_row) for step_row in step_rows]
        return event

    def _attach_steps_many(self, connection: sqlite3.Connection, events: list[dict]) -> list[dict]:
        """Load event_steps for many events in one query."""

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
        """Insert an events row and optional event_steps; return the event_id."""

        event_id = event.get("event_id") or uuid.uuid4().hex
        event_row = {column: _encode_event_value(column, event.get(column)) for column in _EVENT_COLUMNS}
        event_row["event_id"] = event_id
        # Plain dictionary callers from before migration 17 omit the immutable
        # snapshot. Match the schema's safe legacy default instead of inserting
        # an explicit NULL into the NOT NULL column.
        event_row["sender_permission_level"] = event.get("sender_permission_level") or "viewer"
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
        """Merge allowed columns onto an events row and replace steps when given."""

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
        """Return one events row plus its steps, or None."""

        connection = self._read_connection()
        try:
            event_row = connection.execute("SELECT * FROM events WHERE event_id = ?", (event_id,)).fetchone()
            if event_row is None:
                return None
            return self._attach_steps(connection, _decode_event_row(event_row))
        finally:
            connection.close()

    def fetch_events_range(self, start, end) -> list[dict]:
        # Same fix as `fetch_events_by_type_area_window` (DIAGNOSTIC_FINDINGS.MD
        # A.2): this is `history.query.retrieve_range`'s raw-event fallback for
        # a day/month/year with no rolled-up summary — used by both precedent
        # lookback and the legacy narrative `query()` path, and by
        # `history/summaries.py`'s own summary generation. An event with an
        # unresolved occurred_at used to be silently excluded from all three.
        """Events whose occurred_at or received_at falls in [start, end)."""

        connection = self._read_connection()
        try:
            event_rows = connection.execute(
                "SELECT * FROM events WHERE COALESCE(occurred_at, received_at) >= ? "
                "AND COALESCE(occurred_at, received_at) < ? ORDER BY COALESCE(occurred_at, received_at)",
                (start, end),
            ).fetchall()
            decoded_events = [_decode_event_row(event_row) for event_row in event_rows]
            return self._attach_steps_many(connection, decoded_events)
        finally:
            connection.close()

    def fetch_events_by_type_area_window(self, event_type: str, area: str, window_start, window_end) -> list[dict]:
        # Precedent-lookback fix, same root cause as the recency fix
        # (DIAGNOSTIC_FINDINGS.MD A.2): a candidate event whose occurred_at is
        # unresolved used to be silently excluded by the old
        # `occurred_at IS NOT NULL` filter. COALESCE falls back to
        # received_at (never null) instead of dropping such events.
        """Events matching classification and area inside a time window."""

        connection = self._read_connection()
        try:
            event_rows = connection.execute(
                "SELECT * FROM events WHERE classification = ? AND area = ? "
                "AND COALESCE(occurred_at, received_at) >= ? AND COALESCE(occurred_at, received_at) < ? "
                "ORDER BY COALESCE(occurred_at, received_at)",
                (event_type, area, window_start, window_end),
            ).fetchall()
            decoded_events = [_decode_event_row(event_row) for event_row in event_rows]
            return self._attach_steps_many(connection, decoded_events)
        finally:
            connection.close()

    def fetch_event_by_source_message(self, source: str, sender_identity: str, source_message_id: str) -> dict | None:
        """The events row for this source/sender/message id, if ingested already."""

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
        """Bounded events list using only allowlisted search filters."""

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
        """Count events matching the search criteria."""

        where_sql, parameters, _time_column = _search_where(criteria)
        connection = self._read_connection()
        try:
            return int(connection.execute(f"SELECT COUNT(*) FROM events WHERE {where_sql}", parameters).fetchone()[0])
        finally:
            connection.close()

    def aggregate_events(self, criteria: EventSearchCriteria, group_by: str) -> list[dict]:
        """Count matching events grouped by one allowlisted field."""

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
        """Earliest or latest matching timestamp, without loading rows."""

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
        """Upsert one daily/monthly/yearly summary row."""

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
        """Summary rows whose period overlaps [start, end)."""

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
