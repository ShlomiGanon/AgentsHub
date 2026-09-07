"""Public history facade for event writes, queries, and summaries."""

import sys

from history import contracts, event_pipeline, field_catalog, query, summaries
from history.field_catalog import EVENT_FIELD_CATALOG
from history.contracts import (
    ExtractionExecutionError,
    ExtractionResult,
    HistoryAnswer,
    HistoryQuerySpec,
    HistorySearchResult,
    HistoryQueryError,
    HistorySource,
    InitialEventEnvelope,
    PrecedentMatch,
    RetrievedSource,
    StepExecutionEnvelope,
    SummaryGenerationError,
)
from history.event_pipeline import (
    day_bounds,
    extract_event,
    month_bounds,
    parse_timestamp,
    record_event_outcome,
    record_event_data_update,
    record_event_state,
    record_extracted_fields,
    record_initial_event,
    record_step_execution,
    storage_timestamp,
    year_bounds,
)
from history.query import HistoryQueryService, find_precedents, retrieve_range
from history.summaries import SummaryScheduler, generate_summary

events = event_pipeline
extraction = event_pipeline
time_utils = event_pipeline
write = event_pipeline
scheduler = summaries
interface = sys.modules[__name__]
sys.modules[f"{__name__}.events"] = event_pipeline
sys.modules[f"{__name__}.extraction"] = event_pipeline
sys.modules[f"{__name__}.time_utils"] = event_pipeline
sys.modules[f"{__name__}.write"] = event_pipeline
sys.modules[f"{__name__}.scheduler"] = summaries
sys.modules[f"{__name__}.interface"] = sys.modules[__name__]

__all__ = [
    "ExtractionExecutionError",
    "EVENT_FIELD_CATALOG",
    "ExtractionResult",
    "HistoryAnswer",
    "HistoryQuerySpec",
    "HistorySearchResult",
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
    "record_event_data_update",
    "record_event_state",
    "record_extracted_fields",
    "record_initial_event",
    "record_step_execution",
    "retrieve_range",
    "storage_timestamp",
]
