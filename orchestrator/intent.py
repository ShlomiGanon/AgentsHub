"""Message intent classification (work_plan.md §6.13).

Classification only — routing (question → the question flow; report →
the new-event flow via extraction; request → the new-event flow entering
at risk assessment, classified `human_activation`; the approval-flag
bypass for a commander's own request; conversational → straight back to
the Main Agent, below) is `orchestrator.flows`'s job. This module answers
exactly one question: which of the four this message is, and why.

`conversational` (found live, 2026-08): a purely social message — a
greeting, thanks, small talk — asks the system for nothing at all, but
the original three-way split had no bucket for that, so it always fell to
`question` (the only one of the three it doesn't actively contradict).
Landing in the question flow forced `orchestrator.question_flow
.answer_question`'s agent-selection step to pick some agent no matter
what (before this session's NONE fix) or, after that fix, to correctly
decline with "I don't have a way to answer that" — neither is right for
"hey, how are you?", which isn't asking the system to look up, check, or
do anything and deserves a direct, natural reply. Fixing this here rather
than inside `question_flow.py` keeps that module's NONE path exactly what
it was built for — a genuine question with no agent whose role fits —
and keeps the "is this even asking for something real" decision where the
other three already live: this module's single classification step.
`answer_conversationally`, below, is the direct reply for this branch,
kept in this module rather than `question_flow.py` since it never touches
agent selection, retrieval, or any of that module's machinery at all.

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
    intent: Literal["question", "report", "request", "conversational"]
    reason: str


_INTENT_PATTERN = re.compile(r"INTENT:\s*(question|report|request|conversational)", re.IGNORECASE)
_REASON_PATTERN = re.compile(r"REASON:\s*(.+)", re.IGNORECASE | re.DOTALL)


def _build_intent_prompt(message_text: str, protocols: tuple[Protocol, ...]) -> str:
    protocol_lines = "\n".join(f"- {p.name}: {p.description}" for p in protocols)

    return (
        "Decide what kind of message this is: a QUESTION (asking the system to retrieve, check, "
        "or determine something real), a REPORT (describing something that happened), a REQUEST "
        "(asking for an action to be taken), or CONVERSATIONAL (purely social, with nothing to "
        "look up, check, or act on — a greeting, thanks, or small talk). Whether something is a "
        "request depends on whether it asks for something one of the following protocols does — "
        "in practice, \"is this a request for action\" is \"does this ask for something a "
        "protocol does\":\n\n"
        f"{protocol_lines}\n\n"
        f"Message: {message_text}\n\n"
        "A message asking the system to retrieve, check, or determine something is a QUESTION "
        "even if it turns out nothing here can actually answer it — e.g. \"do I have any "
        "tasks?\" is a QUESTION (it asks the system to check something real), not "
        "CONVERSATIONAL, whatever the eventual answer is. Only classify as CONVERSATIONAL when "
        "nothing is actually being asked for at all — e.g. \"hey, how are you?\", \"thanks\", "
        "\"good morning\".\n\n"
        "Respond in exactly this format, two lines:\n"
        "INTENT: <question | report | request | conversational>\n"
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


def _build_conversational_prompt(message_text: str) -> str:
    return (
        "Reply naturally and directly to this message — a greeting, thanks, or other small talk "
        "with nothing to look up, check, or act on. Answer the way a person would: brief, warm, "
        "and direct.\n\n"
        f"Message: {message_text}\n\n"
        "Do not invent facts, data, or capabilities you don't actually have. If this message "
        "turns out to ask for something real you can't honestly answer, say so plainly instead "
        "of guessing — never fabricate an answer to sound helpful. Respond with only your reply, "
        "nothing else."
    )


def answer_conversationally(main_agent: "MainAgent", message_text: str) -> str:
    """The direct reply for a `conversational`-intent message — never
    touches agent selection, `history.query.HistoryQueryService`, or any
    of `orchestrator.question_flow`'s machinery, since nothing here is
    actually being asked for. The honesty constraint in the prompt above
    is a backstop, not the primary safeguard — classification getting
    "asks for something real" vs. "purely social" right is what actually
    keeps this branch from ever being asked to answer a genuine capability
    gap; see `_build_intent_prompt`'s own explicit "do I have any tasks?"
    example.
    """

    prompt = _build_conversational_prompt(message_text)
    with stage_context("conversational_reply"):
        result = main_agent.process(prompt, [])

    if result.status != "success":
        raise OrchestrationParseError(f"conversational reply did not produce a usable response: {result.text}")

    return result.text.strip()
