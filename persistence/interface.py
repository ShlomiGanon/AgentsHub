"""The persistence interface (work_plan.md §2.7).

The full set of operations every other subsystem may perform against
storage. Declared in full now, per the work plan's own guidance for files
more than one branch wants ("agree the full operation list up front... and
let the later branches only implement"), even though only the user-CRUD
operations have a working implementation today (persistence.sqlite_backend,
pulled forward for §1.10). Every other operation is here as an abstract
method so a concrete backend is forced to acknowledge it — see
persistence/schema.py for which tables back which operations and which are
not yet defined.

Swapping SQLite for another engine means writing one new module against
this interface and changing which one is constructed — nothing above this
boundary may reference SQL strings, engine-specific types, or
engine-specific exceptions (persistence.exceptions only).

`update_event` is not in §2.7's literal operation list, but §5.1 ("write
every step record back onto the same event record as the run proceeds")
and §6.11 ("write the outcome back on every branch") both require mutating
an already-appended event — the interface is unusable for either without
it. Added now as completing 2.7's intent, confirmed with the user.
"""

from abc import ABC, abstractmethod
from typing import Any


class PersistenceInterface(ABC):
    def __init__(self, db_path: str):
        self.db_path = db_path

    # -- Events (work_plan.md §2.3) ------------------------------------

    @abstractmethod
    def append_event(self, event: dict) -> str:
        """Write a new event record; return its generated event ID."""

    @abstractmethod
    def update_event(self, event_id: str, updates: dict) -> None:
        """Merge `updates` onto the event identified by `event_id`.

        A `"steps"` key, if present, is a list of step dicts (agent_name,
        task_text, allowed_tools, result_text, attempt_count, step_index)
        upserted by `(event_id, step_index)` — this is how §5.1's
        incremental per-step writes work without touching other steps.
        Every other key maps to a column on the event itself. Never
        accepts `raw_text` — see persistence/schema.py's §2.5 note.
        """

    @abstractmethod
    def fetch_events_range(self, start: Any, end: Any) -> list[dict]:
        """Return events whose occurrence timestamp falls in [start, end]."""

    @abstractmethod
    def fetch_events_by_type_area_window(self, event_type: str, area: str, window_start: Any, window_end: Any) -> list[dict]:
        """Return events matching both classification and area within a window — precedent search's read path."""

    # -- Summaries (work_plan.md §2.6) ---------------------------------

    @abstractmethod
    def write_summary(self, level: str, summary: dict) -> None:
        """Write one period summary. `level` is 'daily' | 'monthly' | 'yearly'."""

    @abstractmethod
    def fetch_summaries_range(self, level: str, start: Any, end: Any) -> list[dict]:
        """Return summaries at `level` whose period overlaps [start, end]."""

    # -- Held events (work_plan.md §6.2, §6.7) -------------------------

    @abstractmethod
    def store_held_event(self, kind: str, hold: dict) -> str:
        """Persist a hold. `kind` is 'clarification' | 'approval'. Returns a hold ID."""

    @abstractmethod
    def list_held_events(self, kind: str) -> list[dict]:
        """Return every currently-unresolved hold of `kind`."""

    @abstractmethod
    def resolve_held_event(self, kind: str, hold_id: str, resolution: dict) -> None:
        """Mark a hold resolved and record who resolved it and how."""

    # -- Users (work_plan.md §2.4, §1.10) ------------------------------

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


def open_persistence(db_path: str) -> PersistenceInterface:
    """Construct the concrete backend for `db_path`.

    The one place that decides which engine is constructed (work_plan.md
    §2.9). Every caller — including cli.user_admin — reaches a concrete
    backend through this function rather than importing
    persistence.sqlite_backend directly, so swapping the engine later means
    changing this one line and nothing above this boundary.
    """

    from persistence.sqlite_backend import SQLitePersistence

    return SQLitePersistence(db_path)
