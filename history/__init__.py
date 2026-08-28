"""Public history facade for event writes, queries, and summaries."""

import sys

from history import contracts, events, query, summaries
from history.contracts import (
    ExtractionExecutionError,
    ExtractionResult,
    HistoryAnswer,
    HistoryQueryError,
    HistorySource,
    InitialEventEnvelope,
    PrecedentMatch,
    RetrievedSource,
    StepExecutionEnvelope,
    SummaryGenerationError,
)
from history.events import (
    day_bounds,
    extract_event,
    month_bounds,
    parse_timestamp,
    record_event_outcome,
    record_event_state,
    record_extracted_fields,
    record_initial_event,
    record_step_execution,
    storage_timestamp,
    year_bounds,
)
from history.query import HistoryQueryService, find_precedents, retrieve_range
from history.summaries import SummaryScheduler, generate_summary

extraction = events
time_utils = events
write = events
scheduler = summaries
interface = sys.modules[__name__]
sys.modules[f"{__name__}.extraction"] = events
sys.modules[f"{__name__}.time_utils"] = events
sys.modules[f"{__name__}.write"] = events
sys.modules[f"{__name__}.scheduler"] = summaries
sys.modules[f"{__name__}.interface"] = sys.modules[__name__]

__all__ = [
    "ExtractionExecutionError",
    "ExtractionResult",
    "HistoryAnswer",
    "HistoryQueryError",
    "HistoryQueryService",
    "HistorySource",
    "InitialEventEnvelope",
    "PrecedentMatch",
    "RetrievedSource",
    "StepExecutionEnvelope",
    "SummaryGenerationError",
    "SummaryScheduler",
    "extract_event",
    "find_precedents",
    "generate_summary",
    "record_event_outcome",
    "record_event_state",
    "record_extracted_fields",
    "record_initial_event",
    "record_step_execution",
    "retrieve_range",
    "storage_timestamp",
]
