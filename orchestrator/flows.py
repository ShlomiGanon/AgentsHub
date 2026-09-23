"""The new-event flow and the package's declared entry point (work_plan.md §6.11, §6.14)."""

import functools
import inspect
import json
import logging
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Literal

from history import (
    ExtractionExecutionError,
    InitialEventEnvelope,
    StepExecutionEnvelope,
    extract_event,
    parse_timestamp,
    record_event_data_update,
    record_event_outcome,
    record_event_state,
    record_action_lifecycle,
    record_extracted_fields,
    record_initial_event,
    record_step_execution,
    resolve_availability_period,
    storage_timestamp,
)
from orchestrator.holds import (
    UNRESOLVED_FIELD,
    answer_approval_hold,
    answer_clarification_hold,
    create_approval_hold,
    create_clarification_hold,
    create_event_data_hold,
    determine_approval_hold,
    determine_clarification_hold,
    protocol_requires_approval,
)
from orchestrator.capabilities import (
    CAPABILITY_DESCRIPTORS,
    CapabilityDescriptor,
    build_role_aware_system_context,
    visible_capabilities,
)
from orchestrator.response_contract import (
    ResponseAuthorityError,
    ResponseClaim,
    ResponseEnvelope,
    capability_response,
    permission_response,
    informational_response,
    render_response,
    refusal_response,
    validate_response,
)
from orchestrator.reasoning import build_insight, construct_insights_agent
from orchestrator.reasoning import (
    MainAgent,
    OrchestrationParseError,
    answer_conversationally,
    answer_question_from_plan,
    assess_final_once,
    assess_risk,
    classify_intent,
    construct_core_agents as construct_main_agent,
    formulate_tasks,
    formulate_event_data_question,
    judge_success,
    make_operational_decision,
    make_operational_intake,
    plan_message,
    rewrite_task,
    extract_event_data_update,
    select_protocol,
    IntentResult,
    ProtocolSelectionResult,
    OperationalDecision,
    OperationalIntake,
    RiskAssessment,
    run_parallel_specialists,
)
from orchestrator.reasoning import answer_question, determine_closure, look_up_precedent
from orchestrator.situational_picture import (  # re-exported: api may only import orchestrator.flows
    OperationalContext,
    OperationalReasoning,
    SituationalPicture,
    SituationalQueryScope,
    build_operational_context,
    build_situational_picture,
    build_typed_snapshot,
    classify_situational_query,
    compose_picture_from_step_outcomes,
    render_typed_snapshot,
    REASONING_OUTPUT_TOKEN_BUDGET,
)
from orchestrator.fixed_state import (  # re-exported: API fixed controls use authoritative stores directly
    FIXED_STATE_PROTOCOLS,
    FixedStateRead,
    read_fixed_operational_state,
)
from orchestrator.follow_up import FollowUpResolution, is_context_dependent_follow_up, resolve_follow_up
from orchestrator.event_queue import PolicyAwareEventQueue, SerialEventQueue, WorkItem
from orchestrator.group_routing import (  # re-exported: api may only import orchestrator.flows
    GROUP_CHAT_TYPES,
    MAIN_AGENT_TARGET,
    GroupBinding,
    GroupNotRegisteredError,
    GroupRoutingTable,
    InvalidRoutingTargetError,
    is_scoped_target,
    resolve_scope,
    scope_deps,
)
from profiles import (
    HUMAN_ACTIVATION_TYPE,
    OptimizationPolicy,
    UNCLASSIFIED_TYPE,
    current_operational_profile,
    profile_for_scope,
)
from protocols import CriticalityLevel, Step, StepOutcome
from protocols.executor import execute_steps
from agents import authenticated_request_identity, trusted_event_metadata, project_report_facts, ToolReceipt, ReportIngestionResult
from orchestrator.supersession import (
    CORRECTION_REPORT_TYPE,
    RETRACTION,
    classify_correction,
    resolve_superseded_event,
)

# How far back a correction may look for the report it retracts. Bounded so the
# lookup stays a lookup and never becomes a scan of the whole history.
CORRECTION_CANDIDATE_LIMIT = 50
from persistence import EventSearchCriteria, operational_timestamp_of_event, scope_from_event
from tools import get_trace_id

if TYPE_CHECKING:
    from agents import Agent
    from agents.runtime import AgentRegistry
    from auth.permissions import PermissionLevel
    from config import BaseConfig, SettingsStore
    from history.query import HistoryQueryService
    from orchestrator.holds import HoldAnswerResult, HoldReason
    from orchestrator.reasoning import InsightsAgent, MainAgent
    from persistence import PersistenceInterface
    from profiles.loader import LoadedProfile
    from protocols import Protocol, ProtocolSet
    from profiles import AreaRegistry, EventTypeRegistry

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ExpiryFinalizationResult:
    """Auditable result of one canonical expiry reconciliation attempt."""

    event_id: str
    status: Literal["finalized", "skipped"]
    previous_outcome: str | None
    outcome: str | None
    finalization_reason: str | None
    recovery_mode: bool
    hold_kinds: tuple[str, ...] = ()
    resolved_hold_ids: tuple[str, ...] = ()
    recovery_evidence: tuple[str, ...] = ()
    skip_reason: str | None = None


_EXPIRY_FINALIZER_IDENTITY = "system:expiry_finalizer"
_EXPIRY_HOLD_REASONS = {
    "approval": "approval_expired",
    "event_data": "required_event_data_expired",
    "clarification": "clarification_expired",
}
_ACTION_STATES_FAILED_BY_EXPIRY = frozenset({"requested", "pending_approval", "approved", "executing"})


def _expiry_timestamp(value: datetime | None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        return current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)


def _event_deadline_has_expired(event: dict, now: datetime) -> bool | None:
    deadline_at = event.get("deadline_at")
    if not deadline_at:
        return False

    try:
        return parse_timestamp(str(deadline_at)) <= now
    except (TypeError, ValueError):
        return None


def _unresolved_hold_kinds(persistence, event_id: str) -> tuple[str, ...]:
    kinds = []
    for kind in ("approval", "event_data", "clarification"):
        if any(hold.get("event_id") == event_id for hold in persistence.list_held_events(kind)):
            kinds.append(kind)
    return tuple(kinds)


def _successful_receipts(event: dict) -> tuple[str, ...]:
    markers: list[str] = []
    seen_receipt_ids: set[str] = set()
    sources = [event.get("action_tool_receipts") or ()]
    sources.extend(step.get("tool_receipts") or () for step in event.get("steps", ()))

    for receipts in sources:
        for receipt in receipts:
            if not isinstance(receipt, dict):
                continue
            if receipt.get("status") != "succeeded" or receipt.get("success") is not True:
                continue
            tool_name = receipt.get("tool_name")
            receipt_id = receipt.get("receipt_id")
            if not tool_name or not receipt_id or receipt_id in seen_receipt_ids:
                continue
            seen_receipt_ids.add(receipt_id)
            markers.append(f"tool:{tool_name}:{receipt_id}")

    return tuple(markers)


def finalize_expired_event(
    deps: "FlowDeps",
    event_id: str,
    now: datetime | None = None,
    *,
    recovery_mode: bool = False,
    active_event_ids: tuple[str, ...] = (),
) -> ExpiryFinalizationResult:
    """Finalize one expired event through the atomic persistence CAS path."""

    event = deps.persistence.fetch_event(event_id)
    current = _expiry_timestamp(now)
    if event is None:
        return ExpiryFinalizationResult(
            event_id, "skipped", None, None, None, recovery_mode, skip_reason="event_not_found"
        )

    if event_id in set(active_event_ids):
        return ExpiryFinalizationResult(
            event_id,
            "skipped",
            event.get("outcome"),
            event.get("outcome"),
            None,
            recovery_mode,
            skip_reason="active_processing",
        )

    expired = _event_deadline_has_expired(event, current)
    if expired is False:
        return ExpiryFinalizationResult(
            event_id,
            "skipped",
            event.get("outcome"),
            event.get("outcome"),
            None,
            recovery_mode,
            skip_reason="deadline_not_expired",
        )
    if expired is None:
        return ExpiryFinalizationResult(
            event_id,
            "skipped",
            event.get("outcome"),
            event.get("outcome"),
            None,
            recovery_mode,
            skip_reason="invalid_deadline",
        )

    hold_kinds = _unresolved_hold_kinds(deps.persistence, event_id)
    recovery_evidence = _successful_receipts(event)
    action_state = event.get("action_state")
    if action_state == "executed" and not recovery_evidence:
        return ExpiryFinalizationResult(
            event_id,
            "skipped",
            event.get("outcome"),
            event.get("outcome"),
            None,
            recovery_mode,
            hold_kinds=hold_kinds,
            skip_reason="executed_state_without_successful_receipt",
        )

    if recovery_evidence and action_state not in {"executing", "executed"}:
        return ExpiryFinalizationResult(
            event_id,
            "skipped",
            event.get("outcome"),
            event.get("outcome"),
            None,
            recovery_mode,
            hold_kinds=hold_kinds,
            recovery_evidence=recovery_evidence,
            skip_reason="receipt_without_executing_lifecycle",
        )

    recovered_successfully = bool(recovery_evidence)
    finalization_reason = (
        None
        if recovered_successfully
        else _EXPIRY_HOLD_REASONS.get(hold_kinds[0], "deadline_expired") if hold_kinds else "deadline_expired"
    )
    final_outcome = "succeeded" if recovered_successfully else "failed"
    resolved_action_state = "executed" if recovered_successfully and action_state == "executing" else None
    failed_action_state = (
        "failed"
        if not recovered_successfully and action_state in _ACTION_STATES_FAILED_BY_EXPIRY
        else resolved_action_state
    )
    resolution_reason = finalization_reason or "reconciled_after_execution_evidence"
    persistence_result = deps.persistence.finalize_event_if_open(
        event_id,
        final_outcome,
        failure_reason=finalization_reason,
        action_state=failed_action_state,
        action_failure_reason=finalization_reason,
        resolved_by=_EXPIRY_FINALIZER_IDENTITY,
        resolution={
            "decision": "reconciled" if recovered_successfully else "expired",
            "finalization_reason": resolution_reason,
            "recovery_mode": recovery_mode,
        },
        finalized_at=storage_timestamp(current),
        emit_notification=not recovery_mode,
    )

    changed = persistence_result.event_changed or bool(persistence_result.resolved_hold_ids)
    if changed:
        logger.info(
            "expired event finalized",
            extra={
                "event": "expired_event_finalized",
                "event_id": event_id,
                "outcome": persistence_result.outcome,
                "finalization_reason": resolution_reason,
                "recovery_mode": recovery_mode,
                "recovery_evidence": list(recovery_evidence),
                "resolved_hold_ids": list(persistence_result.resolved_hold_ids),
                "trace_id": event.get("trace_id") or get_trace_id(),
            },
        )

    return ExpiryFinalizationResult(
        event_id,
        "finalized" if changed else "skipped",
        persistence_result.previous_outcome,
        persistence_result.outcome,
        finalization_reason,
        recovery_mode,
        hold_kinds=hold_kinds,
        resolved_hold_ids=persistence_result.resolved_hold_ids,
        recovery_evidence=recovery_evidence,
        skip_reason=None if changed else "already_reconciled",
    )


