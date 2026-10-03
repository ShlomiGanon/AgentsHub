"""Deterministic, profile-independent run reporting.

Builds a structured account (`RunSummary`) of what a protocol run understood and did, from
already-persisted event/step/hold data — no model call. `render_summary` is the always-works
renderer built from it: the composer's own fallback (orchestrator/report_composer.py) and the
text used whenever rich reporting is disabled entirely.
"""

from dataclasses import dataclass
from typing import Literal

from messages import MessageCatalog, MessageCatalogError
from orchestrator.group_routing import GROUP_CHAT_TYPES

Audience = Literal["viewer", "commander"]

_HOLD_KINDS: tuple[str, ...] = ("approval", "clarification", "event_data")


@dataclass(frozen=True)
class StepSummary:
    """One persisted protocol step as shown in a run report."""

    agent_name: str
    task_text: str
    status: str
    result_text: str | None
    failure_reason: str | None


@dataclass(frozen=True)
class PendingSummary:
    """An unresolved hold still waiting on a human answer."""

    kind: Literal["approval", "clarification", "event_data"]
    reason: str | None = None
    risk_level: str | None = None
    risk_reason: str | None = None
    unresolved_field: str | None = None
    missing_fields: tuple[str, ...] = ()
    question: str | None = None


@dataclass(frozen=True)
class RunSummary:
    """Snapshot of one event used to compose or fall back a run report."""

    event_id: str
    raw_text: str
    sender_permission_level: str
    telegram_chat_type: str | None
    classification: str | None
    area: str | None
    entities: list | None
    description: str | None
    severity: str | None
    selected_protocol: str | None
    protocol_reason: str | None
    risk_level: str | None
    risk_reason: str | None
    steps: tuple[StepSummary, ...]
    pending: PendingSummary | None
    outcome: str | None
    insight_text: str | None
    outcome_failure_reason: str | None
    # Localized fact for handled_resource_unavailable; shown to every audience, unlike insight_text.
    resource_unavailable_fact: str | None = None


def _pending_from_hold(kind: str, hold: dict) -> PendingSummary:
    """Build a PendingSummary from one unresolved hold row."""

    if kind == "approval":
        return PendingSummary(
            kind="approval",
            reason=hold.get("reason"),
            risk_level=hold.get("risk_level"),
            risk_reason=hold.get("risk_reason"),
        )
    if kind == "clarification":
        return PendingSummary(kind="clarification", unresolved_field=hold.get("unresolved_field"))
    return PendingSummary(
        kind="event_data",
        missing_fields=tuple(hold.get("missing_fields", ())),
        question=hold.get("question"),
    )


def build_run_summary(persistence, event_id: str) -> RunSummary:
    """Read everything a report could be built from for one event — the same lookups
    `api.routes.job_status` already performs, held to one place so every caller (the composer,
    its deterministic fallback, and any future consumer) reads a single consistent snapshot."""

    event = persistence.fetch_event(event_id)
    if event is None:
        raise ValueError(f"no such event: '{event_id}'")

    pending: PendingSummary | None = None
    for kind in _HOLD_KINDS:
        hold = persistence.fetch_held_event(kind, event_id)
        if hold is not None and not hold.get("resolved"):
            pending = _pending_from_hold(kind, hold)
            break

    steps = tuple(
        StepSummary(
            agent_name=step["agent_name"],
            task_text=step["task_text"],
            status=step.get("status", ""),
            result_text=step.get("result_text"),
            failure_reason=step.get("failure_reason"),
        )
        for step in event.get("steps", [])
    )

    return RunSummary(
        event_id=event_id,
        raw_text=event.get("raw_text") or "",
        sender_permission_level=event.get("sender_permission_level") or "viewer",
        telegram_chat_type=event.get("telegram_chat_type"),
        classification=event.get("classification"),
        area=event.get("area"),
        entities=event.get("entities"),
        description=event.get("description"),
        severity=event.get("severity"),
        selected_protocol=event.get("selected_protocol"),
        protocol_reason=event.get("protocol_reason"),
        risk_level=event.get("risk_level"),
        risk_reason=event.get("risk_reason"),
        steps=steps,
        pending=pending,
        outcome=event.get("outcome"),
        insight_text=event.get("insight_text"),
        outcome_failure_reason=event.get("outcome_failure_reason"),
    )


