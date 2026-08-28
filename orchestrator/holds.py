"""Holds (work_plan.md §6.2, §6.7)."""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from auth.permissions import PermissionLevel, is_permitted
from persistence import NotFoundError, PersistenceInterface
from protocols import Protocol

if TYPE_CHECKING:
    from history import ExtractionResult
    from orchestrator.reasoning import ProtocolSelectionResult, RiskAssessment
    from profiles import EventTypeRegistry

HoldReason = Literal["flagged_protocol", "ambiguous_selection"]

UNRESOLVED_FIELD = "classification"


@dataclass(frozen=True)
class HoldAnswerResult:
    status: Literal[
        "approved", "rejected", "resolved", "unauthorized", "not_found",
        "invalid_classification", "invalid_candidate",
    ]
    hold: dict | None = None
    message: str = ""


def determine_approval_hold(
    selection: "ProtocolSelectionResult",
    protocols_by_name: dict[str, Protocol],
    originated_from_commander: bool,
) -> HoldReason | None:
    """Return the reason a run must be held, or None to proceed."""

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
    """Write everything needed to resume: the event, the selected protocol or the candidates, the assessed risk, and why it was held."""

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
    decision: Literal["approved", "rejected"] | str,
) -> HoldAnswerResult:
    """Accept an answer only from a commander, validated *now* — at the moment they answer, not whatever level they held when the hold was created."""

    if not is_permitted(answering_level, "approve_run"):
        return HoldAnswerResult(status="unauthorized", message=f"level {answering_level.name} may not approve a run")

    held = next(
        (held_event for held_event in persistence.list_held_events("approval") if held_event["hold_id"] == hold_id),
        None,
    )
    if held is None:
        return HoldAnswerResult(
            status="not_found",
            message=f"no unresolved approval hold '{hold_id}' — it may not exist or may already be resolved",
        )

    if held["reason"] == "ambiguous_selection":
        return _answer_ambiguous_selection_hold(persistence, hold_id, answering_identity, held, decision)

    try:
        persistence.resolve_held_event("approval", hold_id, {"resolved_by": answering_identity, "decision": decision})
    except NotFoundError as exc:
        return HoldAnswerResult(status="not_found", message=str(exc))

    status: Literal["approved", "rejected"] = "approved" if decision == "approved" else "rejected"
    return HoldAnswerResult(status=status, hold=held)


def _answer_ambiguous_selection_hold(
    persistence: PersistenceInterface,
    hold_id: str,
    answering_identity: str,
    held: dict,
    decision: str,
) -> HoldAnswerResult:
    """`"approved"`/`"rejected"` answer nothing here — an ambiguous hold asks which protocol to run, never a yes/no question (§6.7's own second bullet)."""

    candidates = held["candidate_protocol_names"]
    if decision not in candidates:
        return HoldAnswerResult(
            status="invalid_candidate",
            message=f"'{decision}' is not one of this hold's candidates: {candidates}",
        )

    try:
        persistence.resolve_held_event("approval", hold_id, {"resolved_by": answering_identity, "decision": decision})
    except NotFoundError as exc:
        return HoldAnswerResult(status="not_found", message=str(exc))

    resolved_hold = {**held, "selected_protocol_name": decision}
    return HoldAnswerResult(status="approved", hold=resolved_hold)


def determine_clarification_hold(extraction_result: "ExtractionResult") -> bool:
    """True when the event must be held — extraction couldn't resolve a classification, whether because the text didn't fit any registered type or because the source stated a type outs..."""

    return extraction_result.classification is None


def create_clarification_hold(persistence: PersistenceInterface, event_id: str, raw_text: str) -> str:
    """Write everything needed to resume: the event, the raw text, and which field couldn't be resolved — in the terms the prompt will show a commander."""

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
    """Accept a resolution only from a commander, and only a classification drawn from the loaded registry — free text is rejected outright, since the registry is fixed for the run and..."""

    if not is_permitted(answering_level, "resolve_hold"):
        return HoldAnswerResult(status="unauthorized", message=f"level {answering_level.name} may not resolve a hold")

    if not event_type_registry.is_valid(chosen_classification):
        return HoldAnswerResult(
            status="invalid_classification",
            message=f"'{chosen_classification}' is not in the loaded event-type registry",
        )

    held = next(
        (held_event for held_event in persistence.list_held_events("clarification") if held_event["hold_id"] == hold_id),
        None,
    )
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
