"""HTTP and Telegram transport implementations."""

import asyncio

import json
import random
import uuid

from urllib.parse import quote

import httpx

from typing import Literal

from bot.contracts import (
    ApiRequestError,
    BOT_SERVICE_IDENTITY,
    BotApiClient,
    BotNotification,
    EventDataNeededNotice,
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
    TracePollResult,
    UncertainVerdictNotice,
    UserLookupResult,
)

from abc import ABC, abstractmethod

from typing import Callable, Sequence

from bot.interactions import split_message
from tools import get_trace_id, new_trace_id, stage_context

def _do_request(url: str, method: str, identity: str, request_payload: dict | None) -> tuple[int, dict]:
    try:
        response = httpx.request(
            method,
            url,
            headers={"X-Identity": identity},
            json=request_payload,
            timeout=httpx.Timeout(connect=2.0, pool=2.0, write=5.0, read=75.0),
        )
        if not response.content:
            return response.status_code, {}
        try:
            return response.status_code, response.json()
        except ValueError:
            return response.status_code, {"message": response.text}
    except httpx.HTTPError as exc:
        raise ApiRequestError(None, str(exc)) from exc


class HttpApiClient(BotApiClient):
    def __init__(self, base_url: str, bot_service_key: str | None = None):
        self._base_url = base_url.rstrip("/")
        self._client: httpx.AsyncClient | None = None
        self._bot_service_key = bot_service_key

    async def start(self) -> None:
        if self._client is None:
            self._client = self._build_client()

    async def close(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            await client.aclose()

    def _build_client(self) -> httpx.AsyncClient:
        timeout = httpx.Timeout(connect=2.0, pool=2.0, write=5.0, read=75.0)
        limits = httpx.Limits(max_connections=20, max_keepalive_connections=10)
        return httpx.AsyncClient(base_url=self._base_url, timeout=timeout, limits=limits)

    async def _call(
        self,
        method: str,
        path: str,
        identity: str,
        request_payload: dict | None = None,
        *,
        read_timeout: float | None = None,
        trace_id_override: str | None = None,
    ) -> tuple[int, dict]:
        persistent_client = self._client
        client = persistent_client or self._build_client()
        headers = {
            "X-Identity": identity,
            "X-Trace-ID": trace_id_override or get_trace_id() or new_trace_id(),
            "X-Client-Request-ID": uuid.uuid4().hex,
        }
        # Only the bot-service identity needs this — a human Telegram identity is
        # authenticated purely by that identity string, unaffected either way.
        if identity == BOT_SERVICE_IDENTITY and self._bot_service_key:
            headers["X-Service-Key"] = self._bot_service_key
        attempts = 3 if method == "GET" else 1

        try:
            for attempt in range(attempts):
                try:
                    timeout = None if read_timeout is None else httpx.Timeout(connect=2.0, pool=2.0, write=5.0, read=read_timeout)
                    with stage_context("bot_http"):
                        response = await client.request(method, path, headers=headers, json=request_payload, timeout=timeout)
                    try:
                        payload = response.json() if response.content else {}
                    except ValueError:
                        payload = {"message": response.text}

                    if method != "GET" or response.status_code < 500 or attempt == attempts - 1:
                        return response.status_code, payload
                except (httpx.TimeoutException, httpx.TransportError) as exc:
                    if attempt == attempts - 1:
                        raise ApiRequestError(None, str(exc)) from exc

                await asyncio.sleep(random.uniform(0.0, 0.2 * (2 ** attempt)))
        finally:
            if persistent_client is None:
                await client.aclose()

        raise ApiRequestError(None, "request failed without a response")

    def _raise_for_error(self, status: int, payload: dict) -> None:
        raise ApiRequestError(status, payload.get("message", ""), payload.get("error_class"), payload.get("field"))


    async def resolve_user(self, telegram_identity: str) -> UserLookupResult:
        status, response_payload = await self._call("GET", f"/User/{quote(telegram_identity, safe='')}", BOT_SERVICE_IDENTITY)
        if status >= 400:
            self._raise_for_error(status, response_payload)
        return UserLookupResult(registered=response_payload["registered"], permission_level=response_payload["permission_level"])


    async def list_commander_chat_ids(self) -> tuple[str, ...]:
        status, response_payload = await self._call("GET", "/Commanders", BOT_SERVICE_IDENTITY)
        if status >= 400:
            self._raise_for_error(status, response_payload)
        return tuple(c["telegram_identity"] for c in response_payload["commanders"])

    async def submit_message(
        self,
        text: str,
        sender_identity: str,
        source_message_id: str,
        conversation_id: str | None = None,
        trace_id: str | None = None,
    ) -> MessageSubmissionResult:
        body = {"text": text, "sender_identity": sender_identity, "source_message_id": source_message_id}
        if conversation_id is not None:
            body["conversation_id"] = conversation_id
        status, response_payload = await self._call(
            "POST", "/Msg", sender_identity, body, trace_id_override=trace_id
        )
        if status >= 400:
            self._raise_for_error(status, response_payload)

        if response_payload["taken_as"] in {"question", "conversational", "clarification"}:
            return MessageSubmissionResult(
                kind=response_payload["taken_as"],
                answer_text=response_payload.get("answer"),
                provenance=response_payload.get("provenance"),
            )

        return MessageSubmissionResult(
            kind=response_payload["taken_as"],
            answer_text=response_payload.get("answer"),
            job_id=response_payload.get("event_id"),
            awaiting_approval=False,
        )

    async def answer_clarification_hold(self, event_id: str, chosen_classification: str, answering_identity: str) -> HoldAnswerOutcome:
        status, response_payload = await self._call("POST", f"/Clarify/{event_id}", answering_identity, {"classification": chosen_classification})
        return self._hold_answer_outcome(status, response_payload, invalid_field_status="invalid_classification", resolved_status="resolved")

    async def answer_approval_hold(self, event_id: str, decision: str, answering_identity: str) -> HoldAnswerOutcome:
        status, response_payload = await self._call("POST", f"/Approve/{event_id}", answering_identity, {"decision": decision})

        if status == 200 and response_payload.get("status") == "declined":
            return HoldAnswerOutcome(status="rejected")
        if status == 202:
            return HoldAnswerOutcome(status="approved")

        return self._hold_answer_outcome(status, response_payload, invalid_field_status="invalid_candidate", resolved_status="approved")

    def _hold_answer_outcome(self, status: int, response_payload: dict, invalid_field_status: str, resolved_status: str) -> HoldAnswerOutcome:
        if status in (401, 403):
            return HoldAnswerOutcome(status="unauthorized", message=response_payload.get("message", ""))
        if status == 404:
            return HoldAnswerOutcome(status="not_found", message=response_payload.get("message", ""))
        if status == 409:
            resolved_by = response_payload.get("resolved_by")
            message = response_payload.get("message", "")
            if resolved_by is None:
                resolved_by, message = self._parse_already_resolved_message(message)
            return HoldAnswerOutcome(status="not_found", resolved_by=resolved_by, message=message)
        if status == 400:
            return HoldAnswerOutcome(status=invalid_field_status, message=response_payload.get("message", ""))
        if status >= 400:
            self._raise_for_error(status, response_payload)

        return HoldAnswerOutcome(status=resolved_status)

    @staticmethod
    def _parse_already_resolved_message(message: str) -> tuple[str | None, str]:
        marker = "already resolved by '"
        lowered = message.lower()
        if marker not in lowered:
            return None, message
        marker_index = lowered.index(marker)
        after = message[marker_index + len(marker):]
        resolved_by = after.split("'", 1)[0]
        return resolved_by, message

    async def _get_system(self, identity: str) -> dict:
        status, response_payload = await self._call("GET", "/SYSTEM", identity)
        if status >= 400:
            self._raise_for_error(status, response_payload)
        return response_payload

    async def get_profile_view(self, caller_identity: str) -> ProfileView:
        # `agents`/`protocols` are commander-only (view_system_internals) — GET
        # /SYSTEM omits them entirely for a viewer rather than sending an empty
        # hint, so they default to empty here rather than KeyError.
        response_payload = await self._get_system(caller_identity)
        protocols = tuple(
            ProtocolView(name=protocol["name"], description=protocol["description"], criticality=protocol["criticality"], approval_flag=protocol["approval_flag"])
            for protocol in response_payload.get("protocols", ())
        )
        return ProfileView(
            profile_name=response_payload["profile"],
            agent_names=tuple(response_payload.get("agents", ())),
            protocols=protocols,
            event_types=tuple(response_payload["event_types"]),
            areas=tuple(response_payload["areas"]),
        )

    async def get_profile_diff_status(self) -> bool:
        response_payload = await self._get_system(BOT_SERVICE_IDENTITY)
        return response_payload["profile_file_changed"]

    async def write_protocol(self, action: Literal["add", "edit", "remove"], protocol_payload: dict, caller_identity: str) -> WriteResult:
        name = protocol_payload.get("name", "")
        if action == "add":
            status, response_payload = await self._call("POST", "/Protocol", caller_identity, protocol_payload)
        elif action == "edit":
            status, response_payload = await self._call("PUT", f"/Protocol/{quote(name, safe='')}", caller_identity, protocol_payload)
        else:
            status, response_payload = await self._call("DELETE", f"/Protocol/{quote(name, safe='')}", caller_identity, None)

        if status in (401, 403):
            self._raise_for_error(status, response_payload)
        if status >= 400:
            return WriteResult(accepted=False, message=response_payload.get("message", ""))
        return WriteResult(accepted=True, message=response_payload.get("message", ""))

    async def get_settings_view(self, caller_identity: str) -> SettingsView:
        response_payload = await self._get_system(caller_identity)
        # `settings` is commander-only (view_settings) — absent for a viewer.
        # bot.app already refuses this client-side before ever calling here;
        # this is a defensive fallback for a direct caller of this interface.
        if "settings" not in response_payload:
            raise ApiRequestError(403, "settings are not available at your permission level", error_class="authorization_error")
        settings = response_payload["settings"]
        return SettingsView(
            retry_count=settings["retry_count"],
            risk_threshold=settings["risk_threshold"],
            lookback_window_days=settings["lookback_window_days"],
        )

    async def write_setting(self, field: str, value: object, caller_identity: str) -> WriteResult:
        status, response_payload = await self._call("PUT", "/SYSTEM", caller_identity, {field: value})

        if status in (401, 403):
            self._raise_for_error(status, response_payload)
        if status >= 400:
            return WriteResult(accepted=False, message=response_payload.get("message", ""))
        return WriteResult(accepted=True, message=f"'{field}' is now {response_payload[field]}.")

    async def get_job_result(self, job_id: str, caller_identity: str) -> JobResult | None:
        status, response_payload = await self._call("GET", f"/Job/{job_id}", caller_identity)
        if status == 404:
            return None
        if status >= 400:
            self._raise_for_error(status, response_payload)

        outcome = response_payload["status"]
        if outcome not in ("succeeded", "failed", "uncertain", "closed_on_precedent", "declined"):
            return None

        return JobResult(
            job_id=job_id,
            outcome=outcome,
            insight_text=response_payload.get("insight_text", ""),
            steps_completed=tuple(response_payload.get("steps_completed", ())),
            failure_reason=response_payload.get("detail") if outcome == "failed" else None,
            failed_step_agent_name=response_payload.get("failed_step_agent_name"),
        )

    async def poll_pending_notifications(self, since: int, wait_seconds: int = 0) -> tuple[tuple[BotNotification, ...], int]:
        status, response_payload = await self._call(
            "GET",
            f"/Notifications?since={since}&wait_seconds={wait_seconds}",
            BOT_SERVICE_IDENTITY,
            read_timeout=max(5.0, wait_seconds + 5.0),
        )
        if status >= 400:
            self._raise_for_error(status, response_payload)

        notifications = tuple(
            BotNotification(
                kind=entry["kind"],
                target_chat_ids=tuple(entry["target_chat_ids"]),
                payload=self._parse_notification_payload(entry["kind"], entry["payload"]),
                reply_to_message_id=entry.get("reply_to_message_id"),
                trace_id=entry.get("trace_id"),
            )
            for entry in response_payload["notifications"]
        )
        return notifications, response_payload["next_cursor"]

    async def poll_trace(
        self,
        trace_id: str,
        since: int,
        wait_seconds: int,
        caller_identity: str,
    ) -> TracePollResult:
        status, response_payload = await self._call(
            "GET",
            f"/Trace/{quote(trace_id, safe='')}?since={since}&wait_seconds={wait_seconds}",
            caller_identity,
            read_timeout=max(5.0, wait_seconds + 5.0),
            trace_id_override=new_trace_id(),
        )
        if status >= 400:
            self._raise_for_error(status, response_payload)
        return TracePollResult(
            messages=tuple(entry["text"] for entry in response_payload["entries"]),
            next_cursor=response_payload["next_cursor"],
            terminal=bool(response_payload.get("terminal")),
        )

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
        if kind == "event_data_hold":
            return EventDataNeededNotice(
                hold_id=payload["hold_id"],
                event_id=payload["event_id"],
                question=payload["question"],
                missing_fields=tuple(payload.get("missing_fields", ())),
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
    async def send_activity(self, chat_id: str, action: str) -> None:
        """Show non-text activity feedback when the transport supports it."""
    @abstractmethod
    async def validate_token(self) -> bool:
        """True if Telegram accepts the configured token, False if it rejects it outright (§8.1's "fail at startup ..."""

    @abstractmethod
    async def send_text(self, chat_id: str, text: str) -> None: ...

    @abstractmethod
    async def send_status(self, chat_id: str, text: str) -> str:
        """Send a temporary status and return its transport-specific message ID."""

    @abstractmethod
    async def edit_status(self, chat_id: str, message_id: str, text: str) -> None:
        """Replace a previously sent status message."""

    @abstractmethod
    async def delete_status(self, chat_id: str, message_id: str) -> None:
        """Delete a stale status message on a best-effort recovery path."""

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
        with stage_context("telegram_send"):
            for chunk in split_message(text):
                await self._application.bot.send_message(chat_id=chat_id, text=chunk)

    async def send_status(self, chat_id: str, text: str) -> str:
        with stage_context("telegram_send"):
            message = await self._application.bot.send_message(chat_id=chat_id, text=text)
        return str(message.message_id)

    async def edit_status(self, chat_id: str, message_id: str, text: str) -> None:
        with stage_context("telegram_edit"):
            await self._application.bot.edit_message_text(
                chat_id=chat_id,
                message_id=int(message_id),
                text=text,
            )

    async def delete_status(self, chat_id: str, message_id: str) -> None:
        with stage_context("telegram_delete"):
            await self._application.bot.delete_message(chat_id=chat_id, message_id=int(message_id))

    async def send_activity(self, chat_id: str, action: str) -> None:
        await self._application.bot.send_chat_action(chat_id=chat_id, action=action)

    async def send_with_buttons(self, chat_id: str, text: str, buttons: Sequence[tuple[str, str]]) -> None:
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        markup = InlineKeyboardMarkup([[InlineKeyboardButton(label, callback_data=callback_data)] for label, callback_data in buttons])

        with stage_context("telegram_send"):
            chunks = split_message(text)
            for chunk in chunks[:-1]:
                await self._application.bot.send_message(chat_id=chat_id, text=chunk)
            await self._application.bot.send_message(chat_id=chat_id, text=chunks[-1], reply_markup=markup)

    async def send_reply(self, chat_id: str, text: str, reply_to_message_id: str | None) -> None:
        with stage_context("telegram_send"):
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