def finalize_expired_events(
    deps: "FlowDeps",
    now: datetime | None = None,
    *,
    recovery_mode: bool = False,
    active_event_ids: tuple[str, ...] = (),
    limit: int = 100,
) -> tuple[ExpiryFinalizationResult, ...]:
    """Run one bounded, deterministic expiry reconciliation pass."""

    current = _expiry_timestamp(now)
    candidates = {
        event["event_id"]: event
        for event in deps.persistence.list_expired_events(storage_timestamp(current), limit)
    }

    for kind in ("approval", "event_data", "clarification"):
        for hold in deps.persistence.list_held_events(kind):
            event_id = hold.get("event_id")
            if not event_id or event_id in candidates:
                continue
            event = deps.persistence.fetch_event(event_id)
            if event is None or _event_deadline_has_expired(event, current) is not True:
                continue
            candidates[event_id] = event

    ordered = sorted(
        candidates.values(),
        key=lambda event: (str(event.get("deadline_at") or ""), event["event_id"]),
    )[:limit]
    return tuple(
        finalize_expired_event(
            deps,
            event["event_id"],
            current,
            recovery_mode=recovery_mode,
            active_event_ids=active_event_ids,
        )
        for event in ordered
    )


def _deadline_failure(deps: "FlowDeps", event_id: str, next_stage: str) -> "FlowResult | None":
    event = deps.persistence.fetch_event(event_id)
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
    finalization = finalize_expired_event(deps, event_id)
    reason = finalization.finalization_reason or f"event deadline exceeded before {next_stage}"
    if finalization.status == "finalized":
        _log_event_outcome(event_id, finalization.outcome or "failed", failure_reason=reason, stage=next_stage)
    return FlowResult(event_id, finalization.outcome or "failed", reason)

FlowOutcome = Literal[
    "closed_on_precedent", "declined", "succeeded", "failed", "uncertain", "no_match_protocol",
    "held_for_clarification", "held_for_approval", "waiting_for_event_data", "waiting_for_drone_selection",
]

_VERDICT_TO_OUTCOME: dict[str, FlowOutcome] = {
    "success": "succeeded",
    "failure": "failed",
    "uncertain": "uncertain",
}


def assemble_core_agents(loaded_profile: "LoadedProfile", base_config: "BaseConfig") -> dict[str, "Agent"]:
    """The merge point for core-agent construction — see module docstring."""

    return {
        **loaded_profile.core_agents,
        **construct_main_agent(base_config),
        **construct_insights_agent(base_config),
    }


@dataclass(frozen=True)
class FlowDeps:
    persistence: "PersistenceInterface"
    settings_store: "SettingsStore"
    registry: "AgentRegistry"
    protocol_set: "ProtocolSet"
    event_type_registry: "EventTypeRegistry"
    area_registry: "AreaRegistry"
    history_query_service: "HistoryQueryService"
    optimization_policy: OptimizationPolicy = OptimizationPolicy()
    timezone_name: str = "UTC"
    conversation_history_turns: int = 0
    conversation_history_ttl_hours: int = 24
    event_type_business_fields: object = None
    # Trusted routing context set by /Msg for a registered Telegram group.
    # This value comes from the persisted group binding, never from model text.
    group_owner: str | None = None
    # The loaded deployment profile, used only to resolve which operational
    # organization owns an event's scope. Never used to choose behaviour from
    # message content.
    loaded_profile: object = None


@dataclass(frozen=True)
class FlowResult:
    event_id: str
    outcome: FlowOutcome
    detail: str = ""


@dataclass(frozen=True)
class EventDataReplyResult:
    event_id: str
    updates: dict[str, object]
    message: str
    # Populated instead of the fields above when more than one pending
    # event-data hold matches the same (conversation_id, sender_identity) —
    # see the ambiguity check in `apply_event_data_reply`. Empty otherwise.
    ambiguous_event_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class FastPathPlan:
    extraction: object
    decision: OperationalDecision | None = None
    protocol: "Protocol | None" = None
    domain_only: bool = False


def _model_invoker_for(main_agent: "MainAgent"):
    def _invoke(prompt: str) -> str:
        agent_result = main_agent.process(prompt, [])
        if agent_result.status != "success":
            raise ExtractionExecutionError(f"main agent could not produce a usable extraction response: {agent_result.text}")
        return agent_result.text

    return _invoke


def _apply_attendance_temporal_fields(
    extraction_result, raw_text: str, received_at: str, timezone_name: str,
    reference_time: str | None = None,
):
    if extraction_result.classification != "team_attendance_report":
        return extraction_result

    # Simulation steps carry a trusted scenario reference; production keeps
    # the real receive timestamp as the temporal basis.
    period = resolve_availability_period(raw_text, reference_time or received_at, timezone_name)
    if period is not None:
        return replace(
            extraction_result,
            availability_start=period.availability_start,
            availability_end=period.availability_end,
        )

    missing = tuple(dict.fromkeys((*extraction_result.missing_fields, "availability_start", "availability_end")))
    return replace(extraction_result, missing_fields=missing)


def active_operational_profile(deps: "FlowDeps", scope=None):
    """The organization type that owns this work.

    Prefers the profile a request boundary already bound; otherwise resolves it
    from the deployment configuration and the event's own scope. Both routes are
    trusted configuration — neither reads the message.
    """

    bound = current_operational_profile()
    if bound is not None:
        return bound
    loaded = getattr(deps, "loaded_profile", None)
    if loaded is None:
        return None
    return profile_for_scope(loaded, scope)


def eligible_protocols_for_scope(deps: "FlowDeps", scope=None) -> tuple["Protocol", ...]:
    """Return the canonical protocols enabled for one trusted runtime scope.

    Operational-profile gating is explicit deployment configuration. Legacy
    single-purpose deployments retain their existing catalogue, while a gated
    deployment resolves LIVE from its configured profile and simulations from
    trusted fixture metadata.
    """

    declared = tuple(deps.protocol_set.all())
    loaded = getattr(deps, "loaded_profile", None)
    gating = getattr(loaded, "operational_profile_protocol_gating", False)
    if loaded is None or gating is not True:
        return declared

    profile = active_operational_profile(deps, scope)
    if profile is None:
        return ()

    return profile.protocol_catalogue(declared)


def eligible_protocol_for_scope(deps: "FlowDeps", protocol_name: str | None, scope=None):
    if not protocol_name:
        return None

    return next(
        (
            protocol
            for protocol in eligible_protocols_for_scope(deps, scope)
            if protocol.name == protocol_name
        ),
        None,
    )


def _trusted_correction_extraction(deps: FlowDeps, raw_text: str, received_at: str, reference_time: str | None):
    """Recognize a clearly expressed correction before any domain extractor.

    A correction is not a domain report — it is a statement about an earlier
    one — so it is classified here rather than inside the group owner, and it
    keeps its own type instead of being normalized into the owner's domain.
    The profile opts in by declaring the type; where it does not, this path is
    inert and the message takes the ordinary intake route.

    The cue must be explicit. An ordinary negative report ("there are no
    casualties") is a fact about the present, not a withdrawal of an earlier
    report, and must fall through rather than be guessed at.
    """

    is_valid = getattr(deps.event_type_registry, "is_valid", lambda value: True)
    if not is_valid(CORRECTION_REPORT_TYPE):
        return None

    kind = classify_correction(raw_text)
    if kind is None:
        return None

    from history import ExtractionResult

    text = str(raw_text or "")
    return ExtractionResult(
        CORRECTION_REPORT_TYPE,
        "trusted",
        None,
        (),
        text,
        "low",
        reference_time or received_at,
        False,
        (),
        business_fields={"correction_kind": kind},
    )


def _trusted_group_extraction(deps: FlowDeps, raw_text: str, received_at: str, reference_time: str | None, scope=None):
    correction = _trusted_correction_extraction(deps, raw_text, received_at, reference_time)
    if correction is not None:
        return correction

    owner_name = getattr(deps, "group_owner", None)
    if not owner_name:
        return None
    try:
        owner = deps.registry.get(owner_name)
    except KeyError:
        return None
    extractor = getattr(owner, "extract_report", None)
    if not callable(extractor):
        return None
    result = extractor(
        raw_text,
        received_at=received_at,
        scenario_time=reference_time,
        timezone_name=deps.timezone_name,
        scope=scope,
        profile=active_operational_profile(deps, scope),
    )
    if result is None:
        return None
    result = _apply_attendance_temporal_fields(result, raw_text, received_at, deps.timezone_name, reference_time)
    result = _normalize_group_owned_extraction(deps, result)
    if result.classification == UNCLASSIFIED_TYPE or result.area is None and result.classification == "team_attendance_report":
        return None
    if not result.description or not result.severity or not result.occurred_at:
        return None
    return result


