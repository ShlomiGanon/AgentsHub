"""Protocol selection (work_plan.md §6.4).

Selection is by description alone — there is no mapping from event type
to protocol. The Main Agent reads every loaded protocol's full
description plus the event's fields and raw text, and either names one
clear match or reports the candidates it couldn't discriminate between.

High risk changes what happens with an ambiguous result, not what the
model is asked: this module resolves it automatically to the most
critical candidate and proceeds (waiting is the greater risk). At low
risk, the ambiguous result passes through unresolved for
`orchestrator.holds` to act on. Applying the same rule to a commander's
own request (§6.4's last bullet) is the caller's job in the message-intent
flow (§6.13, deferred) — this function only ever sees "high risk or not,"
never who originated the request.
"""

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from orchestrator.errors import OrchestrationParseError
from tools.tracing import stage_context

if TYPE_CHECKING:
    from orchestrator.main_agent import MainAgent
    from protocols.model import Protocol


@dataclass(frozen=True)
class ProtocolSelectionResult:
    status: Literal["selected", "ambiguous"]
    protocol_name: str | None = None
    candidate_names: tuple[str, ...] = ()
    reason: str = ""


_SELECTED_PATTERN = re.compile(r"SELECTED:\s*(\S+)", re.IGNORECASE)
_AMBIGUOUS_PATTERN = re.compile(r"AMBIGUOUS:\s*(.+)", re.IGNORECASE)
_REASON_PATTERN = re.compile(r"REASON:\s*(.+)", re.IGNORECASE | re.DOTALL)


def _build_selection_prompt(raw_text: str, classification: str | None, area: str | None, description: str | None, protocols: tuple["Protocol", ...]) -> str:
    protocol_lines = "\n".join(f"- {p.name}: {p.description}" for p in protocols)
    return (
        "Choose the protocol whose description best fits the following event. Selection is by "
        "description alone — do not infer a match from the classification name.\n\n"
        f"Raw report text: {raw_text}\n"
        f"Classification: {classification or '(unresolved)'}\n"
        f"Area: {area or '(unresolved)'}\n"
        f"Description: {description or '(none provided)'}\n\n"
        "Available protocols:\n"
        f"{protocol_lines}\n\n"
        "If exactly one protocol clearly fits, respond in exactly this format, two lines:\n"
        "SELECTED: <protocol name>\n"
        "REASON: <why this one fits>\n\n"
        "If more than one protocol fits equally well and you cannot discriminate between them, "
        "respond in exactly this format instead:\n"
        "AMBIGUOUS: <comma-separated protocol names>\n"
        "REASON: <why you could not discriminate>"
    )


def _parse_selection_response(raw_text: str) -> ProtocolSelectionResult:
    reason_match = _REASON_PATTERN.search(raw_text)
    reason = reason_match.group(1).strip() if reason_match else ""

    selected_match = _SELECTED_PATTERN.search(raw_text)
    if selected_match:
        return ProtocolSelectionResult(status="selected", protocol_name=selected_match.group(1), reason=reason)

    ambiguous_match = _AMBIGUOUS_PATTERN.search(raw_text)
    if ambiguous_match:
        names = tuple(name.strip() for name in ambiguous_match.group(1).split(",") if name.strip())
        return ProtocolSelectionResult(status="ambiguous", candidate_names=names, reason=reason)

    raise OrchestrationParseError(f"could not parse protocol selection response: {raw_text!r}")


def select_protocol(
    main_agent: "MainAgent",
    raw_text: str,
    classification: str | None,
    area: str | None,
    description: str | None,
    protocols: tuple["Protocol", ...],
    risk_level: Literal["high", "low"],
) -> ProtocolSelectionResult:
    prompt = _build_selection_prompt(raw_text, classification, area, description, protocols)
    with stage_context("protocol_selection"):
        result = main_agent.process(prompt, [])

    if result.status != "success":
        raise OrchestrationParseError(f"protocol selection did not produce a usable response: {result.text}")

    selection = _parse_selection_response(result.text)

    if selection.status == "ambiguous" and risk_level == "high":
        protocols_by_name = {p.name: p for p in protocols}
        candidates = [protocols_by_name[name] for name in selection.candidate_names if name in protocols_by_name]
        if candidates:
            most_critical = max(candidates, key=lambda p: p.criticality)
            return ProtocolSelectionResult(
                status="selected",
                protocol_name=most_critical.name,
                reason=f"high risk, ambiguous among {', '.join(selection.candidate_names)}; "
                f"proceeding with the most critical candidate rather than waiting ({selection.reason})",
            )

    return selection
