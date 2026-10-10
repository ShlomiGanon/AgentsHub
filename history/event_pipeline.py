"""Event extraction, timestamp handling, and history writes."""

import json
import logging
from dataclasses import asdict
from datetime import date, datetime, time, timedelta, timezone
from typing import Callable

from history.contracts import ExtractionExecutionError, ExtractionResult, InitialEventEnvelope, StepExecutionEnvelope
from tools import get_trace_id, stage_context

logger = logging.getLogger(__name__)


def _area_choices(areas, area_labels=None) -> list[str]:
    """Show each area id with its catalog label when the profile has one."""

    labels = area_labels or {}
    choices = []
    for area in areas:
        label = labels.get(area)
        choices.append(f"{area} ({label})" if label else str(area))
    return choices


def _prompt(raw_text: str, source: str, received_at: str, event_types, areas, event_type_descriptions=None, area_labels=None) -> str:
    """Prompt."""

    timestamp_rule = (
        "Set occurred_at to null; the caller supplies the sensor occurrence time."
        if source == "sensor"
        else f"Resolve occurred_at relative to received_at={received_at}; use null if it cannot be resolved."
    )

    # Optional, profile-declared English descriptions next to the event type
    # names they belong to, so classification sees more than the bare type list.
    # A type with no
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
        "Classify the message by its primary operational purpose, not by an isolated keyword "
        "or a secondary/incidental clause. A detail that merely supplies context, reassurance, "
        "or background must not become the classification. If the primary update does not fit "
        "any listed event type, set classification to null even when a secondary phrase resembles "
        "one of the types. "
        f"{descriptions_block}"
        f"area must be one of {_area_choices(areas, area_labels)} or null. "
        "When the report names a place inside a listed area, or uses that area's label, "
        "set area to the listed id. "
        "entities must be an array of strings — list every identifier the report refers to, e.g. "
        "equipment or unit identifiers. Do not guess missing values. availability_start and "
        "availability_end are only present when the report is someone stating their own "
        "unavailability and they gave a time interval — ISO-8601 timestamps, or null if no interval "
        "was stated; never infer or estimate one. absence_reason is the reporter's own stated reason "
        "for being unavailable, or null if none was given. Contextual or incidental wording about "
        "availability is not an absence and must not be put in absence_reason; use null unless the "
        "report explicitly says the reporter is unavailable, absent, cannot attend, or otherwise "
        "will not participate. "
        f"{timestamp_rule}\nEvent text:\n{raw_text}"
    )


def _strip_code_fence(raw_response: str) -> str:
    """Strip code fence."""

    stripped = raw_response.strip()
    if not stripped.startswith("```"):
        return stripped

    lines = stripped.splitlines()
    if len(lines) < 3 or lines[-1].strip() != "```":
        return stripped

    return "\n".join(lines[1:-1]).strip()


