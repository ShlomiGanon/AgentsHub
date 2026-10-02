"""Approval and clarification holds, prompts, and the pending-holds queue."""

import logging
from typing import TYPE_CHECKING

from messages import MessageCatalog, MessageCatalogError

from bot.interaction_format import _catalog, _risk_level_word, format_header, message_catalog_for
from bot.interaction_users import check_permission, resolve_caller

if TYPE_CHECKING:
    from bot.contracts import (
        BotDeps,
        HeldApprovalNotice,
        HeldClarificationNotice,
        HoldAnswerOutcome,
        HoldEscalationNotice,
        NoMatchNotice,
        ResourceUnavailableAlertNotice,
        UncertainVerdictNotice,
    )

from auth.permissions import RequestedOperation

logger = logging.getLogger(__name__)

_EVENT_DATA_REPLY_TARGETS: dict[tuple[str, str], str] = {}



def register_event_data_reply_target(chat_id: str, telegram_message_id: str, event_id: str) -> None:
    """Bind a Telegram message id to the event it asked extra data for."""

    _EVENT_DATA_REPLY_TARGETS[(str(chat_id), str(telegram_message_id))] = event_id



def event_data_event_for_reply(chat_id: str, telegram_message_id: str) -> str | None:
    """Event id for a reply targeting that Telegram message, if any."""

    return _EVENT_DATA_REPLY_TARGETS.get((str(chat_id), str(telegram_message_id)))



def unregister_event_data_reply_target(event_id: str) -> None:
    """Drop reply bindings for an event once the extra data is no longer needed."""

    stale = [key for key, value in _EVENT_DATA_REPLY_TARGETS.items() if value == event_id]
    for key in stale:
        _EVENT_DATA_REPLY_TARGETS.pop(key, None)


CALLBACK_PREFIX = "approve"



def build_callback_data(event_id: str, choice: str) -> str:
    """Callback payload for an approval button."""

    return f"{CALLBACK_PREFIX}:{event_id}:{choice}"



def parse_callback_data(data: str) -> tuple[str, str]:
    """Event id and choice encoded in an approval callback."""

    _, event_id, choice = data.split(":", 2)
    return event_id, choice



def format_approval_prompt(
    notice: "HeldApprovalNotice", catalog: MessageCatalog | None = None
) -> tuple[str, list[tuple[str, str]]]:
    """Return (message text, buttons) — buttons differ by hold reason."""

    messages = _catalog(catalog)
    header = format_header("approval_needed", messages)
    common = messages.text(
        "approval.risk", risk_level=_risk_level_word(notice.risk_level, messages), risk_reason=notice.risk_reason
    )

    if notice.reason == "flagged_protocol":
        text = messages.text(
            "approval.flagged",
            header=header,
            protocol_name=notice.selected_protocol_name,
            risk=common,
        )
        buttons = [
            (messages.text("approval.approve"), build_callback_data(notice.event_id, "approved")),
            (messages.text("approval.reject"), build_callback_data(notice.event_id, "rejected")),
        ]
        return text, buttons

    if notice.reason == "ambiguous_selection":
        candidates = ", ".join(notice.candidate_protocol_names) or messages.text("common.none")
        text = messages.text(
            "approval.ambiguous", header=header, candidates=candidates, risk=common
        )
        buttons = [(name, build_callback_data(notice.event_id, name)) for name in notice.candidate_protocol_names]
        return text, buttons

    raise ValueError(f"unrecognized approval hold reason: {notice.reason!r}")



_OPEN_APPROVAL_HOLDS: set[str] = set()



def register_open_approval_hold(event_id: str) -> None:
    """Remember an event that still needs an approval answer in this process."""

    _OPEN_APPROVAL_HOLDS.add(event_id)



def unregister_open_approval_hold(event_id: str) -> None:
    """Drop a hold after it is answered or cancelled."""

    _OPEN_APPROVAL_HOLDS.discard(event_id)



def get_open_approval_holds() -> list[str]:
    """Event IDs this process still has an unanswered approval prompt for."""

    return list(_OPEN_APPROVAL_HOLDS)



async def hydrate_open_approval_holds(api_client) -> None:
    """Load unresolved approval holds from GET /Holds/Pending into the in-memory set."""

    from bot.contracts import BOT_SERVICE_IDENTITY

    try:
        pending = await api_client.fetch_pending_holds(BOT_SERVICE_IDENTITY)
    except Exception:
        logger.exception(
            "failed to load open approval holds from the API",
            extra={"event": "approval_holds_load_failed"},
        )
        return
    for item in pending.get("holds") or []:
        if item.get("kind") == "approval" and item.get("event_id"):
            register_open_approval_hold(str(item["event_id"]))




