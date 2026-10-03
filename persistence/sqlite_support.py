"""Shared SQLite row helpers used by the domain mixins."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from persistence.contracts import EventSearchCriteria, PersistenceError
from persistence.schema import SUMMARY_TABLE_NAMES

_STOP = object()

_GROUP_COLUMNS = (
    "chat_id, agent_name, label, created_at, auto_register, "
    "attendance_check_enabled, attendance_check_hour"
)

def _group_record(row) -> dict:
    """Decode a telegram_groups row into a dict with typed bools and hour."""

    result = dict(row)
    result["auto_register"] = bool(result["auto_register"])
    result["attendance_check_enabled"] = bool(result["attendance_check_enabled"])
    result["attendance_check_hour"] = int(result["attendance_check_hour"])
    return result

def _normalized_attendance_hour(value: int | None) -> int | None:
    """Validate an attendance hour is 0-23, or return None."""

    if value is None:
        return None
    hour = int(value)
    if hour < 0 or hour > 23:
        raise PersistenceError("attendance_check_hour must be between 0 and 23")
    return hour

_EVENT_COLUMNS = (
    "event_id",
    "received_at",
    "source",
    "sender_identity",
    "sender_permission_level",
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
    "report_text",
    "commander_alert_text",
    "availability_start",
    "availability_end",
    "absence_reason",
    "trace_id",
    "conversation_id",
    "deadline_at",
    "ingestion_key",
    "telegram_chat_id",
    "telegram_chat_type",
    "ack_message_id",
    "corrects_event_id",
    "retracted",
    "hold_escalation_alert_text",
)

_EVENT_JSON_COLUMNS = {"entities", "precedent_matched_event_ids"}

_EVENT_BOOL_COLUMNS = {"occurred_at_is_fallback", "clarification_held", "approval_held", "retracted"}

_EVENT_IMMUTABLE_COLUMNS = {
    "event_id", "received_at", "source", "sender_identity", "sender_permission_level",
    "source_message_id", "raw_text",
    "trace_id", "conversation_id", "deadline_at", "ingestion_key",
    "telegram_chat_id", "telegram_chat_type", "ack_message_id",
}

_UPDATABLE_EVENT_COLUMNS = frozenset(_EVENT_COLUMNS) - _EVENT_IMMUTABLE_COLUMNS

_HELD_EVENT_RESERVED_KEYS = {"hold_id", "event_id", "created_at"}

_HELD_EVENT_RESOLUTION_RESERVED_KEYS = {"resolved_by", "resolved_at"}

_OUTCOME_TO_NOTIFICATION_KINDS: dict[str, tuple[str, ...]] = {
    "succeeded": ("job_finished",),
    "declined": ("job_finished",),
    "failed": ("job_failed",),
    # An unresolved hold that nobody answered within the configured expiry window (item 8) --
    # same reporter-facing delivery as any other failure, via the existing job_failed path.
    "expired": ("job_failed",),
    "uncertain": ("job_finished", "uncertain_verdict", "uncertain_verdict_reporter"),
    "closed_on_precedent": ("job_finished", "precedent_closure"),
    "no_match_protocol": ("job_finished", "no_match_notice"),
    # A genuine completion, not a failure -- job_finished, same as "succeeded", to the
    # reporter's own chat only. The commander alert is its own separate notification kind
    # (resource_unavailable_alert), delivered only to each commander's private chat
    # (bot/interactions.py::notify_resource_unavailable_alert) -- never widened into
    # job_finished's own target_chat_ids, which would leak it into the reporter's chat.
    "handled_resource_unavailable": ("job_finished", "resource_unavailable_alert"),
}

def _encode_event_value(column: str, value):
    """Encode JSON and bool event columns for INSERT/UPDATE."""

    if column in _EVENT_JSON_COLUMNS and value is not None:
        return json.dumps(value)
    if column in _EVENT_BOOL_COLUMNS:
        return 1 if value else 0
    return value

def _decode_event_row(event_row: sqlite3.Row) -> dict:
    """Decode JSON and bool columns on an events row."""

    decoded = dict(event_row)
    for column in _EVENT_JSON_COLUMNS:
        if decoded.get(column) is not None:
            decoded[column] = json.loads(decoded[column])
    for column in _EVENT_BOOL_COLUMNS:
        decoded[column] = bool(decoded[column])
    return decoded

def _decode_step_row(step_row: sqlite3.Row) -> dict:
    """Decode JSON columns on an event_steps row."""

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
    """Decode the event_index JSON on a summary row."""

    decoded = dict(summary_row)
    if decoded.get("event_index") is not None:
        decoded["event_index"] = json.loads(decoded["event_index"])
    return decoded

_UPSERT_STEPS_SQL = """
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
            """

def _upsert_steps(connection: sqlite3.Connection, event_id: str, steps: list[dict]) -> None:
    """Replace or insert event_steps rows for this event_id."""

    payloads = []
    for step in steps:
        requested_status = step.get("status", "auto")
        status = (
            "succeeded" if step.get("result_text") is not None else "failed"
        ) if requested_status == "auto" else requested_status
        payloads.append({
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
        })
    if payloads:
        connection.executemany(_UPSERT_STEPS_SQL, payloads)

def _insert_notification(connection: sqlite3.Connection, kind: str, event_id: str) -> None:
    """Insert one notification_log row for this event."""

    connection.execute(
        "INSERT INTO notification_log (kind, event_id, created_at) VALUES (?, ?, ?)",
        (kind, event_id, datetime.now(timezone.utc).isoformat()),
    )

def _decode_held_event_row(hold_row: sqlite3.Row) -> dict:
    """Flatten a held_events row with its JSON payload and resolution."""

    decoded = dict(hold_row)
    payload = json.loads(decoded.pop("payload"))
    resolution_raw = decoded.pop("resolution")
    decoded["resolved"] = bool(decoded["resolved"])
    decoded["resolution"] = json.loads(resolution_raw) if resolution_raw is not None else None
    decoded.update(payload)
    return decoded

def _summary_table_name(level: str) -> str:
    """Return the summary table name for daily/monthly/yearly, or raise."""

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
    """Build the allowlisted WHERE clause, parameters, and time column for a search."""

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
