"""New-event ingest: persist the raw message, extract, and route by intent."""

from typing import TYPE_CHECKING, Literal

from history import (
    ExtractionExecutionError,
    InitialEventEnvelope,
    extract_event,
    record_event_state,
    record_extracted_fields,
    record_initial_event,
)
from orchestrator.holds import determine_clarification_hold
from orchestrator.reasoning import (
    OrchestrationParseError,
    answer_conversationally,
    answer_question,
    classify_intent,
    extract_and_decide,
)
from profiles import HUMAN_ACTIVATION_TYPE, UNCLASSIFIED_TYPE
from agents import AgentModelError, AgentTimeoutError, is_retryable_invocation_error
from tools import get_trace_id
from tools.log_events import (
    extraction_result as log_extraction_result,
    extraction_retry,
    protocol_selection,
    report_received,
    request_received,
    risk_assessed,
)

from orchestrator.flows_execution import (
    FlowDeps,
    FlowResult,
    _deadline_failure,
    _log_event_outcome,
    _record_outcome_with_report,
)
from orchestrator.flows_protocol import (
    _apply_required_fields_gate,
    _continue_after_required_fields,
    continue_from_risk_assessment,
)

if TYPE_CHECKING:
    from orchestrator.reasoning import InsightsAgent, MainAgent, OperationalDecision


# --- persist and extract ---


def _model_invoker_for(main_agent: "MainAgent"):
    """Main-agent callable for extract_event, retrying once on a transient model error."""

    def _invoke(prompt: str) -> str:
        """Ask Main for an extraction response, retrying once on timeout or model error."""

        try:
            agent_result = main_agent.process(prompt, [])
        except (AgentTimeoutError, AgentModelError) as exc:
            if not is_retryable_invocation_error(exc):
                raise
            # Raw text is already persisted by begin_report, so one retry covers a transient timeout.
            extraction_retry(cause=type(exc).__name__)
            agent_result = main_agent.process(prompt, [])
        if agent_result.status != "success":
            raise ExtractionExecutionError(f"main agent could not produce a usable extraction response: {agent_result.text}")
        return agent_result.text

    return _invoke


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
    telegram_chat_id: str | None = None,
    telegram_chat_type: str | None = None,
    ack_message_id: str | None = None,
) -> str:
    """The synchronous prefix of a report: write the raw text and return the event ID, before any model call runs (§7.2's own requirement — "before any processing begins")."""

    event_id = record_initial_event(
        deps.persistence,
        InitialEventEnvelope(
            raw_text=raw_text, source=source, received_at=received_at, sender_identity=sender_identity,
            sender_permission_level=sender_permission_level,
            source_message_id=source_message_id,
            trace_id=get_trace_id() or None, conversation_id=conversation_id, deadline_at=deadline_at,
            telegram_chat_id=telegram_chat_id, telegram_chat_type=telegram_chat_type, ack_message_id=ack_message_id,
        ),
    )

    report_received(
        event_id=event_id, source=source, sender_identity=sender_identity, raw_text=raw_text,
    )

    return event_id


