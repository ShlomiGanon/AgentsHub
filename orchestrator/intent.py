"""Message intent classification (work_plan.md §6.13).

Classification only — routing (question → the question flow; report →
the new-event flow via extraction; request → the new-event flow entering
at risk assessment, classified `human_activation`; the approval-flag
bypass for a commander's own request) is `orchestrator.flows`'s job,
built next. This module answers exactly one question: which of the three
this message is, and why.

**The `INTENT:`/`REASON:` response format is an unverified prompt
convention**, same status as every other Main Agent decision this
mission.
"""

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from orchestrator.errors import OrchestrationParseError
from protocols.model import Protocol
from tools.tracing import stage_context

if TYPE_CHECKING:
    from orchestrator.main_agent import MainAgent


@dataclass(frozen=True)
class IntentResult:
    intent: Literal["question", "report", "request"]
    reason: str


_INTENT_PATTERN = re.compile(r"INTENT:\s*(question|report|request)", re.IGNORECASE)
_REASON_PATTERN = re.compile(r"REASON:\s*(.+)", re.IGNORECASE | re.DOTALL)


def _build_intent_prompt(message_text: str, protocols: tuple[Protocol, ...]) -> str:
    protocol_lines = "\n".join(f"- {p.name}: {p.description}" for p in protocols)

    return (
        "Decide what kind of message this is: a QUESTION (asking about something), a REPORT "
        "(describing something that happened), or a REQUEST (asking for an action to be taken). "
        "Whether something is a request depends on whether it asks for something one of the "
        "following protocols does — in practice, \"is this a request for action\" is \"does this "
        "ask for something a protocol does\":\n\n"
        f"{protocol_lines}\n\n"
        f"Message: {message_text}\n\n"
        "Respond in exactly this format, two lines:\n"
        "INTENT: <question | report | request>\n"
        "REASON: <why>"
    )


def _parse_intent_response(raw_text: str) -> IntentResult:
    intent_match = _INTENT_PATTERN.search(raw_text)
    reason_match = _REASON_PATTERN.search(raw_text)

    if intent_match is None or reason_match is None:
        raise OrchestrationParseError(f"could not parse message intent response: {raw_text!r}")

    return IntentResult(intent=intent_match.group(1).lower(), reason=reason_match.group(1).strip())


def classify_intent(main_agent: "MainAgent", protocols: tuple[Protocol, ...], message_text: str) -> IntentResult:
    prompt = _build_intent_prompt(message_text, protocols)
    with stage_context("intent_classification"):
        result = main_agent.process(prompt, [])

    if result.status != "success":
        raise OrchestrationParseError(f"message intent classification did not produce a usable response: {result.text}")

    return _parse_intent_response(result.text)
