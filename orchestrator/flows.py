"""The new-event flow and the package's declared entry point (work_plan.md §6.11, §6.14).

`FlowDeps` bundles everything a flow needs — including
`history_query_service`, which *is* §6.14's "one persistent handle to the
history query interface, created at startup and used by every flow":
precedent lookup, the Insights Agent's comparison, and the question flow
all take it from here rather than each opening a second path into
history. No separate module was needed for 6.14 once this bundle existed.

Every write to the event record goes through `history.interface`'s
dedicated functions — never a raw `persistence.update_event` call from
this module — matching Mission 5's write path exactly.

Restartability (§6.11's explicit requirement): the two `resume_*`
functions take only a hold ID and an answer, and re-read everything else
— the event's already-extracted fields, the hold's own payload — from
persistence. Neither depends on anything held in memory from the original
call, so a resume works identically whether it happens in the same
process or after a full restart.

Every entry and resumption point below is actually two pieces composed
together: a fast, synchronous "begin"/"resolve" prefix that only writes a
record and returns, and a "run"/"continue" piece that does the real work
(model calls, tool calls) and can take a while. `process_report`,
`process_request`, `resume_after_clarification`, and the approved branch
of `resume_after_approval` compose their own two pieces back into one
synchronous call, so every one of Mission 6's own tests keeps working
unchanged. §7.2 (the async job mechanism) is what actually needs them
kept apart: it calls a "begin"/"resolve" piece inline in the request
handler to get an event ID back immediately, then submits the matching
"run"/"continue" piece to `orchestrator.queue.SerialEventQueue` instead of
running it in the request. §7.11's deny path is the one exception that
stays fully synchronous even for the API — declining is genuinely final,
with no continuation to queue.
"""

import functools
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Literal

from history.interface import (
    ExtractionExecutionError,
    InitialEventEnvelope,
    StepExecutionEnvelope,
    extract_event,
    record_event_outcome,
    record_event_state,
    record_extracted_fields,
    record_initial_event,
    record_step_execution,
)
from orchestrator.errors import OrchestrationParseError
from orchestrator.formulation import formulate_tasks, rewrite_task
from orchestrator.holds import (
    UNRESOLVED_FIELD,
    answer_approval_hold,
    answer_clarification_hold,
    create_approval_hold,
    create_clarification_hold,
    determine_approval_hold,
    determine_clarification_hold,
)
from orchestrator.insights import build_insight, construct_core_agents as construct_insights_agent
from orchestrator.intent import classify_intent
from orchestrator.judgment import judge_success
from orchestrator.main_agent import assess_risk, construct_core_agents as construct_main_agent
from orchestrator.precedent import determine_closure, look_up_precedent
from orchestrator.question_flow import answer_question
from orchestrator.queue import SerialEventQueue
from orchestrator.selection import select_protocol
from profiles.spec import HUMAN_ACTIVATION_TYPE
from protocols.executor import execute_steps

if TYPE_CHECKING:
    from agents.base import Agent
    from agents.registry import AgentRegistry
    from auth.permissions import PermissionLevel
    from config.base import BaseConfig
    from config.settings_store import SettingsStore
    from history.query import HistoryQueryService
    from orchestrator.holds import HoldAnswerResult, HoldReason
    from orchestrator.insights import InsightsAgent
    from orchestrator.main_agent import MainAgent
    from persistence.interface import PersistenceInterface
    from profiles.loader import LoadedProfile
    from protocols.loader import ProtocolSet
    from protocols.model import Protocol
    from registries.areas import AreaRegistry
    from registries.event_types import EventTypeRegistry

FlowOutcome = Literal[
    "closed_on_precedent", "declined", "succeeded", "failed", "uncertain",
    "held_for_clarification", "held_for_approval",
]

# orchestrator.judgment.SuccessVerdict.verdict speaks "success"/"failure"/
# "uncertain" (its own, separately-evolved sentinel vocabulary); history.write
# .VALID_OUTCOMES speaks "succeeded"/"failed"/"uncertain". Translate here,
# at the one point they meet, rather than coupling either module's
# vocabulary to the other's.
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