async def push_approval_prompt(deps: "BotDeps", notice: "HeldApprovalNotice") -> None:
    """Send the approval prompt with buttons to every commander private chat."""

    register_open_approval_hold(notice.event_id)
    text, buttons = format_approval_prompt(notice, message_catalog_for(deps))

    for chat_id in await deps.api_client.list_commander_chat_ids():
        if not chat_id or chat_id == "bot-service":
            continue
        try:
            await deps.telegram_client.send_with_buttons(chat_id, text, buttons)
        except Exception as exc:
            logger.warning("failed to send approval prompt to %s: %s", chat_id, exc)



def format_uncertain_verdict_notice(
    notice: "UncertainVerdictNotice", catalog: MessageCatalog | None = None
) -> str:
    """Commander notice that the run ended uncertain."""

    messages = _catalog(catalog)
    return messages.text(
        "notice.uncertain",
        header=format_header("uncertain_verdict", messages),
        event_id=notice.event_id,
        insight=notice.insight_text,
    )



async def notify_uncertain_verdict(deps: "BotDeps", notice: "UncertainVerdictNotice") -> None:
    """Send the uncertain-verdict notice to commander private chats."""

    text = format_uncertain_verdict_notice(notice, message_catalog_for(deps))

    for chat_id in await deps.api_client.list_commander_chat_ids():
        if not chat_id or chat_id == "bot-service":
            continue
        try:
            await deps.telegram_client.send_text(chat_id, text)
        except Exception as exc:
            logger.warning("failed to send uncertain verdict notice to %s: %s", chat_id, exc)



def format_resource_unavailable_alert_notice(
    notice: "ResourceUnavailableAlertNotice", catalog: MessageCatalog | None = None
) -> str:
    """Commander alert that a required resource is unavailable."""

    messages = _catalog(catalog)
    return messages.text(
        "notice.resource_unavailable_alert",
        header=format_header("resource_unavailable_alert", messages),
        event_id=notice.event_id,
        alert=notice.alert_text,
    )



async def notify_resource_unavailable_alert(deps: "BotDeps", notice: "ResourceUnavailableAlertNotice") -> None:
    """Commander-only, private-chat delivery — mirrors `notify_uncertain_verdict` exactly.
    Never sent to the reporter's own chat: that chat only ever gets the plain job_finished
    reply, built from `report_text`, which never carries this alert's alternatives."""

    text = format_resource_unavailable_alert_notice(notice, message_catalog_for(deps))

    for chat_id in await deps.api_client.list_commander_chat_ids():
        if not chat_id or chat_id == "bot-service":
            continue
        try:
            await deps.telegram_client.send_text(chat_id, text)
        except Exception as exc:
            logger.warning("failed to send resource unavailable alert to %s: %s", chat_id, exc)



def format_hold_escalation_notice(notice: "HoldEscalationNotice", catalog: MessageCatalog | None = None) -> str:
    """Commander alert that a hold sat unanswered past the escalation window."""

    messages = _catalog(catalog)
    return messages.text(
        "notice.hold_escalation",
        header=format_header("hold_escalation", messages),
        event_id=notice.event_id,
        alert=notice.alert_text,
    )



async def notify_hold_escalation(deps: "BotDeps", notice: "HoldEscalationNotice") -> None:
    """Commander-only, private-chat delivery — mirrors `notify_resource_unavailable_alert`
    exactly (item 8: an unresolved hold that went past the configured escalation window with no
    answer). Never sent to the original sender's own chat, which keeps getting only its own
    reminder of the same original prompt."""

    text = format_hold_escalation_notice(notice, message_catalog_for(deps))

    for chat_id in await deps.api_client.list_commander_chat_ids():
        if not chat_id or chat_id == "bot-service":
            continue
        try:
            await deps.telegram_client.send_text(chat_id, text)
        except Exception as exc:
            logger.warning("failed to send hold escalation alert to %s: %s", chat_id, exc)



def format_uncertain_verdict_reporter_notice(catalog: MessageCatalog | None = None) -> str:
    """The short, generic counterpart to `format_uncertain_verdict_notice` —
    delivered to the original reporter (any role), carries no insight text by
    design (REQUIRED_FIELDS_AND_CLOSED_DECISIONS.md Part 2 / item #8)."""

    messages = _catalog(catalog)
    return messages.text(
        "notice.uncertain_reporter", header=format_header("uncertain_reporter", messages)
    )



def format_no_match_notice(notice: "NoMatchNotice", catalog: MessageCatalog | None = None) -> str:
    """Commander notice that no protocol matched the report."""

    messages = _catalog(catalog)
    why = notice.reason or messages.text("common.no_reason")
    return messages.text(
        "notice.no_match",
        header=format_header("no_match", messages),
        raw_text=notice.raw_text,
        reason=why,
        risk_level=_risk_level_word(notice.risk_level, messages),
        risk_reason=notice.risk_reason,
    )



