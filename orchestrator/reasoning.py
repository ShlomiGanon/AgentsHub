"""Model-driven orchestration decisions and question answering."""

from __future__ import annotations

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Callable, Literal

from agents import Agent, AgentOutputParseError, HistoryAgent, InvocationPolicy, last_finished_invocation_id, record_finished_invocation_id
from config import BaseConfig
from history import EVENT_FIELD_CATALOG, ExtractionExecutionError, HistoryQuerySpec, PrecedentMatch
from history.event_pipeline import extraction_result_from_payload, _prompt as _extraction_prompt
from history.query import HistoryQueryError
from messages import get_current_catalog
from messages.model_messages import (
    CONVERSATIONAL_REPLY_INSTRUCTION,
    EVENT_DATA_QUESTION_INSTRUCTION,
)
from orchestrator.tone import banned_opener
from protocols import EVENT_DATA_FIELDS, Protocol, Step
from tools import stage_context
from tools.log_events import agent_selection, specialist_failed, specialist_finished, specialist_started, specialist_timeout

if TYPE_CHECKING:
    from agents.runtime import AgentDescriptor, AgentRegistry
    from history.query import HistoryQueryService
    from messages import MessageCatalog
    from protocols.executor import StepOutcome

from orchestrator.reasoning_schemas import (
    EventDataUpdateResult,
    FinalAssessment,
    FormulationResult,
    IntentAnalysis,
    IntentResult,
    MainAgent,
    OperationalDecision,
    OrchestrationParseError,
    ProtocolSelectionResult,
    RiskAssessment,
    SuccessVerdict,
    _AGENT_TASK_PATTERN,
    _AMBIGUOUS_PATTERN,
    _EVENT_DATA_FIELD_MEANINGS,
    _EXTRACT_AND_DECIDE_SCHEMA,
    _FINAL_ASSESSMENT_SCHEMA,
    _HISTORY_QUERY_SCHEMA,
    _JSON_CODE_FENCE_PATTERN,
    _LEGACY_INTENT_PATTERN,
    _MESSAGE_PLAN_SCHEMA,
    _NO_MATCH_PATTERN,
    _OPERATIONAL_DECISION_SCHEMA,
    _QUESTION_PLAN_SCHEMA,
    _REASONING_PATTERN,
    _RISK_REASON_PATTERN,
    _RISK_SCORE_PATTERN,
    _SELECTED_PATTERN,
    _VERDICT_PATTERN,
    _load_unique_json_object,
    _structured_call_with_one_repair,
    _unwrap_json_code_fence,
)
from orchestrator.reasoning_report import (
    _build_conversational_prompt,
    _build_formulation_prompt,
    _build_intent_prompt,
    _build_judgment_prompt,
    _build_rewrite_prompt,
    _build_risk_assessment_prompt,
    _build_selection_prompt,
    _formulation_json_candidate,
    _normalize_evidence,
    _operational_decision_from_payload,
    _parse_formulation_response,
    _parse_intent_response,
    _parse_judgment_response,
    _parse_risk_assessment_response,
    _parse_selection_response,
    _parse_structured_intent_response,
    _preferred_agent_hint_block,
    _required_bool,
    _resolved_precedents,
    answer_conversationally,
    assess_final_once,
    assess_risk,
    classify_intent,
    determine_closure,
    extract_and_decide,
    extract_event_data_update,
    formulate_event_data_question,
    formulate_tasks,
    judge_success,
    look_up_precedent,
    make_operational_decision,
    rewrite_task,
    select_protocol,
)
from orchestrator.reasoning_qa import (
    AgentSelectionResult,
    MessagePlan,
    QuestionAnswer,
    SpecialistFailure,
    SpecialistResult,
    _append_partial_failure_note,
    _build_agent_selection_prompt,
    _build_compose_prompt,
    _build_direct_lookup_prompt,
    _build_message_plan_prompt,
    _cant_answer_reply,
    _failed_specialist_names,
    _history_query_spec_from_payload,
    _DIRECT_LOOKUP_PATTERN,
    _NONE_PATTERN,
    _is_direct_most_recent_lookup,
    _parse_agent_selection_response,
    _question_reference_context,
    _reply_for_history_query_error,
    _specialist_from_history_error,
    _usable_specialist_result,
    answer_question,
    answer_question_from_plan,
    plan_message,
    run_parallel_specialists,
)

def construct_core_agents(base_config: BaseConfig) -> dict[str, Agent]:
    """Build the Main agent from core-tier model settings."""

    return {"main_agent": MainAgent(model=base_config.core_model.model, api_key=base_config.core_model.api_key)}

class InsightsAgent(Agent):
    """SUB-tier agent that writes one conclusion after a protocol run."""

    name = "insights_agent"
    role = (
        "Synthesizes the end of every protocol run: given what each sub-agent was asked and what it "
        "returned, plus comparable prior events, forms one conclusion setting this run against history. "
        "Concludes; does not act."
    )
    system_prompt = (
        "You are the Insights Agent. You are given the task text and result for every step of a "
        "protocol run, plus comparable prior events from the historical record. Hold both halves — "
        "the task and the result — together: this is what lets you distinguish an agent that failed "
        "from an agent that was asked the wrong question. Return one conclusion covering both the "
        "current run and how it compares to history, not two separate observations."
    )

def _build_insight_prompt(protocol: Protocol, step_outcomes: tuple["StepOutcome", ...], comparable_history: tuple["PrecedentMatch", ...]) -> str:
    """Prompt asking Insights to compare this run with comparable history."""

    steps_block = "\n".join(
        f"- {outcome.step.agent_name} was asked: {outcome.step.task_text!r}\n"
        f"  and {'succeeded' if outcome.succeeded else 'failed'}, returning: {outcome.result_text!r}"
        for outcome in step_outcomes
    )
    history_block = (
        "\n".join(
            f"- {precedent.occurred_at}: classification={precedent.classification}, protocol={precedent.protocol_name}, "
            f"outcome={precedent.outcome}, resolved={precedent.resolved}"
            for precedent in comparable_history
        )
        or "(no comparable prior events found)"
    )
    return (
        f"Form one conclusion about this run of the '{protocol.name}' protocol, setting it against "
        "comparable prior events — not two separate observations.\n\n"
        f"What happened in this run:\n{steps_block}\n\nComparable prior events:\n{history_block}"
    )

def build_insight(
    insights_agent: InsightsAgent,
    protocol: Protocol,
    step_outcomes: tuple["StepOutcome", ...],
    comparable_history: tuple["PrecedentMatch", ...] = (),
) -> str:
    """Ask Insights for one conclusion about this protocol run. Returns the insight text."""

    with stage_context("insight_generation"):
        agent_result = insights_agent.process(_build_insight_prompt(protocol, step_outcomes, comparable_history), [])
    if agent_result.status != "success":
        raise OrchestrationParseError(f"insight generation did not produce a usable response: {agent_result.text}")
    return agent_result.text

def construct_insights_agent(base_config: BaseConfig) -> dict[str, Agent]:
    """Build the Insights agent from core-tier model settings."""

    return {
        "insights_agent": InsightsAgent(
            model=base_config.core_model.model,
            api_key=base_config.core_model.api_key,
        )
    }

