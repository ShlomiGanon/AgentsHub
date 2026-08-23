"""Holds (work_plan.md §6.2, §6.7).

Both hold mechanisms are owned by this module per the work plan's branch
grouping, both backed by the same generic `held_events` storage
(`persistence.sqlite_backend`'s `store_held_event`/`list_held_events`/
`resolve_held_event`), distinguished only by `kind`.

What this module does *not* do: actually resume execution — from
extraction-was-unresolved to risk assessment on a clarification answer,
or from task formulation on approval. Those are the new-event flow's job
(§6.11). This module produces the *decision* (hold or not, and what the
answer was) and persists it; wiring that back into a resumed run is
§6.11's.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from auth.permissions import PermissionLevel, is_permitted
from persistence.exceptions import NotFoundError
from persistence.interface import PersistenceInterface
from protocols.model import Protocol

if TYPE_CHECKING:
    from history.extraction import ExtractionResult
    from orchestrator.main_agent import RiskAssessment
    from orchestrator.selection import ProtocolSelectionResult
    from registries.event_types import EventTypeRegistry

HoldReason = Literal["flagged_protocol", "ambiguous_selection"]

# Only "classification" ever triggers a clarification hold — an empty area
# or description doesn't (§2.1/§2.2: those narrow precedent search, they
# never hold the event). The field name is fixed, not derived per-hold.
UNRESOLVED_FIELD = "classification"


@dataclass(frozen=True)
class HoldAnswerResult:
    status: Literal["approved", "rejected", "resolved", "unauthorized", "not_found", "invalid_classification"]
    hold: dict | None = None
    message: str = ""


def determine_approval_hold(
    selection: "ProtocolSelectionResult",
    protocols_by_name: dict[str, Protocol],
    originated_from_commander: bool,
) -> HoldReason | None:
    """Return the reason a run must be held, or None to proceed.

    An ambiguous selection always holds, whoever asked — that hold asks
    which protocol to run, which a commander's authority doesn't answer.
    A flagged protocol holds only when the request didn't come from a
    commander in the first place; their authority bypasses the flag, not
    the ambiguity.
    """

    if selection.status == "ambiguous":
        return "ambiguous_selection"

    protocol = protocols_by_name.get(selection.protocol_name)
    if protocol is not None and protocol.approval_flag and not originated_from_commander:
        return "flagged_protocol"

    return None


def create_approval_hold(
    persistence: PersistenceInterface,
    event_id: str,
    hold_reason: HoldReason,
    selection: "ProtocolSelectionResult",
    risk_assessment: "RiskAssessment",
) -> str:
    """Write everything needed to resume: the event, the selected
    protocol or the candidates, the assessed risk, and why it was held.
    """

    hold = {
        "event_id": event_id,
        "reason": hold_reason,
        "selected_protocol_name": selection.protocol_name,
        "candidate_protocol_names": list(selection.candidate_names),
        "selection_reason": selection.reason,
        "risk_level": risk_assessment.level,
        "risk_score": risk_assessment.score,
        "risk_reason": risk_assessment.reason,
    }

    return persistence.store_held_event("approval", hold)


def answer_approval_hold(
    persistence: PersistenceInterface,
    hold_id: str,
    answering_identity: str,
    answering_level: PermissionLevel,
    decision: Literal["approved", "rejected"],
) -> HoldAnswerResult:
    """Accept an answer only from a commander, validated *now* — at the
    moment they answer, not whatever level they held when the hold was
    created. A second answer to an already-resolved hold is reported
    distinctly, never silently accepted as if it were the first.
    """

    if not is_permitted(answering_level, "approve_run"):
        return HoldAnswerResult(status="unauthorized", message=f"level {answering_level.name} may not approve a run")

    held = next((h for h in persistence.list_held_events("approval") if h["hold_id"] == hold_id), None)
    if held is None:
        return HoldAnswerResult(
            status="not_found",
            message=f"no unresolved approval hold '{hold_id}' — it may not exist or may already be resolved",
        )

    try:
        persistence.resolve_held_event("approval", hold_id, {"resolved_by": answering_identity, "decision": decision})
    except NotFoundError as exc:
        # Resolved by someone else between the list() above and this call.
        return HoldAnswerResult(status="not_found", message=str(exc))

    status: Literal["approved", "rejected"] = "approved" if decision == "approved" else "rejected"
    return HoldAnswerResult(status=status, hold=held)


# -- Clarification holds (§6.2) ------------------------------------------


def determine_clarification_hold(extraction_result: "ExtractionResult") -> bool:
    """True when the event must be held — extraction couldn't resolve a
    classification, whether because the text didn't fit any registered
    type or because the source stated a type outside the registry
    (`history.extraction.extract_event` already nullifies the latter case,
    so a `None` classification is the one unified signal for both).
    """

    return extraction_result.classification is None


def create_clarification_hold(persistence: PersistenceInterface, event_id: str, raw_text: str) -> str:
    """Write everything needed to resume: the event, the raw text, and
    which field couldn't be resolved — in the terms the prompt will show
    a commander.
    """

    hold = {
        "event_id": event_id,
        "unresolved_field": UNRESOLVED_FIELD,
        "raw_text": raw_text,
    }

    return persistence.store_held_event("clarification", hold)


def answer_clarification_hold(
    persistence: PersistenceInterface,
    hold_id: str,
    answering_identity: str,
    answering_level: PermissionLevel,
    chosen_classification: str,
    event_type_registry: "EventTypeRegistry",
) -> HoldAnswerResult:
    """Accept a resolution only from a commander, and only a
    classification drawn from the loaded registry — free text is rejected
    outright, since the registry is fixed for the run and accepting
    anything outside it defeats the constraint everything downstream
    relies on.
    """

    if not is_permitted(answering_level, "resolve_hold"):
        return HoldAnswerResult(status="unauthorized", message=f"level {answering_level.name} may not resolve a hold")

    if not event_type_registry.is_valid(chosen_classification):
        return HoldAnswerResult(
            status="invalid_classification",
            message=f"'{chosen_classification}' is not in the loaded event-type registry",
        )

    held = next((h for h in persistence.list_held_events("clarification") if h["hold_id"] == hold_id), None)
    if held is None:
        return HoldAnswerResult(
            status="not_found",
            message=f"no unresolved clarification hold '{hold_id}' — it may not exist or may already be resolved",
        )

    try:
        persistence.resolve_held_event(
            "clarification",
            hold_id,
            {"resolved_by": answering_identity, "chosen_classification": chosen_classification},
        )
    except NotFoundError as exc:
        return HoldAnswerResult(status="not_found", message=str(exc))

    return HoldAnswerResult(status="resolved", hold=held)