def prepare_fast_path_report(
    deps: FlowDeps,
    main_agent: "MainAgent",
    raw_text: str,
    received_at: str,
    originated_from_commander: bool,
    reference_time: str | None = None,
    operational_scope=None,
) -> FastPathPlan | None:
    """Return a validated direct-execution plan, or leave the legacy flow untouched."""

    policy = deps.optimization_policy
    if policy.operational_intake_mode != "single" or policy.deterministic_execution_mode != "direct":
        return None

    trusted_extraction = _trusted_group_extraction(deps, raw_text, received_at, reference_time, operational_scope)
    if trusted_extraction is not None:
        if trusted_extraction.classification != "team_attendance_report":
            return FastPathPlan(trusted_extraction, domain_only=True)
        # Attendance reports are committed by the owning roster store.  The
        # event/hold/audit path remains the same; no action receipt is made.
        return FastPathPlan(trusted_extraction, domain_only=True)

    intake: OperationalIntake = make_operational_intake(
        main_agent,
        raw_text,
        received_at,
        tuple(deps.event_type_registry.types),
        tuple(deps.area_registry.areas),
        eligible_protocols_for_scope(deps, operational_scope),
        deps.settings_store.get_risk_threshold(),
        getattr(deps, "event_type_business_fields", None),
        trusted_report_types=_owned_report_types(deps),
    )
    if not intake.confident or intake.extraction is None:
        return None
    # A report explicitly asking for an action belongs to the action pipeline;
    # the deterministic report fast path must never turn an action into a
    # silent domain-ingestion success.
    if (
        intake.intent.intent != "report"
        or intake.intent.requests_action
    ):
        return None

    extraction = _apply_attendance_temporal_fields(
        intake.extraction,
        raw_text,
        received_at,
        deps.timezone_name,
        reference_time,
    )
    extraction = _normalize_group_owned_extraction(deps, extraction)
    if extraction.classification == UNCLASSIFIED_TYPE:
        return None
    if getattr(deps, "group_owner", None):
        return FastPathPlan(extraction, domain_only=True)
    if intake.decision is None or intake.decision.selection.status != "selected":
        return None
    if extraction.occurred_at is not None:
        try:
            parse_timestamp(extraction.occurred_at)
        except (TypeError, ValueError):
            return None
    protocol = eligible_protocol_for_scope(
        deps, intake.decision.selection.protocol_name, operational_scope
    )
    if protocol is None:
        return None
    if protocol.deterministic_required_event_fields is None or protocol.direct_tool_execution is None:
        return None

    hold_reason = determine_approval_hold(
        intake.decision.selection,
        {
            candidate.name: candidate
            for candidate in eligible_protocols_for_scope(deps, operational_scope)
        },
        originated_from_commander,
    )
    if hold_reason is not None:
        return None

    event_data = asdict(extraction)
    required_fields = tuple(dict.fromkeys((
        *deps.event_type_registry.required_fields_for(extraction.classification),
        *protocol.deterministic_required_event_fields,
    )))
    if any(
        event_data.get(field_name) is None or event_data.get(field_name) == ""
        for field_name in required_fields
    ):
        return None

    formulation = formulate_tasks(
        main_agent,
        protocol,
        deps.registry,
        raw_text,
        extraction.classification,
        extraction.area,
        extraction.description,
        event_data=event_data,
        required_fields_floor=deps.event_type_registry.required_fields_for(extraction.classification),
        allow_direct_execution=True,
    )
    if (
        not formulation.success
        or len(formulation.steps) != 1
        or formulation.steps[0].direct_tool_name is None
    ):
        return None

    return FastPathPlan(extraction, intake.decision, protocol)


