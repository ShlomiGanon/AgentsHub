"""Precedent lookup and closure (work_plan.md §6.5, §6.6).

Read-only: `look_up_precedent` changes nothing and may remove the need for
both a hold and a run. Closure is a plain, deterministic check over what
`history.query.HistoryQueryService.search_precedents` already returns —
`PrecedentMatch.resolved` is a deterministic outcome mapping computed by
`history/precedent.py` itself (never a model judgment), so there's nothing
for this module to interpret beyond the three explicit rules below.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from history.query import HistoryQueryService, PrecedentMatch


def look_up_precedent(
    history_query_service: "HistoryQueryService",
    event_id: str,
    classification: str,
    area: str,
    occurred_at: str,
) -> tuple["PrecedentMatch", ...]:
    return tuple(history_query_service.search_precedents(event_id, classification, area, occurred_at))


def determine_closure(risk_level: str, classification: str, precedents: tuple["PrecedentMatch", ...]) -> str | None:
    """Return the event ID of the resolved precedent that justifies
    closing without a run, or None to proceed normally.

    Closes only below the risk threshold (`risk_level == "low"`, already
    the derived label from §6.3's live comparison — never re-derived
    here), only against a match that was itself resolved (a precedent
    that was seen and never resolved is not evidence that anything
    works), and never for a human-activation event (a person asked for
    something; answering with silence because a similar request was
    handled before is the wrong response even when the precedent is
    sound). Among several resolved matches, the most recent one is used —
    `search_precedents` already returns matches most-recent-first.
    """

    if risk_level != "low":
        return None

    if classification == "human_activation":
        return None

    for precedent in precedents:
        if precedent.resolved:
            return precedent.event_id

    return None
