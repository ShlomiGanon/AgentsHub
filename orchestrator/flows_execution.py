"""Shared flow types, deadline checks, and terminal-outcome recording.

Both `flows.py` and `flows_protocol.py` import this module so protocol
execution does not need a circular host import.
"""

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Callable, Literal

from history import parse_timestamp, record_event_outcome
from messages import get_catalog
from orchestrator.report_composer import compose_report
from orchestrator.run_report import build_run_summary, resolve_audience
from orchestrator.reasoning import construct_core_agents as construct_main_agent
from orchestrator.reasoning import construct_insights_agent
from profiles import OptimizationPolicy
from protocols.executor import execute_steps
from tools.log_events import event_outcome, reply_latency

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
    if deps.settings_store.get_rich_reports_enabled() or resource_unavailable_fact is not None or force_compose:
        summary = build_run_summary(deps.persistence, event_id)
        summary = replace(
            summary,
            outcome=outcome,
            outcome_failure_reason=failure_reason,
            insight_text=insight_text if insight_text is not None else summary.insight_text,
            resource_unavailable_fact=resource_unavailable_fact,
        )
        if summary.selected_protocol in {"query_situational_picture", "overall_situational_picture"} and summary.insight_text:
            # Picture text is already user-facing; the generic composer would leak step internals.
            report_text = summary.insight_text.strip()
        else:
            report_text = compose_report(deps.report_composer_agent, summary, resolve_audience(summary), deps.message_catalog)

    record_event_outcome(
        deps.persistence, event_id, outcome,
        failure_reason=failure_reason, insight_text=insight_text, report_text=report_text,
        commander_alert_text=commander_alert_text,
    )
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
