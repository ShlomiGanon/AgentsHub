"""Consolidated responsibility module for client."""

import asyncio

import json

import urllib.error

import urllib.parse

import urllib.request

from typing import Literal

from bot.contracts import (
    ApiRequestError,
    BOT_SERVICE_IDENTITY,
    BotApiClient,
    BotNotification,
    FailureNotice,
    HeldApprovalNotice,
    HeldClarificationNotice,
    HoldAnswerOutcome,
    JobResult,
    MessageSubmissionResult,
    NoMatchNotice,
    PrecedentClosureNotice,
    ProfileView,
    ProtocolView,
    WriteResult,
    SettingsView,
    UncertainVerdictNotice,
    UserLookupResult,
)

from abc import ABC, abstractmethod

from typing import Callable, Sequence

from bot.presentation import split_message

def _do_request(url: str, method: str, identity: str, body: dict | None) -> tuple[int, dict]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"X-Identity": identity}
    if data is not None:
        headers["Content-Type"] = "application/json"

    request = urllib.request.Request(url, data=data, method=method, headers=headers)

    try:
        with urllib.request.urlopen(request) as response:
            raw = response.read()
            return response.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            payload = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            payload = {"message": raw.decode("utf-8", errors="replace")}
        return exc.code, payload
    except urllib.error.URLError as exc:
        raise ApiRequestError(None, str(exc.reason)) from exc


