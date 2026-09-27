"""The model-written run report — composes a short, natural reply from a `RunSummary`, grounded
strictly in its fields, with `render_summary` as the always-available fallback.

Wired in once, at the point a run's terminal outcome is recorded (`orchestrator.flows`), never on
a read path: `/Job` polling and `/Notifications` only ever read the already-stored result.
"""

import json
import logging
import time

from agents import Agent, InvocationPolicy
from messages import MessageCatalog
from messages.model_messages import (
    REPORT_COMPOSE_COMMANDER_AUDIENCE_RULES,
    REPORT_COMPOSE_INSTRUCTION,
    REPORT_COMPOSE_VIEWER_AUDIENCE_RULES,
)
from orchestrator.run_report import Audience, RunSummary, render_summary
from orchestrator.tone import banned_opener
from tools import get_trace_id, stage_context

logger = logging.getLogger(__name__)

REPORT_COMPOSE_TIMEOUT_SECONDS = 10.0
_COMPOSE_POLICY = InvocationPolicy(max_output_tokens=400, timeout_seconds=REPORT_COMPOSE_TIMEOUT_SECONDS, reasoning_effort="none")

_LANGUAGE_NAMES = {"en": "English", "he": "Hebrew"}


class ReportComposerAgent(Agent):
    name = "report_composer_agent"
    role = (
        "Writes the user-facing reply reporting what was understood and done for one event, "
        "strictly from a structured summary handed to it. Concludes; does not act."
    )
    system_prompt = (
        "You are the Report Composer. You are given a structured summary of one event's "
        "outcome and asked to write a short, natural reply to the user's original message. "
        "You may state only what the summary contains — never invent facts, names, or numbers."
    )


def _pending_context(summary: RunSummary, *, include_risk: bool) -> dict | None:
    pending = summary.pending
    if pending is None:
        return None
    if pending.kind == "clarification":
        return {"kind": "clarification", "unresolved_field": pending.unresolved_field}
    if pending.kind == "event_data":
        return {"kind": "event_data", "question": pending.question, "missing_fields": list(pending.missing_fields)}
    context: dict = {"kind": "approval"}
    if include_risk:
        context["reason"] = pending.reason
        context["risk_level"] = pending.risk_level
        context["risk_reason"] = pending.risk_reason
    return context


def _viewer_context(summary: RunSummary) -> dict:
    context: dict = {
        "raw_text": summary.raw_text,
        "classification": summary.classification,
        "area": summary.area,
        "severity": summary.severity,
        "description": summary.description,
        "outcome": summary.outcome,
        "outcome_failure_reason": summary.outcome_failure_reason,
    }
    pending = _pending_context(summary, include_risk=False)
    if pending is not None:
        context["pending"] = pending
    return context


def _commander_context(summary: RunSummary) -> dict:
    context = _viewer_context(summary)
    context["insight_text"] = summary.insight_text
    context["selected_protocol"] = summary.selected_protocol
    context["protocol_reason"] = summary.protocol_reason
    context["risk_level"] = summary.risk_level
    context["risk_reason"] = summary.risk_reason
    context["steps"] = [
        {
            "agent_name": step.agent_name,
            "task_text": step.task_text,
            "status": step.status,
            "result_text": step.result_text,
            "failure_reason": step.failure_reason,
        }
        for step in summary.steps
    ]
    pending = _pending_context(summary, include_risk=True)
    if pending is not None:
        context["pending"] = pending
    return context


def build_prompt(summary: RunSummary, audience: Audience, language: str, catalog: MessageCatalog | None = None) -> str:
    context = _commander_context(summary) if audience == "commander" else _viewer_context(summary)
    audience_rules = REPORT_COMPOSE_COMMANDER_AUDIENCE_RULES if audience == "commander" else REPORT_COMPOSE_VIEWER_AUDIENCE_RULES
    tone_examples = catalog.text("orchestrator.report_tone.examples") if catalog is not None else ""
    return REPORT_COMPOSE_INSTRUCTION.format(
        language=_LANGUAGE_NAMES.get(language, language),
        audience_rules=audience_rules,
        tone_examples=tone_examples,
        raw_text_json=json.dumps(summary.raw_text, ensure_ascii=False),
        context_json=json.dumps(context, ensure_ascii=False, sort_keys=True),
    )


def compose_report(
    agent: "ReportComposerAgent | None",
    summary: RunSummary,
    audience: Audience,
    catalog: MessageCatalog,
) -> str:
    """Always returns usable text — the model's composed reply, or, on any failure, timeout,
    unclear/empty response, or when no agent is available at all, the deterministic
    `render_summary` fallback. Never raises."""

    fallback = render_summary(summary, audience, catalog)
    if agent is None:
        return fallback

    prompt = build_prompt(summary, audience, catalog.language, catalog)
    started = time.monotonic()
    for attempt in (1, 2):
        try:
            with stage_context("report_composition"):
                result = agent.process(prompt, [], invocation_policy=_COMPOSE_POLICY)
        except Exception as exc:
            logger.warning(
                "report composition failed; using deterministic fallback",
                extra={
                    "event": "report_composed", "source": "fallback", "reason": str(exc),
                    "duration_seconds": time.monotonic() - started, "trace_id": get_trace_id(),
                },
            )
            return fallback

        if result.status != "success" or not result.text.strip():
            logger.warning(
                "report composition returned no usable text; using deterministic fallback",
                extra={
                    "event": "report_composed", "source": "fallback", "reason": "empty_or_unclear",
                    "duration_seconds": time.monotonic() - started, "trace_id": get_trace_id(),
                },
            )
            return fallback

        text = result.text.strip()
        banned = banned_opener(text, catalog)
        if banned is None:
            logger.info(
                "report composed",
                extra={
                    "event": "report_composed", "source": "model",
                    "duration_seconds": time.monotonic() - started, "trace_id": get_trace_id(),
                },
            )
            return text

        if attempt == 1:
            logger.info(
                "report composition used a banned opener; retrying once",
                extra={"event": "report_composed_retry", "banned_phrase": banned, "trace_id": get_trace_id()},
            )
            prompt = prompt + f"\n\nYour previous reply opened with a banned phrase (\"{banned}\"). Rewrite it, starting directly with what was understood and done."

    logger.warning(
        "report composition kept using a banned opener after retry; using deterministic fallback",
        extra={
            "event": "report_composed", "source": "fallback", "reason": "banned_opener",
            "duration_seconds": time.monotonic() - started, "trace_id": get_trace_id(),
        },
    )
    return fallback