async def notify_no_match(deps: "BotDeps", notice: "NoMatchNotice") -> None:
    """Send the no-match notice to commander private chats."""

    text = format_no_match_notice(notice, message_catalog_for(deps))

    for chat_id in await deps.api_client.list_commander_chat_ids():
        if not chat_id or chat_id == "bot-service":
            continue
        try:
            await deps.telegram_client.send_text(chat_id, text)
        except Exception as exc:
            logger.warning("failed to send no match notice to %s: %s", chat_id, exc)



def _describe_outcome(outcome, catalog: MessageCatalog | None = None) -> str:
    """Human-readable result of answering an approval hold."""

    messages = _catalog(catalog)
    if outcome.status == "approved":
        return messages.text("approval.resumed")

    if outcome.status == "rejected":
        return messages.text("approval.rejected")

    if outcome.status in ("unauthorized", "invalid_classification", "invalid_candidate"):
        return outcome.message

    who = messages.text("common.by_identity", identity=outcome.resolved_by) if outcome.resolved_by else ""
    return messages.text("approval.already_answered", who=who, message=outcome.message).strip()



async def handle_approval_answer(deps: "BotDeps", chat_id: str, answering_identity: str, event_id: str, choice: str) -> "HoldAnswerOutcome | None":
    """`choice` is already "approved"/"rejected" for a flagged-protocol hold (the button's callback data), or the chosen candidate's protocol name for an ambiguous-selection hold — see..."""

    unregister_open_approval_hold(event_id)
    messages = message_catalog_for(deps)
    resolution = await resolve_caller(deps.api_client, answering_identity, messages)
    if resolution.status == "unregistered":
        await deps.telegram_client.send_text(chat_id, resolution.refusal_message)
        return None

    refusal = check_permission(resolution.caller, RequestedOperation.APPROVE_RUN, messages)
    if refusal is not None:
        await deps.telegram_client.send_text(chat_id, refusal)
        return None

    outcome = await deps.api_client.answer_approval_hold(event_id, choice, answering_identity)
    await deps.telegram_client.send_text(chat_id, _describe_outcome(outcome, messages))
    return outcome


CLARIFICATION_CALLBACK_PREFIX = "clarify"



def build_clarification_callback_data(event_id: str, classification: str) -> str:
    """Callback payload for a clarification classification button."""

    return f"{CLARIFICATION_CALLBACK_PREFIX}:{event_id}:{classification}"



def parse_clarification_callback_data(data: str) -> tuple[str, str]:
    """Event id and classification encoded in a clarification callback."""

    _, event_id, classification = data.split(":", 2)
    return event_id, classification



def format_clarification_prompt(
    notice: "HeldClarificationNotice", catalog: MessageCatalog | None = None
) -> str:
    """Telegram body asking commanders to pick a classification."""

    messages = _catalog(catalog)
    return messages.text(
        "clarification.prompt",
        header=format_header("clarification_needed", messages),
        raw_text=notice.raw_text,
        field=notice.unresolved_field,
    )



async def push_clarification_prompt(deps: "BotDeps", notice: "HeldClarificationNotice") -> None:
    """Send the clarification prompt with buttons to commander chats."""

    text = format_clarification_prompt(notice, message_catalog_for(deps))
    buttons = [(choice, build_clarification_callback_data(notice.event_id, choice)) for choice in notice.available_classifications]

    for chat_id in await deps.api_client.list_commander_chat_ids():
        await deps.telegram_client.send_with_buttons(chat_id, text, buttons)



def _describe_clarification_outcome(outcome, catalog: MessageCatalog | None = None) -> str:
    """Human-readable result of answering a clarification hold."""

    messages = _catalog(catalog)
    if outcome.status == "resolved":
        return messages.text("clarification.resumed")

    if outcome.status == "unauthorized":
        return outcome.message

    if outcome.status == "invalid_classification":
        return outcome.message

    # "not_found": already resolved, by this same race or someone else —
    # never silently re-accepted as if it were the first answer (§8.4).
    who = messages.text("common.by_identity", identity=outcome.resolved_by) if outcome.resolved_by else ""
    return messages.text("clarification.already_resolved", who=who, message=outcome.message).strip()



async def handle_clarification_answer(
    deps: "BotDeps", chat_id: str, answering_identity: str, event_id: str, chosen_classification: str
) -> "HoldAnswerOutcome | None":
    """Authorize the answerer and resolve the clarification hold via the API."""

    messages = message_catalog_for(deps)
    resolution = await resolve_caller(deps.api_client, answering_identity, messages)
    if resolution.status == "unregistered":
        await deps.telegram_client.send_text(chat_id, resolution.refusal_message)
        return None

    refusal = check_permission(resolution.caller, RequestedOperation.RESOLVE_CLARIFICATION, messages)
    if refusal is not None:
        await deps.telegram_client.send_text(chat_id, refusal)
        return None

    outcome = await deps.api_client.answer_clarification_hold(event_id, chosen_classification, answering_identity)
    await deps.telegram_client.send_text(chat_id, _describe_clarification_outcome(outcome, messages))
    return outcome



