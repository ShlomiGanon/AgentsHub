"""Shared flow types, deadline checks, and terminal-outcome recording.

Both `flows.py` and `flows_protocol.py` import this module so protocol
execution does not need a circular host import.
"""

import logging
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Callable, Literal

from history import parse_timestamp, record_event_data_update, record_event_outcome, record_event_state
from messages import get_catalog
from orchestrator.report_composer import compose_report
from orchestrator.run_report import build_run_summary, resolve_audience
from orchestrator.reasoning import construct_core_agents as construct_main_agent
from orchestrator.reasoning import construct_insights_agent
from profiles import OptimizationPolicy
from protocols.executor import execute_steps
from tools.log_events import event_correction_recorded, event_outcome, reply_latency

if TYPE_CHECKING:
    from agents import Agent
    from agents.runtime import AgentRegistry
    from config import BaseConfig, SettingsStore
    from history.query import HistoryQueryService
    from messages import MessageCatalog
    from orchestrator.report_composer import ReportComposerAgent
    from persistence import PersistenceInterface
    from profiles.loader import LoadedProfile
    from protocols import ProtocolSet
    from profiles import AreaRegistry, EventTypeRegistry

logger = logging.getLogger(__name__)

# Tests patch orchestrator.flows.execute_steps; call_execute_steps reads that name at call time.
execute_steps = execute_steps

FlowOutcome = Literal[
    "closed_on_precedent", "declined", "succeeded", "failed", "uncertain", "no_match_protocol",
    "held_for_clarification", "held_for_approval", "waiting_for_event_data", "waiting_for_drone_selection",
    "handled_resource_unavailable",
]

_VERDICT_TO_OUTCOME: dict[str, FlowOutcome] = {
    "success": "succeeded",
    "failure": "failed",
    "uncertain": "uncertain",
}


@dataclass(frozen=True)
class FlowDeps:
    """Persistence, registries, and settings one orchestration run needs."""

    persistence: "PersistenceInterface"
    settings_store: "SettingsStore"
    registry: "AgentRegistry"
    protocol_set: "ProtocolSet"
    event_type_registry: "EventTypeRegistry"
    area_registry: "AreaRegistry"
    history_query_service: "HistoryQueryService"
    optimization_policy: OptimizationPolicy = OptimizationPolicy()
    conversation_history_turns: int = 0
    conversation_history_ttl_hours: int = 24
    # Rich run-report composition: the SUB-tier agent that writes report_text.
    # None means compose_report always falls back, matching pre-feature behavior.
    report_composer_agent: "ReportComposerAgent | None" = None
    message_catalog: "MessageCatalog" = field(default_factory=lambda: get_catalog("en"))
    # Bound Telegram-group agent, a protocol-selection hint only — never a hard filter.
    preferred_agent_hint: str | None = None
    # Profile hook: (resource_kind, area, reason, registry) -> (fact, alternatives).
    resource_unavailable_description: "Callable[[str, str, str, object], tuple[str, str]] | None" = None


@dataclass(frozen=True)
class FlowResult:
    """Terminal or held outcome of one orchestration run."""

    event_id: str
    outcome: FlowOutcome
    detail: str = ""


def operational_context_for(deps: FlowDeps, exclude_event_id: str | None = None) -> list[dict]:
    """Recent cross-channel event digest for one decision; empty when history cannot supply it."""

    reader = getattr(deps.history_query_service, "operational_context", None)
    if not callable(reader):
        return []
    try:
        entries = reader(
            area_labels=getattr(deps.area_registry, "labels", None),
            exclude_event_ids=(exclude_event_id,) if exclude_event_id else (),
        )
    except Exception as exc:  # context is advisory; a read failure must not block the run
        logger.warning(
            "operational context unavailable",
            extra={"event": "operational_context_failed", "reason": str(exc)},
        )
        return []
    return entries if isinstance(entries, list) else []


def sender_context_for(deps: FlowDeps, event: dict) -> dict | None:
    """Who sent this event and from which channel, so a decision can tell a team member's own
    update from an outside party's report about others."""

    identity = str(event.get("sender_identity") or "")
    if not identity:
        return None
    try:
        user = deps.persistence.read_user(identity) or {}
    except Exception:  # advisory only
        user = {}
    channel = None
    chat_id = event.get("telegram_chat_id")
    if chat_id:
        try:
            channel = next(
                (group.get("label") for group in deps.persistence.list_groups() if str(group.get("chat_id")) == str(chat_id)),
                None,
            )
        except Exception:
            channel = None
    if channel is None and event.get("telegram_chat_type") == "private":
        channel = "private chat with the system"
    context = {
        "name": (user.get("full_name") or "").strip() or None,
        "role": "commander" if event.get("sender_permission_level") == "commander" else None,
        "channel": channel,
    }
    return {key: value for key, value in context.items() if value} or None