@dataclass(frozen=True)
class FlowResult:
    event_id: str
    outcome: FlowOutcome
    detail: str = ""


def _model_invoker_for(main_agent: "MainAgent"):
    def _invoke(prompt: str) -> str:
        result = main_agent.process(prompt, [])
        if result.status != "success":
            raise ExtractionExecutionError(f"main agent could not produce a usable extraction response: {result.text}")
        return result.text

    return _invoke


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# -- Entry points: brand new messages ----------------------------------


def begin_report(
    deps: FlowDeps,
    raw_text: str,
    source: Literal["sensor", "telegram"],
    received_at: str,
    sender_identity: str,
) -> str:
    """The synchronous prefix of a report: write the raw text and return
    the event ID, before any model call runs (§7.2's own requirement —
    "before any processing begins"). Pass the returned event ID to
    `run_report_extraction` next, either inline (as `process_report`
    does) or via a queued continuation (as §7.2/§7.3/§7.4 do).
    """

    return record_initial_event(
        deps.persistence,
        InitialEventEnvelope(raw_text=raw_text, source=source, received_at=received_at, sender_identity=sender_identity),
    )


def run_report_extraction(deps: FlowDeps, event_id: str, main_agent: "MainAgent", insights_agent: "InsightsAgent") -> FlowResult:
    """The rest of a report: extraction through outcome. Re-reads the
    event `begin_report` already wrote rather than taking `raw_text`/
    `source`/`received_at` as arguments — this is the piece §7.2 queues,
    so it must work from the event ID alone.
    """

    event = deps.persistence.fetch_event(event_id)
    raw_text, source, received_at = event["raw_text"], event["source"], event["received_at"]

    try:
        extraction_result = extract_event(
            raw_text, source, received_at, deps.event_type_registry, deps.area_registry,
            model_invoker=_model_invoker_for(main_agent),
        )
    except ExtractionExecutionError as exc:
        record_event_outcome(deps.persistence, event_id, "failed", failure_reason=str(exc))
        return FlowResult(event_id, "failed", str(exc))

    # No scheduler wired up here — FlowDeps has none; §5.6's late-arriving-
    # event hook is an available extension point once a real startup
    # sequence (§7/§9) constructs and threads a SummaryScheduler through,
    # not required by §6.11's own bullets.
    record_extracted_fields(deps.persistence, event_id, extraction_result)

    if determine_clarification_hold(extraction_result):
        create_clarification_hold(deps.persistence, event_id, raw_text)
        record_event_state(deps.persistence, event_id, {"clarification_held": True, "clarification_unresolved_field": UNRESOLVED_FIELD})
        return FlowResult(event_id, "held_for_clarification")

    return continue_from_risk_assessment(deps, event_id, main_agent, insights_agent, originated_from_commander=False)


def process_report(
    deps: FlowDeps,
    main_agent: "MainAgent",
    insights_agent: "InsightsAgent",
    raw_text: str,
    source: Literal["sensor", "telegram"],
    received_at: str,
    sender_identity: str,
) -> FlowResult:
    """A report of something that happened, run synchronously start to
    finish — `begin_report` + `run_report_extraction` composed back into
    one call. A report never originates from "a commander's own request"
    (§6.4/§6.7's bypass is for requests only), so closure/approval logic
    downstream always treats it as not commander-originated.
    """

    event_id = begin_report(deps, raw_text, source, received_at, sender_identity)
    return run_report_extraction(deps, event_id, main_agent, insights_agent)


