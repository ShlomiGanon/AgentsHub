"""Format output for chat (work_plan.md §8.10).

Three concerns, each one function: splitting long text at Telegram's
message-length limit without cutting mid-sentence, giving every
unprompted message type (clarification / approval / closure notice) a
distinct, unmistakable header that also says whether it needs an answer,
and ordering a delivered result as verdict, then what was done, then the
insight.
"""

from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from bot.api_client import FailureNotice, JobResult

# Telegram's hard limit on one message's text, per its Bot API docs.
TELEGRAM_MESSAGE_LIMIT = 4096

MessageKind = Literal[
    "clarification_needed",
    "approval_needed",
    "precedent_closure",
    "uncertain_verdict",
    "result",
    "failed",
    "declined",
]

# Every header states, up front, what the message is and whether it needs
# a reply — the two questions §8.10 requires a reader be able to answer
# before reading anything else.
_HEADERS: dict[MessageKind, str] = {
    "clarification_needed": "[CLARIFICATION NEEDED — please reply]",
    "approval_needed": "[APPROVAL NEEDED — please reply]",
    "precedent_closure": "[NOTICE — closed on precedent — no reply needed]",
    "uncertain_verdict": "[NOTICE — uncertain verdict — no reply needed]",
    "result": "[RESULT]",
    "failed": "[RUN FAILED]",
    "declined": "[DECLINED]",
}


def format_header(kind: MessageKind) -> str:
    return _HEADERS[kind]


def _split_on(text: str, separator: str, limit: int) -> list[str] | None:
    """Greedily pack `text` into chunks no longer than `limit`, breaking
    only at `separator` boundaries. Returns None if some single unit
    between separators is itself longer than `limit` — the caller falls
    back to a smaller separator rather than accept a chunk over the
    limit.
    """

    units = text.split(separator)
    chunks: list[str] = []
    current = ""

    for unit in units:
        candidate = unit if not current else current + separator + unit

        if len(candidate) <= limit:
            current = candidate
            continue

        if not current:
            # This single unit alone doesn't fit — the caller needs a finer separator.
            return None

        chunks.append(current)
        current = unit

        if len(current) > limit:
            return None

    if current:
        chunks.append(current)

    return chunks


def split_message(text: str, limit: int = TELEGRAM_MESSAGE_LIMIT) -> list[str]:
    """Split `text` into chunks that each fit in one Telegram message,
    breaking at paragraph boundaries first, then sentence boundaries,
    then plain newlines, and only as a last resort at a hard character
    boundary — never silently truncating and never splitting a sentence
    unless nothing else fits.
    """

    if len(text) <= limit:
        return [text] if text else [""]

    for separator in ("\n\n", ". ", "\n"):
        chunks = _split_on(text, separator, limit)
        if chunks is not None:
            return chunks

    return [text[i : i + limit] for i in range(0, len(text), limit)]


def format_job_result(result: "JobResult") -> str:
    kind: MessageKind = "result" if result.outcome != "declined" else "declined"
    lines = [format_header(kind), "", f"Verdict: {result.outcome}"]

    if result.steps_completed:
        lines += ["", "What was done:"]
        lines += [f"- {step}" for step in result.steps_completed]

    if result.insight_text:
        lines += ["", "Insight:", result.insight_text]

    return "\n".join(lines)


def format_failure_notice(notice: "FailureNotice") -> str:
    lines = [format_header("failed"), "", f"Failed step: {notice.failed_step_agent_name or '(unknown)'}", f"Reason: {notice.failure_reason}"]

    if notice.steps_completed_before_failure:
        lines += ["", "Completed before the failure:"]
        lines += [f"- {step}" for step in notice.steps_completed_before_failure]
    else:
        lines += ["", "Nothing completed before the failure."]

    return "\n".join(lines)
