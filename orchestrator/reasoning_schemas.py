"""Shared orchestration parse types, JSON repair, and decision schemas."""

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

_EVENT_DATA_FIELD_MEANINGS = {
    definition.key: definition.meaning
    for definition in EVENT_FIELD_CATALOG
    if definition.key in EVENT_DATA_FIELDS
}

class OrchestrationParseError(Exception):
    """A Main Agent response could not be parsed into the expected shape."""

    def __init__(self, message: str = "", *, duplicate_agent: bool = False):
        super().__init__(message)
        self.duplicate_agent = duplicate_agent

@dataclass(frozen=True)
class EventDataUpdateResult:
    addresses_request: bool
    updates: dict[str, object] = field(default_factory=dict)
    reply_text: str = ""

class MainAgent(Agent):
    name = "main_agent"
    role = (
        "The orchestrator, and the only component that makes judgment calls: risk assessment, "
        "protocol selection, task formulation, and success judgment. Reasons over what it is "
        "handed; the specialist agents act, this agent decides."
    )
    system_prompt = (
        "You are the Main Agent, the orchestrator of a field-report multi-agent system. You are "
        "given one focused judgment to make at a time, with everything relevant already provided — "
        "never assume context from a different judgment. Follow the exact response format each "
        "prompt requests precisely; your response is parsed programmatically, not read by a person. "
        "When composing an answer or replying to messages in Hebrew, ALWAYS respond exclusively in "
        "concise, direct Hebrew (at most 3-5 lines), with no English."
    )

@dataclass(frozen=True)
class RiskAssessment:
    score: float
    level: Literal["high", "low"]
    reason: str

@dataclass(frozen=True)
class IntentResult:
    intent: Literal["question", "report", "request", "conversational", "needs_clarification"]
    reason: str
    clarification_question: str | None = None

@dataclass(frozen=True)
class IntentAnalysis:
    primary_intent: Literal["question", "report", "request", "conversational", "needs_clarification"]
    asks_for_information: bool
    reports_occurrence: bool
    requests_action: bool
    social_only: bool
    is_quoted: bool
    is_hypothetical: bool
    is_followup_without_context: bool
    evidence: dict[str, str]
    matched_protocol_names: tuple[str, ...]
    reason: str
    ambiguity_reason: str | None = None
    clarification_question: str | None = None

@dataclass(frozen=True)
class ProtocolSelectionResult:
    status: Literal["selected", "ambiguous", "no_match"]
    protocol_name: str | None = None
    candidate_names: tuple[str, ...] = ()
    reason: str = ""

@dataclass(frozen=True)
class FormulationResult:
    steps: tuple[Step, ...] = ()
    failed_agent_name: str | None = None
    failure_reason: str | None = None
    # Correction/retraction linkage (docs memory-audit follow-up): set only when the model
    # explicitly identifies this event's raw text as correcting/retracting one of the
    # RESOLVED precedents it was shown (see _build_formulation_prompt) -- validated by the
    # caller against that same candidate set, never trusted as an arbitrary model-supplied ID.
    corrects_event_id: str | None = None

    @property
    def success(self) -> bool:
        return self.failure_reason is None

@dataclass(frozen=True)
class SuccessVerdict:
    verdict: Literal["success", "failure", "uncertain"]
    reasoning: str

_RISK_SCORE_PATTERN = re.compile(r"RISK_SCORE:\s*([0-9]*\.?[0-9]+)", re.IGNORECASE)
_RISK_REASON_PATTERN = re.compile(r"REASON:\s*(.+)", re.IGNORECASE | re.DOTALL)
_LEGACY_INTENT_PATTERN = re.compile(
    r"\A\s*INTENT:\s*(question|report|request|conversational)\s*\r?\nREASON:\s*(\S(?:[^\r\n]*\S)?)\s*\Z",
    re.IGNORECASE,
)
# Anchored on (?:\A|\n) rather than \A, and matched with .search() rather than .fullmatch()
# (see _parse_selection_response): a real model response commonly reasons through the
# candidates in prose before giving its decision, and requiring the *entire* response to be
# exactly the two-line SELECTED:/REASON: block rejected that prose-then-answer shape outright —
# confirmed live, 2 of 3 identical test runs (docs/IMPROVES/CRITICAL_FIXES_PLAN.MD item 3).
# Leading reasoning is now tolerated; the matched block still must be the final content in the
# response, so a stray, coincidental "SELECTED:"/"REASON:" pair embedded mid-reasoning (not as
# the response's actual last lines) still won't match.
_SELECTED_PATTERN = re.compile(
    r"(?:\A|\n)\s*SELECTED:\s*(\S+)\s*\r?\nREASON:\s*(\S(?:[^\r\n]*\S)?)\s*\Z",
    re.IGNORECASE,
)
_AMBIGUOUS_PATTERN = re.compile(
    r"(?:\A|\n)\s*AMBIGUOUS:\s*([^\r\n]+)\s*\r?\nREASON:\s*(\S(?:[^\r\n]*\S)?)\s*\Z",
    re.IGNORECASE,
)
_NO_MATCH_PATTERN = re.compile(r"(?:\A|\n)\s*NO_MATCH:\s*(\S(?:.*?\S)?)\s*\Z", re.IGNORECASE | re.DOTALL)
_AGENT_TASK_PATTERN = re.compile(r"AGENT:\s*(\S+)\s*\n\s*TASK:\s*(.+?)(?=\nAGENT:|\Z)", re.IGNORECASE | re.DOTALL)
_JSON_CODE_FENCE_PATTERN = re.compile(r"```(?:json)?\s*\n?(.*?)\n?```", re.IGNORECASE | re.DOTALL)
_VERDICT_PATTERN = re.compile(r"VERDICT:\s*(success|failure|uncertain)", re.IGNORECASE)
_REASONING_PATTERN = re.compile(r"REASONING:\s*(.+)", re.IGNORECASE | re.DOTALL)

