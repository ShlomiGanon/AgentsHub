"""Bot contracts: errors, API views, and the BotApiClient interface."""

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from auth.permissions import BOT_SERVICE_IDENTITY, BOT_SERVICE_KEY_ENV_VAR

# --- errors ---

if TYPE_CHECKING:
    from bot.transports import TelegramClient
    from profiles.loader import LoadedProfile


class BotError(Exception):
    """Base error for bot-side failures."""

    pass


class BotStartupError(BotError):
    """The bot process cannot start with the current configuration."""

    pass


class ApiNotImplementedError(BotError, NotImplementedError):
    """This client does not implement the requested API operation."""

    def __init__(self, operation: str, blocked_on: str):
        """Record which operation is blocked and what it still depends on."""

        self.operation = operation
        self.blocked_on = blocked_on
        super().__init__(
            f"'{operation}' is not available: it depends on {blocked_on}."
        )


class ApiRequestError(BotError):
    """The API returned an error status or the HTTP call failed."""

    def __init__(self, status_code: int | None, message: str, error_class: str | None = None, field: str | None = None):
        """Store status, message, and optional error class/field from the API."""

        self.status_code = status_code
        self.message = message
        self.error_class = error_class
        self.field = field
        super().__init__(f"API request failed ({status_code if status_code is not None else 'no response'}): {message}")


class AlreadyRunningError(BotStartupError):
    """Another bot process already holds the single-instance lock."""

    pass


@dataclass(frozen=True)
class BotDeps:
    """Profile, Telegram client, and API client used by one bot process."""

    loaded_profile: "LoadedProfile"
    telegram_client: "TelegramClient"
    api_client: "BotApiClient"

# --- identity ---

PermissionLevelName = Literal["viewer", "commander"]

# Canonical values live in auth.permissions so api and bot never import each other.


def resolve_bot_service_key() -> str | None:
    """The configured BOT_SERVICE_KEY, or None if it isn't set."""

    return os.environ.get(BOT_SERVICE_KEY_ENV_VAR)

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


# --- views ---

@dataclass(frozen=True)
class UserLookupResult:
    """Whether a Telegram identity is registered and at which permission level."""

    registered: bool
    permission_level: PermissionLevelName | None = None
    full_name: str | None = None
    auto_register: bool = False


@dataclass(frozen=True)
class MessageSubmissionResult:
    """Immediate /Msg answer: kind, optional text, and optional async job id."""

    kind: Literal["question", "report", "request", "conversational", "clarification", "event_update"]
    answer_text: str | None = None
    job_id: str | None = None
    provenance: dict | None = None


@dataclass(frozen=True)
class TracePollResult:
    """A page of Deep Debug messages plus the cursor for the next poll."""

    messages: tuple[str, ...]
    next_cursor: int
    terminal: bool = False


@dataclass(frozen=True)
class GroupBindingView:
    """One Telegram group -> agent binding as `GET /Groups` reports it."""

    chat_id: str
    agent_name: str
    label: str = ""
    auto_register: bool = False
    attendance_check_enabled: bool = False
    attendance_check_hour: int = 8


@dataclass(frozen=True)
class TelegramAdmissionResult:
    """Whether this Telegram update is allowed into the system, and why."""

    allowed: bool
    reason: str
    safe_mode: bool
    user: UserLookupResult | None = None
    group: GroupBindingView | None = None


@dataclass(frozen=True)
class AttendanceCheckResult:
    """`POST /TeamStatus/AttendanceCheck`'s answer: whether a cycle was just opened, and where to announce it."""

    opened: bool
    agent_name: str
    target_chat_ids: tuple[str, ...] = ()
    cycle_key: str | None = None
    deadline_at: str | None = None
    members_required: tuple[str, ...] = ()


@dataclass(frozen=True)
class JobResult:
    """Finished-job fields the bot needs to format a result message."""

    job_id: str
    outcome: BotOutcome
    insight_text: str = ""
    steps_completed: tuple[str, ...] = ()
    failure_reason: str | None = None
    failed_step_agent_name: str | None = None
    # Sourced from data already computed during the run (no new model call) —
    # None whenever no protocol was ever selected (e.g. a `no_match_protocol`
    # outcome), which format_job_result treats as "no suffix to show".
    protocol_name: str | None = None
    risk_level: str | None = None
    protocol_reason: str | None = None
    # Composed once, server-side, when the run finished — None when rich
    # reporting is disabled, in which case the bot falls back to format_job_result.
    report_text: str | None = None
    selection_required: bool = False


