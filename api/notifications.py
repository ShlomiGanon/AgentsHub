"""`GET /Notifications` (work_plan.md §8.12).

Closes the gap the Mission 8 deep audit found: `bot.api_client
.BotApiClient.poll_pending_notifications` — what §8.4/§8.5/§8.6/§8.9/
§8.11's proactive pushes all funnel through — had no corresponding
endpoint anywhere in the API Layer. Numbered under §8, not §7, because
§8's own commands and prompts are the reason it exists; the code itself
lives here in `api/*`, the same way §7.11/§7.12 already do.

Read-only: every row this endpoint serves already exists, written by
`persistence.sqlite_backend`'s `store_held_event`/`update_event` in the
same transaction as the state change it records (§2.9's migration 8).
This module's only job is turning a `notification_log` row plus a
re-fetch of the event/hold it references into the exact JSON shape
`bot.api_client.BotNotification`'s dataclasses mirror by value.

COMMANDER-level (`poll_notifications`), not the per-notification-kind
viewer/commander split an earlier draft of this endpoint's own work_plan
text sketched: in this system's real design there is exactly one caller,
the bot's own service identity (`docs/allowed_calls.md`: "bot calls only
api"), which needs to see every kind — including commander-only hold
detail — to do its own fan-out (§8.4/§8.5's "push to every commander",
§8.9's "deliver to whoever submitted it") correctly. See
`docs/api_spec.md`'s "Service identity" section for who that caller is.

The cursor is the caller's own responsibility to remember and pass back
(`since`, default 0) — this endpoint keeps no per-caller read state, only
the one global, monotonic `sequence_id` ordering `notification_log`
already provides. Polling again with the same `since` returns nothing
new; the `next_cursor` a response carries is exactly what the next poll
should pass as `since`.
"""

from typing import TYPE_CHECKING

from flask import Blueprint, jsonify, request

from api.auth import authenticate, require
from api.errors import InvalidInputError
from api.jobs import _failed_step_agent_name, _steps_completed

if TYPE_CHECKING:
    from api.app import ApiContext


def _clarification_hold_payload(ctx: "ApiContext", event_id: str) -> dict:
    hold = ctx.deps.persistence.fetch_held_event("clarification", event_id)
    return {
        "hold_id": hold["hold_id"],
        "event_id": event_id,
        "raw_text": hold["raw_text"],
        "unresolved_field": hold["unresolved_field"],
        "available_classifications": list(ctx.deps.event_type_registry.types),
    }


def _approval_hold_payload(ctx: "ApiContext", event_id: str) -> dict:
    hold = ctx.deps.persistence.fetch_held_event("approval", event_id)
    return {
        "hold_id": hold["hold_id"],
        "event_id": event_id,
        "reason": hold["reason"],
        "risk_level": hold["risk_level"],
        "risk_reason": hold["risk_reason"],
        "selected_protocol_name": hold.get("selected_protocol_name"),
        "candidate_protocol_names": hold.get("candidate_protocol_names") or [],
    }


def _uncertain_verdict_payload(ctx: "ApiContext", event_id: str) -> dict:
    event = ctx.deps.persistence.fetch_event(event_id)
    return {"event_id": event_id, "insight_text": event.get("insight_text") or ""}


def _precedent_closure_payload(ctx: "ApiContext", event_id: str) -> dict:
    event = ctx.deps.persistence.fetch_event(event_id)
    matched_id = event["precedent_closed_by_event_id"]
    matched_event = ctx.deps.persistence.fetch_event(matched_id)
    return {
        "event_id": event_id,
        "raw_text": event["raw_text"],
        "matched_precedent_event_id": matched_id,
        "precedent_ending": matched_event["outcome"] if matched_event is not None else "unknown",
    }


def _no_match_payload(ctx: "ApiContext", event_id: str) -> dict:
    # A real terminal outcome ("no_match_protocol"), not a hold — the
    # "why" text lives in outcome_failure_reason, the same column "failed"
    # uses, written by orchestrator.flows.continue_from_risk_assessment's
    # own record_event_outcome(..., failure_reason=selection.reason) call.
    event = ctx.deps.persistence.fetch_event(event_id)
    return {
        "event_id": event_id,
        "raw_text": event["raw_text"],
        "reason": event.get("outcome_failure_reason") or "",
        "risk_level": event.get("risk_level") or "",
        "risk_reason": event.get("risk_reason") or "",
    }