def run_report_extraction(deps: FlowDeps, event_id: str, main_agent: "MainAgent", insights_agent: "InsightsAgent") -> FlowResult:
    """The rest of a report: extraction through outcome."""

    deadline_failure = _deadline_failure(deps, event_id, "extraction")
    if deadline_failure is not None:
        return deadline_failure

    event = deps.persistence.fetch_event(event_id)
    raw_text, source, received_at = event["raw_text"], event["source"], event["received_at"]

    operational_decision = None
    try:
        if deps.optimization_policy.operational_decision_mode == "merged":
            try:
                extraction_result, operational_decision = extract_and_decide(
                    main_agent,
                    raw_text,
                    source,
                    received_at,
                    deps.event_type_registry,
                    deps.area_registry,
                    deps.protocol_set.all(),
                    deps.settings_store.get_risk_threshold(),
                    preferred_agent_hint=deps.preferred_agent_hint,
                )
            except ExtractionExecutionError:
                extraction_result = extract_event(
                    raw_text, source, received_at, deps.event_type_registry, deps.area_registry,
                    model_invoker=_model_invoker_for(main_agent),
                )
                operational_decision = None
        else:
            extraction_result = extract_event(
                raw_text, source, received_at, deps.event_type_registry, deps.area_registry,
                model_invoker=_model_invoker_for(main_agent),
            )
    except ExtractionExecutionError as exc:
        _record_outcome_with_report(deps, event_id, "failed", failure_reason=str(exc))
        _log_event_outcome(event_id, "failed", failure_reason=str(exc), stage="extraction")
        return FlowResult(event_id, "failed", str(exc))

    log_extraction_result(
        event_id=event_id,
        classification=extraction_result.classification,
        area=extraction_result.area,
        missing_fields=extraction_result.missing_fields,
        occurred_at_is_fallback=extraction_result.occurred_at_is_fallback,
    )

    record_extracted_fields(deps.persistence, event_id, extraction_result)

    # A merged extraction/decision can positively determine that the primary update is outside
    # every declared protocol. Finish that result before the classification/required-field gates:
    # an unsupported primary update must not be turned into a clarification merely because an
    # incidental clause resembled an event type or left that type's fields empty.
    if operational_decision is not None and operational_decision.selection.status == "no_match":
        risk_assessed(
            event_id=event_id,
            risk_level=operational_decision.risk.level,
            risk_score=operational_decision.risk.score,
            risk_reason=operational_decision.risk.reason,
        )
        protocol_selection(
            event_id=event_id,
            status="no_match",
            protocol_name=None,
            candidate_names=(),
            reason=operational_decision.selection.reason,
        )
        record_event_state(
            deps.persistence,
            event_id,
            {
                "risk_level": operational_decision.risk.level,
                "risk_reason": operational_decision.risk.reason,
            },
        )
        _record_outcome_with_report(
            deps,
            event_id,
            "no_match_protocol",
            failure_reason=operational_decision.selection.reason,
        )
        _log_event_outcome(
            event_id,
            "no_match_protocol",
            reason=operational_decision.selection.reason,
        )
        return FlowResult(
            event_id,
            "no_match_protocol",
            operational_decision.selection.reason,
        )

    # Persist UNCLASSIFIED_TYPE so the event has a real type; clarification still uses the original result.
    if determine_clarification_hold(extraction_result):
        record_event_state(deps.persistence, event_id, {"classification": UNCLASSIFIED_TYPE})
    resolved_classification = extraction_result.classification or UNCLASSIFIED_TYPE

    try:
        gate_result = _apply_required_fields_gate(deps, event_id, main_agent, resolved_classification)
        if gate_result is not None:
            return gate_result

        return _continue_after_required_fields(
            deps, event_id, main_agent, insights_agent, raw_text, resolved_classification,
            operational_decision=operational_decision,
        )
    except Exception as exc:
        # Last-resort safety net, mirroring the extraction step's own try/except above: this
        # event already exists (begin_report), and everything from here on runs off the serial
        # queue with no caller left waiting for an exception -- the queue's own worker loop only
        # logs and moves on (orchestrator/event_queue.py), so an exception that escapes protocol
        # selection/execution/composition here, uncaught, would otherwise leave the event with no
        # recorded outcome and the sender with no reply at all. Known, expected failure classes
        # deeper in the pipeline already record their own outcome and return normally; only a
        # genuinely unhandled failure reaches this except.
        _record_outcome_with_report(deps, event_id, "failed", failure_reason=str(exc))
        _log_event_outcome(event_id, "failed", failure_reason=str(exc), stage="protocol_execution")
        return FlowResult(event_id, "failed", str(exc))


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
    telegram_chat_id: str | None = None,
    telegram_chat_type: str | None = None,
    ack_message_id: str | None = None,
) -> str:
    """The synchronous prefix of a request: write the raw text, already classified `human_activation` (§6.13 — there is nothing to extract), and return the event ID."""

    event_id = record_initial_event(
        deps.persistence,
        InitialEventEnvelope(
            raw_text=raw_text, source="telegram", received_at=received_at, sender_identity=sender_identity,
            sender_permission_level=sender_permission_level,
            source_message_id=source_message_id, occurred_at=received_at, occurred_at_is_fallback=False,
            trace_id=get_trace_id() or None, conversation_id=conversation_id, deadline_at=deadline_at,
            telegram_chat_id=telegram_chat_id, telegram_chat_type=telegram_chat_type, ack_message_id=ack_message_id,
        ),
    )
    record_event_state(deps.persistence, event_id, {"classification": HUMAN_ACTIVATION_TYPE})

    request_received(event_id=event_id, sender_identity=sender_identity, raw_text=raw_text)

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

    intent = classify_intent(main_agent, deps.protocol_set.all(), message_text)

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