def apply_event_links(
    deps: FlowDeps,
    event_id: str,
    context: list[dict],
    *,
    related_ref: str | None = None,
    corrects_ref: str | None = None,
    area: str | None = None,
) -> dict:
    """Record incident membership, an explicit correction, and a resolved area for one event.

    Refs are only trusted when they name an entry of the context the model was shown, so a
    hallucinated id can never retract or re-parent an unrelated event. Returns the updates made."""

    known_refs = {entry.get("ref") for entry in context if entry.get("ref")}
    event = deps.persistence.fetch_event(event_id) or {}
    state: dict = {}
    data: dict = {}

    anchor_ref = related_ref if related_ref in known_refs and related_ref != event_id else None
    corrected_ref = corrects_ref if corrects_ref in known_refs and corrects_ref != event_id else None
    anchor = deps.persistence.fetch_event(anchor_ref or corrected_ref) if (anchor_ref or corrected_ref) else None
    if anchor and not event.get("incident_id"):
        state["incident_id"] = anchor.get("incident_id") or anchor.get("event_id")

    if corrected_ref and not event.get("corrects_event_id"):
        state["corrects_event_id"] = corrected_ref
        record_event_state(deps.persistence, corrected_ref, {"retracted": True})
        event_correction_recorded(event_id=event_id, corrects_event_id=corrected_ref)

    if not event.get("area"):
        resolved = None
        if area:
            resolver = getattr(deps.area_registry, "resolve", None)
            resolved = resolver(area) if callable(resolver) else (area if deps.area_registry.is_valid(area) else None)
        if resolved is None and anchor_ref and anchor:
            resolved = anchor.get("area")
        if resolved:
            data["area"] = resolved

    if state:
        record_event_state(deps.persistence, event_id, state)
    if data:
        record_event_data_update(deps.persistence, event_id, data)
    return {**state, **data}


@dataclass(frozen=True)
class EventDataReplyResult:
    """Parsed extra-data reply, or the event ids that made it ambiguous."""

    event_id: str
    updates: dict[str, object]
    message: str
    # Set when more than one pending event-data hold matches the same sender.
    ambiguous_event_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class DroneSelectionReplyResult:
    """Result of applying a drone-selection reply to a waiting event."""

    event_id: str
    message: str
    status: Literal["waiting_for_drone_selection", "succeeded"]


def assemble_core_agents(loaded_profile: "LoadedProfile", base_config: "BaseConfig") -> dict[str, "Agent"]:
    """Merge profile agents with the constructed Main and Insights agents."""

    return {
        **loaded_profile.core_agents,
        **construct_main_agent(base_config),
        **construct_insights_agent(base_config),
    }


def _now() -> str:
    """Current UTC timestamp in ISO-8601 storage form."""

    return datetime.now(timezone.utc).isoformat()


def _log_event_outcome(event_id: str, outcome: str, **detail) -> None:
    """Log one terminal outcome so a run can be reassembled from the event stream."""

    event_outcome(event_id=event_id, outcome=outcome, **detail)


def _log_reply_latency(deps: FlowDeps, event_id: str) -> None:
    """Record received_at-to-reply seconds once, at the shared terminal-outcome write."""

    event = deps.persistence.fetch_event(event_id)
    received_at = event.get("received_at") if event else None
    if not received_at:
        return
    try:
        elapsed_seconds = (datetime.now(timezone.utc) - parse_timestamp(received_at)).total_seconds()
    except (TypeError, ValueError):
        return
    reply_latency(event_id=event_id, elapsed_seconds=elapsed_seconds)


def _selected_protocol(deps: FlowDeps, event_id: str):
    """The protocol already stored on the event, if selection finished."""

    event = deps.persistence.fetch_event(event_id)
    protocol_name = event.get("selected_protocol") if event else None
    if not protocol_name:
        return None
    return deps.protocol_set.get(protocol_name)