def resolve_audience(summary: RunSummary) -> Audience:
    """Group chat -> viewer always, regardless of the poster's own permission level — a group
    is a shared, visible surface, and protocol/agent/risk internals must not leak into it just
    because whoever happened to post is a commander. Private chat -> the sender's own level,
    exactly as before (a private chat's identity is always the sender, per Telegram itself)."""

    if summary.telegram_chat_type in GROUP_CHAT_TYPES:
        return "viewer"
    return "commander" if summary.sender_permission_level == "commander" else "viewer"


def _translated(prefix: str, value: str | None, catalog: MessageCatalog) -> str | None:
    """Catalog lookup for a known code, or the raw value when no key exists."""

    if not value:
        return None
    try:
        return catalog.text(f"{prefix}.{value}")
    except MessageCatalogError:
        return value


def _understood_details(summary: RunSummary, catalog: MessageCatalog) -> str:
    """Short extracted-field line for the 'understood' sentence."""

    parts = [
        part
        for part in (summary.classification, summary.area, summary.severity, summary.description)
        if part
    ]
    return "; ".join(parts) if parts else catalog.text("common.none")


def _render_pending(pending: PendingSummary, audience: Audience, catalog: MessageCatalog) -> str:
    """Audience-scoped text for the still-open hold."""

    if pending.kind == "approval":
        if audience == "commander":
            risk_word = _translated("risk", pending.risk_level, catalog) or catalog.text("common.none")
            return catalog.text(
                "report.pending_approval_commander",
                risk_level=risk_word,
                risk_reason=pending.risk_reason or catalog.text("common.no_reason"),
            )
        return catalog.text("report.pending_approval_viewer")
    if pending.kind == "clarification":
        return catalog.text("report.pending_clarification", field=pending.unresolved_field or "")
    return catalog.text("report.pending_event_data", question=pending.question or "")


def render_summary(summary: RunSummary, audience: Audience, catalog: MessageCatalog) -> str:
    """The deterministic fallback — always produces text, without a model, from `summary`
    alone. Commander sees protocol/risk/per-step detail; viewer sees only what was understood,
    the outcome, and what is still needed from them (Part 2's audience rules)."""

    lines: list[str] = [catalog.text("report.understood", details=_understood_details(summary, catalog))]

    if summary.outcome:
        outcome_word = _translated("outcome", summary.outcome, catalog) or summary.outcome
        lines.append(catalog.text("report.outcome", outcome=outcome_word))
        if summary.outcome == "failed" and summary.outcome_failure_reason:
            lines.append(catalog.text("report.failure_reason", reason=summary.outcome_failure_reason))
        if summary.resource_unavailable_fact:
            lines.append(summary.resource_unavailable_fact)

    if audience == "commander" and summary.steps:
        lines.append("")
        lines.append(catalog.text("result.what_was_done"))
        for step in summary.steps:
            result = step.result_text if step.result_text is not None else (
                step.failure_reason or catalog.text("common.none")
            )
            lines.append(
                catalog.text("report.step_line", agent_name=step.agent_name, task_text=step.task_text, result=result)
            )
    elif audience == "viewer":
        # Same underlying facts a commander sees per step, but in the tool's own plain-language
        # confirmation text only -- never the agent name or the task text it was given, which
        # are internal routing details a viewer has no reason to see.
        actions = [step.result_text for step in summary.steps if step.status == "succeeded" and step.result_text]
        if actions:
            lines.append("")
            lines.append(catalog.text("result.what_was_done"))
            for action in actions:
                lines.append(catalog.text("report.action_line", action=action))

    if summary.pending is not None:
        lines.append("")
        lines.append(_render_pending(summary.pending, audience, catalog))

    if audience == "commander" and summary.selected_protocol:
        risk_word = _translated("risk", summary.risk_level, catalog) or catalog.text("common.none")
        reason = summary.protocol_reason or catalog.text("common.no_reason")
        lines.append("")
        lines.append(
            catalog.text("result.protocol_suffix", protocol_name=summary.selected_protocol, risk_level=risk_word, reason=reason)
        )

    return "\n".join(lines)