def begin_request(deps: FlowDeps, raw_text: str, received_at: str, sender_identity: str) -> str:
    """The synchronous prefix of a request: write the raw text, already
    classified `human_activation` (§6.13 — there is nothing to extract),
    and return the event ID. Pass it to `continue_from_risk_assessment`
    next, either inline (as `process_request` does) or via a queued
    continuation (as §7.4 does).
    """

    event_id = record_initial_event(
        deps.persistence,
        InitialEventEnvelope(
            raw_text=raw_text, source="telegram", received_at=received_at, sender_identity=sender_identity,
            occurred_at=received_at, occurred_at_is_fallback=False,
        ),
    )
    record_event_state(deps.persistence, event_id, {"classification": HUMAN_ACTIVATION_TYPE})

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
    """A person's request for an action, run synchronously start to
    finish — `begin_request` + `continue_from_risk_assessment` composed
    back into one call.
    """

    event_id = begin_request(deps, raw_text, received_at, sender_identity)
    return continue_from_risk_assessment(deps, event_id, main_agent, insights_agent, originated_from_commander)


def process_message(
    deps: FlowDeps,
    main_agent: "MainAgent",
    insights_agent: "InsightsAgent",
    message_text: str,
    sender_identity: str,
    received_at: str,
    is_commander: bool,
) -> tuple[Literal["question", "report", "request"], object]:
    """Route a person's message by intent (§6.13). Returns which of the
    three the message was taken as, alongside the result — a question's
    answer (`str`), or a `FlowResult` for a report or request. Delivering
    that "taken as" information back to the sender is the bot/API's job
    (§8/§7, not yet built); this is what it will read.
    """

    intent = classify_intent(main_agent, deps.protocol_set.all(), message_text)

    if intent.intent == "question":
        return "question", answer_question(main_agent, message_text, deps.registry, deps.history_query_service)

    if intent.intent == "report":
        return "report", process_report(deps, main_agent, insights_agent, message_text, "telegram", received_at, sender_identity)

    return "request", process_request(deps, main_agent, insights_agent, message_text, received_at, sender_identity, is_commander)


# -- Resumption points ----------------------------------------------------


def resolve_clarification(
    deps: FlowDeps,
    hold_id: str,
    answering_identity: str,
    answering_level: "PermissionLevel",
    chosen_classification: str,
) -> "HoldAnswerResult":
    """The synchronous prefix of answering a clarification hold: validate
    and record the answer, nothing more. A resolved answer always needs a
    continuation (`continue_after_clarification`) — unlike an approval
    answer, there is no "final, no continuation" branch here, since
    resolving a classification never itself ends a run.
    """

    answer = answer_clarification_hold(deps.persistence, hold_id, answering_identity, answering_level, chosen_classification, deps.event_type_registry)
    if answer.status != "resolved":
        return answer  # unauthorized / not_found / invalid_classification — nothing to resume

    event_id = answer.hold["event_id"]
    record_event_state(
        deps.persistence, event_id,
        {"classification": chosen_classification, "clarification_resolved_by": answering_identity, "clarification_chosen_classification": chosen_classification},
    )

    return answer


def continue_after_clarification(deps: FlowDeps, event_id: str, main_agent: "MainAgent", insights_agent: "InsightsAgent") -> FlowResult:
    """Resume at risk assessment, not extraction — the other extracted
    fields are still valid and re-running extraction would discard the
    commander's decision (§6.2's own rule). This is a full run (§7.11's
    own note) — the piece §7.2/§7.11 queue rather than run inline.
    """

    return continue_from_risk_assessment(deps, event_id, main_agent, insights_agent, originated_from_commander=False)


def resume_after_clarification(
    deps: FlowDeps,
    main_agent: "MainAgent",
    insights_agent: "InsightsAgent",
    hold_id: str,
    answering_identity: str,
    answering_level: "PermissionLevel",
    chosen_classification: str,
):
    """Answer a clarification hold and resume, synchronously start to
    finish — `resolve_clarification` + `continue_after_clarification`
    composed back into one call.
    """

    answer = resolve_clarification(deps, hold_id, answering_identity, answering_level, chosen_classification)
    if answer.status != "resolved":
        return answer  # unauthorized / not_found / invalid_classification — nothing to resume

    return continue_after_clarification(deps, answer.hold["event_id"], main_agent, insights_agent)