@dataclass(frozen=True)
class HeldClarificationNotice:
    """A clarification hold waiting for a commander classification."""

    hold_id: str
    event_id: str
    raw_text: str
    unresolved_field: str
    available_classifications: tuple[str, ...]


@dataclass(frozen=True)
class HeldApprovalNotice:
    """An approval hold waiting for a commander decision."""

    hold_id: str
    event_id: str
    reason: Literal["flagged_protocol", "ambiguous_selection"]
    risk_level: str
    risk_reason: str
    selected_protocol_name: str | None = None
    candidate_protocol_names: tuple[str, ...] = ()


@dataclass(frozen=True)
class EventDataNeededNotice:
    """A prompt asking the reporter for missing event data."""

    hold_id: str
    event_id: str
    question: str
    missing_fields: tuple[str, ...]


@dataclass(frozen=True)
class UncertainVerdictNotice:
    """Commander-facing notice that a run ended uncertain."""

    event_id: str
    insight_text: str


@dataclass(frozen=True)
class UncertainVerdictReporterNotice:
    """Reporter-facing uncertain notice with no insight text, unlike the commander notice."""

    event_id: str


@dataclass(frozen=True)
class ResourceUnavailableAlertNotice:
    """Commander-only alert that a required resource was unavailable, with alternatives."""

    event_id: str
    alert_text: str


@dataclass(frozen=True)
class HoldEscalationNotice:
    """Commander-only alert that a hold sat unanswered past the escalation window."""

    event_id: str
    alert_text: str


@dataclass(frozen=True)
class NoMatchNotice:
    """Commander-facing notice that no protocol matched."""

    event_id: str
    raw_text: str
    reason: str
    risk_level: str
    risk_reason: str


@dataclass(frozen=True)
class HoldAnswerOutcome:
    """Result of answering an approval or clarification hold."""

    status: HoldAnswerStatus
    resolved_by: str | None = None
    message: str = ""




@dataclass(frozen=True)
class PrecedentClosureNotice:
    """Commander-facing notice that a report closed on a precedent."""

    event_id: str
    raw_text: str
    matched_precedent_event_id: str
    precedent_ending: str


@dataclass(frozen=True)
class ProtocolView:
    """One protocol as shown on the profile view."""

    name: str
    description: str
    criticality: str
    approval_flag: bool


@dataclass(frozen=True)
class ProfileView:
    """Live profile snapshot for /profile view."""

    profile_name: str
    agent_names: tuple[str, ...]
    protocols: tuple[ProtocolView, ...]
    event_types: tuple[str, ...]
    areas: tuple[str, ...]


@dataclass(frozen=True)
class WriteResult:
    """Whether a protocol or settings write was accepted, plus the API message."""

    accepted: bool
    message: str


@dataclass(frozen=True)
class SettingsView:
    """Live settings snapshot for /settings view."""

    retry_count: int
    risk_threshold: float
    lookback_window_days: int
    safe_mode: bool = False



# Every asynchronous bot delivery uses the same cursor-backed notification shape.
BotNotificationKind = Literal[
    "clarification_hold",
    "approval_hold",
    "uncertain_verdict",
    "uncertain_verdict_reporter",
    "precedent_closure",
    "no_match_notice",
    "job_finished",
    "job_failed",
    "event_data_hold",
    "resource_unavailable_alert",
    "hold_escalation",
]


@dataclass(frozen=True)
class FailureNotice:
    """Failed-step fields the bot needs to format a failure message."""

    event_id: str
    failed_step_agent_name: str | None
    failure_reason: str
    steps_completed_before_failure: tuple[str, ...] = ()
    report_text: str | None = None


@dataclass(frozen=True)
class BotNotification:
    """One item from the notification poll, already typed by kind."""

    kind: BotNotificationKind
    target_chat_ids: tuple[str, ...]
    payload: (
        HeldClarificationNotice
        | HeldApprovalNotice
        | UncertainVerdictNotice
        | UncertainVerdictReporterNotice
        | PrecedentClosureNotice
        | NoMatchNotice
        | JobResult
        | FailureNotice
        | EventDataNeededNotice
        | HoldEscalationNotice
    )
    reply_to_message_id: str | None = None
    # job_finished/job_failed only: the status/ack message to edit in place with the final
    # result. None for every other kind, or when the event has no stored ack.
    ack_message_id: str | None = None
    trace_id: str | None = None


# --- client ---

