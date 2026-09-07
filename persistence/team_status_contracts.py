"""Storage contract for the isolated readiness-team status database."""

from abc import ABC, abstractmethod
from dataclasses import dataclass


class TeamStatusPersistenceError(Exception):
    """The readiness-team state request could not be completed."""


@dataclass(frozen=True)
class AttendanceCycle:
    cycle_id: str
    cycle_key: str
    opened_at: str
    deadline_at: str
    created: bool


class TeamStatusPersistenceInterface(ABC):
    @abstractmethod
    def register_member(self, telegram_identity: str, full_name: str, registered_at: str | None = None) -> None: ...

    @abstractmethod
    def approve_roster(self, approved_by: str, approved_at: str | None = None) -> int: ...

    @abstractmethod
    def roster_is_approved(self) -> bool: ...

    @abstractmethod
    def list_members(self, *, approved_only: bool = True) -> list[dict]: ...

    @abstractmethod
    def open_cycle(self, cycle_key: str, opened_at: str, deadline_at: str) -> AttendanceCycle: ...

    @abstractmethod
    def latest_cycle(self) -> dict | None: ...

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
    ) -> dict: ...

    @abstractmethod
    def review_late_response(
        self, response_id: str, *, approved: bool, reviewed_by: str, reviewed_at: str | None = None
    ) -> dict: ...

    @abstractmethod
    def pending_late_responses(self) -> list[dict]: ...

    @abstractmethod
    def availability_snapshot(self, as_of: str) -> list[dict]: ...


def open_team_status_persistence(db_path: str) -> TeamStatusPersistenceInterface:
    from persistence.team_status_store import SQLiteTeamStatusPersistence

    return SQLiteTeamStatusPersistence(db_path)
