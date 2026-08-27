"""Fakes for testing `bot/` without a real Telegram connection or a real
API Layer (work_plan.md §8). Mirrors `tests/helpers.py`'s role for
Missions 1/4, kept in its own file since Mission 8 is the first to need
this many fakes for one package.

`FakeBotApiClient` implements the full `BotApiClient` interface with
canned, test-controlled responses — used to test every `bot/` module's
own logic in isolation from `bot.api_client.UnimplementedApiClient`,
which exists to fail loudly, not to be a test double.
"""

from dataclasses import dataclass, field
from typing import Sequence

from bot.api_client import BotApiClient, BotNotification, HoldAnswerOutcome, JobResult, MessageSubmissionResult, ProfileView, SettingsView, UserLookupResult, WriteResult
from bot.telegram_client import TelegramClient


@dataclass
class SentMessage:
    chat_id: str
    text: str
    buttons: tuple[tuple[str, str], ...] | None = None
    reply_to_message_id: str | None = None


class FakeTelegramClient(TelegramClient):
    def __init__(self, token_is_valid: bool = True):
        self.token_is_valid = token_is_valid
        self.sent: list[SentMessage] = []
        self.answered_callback_query_ids: list[str] = []

    async def validate_token(self) -> bool:
        return self.token_is_valid

    async def send_text(self, chat_id: str, text: str) -> None:
        self.sent.append(SentMessage(chat_id=chat_id, text=text))

    async def send_with_buttons(self, chat_id: str, text: str, buttons: Sequence[tuple[str, str]]) -> None:
        self.sent.append(SentMessage(chat_id=chat_id, text=text, buttons=tuple(buttons)))

    async def send_reply(self, chat_id: str, text: str, reply_to_message_id: str | None) -> None:
        self.sent.append(SentMessage(chat_id=chat_id, text=text, reply_to_message_id=reply_to_message_id))

    async def answer_callback_query(self, callback_query_id: str, text: str | None = None) -> None:
        self.answered_callback_query_ids.append(callback_query_id)

    def run_polling(self, register_handlers) -> None:
        raise NotImplementedError("FakeTelegramClient never actually polls")


@dataclass
class FakeBotApiClient(BotApiClient):
    users: dict = field(default_factory=dict)  # telegram_identity -> "viewer" | "commander"
    commander_chat_ids: tuple[str, ...] = ()
    message_submission_result: MessageSubmissionResult | None = None
    clarification_answer_outcome: HoldAnswerOutcome | None = None
    approval_answer_outcome: HoldAnswerOutcome | None = None
    profile_view: ProfileView | None = None
    profile_diff_status: bool | None = None
    protocol_write_result: WriteResult | None = None
    settings_view: SettingsView | None = None
    settings_write_result: WriteResult | None = None
    job_result: JobResult | None = None
    pending_notifications: tuple[BotNotification, ...] = ()

    calls: list[tuple] = field(default_factory=list)

    async def resolve_user(self, telegram_identity: str) -> UserLookupResult:
        self.calls.append(("resolve_user", telegram_identity))
        level = self.users.get(telegram_identity)
        if level is None:
            return UserLookupResult(registered=False)
        return UserLookupResult(registered=True, permission_level=level)

    async def list_commander_chat_ids(self) -> tuple[str, ...]:
        self.calls.append(("list_commander_chat_ids",))
        return self.commander_chat_ids

    async def submit_message(self, text: str, sender_identity: str, source_message_id: str) -> MessageSubmissionResult:
        self.calls.append(("submit_message", text, sender_identity, source_message_id))
        assert self.message_submission_result is not None, "test must set message_submission_result"
        return self.message_submission_result

    async def answer_clarification_hold(self, event_id: str, chosen_classification: str, answering_identity: str) -> HoldAnswerOutcome:
        self.calls.append(("answer_clarification_hold", event_id, chosen_classification, answering_identity))
        assert self.clarification_answer_outcome is not None
        return self.clarification_answer_outcome

    async def answer_approval_hold(self, event_id: str, decision: str, answering_identity: str) -> HoldAnswerOutcome:
        self.calls.append(("answer_approval_hold", event_id, decision, answering_identity))
        assert self.approval_answer_outcome is not None
        return self.approval_answer_outcome

    async def get_profile_view(self, caller_identity: str) -> ProfileView:
        self.calls.append(("get_profile_view", caller_identity))
        assert self.profile_view is not None
        return self.profile_view

    async def get_profile_diff_status(self) -> bool:
        assert self.profile_diff_status is not None
        return self.profile_diff_status

    async def write_protocol(self, action, protocol_payload: dict, caller_identity: str) -> WriteResult:
        self.calls.append(("write_protocol", action, protocol_payload, caller_identity))
        assert self.protocol_write_result is not None
        return self.protocol_write_result

    async def get_settings_view(self, caller_identity: str) -> SettingsView:
        self.calls.append(("get_settings_view", caller_identity))
        assert self.settings_view is not None
        return self.settings_view

    async def write_setting(self, field: str, value: object, caller_identity: str) -> WriteResult:
        self.calls.append(("write_setting", field, value, caller_identity))
        assert self.settings_write_result is not None
        return self.settings_write_result

    async def get_job_result(self, job_id: str, caller_identity: str) -> JobResult | None:
        self.calls.append(("get_job_result", job_id, caller_identity))
        return self.job_result

    async def poll_pending_notifications(self, since: int) -> tuple[tuple[BotNotification, ...], int]:
        self.calls.append(("poll_pending_notifications", since))
        return self.pending_notifications, since + len(self.pending_notifications)
