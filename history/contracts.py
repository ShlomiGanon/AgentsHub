"""Passive contracts shared by history event, query, and summary services."""

from dataclasses import dataclass


class ExtractionExecutionError(Exception):
    pass


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


@dataclass(frozen=True)
class RetrievedSource:
    level: str
    period_start: str
    period_end: str
    source_id: str
    content: object
    matched_event_ids: tuple[str, ...]


@dataclass(frozen=True)
class PrecedentMatch:
    event_id: str
    classification: str
    area: str
    occurred_at: str
    protocol_name: str | None
    steps_summary: list[dict]
    outcome: str | None
    resolved: bool


class HistoryQueryError(Exception):
    pass


@dataclass(frozen=True)
class HistorySource:
    level: str
    period_start: str
    period_end: str
    source_id: str


@dataclass(frozen=True)
class HistoryAnswer:
    answer: str
    sources_used: tuple[HistorySource, ...]
    time_start: str | None
    time_end: str | None
    total_events_matched: int


class SummaryGenerationError(Exception):
    pass
