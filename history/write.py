"""Durable, incremental event writes for the history subsystem."""

from dataclasses import asdict, dataclass


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
# `classification` was added in Mission 6 (§6.11): resuming a clarification
# hold means writing the commander's chosen classification onto the event
# itself, not just recording it as `clarification_chosen_classification` —
# and re-running full extraction would discard the commander's decision
# and any already-resolved fields (§6.2's own explicit "resume at risk
# assessment, not at extraction" rule). `record_extracted_fields` isn't a
# fit either — it unconditionally overwrites every extracted field, which
# would null out area/description/severity that were already correctly
# resolved. This is a state transition like any other in this set.


@dataclass(frozen=True)
class InitialEventEnvelope:
    raw_text: str
    source: str
    received_at: str
    sender_identity: str
    source_message_id: str | None = None
    occurred_at: str | None = None
    occurred_at_is_fallback: bool = False
    event_id: str | None = None


@dataclass(frozen=True)
class StepExecutionEnvelope:
    step_index: int
    agent_name: str
    task_text: str
    allowed_tools: list[str]
    result_text: str | None
    attempt_count: int


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

    if scheduler is None:
        return

    scheduler.notify_event_written(
        event_id,
        source,
        extraction_result.occurred_at,
        received_at,
    )


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
        {
            "outcome": outcome,
            "outcome_failure_reason": failure_reason,
            "insight_text": insight_text,
        },
    )


def record_event_state(persistence, event_id: str, updates: dict) -> None:
    rejected = set(updates) - STATE_UPDATE_FIELDS
    if rejected:
        raise ValueError(f"event state update contains forbidden field(s): {', '.join(sorted(rejected))}")

    persistence.update_event(event_id, dict(updates))
