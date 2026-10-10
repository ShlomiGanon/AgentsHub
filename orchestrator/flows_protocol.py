"""Protocol selection, plan execution, and step continuation."""

import functools
import json
from dataclasses import replace
from typing import TYPE_CHECKING

from agents import authenticated_request_identity
from history import StepExecutionEnvelope, record_event_state, record_step_executions
from orchestrator.flows_execution import (
    FlowDeps,
    FlowResult,
    _VERDICT_TO_OUTCOME,
    _deadline_failure,
    _log_event_outcome,
    _record_outcome_with_report,
    call_execute_steps,
    call_finish_protocol_assessment,
)
from orchestrator.holds import (
    UNRESOLVED_FIELD,
    create_approval_hold,
    create_clarification_hold,
    create_event_data_hold,
    determine_approval_hold,
)
from orchestrator.reasoning import (
    OrchestrationParseError,
    ProtocolSelectionResult,
    RiskAssessment,
    assess_final_once,
    assess_risk,
    build_insight,
    determine_closure,
    formulate_event_data_question,
    formulate_tasks,
    judge_success,
    look_up_precedent,
    make_operational_decision,
    rewrite_task,
    select_protocol,
)
from orchestrator.situational_picture import compose_picture_from_step_outcomes
from profiles import UNCLASSIFIED_TYPE
from protocols import CriticalityLevel, EVENT_DATA_FIELDS, Step, StepOutcome
from tools import event_id_context, protocol_context
from tools.log_events import (
    event_correction_recorded,
    final_assessment_invalid,
    final_verdict,
    hold_created,
    insight_generated,
    operational_decision_invalid,
    precedent_closure,
    protocol_selection,
    protocol_waiting_for_event_data,
    resource_unavailable_alert,
    resource_unavailable_description_failed,
    risk_assessed,
    synthesis_failed,
)

if TYPE_CHECKING:
    from orchestrator.holds import HoldReason
    from orchestrator.reasoning import InsightsAgent, MainAgent, OperationalDecision
    from protocols import Protocol, ResourceUnavailable


# --- formulate and execute ---


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
    """Formulate or bind steps, then execute the selected protocol for this event."""

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
        if getattr(protocol, "retracts_precedent", False):
            _retract_latest_resolved_precedent(deps, event_id, precedent_matches)
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

def _retract_latest_resolved_precedent(deps: FlowDeps, event_id: str, precedent_matches: tuple) -> None:
    """Mark the latest resolved comparable event as retracted by this correction."""

    resolved = [item for item in precedent_matches if getattr(item, "resolved", False) and getattr(item, "event_id", "")]
    if not resolved:
        return
    target = max(resolved, key=lambda item: getattr(item, "occurred_at", "") or "")
    record_event_state(deps.persistence, event_id, {"corrects_event_id": target.event_id})
    record_event_state(deps.persistence, target.event_id, {"retracted": True})
    event_correction_recorded(event_id=event_id, corrects_event_id=target.event_id)


def _persist_step_plan(deps: FlowDeps, event_id: str, steps: tuple[Step, ...]) -> None:
    """Store the formulated step plan so a later resume can continue it."""

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
    """Rebuild a Step from a persisted step row."""

    return Step(
        agent_name=row["agent_name"],
        task_text=row["task_text"],
        allowed_tools=tuple(row.get("allowed_tools") or ()),
        step_id=row.get("step_id") or str(row["step_index"]),
        depends_on=tuple(row.get("depends_on") or ()),
        required_event_fields=tuple(row.get("required_event_fields") or ()),
    )