def _format_waiting_time(created_at_iso: str | None, messages: MessageCatalog) -> str:
    """Relative waiting time for a hold, or unknown if the timestamp is missing."""

    if not created_at_iso:
        return messages.text("time.unknown")
    try:
        from datetime import datetime, timezone
        clean_iso = created_at_iso.replace("Z", "+00:00")
        dt = datetime.fromisoformat(clean_iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        diff = (now - dt).total_seconds()
        if diff < 0:
            diff = 0
        if diff < 60:
            return messages.text("time.seconds_ago", seconds=int(diff))
        if diff < 3600:
            return messages.text("time.minutes_ago", minutes=int(diff // 60))
        return messages.text("time.hours_ago", hours=int(diff // 3600))
    except Exception:
        return messages.text("time.unknown")



def _friendly_action_type(protocol_name: str, messages: MessageCatalog) -> str:
    """Translated action label for a protocol name, or the generic fallback."""

    key = f"action.{protocol_name}"
    try:
        return messages.text(key)
    except MessageCatalogError:
        return messages.text("action.generic")



async def present_pending_approvals_queue(
    deps: "BotDeps", chat_id: str, answering_identity: str
) -> None:
    """Show this commander the current pending holds as cards with answer buttons."""

    from bot.contracts import ApiRequestError

    messages = message_catalog_for(deps)
    resolution = await resolve_caller(deps.api_client, answering_identity, messages)
    if resolution.status == "unregistered":
        await deps.telegram_client.send_text(chat_id, resolution.refusal_message)
        return

    refusal = check_permission(resolution.caller, RequestedOperation.APPROVE_RUN, messages)
    if refusal is not None:
        await deps.telegram_client.send_text(chat_id, refusal)
        return

    try:
        data = await deps.api_client.fetch_pending_holds(answering_identity)
    except ApiRequestError as exc:
        if exc.status_code == 403:
            await deps.telegram_client.send_text(chat_id, messages.text("bot.commander_only"))
            return
        await deps.telegram_client.send_text(chat_id, messages.text("error.request_failed", reason=exc.message))
        return

    holds = data.get("holds", [])
    if not holds:
        await deps.telegram_client.send_text(chat_id, f"\u2705 {messages.text('bot.queue_empty')}")
        return

    header_text = f"\u23f3 {messages.text('bot.queue_header', count=len(holds))}"
    await deps.telegram_client.send_text(chat_id, header_text)

    for hold in holds:
        kind = hold.get("kind")
        event_id = hold.get("event_id", "")
        created_at = hold.get("created_at")
        waiting_time = _format_waiting_time(created_at, messages)
        requester = hold.get("sender_identity") or messages.text("common.unknown")

        if kind == "approval":
            protocol_name = hold.get("protocol_name") or ""
            action_type = _friendly_action_type(protocol_name, messages)
            description = hold.get("raw_text") or messages.text("action.generic")
            risk_level = _risk_level_word(hold.get("risk_level", "low"), messages)
            risk_reason_val = hold.get("risk_reason")
            risk_reason = f" ({risk_reason_val})" if risk_reason_val else ""

            card_text = messages.text(
                "bot.queue_card_approval",
                action_type=action_type,
                description=description,
                waiting_time=waiting_time,
                requester=requester,
                risk_level=risk_level,
                risk_reason=risk_reason,
            )
            buttons = [
                (f"\u2705 {messages.text('bot.btn_approve')}", build_callback_data(event_id, "approved")),
                (f"\u274c {messages.text('bot.btn_reject')}", build_callback_data(event_id, "rejected")),
            ]
            await deps.telegram_client.send_with_buttons(chat_id, card_text, buttons)

        elif kind == "clarification":
            action_type = messages.text("action.clarification")
            description = hold.get("raw_text") or messages.text("common.unknown")
            unresolved_info = messages.text("action.unresolved_classification")

            card_text = messages.text(
                "bot.queue_card_clarification",
                action_type=action_type,
                description=description,
                waiting_time=waiting_time,
                requester=requester,
                unresolved_info=unresolved_info,
            )
            avail = hold.get("available_classifications") or ()
            buttons = [
                (choice, build_clarification_callback_data(event_id, choice))
                for choice in avail
            ]
            if buttons:
                await deps.telegram_client.send_with_buttons(chat_id, card_text, buttons)
            else:
                await deps.telegram_client.send_text(chat_id, card_text)
