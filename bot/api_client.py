"""The bot's one gateway to the system (work_plan.md §8, docs/allowed_calls.md).

`docs/allowed_calls.md` is explicit: "`bot` calls only `api`. It has no
other path into the system." The API Layer runs as its own process,
reachable over the port the profile names (§1.4/§8.1) — this is a network
boundary, not a Python import, which is why nothing in this package ever
imports from the `api` package.

**This module is the seam work_plan.md §7 (API Layer) has not been built
yet.** `BotApiClient` declares every operation the rest of `bot/` needs
from the API, with request/response shapes derived directly from
`docs/vocabulary.md` and the exact wording of each work_plan.md §8
subtask. `UnimplementedApiClient` is the only implementation that exists
today: every method raises `bot.errors.ApiNotImplementedError`, naming
the work_plan.md §7 subtask it is blocked on. Nothing here pretends to
succeed, and nothing here talks to a real socket.

Every other module in this package is built and tested against
`BotApiClient`'s interface via dependency injection, never against
`UnimplementedApiClient` directly (except `bot.app`, which wires the
default in). Once §7 exists, a `HttpApiClient(BotApiClient)` implementing
these same methods with real HTTP calls to the profile's `api_port` is
the only new code required — nothing in `bot/` that consumes this
interface needs to change, per this mission's explicit instruction to
"keep the structure ready so the missing functionality can be connected
later without major refactoring."
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Literal

from bot.errors import ApiNotImplementedError

PermissionLevelName = Literal["viewer", "commander"]

# Mirrors orchestrator.flows.FlowOutcome by value, not by import — bot may
# not import orchestrator (docs/allowed_calls.md: bot calls only api).
# Once §7 exists it is the API's job to translate its own response shape
# into (or out of) this vocabulary; declared independently here so this
# module has no cross-package coupling beyond the network boundary.
BotOutcome = Literal[
    "closed_on_precedent",
    "declined",
    "succeeded",
    "failed",
    "uncertain",
]

HoldAnswerStatus = Literal[
    "resolved", "approved", "rejected", "unauthorized", "not_found", "invalid_classification", "invalid_candidate"
]


# -- §8.2: resolving a Telegram identity against the user table -----------


@dataclass(frozen=True)
class UserLookupResult:
    registered: bool
    permission_level: PermissionLevelName | None = None


# -- §8.3 / §7.4: submitting whatever a person sent ------------------------


@dataclass(frozen=True)
class MessageSubmissionResult:
    kind: Literal["question", "report", "request"]
    answer_text: str | None = None
    job_id: str | None = None
    awaiting_approval: bool = False


# -- §8.9 / §7.2: async job status and result -------------------------------


@dataclass(frozen=True)
class JobResult:
    job_id: str
    outcome: BotOutcome
    insight_text: str = ""
    steps_completed: tuple[str, ...] = ()
    failure_reason: str | None = None
    failed_step_agent_name: str | None = None


# -- §8.4: clarification holds ----------------------------------------------


@dataclass(frozen=True)
class HeldClarificationNotice:
    hold_id: str
    event_id: str
    raw_text: str
    unresolved_field: str
    available_classifications: tuple[str, ...]


# -- §8.5: approval holds, and the separate uncertain-verdict notice --------


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
class HoldAnswerOutcome:
    status: HoldAnswerStatus
    resolved_by: str | None = None
    message: str = ""


# -- §8.6: precedent-closure notifications ----------------------------------


@dataclass(frozen=True)
class PrecedentClosureNotice:
    event_id: str
    raw_text: str
    matched_precedent_event_id: str
    precedent_ending: str


# -- §8.7: profile commands --------------------------------------------------


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
class ProfileDiffStatus:
    differs_from_running: bool


@dataclass(frozen=True)
class ProtocolWriteResult:
    accepted: bool
    message: str


# -- §8.8: settings commands --------------------------------------------------


@dataclass(frozen=True)
class SettingsView:
    retry_count: int
    risk_threshold: float
    lookback_window_days: int


@dataclass(frozen=True)
class SettingsWriteResult:
    accepted: bool
    message: str


# -- §8.4/§8.5/§8.6/§8.9/§8.11: the unified proactive-push feed --------------
#
# work_plan.md §7.2 leaves "how a finished result reaches whoever submitted
# it" as one of the API's own open design points ("implement one path, not
# an unspecified mixture"); the same question applies to §8.4/§8.5/§8.6's
# unprompted pushes. Rather than guess at §7's eventual mechanism (a
# webhook the API calls into the bot with, versus the bot polling the API),
# every push-style notification funnels through one shape,
# `BotNotification`, and one retrieval method, `poll_pending_notifications`.
# Whichever mechanism §7 ends up choosing, it only has to produce this one
# shape; `bot.notifications.dispatch_notification` (built for §8.4, reused
# by §8.5/§8.6/§8.9/§8.11) is the only thing that reads it.

BotNotificationKind = Literal[
    "clarification_hold",
    "approval_hold",
    "uncertain_verdict",
    "precedent_closure",
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
        | JobResult
        | FailureNotice
    )
    reply_to_message_id: str | None = None


class BotApiClient(ABC):
    """Everything `bot/` needs from the API Layer. See module docstring."""

    # -- §8.2 ---------------------------------------------------------------

    @abstractmethod
    async def resolve_user(self, telegram_identity: str) -> UserLookupResult: ...

    @abstractmethod
    async def list_commander_chat_ids(self) -> tuple[str, ...]:
        """Every commander's Telegram identity, for pushing §8.4/§8.5/§8.6
        notifications to. Not user-facing — no bot command exposes this
        (§8.2's own explicit prohibition on a user-list command).
        """

    # -- §8.3 / §7.4 ----------------------------------------------------------

    @abstractmethod
    async def submit_message(self, text: str, sender_identity: str) -> MessageSubmissionResult: ...

    # -- §8.4 / §6.2 / §7.11 ---------------------------------------------------

    @abstractmethod
    async def answer_clarification_hold(
        self, event_id: str, chosen_classification: str, answering_identity: str
    ) -> HoldAnswerOutcome:
        """`event_id` — not the orchestrator's internal hold ID — since
        `api/holds.py`'s `POST /Clarify/<event_id>` (§7.11) is deliberately
        keyed by event ID: the one stable external identifier the whole API
        is already built around (`GET /Job/<event_id>` uses it too, and
        §7.2 defines the job ID as the event ID). `hold_id` is a Mission-6
        implementation detail that was never meant to cross the API
        boundary. `HeldClarificationNotice.event_id` is what a caller
        should pass here — found and fixed in the Mission 8 deep audit;
        this parameter used to be named `hold_id` and was forwarded from
        the wrong field.
        """

    # -- §8.5 / §6.7 / §7.11 ---------------------------------------------------

    @abstractmethod
    async def answer_approval_hold(self, event_id: str, decision: str, answering_identity: str) -> HoldAnswerOutcome:
        """`event_id` — not the orchestrator's internal hold ID — for the
        same reason `answer_clarification_hold` takes it: `api/holds.py`'s
        `POST /Approve/<event_id>` (§7.11) is keyed by event ID.
        `HeldApprovalNotice.event_id` is what a caller should pass here.

        `decision` is `"approved"` or `"rejected"` for a flagged-protocol
        hold; for an ambiguous-selection hold it is the name of the chosen
        candidate protocol (rejection there is expressed by choosing
        none — see `bot.approval`'s formatting, which offers no separate
        "reject" button for that case). `orchestrator.holds
        .answer_approval_hold` (§6.7) now accepts all three shapes
        additively — the gap this docstring used to describe (no path for
        a candidate name to reach a resumed run) was closed in Mission 7;
        see `docs/progress.md`'s amended §6.7 entry.
        """

    # -- §8.7 / §7.6 / §7.7 -----------------------------------------------------

    @abstractmethod
    async def get_profile_view(self) -> ProfileView: ...

    @abstractmethod
    async def get_profile_diff_status(self) -> ProfileDiffStatus: ...

    @abstractmethod
    async def write_protocol(
        self, action: Literal["add", "edit", "remove"], protocol_payload: dict
    ) -> ProtocolWriteResult: ...

    # -- §8.8 / §7.8 ------------------------------------------------------------

    @abstractmethod
    async def get_settings_view(self) -> SettingsView: ...

    @abstractmethod
    async def write_setting(self, field: str, value: object) -> SettingsWriteResult: ...

    # -- §8.9 / §7.2 --------------------------------------------------------------

    @abstractmethod
    async def get_job_result(self, job_id: str) -> JobResult | None: ...

    # -- §8.4/§8.5/§8.6/§8.9/§8.11 -------------------------------------------------

    @abstractmethod
    async def poll_pending_notifications(self) -> tuple[BotNotification, ...]: ...


class UnimplementedApiClient(BotApiClient):
    """The only concrete `BotApiClient` today. Every method raises
    `ApiNotImplementedError`, naming exactly which work_plan.md §7 subtask
    would implement it. This is `bot.app`'s default until Mission 7 lands
    — see the module docstring for why nothing here attempts a real
    network call.
    """

    async def resolve_user(self, telegram_identity: str) -> UserLookupResult:
        raise ApiNotImplementedError("resolve_user", "§7.9 (authentication/authorization enforcement)")

    async def list_commander_chat_ids(self) -> tuple[str, ...]:
        raise ApiNotImplementedError("list_commander_chat_ids", "§7.9 (authentication/authorization enforcement)")

    async def submit_message(self, text: str, sender_identity: str) -> MessageSubmissionResult:
        raise ApiNotImplementedError("submit_message", "§7.4 (POST /Msg)")

    async def answer_clarification_hold(
        self, event_id: str, chosen_classification: str, answering_identity: str
    ) -> HoldAnswerOutcome:
        raise ApiNotImplementedError("answer_clarification_hold", "§7.9 (authentication/authorization enforcement)")

    async def answer_approval_hold(self, event_id: str, decision: str, answering_identity: str) -> HoldAnswerOutcome:
        raise ApiNotImplementedError("answer_approval_hold", "§7.9 (authentication/authorization enforcement)")

    async def get_profile_view(self) -> ProfileView:
        raise ApiNotImplementedError("get_profile_view", "§7.7 (GET /SYSTEM)")

    async def get_profile_diff_status(self) -> ProfileDiffStatus:
        raise ApiNotImplementedError("get_profile_diff_status", "§7.7 (GET /SYSTEM)")

    async def write_protocol(self, action: Literal["add", "edit", "remove"], protocol_payload: dict) -> ProtocolWriteResult:
        raise ApiNotImplementedError("write_protocol", "§7.6 (CRUD /Protocol)")

    async def get_settings_view(self) -> SettingsView:
        raise ApiNotImplementedError("get_settings_view", "§7.7 (GET /SYSTEM)")

    async def write_setting(self, field: str, value: object) -> SettingsWriteResult:
        raise ApiNotImplementedError("write_setting", "§7.8 (PUT /SYSTEM)")

    async def get_job_result(self, job_id: str) -> JobResult | None:
        raise ApiNotImplementedError("get_job_result", "§7.2 (async job mechanism)")

    async def poll_pending_notifications(self) -> tuple[BotNotification, ...]:
        raise ApiNotImplementedError("poll_pending_notifications", "§7.2 (async job mechanism)")
