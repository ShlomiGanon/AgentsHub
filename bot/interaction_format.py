"""Telegram message formatting shared by commands, holds, and delivery."""

from typing import TYPE_CHECKING, Literal

from messages import MessageCatalog, MessageCatalogError, get_catalog

if TYPE_CHECKING:
    from bot.contracts import FailureNotice, JobResult


TELEGRAM_MESSAGE_LIMIT = 4096


MessageKind = Literal[
    "clarification_needed",
    "approval_needed",
    "precedent_closure",
    "uncertain_verdict",
    "uncertain_reporter",
    "no_match",
    "result",
    "failed",
    "declined",
    "event_data_needed",
    "resource_unavailable_alert",
    "hold_escalation",
]


_HEADER_KEYS: dict[MessageKind, str] = {
    "clarification_needed": "header.clarification_needed",
    "approval_needed": "header.approval_needed",
    "precedent_closure": "header.precedent_closure",
    "uncertain_verdict": "header.uncertain_verdict",
    "uncertain_reporter": "header.uncertain_reporter",
    "no_match": "header.no_match",
    "result": "header.result",
    "failed": "header.failed",
    "declined": "header.declined",
    "event_data_needed": "header.event_data_needed",
    "resource_unavailable_alert": "header.resource_unavailable_alert",
    "hold_escalation": "header.hold_escalation",
}



def _catalog(catalog: MessageCatalog | None = None) -> MessageCatalog:
    """Use the given catalog, or English when none is passed."""

    return catalog or get_catalog("en")



def message_catalog_for(deps) -> MessageCatalog:
    """The profile's message catalog, falling back to English."""

    loaded_profile = getattr(deps, "loaded_profile", None)
    return getattr(loaded_profile, "message_catalog", None) or get_catalog("en")



def format_header(kind: MessageKind, catalog: MessageCatalog | None = None) -> str:
    """Translated header line for this message kind."""

    return _catalog(catalog).text(_HEADER_KEYS[kind])



def _split_on(text: str, separator: str, limit: int) -> list[str] | None:
    """Greedily pack `text` into chunks no longer than `limit`, breaking only at `separator` boundaries."""

    units = text.split(separator)
    chunks: list[str] = []
    current = ""

    for unit in units:
        candidate = unit if not current else current + separator + unit

        if len(candidate) <= limit:
            current = candidate
            continue

        if not current:
            return None

        chunks.append(current)
        current = unit

        if len(current) > limit:
            return None

    if current:
        chunks.append(current)

    return chunks



def split_message(text: str, limit: int = TELEGRAM_MESSAGE_LIMIT) -> list[str]:
    """Split `text` into chunks that each fit in one Telegram message, breaking at paragraph boundaries first, then sentence boundaries, then plain newlines, and only as a last resort..."""

    if len(text) <= limit:
        return [text] if text else [""]

    for separator in ("\n\n", ". ", "\n"):
        chunks = _split_on(text, separator, limit)
        if chunks is not None:
            return chunks

    return [text[i : i + limit] for i in range(0, len(text), limit)]



# A `failure_reason` is meant to be a short explanation, but its actual source can be an entire
# rejected model response (e.g. protocol selection's own raw chain-of-thought when the response
# didn't parse) — cap what a Telegram message ever shows for it, regardless of how that text was
# produced. Only the *displayed* copy is capped; the stored value (DB row, logs, DEEP_DEBUG) is
# never touched here and stays full length (docs/IMPROVES/CRITICAL_FIXES_PLAN.MD item 3).
_FAILURE_REASON_DISPLAY_LIMIT = 240



def _short_failure_reason(failure_reason: str) -> str:
    """Trim a failure reason for Telegram display without changing the stored value."""

    stripped = failure_reason.strip()
    if len(stripped) <= _FAILURE_REASON_DISPLAY_LIMIT:
        return stripped
    return stripped[:_FAILURE_REASON_DISPLAY_LIMIT].rstrip() + "…"



