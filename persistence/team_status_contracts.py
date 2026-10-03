"""Storage contract for the isolated readiness-team status database."""

from abc import ABC, abstractmethod
from dataclasses import dataclass


class TeamStatusPersistenceError(Exception):
    """The readiness-team state request could not be completed."""


@dataclass(frozen=True)
class AttendanceCycle:
    """One attendance_cycles row plus whether this open created it."""

    cycle_id: str
    cycle_key: str
    opened_at: str
    deadline_at: str
    created: bool


class TeamStatusPersistenceInterface(ABC):
    """Contract for roster and attendance rows on a dedicated status database."""

    @abstractmethod
    def register_member(self, telegram_identity: str, full_name: str, registered_at: str | None = None) -> None:
        """Insert or refresh a team_members row."""

    @abstractmethod
    def approve_roster(self, approved_by: str, approved_at: str | None = None) -> int:
        """Approve every member and record the singleton approval."""

    @abstractmethod
    def roster_is_approved(self) -> bool:
        """True when the singleton roster_approval row exists."""

    @abstractmethod
    def list_members(self, *, approved_only: bool = True) -> list[dict]:
        """Return team_members rows, optionally limited to approved members."""

    @abstractmethod
    def open_cycle(self, cycle_key: str, opened_at: str, deadline_at: str) -> AttendanceCycle:
        """Create an attendance cycle for this key, or return the existing one."""

    @abstractmethod
    def latest_cycle(self) -> dict | None:
        """Return the newest attendance_cycles row, or None."""

    @abstractmethod
    def request_broadcast(self, cycle_key: str) -> None:
        """Queue this cycle_key on the singleton attendance_broadcast row."""

    @abstractmethod
    def claim_broadcast(self) -> dict | None:
        """Take the pending broadcast and return the cycle plus members still required."""

    @abstractmethod
    def record_response(
        self,
        *,
        telegram_identity: str,
        source_message_id: str,
        availability: str,
        original_text: str,
        received_at: str,
        reason: str | None = None,
        unavailable_until: str | None = None,
    ) -> dict:
        """Insert an attendance response, or return the existing one for this message id."""

    @abstractmethod
    def review_late_response(
        self, response_id: str, *, approved: bool, reviewed_by: str, reviewed_at: str | None = None
    ) -> dict:
        """Accept or reject a pending late attendance response."""

    @abstractmethod
    def pending_late_responses(self) -> list[dict]:
        """Return pending attendance responses with member names."""

    @abstractmethod
    def availability_snapshot(self, as_of: str) -> list[dict]:
        """Derive each approved member's current availability as of this instant."""


def open_team_status_persistence(db_path: str) -> TeamStatusPersistenceInterface:
    """Construct the dedicated team-status store for this database path."""

    from persistence.team_status_store import SQLiteTeamStatusPersistence

    return SQLiteTeamStatusPersistence(db_path)