def resolve_approval(
    deps: FlowDeps,
    hold_id: str,
    answering_identity: str,
    answering_level: "PermissionLevel",
    decision: Literal["approved", "rejected"],
) -> "HoldAnswerResult":
    """The synchronous prefix of answering an approval hold: validate and
    record the answer, nothing more. Unlike a clarification answer, what
    follows genuinely forks: `rejected` is already final (see
    `resume_after_approval`'s own handling) and needs no continuation;
    only `approved` needs `continue_after_approval`.
    """

    answer = answer_approval_hold(deps.persistence, hold_id, answering_identity, answering_level, decision)
    if answer.status not in ("approved", "rejected"):
        return answer  # unauthorized / not_found — nothing to resume

    event_id = answer.hold["event_id"]
    record_event_state(deps.persistence, event_id, {"approval_answered_by": answering_identity, "approval_answered_at": _now()})

    return answer


def decline(deps: FlowDeps, event_id: str) -> FlowResult:
    """Record a rejected approval hold's outcome as declined — the
    synchronous, no-continuation-needed branch `resume_after_approval`
    and `api.holds`'s deny path (§7.11) both share.
    """

    record_event_outcome(deps.persistence, event_id, "declined")
    return FlowResult(event_id, "declined")


def continue_after_approval(deps: FlowDeps, event_id: str, main_agent: "MainAgent", insights_agent: "InsightsAgent", selected_protocol_name: str) -> FlowResult:
    """Resume execution from task formulation through protocol execution
    — the approved branch only. This is not free: it costs the same
    several-model-call run §7.2 exists to avoid blocking a request on, so
    it is the piece §7.2/§7.11 queue rather than run inline.
    """

    protocol = deps.protocol_set.get(selected_protocol_name)
    event = deps.persistence.fetch_event(event_id)
    precedent_matches = _look_up_precedent_if_possible(deps, event_id, event)

    return _run_protocol(deps, event_id, main_agent, insights_agent, protocol, precedent_matches, event["raw_text"], event["classification"], event["area"], event["description"])


def resume_after_approval(
    deps: FlowDeps,
    main_agent: "MainAgent",
    insights_agent: "InsightsAgent",
    hold_id: str,
    answering_identity: str,
    answering_level: "PermissionLevel",
    decision: Literal["approved", "rejected"],
):
    """Answer an approval hold and resume, synchronously start to finish
    — `resolve_approval` + (on approval only) `continue_after_approval`
    composed back into one call. End the run as declined on rejection,
    writing that outcome to the event record; declining is already final
    here, with nothing left to continue.
    """

    answer = resolve_approval(deps, hold_id, answering_identity, answering_level, decision)
    if answer.status not in ("approved", "rejected"):
        return answer  # unauthorized / not_found — nothing to resume

    event_id = answer.hold["event_id"]

    if answer.status == "rejected":
        return decline(deps, event_id)

    return continue_after_approval(deps, event_id, main_agent, insights_agent, answer.hold["selected_protocol_name"])


# -- Internal continuation --------------------------------------------------


def _look_up_precedent_if_possible(deps: FlowDeps, event_id: str, event: dict) -> tuple:
    if event["occurred_at"] is None or event["classification"] is None or event["area"] is None:
        return ()
    return look_up_precedent(deps.history_query_service, event_id, event["classification"], event["area"], event["occurred_at"])


