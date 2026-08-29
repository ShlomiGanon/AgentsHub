"""Passive contracts shared by history event, query, and summary services."""

from dataclasses import dataclass
from typing import Literal


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
    trace_id: str | None = None
    conversation_id: str | None = None
    deadline_at: str | None = None


@dataclass(frozen=True)
class StepExecutionEnvelope:
    step_index: int
    agent_name: str
    task_text: str
    allowed_tools: list[str]
    result_text: str | None
    attempt_count: int
    step_id: str = ""
    depends_on: tuple[str, ...] = ()


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


HistoryOperation = Literal[
    "latest",
    "event_details",
    "list",
    "count",
    "aggregate",
    "compare",
    "similar_cases",
    "narrative",
]
HistoryTimeBasis = Literal["occurred_at", "received_at"]
HistoryOrder = Literal["newest", "oldest"]
HistoryGroupBy = Literal["none", "classification", "area", "outcome", "protocol", "day", "month"]


@dataclass(frozen=True)
class HistoryQuerySpec:
    """A validated, model-independent description of a history lookup."""

    operation: HistoryOperation = "narrative"
    time_start: str | None = None
    time_end: str | None = None
    time_basis: HistoryTimeBasis = "occurred_at"
    classifications: tuple[str, ...] = ()
    areas: tuple[str, ...] = ()
    outcomes: tuple[str, ...] = ()
    protocol_names: tuple[str, ...] = ()
    event_ids: tuple[str, ...] = ()
    risk_levels: tuple[str, ...] = ()
    order: HistoryOrder = "newest"
    group_by: HistoryGroupBy = "none"
    limit: int = 50


@dataclass(frozen=True)
class HistorySearchResult:
    events: tuple[dict, ...] = ()
    total_count: int = 0
    aggregates: tuple[dict, ...] = ()
    truncated: bool = False


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
    applied_query: HistoryQuerySpec | None = None
    truncated: bool = False


class SummaryGenerationError(Exception):
    pass


@dataclass(frozen=True)
class EventFieldDefinition:
    """English meaning of one persisted event field, for response generation only —
    never persistence schema (docs/Next_Plan.md §4.6, §9)."""

    key: str
    label: str
    meaning: str
    category: Literal["narrative", "internal"]


@dataclass(frozen=True)
class SemanticEventView:
    """A response-only, English-labeled representation of one stored event, built
    from a persistence record and the field catalog (history/field_catalog.py).
    Does not change the SQLite schema. Only `category="narrative"` fields that
    are actually present on the record ever appear in `fields` — internal
    plumbing (trace_id, conversation_id, deadline_at, ingestion identity) never
    enters this view for any caller, regardless of role."""

    event_id: str
    fields: tuple[tuple[str, object], ...] = ()
    steps: tuple[dict, ...] = ()