def _prior_outcomes(rows: list[dict], steps: tuple[Step, ...]) -> tuple[StepOutcome, ...]:
    """StepOutcome values already recorded for these steps."""

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
    """Persist each outcome against its original step, matching by step_id or position."""

    # Empty step_id happens only on the legacy sequential AGENT:/TASK: path, so position is identity.

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
    """Run persist-execute-assess for an already formulated or rebound plan."""

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
        run_result = call_execute_steps(
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

    return call_finish_protocol_assessment(
        deps, event_id, main_agent, insights_agent, protocol, run_result.step_outcomes, precedent_matches,
        enforce_deadline=not resumed,
    )

def _finish_with_resource_unavailable(
    deps: FlowDeps, event_id: str, resource: "ResourceUnavailable",
) -> FlowResult:
    """Record handled_resource_unavailable with a reporter fact and a commander-only alert."""

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
    """Judge the run, record the outcome, and return the FlowResult."""

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


# --- required fields and risk / approval resume ---


def _apply_required_fields_gate(
    deps: FlowDeps, event_id: str, main_agent: "MainAgent", classification: str
) -> FlowResult | None:
    """Hold for missing event-type required fields, or return None when the gate is already satisfied."""

    required = deps.event_type_registry.required_fields_for(classification)
    if not required:
        return None

    event = deps.persistence.fetch_event(event_id)
    # Availability bounds describe a period of absence. They are conditionally required only
    # when the reporter actually declared an absence; an available/on-duty status has no missing
    # start or end to clarify. The protocol's dynamic attendance binder applies the same rule at
    # step level. Keeping it here as well prevents the earlier event-type gate from asking an
    # irrelevant time question before protocol selection has even run.
    if not (event.get("absence_reason") or "").strip():
        required = tuple(
            field_name for field_name in required
            if field_name not in {"availability_start", "availability_end"}
        )
    missing = tuple(name for name in required if not event.get(name))
    if not missing:
        return None

    conversation_messages: tuple[dict, ...] = ()
    if event.get("conversation_id") and deps.conversation_history_turns > 0:
        conversation_messages = tuple(
            deps.persistence.fetch_conversation_messages(event["conversation_id"], deps.conversation_history_turns * 2)
        )

    question = formulate_event_data_question(main_agent, event, missing, conversation_messages, deps.message_catalog)

    if event.get("conversation_id") and deps.conversation_history_turns > 0:
        deps.persistence.append_conversation_message(
            event["conversation_id"], "assistant", question,
            ttl_hours=deps.conversation_history_ttl_hours, max_turns=deps.conversation_history_turns,
            event_id=event_id,
        )

    # waiting_step_ids=() — no protocol selected yet, so resume can tell this hold from a per-step one.
    create_event_data_hold(deps.persistence, event_id, missing, question, ())
    hold_created(
        hold_kind="event_data", event_id=event_id, missing_fields=missing, classification=classification,
    )
    return FlowResult(event_id, "waiting_for_event_data", question)


def _continue_after_required_fields(
    deps: FlowDeps, event_id: str, main_agent: "MainAgent", insights_agent: "InsightsAgent",
    raw_text: str, classification: str,
    operational_decision: "OperationalDecision | None" = None,
) -> FlowResult:
    """Continue extraction after required fields are present, shared by the fresh and resumed paths."""

    if classification == UNCLASSIFIED_TYPE:
        create_clarification_hold(deps.persistence, event_id, raw_text)
        record_event_state(deps.persistence, event_id, {"clarification_held": True, "clarification_unresolved_field": UNRESOLVED_FIELD})
        hold_created(
            hold_kind="clarification", event_id=event_id, unresolved_field=UNRESOLVED_FIELD,
        )
        return FlowResult(event_id, "held_for_clarification")

    return continue_from_risk_assessment(
        deps, event_id, main_agent, insights_agent, operational_decision=operational_decision,
    )


def _look_up_precedent_if_possible(deps: FlowDeps, event_id: str, event: dict) -> tuple:
    """Comparable prior events for this classification and area, or empty if lookup cannot run."""

    if event["classification"] is None or event["area"] is None:
        return ()
    # Fall back to received_at so an unresolved occurred_at does not skip precedent entirely.
    anchor_time = event["occurred_at"] or event["received_at"]
    return look_up_precedent(deps.history_query_service, event_id, event["classification"], event["area"], anchor_time)


def continue_from_risk_assessment(
    deps: FlowDeps,
    event_id: str,
    main_agent: "MainAgent",
    insights_agent: "InsightsAgent",
    originated_from_commander: bool | None = None,
    selected_protocol: "Protocol | None" = None,
    operational_decision: "OperationalDecision | None" = None,
) -> FlowResult:
    """Run risk, protocol selection, holds, and execution from a persisted event."""

    deadline_failure = _deadline_failure(deps, event_id, "risk_assessment")
    if deadline_failure is not None:
        return deadline_failure
    event = deps.persistence.fetch_event(event_id)
    if originated_from_commander is None:
        # Authorization belongs to the original submitter, not a later hold answerer.
        originated_from_commander = event.get("sender_permission_level") == "commander"
    raw_text, classification, area = event["raw_text"], event["classification"], event["area"]
    description, severity = event["description"], event["severity"]

    if selected_protocol is not None:
        risk_level = "high" if selected_protocol.criticality == CriticalityLevel.HIGH else "low"
        risk_assessment = RiskAssessment(
            level=risk_level,
            score=0.8 if risk_level == "high" else 0.2,
            reason="Deterministic button protocol selection",
        )
        selection = ProtocolSelectionResult(
            status="selected",
            protocol_name=selected_protocol.name,
            candidate_names=(selected_protocol.name,),
            reason="Deterministic button protocol mapping",
        )
    else:
        operational_mode = deps.optimization_policy.operational_decision_mode
        combined_decision = operational_decision if operational_mode == "merged" else None
        if combined_decision is None and operational_mode in {"shadow", "merged"}:
            try:
                combined_decision = make_operational_decision(
                    main_agent, raw_text, classification, area, description, severity,
                    deps.protocol_set.all(), deps.settings_store.get_risk_threshold(),
                    preferred_agent_hint=deps.preferred_agent_hint,
                )
            except OrchestrationParseError as exc:
                operational_decision_invalid(mode=operational_mode, reason=str(exc))
                if operational_mode == "merged":
                    _record_outcome_with_report(deps, event_id, "failed", failure_reason=str(exc))
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
            _record_outcome_with_report(deps, event_id, "failed", failure_reason=str(exc))
            _log_event_outcome(event_id, "failed", failure_reason=str(exc), stage="risk_assessment")
            return FlowResult(event_id, "failed", str(exc))
        risk_assessed(
            event_id=event_id, risk_level=risk_assessment.level,
            risk_score=risk_assessment.score, risk_reason=risk_assessment.reason,
        )

        deadline_failure = _deadline_failure(deps, event_id, "protocol_selection")
        if deadline_failure is not None:
            return deadline_failure
        try:
            selection = (
                combined_decision.selection
                if operational_mode == "merged" and combined_decision is not None
                else select_protocol(
                    main_agent, raw_text, classification, area, description, deps.protocol_set.all(),
                    risk_assessment.level, preferred_agent_hint=deps.preferred_agent_hint,
                )
            )
        except OrchestrationParseError as exc:
            _record_outcome_with_report(deps, event_id, "failed", failure_reason=str(exc))
            _log_event_outcome(event_id, "failed", failure_reason=str(exc), stage="protocol_selection")
            return FlowResult(event_id, "failed", str(exc))

    protocol_selection(
        event_id=event_id, status=selection.status, protocol_name=selection.protocol_name,
        candidate_names=selection.candidate_names, reason=selection.reason,
    )

    precedent_matches = _look_up_precedent_if_possible(deps, event_id, event)
    state_updates = {
        "risk_level": risk_assessment.level,
        "risk_reason": risk_assessment.reason,
    }
    if selection.status == "selected":
        state_updates["selected_protocol"] = selection.protocol_name
        state_updates["protocol_reason"] = selection.reason
    if precedent_matches:
        state_updates["precedent_matched_event_ids"] = [precedent_match.event_id for precedent_match in precedent_matches]
    record_event_state(deps.persistence, event_id, state_updates)
    event.update(state_updates)

    # A precedent can answer an informational report. A protocol that writes a reply
    # or sends an operational notice has to run, the same way a fresh attendance write does.
    selected_protocol = (
        deps.protocol_set.get(selection.protocol_name)
        if selection.status == "selected" and selection.protocol_name
        else None
    )
    precedent_closure_blocked = selected_protocol is not None and (
        selected_protocol.name in {"record_attendance", "record_attendance_response"}
        or bool(getattr(selected_protocol, "viewer_reply_key", ""))
        or bool(getattr(selected_protocol, "operational_notice_key", ""))
    )
    closing_event_id = (
        None
        if precedent_closure_blocked
        else determine_closure(risk_assessment.level, classification, precedent_matches)
    )
    precedent_closure(
        event_id=event_id,
        matched_event_ids=[precedent_match.event_id for precedent_match in precedent_matches],
        closed=closing_event_id is not None,
        closing_event_id=closing_event_id,
    )
    if closing_event_id is not None:
        record_event_state(deps.persistence, event_id, {"precedent_closed_by_event_id": closing_event_id})
        _record_outcome_with_report(deps, event_id, "closed_on_precedent")
        _log_event_outcome(event_id, "closed_on_precedent", precedent_event_id=closing_event_id)
        return FlowResult(event_id, "closed_on_precedent", f"closed against resolved precedent '{closing_event_id}'")

    # No-match is terminal because there is no actionable hold to resolve.
    if selection.status == "no_match":
        _record_outcome_with_report(deps, event_id, "no_match_protocol", failure_reason=selection.reason)
        _log_event_outcome(event_id, "no_match_protocol", reason=selection.reason)
        return FlowResult(event_id, "no_match_protocol", selection.reason)

    protocols_by_name = {protocol.name: protocol for protocol in deps.protocol_set.all()}
    hold_reason: "HoldReason | None" = determine_approval_hold(selection, protocols_by_name, originated_from_commander)

    if hold_reason is not None:
        create_approval_hold(deps.persistence, event_id, hold_reason, selection, risk_assessment)
        record_event_state(deps.persistence, event_id, {"approval_held": True, "approval_reason": hold_reason})
        hold_created(hold_kind="approval", event_id=event_id, reason=hold_reason)
        return FlowResult(event_id, "held_for_approval", hold_reason)

    protocol = protocols_by_name[selection.protocol_name]
    return _run_protocol(
        deps, event_id, main_agent, insights_agent, protocol, precedent_matches,
        raw_text, classification, area, description, event=event,
    )


def decline(deps: FlowDeps, event_id: str) -> FlowResult:
    """Record a rejected approval hold as declined."""

    _record_outcome_with_report(deps, event_id, "declined")
    _log_event_outcome(event_id, "declined")
    return FlowResult(event_id, "declined")


def continue_after_approval(deps: FlowDeps, event_id: str, main_agent: "MainAgent", insights_agent: "InsightsAgent", selected_protocol_name: str) -> FlowResult:
    """Resume execution from task formulation through protocol execution after approval."""

    protocol = deps.protocol_set.get(selected_protocol_name)
    event = deps.persistence.fetch_event(event_id)
    precedent_matches = _look_up_precedent_if_possible(deps, event_id, event)

    return _run_protocol(
        deps, event_id, main_agent, insights_agent, protocol, precedent_matches,
        event["raw_text"], event["classification"], event["area"], event["description"], event=event,
    )


def resume_after_event_data(
    deps: FlowDeps,
    event_id: str,
    main_agent: "MainAgent",
    insights_agent: "InsightsAgent",
) -> FlowResult:
    """Continue a run after missing event fields have been filled in."""

    event = deps.persistence.fetch_event(event_id)

    if event.get("selected_protocol") is None:
        # No protocol yet — this hold came from the event-type required-fields gate.
        return _continue_after_required_fields(
            deps, event_id, main_agent, insights_agent, event.get("raw_text", ""), event.get("classification")
        )

    protocol = deps.protocol_set.get(event.get("selected_protocol"))
    if protocol is None:
        reason = "the selected protocol is no longer available"
        _record_outcome_with_report(deps, event_id, "failed", failure_reason=reason)
        return FlowResult(event_id, "failed", reason)
    if protocol.direct_tool_binder is not None:
        # Re-bind from the now-complete event; persisted step rows do not round-trip binder fields.
        steps = protocol.direct_tool_binder(event)
    else:
        rows = event.get("steps", [])
        steps = tuple(_step_from_row(row) for row in rows)
    precedent_matches = _look_up_precedent_if_possible(deps, event_id, event)
    return _execute_protocol_plan(
        deps, event_id, main_agent, insights_agent, protocol, steps, precedent_matches,
        resumed=True, event=event,
    )
