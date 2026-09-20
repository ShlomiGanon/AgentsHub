"""Storage contract for the isolated readiness-team status database."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from persistence.operational_scope import OperationalScope


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
    def ensure_scope(self, scope: OperationalScope, baseline: dict | None = None) -> None: ...

    @abstractmethod
    def register_member(self, telegram_identity: str, full_name: str, registered_at: str | None = None, *, scope: OperationalScope | None = None) -> None: ...

    @abstractmethod
    def approve_roster(self, approved_by: str, approved_at: str | None = None, *, scope: OperationalScope | None = None) -> int: ...

    @abstractmethod
    def roster_is_approved(self, *, scope: OperationalScope | None = None) -> bool: ...

    @abstractmethod
    def list_members(self, *, approved_only: bool = True, scope: OperationalScope | None = None) -> list[dict]: ...

    def get_members(self, *, approved_only: bool = True, scope: OperationalScope | None = None) -> list[dict]:
        """Canonical scope-bound membership read API."""
        return self.list_members(approved_only=approved_only, scope=scope)

    def get_member_state(self, telegram_identity: str, *, scope: OperationalScope | None = None) -> dict | None:
        for member in self.list_members(approved_only=False, scope=scope):
            if member.get("telegram_identity") == telegram_identity:
                return member
        return None

    def retire_member(self, telegram_identity: str, *, scope: OperationalScope | None = None) -> bool:
        raise NotImplementedError

    @abstractmethod
    def open_cycle(self, cycle_key: str, opened_at: str, deadline_at: str, *, scope: OperationalScope | None = None) -> AttendanceCycle: ...

    @abstractmethod
    def latest_cycle(self, *, scope: OperationalScope | None = None) -> dict | None: ...

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
        availability_start: str | None = None,
        availability_end: str | None = None,
        scope: OperationalScope | None = None,
    ) -> dict: ...

    @abstractmethod
    def review_late_response(
        self, response_id: str, *, approved: bool, reviewed_by: str, reviewed_at: str | None = None, scope: OperationalScope | None = None
    ) -> dict: ...

    @abstractmethod
    def pending_late_responses(self, *, scope: OperationalScope | None = None) -> list[dict]: ...

    @abstractmethod
    def availability_snapshot(self, as_of: str, *, scope: OperationalScope | None = None) -> list[dict]: ...

    def clear_runtime_state(self, *, scope: OperationalScope | None = None) -> dict[str, int]:
        """Remove runtime attendance responses/cycles while preserving roster seed."""

        raise NotImplementedError


def open_team_status_persistence(db_path: str) -> TeamStatusPersistenceInterface:
    from persistence.team_status_store import SQLiteTeamStatusPersistence

    return SQLiteTeamStatusPersistence(db_path)