def _record_outcome_with_report(
    deps: FlowDeps,
    event_id: str,
    outcome: str,
    failure_reason: str | None = None,
    insight_text: str | None = None,
    resource_unavailable_fact: str | None = None,
    commander_alert_text: str | None = None,
    force_compose: bool = False,
) -> None:
    """Persist a terminal outcome and compose report_text once before notifications fire."""

    report_text = None
    protocol = _selected_protocol(deps, event_id)
    reply_key = getattr(protocol, "viewer_reply_key", "") if protocol else ""
    notice_key = getattr(protocol, "operational_notice_key", "") if protocol else ""
    if outcome == "succeeded" and reply_key and (
        deps.report_composer_agent is None or not deps.settings_store.get_rich_reports_enabled()
    ):
        report_text = deps.message_catalog.text(reply_key)
    elif deps.settings_store.get_rich_reports_enabled() or resource_unavailable_fact is not None or force_compose:
        summary = build_run_summary(deps.persistence, event_id)
        summary = replace(
            summary,
            outcome=outcome,
            outcome_failure_reason=failure_reason,
            insight_text=insight_text if insight_text is not None else summary.insight_text,
            resource_unavailable_fact=resource_unavailable_fact,
        )
        history_only = protocol is not None and tuple(protocol.participating_agents) == ("history_agent",)
        record_answer = "\n".join(
            step.result_text.strip()
            for step in summary.steps
            if step.status == "succeeded" and step.result_text and step.result_text.strip()
        )
        if history_only and outcome == "succeeded" and record_answer:
            # A debrief or timeline written from the stored record is itself the answer; the
            # short report composer would boil it down to a sentence about having summarized.
            report_text = record_answer
        elif summary.selected_protocol in {"query_situational_picture", "overall_situational_picture"}:
            # Picture protocols collect deliberately detailed, heterogeneous read-tool output.
            # Prefer the dedicated picture synthesis produced by flows_protocol; concatenating
            # the raw tool strings leaks their unrelated headings, languages and formatting.
            # The raw findings remain a deterministic fallback if synthesis was unavailable.
            report_text = (summary.insight_text or "").strip()
            if not report_text:
                picture_lines = [
                    step.result_text.strip()
                    for step in summary.steps
                    if step.status == "succeeded" and step.result_text and step.result_text.strip()
                ]
                report_text = "\n".join(picture_lines)
        else:
            # A protocol's fixed reply is only the fallback: the composed reply answers what this
            # sender actually said (a correction, a request, a follow-up), not a canned sentence.
            fixed_reply = deps.message_catalog.text(reply_key) if outcome == "succeeded" and reply_key else None
            audience = resolve_audience(summary)
            report_text = compose_report(
                deps.report_composer_agent, summary, audience, deps.message_catalog,
                fallback_text=fixed_reply,
                recent_reports=operational_context_for(deps, exclude_event_id=event_id) if audience == "commander" else None,
                area_labels=getattr(deps.area_registry, "labels", None),
            )

    record_event_outcome(
        deps.persistence, event_id, outcome,
        failure_reason=failure_reason, insight_text=insight_text, report_text=report_text,
        commander_alert_text=commander_alert_text,
    )
    if outcome == "succeeded" and notice_key:
        deps.persistence.insert_notification("operational_update", event_id)
    _log_reply_latency(deps, event_id)


def _deadline_failure(deps: FlowDeps, event_id: str, next_stage: str) -> FlowResult | None:
    """Failed FlowResult when the event deadline has already passed, else None."""

    event = deps.persistence.fetch_event(event_id)
    # Approval is an explicit pause; the original queue deadline must not kill the approved resume.
    if event is not None and event.get("approval_answered_at"):
        return None
    deadline_at = event.get("deadline_at") if event is not None else None
    if not deadline_at:
        return None
    try:
        deadline = datetime.fromisoformat(deadline_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) < deadline:
        return None
    reason = f"event deadline exceeded before {next_stage}"
    _record_outcome_with_report(deps, event_id, "failed", failure_reason=reason)
    _log_event_outcome(event_id, "failed", failure_reason=reason, stage=next_stage)
    return FlowResult(event_id, "failed", reason)


def call_execute_steps(*args, **kwargs):
    """Run protocol steps; tests patch orchestrator.flows.execute_steps so this name is resolved at call time."""

    import orchestrator.flows as flows
    return flows.execute_steps(*args, **kwargs)


def call_finish_protocol_assessment(*args, **kwargs):
    """Finish a protocol run; tests patch orchestrator.flows._finish_protocol_assessment so this name is resolved at call time."""

    import orchestrator.flows as flows
    return flows._finish_protocol_assessment(*args, **kwargs)