def _log_optional_field_dropped(key: str, received_value: object) -> None:
    """Drop one malformed optional field and log its name, never the raw value."""

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

    Every field this is called for is optional here; a type's required-fields
    gate runs later. A wrong-type value is dropped and logged, never invented."""

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
    """Extract event."""

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
        getattr(area_registry, "labels", None),
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

    return extraction_result_from_payload(
        payload, source, received_at, event_type_registry, area_registry, raw_text=raw_text,
    )


def extraction_result_from_payload(
    payload: dict,
    source: str,
    received_at: str,
    event_type_registry,
    area_registry,
    raw_text: str = "",
) -> ExtractionResult:
    """Extraction result from payload."""

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
        # A malformed entities value is dropped; it must not reject the whole report.
        _log_optional_field_dropped("entities", entities_value)
        entities = ()

    if classification is not None and not event_type_registry.is_valid(classification):
        classification = None

    if area is not None:
        if hasattr(area_registry, "resolve"):
            area = area_registry.resolve(area)
        elif not area_registry.is_valid(area):
            area = None
    # A place named in the report still counts when the model leaves area empty.
    if area is None and hasattr(area_registry, "resolve"):
        area = area_registry.resolve(f"{raw_text} {description or ''}".strip())

    occurred_at = received_at if source == "sensor" else model_occurred_at

    if source == "telegram" and occurred_at is not None:
        try:
            parse_timestamp(occurred_at)
        except (TypeError, ValueError) as exc:
            raise ExtractionExecutionError("extraction field 'occurred_at' must be an ISO-8601 timestamp or null") from exc

    # availability_start/availability_end are optional timestamps — unlike
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
    """Parse timestamp."""

    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"

    parsed_timestamp = datetime.fromisoformat(normalized)
    if parsed_timestamp.tzinfo is None:
        parsed_timestamp = parsed_timestamp.replace(tzinfo=UTC)
    return parsed_timestamp.astimezone(UTC)


def storage_timestamp(value: datetime) -> str:
    """Storage timestamp."""

    return value.astimezone(UTC).replace(tzinfo=None).isoformat(timespec="seconds")


def day_bounds(value: datetime) -> tuple[datetime, datetime]:
    """Day bounds."""

    start = datetime.combine(value.date(), time.min, tzinfo=UTC)
    return start, start + timedelta(days=1)


def month_bounds(value: datetime) -> tuple[datetime, datetime]:
    """Month bounds."""

    start = datetime(value.year, value.month, 1, tzinfo=UTC)
    if value.month == 12:
        end = datetime(value.year + 1, 1, 1, tzinfo=UTC)
    else:
        end = datetime(value.year, value.month + 1, 1, tzinfo=UTC)
    return start, end


def year_bounds(value: datetime) -> tuple[datetime, datetime]:
    """Year bounds."""

    return datetime(value.year, 1, 1, tzinfo=UTC), datetime(value.year + 1, 1, 1, tzinfo=UTC)


def add_month(value: datetime) -> datetime:
    """Add month."""

    return month_bounds(value)[1]


def iter_days(start: datetime, end: datetime):
    """Iter days."""

    cursor = day_bounds(start)[0]
    while cursor < end:
        yield cursor, cursor + timedelta(days=1)
        cursor += timedelta(days=1)


def iter_months(start: datetime, end: datetime):
    """Iter months."""

    cursor = month_bounds(start)[0]
    while cursor < end:
        next_cursor = add_month(cursor)
        yield cursor, next_cursor
        cursor = next_cursor


def iter_years(start: datetime, end: datetime):
    """Iter years."""

    cursor = year_bounds(start)[0]
    while cursor < end:
        next_cursor = datetime(cursor.year + 1, 1, 1, tzinfo=UTC)
        yield cursor, next_cursor
        cursor = next_cursor


VALID_OUTCOMES = frozenset(
    {
        "succeeded", "failed", "uncertain", "closed_on_precedent", "declined", "no_match_protocol",
        # The report/request was genuinely handled -- deterministically detected, from the DB,
        # not the model's wording -- but a resource it needed was unavailable. Never "failed":
        # commanders are alerted with concrete alternatives (orchestrator/flows.py's
        # `_finish_protocol_assessment`) and decide from there; nothing is auto-retried.
        "handled_resource_unavailable",
        # An unresolved hold (clarification/approval/event_data) that nobody answered within the
        # configured expiry window (SettingsStore.get_hold_expiry_hours) -- closed automatically,
        # not by any human decision or model judgment (bot/background_services.py's hold sweep).
        "expired",
    }
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
        "corrects_event_id",
        "retracted",
        "hold_escalation_alert_text",
    }
)

EVENT_DATA_UPDATE_FIELDS = frozenset(
    {
        "classification", "area", "entities", "description", "severity", "occurred_at", "occurred_at_is_fallback",
        # A reporter's follow-up can fill these through the event_data reply path.
        "availability_start", "availability_end", "absence_reason",
    }
)


def record_initial_event(persistence, envelope: InitialEventEnvelope) -> str:
    """Record initial event."""

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
    """Record extracted fields."""

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
    """Record step execution."""

    record_step_executions(persistence, event_id, (step,))


def record_step_executions(persistence, event_id: str, steps: tuple[StepExecutionEnvelope, ...] | list[StepExecutionEnvelope]) -> None:
    """Record step executions."""

    if not steps:
        return
    payloads = []
    for step in steps:
        if step.step_index < 0:
            raise ValueError("step_index must not be negative")
        if step.attempt_count < 0:
            raise ValueError("attempt_count must not be negative")
        payloads.append(asdict(step))
    persistence.update_event(event_id, {"steps": payloads})


def record_event_outcome(
    persistence,
    event_id: str,
    outcome: str,
    failure_reason: str | None = None,
    insight_text: str | None = None,
    report_text: str | None = None,
    commander_alert_text: str | None = None,
) -> None:
    """Record event outcome."""

    if outcome not in VALID_OUTCOMES:
        raise ValueError(f"invalid event outcome: '{outcome}'")
    persistence.update_event(
        event_id,
        {
            "outcome": outcome,
            "outcome_failure_reason": failure_reason,
            "insight_text": insight_text,
            "report_text": report_text,
            "commander_alert_text": commander_alert_text,
        },
    )


def record_event_state(persistence, event_id: str, updates: dict) -> None:
    """Record event state."""

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
