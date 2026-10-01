"""Protocol plan execution and step continuation."""

import functools
import json
import threading
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Callable, Literal

from history import (
    ExtractionExecutionError,
    InitialEventEnvelope,
    StepExecutionEnvelope,
    extract_event,
    parse_timestamp,
    record_event_data_update,
    record_event_outcome,
    record_event_state,
    record_extracted_fields,
    record_initial_event,
    record_step_executions,
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
from orchestrator.capabilities import CapabilityDescriptor, build_role_aware_system_context, visible_capabilities
from orchestrator.reasoning import build_insight, construct_insights_agent
from orchestrator.report_composer import ReportComposerAgent, compose_report  # re-exported: api may only import orchestrator.flows
from orchestrator.run_report import build_run_summary, resolve_audience
from orchestrator.reasoning import (
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
    plan_message,
    rewrite_task,
    extract_and_decide,
    extract_event_data_update,
    select_protocol,
    _unwrap_json_code_fence,
    ProtocolSelectionResult,
    RiskAssessment,
    SpecialistFailure,
    SpecialistResult,
    run_parallel_specialists,
)
from orchestrator.reasoning import answer_question, determine_closure, look_up_precedent
from orchestrator.situational_picture import (  # re-exported: api may only import orchestrator.flows
    SituationalPicture,
    build_situational_picture,
    compose_picture_from_step_outcomes,
)
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
from profiles import HUMAN_ACTIVATION_TYPE, OptimizationPolicy, UNCLASSIFIED_TYPE
from protocols import CriticalityLevel, EVENT_DATA_FIELDS, ResourceUnavailable, Step, StepOutcome
from protocols.executor import execute_steps
from agents import AgentModelError, AgentTimeoutError, InvocationPolicy, authenticated_request_identity, is_retryable_invocation_error
from messages import get_catalog
from tools import event_id_context, get_trace_id, protocol_context, stage_context
from tools.log_events import (
    direct_lane_accepted,
    direct_lane_declined,
    event_correction_recorded,
    event_outcome,
    extraction_result as log_extraction_result,
    extraction_retry,
    final_assessment_invalid,
    final_verdict,
    hold_created,
    hold_escalated,
    hold_reminder_sent,
    hold_resolved,
    hold_sweep_failed,
    insight_generated,
    operational_decision_invalid,
    precedent_closure,
    protocol_selection,
    protocol_waiting_for_event_data,
    reply_latency,
    report_received,
    request_received,
    resource_unavailable_alert,
    resource_unavailable_description_failed,
    risk_assessed,
    synthesis_failed,
)

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
    from messages import MessageCatalog

from orchestrator.flows import FlowDeps, FlowResult, _VERDICT_TO_OUTCOME, _deadline_failure, _log_event_outcome, _record_outcome_with_report

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
    event: dict | None = None,
) -> FlowResult:
    deadline_failure = _deadline_failure(deps, event_id, "formulation")
    if deadline_failure is not None:
        return deadline_failure
    if event is None:
        event = deps.persistence.fetch_event(event_id)
    if protocol.direct_tool_binder is not None:
        # Declared direct-tool steps (Phase A): parameters are bound from the event's own
        # extracted fields by the profile's own binder, never by the model — no
        # formulate_tasks/task_rewrite call at all, so precedent_matches (whatever comparable
        # history this event has) structurally cannot reach or escalate a direct-tool step's
        # instructions, since no instructions are ever written for one.
        direct_tool_steps = protocol.direct_tool_binder(event)
        return _execute_protocol_plan(
            deps, event_id, main_agent, insights_agent, protocol, direct_tool_steps, precedent_matches,
            event=event,
        )
    conversation_messages: tuple = ()
    conversation_id = (event or {}).get("conversation_id")
    if conversation_id and deps.conversation_history_turns > 0:
        conversation_messages = tuple(
            deps.persistence.fetch_conversation_messages(conversation_id, deps.conversation_history_turns * 2)
        )
    formulation = formulate_tasks(
        main_agent, protocol, deps.registry, raw_text, classification, area, description,
        precedent_context=precedent_matches, event_data=event,
        # The event type's statically-declared required fields (item #6's
        # EVENT_TYPE_REQUIRED_FIELDS), unioned into every formulated step's own
        # required_event_fields regardless of what the model declares — see
        # formulate_tasks' docstring.
        required_fields_floor=deps.event_type_registry.required_fields_for(classification),
        conversation_messages=conversation_messages,
    )
    if formulation.success and formulation.corrects_event_id:
        # Correction/retraction linkage: the target event_id was already validated (formulate_tasks
        # only ever accepts one of the RESOLVED precedents it was shown) before reaching here.
        record_event_state(deps.persistence, event_id, {"corrects_event_id": formulation.corrects_event_id})
        record_event_state(deps.persistence, formulation.corrects_event_id, {"retracted": True})
        event_correction_recorded(event_id=event_id, corrects_event_id=formulation.corrects_event_id)
    if not formulation.success:
        _record_outcome_with_report(deps, event_id, "failed", failure_reason=formulation.failure_reason)
        _log_event_outcome(event_id, "failed", failure_reason=formulation.failure_reason, stage="formulation")
        return FlowResult(event_id, "failed", formulation.failure_reason or "")
    return _execute_protocol_plan(
        deps, event_id, main_agent, insights_agent, protocol, formulation.steps, precedent_matches,
        event=event,
    )