class BotApiClient(ABC):
    """Everything `bot/` needs from the API over HTTP."""

    async def start(self) -> None:
        """Open lifecycle-managed transport resources when needed."""

    async def close(self) -> None:
        """Close lifecycle-managed transport resources."""

    @abstractmethod
    async def admit_telegram_update(
        self,
        telegram_identity: str,
        chat_id: str,
        chat_type: str,
        chat_label: str = "",
    ) -> TelegramAdmissionResult:
        """Register or refuse this Telegram user/chat before handling the update."""

        ...

    @abstractmethod
    async def resolve_user(self, telegram_identity: str) -> UserLookupResult:
        """Look up whether this Telegram identity is a registered user."""

        ...

    @abstractmethod
    async def update_own_full_name(self, telegram_identity: str, full_name: str) -> str:
        """Persist the caller's own full name after they supply it in chat."""

        ...

    @abstractmethod
    async def list_commander_chat_ids(self) -> tuple[str, ...]:
        """Every commander's Telegram identity, for pushing hold and verdict notifications."""

    @abstractmethod
    async def list_groups(self) -> tuple[GroupBindingView, ...]:
        """Every registered Telegram group binding (`GET /Groups`, as bot-service) — the bot's only source for "is this group ours, and whose is it"."""

    @abstractmethod
    async def run_attendance_check(self) -> AttendanceCheckResult:
        """Ask the server to open today's attendance cycle if it is due (`POST /TeamStatus/AttendanceCheck`, as bot-service)."""

    @abstractmethod
    async def submit_message(
        self,
        text: str,
        sender_identity: str,
        source_message_id: str,
        conversation_id: str | None = None,
        trace_id: str | None = None,
        event_data_event_id: str | None = None,
        protocol_hint: str | None = None,
        telegram_chat_id: str | None = None,
        telegram_chat_type: str | None = None,
        ack_message_id: str | None = None,
    ) -> MessageSubmissionResult:
        """Submit inbound text; `source_message_id` and `ack_message_id` let later replies thread and edit in place."""


    @abstractmethod
    async def answer_clarification_hold(
        self, event_id: str, chosen_classification: str, answering_identity: str
    ) -> HoldAnswerOutcome:
        """Answer a clarification through its stable external event ID."""


    @abstractmethod
    async def answer_approval_hold(self, event_id: str, decision: str, answering_identity: str) -> HoldAnswerOutcome:
        """Answer an approval through its stable external event ID."""


    @abstractmethod
    async def fetch_pending_holds(self, caller_identity: str) -> dict:
        """Fetch all pending approval and clarification holds for a commander."""


    @abstractmethod
    async def get_profile_view(self, caller_identity: str) -> ProfileView:
        """Live profile snapshot for the already-resolved caller identity."""

    @abstractmethod
    async def get_profile_diff_status(self) -> bool:
        """True when a profile write is waiting for the next restart."""

        ...

    @abstractmethod
    async def write_protocol(
        self, action: Literal["add", "edit", "remove"], protocol_payload: dict, caller_identity: str
    ) -> WriteResult:
        """Add, edit, or remove a protocol as this already-resolved caller."""


    @abstractmethod
    async def get_settings_view(self, caller_identity: str) -> SettingsView:
        """Live settings snapshot for the already-resolved caller identity."""

    @abstractmethod
    async def write_setting(self, field: str, value: object, caller_identity: str) -> WriteResult:
        """Persist one settings field as this already-resolved caller."""


    @abstractmethod
    async def get_job_result(self, job_id: str, caller_identity: str) -> JobResult | None:
        """Finished-job fields for this caller, or None if the job is missing."""


    @abstractmethod
    async def poll_pending_notifications(self, since: int, wait_seconds: int = 0) -> tuple[tuple[BotNotification, ...], int]:
        """Everything newly relevant since the caller's own `since` cursor (0 for "from the beginning"), plus the cursor to pass as `since` on the next call."""

    @abstractmethod
    async def poll_trace(
        self,
        trace_id: str,
        since: int,
        wait_seconds: int,
        caller_identity: str,
    ) -> TracePollResult:
        """Return commander-authorized deterministic live trace messages."""