def _job_payload(ctx: "ApiContext", event_id: str) -> dict:
    event = ctx.deps.persistence.fetch_event(event_id)
    return {
        "job_id": event_id,
        "outcome": event["outcome"],
        "insight_text": event.get("insight_text") or "",
        "steps_completed": _steps_completed(event),
        "failure_reason": event.get("outcome_failure_reason"),
        "failed_step_agent_name": _failed_step_agent_name(event),
    }


_PAYLOAD_BUILDERS = {
    "clarification_hold": _clarification_hold_payload,
    "approval_hold": _approval_hold_payload,
    "uncertain_verdict": _uncertain_verdict_payload,
    "precedent_closure": _precedent_closure_payload,
    "no_match_notice": _no_match_payload,
    "job_finished": _job_payload,
    "job_failed": _job_payload,
}


def _target_chat_ids(ctx: "ApiContext", kind: str, event_id: str) -> list[str]:
    """`job_finished`/`job_failed` are addressed to whoever submitted the
    original event (§8.9: "deliver to whoever submitted it") — its
    `sender_identity` doubles as the chat to reach them at, the same
    "a private chat's chat_id is its user's own identity" equivalence
    §8.13's commander roster already relies on. The other four kinds are
    addressed to every commander, which the bot resolves for itself via
    `GET /Commanders` rather than this endpoint repeating that list on
    every single notification — so they carry none here, matching how
    `bot/clarification.py`/`bot/approval.py`/`bot/precedent_notify.py`
    already ignore `BotNotification.target_chat_ids` for these kinds and
    call `list_commander_chat_ids` themselves.
    """

    if kind not in ("job_finished", "job_failed"):
        return []

    event = ctx.deps.persistence.fetch_event(event_id)
    return [event["sender_identity"]]


def _reply_to_message_id(ctx: "ApiContext", kind: str, event_id: str) -> str | None:
    """The originating Telegram message's own ID (work_plan.md §2.3's
    `source_message_id` column), so `job_finished`/`job_failed` — the two
    kinds ever delivered via `TelegramClient.send_reply` (§8.9/§8.11) — can
    actually reference the message that started this event. `None` for a
    sensor-sourced event (no Telegram message to reference) and for every
    other notification kind (none of them are replies to anything).
    """

    if kind not in ("job_finished", "job_failed"):
        return None

    event = ctx.deps.persistence.fetch_event(event_id)
    return event.get("source_message_id")


def _format_notification(ctx: "ApiContext", row: dict) -> dict:
    builder = _PAYLOAD_BUILDERS[row["kind"]]
    return {
        "sequence_id": row["sequence_id"],
        "kind": row["kind"],
        "payload": builder(ctx, row["event_id"]),
        "target_chat_ids": _target_chat_ids(ctx, row["kind"], row["event_id"]),
        "reply_to_message_id": _reply_to_message_id(ctx, row["kind"], row["event_id"]),
    }


def build_notifications_blueprint(ctx: "ApiContext") -> Blueprint:
    blueprint = Blueprint("notifications", __name__)

    @blueprint.route("/Notifications", methods=["GET"])
    def get_notifications():
        level = authenticate(ctx.deps.persistence, request.headers.get("X-Identity"))
        require(level, "poll_notifications")

        raw_since = request.args.get("since", "0")
        try:
            since = int(raw_since)
            if since < 0:
                raise ValueError
        except ValueError:
            raise InvalidInputError("'since' must be a non-negative integer cursor", field="since")

        rows = ctx.deps.persistence.fetch_notifications_since(since)
        notifications = [_format_notification(ctx, row) for row in rows]
        next_cursor = rows[-1]["sequence_id"] if rows else since

        return jsonify({"notifications": notifications, "next_cursor": next_cursor})

    return blueprint
