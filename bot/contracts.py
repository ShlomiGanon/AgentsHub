"""The bot's one gateway to the system (work_plan.md §8, docs/allowed_calls.md)."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from bot.transports import TelegramClient
    from profiles.loader import LoadedProfile


class BotError(Exception):
    pass


class BotStartupError(BotError):
    pass


class ApiNotImplementedError(BotError, NotImplementedError):
    def __init__(self, operation: str, blocked_on: str):
        self.operation = operation
        self.blocked_on = blocked_on
        super().__init__(
            f"'{operation}' is not available: it depends on {blocked_on} "
            f"(work_plan.md §7 — API Layer), which has not been built yet."
        )


class ApiRequestError(BotError):
    def __init__(self, status_code: int | None, message: str, error_class: str | None = None, field: str | None = None):
        self.status_code = status_code
        self.message = message
        self.error_class = error_class
        self.field = field
        super().__init__(f"API request failed ({status_code if status_code is not None else 'no response'}): {message}")


class AlreadyRunningError(BotStartupError):
    pass


@dataclass(frozen=True)
class BotDeps:
    loaded_profile: "LoadedProfile"
    telegram_client: "TelegramClient"
    api_client: "BotApiClient"

PermissionLevelName = Literal["viewer", "commander"]

# Public deployment identity for service-level API calls; it is not a secret.
BOT_SERVICE_IDENTITY = "bot-service"

BotOutcome = Literal[
    "closed_on_precedent",
    "declined",
    "succeeded",
    "failed",
    "uncertain",
    "no_match_protocol",
]

HoldAnswerStatus = Literal[
    "resolved", "approved", "rejected", "unauthorized", "not_found", "invalid_classification", "invalid_candidate"
]


@dataclass(frozen=True)
class UserLookupResult:
    registered: bool
    permission_level: PermissionLevelName | None = None


@dataclass(frozen=True)
class MessageSubmissionResult:
    kind: Literal["question", "report", "request", "conversational", "clarification"]
    answer_text: str | None = None
    job_id: str | None = None
    awaiting_approval: bool = False


@dataclass(frozen=True)
class JobResult:
    job_id: str
    outcome: BotOutcome
    insight_text: str = ""
    steps_completed: tuple[str, ...] = ()
    failure_reason: str | None = None
    failed_step_agent_name: str | None = None


@dataclass(frozen=True)
class HeldClarificationNotice:
    hold_id: str
    event_id: str
    raw_text: str
    unresolved_field: str
    available_classifications: tuple[str, ...]


@dataclass(frozen=True)
class HeldApprovalNotice:
    hold_id: str
    event_id: str
    reason: Literal["flagged_protocol", "ambiguous_selection"]
    risk_level: str
    risk_reason: str
    selected_protocol_name: str | None = None
    candidate_protocol_names: tuple[str, ...] = ()


@dataclass(frozen=True)
class UncertainVerdictNotice:
    event_id: str
    insight_text: str


@dataclass(frozen=True)
class NoMatchNotice:
    event_id: str
    raw_text: str
    reason: str
    risk_level: str
    risk_reason: str


@dataclass(frozen=True)
class HoldAnswerOutcome:
    status: HoldAnswerStatus
    resolved_by: str | None = None
    message: str = ""




@dataclass(frozen=True)
class PrecedentClosureNotice:
    event_id: str
    raw_text: str
    matched_precedent_event_id: str
    precedent_ending: str


@dataclass(frozen=True)
class ProtocolView:
    name: str
    description: str
    criticality: str
    approval_flag: bool


@dataclass(frozen=True)
class ProfileView:
    profile_name: str
    agent_names: tuple[str, ...]
    protocols: tuple[ProtocolView, ...]
    event_types: tuple[str, ...]
    areas: tuple[str, ...]


@dataclass(frozen=True)
class WriteResult:
    accepted: bool
    message: str


@dataclass(frozen=True)
class SettingsView:
    retry_count: int
    risk_threshold: float
    lookback_window_days: int



# Every asynchronous bot delivery uses the same cursor-backed notification shape.
BotNotificationKind = Literal[
    "clarification_hold",
    "approval_hold",
    "uncertain_verdict",
    "precedent_closure",
    "no_match_notice",
    "job_finished",
    "job_failed",
]


@dataclass(frozen=True)
class FailureNotice:
    event_id: str
    failed_step_agent_name: str | None
    failure_reason: str
    steps_completed_before_failure: tuple[str, ...] = ()


@dataclass(frozen=True)
class BotNotification:
    kind: BotNotificationKind
    target_chat_ids: tuple[str, ...]
    payload: (
        HeldClarificationNotice
        | HeldApprovalNotice
        | UncertainVerdictNotice
        | PrecedentClosureNotice
        | NoMatchNotice
        | JobResult
        | FailureNotice
    )
    reply_to_message_id: str | None = None


class BotApiClient(ABC):
    """Everything `bot/` needs from the API Layer."""


    @abstractmethod
    async def resolve_user(self, telegram_identity: str) -> UserLookupResult: ...

    @abstractmethod
    async def list_commander_chat_ids(self) -> tuple[str, ...]:
        """Every commander's Telegram identity, for pushing §8.4/§8.5/§8.6 notifications to."""


    @abstractmethod
    async def submit_message(self, text: str, sender_identity: str, source_message_id: str) -> MessageSubmissionResult:
        """`source_message_id` — the incoming Telegram message's own ID — is what an eventual asynchronous job result (§8.9) or failure notification (§8.11) needs to send its reply *as a r..."""


    @abstractmethod
    async def answer_clarification_hold(
        self, event_id: str, chosen_classification: str, answering_identity: str
    ) -> HoldAnswerOutcome:
        """Answer a clarification through its stable external event ID."""


    @abstractmethod
    async def answer_approval_hold(self, event_id: str, decision: str, answering_identity: str) -> HoldAnswerOutcome:
        """Answer an approval through its stable external event ID."""


    @abstractmethod
    async def get_profile_view(self, caller_identity: str) -> ProfileView:
        """`caller_identity` — the real Telegram identity asking, already resolved and permission-checked by `bot.users.resolve_caller` before this is ever called — is what the API's own §..."""

    @abstractmethod
    async def get_profile_diff_status(self) -> bool: ...

    @abstractmethod
    async def write_protocol(
        self, action: Literal["add", "edit", "remove"], protocol_payload: dict, caller_identity: str
    ) -> WriteResult:
        """`caller_identity` — see `get_profile_view`'s docstring; the same reasoning applies to every write in this interface."""


    @abstractmethod
    async def get_settings_view(self, caller_identity: str) -> SettingsView:
        """`caller_identity` — see `get_profile_view`'s docstring."""

    @abstractmethod
    async def write_setting(self, field: str, value: object, caller_identity: str) -> WriteResult:
        """`caller_identity` — see `get_profile_view`'s docstring."""


    @abstractmethod
    async def get_job_result(self, job_id: str, caller_identity: str) -> JobResult | None:
        """`caller_identity` — see `get_profile_view`'s docstring."""


    @abstractmethod
    async def poll_pending_notifications(self, since: int) -> tuple[tuple[BotNotification, ...], int]:
        """Everything newly relevant since the caller's own `since` cursor (0 for "from the beginning"), plus the cursor to pass as `since` on the next call."""