def continue_from_risk_assessment(deps: FlowDeps, event_id: str, main_agent: "MainAgent", insights_agent: "InsightsAgent", originated_from_commander: bool) -> FlowResult:
    event = deps.persistence.fetch_event(event_id)
    raw_text, classification, area = event["raw_text"], event["classification"], event["area"]
    description, severity = event["description"], event["severity"]

    risk_assessment = assess_risk(main_agent, classification, area, description, severity, deps.settings_store.get_risk_threshold())
    record_event_state(deps.persistence, event_id, {"risk_level": risk_assessment.level, "risk_reason": risk_assessment.reason})

    selection = select_protocol(main_agent, raw_text, classification, area, description, deps.protocol_set.all(), risk_assessment.level)
    if selection.status == "selected":
        record_event_state(deps.persistence, event_id, {"selected_protocol": selection.protocol_name, "protocol_reason": selection.reason})

    precedent_matches = _look_up_precedent_if_possible(deps, event_id, event)
    if precedent_matches:
        record_event_state(deps.persistence, event_id, {"precedent_matched_event_ids": [p.event_id for p in precedent_matches]})

    closing_event_id = determine_closure(risk_assessment.level, classification, precedent_matches)
    if closing_event_id is not None:
        record_event_state(deps.persistence, event_id, {"precedent_closed_by_event_id": closing_event_id})
        record_event_outcome(deps.persistence, event_id, "closed_on_precedent")
        return FlowResult(event_id, "closed_on_precedent", f"closed against resolved precedent '{closing_event_id}'")

    protocols_by_name = {p.name: p for p in deps.protocol_set.all()}
    hold_reason: "HoldReason | None" = determine_approval_hold(selection, protocols_by_name, originated_from_commander)

    if hold_reason is not None:
        create_approval_hold(deps.persistence, event_id, hold_reason, selection, risk_assessment)
        record_event_state(deps.persistence, event_id, {"approval_held": True, "approval_reason": hold_reason})
        return FlowResult(event_id, "held_for_approval", hold_reason)

    protocol = protocols_by_name[selection.protocol_name]
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
    formulation = formulate_tasks(main_agent, protocol, deps.registry, raw_text, classification, area, description, precedent_context=precedent_matches)
    if not formulation.success:
        # Rerun formulation once — no agent has executed yet, nothing has touched the world (§6.11).
        formulation = formulate_tasks(main_agent, protocol, deps.registry, raw_text, classification, area, description, precedent_context=precedent_matches)

    if not formulation.success:
        record_event_outcome(deps.persistence, event_id, "failed", failure_reason=formulation.failure_reason)
        return FlowResult(event_id, "failed", formulation.failure_reason or "")

    agents_by_name = {name: deps.registry.get(name) for name in protocol.participating_agents}
    task_rewriter = functools.partial(rewrite_task, main_agent)

    run_result = execute_steps(list(formulation.steps), agents_by_name, deps.settings_store, task_rewriter=task_rewriter)

    for index, outcome in enumerate(run_result.step_outcomes):
        record_step_execution(
            deps.persistence, event_id,
            StepExecutionEnvelope(
                step_index=index, agent_name=outcome.step.agent_name, task_text=outcome.step.task_text,
                allowed_tools=list(outcome.step.allowed_tools), result_text=outcome.result_text, attempt_count=outcome.attempt_count,
            ),
        )

    if not run_result.completed:
        record_event_outcome(deps.persistence, event_id, "failed", failure_reason=run_result.failure_cause)
        return FlowResult(event_id, "failed", run_result.failure_cause or "")

    insight_text = build_insight(insights_agent, protocol, run_result.step_outcomes, comparable_history=precedent_matches)

    try:
        verdict = judge_success(main_agent, protocol, run_result.step_outcomes, insight_text=insight_text)
    except OrchestrationParseError:
        try:
            verdict = judge_success(main_agent, protocol, run_result.step_outcomes, insight_text=insight_text)
        except OrchestrationParseError as exc:
            # Rerun only the judgment, never the agents, which already acted (§6.10). Both attempts failed.
            record_event_outcome(deps.persistence, event_id, "failed", failure_reason=f"success judgment failed: {exc}", insight_text=insight_text)
            return FlowResult(event_id, "failed", str(exc))

    outcome = _VERDICT_TO_OUTCOME[verdict.verdict]
    record_event_outcome(deps.persistence, event_id, outcome, insight_text=insight_text)
    return FlowResult(event_id, outcome, verdict.reasoning)