def _outcome_word(outcome: str, catalog: MessageCatalog) -> str:
    """`outcome` (history.event_pipeline.VALID_OUTCOMES) is a fixed, internal English identifier
    — interpolating it directly into a translated message left it as a raw English word inside an
    otherwise-Hebrew sentence (docs/IMPROVES/CRITICAL_FIXES_PLAN.MD item 4). Every valid outcome
    has a matching `outcome.<value>` catalog key in both languages; the fallback to the raw value
    is defensive only — it should never actually trigger while the catalog stays in sync with
    VALID_OUTCOMES."""

    try:
        return catalog.text(f"outcome.{outcome}")
    except MessageCatalogError:
        return outcome



def _risk_level_word(risk_level: str, catalog: MessageCatalog) -> str:
    """`risk_level` (`orchestrator.reasoning.RiskAssessment.level`, `Literal["high",
    "low"]`) is a fixed, internal English identifier — same class of bug as
    `_outcome_word` above, found while building item #9's protocol suffix
    (REQUIRED_FIELDS_AND_CLOSED_DECISIONS.md HARD RULE: don't introduce a third
    untranslated-value instance). Also applied to the two pre-existing call sites
    that already interpolated `risk_level` raw (`approval.risk`, `notice.no_match`)."""

    try:
        return catalog.text(f"risk.{risk_level}")
    except MessageCatalogError:
        return risk_level



def format_job_result(result: "JobResult", catalog: MessageCatalog | None = None) -> str:
    """Telegram body for a finished job, including the protocol suffix when one ran."""

    messages = _catalog(catalog)
    if result.selection_required:
        selection_text = "\n".join(result.steps_completed).strip()
        return (
            f"{format_header('event_data_needed', messages)}\n"
            f"{messages.text('result.job_id', job_id=result.job_id)}\n\n{selection_text}"
        )

    kind: MessageKind = "result" if result.outcome != "declined" else "declined"
    lines = [format_header(kind, messages), "", messages.text("result.verdict", outcome=_outcome_word(result.outcome, messages))]

    if result.failure_reason:
        lines += ["", _short_failure_reason(result.failure_reason)]

    if result.steps_completed:
        lines += ["", messages.text("result.what_was_done")]
        lines += [f"- {step}" for step in result.steps_completed]

    if result.insight_text:
        lines += ["", messages.text("result.insight"), result.insight_text]

    if result.protocol_name:
        # Always-on trailing protocol/reason line (REQUIRED_FIELDS_AND_CLOSED_
        # DECISIONS.md Part 3 / item #9) — sourced entirely from data already
        # computed during the run, no new model call. Omitted whenever no
        # protocol was ever selected (e.g. a `no_match_protocol` outcome) —
        # there is nothing to name in that case, same principle as the plain
        # question/conversation replies this doesn't apply to at all (those
        # never reach format_job_result). Uses the protocol *selection*
        # reason, not the risk reason, as "the reason the protocol that ran
        # was chosen."
        risk_word = _risk_level_word(result.risk_level, messages) if result.risk_level else messages.text("common.none")
        reason = result.protocol_reason or messages.text("common.no_reason")
        lines += [
            "",
            messages.text(
                "result.protocol_suffix", protocol_name=result.protocol_name, risk_level=risk_word, reason=reason
            ),
        ]

    return "\n".join(lines)



def format_failure_notice(notice: "FailureNotice", catalog: MessageCatalog | None = None) -> str:
    """Telegram body for a failed agent step."""

    messages = _catalog(catalog)
    agent = notice.failed_step_agent_name or messages.text("common.unknown")
    lines = [
        format_header("failed", messages),
        "",
        messages.text("failure.failed_step", agent=agent),
        messages.text("failure.reason", reason=_short_failure_reason(notice.failure_reason)),
    ]

    if notice.steps_completed_before_failure:
        lines += ["", messages.text("failure.completed_before")]
        lines += [f"- {step}" for step in notice.steps_completed_before_failure]
    else:
        lines += ["", messages.text("failure.nothing_completed")]

    return "\n".join(lines)



def format_event_data_needed(notice, catalog: MessageCatalog | None = None) -> str:
    """Telegram body asking the reporter for missing event data."""

    return f"{format_header('event_data_needed', catalog)}\n\n{notice.question}"
