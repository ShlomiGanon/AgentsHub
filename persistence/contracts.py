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
    # Ownership scoping (docs/Next_Plan.md §5 decision record): when set, restricts
    # the search to events submitted by exactly this identity. None (the default)
    # applies no such restriction — existing callers are unaffected. No schema
    # change: `sender_identity` is an existing `events` column (persistence/schema.py).
    sender_identity: str | None = None
    scenario_id: str | None = None
    scenario_run_id: str | None = None
    order: Literal["newest", "oldest"] = "newest"
    limit: int = 50


@dataclass(frozen=True)
class ConversationEventLink:
    """Typed, persisted operational referent for a conversation follow-up."""

    conversation_id: str
    event_id: str
    action_state: str | None
    outcome: str | None
    latest_user_visible_result: str | None
    failure_reason: str | None
    timestamp: str
    protocol_name: str | None
    tool_receipts: tuple[dict, ...] = ()


@dataclass(frozen=True)
class EventFinalization:
    """Atomic result of an event-terminalization attempt."""

    event_id: str
    event_changed: bool
    previous_outcome: str | None
    outcome: str | None
    resolved_hold_ids: tuple[str, ...] = ()


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
    def fetch_event_by_source_message(self, source: str, sender_identity: str, source_message_id: str) -> dict | None:
        """Return an idempotently ingested event, if present."""

    @abstractmethod
    def list_expired_events(self, now: str, limit: int = 100) -> list[dict]:
        """Return bounded, deterministic candidates whose open outcome deadline has passed."""

    @abstractmethod
    def finalize_event_if_open(
        self,
        event_id: str,
        outcome: str,
        failure_reason: str | None = None,
        *,
        insight_text: str | None = None,
        action_state: str | None = None,
        action_failure_reason: str | None = None,
        resolved_by: str | None = None,
        resolution: dict | None = None,
        finalized_at: str | None = None,
        emit_notification: bool = True,
    ) -> EventFinalization:
        """Atomically finalize an open event and optionally resolve its holds.

        The write is compare-and-set against ``outcome IS NULL``.  A terminal
        event is never overwritten, while an attached unresolved hold may
        still be resolved in the same transaction when requested.
        """

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
    def wait_for_notifications_since(self, since: int, timeout_seconds: float) -> list[dict]:
        """Wait up to a bounded timeout for notification rows after `since`."""

    @abstractmethod
    def append_conversation_message(
        self,
        conversation_id: str,
        role: Literal["user", "assistant"],
        content: str,
        *,
        ttl_hours: int,
        max_turns: int,
        event_id: str | None = None,
    ) -> None:
        """Append and prune one conversation's bounded context."""

    @abstractmethod
    def fetch_conversation_messages(self, conversation_id: str, limit: int) -> list[dict]:
        """Return bounded conversation messages in chronological order."""

    def list_conversation_event_links(
        self, conversation_id: str, sender_identity: str, limit: int = 20
    ) -> list[ConversationEventLink]:
        """Return persisted operational events owned by this conversation/sender."""

        raise NotImplementedError

    @abstractmethod
    def read_user(self, telegram_identity: str) -> dict | None:
        """Return the user's record, including auto_register, or None if unregistered."""

    @abstractmethod
    def write_user(self, telegram_identity: str, permission_level: str, full_name: str | None = None) -> None:
        """Create/update a manual user; None preserves an existing full name and edits preserve registration source."""

    @abstractmethod
    def register_telegram_user_if_missing(self, telegram_identity: str) -> dict:
        """Atomically create an automatic viewer when absent and return the effective record."""

    @abstractmethod
    def ensure_user_exists(
        self,
        telegram_identity: str,
        permission_level: str,
        full_name: str,
        *,
        identity_kind: str = "LIVE",
    ) -> bool:
        """Create the user (auto_register=False) if absent; never touch it if present.
        Returns True iff a new row was created — used for idempotent simulation-user
        provisioning (docs/profile_simulations_design.md), distinct from
        `register_telegram_user_if_missing`'s safe-mode-pending-approval semantics."""

    @abstractmethod
    def mark_simulation_identity(self, telegram_identity: str) -> bool:
        """Mark an existing declared synthetic identity as SIMULATION, idempotently."""

    @abstractmethod
    def list_live_users(self) -> list[dict]:
        """Return only real LIVE identities for onboarding/admin management."""

    def is_simulation_identity(self, telegram_identity: str) -> bool:
        """Return whether an identity is explicitly marked synthetic."""
        return False

    @abstractmethod
    def admit_telegram_update(
        self,
        telegram_identity: str,
        group_chat_id: str | None,
        group_label: str,
        allow_registration: bool,
    ) -> dict:
        """Atomically read, and when allowed create, the user and optional group for one Telegram update."""

    @abstractmethod
    def approve_user(self, telegram_identity: str) -> dict:
        """Mark an existing user as manually approved and return it."""

    @abstractmethod
    def update_user_full_name(self, telegram_identity: str, full_name: str) -> None:
        """Update an existing user's full name; raise NotFoundError when absent."""

    @abstractmethod
    def delete_user(self, telegram_identity: str) -> None:
        """Remove a user."""

    @abstractmethod
    def list_users(self) -> list[dict]:
        """Return every registered user."""

    @abstractmethod
    def read_group(self, chat_id: str) -> dict | None:
        """Return the Telegram group's routing record, including auto_register, or None if unregistered."""

    @abstractmethod
    def write_group(self, chat_id: str, agent_name: str, label: str = "") -> None:
        """Create/update a manual Telegram group; edits preserve registration source."""

    @abstractmethod
    def register_telegram_group_if_missing(self, chat_id: str, label: str = "") -> dict:
        """Atomically create an automatic main-agent group when absent and return the effective record."""

    @abstractmethod
    def ensure_group_exists(self, chat_id: str, agent_name: str, label: str) -> bool:
        """Create the group (auto_register=False) if absent; never touch it if present.
        Returns True iff a new row was created — used for idempotent simulation-group
        provisioning (docs/profile_simulations_design.md), distinct from
        `register_telegram_group_if_missing`'s safe-mode-pending-approval semantics."""

    @abstractmethod
    def rename_group(self, old_chat_id: str, new_chat_id: str) -> dict:
        """Change a group's chat_id (its primary key) in place and return the updated record.
        Raises NotFoundError if `old_chat_id` doesn't exist, or PersistenceError if
        `new_chat_id` is already taken by another group."""

    @abstractmethod
    def approve_group(self, chat_id: str) -> dict:
        """Mark an existing Telegram group as manually approved and return it."""

    @abstractmethod
    def delete_group(self, chat_id: str) -> None:
        """Remove a Telegram group binding; raises NotFoundError when absent."""

    @abstractmethod
    def list_groups(self) -> list[dict]:
        """Return every registered Telegram group binding."""


    @abstractmethod
    def write_log_entry(self, trace_id: str | None, details: dict) -> None:
        """Persist one structured log record — the single write path `tools.logging_config`'s DB-backed handler funnels every `logger.*` call site through, so no call site ever talks to st..."""

    @abstractmethod
    def fetch_log_entries(self, trace_id: str) -> list[dict]:
        """Return every log entry recorded for `trace_id`, oldest first — each dict merging `details` back out with `id`/`trace_id`/ `timestamp`, reconstructing the full original record."""

    @abstractmethod
    def fetch_log_entries_since(self, trace_id: str, since: int) -> list[dict]:
        """Return trace entries with an ID greater than the supplied cursor."""

    @abstractmethod
    def wait_for_log_entries_since(
        self, trace_id: str, since: int, timeout_seconds: float
    ) -> list[dict]:
        """Wait up to 30 seconds for trace entries newer than the cursor."""

    def clear_runtime_history(self) -> dict[str, int]:
        """Remove profile-owned runtime history while preserving seed entities.

        This maintenance operation is intentionally outside the normal event
        workflow. Concrete backends may implement it for an explicitly scoped
        test/demo reset; it must never drop schema or touch users/groups.
        """

        raise NotImplementedError


def open_persistence(db_path: str) -> PersistenceInterface:
    """Construct the concrete backend for `db_path`."""

    from persistence.sqlite_store import SQLitePersistence

    return SQLitePersistence(db_path)
