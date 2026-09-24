"""Event extraction, timestamp handling, and history writes."""

import json
import logging
from dataclasses import asdict
from datetime import date, datetime, time, timedelta, timezone
from typing import Callable

from history.contracts import ExtractionExecutionError, ExtractionResult, InitialEventEnvelope, StepExecutionEnvelope
from tools import get_trace_id, stage_context

logger = logging.getLogger(__name__)


def _prompt(raw_text: str, source: str, received_at: str, event_types, areas, event_type_descriptions=None) -> str:
    timestamp_rule = (
        "Set occurred_at to null; the caller supplies the sensor occurrence time."
        if source == "sensor"
        else f"Resolve occurred_at relative to received_at={received_at}; use null if it cannot be resolved."
    )

    # Optional, profile-declared English descriptions next to the event type
    # names they belong to (docs/bar_improves.md's follow-up to Stage 4:
    # event-type descriptions previously had no structural home, so
    # classification only ever saw the bare type-name list). A type with no
    # declared description still appears in the list above, name only,
    # exactly as before — this block is additive and empty when the active
    # profile declares no EVENT_TYPE_DESCRIPTIONS at all, which is what
    # keeps this prompt byte-for-byte unchanged for every profile that
    # doesn't use the new attribute.
    event_types_list = list(event_types)
    descriptions_block = ""
    if event_type_descriptions:
        described = [
            f"{event_type}: {event_type_descriptions[event_type]}"
            for event_type in event_types_list
            if event_type in event_type_descriptions
        ]
        if described:
            descriptions_block = "Event type descriptions: " + " | ".join(described) + ". "

    return (
        "Extract this operational event into one JSON object with exactly these keys: "
        "classification, area, entities, description, severity, occurred_at, availability_start, "
        "availability_end, absence_reason. "
        f"classification must be one of {event_types_list} or null. "
        f"{descriptions_block}"
        f"area must be one of {list(areas)} or null. "
        "entities must be an array of strings — list every identifier the report refers to, e.g. "
        "equipment or unit identifiers. Do not guess missing values. availability_start and "
        "availability_end are only present when the report is someone stating their own "
        "unavailability and they gave a time interval — ISO-8601 timestamps, or null if no interval "
        "was stated; never infer or estimate one. absence_reason is the reporter's own stated reason "
        "for being unavailable, or null if none was given. "
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


def _log_optional_field_dropped(key: str, received_value: object) -> None:
    """Stage 1 (docs/bar_improves.md): a malformed OPTIONAL extracted field is
    dropped, never the whole report. Log only the field name and the received
    Python type — never the value itself, which may carry the reporter's raw
    text."""

    logger.info(
        "extraction optional field dropped",
        extra={
            "event": "extraction_optional_field_dropped",
            "field": key,
            "received_type": type(received_value).__name__,
            "trace_id": get_trace_id(),
        },
    )


def _optional_string(payload: dict, key: str) -> str | None:
    """Return `payload[key]` as a string, or `None`.

    Every field this is called for (classification, area, description,
    severity, occurred_at) is optional in the domain vocabulary
    (docs/vocabulary.md) — required-ness for a *specific* event type is
    enforced later, downstream, by the event-type-required-fields gate
    (`profiles.contracts.EventTypeRegistry.required_fields_for`), not here.
    A `None` value is a normal, expected "not extracted" result and is
    returned as-is. A non-null value of the wrong type (the model returned an
    object or a list where a string was expected) used to reject the entire
    report; it is now normalized to `None` and logged instead (Stage 1,
    docs/bar_improves.md) — we never invent a value the source did not
    establish, so dropping is the only alternative to full rejection. This
    does not change how an out-of-registry classification/area value (a
    correctly-typed string just not in the profile's own list) is handled —
    that check runs afterward, unchanged, in `extract_event`."""

    extracted_value = payload.get(key)
    if extracted_value is None:
        return None
    if not isinstance(extracted_value, str):
        _log_optional_field_dropped(key, extracted_value)
        return None
    return extracted_value


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
        getattr(event_type_registry, "descriptions", None),
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
    availability_start = _optional_string(payload, "availability_start")
    availability_end = _optional_string(payload, "availability_end")
    absence_reason = _optional_string(payload, "absence_reason")

    entities_value = payload.get("entities")
    if entities_value is None:
        entities = ()
    elif isinstance(entities_value, list) and all(isinstance(item, str) for item in entities_value):
        entities = tuple(entities_value)
    else:
        # Stage 1 (docs/bar_improves.md): entities is also an OPTIONAL field —
        # a malformed value (not a list of strings) is dropped, not treated as
        # a reason to reject the whole report.
        _log_optional_field_dropped("entities", entities_value)
        entities = ()

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

    # Stage 3 (docs/bar_improves.md): availability_start/availability_end are
    # OPTIONAL timestamp fields, same as the other fields above — unlike
    # occurred_at (which has a sensor fallback and pre-existing behavior we
    # must not change), an unparseable value here is treated the same way
    # Stage 1 treats any other malformed optional field: dropped and logged,
    # never rejecting the whole report. We never guess a time range that was
    # not stated — the required-fields gate is what asks the reporter for it.
    if availability_start is not None:
        try:
            availability_start = storage_timestamp(parse_timestamp(availability_start))
        except (TypeError, ValueError):
            _log_optional_field_dropped("availability_start", availability_start)
            availability_start = None
    if availability_end is not None:
        try:
            availability_end = storage_timestamp(parse_timestamp(availability_end))
        except (TypeError, ValueError):
            _log_optional_field_dropped("availability_end", availability_end)
            availability_end = None

    missing = []
    for field_name, extracted_value in (
        ("classification", classification),
        ("area", area),
        ("description", description),
        ("severity", severity),
        ("occurred_at", occurred_at),
        ("availability_start", availability_start),
        ("availability_end", availability_end),
        ("absence_reason", absence_reason),
    ):
        if extracted_value is None:
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
        availability_start=availability_start,
        availability_end=availability_end,
        absence_reason=absence_reason,
    )


UTC = timezone.utc


def parse_timestamp(value: str) -> datetime:
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"

    parsed_timestamp = datetime.fromisoformat(normalized)
    if parsed_timestamp.tzinfo is None:
        parsed_timestamp = parsed_timestamp.replace(tzinfo=UTC)
    return parsed_timestamp.astimezone(UTC)


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

EVENT_DATA_UPDATE_FIELDS = frozenset(
    {
        "classification", "area", "entities", "description", "severity", "occurred_at", "occurred_at_is_fallback",
        # Stage 3, docs/bar_improves.md: lets a reporter's follow-up reply
        # fill these in through the same existing event_data reply path.
        "availability_start", "availability_end", "absence_reason",
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
    if envelope.sender_permission_level not in {"viewer", "commander"}:
        raise ValueError("sender_permission_level must be 'viewer' or 'commander'")
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
            "availability_start": extraction_result.availability_start,
            "availability_end": extraction_result.availability_end,
            "absence_reason": extraction_result.absence_reason,
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


def record_event_data_update(persistence, event_id: str, updates: dict) -> None:
    """Merge validated reporter-supplied facts into an existing event."""

    rejected = set(updates) - EVENT_DATA_UPDATE_FIELDS
    if rejected:
        raise ValueError(f"event data update contains forbidden field(s): {', '.join(sorted(rejected))}")
    persistence.update_event(event_id, dict(updates))