class UnimplementedApiClient(BotApiClient):
    """The only concrete `BotApiClient` today."""

    async def resolve_user(self, telegram_identity: str) -> UserLookupResult:
        raise ApiNotImplementedError("resolve_user", "§7.9 (authentication/authorization enforcement)")

    async def list_commander_chat_ids(self) -> tuple[str, ...]:
        raise ApiNotImplementedError("list_commander_chat_ids", "§7.9 (authentication/authorization enforcement)")

    async def submit_message(self, text: str, sender_identity: str, source_message_id: str) -> MessageSubmissionResult:
        raise ApiNotImplementedError("submit_message", "§7.4 (POST /Msg)")

    async def answer_clarification_hold(
        self, event_id: str, chosen_classification: str, answering_identity: str
    ) -> HoldAnswerOutcome:
        raise ApiNotImplementedError("answer_clarification_hold", "§7.9 (authentication/authorization enforcement)")

    async def answer_approval_hold(self, event_id: str, decision: str, answering_identity: str) -> HoldAnswerOutcome:
        raise ApiNotImplementedError("answer_approval_hold", "§7.9 (authentication/authorization enforcement)")

    async def get_profile_view(self, caller_identity: str) -> ProfileView:
        raise ApiNotImplementedError("get_profile_view", "§7.7 (GET /SYSTEM)")

    async def get_profile_diff_status(self) -> bool:
        raise ApiNotImplementedError("get_profile_diff_status", "§7.7 (GET /SYSTEM)")

    async def write_protocol(self, action: Literal["add", "edit", "remove"], protocol_payload: dict, caller_identity: str) -> WriteResult:
        raise ApiNotImplementedError("write_protocol", "§7.6 (CRUD /Protocol)")

    async def get_settings_view(self, caller_identity: str) -> SettingsView:
        raise ApiNotImplementedError("get_settings_view", "§7.7 (GET /SYSTEM)")

    async def write_setting(self, field: str, value: object, caller_identity: str) -> WriteResult:
        raise ApiNotImplementedError("write_setting", "§7.8 (PUT /SYSTEM)")

    async def get_job_result(self, job_id: str, caller_identity: str) -> JobResult | None:
        raise ApiNotImplementedError("get_job_result", "§7.2 (async job mechanism)")

    async def poll_pending_notifications(self, since: int) -> tuple[tuple[BotNotification, ...], int]:
        raise ApiNotImplementedError("poll_pending_notifications", "§7.2 (async job mechanism)")