class HttpApiClient(BotApiClient):
    def __init__(self, base_url: str):
        self._base_url = base_url.rstrip("/")

    async def _call(self, method: str, path: str, identity: str, body: dict | None = None) -> tuple[int, dict]:
        url = f"{self._base_url}{path}"
        return await asyncio.to_thread(_do_request, url, method, identity, body)

    def _raise_for_error(self, status: int, payload: dict) -> None:
        raise ApiRequestError(status, payload.get("message", ""), payload.get("error_class"), payload.get("field"))


    async def resolve_user(self, telegram_identity: str) -> UserLookupResult:
        status, body = await self._call("GET", f"/User/{urllib.parse.quote(telegram_identity, safe='')}", BOT_SERVICE_IDENTITY)
        if status >= 400:
            self._raise_for_error(status, body)
        return UserLookupResult(registered=body["registered"], permission_level=body["permission_level"])


    async def list_commander_chat_ids(self) -> tuple[str, ...]:
        status, body = await self._call("GET", "/Commanders", BOT_SERVICE_IDENTITY)
        if status >= 400:
            self._raise_for_error(status, body)
        return tuple(c["telegram_identity"] for c in body["commanders"])

    async def submit_message(self, text: str, sender_identity: str, source_message_id: str) -> MessageSubmissionResult:
        status, body = await self._call(
            "POST", "/Msg", sender_identity, {"text": text, "sender_identity": sender_identity, "source_message_id": source_message_id}
        )
        if status >= 400:
            self._raise_for_error(status, body)

        if body["taken_as"] == "question":
            return MessageSubmissionResult(kind="question", answer_text=body.get("answer"))

        # POST /Msg's acknowledgment never knows whether this lands on an
        # approval hold — §8.3's own explicit design: risk assessment and
        # protocol selection haven't run yet at acknowledgment time, so
        # that outcome always arrives later, via GET /Notifications
        # (§8.12) or GET /Job (§7.2) polling — never synchronously here.
        return MessageSubmissionResult(kind=body["taken_as"], job_id=body.get("event_id"), awaiting_approval=False)

    async def answer_clarification_hold(self, event_id: str, chosen_classification: str, answering_identity: str) -> HoldAnswerOutcome:
        status, body = await self._call("POST", f"/Clarify/{event_id}", answering_identity, {"classification": chosen_classification})
        return self._hold_answer_outcome(status, body, invalid_field_status="invalid_classification", resolved_status="resolved")

    async def answer_approval_hold(self, event_id: str, decision: str, answering_identity: str) -> HoldAnswerOutcome:
        status, body = await self._call("POST", f"/Approve/{event_id}", answering_identity, {"decision": decision})

        if status == 200 and body.get("status") == "declined":
            return HoldAnswerOutcome(status="rejected")
        if status == 202:
            return HoldAnswerOutcome(status="approved")

        return self._hold_answer_outcome(status, body, invalid_field_status="invalid_candidate", resolved_status="approved")

    def _hold_answer_outcome(self, status: int, body: dict, invalid_field_status: str, resolved_status: str) -> HoldAnswerOutcome:
        if status in (401, 403):
            return HoldAnswerOutcome(status="unauthorized", message=body.get("message", ""))
        if status == 404:
            return HoldAnswerOutcome(status="not_found", message=body.get("message", ""))
        if status == 409:
            resolved_by, message = self._parse_already_resolved_message(body.get("message", ""))
            return HoldAnswerOutcome(status="not_found", resolved_by=resolved_by, message=message)
        if status == 400:
            return HoldAnswerOutcome(status=invalid_field_status, message=body.get("message", ""))
        if status >= 400:
            self._raise_for_error(status, body)

        return HoldAnswerOutcome(status=resolved_status)

    @staticmethod
    def _parse_already_resolved_message(message: str) -> tuple[str | None, str]:
        marker = "already resolved by '"
        if marker not in message:
            return None, message
        after = message.split(marker, 1)[1]
        resolved_by = after.split("'", 1)[0]
        return resolved_by, message

    async def _get_system(self, identity: str) -> dict:
        status, body = await self._call("GET", "/SYSTEM", identity)
        if status >= 400:
            self._raise_for_error(status, body)
        return body

    async def get_profile_view(self, caller_identity: str) -> ProfileView:
        body = await self._get_system(caller_identity)
        protocols = tuple(
            ProtocolView(name=p["name"], description=p["description"], criticality=p["criticality"], approval_flag=p["approval_flag"])
            for p in body["protocols"]
        )
        return ProfileView(
            profile_name=body["profile"],
            agent_names=tuple(body["agents"]),
            protocols=protocols,
            event_types=tuple(body["event_types"]),
            areas=tuple(body["areas"]),
        )

    async def get_profile_diff_status(self) -> bool:
        # Deliberately still BOT_SERVICE_IDENTITY — this carries no
        # permission-sensitive content (a hash comparison) and no
        # dedicated action key exists for it; not one of Problem 1's five.
        body = await self._get_system(BOT_SERVICE_IDENTITY)
        return body["profile_file_changed"]

    async def write_protocol(self, action: Literal["add", "edit", "remove"], protocol_payload: dict, caller_identity: str) -> WriteResult:
        name = protocol_payload.get("name", "")
        if action == "add":
            status, body = await self._call("POST", "/Protocol", caller_identity, protocol_payload)
        elif action == "edit":
            status, body = await self._call("PUT", f"/Protocol/{urllib.parse.quote(name, safe='')}", caller_identity, protocol_payload)
        else:
            status, body = await self._call("DELETE", f"/Protocol/{urllib.parse.quote(name, safe='')}", caller_identity, None)

        if status in (401, 403):
            self._raise_for_error(status, body)
        if status >= 400:
            return WriteResult(accepted=False, message=body.get("message", ""))
        return WriteResult(accepted=True, message=body.get("message", ""))

    async def get_settings_view(self, caller_identity: str) -> SettingsView:
        body = await self._get_system(caller_identity)
        settings = body["settings"]
        return SettingsView(
            retry_count=settings["retry_count"],
            risk_threshold=settings["risk_threshold"],
            lookback_window_days=settings["lookback_window_days"],
        )

    async def write_setting(self, field: str, value: object, caller_identity: str) -> WriteResult:
        status, body = await self._call("PUT", "/SYSTEM", caller_identity, {field: value})

        if status in (401, 403):
            self._raise_for_error(status, body)
        if status >= 400:
            return WriteResult(accepted=False, message=body.get("message", ""))
        return WriteResult(accepted=True, message=f"'{field}' is now {body[field]}.")

    async def get_job_result(self, job_id: str, caller_identity: str) -> JobResult | None:
        status, body = await self._call("GET", f"/Job/{job_id}", caller_identity)
        if status == 404:
            return None
        if status >= 400:
            self._raise_for_error(status, body)

        # GET /Job/<event_id> reports every in-flight state too (queued,
        # running, held_for_*) through the same "status" field a finished
        # job's outcome lives in — this method's own declared return type,
        # JobResult | None, has no "still pending" shape to construct, so
        # anything that isn't one of the five real terminal outcomes
        # reports as no result yet, the same as a job that doesn't exist.
        # (Declared by BotApiClient but not yet called from anywhere else
        # in bot/* — every result today is delivered via the notification
        # feed's push path, §8.9, not by polling this.)
        outcome = body["status"]
        if outcome not in ("succeeded", "failed", "uncertain", "closed_on_precedent", "declined"):
            return None

        return JobResult(
            job_id=job_id,
            outcome=outcome,
            insight_text=body.get("insight_text", ""),
            steps_completed=tuple(body.get("steps_completed", ())),
            failure_reason=body.get("detail") if outcome == "failed" else None,
            failed_step_agent_name=body.get("failed_step_agent_name"),
        )

    async def poll_pending_notifications(self, since: int) -> tuple[tuple[BotNotification, ...], int]:
        status, body = await self._call("GET", f"/Notifications?since={since}", BOT_SERVICE_IDENTITY)
        if status >= 400:
            self._raise_for_error(status, body)

        notifications = tuple(
            BotNotification(
                kind=entry["kind"],
                target_chat_ids=tuple(entry["target_chat_ids"]),
                payload=self._parse_notification_payload(entry["kind"], entry["payload"]),
                reply_to_message_id=entry.get("reply_to_message_id"),
            )
            for entry in body["notifications"]
        )
        return notifications, body["next_cursor"]

    @staticmethod
    def _parse_notification_payload(kind: str, payload: dict):
        if kind == "clarification_hold":
            return HeldClarificationNotice(
                hold_id=payload["hold_id"],
                event_id=payload["event_id"],
                raw_text=payload["raw_text"],
                unresolved_field=payload["unresolved_field"],
                available_classifications=tuple(payload["available_classifications"]),
            )
        if kind == "approval_hold":
            return HeldApprovalNotice(
                hold_id=payload["hold_id"],
                event_id=payload["event_id"],
                reason=payload["reason"],
                risk_level=payload["risk_level"],
                risk_reason=payload["risk_reason"],
                selected_protocol_name=payload.get("selected_protocol_name"),
                candidate_protocol_names=tuple(payload.get("candidate_protocol_names", ())),
            )
        if kind == "uncertain_verdict":
            return UncertainVerdictNotice(event_id=payload["event_id"], insight_text=payload["insight_text"])
        if kind == "precedent_closure":
            return PrecedentClosureNotice(
                event_id=payload["event_id"],
                raw_text=payload["raw_text"],
                matched_precedent_event_id=payload["matched_precedent_event_id"],
                precedent_ending=payload["precedent_ending"],
            )
        if kind == "no_match_notice":
            return NoMatchNotice(
                event_id=payload["event_id"],
                raw_text=payload["raw_text"],
                reason=payload["reason"],
                risk_level=payload["risk_level"],
                risk_reason=payload["risk_reason"],
            )
        if kind == "job_finished":
            return JobResult(
                job_id=payload["job_id"],
                outcome=payload["outcome"],
                insight_text=payload.get("insight_text", ""),
                steps_completed=tuple(payload.get("steps_completed", ())),
                failure_reason=payload.get("failure_reason"),
                failed_step_agent_name=payload.get("failed_step_agent_name"),
            )
        if kind == "job_failed":
            return FailureNotice(
                event_id=payload["job_id"],
                failed_step_agent_name=payload.get("failed_step_agent_name"),
                failure_reason=payload.get("failure_reason") or "",
                steps_completed_before_failure=tuple(payload.get("steps_completed", ())),
            )
        raise ValueError(f"unknown notification kind: {kind!r}")