class UnimplementedApiClient(BotApiClient):
    """Test double that raises ApiNotImplementedError; production uses HttpApiClient."""

    async def admit_telegram_update(
        self,
        telegram_identity: str,
        chat_id: str,
        chat_type: str,
        chat_label: str = "",
    ) -> TelegramAdmissionResult:
        """Refuse; inject HttpApiClient for a live API."""

        raise ApiNotImplementedError(
            "admit_telegram_update",
            "§7 Telegram admission policy",
        )

    async def resolve_user(self, telegram_identity: str) -> UserLookupResult:
        """Refuse; inject HttpApiClient for a live API."""

        raise ApiNotImplementedError("resolve_user", "§7.9 (authentication/authorization enforcement)")

    async def update_own_full_name(self, telegram_identity: str, full_name: str) -> str:
        """Refuse; inject HttpApiClient for a live API."""

        raise ApiNotImplementedError("update_own_full_name", "§7.9 (PUT /User/<identity>/name)")

    async def list_commander_chat_ids(self) -> tuple[str, ...]:
        """Refuse; inject HttpApiClient for a live API."""

        raise ApiNotImplementedError("list_commander_chat_ids", "§7.9 (authentication/authorization enforcement)")

    async def list_groups(self) -> tuple[GroupBindingView, ...]:
        """Refuse; inject HttpApiClient for a live API."""

        raise ApiNotImplementedError("list_groups", "§7.9 (GET /Groups, Telegram group routing)")

    async def run_attendance_check(self) -> AttendanceCheckResult:
        """Refuse; inject HttpApiClient for a live API."""

        raise ApiNotImplementedError("run_attendance_check", "§7.9 (POST /TeamStatus/AttendanceCheck)")

    async def submit_message(
        self, text: str, sender_identity: str, source_message_id: str,
        conversation_id: str | None = None, trace_id: str | None = None,
        event_data_event_id: str | None = None, protocol_hint: str | None = None,
        telegram_chat_id: str | None = None, telegram_chat_type: str | None = None,
        ack_message_id: str | None = None,
    ) -> MessageSubmissionResult:
        """Refuse; inject HttpApiClient for a live API."""

        raise ApiNotImplementedError("submit_message", "§7.4 (POST /Msg)")

    async def answer_clarification_hold(
        self, event_id: str, chosen_classification: str, answering_identity: str
    ) -> HoldAnswerOutcome:
        """Refuse; inject HttpApiClient for a live API."""

        raise ApiNotImplementedError("answer_clarification_hold", "§7.9 (authentication/authorization enforcement)")

    async def answer_approval_hold(self, event_id: str, decision: str, answering_identity: str) -> HoldAnswerOutcome:
        """Refuse; inject HttpApiClient for a live API."""

        raise ApiNotImplementedError("answer_approval_hold", "§7.9 (authentication/authorization enforcement)")

    async def fetch_pending_holds(self, caller_identity: str) -> dict:
        """Refuse; inject HttpApiClient for a live API."""

        raise ApiNotImplementedError("fetch_pending_holds", "§7.9 (GET /Holds/Pending)")

    async def get_profile_view(self, caller_identity: str) -> ProfileView:
        """Refuse; inject HttpApiClient for a live API."""

        raise ApiNotImplementedError("get_profile_view", "§7.7 (GET /SYSTEM)")

    async def get_profile_diff_status(self) -> bool:
        """Refuse; inject HttpApiClient for a live API."""

        raise ApiNotImplementedError("get_profile_diff_status", "§7.7 (GET /SYSTEM)")

    async def write_protocol(self, action: Literal["add", "edit", "remove"], protocol_payload: dict, caller_identity: str) -> WriteResult:
        """Refuse; inject HttpApiClient for a live API."""

        raise ApiNotImplementedError("write_protocol", "§7.6 (CRUD /Protocol)")

    async def get_settings_view(self, caller_identity: str) -> SettingsView:
        """Refuse; inject HttpApiClient for a live API."""

        raise ApiNotImplementedError("get_settings_view", "§7.7 (GET /SYSTEM)")

    async def write_setting(self, field: str, value: object, caller_identity: str) -> WriteResult:
        """Refuse; inject HttpApiClient for a live API."""

        raise ApiNotImplementedError("write_setting", "§7.8 (PUT /SYSTEM)")

    async def get_job_result(self, job_id: str, caller_identity: str) -> JobResult | None:
        """Refuse; inject HttpApiClient for a live API."""

        raise ApiNotImplementedError("get_job_result", "§7.2 (async job mechanism)")

    async def poll_trace(
        self, trace_id: str, since: int, wait_seconds: int, caller_identity: str
    ) -> TracePollResult:
        """Refuse; inject HttpApiClient for a live API."""

        raise ApiNotImplementedError("poll_trace", "§7 commander Deep Debug trace feed")

    async def poll_pending_notifications(self, since: int, wait_seconds: int = 0) -> tuple[tuple[BotNotification, ...], int]:
        """Refuse; inject HttpApiClient for a live API."""

        raise ApiNotImplementedError("poll_pending_notifications", "§7.2 (async job mechanism)")