def continue_fast_path_report(
    deps: FlowDeps,
    event_id: str,
    main_agent: "MainAgent",
    insights_agent: "InsightsAgent",
    plan: FastPathPlan,
) -> FlowResult:
    record_extracted_fields(deps.persistence, event_id, plan.extraction)
    if plan.domain_only:
        report_commit = _commit_report_domain_state(deps, event_id)
        return _complete_committed_report(deps, event_id, report_commit)
    return continue_from_risk_assessment(
        deps,
        event_id,
        main_agent,
        insights_agent,
        operational_decision=plan.decision,
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _log_event_outcome(event_id: str, outcome: str, **detail) -> None:
    """One place every terminal outcome (§1.8's "final verdict") is logged — closed on precedent, declined, failed, succeeded, or uncertain — so a run can be reassembled by querying it..."""

    logger.info(
        "event outcome",
        extra={"event": "event_outcome", "event_id": event_id, "outcome": outcome, "trace_id": get_trace_id(), **detail},
    )


def begin_report(
    deps: FlowDeps,
    raw_text: str,
    source: Literal["sensor", "telegram"],
    received_at: str,
    sender_identity: str,
    source_message_id: str | None = None,
    conversation_id: str | None = None,
    deadline_at: str | None = None,
    sender_permission_level: str = "viewer",
    simulation_context=None,
) -> str:
    """The synchronous prefix of a report: write the raw text and return the event ID, before any model call runs (§7.2's own requirement — "before any processing begins")."""

    event_id = record_initial_event(
        deps.persistence,
        InitialEventEnvelope(
            raw_text=raw_text, source=source, received_at=received_at, sender_identity=sender_identity,
            sender_permission_level=sender_permission_level,
            source_message_id=source_message_id,
            scenario_id=getattr(simulation_context, "scenario_id", None),
            scenario_run_id=getattr(simulation_context, "scenario_run_id", None),
            scenario_step=getattr(simulation_context, "scenario_step", None),
            scenario_time=getattr(simulation_context, "scenario_time", None),
            trace_id=get_trace_id() or None, conversation_id=conversation_id, deadline_at=deadline_at,
        ),
    )

    logger.info(
        "report received",
        extra={
            "event": "report_received", "event_id": event_id, "source": source,
            "sender_identity": sender_identity, "raw_text": raw_text, "trace_id": get_trace_id(),
        },
    )

    return event_id


def run_report_extraction(deps: FlowDeps, event_id: str, main_agent: "MainAgent", insights_agent: "InsightsAgent") -> FlowResult:
    """The rest of a report: extraction through outcome."""

    deadline_failure = _deadline_failure(deps, event_id, "extraction")
    if deadline_failure is not None:
        return deadline_failure

    event = deps.persistence.fetch_event(event_id)
    raw_text, source, received_at = event["raw_text"], event["source"], event["received_at"]

    try:
        extraction_result = extract_event(
            raw_text, source, received_at, deps.event_type_registry, deps.area_registry,
            model_invoker=_model_invoker_for(main_agent),
            event_type_business_fields=getattr(deps, "event_type_business_fields", None),
            normalize_declared_business_fields=bool(getattr(deps, "group_owner", None)),
        )
    except ExtractionExecutionError as exc:
        record_event_outcome(deps.persistence, event_id, "failed", failure_reason=str(exc))
        _log_event_outcome(event_id, "failed", failure_reason=str(exc), stage="extraction")
        return FlowResult(event_id, "failed", str(exc))

    extraction_result = _apply_attendance_temporal_fields(
        extraction_result, raw_text, received_at, deps.timezone_name,
        event.get("scenario_time"),
    )

    logger.info(
        "extraction result",
        extra={
            "event": "extraction_result",
            "event_id": event_id,
            "classification": extraction_result.classification,
            "area": extraction_result.area,
            "missing_fields": list(extraction_result.missing_fields),
            "occurred_at_is_fallback": extraction_result.occurred_at_is_fallback,
            "trace_id": get_trace_id(),
        },
    )

    extraction_result = _normalize_group_owned_extraction(deps, extraction_result)
    record_extracted_fields(deps.persistence, event_id, extraction_result)

    # A report that doesn't match any event type the active profile declares
    # resolves to the built-in UNCLASSIFIED_TYPE fallback rather than staying
    # `None` — a real, storable classification with its own (core-declared)
    # required fields, distinct from `HUMAN_ACTIVATION_TYPE` (a source label,
    # not an event type) (REQUIRED_FIELDS_AND_CLOSED_DECISIONS.md Part 1 /
    # item #6). `determine_clarification_hold` still keys off the *original*
    # extraction result, unchanged — this only affects what gets persisted.
    resolved_classification = extraction_result.classification or UNCLASSIFIED_TYPE
    if extraction_result.classification != resolved_classification:
        resolved_classification = extraction_result.classification or UNCLASSIFIED_TYPE

    # A trusted group owner may refine an otherwise unresolved/misclassified
    # report into its own declared domain.  Unscoped/private messages retain
    # the legacy clarification behavior.
    if determine_clarification_hold(extraction_result) and not getattr(deps, "group_owner", None):
        record_event_state(deps.persistence, event_id, {"classification": UNCLASSIFIED_TYPE})

    gate_result = _apply_required_fields_gate(deps, event_id, main_agent, resolved_classification)
    if gate_result is not None:
        return gate_result

    # A domain agent may own report ingestion (for example, surveillance
    # observations). Commit those validated facts before protocol selection so
    # a report can never be mistaken for a read-only query. Action reports,
    # such as attendance, continue to their declared protocol/tool path.
    if resolved_classification != UNCLASSIFIED_TYPE:
        report_commit = _commit_report_domain_state(deps, event_id)
        if report_commit.status != "not_applicable":
            return _complete_committed_report(deps, event_id, report_commit)

    return _continue_after_required_fields(deps, event_id, main_agent, insights_agent, raw_text, resolved_classification)


def _apply_required_fields_gate(
    deps: "FlowDeps", event_id: str, main_agent: "MainAgent", classification: str
) -> "FlowResult | None":
    """Item #6's early, event-type-level required-field gate — checked
    against whatever classification the event holds *right now*, before
    risk assessment or protocol selection run against it. Protocol selection
    itself reads whatever fields extraction produced, so a missing required
    field at this point could otherwise silently commit to the wrong
    protocol. Additional to, not a replacement for, the existing
    per-protocol-step required-field check in `protocols/executor.py`
    (`_execute_protocol_plan`), which still fires unchanged for a step that
    needs a field of its own, and the formulation-time floor merged into
    every step's own `required_event_fields` (`orchestrator.reasoning.
    formulate_tasks`). Returns None when there is nothing to wait for (no
    required fields declared, or every required field is already present).

    Called from two places, not just one: `run_report_extraction` (the
    fresh path, right after extraction resolves an event type — profile-
    defined or the built-in UNCLASSIFIED_TYPE fallback), and
    `continue_after_clarification` (the resumed path, right after a
    clarification hold resolves a *different* classification than the
    fixed UNCLASSIFIED_TYPE floor already checked). The second call site
    exists because a classification chosen by clarification can carry its
    own required fields that were never checked for this event — the fresh
    path's single gate call only ever validated UNCLASSIFIED_TYPE's fixed
    `("area",)` floor, not whatever the newly-chosen real type declares."""

    required = deps.event_type_registry.required_fields_for(classification)
    if not required:
        return None

    event = deps.persistence.fetch_event(event_id)
    missing = tuple(name for name in required if not event.get(name))
    if not missing:
        return None

    conversation_messages: tuple[dict, ...] = ()
    if event.get("conversation_id") and deps.conversation_history_turns > 0:
        conversation_messages = tuple(
            deps.persistence.fetch_conversation_messages(
                event["conversation_id"], deps.conversation_history_turns * 2, scope=scope_from_event(event)
            )
        )

    question = formulate_event_data_question(main_agent, event, missing, conversation_messages)

    if event.get("conversation_id") and deps.conversation_history_turns > 0:
        deps.persistence.append_conversation_message(
            event["conversation_id"], "assistant", question,
            ttl_hours=deps.conversation_history_ttl_hours, max_turns=deps.conversation_history_turns,
            event_id=event_id,
            scope=scope_from_event(event),
        )

    # waiting_step_ids=() — no protocol has been selected yet. This is what
    # lets resume_after_event_data tell this hold apart from a per-step one.
    create_event_data_hold(deps.persistence, event_id, missing, question, ())
    logger.info(
        "hold created",
        extra={
            "event": "hold_created", "hold_kind": "event_data", "event_id": event_id,
            "missing_fields": list(missing), "classification": classification, "trace_id": get_trace_id(),
        },
    )
    return FlowResult(event_id, "waiting_for_event_data", question)


def _continue_after_required_fields(
    deps: "FlowDeps", event_id: str, main_agent: "MainAgent", insights_agent: "InsightsAgent",
    raw_text: str, classification: str,
) -> "FlowResult":
    """What extraction would have done next, had the event type's required
    fields already been present — shared by the fresh path
    (run_report_extraction) and the resumed path (resume_after_event_data),
    so both stay in sync with exactly one copy of this branching logic."""

    if classification == UNCLASSIFIED_TYPE:
        create_clarification_hold(deps.persistence, event_id, raw_text)
        record_event_state(deps.persistence, event_id, {"clarification_held": True, "clarification_unresolved_field": UNRESOLVED_FIELD})
        logger.info(
            "hold created",
            extra={"event": "hold_created", "hold_kind": "clarification", "event_id": event_id, "unresolved_field": UNRESOLVED_FIELD, "trace_id": get_trace_id()},
        )
        return FlowResult(event_id, "held_for_clarification")

    return continue_from_risk_assessment(deps, event_id, main_agent, insights_agent)


_TEAM_ATTENDANCE_TYPES = frozenset({"team_attendance_report", "team_availability"})
_TEAM_RESOURCE_TYPES = frozenset({"team_resource_report"})


def _owned_report_types(deps: "FlowDeps") -> tuple[str, ...]:
    owner_name = getattr(deps, "group_owner", None)
    if not owner_name:
        return ()

    try:
        owner = deps.registry.get(owner_name)
    except KeyError:
        return ()

    return tuple(
        report_type
        for report_type in getattr(owner, "owned_report_types", ())
        if getattr(deps.event_type_registry, "is_valid", lambda value: True)(report_type)
    )


def _owner_report_classification(
    deps: "FlowDeps",
    classification: str,
    business_fields: dict[str, object] | None = None,
    classification_status: str | None = None,
) -> str:
    """Constrain report classification to trusted group ownership.

    Agents declare the event types they can ingest.  A classifier may refine a
    report within that set, but cannot move a group-owned report into another
    domain.  If the owner has no declared report type in the active profile,
    the original classification is retained so legacy/private behavior stays
    unchanged.
    """

    owner_name = getattr(deps, "group_owner", None)
    if not owner_name:
        return classification
    try:
        owner = deps.registry.get(owner_name)
    except KeyError:
        return classification
    owned_types = tuple(getattr(owner, "owned_report_types", ()))
    registry = deps.event_type_registry
    is_valid = getattr(registry, "is_valid", lambda value: True)

    fields = business_fields or {}
    if owner_name == "team_status_agent":
        attendance_is_typed = (
            classification_status == "trusted"
            and classification in _TEAM_ATTENDANCE_TYPES
            and fields.get("availability") in {"available", "unavailable"}
        )
        if attendance_is_typed:
            return "team_attendance_report" if is_valid("team_attendance_report") else classification

        resource_is_typed = (
            classification in _TEAM_RESOURCE_TYPES
            and classification_status == "trusted"
            and type(fields.get("manpower_count")) is int
        )
        if resource_is_typed:
            return classification

        if "team_operational_report" in owned_types and is_valid("team_operational_report"):
            return "team_operational_report"

        return UNCLASSIFIED_TYPE

    if classification in owned_types and is_valid(classification):
        return classification

    default_type = getattr(owner, "default_report_type", None)
    if isinstance(default_type, str) and default_type in owned_types and is_valid(default_type):
        return default_type
    return classification


def _normalize_group_owned_extraction(deps: "FlowDeps", extraction):
    owner_name = getattr(deps, "group_owner", None)
    if not owner_name:
        return extraction

    original_classification = extraction.classification or UNCLASSIFIED_TYPE
    classification = _owner_report_classification(
        deps,
        original_classification,
        dict(extraction.business_fields or {}),
        extraction.classification_status,
    )
    declarations = getattr(deps, "event_type_business_fields", None) or {}
    declared_fields = declarations.get(classification, {}) or {}
    source_fields = dict(extraction.business_fields or {})
    if (
        classification == "surveillance_report"
        and "status" in source_fields
        and "camera_status" not in source_fields
    ):
        source_fields["camera_status"] = source_fields.pop("status")

    normalized_fields = {}
    dropped_fields = []
    for field_name, value in source_fields.items():
        if field_name not in declared_fields:
            dropped_fields.append(field_name)
            continue
        if type(value) not in {str, int, float, bool}:
            if value is None:
                normalized_fields[field_name] = None
                continue
            dropped_fields.append(field_name)
            continue
        normalized_fields[field_name] = value

    if dropped_fields:
        logger.info(
            "group-owned report fields normalized",
            extra={
                "event": "group_report_fields_normalized",
                "owner": owner_name,
                "classification": classification,
                "dropped_fields": sorted(set(dropped_fields)),
                "trace_id": get_trace_id(),
            },
        )

    return replace(
        extraction,
        classification=classification,
        classification_status="trusted",
        business_fields=normalized_fields,
    )


def _correction_candidates(deps: "FlowDeps", event: dict, scope) -> list[dict]:
    """Committed reports from this correction's own operational scope."""

    criteria = EventSearchCriteria(
        outcomes=("succeeded",),
        # Retrieval only. Every committed event has a receipt time, while
        # `occurred_at` may be unset and would silently drop candidates. The
        # ordering that decides what a correction may retract is the
        # operational clock, and the resolver applies it.
        time_basis="received_at",
        scenario_id=scope.scenario_id if scope.is_simulation else None,
        scenario_run_id=scope.scenario_run_id if scope.is_simulation else None,
        order="newest",
        limit=CORRECTION_CANDIDATE_LIMIT,
    )
    try:
        candidates = deps.persistence.search_events(criteria)
    except Exception:
        logger.warning(
            "correction target lookup failed",
            extra={"event": "correction_lookup_failed", "event_id": event.get("event_id"), "trace_id": get_trace_id()},
        )
        return []

    if scope.is_simulation:
        return candidates

    # A LIVE correction must not reach into any simulation run.
    return [candidate for candidate in candidates if not candidate.get("scenario_run_id")]


def _commit_correction_report(deps: "FlowDeps", event: dict) -> ReportIngestionResult:
    """Commit a correction and, when it resolves to exactly one report, retract it.

    The correction is always committed as its own operational fact. Retraction
    is an additional, separate effect that happens only when the target is
    unambiguous — an unresolved or ambiguous correction records that and
    retracts nothing, because silently withdrawing the wrong report is worse
    than withdrawing none.
    """

    scope = scope_from_event(event)
    event_id = str(event.get("event_id") or "")

    already_retracted = event.get("supersedes_event_id")
    if already_retracted:
        # A replayed correction reports the link it already made. Re-resolving
        # could otherwise attach it to a different report, because its original
        # target is no longer an eligible candidate.
        return ReportIngestionResult(
            "committed",
            "correction committed",
            projection=project_report_facts(
                event,
                domain="correction",
                projection_kind="operational_fact",
                facts={
                    "correction_kind": str(event.get("supersession_kind") or RETRACTION),
                    "target_status": "resolved",
                    "superseded_event_id": str(already_retracted),
                },
            ),
        )

    resolution = resolve_superseded_event(event, _correction_candidates(deps, event, scope), scope=scope)

    facts: dict[str, object] = {
        "correction_kind": resolution.kind,
        "target_status": resolution.status,
    }

    if resolution.resolved:
        try:
            deps.persistence.record_supersession(
                superseded_event_id=str(resolution.target_event_id),
                superseding_event_id=event_id,
                kind=resolution.kind,
            )
        except Exception as exc:
            facts["target_status"] = "link_refused"
            logger.warning(
                "correction did not retract its target",
                extra={
                    "event": "correction_link_refused",
                    "event_id": event_id,
                    "reason": str(exc),
                    "trace_id": get_trace_id(),
                },
            )
        else:
            facts["superseded_event_id"] = str(resolution.target_event_id)
            logger.info(
                "report retracted by a correction",
                extra={
                    "event": "report_superseded",
                    "event_id": event_id,
                    "superseded_event_id": str(resolution.target_event_id),
                    "supersession_kind": resolution.kind,
                    "trace_id": get_trace_id(),
                },
            )
    elif resolution.candidate_event_ids:
        facts["ambiguous_candidate_count"] = len(resolution.candidate_event_ids)

    return ReportIngestionResult(
        "committed",
        "correction committed",
        projection=project_report_facts(
            event, domain="correction", projection_kind="operational_fact", facts=facts
        ),
    )


def _commit_report_domain_state(deps: "FlowDeps", event_id: str) -> ReportIngestionResult:
    """Commit a report through the owning domain agent, if one declares a hook.

    Event persistence is already the authoritative source for generic
    intelligence reports.  Domain agents may additionally project validated
    facts into their own store (for example, a surveillance camera report).
    This path is deliberately separate from protocol/tool execution, so a
    committed report never receives a synthetic action receipt.
    """

    event = deps.persistence.fetch_event(event_id) or {}

    if event.get("classification") == CORRECTION_REPORT_TYPE:
        return _commit_correction_report(deps, event)

    owner_name = getattr(deps, "group_owner", None)
    if owner_name:
        try:
            agents = (deps.registry.get(owner_name),)
        except KeyError:
            return ReportIngestionResult("failed", "group owner is not available for report ingestion")
    else:
        agents = deps.registry.all()

    for agent in agents:
        ingest_report = getattr(agent, "ingest_report", None)
        if ingest_report is None:
            continue
        scope = scope_from_event(event)
        try:
            parameters = inspect.signature(ingest_report).parameters.values()
        except (TypeError, ValueError):
            parameters = ()
        accepts_scope = any(
            parameter.name == "scope" or parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in parameters
        )
        # Domain ingestion resolves resource names through the active
        # organization's catalogue, so it needs the same trusted channel the
        # protocol path uses rather than a second way of learning its profile.
        with trusted_event_metadata({
            "operational_scope": scope,
            "operational_profile": active_operational_profile(deps, scope_from_event(event)),
        }):
            result = ingest_report(event, scope=scope) if accepts_scope else ingest_report(event)
        if result is None:
            return ReportIngestionResult("failed", "domain report ingestion returned no typed result")
        if result.status != "not_applicable":
            return result
    if owner_name:
        # ``not_applicable`` is an intentional outcome for owners such as the
        # team-status agent whose attendance reports continue through their
        # declared attendance protocol/tool.  The registry is already scoped,
        # so this cannot fall through to an unrelated domain.
        return ReportIngestionResult("not_applicable")
    return ReportIngestionResult("not_applicable")


def _complete_committed_report(deps: "FlowDeps", event_id: str, result: ReportIngestionResult) -> FlowResult:
    if result.committed:
        record_event_outcome(deps.persistence, event_id, "succeeded", insight_text=result.detail)
        _log_event_outcome(event_id, "succeeded", stage="report_ingestion", detail=result.detail)
        return FlowResult(event_id, "succeeded", result.detail)
    record_event_outcome(deps.persistence, event_id, "failed", failure_reason=result.detail)
    _log_event_outcome(event_id, "failed", failure_reason=result.detail, stage="report_ingestion")
    return FlowResult(event_id, "failed", result.detail)


def process_report(
    deps: FlowDeps,
    main_agent: "MainAgent",
    insights_agent: "InsightsAgent",
    raw_text: str,
    source: Literal["sensor", "telegram"],
    received_at: str,
    sender_identity: str,
) -> FlowResult:
    """A report of something that happened, run synchronously start to finish — `begin_report` + `run_report_extraction` composed back into one call."""

    event_id = begin_report(deps, raw_text, source, received_at, sender_identity)
    return run_report_extraction(deps, event_id, main_agent, insights_agent)


def begin_request(
    deps: FlowDeps,
    raw_text: str,
    received_at: str,
    sender_identity: str,
    source_message_id: str | None = None,
    conversation_id: str | None = None,
    deadline_at: str | None = None,
    sender_permission_level: str = "viewer",
    simulation_context=None,
) -> str:
    """The synchronous prefix of a request: write the raw text, already classified `human_activation` (§6.13 — there is nothing to extract), and return the event ID."""

    event_id = record_initial_event(
        deps.persistence,
        InitialEventEnvelope(
            raw_text=raw_text, source="telegram", received_at=received_at, sender_identity=sender_identity,
            sender_permission_level=sender_permission_level,
            source_message_id=source_message_id, occurred_at=received_at, occurred_at_is_fallback=False,
            scenario_id=getattr(simulation_context, "scenario_id", None),
            scenario_run_id=getattr(simulation_context, "scenario_run_id", None),
            scenario_step=getattr(simulation_context, "scenario_step", None),
            scenario_time=getattr(simulation_context, "scenario_time", None),
            trace_id=get_trace_id() or None, conversation_id=conversation_id, deadline_at=deadline_at,
        ),
    )
    record_event_state(deps.persistence, event_id, {"classification": HUMAN_ACTIVATION_TYPE})

    logger.info(
        "request received",
        extra={
            "event": "request_received", "event_id": event_id,
            "sender_identity": sender_identity, "raw_text": raw_text, "trace_id": get_trace_id(),
        },
    )

    return event_id


def process_request(
    deps: FlowDeps,
    main_agent: "MainAgent",
    insights_agent: "InsightsAgent",
    raw_text: str,
    received_at: str,
    sender_identity: str,
    originated_from_commander: bool,
) -> FlowResult:
    """A person's request for an action, run synchronously start to finish — `begin_request` + `continue_from_risk_assessment` composed back into one call."""

    event_id = begin_request(
        deps,
        raw_text,
        received_at,
        sender_identity,
        sender_permission_level="commander" if originated_from_commander else "viewer",
    )
    return continue_from_risk_assessment(deps, event_id, main_agent, insights_agent)


def process_message(
    deps: FlowDeps,
    main_agent: "MainAgent",
    insights_agent: "InsightsAgent",
    message_text: str,
    sender_identity: str,
    received_at: str,
    is_commander: bool,
) -> tuple[Literal["question", "report", "request", "conversational", "clarification"], object]:
    """Route a person's message by intent (§6.13)."""

    intent = classify_intent(
        main_agent,
        eligible_protocols_for_scope(deps),
        message_text,
    )

    if intent.intent == "needs_clarification":
        return "clarification", intent.clarification_question or "Could you clarify what you want me to do?"

    if intent.intent == "conversational":
        return "conversational", answer_conversationally(main_agent, message_text)

    if intent.intent == "question":
        return "question", answer_question(main_agent, message_text, deps.registry, deps.history_query_service)

    if intent.intent == "report":
        return "report", process_report(deps, main_agent, insights_agent, message_text, "telegram", received_at, sender_identity)

    if intent.intent == "request":
        return "request", process_request(deps, main_agent, insights_agent, message_text, received_at, sender_identity, is_commander)

    raise OrchestrationParseError(f"unsupported message intent: {intent.intent!r}")


def resolve_clarification(
    deps: FlowDeps,
    hold_id: str,
    answering_identity: str,
    answering_level: "PermissionLevel",
    chosen_classification: str,
) -> "HoldAnswerResult":
    """The synchronous prefix of answering a clarification hold: validate and record the answer, nothing more."""

    answer = answer_clarification_hold(deps.persistence, hold_id, answering_identity, answering_level, chosen_classification, deps.event_type_registry)
    # Only a resolved hold may resume orchestration side effects.
    if answer.status != "resolved":
        return answer  # unauthorized / not_found / invalid_classification — nothing to resume

    event_id = answer.hold["event_id"]
    record_event_state(
        deps.persistence, event_id,
        {"classification": chosen_classification, "clarification_resolved_by": answering_identity, "clarification_chosen_classification": chosen_classification},
    )

    logger.info(
        "clarification hold resolved",
        extra={
            "event": "hold_resolved", "hold_kind": "clarification", "event_id": event_id,
            "resolved_by": answering_identity, "chosen_classification": chosen_classification, "trace_id": get_trace_id(),
        },
    )

    return answer


def continue_after_clarification(deps: FlowDeps, event_id: str, main_agent: "MainAgent", insights_agent: "InsightsAgent") -> FlowResult:
    """Resume at risk assessment, not extraction — the other extracted fields are still valid and re-running extraction would discard the commander's decision (§6.2's own rule).

    First re-applies the required-fields gate for the classification the
    commander just chose. `resolve_clarification` only ever validated
    UNCLASSIFIED_TYPE's fixed `("area",)` floor before this hold existed —
    resolving into a real, profile-declared type can introduce required
    fields of its own that were never checked for this event. If any are
    missing, this creates the same kind of event_data hold the fresh-
    extraction path creates (`_apply_required_fields_gate`), asking
    immediately rather than letting the event proceed into risk assessment
    with a required field still unresolved."""

    event = deps.persistence.fetch_event(event_id)
    gate_result = _apply_required_fields_gate(deps, event_id, main_agent, event.get("classification"))
    if gate_result is not None:
        return gate_result

    return continue_from_risk_assessment(deps, event_id, main_agent, insights_agent)


def resume_after_clarification(
    deps: FlowDeps,
    main_agent: "MainAgent",
    insights_agent: "InsightsAgent",
    hold_id: str,
    answering_identity: str,
    answering_level: "PermissionLevel",
    chosen_classification: str,
):
    """Answer a clarification hold and resume, synchronously start to finish — `resolve_clarification` + `continue_after_clarification` composed back into one call."""

    answer = resolve_clarification(deps, hold_id, answering_identity, answering_level, chosen_classification)
    # Only a resolved hold may resume orchestration side effects.
    if answer.status != "resolved":
        return answer  # unauthorized / not_found / invalid_classification — nothing to resume

    return continue_after_clarification(deps, answer.hold["event_id"], main_agent, insights_agent)


def resolve_approval(
    deps: FlowDeps,
    hold_id: str,
    answering_identity: str,
    answering_level: "PermissionLevel",
    decision: Literal["approved", "rejected"] | str,
) -> "HoldAnswerResult":
    """The synchronous prefix of answering an approval hold: validate and record the answer, nothing more."""

    answer = answer_approval_hold(deps.persistence, hold_id, answering_identity, answering_level, decision)
    if answer.status not in ("approved", "rejected"):
        return answer  # unauthorized / not_found / invalid_candidate — nothing to resume

    event_id = answer.hold["event_id"]
    record_event_state(
        deps.persistence, event_id,
        {
            "approval_answered_by": answering_identity,
            "approval_answered_at": _now(),
            "selected_protocol": answer.hold["selected_protocol_name"],
        },
    )

    if answer.status == "approved":
        try:
            event = deps.persistence.fetch_event(event_id)
            protocol = eligible_protocol_for_scope(
                deps,
                answer.hold["selected_protocol_name"],
                scope_from_event(event),
            )
            if protocol is not None and protocol_has_side_effects(deps, protocol):
                _safe_action_transition(deps, event_id, "approved")
        except KeyError:
            pass

    logger.info(
        "approval hold resolved",
        extra={
            "event": "hold_resolved", "hold_kind": "approval", "event_id": event_id, "resolved_by": answering_identity,
            "decision": decision, "status": answer.status, "selected_protocol": answer.hold["selected_protocol_name"],
            "trace_id": get_trace_id(),
        },
    )

    return answer


def decline(deps: FlowDeps, event_id: str) -> FlowResult:
    """Record a rejected approval hold's outcome as declined — the synchronous, no-continuation-needed branch `resume_after_approval` and `api.operations`'s deny path (§7.11) both share."""

    record_event_outcome(deps.persistence, event_id, "declined")
    _log_event_outcome(event_id, "declined")
    return FlowResult(event_id, "declined")


def continue_after_approval(deps: FlowDeps, event_id: str, main_agent: "MainAgent", insights_agent: "InsightsAgent", selected_protocol_name: str) -> FlowResult:
    """Resume execution from task formulation through protocol execution — the approved branch only."""

    event = deps.persistence.fetch_event(event_id)
    protocol = eligible_protocol_for_scope(
        deps, selected_protocol_name, scope_from_event(event)
    )
    if protocol is None:
        reason = "the selected protocol is unavailable for the active operational profile"
        record_event_outcome(
            deps.persistence, event_id, "no_match_protocol", failure_reason=reason
        )
        _safe_action_transition(deps, event_id, "failed", failure_reason=reason)
        return FlowResult(event_id, "no_match_protocol", reason)

    precedent_matches = _look_up_precedent_if_possible(deps, event_id, event)

    return _run_protocol(deps, event_id, main_agent, insights_agent, protocol, precedent_matches, event["raw_text"], event["classification"], event["area"], event["description"])


def resume_after_approval(
    deps: FlowDeps,
    main_agent: "MainAgent",
    insights_agent: "InsightsAgent",
    hold_id: str,
    answering_identity: str,
    answering_level: "PermissionLevel",
    decision: Literal["approved", "rejected"] | str,
):
    """Answer an approval hold and resume, synchronously start to finish — `resolve_approval` + (on approval only) `continue_after_approval` composed back into one call."""

    answer = resolve_approval(deps, hold_id, answering_identity, answering_level, decision)
    if answer.status not in ("approved", "rejected"):
        return answer  # unauthorized / not_found — nothing to resume

    event_id = answer.hold["event_id"]

    if answer.status == "rejected":
        return decline(deps, event_id)

    return continue_after_approval(deps, event_id, main_agent, insights_agent, answer.hold["selected_protocol_name"])


def _look_up_precedent_if_possible(deps: FlowDeps, event_id: str, event: dict) -> tuple:
    # Precedent-lookback fix (same root cause as the recency fix,
    # DIAGNOSTIC_FINDINGS.MD A.2): an unresolved occurred_at used to skip
    # precedent lookup for this event entirely. Fall back to received_at as
    # the lookback window's anchor instead — occurred_at is no longer
    # required for this event to be checked against precedent.
    if event["classification"] is None or event["area"] is None:
        return ()
    anchor_time = event["occurred_at"] or event["received_at"]
    return look_up_precedent(deps.history_query_service, event_id, event["classification"], event["area"], anchor_time)


def protocol_has_side_effects(deps: FlowDeps, protocol: "Protocol") -> bool:
    """Read side-effect capability only from registered tool metadata."""

    for agent_name in protocol.participating_agents:
        exposed = {tool.name: tool for tool in deps.registry.descriptor_for(agent_name).tools}
        if any(exposed.get(tool_name) is not None and exposed[tool_name].side_effecting for tool_name in protocol.approved_tools):
            return True
    return False


def action_protocols_for_request(deps: FlowDeps, scope=None) -> tuple["Protocol", ...]:
    """The only protocols an action request may be resolved to.

    The classification comes from the protocol's approved tools and their
    runtime metadata; protocol names and user wording never define it.
    """

    return tuple(
        protocol
        for protocol in eligible_protocols_for_scope(deps, scope)
        if protocol_has_side_effects(deps, protocol)
    )


def enforce_action_routing_guard(deps: FlowDeps, intent: IntentResult, scope=None) -> IntentResult:
    """Keep a typed action signal on the action path before response routing.

    A structured intent response may carry a true action flag while naming a
    non-action primary intent. The flag is grounded in an exact user quote by
    the existing intent contract, so the safe route is an action event; later
    protocol resolution still decides whether there is a supported action.
    """

    if intent.intent == "request" or not intent.requests_action:
        return intent

    candidate_names = set(intent.matched_protocol_names)
    side_effect_candidates = {
        protocol.name
        for protocol in action_protocols_for_request(deps, scope)
    }
    logger.warning(
        "action intent corrected before response routing",
        extra={
            "event": "action_routing_guard_applied",
            "primary_intent": intent.intent,
            "matched_protocol_names": sorted(candidate_names),
            "matched_side_effect_protocol_names": sorted(candidate_names & side_effect_candidates),
            "trace_id": get_trace_id(),
        },
    )
    return replace(intent, intent="request")


def _safe_action_transition(deps: FlowDeps, event_id: str, state: str, *, failure_reason: str | None = None, receipts=()) -> None:
    current = (deps.persistence.fetch_event(event_id) or {}).get("action_state")
    if current == state and not receipts and failure_reason is None:
        return
    if current in {"executed", "failed"} and current != state:
        return
    try:
        record_action_lifecycle(deps.persistence, event_id, state, failure_reason=failure_reason, receipts=tuple(receipts))
    except ValueError:
        logger.warning(
            "action lifecycle transition ignored",
            extra={"event": "action_lifecycle_transition_invalid", "event_id": event_id, "from_state": current, "to_state": state, "trace_id": get_trace_id()},
        )


def continue_from_risk_assessment(
    deps: FlowDeps,
    event_id: str,
    main_agent: "MainAgent",
    insights_agent: "InsightsAgent",
    originated_from_commander: bool | None = None,
    selected_protocol: "Protocol | None" = None,
    operational_decision: OperationalDecision | None = None,
) -> FlowResult:
    deadline_failure = _deadline_failure(deps, event_id, "risk_assessment")
    if deadline_failure is not None:
        return deadline_failure
    event = deps.persistence.fetch_event(event_id)
    event_scope = scope_from_event(event)
    if originated_from_commander is None:
        # Authorization belongs to the original authenticated submitter.  It
        # is persisted with the event so delayed extraction and resumptions do
        # not accidentally inherit the role of a hold/event-data answerer.
        originated_from_commander = event.get("sender_permission_level") == "commander"
    raw_text, classification, area = event["raw_text"], event["classification"], event["area"]
    description, severity = event["description"], event["severity"]
    requires_side_effecting_protocol = classification == HUMAN_ACTIVATION_TYPE
    selectable_protocols = (
        action_protocols_for_request(deps, event_scope)
        if requires_side_effecting_protocol
        else eligible_protocols_for_scope(deps, event_scope)
    )
    protocols_by_name = {
        protocol.name: protocol
        for protocol in eligible_protocols_for_scope(deps, event_scope)
    }

    if selected_protocol is not None:
        risk_level = "high" if selected_protocol.criticality == CriticalityLevel.HIGH else "low"
        risk_assessment = RiskAssessment(
            level=risk_level,
            score=0.8 if risk_level == "high" else 0.2,
            reason="Deterministic button protocol selection",
        )
        record_event_state(deps.persistence, event_id, {"risk_level": risk_assessment.level, "risk_reason": risk_assessment.reason})
        selection = ProtocolSelectionResult(
            status="selected",
            protocol_name=selected_protocol.name,
            candidate_names=(selected_protocol.name,),
            reason="Deterministic button protocol mapping",
        )
    elif operational_decision is not None:
        risk_assessment = operational_decision.risk
        selection = operational_decision.selection
        record_event_state(
            deps.persistence,
            event_id,
            {"risk_level": risk_assessment.level, "risk_reason": risk_assessment.reason},
        )
    else:
        operational_mode = deps.optimization_policy.operational_decision_mode
        combined_decision = None
        if operational_mode in {"shadow", "merged"}:
            try:
                combined_decision = make_operational_decision(
                    main_agent, raw_text, classification, area, description, severity,
                    selectable_protocols, deps.settings_store.get_risk_threshold(),
                )
            except OrchestrationParseError as exc:
                logger.warning(
                    "combined operational decision failed validation",
                    extra={"event": "operational_decision_invalid", "mode": operational_mode, "reason": str(exc), "trace_id": get_trace_id()},
                )
                if operational_mode == "merged":
                    record_event_outcome(deps.persistence, event_id, "failed", failure_reason=str(exc))
                    return FlowResult(event_id, "failed", str(exc))

        try:
            risk_assessment = (
                combined_decision.risk
                if operational_mode == "merged" and combined_decision is not None
                else assess_risk(
                    main_agent, classification, area, description, severity,
                    deps.settings_store.get_risk_threshold(),
                )
            )
        except OrchestrationParseError as exc:
            record_event_outcome(deps.persistence, event_id, "failed", failure_reason=str(exc))
            _log_event_outcome(event_id, "failed", failure_reason=str(exc), stage="risk_assessment")
            return FlowResult(event_id, "failed", str(exc))
        record_event_state(deps.persistence, event_id, {"risk_level": risk_assessment.level, "risk_reason": risk_assessment.reason})
        logger.info(
            "risk assessed",
            extra={
                "event": "risk_assessed", "event_id": event_id, "risk_level": risk_assessment.level,
                "risk_score": risk_assessment.score, "risk_reason": risk_assessment.reason, "trace_id": get_trace_id(),
            },
        )

        deadline_failure = _deadline_failure(deps, event_id, "protocol_selection")
        if deadline_failure is not None:
            return deadline_failure
        try:
            selection = (
                combined_decision.selection
                if operational_mode == "merged" and combined_decision is not None
                else select_protocol(main_agent, raw_text, classification, area, description, selectable_protocols, risk_assessment.level)
            )
        except OrchestrationParseError as exc:
            record_event_outcome(deps.persistence, event_id, "failed", failure_reason=str(exc))
            _log_event_outcome(event_id, "failed", failure_reason=str(exc), stage="protocol_selection")
            return FlowResult(event_id, "failed", str(exc))

    if selection.status == "selected":
        chosen_protocol = protocols_by_name.get(selection.protocol_name)
        if chosen_protocol is None:
            selection = ProtocolSelectionResult(
                status="no_match",
                reason="protocol unavailable for the active operational profile",
            )
        elif (
            requires_side_effecting_protocol
            and not protocol_has_side_effects(deps, chosen_protocol)
        ):
            selection = ProtocolSelectionResult(
                status="no_match",
                reason="action requests require a side-effecting protocol",
            )
        else:
            record_event_state(
                deps.persistence,
                event_id,
                {"selected_protocol": selection.protocol_name, "protocol_reason": selection.reason},
            )

    logger.info(
        "protocol selection",
        extra={
            "event": "protocol_selection", "event_id": event_id, "status": selection.status,
            "protocol_name": selection.protocol_name, "candidate_names": list(selection.candidate_names),
            "reason": selection.reason, "trace_id": get_trace_id(),
        },
    )

    precedent_matches = _look_up_precedent_if_possible(deps, event_id, event)
    if precedent_matches:
        record_event_state(
            deps.persistence,
            event_id,
            {"precedent_matched_event_ids": [precedent_match.event_id for precedent_match in precedent_matches]},
        )

    selected_candidate = protocols_by_name.get(selection.protocol_name) if selection.status == "selected" else None
    direct_capability = selected_candidate.direct_tool_execution if selected_candidate is not None else None
    precedent_closure_blocked = (
        selection.status == "selected" and selection.protocol_name == "record_attendance_response"
    )
    if direct_capability is not None and len(selected_candidate.participating_agents) == 1:
        descriptor = deps.registry.descriptor_for(selected_candidate.participating_agents[0])
        precedent_closure_blocked = any(
            tool.name == direct_capability.tool_name and tool.side_effecting
            for tool in descriptor.tools
        )
    closing_event_id = (
        None
        if precedent_closure_blocked
        else determine_closure(risk_assessment.level, classification, precedent_matches)
    )
    logger.info(
        "precedent closure decision",
        extra={
            "event": "precedent_closure", "event_id": event_id,
            "matched_event_ids": [precedent_match.event_id for precedent_match in precedent_matches],
            "closed": closing_event_id is not None, "closing_event_id": closing_event_id, "trace_id": get_trace_id(),
        },
    )
    if closing_event_id is not None:
        record_event_state(deps.persistence, event_id, {"precedent_closed_by_event_id": closing_event_id})
        record_event_outcome(deps.persistence, event_id, "closed_on_precedent")
        _log_event_outcome(event_id, "closed_on_precedent", precedent_event_id=closing_event_id)
        return FlowResult(event_id, "closed_on_precedent", f"closed against resolved precedent '{closing_event_id}'")

    # A valid report without an action protocol is still a committed domain
    # fact.  Only action requests use no-match as a terminal protocol failure.
    if selection.status == "no_match":
        if classification != UNCLASSIFIED_TYPE and not requires_side_effecting_protocol:
            report_commit = _commit_report_domain_state(deps, event_id)
            if report_commit.status != "not_applicable":
                return _complete_committed_report(deps, event_id, report_commit)
            report_commit = ReportIngestionResult(True, "event report committed")
            return _complete_committed_report(deps, event_id, report_commit)
        record_event_outcome(deps.persistence, event_id, "no_match_protocol", failure_reason=selection.reason)
        _log_event_outcome(event_id, "no_match_protocol", reason=selection.reason)
        return FlowResult(event_id, "no_match_protocol", selection.reason)

    hold_reason: "HoldReason | None" = determine_approval_hold(selection, protocols_by_name, originated_from_commander)

    protocol = protocols_by_name.get(selection.protocol_name) if selection.status == "selected" else None
    side_effecting_protocol = bool(protocol and protocol_has_side_effects(deps, protocol))
    if side_effecting_protocol and (deps.persistence.fetch_event(event_id) or {}).get("action_state") is None:
        _safe_action_transition(deps, event_id, "requested")

    if hold_reason is not None:
        create_approval_hold(deps.persistence, event_id, hold_reason, selection, risk_assessment)
        record_event_state(deps.persistence, event_id, {"approval_held": True, "approval_reason": hold_reason})
        if side_effecting_protocol:
            _safe_action_transition(deps, event_id, "pending_approval")
        logger.info(
            "hold created",
            extra={"event": "hold_created", "hold_kind": "approval", "event_id": event_id, "reason": hold_reason, "trace_id": get_trace_id()},
        )
        return FlowResult(event_id, "held_for_approval", hold_reason)

    if side_effecting_protocol:
        _safe_action_transition(deps, event_id, "approved")
    if protocol is None:
        return FlowResult(event_id, "failed", "selected protocol is missing")
    return _run_protocol(deps, event_id, main_agent, insights_agent, protocol, precedent_matches, raw_text, classification, area, description)


def _run_protocol(
    deps: FlowDeps,
    event_id: str,
    main_agent: "MainAgent",
    insights_agent: "InsightsAgent",
    protocol: "Protocol",
    precedent_matches: tuple,
    raw_text: str,
    classification: str | None,
    area: str | None,
    description: str | None,
) -> FlowResult:
    deadline_failure = _deadline_failure(deps, event_id, "formulation")
    if deadline_failure is not None:
        return deadline_failure
    formulation = formulate_tasks(
        main_agent, protocol, deps.registry, raw_text, classification, area, description,
        precedent_context=precedent_matches, event_data=deps.persistence.fetch_event(event_id),
        # The event type's statically-declared required fields (item #6's
        # EVENT_TYPE_REQUIRED_FIELDS), unioned into every formulated step's own
        # required_event_fields regardless of what the model declares — see
        # formulate_tasks' docstring.
        required_fields_floor=deps.event_type_registry.required_fields_for(classification),
        allow_direct_execution=deps.optimization_policy.deterministic_execution_mode == "direct",
    )
    if not formulation.success:
        record_event_outcome(deps.persistence, event_id, "failed", failure_reason=formulation.failure_reason)
        _log_event_outcome(event_id, "failed", failure_reason=formulation.failure_reason, stage="formulation")
        return FlowResult(event_id, "failed", formulation.failure_reason or "")
    return _execute_protocol_plan(
        deps, event_id, main_agent, insights_agent, protocol, formulation.steps, precedent_matches,
    )


def _persist_step_plan(deps: FlowDeps, event_id: str, steps: tuple[Step, ...]) -> None:
    for index, step in enumerate(steps):
        record_step_execution(
            deps.persistence,
            event_id,
            StepExecutionEnvelope(
                step_index=index,
                agent_name=step.agent_name,
                task_text=step.task_text,
                allowed_tools=list(step.allowed_tools),
                result_text=None,
                attempt_count=0,
                step_id=step.step_id,
                depends_on=step.depends_on,
                required_event_fields=step.required_event_fields,
                status="pending",
                direct_tool_name=step.direct_tool_name,
                direct_tool_arguments=step.direct_tool_arguments,
            ),
        )


def _step_from_row(row: dict) -> Step:
    return Step(
        agent_name=row["agent_name"],
        task_text=row["task_text"],
        allowed_tools=tuple(row.get("allowed_tools") or ()),
        step_id=row.get("step_id") or str(row["step_index"]),
        depends_on=tuple(row.get("depends_on") or ()),
        required_event_fields=tuple(row.get("required_event_fields") or ()),
        direct_tool_name=row.get("direct_tool_name"),
        direct_tool_arguments=row.get("direct_tool_arguments"),
    )


def _prior_outcomes(rows: list[dict], steps: tuple[Step, ...]) -> tuple[StepOutcome, ...]:
    by_index = {row["step_index"]: row for row in rows}
    outcomes: list[StepOutcome] = []
    for index, step in enumerate(steps):
        row = by_index.get(index, {})
        outcomes.append(
            StepOutcome(
                step=step,
                result_text=row.get("result_text"),
                attempt_count=row.get("attempt_count", 0),
                succeeded=row.get("status") == "succeeded",
                failure_reason=row.get("failure_reason"),
                status=row.get("status", "pending"),
                missing_event_fields=tuple(row.get("missing_event_fields") or ()),
                action_state=row.get("action_state"),
                tool_receipts=tuple(
                    ToolReceipt(**receipt) if isinstance(receipt, dict) else receipt
                    for receipt in (row.get("tool_receipts") or ())
                ),
            )
        )
    return tuple(outcomes)


def _persist_step_outcomes(
    deps: FlowDeps,
    event_id: str,
    steps: tuple[Step, ...],
    outcomes: tuple[StepOutcome, ...],
) -> None:
    """Match each outcome back to the step it belongs to, by `step_id` where
    one exists. `outcome.step` is not necessarily `is`-identical to its
    entry in `steps` — this function receives the *original* `steps`, but
    `_execute_protocol_plan` runs a derived copy where any step with a
    non-empty `required_event_fields` has its `task_text` rewritten first
    (the "Current validated event data JSON" injection, above) — so an
    outcome's step can differ from the original by value once that
    injection applies.

    A previous version of this function fell back to matching by full
    value-equality (`step == outcome.step`) whenever a step's `step_id` was
    empty, on the unstated assumption that a step_id-less step's fields
    never get mutated after formulation — true only by accident, and it
    broke the moment a step_id-less step also had a non-empty
    `required_event_fields`.

    Every step's `step_id` is empty only on one production path today: the
    legacy AGENT:/TASK: fallback in `orchestrator.reasoning.formulate_tasks`
    (the JSON formulation path always assigns each step a unique, non-empty
    id, and reloading a persisted plan via `_step_from_row` always
    substitutes `str(step_index)` for an absent one) — and a legacy-parsed
    plan, having no step_id or depends_on on any step, can only ever run
    through `protocols.executor.execute_steps`' plain sequential branch
    (never the dependency-graph one), which is guaranteed to produce
    `outcomes` in exactly the same order and position as `steps`, truncated
    at most (a blocked or failed run stops partway through) but never
    reordered or skipped over. So when `step_id` is empty, this outcome's
    own position *is* its step's position — no value comparison needed, and
    nothing about it changes if the step was mutated in the meantime."""

    index_by_step_id = {step.step_id: index for index, step in enumerate(steps) if step.step_id}
    for position, outcome in enumerate(outcomes):
        step_key = outcome.step.step_id
        index = index_by_step_id[step_key] if step_key else position
        persisted_step = steps[index]
        record_step_execution(
            deps.persistence,
            event_id,
            StepExecutionEnvelope(
                step_index=index,
                agent_name=persisted_step.agent_name,
                task_text=persisted_step.task_text,
                allowed_tools=list(persisted_step.allowed_tools),
                result_text=outcome.result_text,
                attempt_count=outcome.attempt_count,
                step_id=persisted_step.step_id,
                depends_on=persisted_step.depends_on,
                required_event_fields=persisted_step.required_event_fields,
                missing_event_fields=outcome.missing_event_fields,
                status=outcome.status,
                failure_reason=outcome.failure_reason,
                direct_tool_name=persisted_step.direct_tool_name,
                direct_tool_arguments=persisted_step.direct_tool_arguments,
                action_state=outcome.action_state,
                tool_receipts=outcome.tool_receipts,
            ),
        )


def _execute_protocol_plan(
    deps: FlowDeps,
    event_id: str,
    main_agent: "MainAgent",
    insights_agent: "InsightsAgent",
    protocol: "Protocol",
    steps: tuple[Step, ...],
    precedent_matches: tuple,
    *,
    resumed: bool = False,
) -> FlowResult:
    event = deps.persistence.fetch_event(event_id)
    if not resumed:
        deadline_failure = _deadline_failure(deps, event_id, "execution")
        if deadline_failure is not None:
            return deadline_failure

    agents_by_name = {name: deps.registry.get(name) for name in protocol.participating_agents}
    persisted_rows = event.get("steps", [])
    prior = _prior_outcomes(persisted_rows, steps)
    execution_steps = tuple(
        replace(
            step,
            task_text=(
                f"{step.task_text}\n\nCurrent validated event data JSON (use this as the source of truth): "
                f"{json.dumps({name: event.get(name) for name in step.required_event_fields}, ensure_ascii=False, sort_keys=True)}"
            ),
        )
        if step.required_event_fields
        else step
        for step in steps
    )

    side_effecting_protocol = protocol_has_side_effects(deps, protocol)

    def _lifecycle_callback(state: str, _step: Step) -> None:
        if side_effecting_protocol:
            _safe_action_transition(deps, event_id, state)

    with authenticated_request_identity(event["sender_identity"]), trusted_event_metadata(
        {
            "source_message_id": event.get("source_message_id"),
            "original_text": event.get("raw_text"),
            "received_at": event.get("received_at"),
            "reported_at": operational_timestamp_of_event(event, scope=scope_from_event(event)),
            "availability_start": event.get("availability_start"),
            "availability_end": event.get("availability_end"),
            "operational_scope": scope_from_event(event),
            "operational_profile": active_operational_profile(deps, scope_from_event(event)),
        }
    ):
        run_result = execute_steps(
            list(execution_steps),
            agents_by_name,
            deps.settings_store,
            task_rewriter=functools.partial(rewrite_task, main_agent),
            event_data=event,
            prior_outcomes=prior,
            event_id=event_id,
            lifecycle_callback=_lifecycle_callback,
        )
    if run_result.waiting_for_event_data and not persisted_rows:
        _persist_step_plan(deps, event_id, steps)
    _persist_step_outcomes(deps, event_id, steps, run_result.step_outcomes)

    if run_result.waiting_for_event_data:
        latest_event = deps.persistence.fetch_event(event_id)
        conversation_messages: tuple[dict, ...] = ()
        if latest_event.get("conversation_id"):
            conversation_messages = tuple(
                deps.persistence.fetch_conversation_messages(
                    latest_event["conversation_id"], 12, scope=scope_from_event(latest_event)
                )
            )
        question = formulate_event_data_question(
            main_agent, latest_event, run_result.missing_event_fields, conversation_messages
        )
        waiting_step_ids = tuple(
            outcome.step.step_id for outcome in run_result.step_outcomes
            if outcome.status == "waiting_for_event_data"
        )
        if latest_event.get("conversation_id") and deps.conversation_history_turns > 0:
            deps.persistence.append_conversation_message(
                latest_event["conversation_id"],
                "assistant",
                question,
                ttl_hours=deps.conversation_history_ttl_hours,
                max_turns=deps.conversation_history_turns,
                event_id=event_id,
                scope=scope_from_event(latest_event),
            )
        create_event_data_hold(
            deps.persistence, event_id, run_result.missing_event_fields, question, waiting_step_ids
        )
        logger.info(
            "protocol waiting for event data",
            extra={
                "event": "protocol_waiting_for_event_data",
                "event_id": event_id,
                "missing_event_fields": list(run_result.missing_event_fields),
                "trace_id": get_trace_id(),
            },
        )
        return FlowResult(event_id, "waiting_for_event_data", question)

    if not run_result.completed:
        if side_effecting_protocol:
            _safe_action_transition(deps, event_id, "failed", failure_reason=run_result.failure_cause)
        record_event_outcome(deps.persistence, event_id, "failed", failure_reason=run_result.failure_cause)
        _log_event_outcome(
            event_id, "failed", failure_reason=run_result.failure_cause, stage="execution",
            failed_step_agent=run_result.failed_step_agent,
        )
        return FlowResult(event_id, "failed", run_result.failure_cause or "")

    recall_selection = next(
        (
            outcome.result_text
            for outcome in run_result.step_outcomes
            if outcome.result_text and outcome.result_text.startswith("DRONE_SELECTION_REQUIRED:")
        ),
        None,
    )
    if protocol.name in {"return_drone_to_base", "recall_drone_to_base"} and recall_selection is not None:
        create_event_data_hold(
            deps.persistence,
            event_id,
            ("drone_selection",),
            recall_selection,
            tuple(outcome.step.step_id for outcome in run_result.step_outcomes),
        )
        return FlowResult(event_id, "waiting_for_drone_selection", recall_selection)

    if side_effecting_protocol:
        receipts = tuple(receipt for outcome in run_result.step_outcomes for receipt in outcome.tool_receipts)
        if receipts:
            _safe_action_transition(deps, event_id, "executed", receipts=receipts)

    return _finish_protocol_assessment(
        deps, event_id, main_agent, insights_agent, protocol, run_result.step_outcomes, precedent_matches,
        enforce_deadline=not resumed,
    )


def _finish_protocol_assessment(
    deps: FlowDeps,
    event_id: str,
    main_agent: "MainAgent",
    insights_agent: "InsightsAgent",
    protocol: "Protocol",
    step_outcomes: tuple[StepOutcome, ...],
    precedent_matches: tuple,
    *,
    enforce_deadline: bool,
) -> FlowResult:
    if enforce_deadline:
        deadline_failure = _deadline_failure(deps, event_id, "final_assessment")
        if deadline_failure is not None:
            return deadline_failure
    final_assessment = None
    persisted_event = deps.persistence.fetch_event(event_id)
    if deps.optimization_policy.final_assessment_mode == "low_risk_merged" and persisted_event.get("risk_level") == "low":
        try:
            final_assessment = assess_final_once(main_agent, protocol, step_outcomes, precedent_matches)
        except OrchestrationParseError as exc:
            logger.warning(
                "merged final assessment failed; using separate verifiers",
                extra={"event": "final_assessment_invalid", "reason": str(exc), "trace_id": get_trace_id()},
            )

    typed_snapshot = None
    if len(protocol.participating_agents) > 1:
        sender_filter = (
            None
            if persisted_event.get("sender_permission_level") == "commander"
            else persisted_event.get("sender_identity")
        )
        typed_snapshot = build_typed_snapshot(
            deps.registry,
            history_query_service=deps.history_query_service,
            sender_identity_filter=sender_filter,
            scenario_id=persisted_event.get("scenario_id"),
            scenario_run_id=persisted_event.get("scenario_run_id"),
            operational_scope=scope_from_event(persisted_event),
        )

    if typed_snapshot is not None:
        insight_text = render_typed_snapshot(typed_snapshot)
    else:
        insight_text = (
            final_assessment.insight
            if final_assessment is not None
            else build_insight(insights_agent, protocol, step_outcomes, comparable_history=precedent_matches)
        )
    if len(protocol.participating_agents) > 1:
        # A multi-domain protocol uses the typed snapshot whenever authoritative
        # stores are available. Legacy profiles without those stores retain their
        # existing specialist composition path.
        try:
            if typed_snapshot is None:
                synthesis = compose_picture_from_step_outcomes(
                    main_agent,
                    protocol,
                    step_outcomes,
                    persisted_event.get("raw_text", ""),
                    deps.history_query_service,
                    sender_identity_filter=sender_filter,
                    scenario_id=persisted_event.get("scenario_id"),
                    scenario_run_id=persisted_event.get("scenario_run_id"),
                )
                if synthesis:
                    insight_text = synthesis
        except Exception as exc:
            logger.warning(
                "multi-agent synthesis failed: %s", exc, extra={"event": "synthesis_failed", "trace_id": get_trace_id()}
            )
    logger.info(
        "insight generated",
        extra={
            "event": "insight_generated", "event_id": event_id, "protocol": protocol.name,
            "insight_text": insight_text, "trace_id": get_trace_id(),
        },
    )

    if enforce_deadline:
        deadline_failure = _deadline_failure(deps, event_id, "judgment")
        if deadline_failure is not None:
            return deadline_failure
    if final_assessment is not None:
        verdict = final_assessment.verdict
    else:
        try:
            verdict = judge_success(main_agent, protocol, step_outcomes, insight_text=insight_text)
        except OrchestrationParseError:
            try:
                verdict = judge_success(main_agent, protocol, step_outcomes, insight_text=insight_text)
            except OrchestrationParseError as exc:
                record_event_outcome(
                    deps.persistence, event_id, "failed",
                    failure_reason=f"success judgment failed: {exc}", insight_text=insight_text,
                )
                _log_event_outcome(event_id, "failed", failure_reason=str(exc), stage="judgment")
                return FlowResult(event_id, "failed", str(exc))

    outcome = _VERDICT_TO_OUTCOME[verdict.verdict]
    record_event_outcome(deps.persistence, event_id, outcome, insight_text=insight_text)
    logger.info(
        "final verdict",
        extra={
            "event": "final_verdict", "event_id": event_id, "verdict": verdict.verdict,
            "reasoning": verdict.reasoning, "trace_id": get_trace_id(),
        },
    )
    _log_event_outcome(event_id, outcome, reasoning=verdict.reasoning)
    return FlowResult(event_id, outcome, verdict.reasoning)


def apply_event_data_reply(
    deps: FlowDeps,
    main_agent: "MainAgent",
    reply_text: str,
    sender_identity: str,
    conversation_id: str | None,
    conversation_messages: tuple[dict, ...] = (),
    target_event_id: str | None = None,
) -> EventDataReplyResult | None:
    """Apply a conversational answer to the newest matching reporter-facing data request."""

    if not conversation_id:
        return None
    candidates: list[tuple[dict, dict]] = []
    for hold in deps.persistence.list_held_events("event_data"):
        if target_event_id is not None and hold["event_id"] != target_event_id:
            continue
        event = deps.persistence.fetch_event(hold["event_id"])
        if (
            event is not None
            and event.get("conversation_id") == conversation_id
            and event.get("sender_identity") == sender_identity
        ):
            candidates.append((hold, event))
    if not candidates:
        return None
    if len(candidates) > 1:
        # More than one report is waiting on this same sender/conversation for
        # missing details. Previously this silently applied the reply to the
        # single most-recently-created one, which could misapply a correction
        # meant for an earlier report (CRITICAL_FIXES_PLAN.MD item 7). Fail
        # loudly instead of guessing — the caller (api/routes.py) turns this
        # into a clarification response rather than a model-parsed update, so
        # no extraction call is made against an ambiguous target.
        return EventDataReplyResult(
            event_id="",
            updates={},
            message="",
            ambiguous_event_ids=tuple(event["event_id"] for _hold, event in candidates),
        )

    hold, event = candidates[-1]
    requested_fields = tuple(hold.get("missing_fields") or ())
    parsed = extract_event_data_update(
        main_agent,
        event,
        reply_text,
        requested_fields,
        deps.event_type_registry.types,
        deps.area_registry.areas,
        conversation_messages,
    )
    if not parsed.addresses_request:
        return None
    if not parsed.updates:
        return EventDataReplyResult(event["event_id"], {}, hold["question"])

    updates = dict(parsed.updates)
    if "occurred_at" in updates:
        try:
            updates["occurred_at"] = storage_timestamp(parse_timestamp(str(updates["occurred_at"])))
        except (TypeError, ValueError) as exc:
            raise OrchestrationParseError("event data update returned an invalid occurred_at timestamp") from exc
        updates["occurred_at_is_fallback"] = False
    record_event_data_update(deps.persistence, event["event_id"], updates)
    deps.persistence.resolve_held_event(
        "event_data", hold["hold_id"], {"resolved_by": sender_identity, "updated_fields": sorted(updates)}
    )
    return EventDataReplyResult(
        event["event_id"], updates,
        parsed.reply_text,
    )


def resume_after_event_data(
    deps: FlowDeps,
    event_id: str,
    main_agent: "MainAgent",
    insights_agent: "InsightsAgent",
) -> FlowResult:
    event = deps.persistence.fetch_event(event_id)

    if event.get("selected_protocol") is None:
        # No protocol was ever selected — this hold came from item #6's
        # early, event-type-level gate (`_apply_required_fields_gate`), not
        # a per-protocol-step one (which always resumes with a protocol
        # already selected). Continue exactly where extraction would have,
        # had the required fields already been present.
        return _continue_after_required_fields(
            deps, event_id, main_agent, insights_agent, event.get("raw_text", ""), event.get("classification")
        )

    protocol = eligible_protocol_for_scope(
        deps, event.get("selected_protocol"), scope_from_event(event)
    )
    if protocol is None:
        reason = "the selected protocol is no longer available"
        record_event_outcome(deps.persistence, event_id, "failed", failure_reason=reason)
        return FlowResult(event_id, "failed", reason)
    rows = event.get("steps", [])
    steps = tuple(_step_from_row(row) for row in rows)
    precedent_matches = _look_up_precedent_if_possible(deps, event_id, event)
    return _execute_protocol_plan(
        deps, event_id, main_agent, insights_agent, protocol, steps, precedent_matches, resumed=True,
    )