def _persist_step_plan(deps: FlowDeps, event_id: str, steps: tuple[Step, ...]) -> None:
    record_step_executions(
        deps.persistence,
        event_id,
        tuple(
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
            )
            for index, step in enumerate(steps)
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
    envelopes = []
    for position, outcome in enumerate(outcomes):
        step_key = outcome.step.step_id
        index = index_by_step_id[step_key] if step_key else position
        persisted_step = steps[index]
        envelopes.append(
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
            )
        )
    record_step_executions(deps.persistence, event_id, envelopes)

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
    event: dict | None = None,
) -> FlowResult:
    import orchestrator.flows as _host

    if event is None:
        event = deps.persistence.fetch_event(event_id)
    if not resumed:
        deadline_failure = _deadline_failure(deps, event_id, "execution")
        if deadline_failure is not None:
            return deadline_failure

    agents_by_name = {name: deps.registry.get(name) for name in protocol.participating_agents}
    persisted_rows = event.get("steps", [])
    prior = _prior_outcomes(persisted_rows, steps)
    # Every agent step receives the complete immutable envelope of the event that
    # caused the protocol run.  Previously only fields listed in
    # ``required_event_fields`` were injected.  That let a model see the parsed
    # absence interval while not seeing the sender identity, source message ID,
    # original text, or receipt time needed by write tools such as
    # ``record_attendance_response``.  Those values already exist in persistence
    # and the authenticated execution context; exposing them here prevents a
    # needless UNCLEAR_TASK refusal without allowing the model to invent them.
    event_envelope = {
        "event_id": event.get("event_id"),
        "sender_identity": event.get("sender_identity"),
        "sender_permission_level": event.get("sender_permission_level"),
        "source": event.get("source"),
        "source_message_id": event.get("source_message_id"),
        "received_at": event.get("received_at"),
        "raw_text": event.get("raw_text"),
        "validated_event_fields": {name: event.get(name) for name in EVENT_DATA_FIELDS},
    }
    event_envelope_text = (
        "\n\nAuthoritative event envelope (use these values; do not ask the caller to provide them): "
        + json.dumps(event_envelope, ensure_ascii=False, sort_keys=True)
    )
    execution_steps = tuple(
        replace(
            step,
            task_text=(
                f"{step.task_text}\n\nCurrent validated event data JSON (use this as the source of truth): "
                f"{json.dumps({name: event.get(name) for name in step.required_event_fields}, ensure_ascii=False, sort_keys=True)}"
                f"{event_envelope_text}"
            ),
        )
        for step in steps
    )
    with event_id_context(event_id), protocol_context(protocol.name), authenticated_request_identity(event["sender_identity"]):
        run_result = _host.execute_steps(
            list(execution_steps),
            agents_by_name,
            deps.settings_store,
            task_rewriter=functools.partial(rewrite_task, main_agent),
            event_data=event,
            prior_outcomes=prior,
        )
    if run_result.waiting_for_event_data and not persisted_rows:
        _persist_step_plan(deps, event_id, steps)
    _persist_step_outcomes(deps, event_id, steps, run_result.step_outcomes)

    if run_result.waiting_for_event_data:
        latest_event = deps.persistence.fetch_event(event_id)
        conversation_messages: tuple[dict, ...] = ()
        if latest_event.get("conversation_id"):
            conversation_messages = tuple(
                deps.persistence.fetch_conversation_messages(latest_event["conversation_id"], 12)
            )
        question = formulate_event_data_question(
            main_agent, latest_event, run_result.missing_event_fields, conversation_messages, deps.message_catalog
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
            )
        create_event_data_hold(
            deps.persistence, event_id, run_result.missing_event_fields, question, waiting_step_ids
        )
        protocol_waiting_for_event_data(
            event_id=event_id, missing_event_fields=run_result.missing_event_fields,
        )
        return FlowResult(event_id, "waiting_for_event_data", question)

    if not run_result.completed:
        _record_outcome_with_report(deps, event_id, "failed", failure_reason=run_result.failure_cause)
        _log_event_outcome(
            event_id, "failed", failure_reason=run_result.failure_cause, stage="execution",
            failed_step_agent=run_result.failed_step_agent,
        )
        return FlowResult(event_id, "failed", run_result.failure_cause or "")

    recall_selection = next(
        (
            outcome.result_text
            for outcome in run_result.step_outcomes
            if outcome.selection_required and outcome.result_text
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

    return _host._finish_protocol_assessment(
        deps, event_id, main_agent, insights_agent, protocol, run_result.step_outcomes, precedent_matches,
        enforce_deadline=not resumed,
    )

def _finish_with_resource_unavailable(
    deps: FlowDeps, event_id: str, resource: "ResourceUnavailable",
) -> FlowResult:
    """The resource-unavailable outcome. Two strictly separate texts, both grounded in
    `resource` alone, never in each other:

    - `fact_sentence`: what happened, in plain already-localized language (the profile's own
      `resource_unavailable_description` hook translates `resource`, which is why core never
      hardcodes "drone"/"east_gate"-style identifiers into user-facing text itself). Fed to the
      *reporter's own* report composer as an ordinary input fact (`resource_unavailable_fact`
      on `RunSummary`) — reaches the reporter through the normal composed reply (or its
      deterministic fallback), for every audience. Never contains alternatives.
    - `commander_alert_text`: the fact plus concrete alternatives, persisted on its own column,
      read only by the separate `resource_unavailable_alert` notification (api/routes.py),
      delivered only to each commander's own private chat (bot/interactions.py). Never part of
      `report_text`, never reaches the reporter's own chat, never model-composed.
    """

    fact_sentence = f"{resource.resource_kind} was unavailable for {resource.area}: {resource.reason}"
    alternatives = ""
    if deps.resource_unavailable_description is not None:
        try:
            fact_sentence, alternatives = deps.resource_unavailable_description(
                resource.resource_kind, resource.area, resource.reason, deps.registry
            )
        except Exception as exc:
            resource_unavailable_description_failed(resource_kind=resource.resource_kind, reason=str(exc))
    if not alternatives:
        alternatives = deps.message_catalog.text("orchestrator.resource_unavailable.no_alternatives")

    commander_alert_text = deps.message_catalog.text(
        "orchestrator.resource_unavailable.commander_alert", fact=fact_sentence, alternatives=alternatives,
    )

    _record_outcome_with_report(
        deps, event_id, "handled_resource_unavailable",
        resource_unavailable_fact=fact_sentence, commander_alert_text=commander_alert_text,
    )
    resource_unavailable_alert(
        event_id=event_id, resource_kind=resource.resource_kind, area=resource.area, reason=resource.reason,
    )
    _log_event_outcome(event_id, "handled_resource_unavailable", resource_kind=resource.resource_kind, area=resource.area)
    return FlowResult(event_id, "handled_resource_unavailable", fact_sentence)

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

    resource_unavailable = next(
        (outcome.resource_unavailable for outcome in step_outcomes if outcome.resource_unavailable is not None), None
    )
    if resource_unavailable is not None:
        # A deterministic, DB-sourced signal always wins over the normal judgment path (below)
        # for THIS aspect of the outcome, regardless of `protocol.needs_insight` — a model
        # judging "no drone was available" as plain failure would be both wrong (the report
        # itself was handled correctly) and inconsistent across protocols, since only some
        # declare needs_insight=False. See `_finish_with_resource_unavailable`.
        return _finish_with_resource_unavailable(deps, event_id, resource_unavailable)

    if not protocol.needs_insight:
        # Deterministic verdict, no build_insight/judge_success call at all (Phase A): every
        # step succeeded -> succeeded, else failed with the first failing step's own reason.
        # Recording a report exactly as given IS correct behavior for a direct-tool step, not
        # something that needs a model's judgment call.
        first_failure = next((outcome for outcome in step_outcomes if not outcome.succeeded), None)
        outcome = "succeeded" if first_failure is None else "failed"
        failure_reason = first_failure.failure_reason if first_failure is not None else None
        _record_outcome_with_report(deps, event_id, outcome, failure_reason=failure_reason, insight_text="")
        _log_event_outcome(event_id, outcome, failure_reason=failure_reason)
        return FlowResult(event_id, outcome, failure_reason or "")
    final_assessment = None
    persisted_event = deps.persistence.fetch_event(event_id)
    if deps.optimization_policy.final_assessment_mode == "low_risk_merged" and persisted_event.get("risk_level") == "low":
        try:
            final_assessment = assess_final_once(main_agent, protocol, step_outcomes, precedent_matches)
        except OrchestrationParseError as exc:
            final_assessment_invalid(reason=str(exc))

    insight_text = (
        final_assessment.insight
        if final_assessment is not None
        else build_insight(insights_agent, protocol, step_outcomes, comparable_history=precedent_matches)
    )
    if len(protocol.participating_agents) > 1:
        # A multi-domain protocol's insight is the live picture composed from what the
        # specialists just reported plus the recent event log, never a prepared text.
        # The viewer/commander ownership scope follows the event's persisted role snapshot.
        sender_filter = (
            None
            if persisted_event.get("sender_permission_level") == "commander"
            else persisted_event.get("sender_identity")
        )
        try:
            synthesis = compose_picture_from_step_outcomes(
                main_agent,
                protocol,
                step_outcomes,
                persisted_event.get("raw_text", ""),
                deps.history_query_service,
                sender_identity_filter=sender_filter,
            )
            if synthesis:
                insight_text = synthesis
        except Exception as exc:
            synthesis_failed(cause=str(exc))
    insight_generated(event_id=event_id, protocol=protocol.name, insight_text=insight_text)

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
                _record_outcome_with_report(
                    deps, event_id, "failed",
                    failure_reason=f"success judgment failed: {exc}", insight_text=insight_text,
                )
                _log_event_outcome(event_id, "failed", failure_reason=str(exc), stage="judgment")
                return FlowResult(event_id, "failed", str(exc))

    outcome = _VERDICT_TO_OUTCOME[verdict.verdict]
    _record_outcome_with_report(deps, event_id, outcome, insight_text=insight_text)
    final_verdict(event_id=event_id, verdict=verdict.verdict, reasoning=verdict.reasoning)
    _log_event_outcome(event_id, outcome, reasoning=verdict.reasoning)
    return FlowResult(event_id, outcome, verdict.reasoning)
