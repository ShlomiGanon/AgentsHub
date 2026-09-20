"""Canonical correction / supersession resolution for committed reports.

A correction is a report that says a previously reported operational fact did
not occur, or occurred differently. It is never an action: it creates no tool
receipt, requests nothing and approves nothing.

The rule this module exists to enforce is that a correction must not silently
retract the wrong report. It resolves a target only when exactly one committed
report in the same operational scope matches the subject the correction names.
Zero matches, or several, retract nothing — the correction is still committed as
its own operational fact, and the ambiguity is reported rather than guessed
away.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from persistence import OperationalScope, operational_time_of_event


RETRACTION = "retraction"
CORRECTION = "correction"

# The canonical event type a deterministic correction intake produces. A
# profile opts in by declaring it among its event types.
CORRECTION_REPORT_TYPE = "correction_report"


# How an operator states that an earlier report was wrong, in either language.
# No scenario, place name or fixture sentence appears here.
#
# The three classes are kept apart on purpose, because a bare negation is not a
# retraction. "There are no casualties" is an ordinary negative report about the
# present, not a claim that an earlier casualty report was false. A retraction
# therefore needs either an unambiguous falsity statement, or a negation that
# explicitly refers back to something already reported.

# Class A — an explicit reference to an earlier report.
_PRIOR_REPORT_REFERENCE = re.compile(
    r"הדיווח|דיווח קודם|שדווח|שדווחה|שדווחו|"
    r"previous report|earlier report|the report (?:about|regarding|of)|prior report|reported earlier",
    re.IGNORECASE,
)

# Class B — a statement that something is not so, or is cancelled.
_NEGATION_OR_CANCELLATION = re.compile(
    r"אין\b|בוטל|מבוטל|בטל|ביטול|"
    r"\bthere is no\b|\bthere are no\b|\bno\s+\S+\s+at\b|\bcancel|\bretract",
    re.IGNORECASE,
)

# Class B-strong — a falsity statement that is itself unambiguous evidence that
# an earlier report is being withdrawn.
_FALSITY_STATEMENT = re.compile(
    r"דיווח שווא|דיווחי שווא|אזעקת שווא|"
    r"סרק\b|התברר כשגוי|אינו נכון|לא נכון|"
    r"false report|false alarm|unfounded|disregard|stand down",
    re.IGNORECASE,
)

# Class C — an explicit correction marker.
_CLARIFICATION_MARKER = re.compile(
    r"הבהרה|תיקון|מתקן|clarification|correction|to clarify",
    re.IGNORECASE,
)

_TOKEN = re.compile(r"[\w֐-׿]{3,}", re.IGNORECASE)

# Words that carry no operational subject, so matching on them would make any
# two messages look related.
_STOPWORDS = frozenset(
    {
        "את", "על", "של", "זה", "הוא",
        "היא", "אני", "אנחנו",
        "יש", "אין", "לא", "כן",
        "הבהרה", "דיווח",
        "the", "and", "for", "with", "that", "this", "there", "report", "reported",
        "clarification", "correction", "from", "our", "are", "was", "were", "not",
    }
)

# A resolved target must share at least this many distinctive subject tokens.
MINIMUM_SUBJECT_OVERLAP = 2


@dataclass(frozen=True)
class SupersessionResolution:
    """What a correction retracted, or why it retracted nothing."""

    kind: str
    target_event_id: str | None
    status: str
    candidate_event_ids: tuple[str, ...] = ()

    @property
    def resolved(self) -> bool:
        return self.target_event_id is not None


def classify_correction(text: str) -> str | None:
    """`retraction`, `correction`, or None when the message retracts nothing."""

    body = str(text or "")
    if not body.strip():
        return None

    states_falsity = bool(_FALSITY_STATEMENT.search(body))
    refers_back = bool(_PRIOR_REPORT_REFERENCE.search(body))
    negates = bool(_NEGATION_OR_CANCELLATION.search(body))
    clarifies = bool(_CLARIFICATION_MARKER.search(body))

    # A retraction needs an unambiguous falsity statement, or a negation that
    # is explicitly tied to something already reported or to a correction.
    if states_falsity or (negates and (refers_back or clarifies)):
        return RETRACTION
    if clarifies:
        return CORRECTION
    return None


def subject_tokens(text: str) -> frozenset[str]:
    """The distinctive words a message is about."""

    return frozenset(
        token.casefold()
        for token in _TOKEN.findall(str(text or ""))
        if token.casefold() not in _STOPWORDS
    )


def resolve_superseded_event(
    correction: dict,
    candidates,
    *,
    scope: OperationalScope,
) -> SupersessionResolution:
    """Find the one committed report this correction retracts, or none.

    `candidates` are committed reports already read from the correction's own
    operational scope. Anything already superseded, later than the correction on
    the operational clock, or the correction itself is not a candidate.
    """

    kind = classify_correction(correction.get("raw_text") or correction.get("description") or "")
    if kind is None:
        return SupersessionResolution(kind=CORRECTION, target_event_id=None, status="not_a_correction")

    correction_id = correction.get("event_id")
    correction_time = operational_time_of_event(correction, scope=scope)
    wanted = subject_tokens(correction.get("raw_text") or correction.get("description") or "")

    matches: list[tuple[int, datetime, str]] = []
    for candidate in candidates:
        candidate_id = candidate.get("event_id")
        if not candidate_id or candidate_id == correction_id:
            continue
        if candidate.get("superseded_by_event_id"):
            continue
        if candidate.get("outcome") != "succeeded":
            continue
        if not str(candidate.get("classification") or "").casefold().endswith("_report"):
            continue

        candidate_time = operational_time_of_event(candidate, scope=scope)
        if candidate_time > correction_time:
            continue

        overlap = wanted & subject_tokens(
            f"{candidate.get('raw_text') or ''} {candidate.get('description') or ''}"
        )
        if len(overlap) >= MINIMUM_SUBJECT_OVERLAP:
            matches.append((len(overlap), candidate_time, str(candidate_id)))

    if not matches:
        return SupersessionResolution(kind=kind, target_event_id=None, status="no_match")

    best_overlap = max(overlap for overlap, _, _ in matches)
    best = [entry for entry in matches if entry[0] == best_overlap]
    if len(best) > 1:
        return SupersessionResolution(
            kind=kind,
            target_event_id=None,
            status="ambiguous",
            candidate_event_ids=tuple(sorted(event_id for _, _, event_id in best)),
        )

    return SupersessionResolution(kind=kind, target_event_id=best[0][2], status="resolved")
