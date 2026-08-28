"""Strict structured extraction from sensor and Telegram event text."""

import json
from dataclasses import asdict
from datetime import date, datetime, time, timedelta, timezone
from typing import Callable

from history.contracts import ExtractionExecutionError, ExtractionResult, InitialEventEnvelope, StepExecutionEnvelope
from tools import stage_context


def _prompt(raw_text: str, source: str, received_at: str, event_types, areas) -> str:
    timestamp_rule = (
        "Set occurred_at to null; the caller supplies the sensor occurrence time."
        if source == "sensor"
        else f"Resolve occurred_at relative to received_at={received_at}; use null if it cannot be resolved."
    )

    return (
        "Extract this operational event into one JSON object with exactly these keys: "
        "classification, area, entities, description, severity, occurred_at. "
        f"classification must be one of {list(event_types)} or null. "
        f"area must be one of {list(areas)} or null. "
        "entities must be an array of strings. Do not guess missing values. "
        f"{timestamp_rule}\nEvent text:\n{raw_text}"
    )


def _strip_code_fence(raw_response: str) -> str:
    stripped = raw_response.strip()
    if not stripped.startswith("```"):
        return stripped

    lines = stripped.splitlines()
    if len(lines) < 3 or lines[-1].strip() != "```":
        return stripped

    return "\n".join(lines[1:-1]).strip()


def _optional_string(payload: dict, key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ExtractionExecutionError(f"extraction field '{key}' must be a string or null")
    return value


def extract_event(
    raw_text: str,
    source: str,
    received_at: str,
    event_type_registry,
    area_registry,
    model_invoker: Callable[[str], str] | None = None,
) -> ExtractionResult:
    if source not in {"sensor", "telegram"}:
        raise ValueError("source must be 'sensor' or 'telegram'")

    if model_invoker is None:
        raise ExtractionExecutionError("model_invoker is required for structured extraction")

    prompt = _prompt(
        raw_text,
        source,
        received_at,
        getattr(event_type_registry, "types", ()),
        getattr(area_registry, "areas", ()),
    )

    try:
        with stage_context("extraction"):
            raw_response = model_invoker(prompt)
    except Exception as exc:
        raise ExtractionExecutionError(f"model invocation failed: {exc}") from exc

    if not isinstance(raw_response, str):
        raise ExtractionExecutionError("model response must be text")

    try:
        payload = json.loads(_strip_code_fence(raw_response))
    except (json.JSONDecodeError, TypeError) as exc:
        raise ExtractionExecutionError("model response was not valid JSON") from exc

    if not isinstance(payload, dict):
        raise ExtractionExecutionError("model response must be one JSON object")

    classification = _optional_string(payload, "classification")
    area = _optional_string(payload, "area")
    description = _optional_string(payload, "description")
    severity = _optional_string(payload, "severity")
    model_occurred_at = _optional_string(payload, "occurred_at")

    entities_value = payload.get("entities")
    if entities_value is None:
        entities = ()
    elif isinstance(entities_value, list) and all(isinstance(item, str) for item in entities_value):
        entities = tuple(entities_value)
    else:
        raise ExtractionExecutionError("extraction field 'entities' must be an array of strings")

    if classification is not None and not event_type_registry.is_valid(classification):
        classification = None

    if area is not None and not area_registry.is_valid(area):
        area = None

    occurred_at = received_at if source == "sensor" else model_occurred_at

    if source == "telegram" and occurred_at is not None:
        try:
            parse_timestamp(occurred_at)
        except (TypeError, ValueError) as exc:
            raise ExtractionExecutionError("extraction field 'occurred_at' must be an ISO-8601 timestamp or null") from exc

    missing = []
    for field_name, value in (
        ("classification", classification),
        ("area", area),
        ("description", description),
        ("severity", severity),
        ("occurred_at", occurred_at),
    ):
        if value is None:
            missing.append(field_name)

    if not entities:
        missing.append("entities")

    return ExtractionResult(
        classification=classification,
        classification_status="resolved" if classification is not None else "unresolved",
        area=area,
        entities=entities,
        description=description,
        severity=severity,
        occurred_at=occurred_at,
        occurred_at_is_fallback=False,
        missing_fields=tuple(missing),
    )


UTC = timezone.utc


def parse_timestamp(value: str) -> datetime:
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"

    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def storage_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).replace(tzinfo=None).isoformat(timespec="seconds")


