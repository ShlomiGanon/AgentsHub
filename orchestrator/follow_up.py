"""Deterministic resolution of short, context-dependent follow-up messages."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from persistence import ConversationEventLink, PersistenceInterface


FollowUpKind = Literal[
    "none",
    "ambiguous",
    "failed",
    "executed",
    "pending_approval",
    "approved",
    "executing",
    "requested",
    "unverified",
]


@dataclass(frozen=True)
class FollowUpResolution:
    kind: FollowUpKind
    event: ConversationEventLink | None = None
    candidate_event_ids: tuple[str, ...] = ()
    hold: dict | None = None


_FOLLOW_UP_MARKERS = frozenset(
    {
        "למה", "מה קרה", "זה בוצע", "למה זה נכשל", "ומה עכשיו",
        "why", "what happened", "was it done", "did it execute", "why did it fail", "what now",
    }
)
_YES_MARKERS = frozenset({"כן", "אישור", "מאשר", "approve", "approved", "yes"})


def _normalized(text: str) -> str:
    return " ".join(str(text).strip().casefold().split()).rstrip("?!.")


def is_context_dependent_follow_up(text: str) -> bool:
    """Use linguistic markers only as a candidate signal; state picks the referent."""

    normalized = _normalized(text)
    if normalized in _FOLLOW_UP_MARKERS or normalized in _YES_MARKERS:
        return True
    return len(normalized) <= 40 and any(
        marker in normalized for marker in ("למה ", "why ", "זה ", "what ", "מה ")
    )


def _is_recent(candidate: ConversationEventLink, newest: ConversationEventLink) -> bool:
    try:
        first = datetime.fromisoformat(candidate.timestamp.replace("Z", "+00:00"))
        second = datetime.fromisoformat(newest.timestamp.replace("Z", "+00:00"))
        if first.tzinfo is None:
            first = first.replace(tzinfo=timezone.utc)
        if second.tzinfo is None:
            second = second.replace(tzinfo=timezone.utc)
        return (second - first).total_seconds() <= 24 * 60 * 60
    except (TypeError, ValueError):
        return True


def _verified_receipt(event: ConversationEventLink) -> bool:
    return any(
        isinstance(receipt, dict)
        and receipt.get("status") == "succeeded"
        and receipt.get("success") is True
        for receipt in event.tool_receipts
    )


def resolve_follow_up(
    persistence: PersistenceInterface,
    conversation_id: str | None,
    sender_identity: str,
    text: str,
) -> FollowUpResolution:
    """Resolve a follow-up from persisted conversation/event state, never from model prose."""

    if not conversation_id or not is_context_dependent_follow_up(text):
        return FollowUpResolution("none")

    normalized = _normalized(text)
    try:
        if normalized in _YES_MARKERS:
            for hold in persistence.list_held_events("approval"):
                event = persistence.fetch_event(hold["event_id"])
                if (
                    event is not None
                    and not hold.get("resolved")
                    and event.get("conversation_id") == conversation_id
                ):
                    return FollowUpResolution("pending_approval", hold=hold)

        links = persistence.list_conversation_event_links(conversation_id, sender_identity)
    except (AttributeError, NotImplementedError):
        return FollowUpResolution("none")
    links = [
        link
        for link in links
        if link.protocol_name
        # A successful read-only/query event is not an actionable referent for
        # "was it executed?".  Keep legacy failed events (which may predate
        # lifecycle persistence) because their persisted failure reason is
        # still useful, but require lifecycle/receipt evidence for success.
        and (
            link.action_state is not None
            or bool(link.tool_receipts)
            or link.outcome == "failed"
        )
    ]

    if links:
        newest = links[0]
        links = [link for link in links if _is_recent(link, newest)]

    if len(links) > 1:
        return FollowUpResolution(
            "ambiguous",
            candidate_event_ids=tuple(link.event_id for link in links),
        )

    if links:
        event = links[0]
        if event.outcome == "failed" or event.action_state == "failed":
            return FollowUpResolution("failed", event=event)
        if event.action_state == "pending_approval":
            return FollowUpResolution("pending_approval", event=event)
        if event.action_state == "approved":
            return FollowUpResolution("approved", event=event)
        if _verified_receipt(event):
            return FollowUpResolution("executed", event=event)
        if event.action_state == "executed" or event.outcome == "succeeded":
            return FollowUpResolution("unverified", event=event)
        if event.action_state == "executing":
            return FollowUpResolution("executing", event=event)
        if event.action_state == "requested":
            return FollowUpResolution("requested", event=event)

    return FollowUpResolution("none")


__all__ = ["FollowUpResolution", "is_context_dependent_follow_up", "resolve_follow_up"]
