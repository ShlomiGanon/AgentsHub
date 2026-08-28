"""The persistence interface (work_plan.md §2.7)."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Literal


class PersistenceError(Exception):
    """Base exception exposed by persistence implementations."""


class NotFoundError(PersistenceError):
    """The requested persisted record does not exist."""


@dataclass(frozen=True)
class EventSearchCriteria:
    time_start: str | None = None
    time_end: str | None = None
    time_basis: Literal["occurred_at", "received_at"] = "occurred_at"
    classifications: tuple[str, ...] = ()
    areas: tuple[str, ...] = ()
    outcomes: tuple[str, ...] = ()
    protocol_names: tuple[str, ...] = ()
    event_ids: tuple[str, ...] = ()
    risk_levels: tuple[str, ...] = ()
    order: Literal["newest", "oldest"] = "newest"
    limit: int = 50


class PersistenceInterface(ABC):
    def __init__(self, db_path: str):
        self.db_path = db_path


    @abstractmethod
    def append_event(self, event: dict) -> str:
        """Write a new event record; return its generated event ID."""

    @abstractmethod
    def update_event(self, event_id: str, updates: dict) -> None:
        """Merge `updates` onto the event identified by `event_id`."""

    @abstractmethod
    def fetch_event(self, event_id: str) -> dict | None:
        """Return the single event identified by `event_id`, or None."""

    @abstractmethod
    def fetch_events_range(self, start: Any, end: Any) -> list[dict]:
        """Return events whose occurrence timestamp falls in [start, end)."""

    @abstractmethod
    def fetch_events_by_type_area_window(self, event_type: str, area: str, window_start: Any, window_end: Any) -> list[dict]:
        """Return events matching both classification and area within a window — precedent search's read path."""

    @abstractmethod
    def search_events(self, criteria: EventSearchCriteria) -> list[dict]:
        """Return a bounded event list using only allowlisted filters."""

    @abstractmethod
    def count_events(self, criteria: EventSearchCriteria) -> int:
        """Count events using the constrained search vocabulary."""

    @abstractmethod
    def aggregate_events(self, criteria: EventSearchCriteria, group_by: str) -> list[dict]:
        """Count matching events grouped by one allowlisted field."""

    @abstractmethod
    def fetch_event_time_boundary(self, criteria: EventSearchCriteria, *, latest: bool) -> str | None:
        """Return a matching time boundary without loading event rows."""


    @abstractmethod
    def write_summary(self, level: str, summary: dict) -> None:
        """Write one period summary."""

    @abstractmethod
    def fetch_summaries_range(self, level: str, start: Any, end: Any) -> list[dict]:
        """Return summaries at `level` whose half-open period overlaps [start, end)."""


    @abstractmethod
    def store_held_event(self, kind: str, hold: dict) -> str:
        """Persist a hold."""

    @abstractmethod
    def list_held_events(self, kind: str) -> list[dict]:
        """Return every currently-unresolved hold of `kind`."""

    @abstractmethod
    def fetch_held_event(self, kind: str, event_id: str) -> dict | None:
        """Return the hold of `kind` created against `event_id`, or None."""

    @abstractmethod
    def resolve_held_event(self, kind: str, hold_id: str, resolution: dict) -> None:
        """Mark a hold resolved and record who resolved it and how."""

    # -- Notification log (work_plan.md §8.12) --------------------------

    @abstractmethod
    def fetch_notifications_since(self, since: int) -> list[dict]:
        """Return every notification-log row with `sequence_id > since`, in ascending order — `since=0` returns everything ever recorded."""


    @abstractmethod
    def read_user(self, telegram_identity: str) -> dict | None:
        """Return the user's record, or None if unregistered."""

    @abstractmethod
    def write_user(self, telegram_identity: str, permission_level: str) -> None:
        """Create or update a user's permission level."""

    @abstractmethod
    def delete_user(self, telegram_identity: str) -> None:
        """Remove a user."""

    @abstractmethod
    def list_users(self) -> list[dict]:
        """Return every registered user."""


    @abstractmethod
    def write_log_entry(self, trace_id: str | None, details: dict) -> None:
        """Persist one structured log record — the single write path `tools.logging_config`'s DB-backed handler funnels every `logger.*` call site through, so no call site ever talks to st..."""

    @abstractmethod
    def fetch_log_entries(self, trace_id: str) -> list[dict]:
        """Return every log entry recorded for `trace_id`, oldest first — each dict merging `details` back out with `id`/`trace_id`/ `timestamp`, reconstructing the full original record."""


def open_persistence(db_path: str) -> PersistenceInterface:
    """Construct the concrete backend for `db_path`."""

    from persistence.sqlite_store import SQLitePersistence

    return SQLitePersistence(db_path)