def day_bounds(value: datetime) -> tuple[datetime, datetime]:
    start = datetime.combine(value.date(), time.min, tzinfo=UTC)
    return start, start + timedelta(days=1)


def month_bounds(value: datetime) -> tuple[datetime, datetime]:
    start = datetime(value.year, value.month, 1, tzinfo=UTC)
    if value.month == 12:
        end = datetime(value.year + 1, 1, 1, tzinfo=UTC)
    else:
        end = datetime(value.year, value.month + 1, 1, tzinfo=UTC)
    return start, end


def year_bounds(value: datetime) -> tuple[datetime, datetime]:
    return datetime(value.year, 1, 1, tzinfo=UTC), datetime(value.year + 1, 1, 1, tzinfo=UTC)


def add_month(value: datetime) -> datetime:
    return month_bounds(value)[1]


def iter_days(start: datetime, end: datetime):
    cursor = day_bounds(start)[0]
    while cursor < end:
        yield cursor, cursor + timedelta(days=1)
        cursor += timedelta(days=1)


def iter_months(start: datetime, end: datetime):
    cursor = month_bounds(start)[0]
    while cursor < end:
        next_cursor = add_month(cursor)
        yield cursor, next_cursor
        cursor = next_cursor


def iter_years(start: datetime, end: datetime):
    cursor = year_bounds(start)[0]
    while cursor < end:
        next_cursor = datetime(cursor.year + 1, 1, 1, tzinfo=UTC)
        yield cursor, next_cursor
        cursor = next_cursor


VALID_OUTCOMES = frozenset(
    {"succeeded", "failed", "uncertain", "closed_on_precedent", "declined", "no_match_protocol"}
)

STATE_UPDATE_FIELDS = frozenset(
    {
        "classification",
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
    }
)


def record_initial_event(persistence, envelope: InitialEventEnvelope) -> str:
    if envelope.source not in {"sensor", "telegram"}:
        raise ValueError("source must be 'sensor' or 'telegram'")
    if not envelope.raw_text:
        raise ValueError("raw_text must not be empty")
    if not envelope.received_at:
        raise ValueError("received_at must not be empty")
    if not envelope.sender_identity:
        raise ValueError("sender_identity must not be empty")
    return persistence.append_event(asdict(envelope))


def record_extracted_fields(
    persistence,
    event_id: str,
    extraction_result,
    scheduler=None,
    *,
    source: str | None = None,
    received_at: str | None = None,
) -> None:
    if scheduler is not None and (source is None or received_at is None):
        raise ValueError("source and received_at are required when scheduler is provided")

    persistence.update_event(
        event_id,
        {
            "classification": extraction_result.classification,
            "area": extraction_result.area,
            "entities": list(extraction_result.entities) or None,
            "description": extraction_result.description,
            "severity": extraction_result.severity,
            "occurred_at": extraction_result.occurred_at,
            "occurred_at_is_fallback": extraction_result.occurred_at_is_fallback,
        },
    )

    if scheduler is not None:
        scheduler.notify_event_written(event_id, source, extraction_result.occurred_at, received_at)


def record_step_execution(persistence, event_id: str, step: StepExecutionEnvelope) -> None:
    if step.step_index < 0:
        raise ValueError("step_index must not be negative")
    if step.attempt_count < 0:
        raise ValueError("attempt_count must not be negative")
    persistence.update_event(event_id, {"steps": [asdict(step)]})


def record_event_outcome(
    persistence,
    event_id: str,
    outcome: str,
    failure_reason: str | None = None,
    insight_text: str | None = None,
) -> None:
    if outcome not in VALID_OUTCOMES:
        raise ValueError(f"invalid event outcome: '{outcome}'")
    persistence.update_event(
        event_id,
        {"outcome": outcome, "outcome_failure_reason": failure_reason, "insight_text": insight_text},
    )


def record_event_state(persistence, event_id: str, updates: dict) -> None:
    rejected = set(updates) - STATE_UPDATE_FIELDS
    if rejected:
        raise ValueError(f"event state update contains forbidden field(s): {', '.join(sorted(rejected))}")
    persistence.update_event(event_id, dict(updates))