class TelegramClient(ABC):
    @abstractmethod
    async def validate_token(self) -> bool:
        """True if Telegram accepts the configured token, False if it rejects it outright (§8.1's "fail at startup ..."""

    @abstractmethod
    async def send_text(self, chat_id: str, text: str) -> None: ...

    @abstractmethod
    async def send_with_buttons(self, chat_id: str, text: str, buttons: Sequence[tuple[str, str]]) -> None:
        """`buttons` is a sequence of (label, callback_data) pairs, laid out one per row — used for clarification/approval choices (§8.4, §8.5), never for free text (§8.4's own "buttons ra..."""

    @abstractmethod
    async def send_reply(self, chat_id: str, text: str, reply_to_message_id: str | None) -> None:
        """Like `send_text`, but referencing the original message when one is given — §8.9's "reference the original message when delivering" (minutes may have passed; the sender may have..."""

    @abstractmethod
    async def answer_callback_query(self, callback_query_id: str, text: str | None = None) -> None:
        """Acknowledge a button press so Telegram clears its spinner."""

    @abstractmethod
    def run_polling(self, register_handlers: Callable[[object], None]) -> None:
        """Register handlers on the underlying application via `register_handlers(application)`, then block, polling for updates, until the process is stopped."""


class PTBTelegramClient(TelegramClient):
    def __init__(self, token: str):
        from telegram.ext import ApplicationBuilder

        self._application = ApplicationBuilder().token(token).build()

    async def validate_token(self) -> bool:
        from telegram.error import TelegramError

        try:
            await self._application.bot.get_me()
            return True
        except TelegramError:
            return False

    async def send_text(self, chat_id: str, text: str) -> None:
        for chunk in split_message(text):
            await self._application.bot.send_message(chat_id=chat_id, text=chunk)

    async def send_with_buttons(self, chat_id: str, text: str, buttons: Sequence[tuple[str, str]]) -> None:
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        markup = InlineKeyboardMarkup([[InlineKeyboardButton(label, callback_data=data)] for label, data in buttons])

        chunks = split_message(text)
        for chunk in chunks[:-1]:
            await self._application.bot.send_message(chat_id=chat_id, text=chunk)
        await self._application.bot.send_message(chat_id=chat_id, text=chunks[-1], reply_markup=markup)

    async def send_reply(self, chat_id: str, text: str, reply_to_message_id: str | None) -> None:
        chunks = split_message(text)
        if chunks:
            await self._application.bot.send_message(chat_id=chat_id, text=chunks[0], reply_to_message_id=reply_to_message_id)
        for chunk in chunks[1:]:
            await self._application.bot.send_message(chat_id=chat_id, text=chunk)

    async def answer_callback_query(self, callback_query_id: str, text: str | None = None) -> None:
        await self._application.bot.answer_callback_query(callback_query_id=callback_query_id, text=text)

    def run_polling(self, register_handlers: Callable[[object], None]) -> None:
        register_handlers(self._application)
        self._application.run_polling()
