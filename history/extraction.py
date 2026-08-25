"""Strict structured extraction from sensor and Telegram event text."""

import json
from dataclasses import dataclass
from typing import Callable

from history.time_utils import parse_timestamp
from tools.tracing import stage_context


class ExtractionExecutionError(Exception):
    """The model invocation or its structured response could not be used."""


@dataclass(frozen=True)
class ExtractionResult:
    classification: str | None
    classification_status: str
    area: str | None
    entities: tuple[str, ...]
    description: str | None
    severity: str | None
    occurred_at: str | None
    occurred_at_is_fallback: bool
    missing_fields: tuple[str, ...]


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
        # `stage_context` tags whatever real model call `model_invoker`
        # makes underneath (in production, `main_agent.process(...)` ->
        # `agents/adapter.py::invoke`, the one place model I/O is actually
        # logged) as "extraction" — no separate log call needed here,
        # which would otherwise duplicate the same interaction under a
        # second, differently-shaped record.
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