def _unwrap_json_code_fence(raw_text: str) -> str:
    """Strip a Markdown code fence around a JSON object, if present.

    A model asked for a bare JSON object commonly wraps it in a ```json ... ``` fence anyway.
    Every JSON-expecting parser in this module used to see that fence as unparseable text and
    fail outright — for `classify_intent` specifically, that meant a guaranteed second model
    call every time the model fenced its response (observed ~40% of the time in a real-model
    diagnostic run), silently doubling cost for zero benefit since the re-asked response carries
    the same content unfenced. Returns the fenced content when it looks like a JSON object;
    otherwise returns the input stripped, unchanged, so a non-JSON response (e.g. the legacy
    INTENT:/REASON: format) is unaffected.
    """
    stripped = raw_text.strip()
    if stripped.startswith("{"):
        return stripped
    fence_match = _JSON_CODE_FENCE_PATTERN.search(stripped)
    if fence_match:
        candidate = fence_match.group(1).strip()
        if candidate.startswith("{"):
            return candidate
    return stripped

_EXTRACT_AND_DECIDE_SCHEMA = {
    "type": "object",
    "properties": {
        "classification": {"type": ["string", "null"]},
        "area": {"type": ["string", "null"]},
        "entities": {"type": "array", "items": {"type": "string"}},
        "description": {"type": ["string", "null"]},
        "severity": {"type": ["string", "null"]},
        "occurred_at": {"type": ["string", "null"]},
        "availability_start": {"type": ["string", "null"]},
        "availability_end": {"type": ["string", "null"]},
        "absence_reason": {"type": ["string", "null"]},
        "risk_score": {"type": "number", "minimum": 0, "maximum": 1},
        "risk_reason": {"type": "string"},
        "protocol_status": {"type": "string", "enum": ["selected", "ambiguous", "no_match"]},
        "protocol_name": {"type": ["string", "null"]},
        "candidate_names": {"type": "array", "items": {"type": "string"}},
        "protocol_reason": {"type": "string"},
    },
    "required": [
        "classification", "area", "entities", "description", "severity",
        "occurred_at", "availability_start", "availability_end", "absence_reason",
        "risk_score", "risk_reason", "protocol_status", "protocol_name", "candidate_names", "protocol_reason",
    ],
    "additionalProperties": False,
}

_OPERATIONAL_DECISION_SCHEMA = {
    "type": "object",
    "properties": {
        "risk_score": {"type": "number", "minimum": 0, "maximum": 1},
        "risk_reason": {"type": "string"},
        "protocol_status": {"type": "string", "enum": ["selected", "ambiguous", "no_match"]},
        "protocol_name": {"type": ["string", "null"]},
        "candidate_names": {"type": "array", "items": {"type": "string"}},
        "protocol_reason": {"type": "string"},
    },
    "required": [
        "risk_score", "risk_reason", "protocol_status", "protocol_name", "candidate_names", "protocol_reason"
    ],
    "additionalProperties": False,
}

_FINAL_ASSESSMENT_SCHEMA = {
    "type": "object",
    "properties": {
        "insight": {"type": "string"},
        "verdict": {"type": "string", "enum": ["success", "failure", "uncertain"]},
        "reasoning": {"type": "string"},
    },
    "required": ["insight", "verdict", "reasoning"],
    "additionalProperties": False,
}

_HISTORY_QUERY_SCHEMA = {
    "type": ["object", "null"],
    "properties": {
        "operation": {
            "type": "string",
            "enum": ["latest", "event_details", "list", "count", "aggregate", "compare", "similar_cases", "narrative"],
        },
        "time_start": {"type": ["string", "null"]},
        "time_end": {"type": ["string", "null"]},
        "time_basis": {"type": "string", "enum": ["occurred_at", "received_at"]},
        "classifications": {"type": "array", "items": {"type": "string"}},
        "areas": {"type": "array", "items": {"type": "string"}},
        "outcomes": {"type": "array", "items": {"type": "string"}},
        "protocol_names": {"type": "array", "items": {"type": "string"}},
        "event_ids": {"type": "array", "items": {"type": "string"}},
        "risk_levels": {"type": "array", "items": {"type": "string"}},
        "order": {"type": "string", "enum": ["newest", "oldest"]},
        "group_by": {
            "type": "string",
            "enum": ["none", "classification", "area", "outcome", "protocol", "day", "month"],
        },
        "limit": {"type": "integer", "minimum": 1, "maximum": 200},
    },
    "required": [
        "operation", "time_start", "time_end", "time_basis", "classifications", "areas", "outcomes",
        "protocol_names", "event_ids", "risk_levels", "order", "group_by", "limit"
    ],
    "additionalProperties": False,
}

_QUESTION_PLAN_SCHEMA = {
    "type": ["object", "null"],
    "properties": {
        "route": {"type": "string", "enum": ["history", "agents", "none", "clarification"]},
        "reason": {"type": "string"},
        "history_query": _HISTORY_QUERY_SCHEMA,
        "tasks": {
            "type": "array",
            "maxItems": 4,
            "items": {
                "type": "object",
                "properties": {"agent_name": {"type": "string"}, "task": {"type": "string"}},
                "required": ["agent_name", "task"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["route", "reason", "history_query", "tasks"],
    "additionalProperties": False,
}

_MESSAGE_PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "primary_intent": {
            "type": "string",
            "enum": ["question", "report", "request", "conversational", "needs_clarification"],
        },
        "asks_for_information": {"type": "boolean"},
        "reports_occurrence": {"type": "boolean"},
        "requests_action": {"type": "boolean"},
        "social_only": {"type": "boolean"},
        "is_quoted": {"type": "boolean"},
        "is_hypothetical": {"type": "boolean"},
        "is_followup_without_context": {"type": "boolean"},
        "evidence": {
            "type": "object",
            "properties": {
                "question": {"type": "string"}, "report": {"type": "string"}, "request": {"type": "string"}
            },
            "required": ["question", "report", "request"],
            "additionalProperties": False,
        },
        "matched_protocol_names": {"type": "array", "items": {"type": "string"}},
        "reason": {"type": "string"},
        "ambiguity_reason": {"type": ["string", "null"]},
        "clarification_question": {"type": ["string", "null"]},
        "question_plan": _QUESTION_PLAN_SCHEMA,
        "conversational_reply": {"type": ["string", "null"]},
    },
    "required": [
        "primary_intent", "asks_for_information", "reports_occurrence", "requests_action", "social_only",
        "is_quoted", "is_hypothetical", "is_followup_without_context", "evidence", "matched_protocol_names",
        "reason", "ambiguity_reason", "clarification_question", "question_plan", "conversational_reply"
    ],
    "additionalProperties": False,
}

def _load_unique_json_object(raw_text: str, label: str) -> dict:
    def _reject_duplicate_keys(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate key {key!r}")
            result[key] = value
        return result

    try:
        payload = json.loads(raw_text, object_pairs_hook=_reject_duplicate_keys)
    except (TypeError, ValueError) as exc:
        raise OrchestrationParseError(f"could not parse {label} JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise OrchestrationParseError(f"{label} response must be one JSON object")
    return payload

def _structured_call_with_one_repair(
    main_agent: MainAgent,
    prompt: str,
    *,
    stage: str,
    label: str,
    policy: InvocationPolicy,
) -> tuple[dict, str]:
    last_error: OrchestrationParseError | None = None
    for attempt in range(2):
        attempt_prompt = prompt
        if attempt and last_error is not None:
            attempt_prompt += (
                f"\n\nYour previous response had this schema error: {last_error}. "
                "Repair only the JSON shape and return one object."
            )
        try:
            with stage_context(stage):
                result = main_agent.process(attempt_prompt, [], invocation_policy=policy)
        except AgentOutputParseError as exc:
            # A truncated structured decision cannot be repaired from partial JSON.
            # Surface it through the flow's normal terminal-failure handling;
            # letting it escape the queue leaves the persisted job pending forever.
            raise OrchestrationParseError(f"{label} response was incomplete: {exc}") from exc
        if result.status != "success":
            raise OrchestrationParseError(f"{label} was refused or unusable: {result.text}")
        try:
            return _load_unique_json_object(_unwrap_json_code_fence(result.text), label), result.text
        except OrchestrationParseError as exc:
            last_error = exc
    assert last_error is not None
    raise last_error

_AGENT_TASK_PATTERN = re.compile(r"AGENT:\s*(\S+)\s*\n\s*TASK:\s*(.+?)(?=\nAGENT:|\Z)", re.IGNORECASE | re.DOTALL)

@dataclass(frozen=True)
class OperationalDecision:
    risk: RiskAssessment
    selection: ProtocolSelectionResult

@dataclass(frozen=True)
class FinalAssessment:
    insight: str
    verdict: SuccessVerdict
