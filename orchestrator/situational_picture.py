"""A situational picture assembled at request time, never from prepared text.

The Main Agent first decides what it needs to know from every specialist
that participates in the picture protocol (one concrete question per
domain), those questions are put to the specialists concurrently so each
one answers from its own read-only tools against live state, the recent
event log is pulled from history for the window the Main Agent chose, and
only then does the Main Agent write the picture from exactly those
findings. A domain that fails or times out is reported as unavailable
rather than papered over, and when the model cannot compose, the findings
are still returned verbatim under a timestamped header.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Callable, Literal

from agents import InvocationPolicy, authenticated_request_identity, get_active_provider_diagnostic_trace
from history import HistoryQuerySpec, storage_timestamp
from history.query import HistoryQueryError
from persistence import OperationalScope, operational_scope_context, operational_time_context, resolve_operational_scope
from profiles import current_operational_profile, operational_profile_context
from messages import get_current_catalog
from messages.model_messages import (
    SITUATIONAL_PICTURE_COMPOSE_INSTRUCTION,
    SITUATIONAL_PICTURE_PLAN_INSTRUCTION,
    SITUATIONAL_PICTURE_REASONING_INSTRUCTION,
)
from orchestrator.reasoning import run_parallel_specialists
from tools import get_trace_id, stage_context

if TYPE_CHECKING:
    from agents.runtime import AgentRegistry
    from history.query import HistoryQueryService
    from orchestrator.reasoning import MainAgent
    from protocols import Protocol, StepOutcome

logger = logging.getLogger(__name__)

RECENT_EVENTS_DOMAIN = "recent_events"

DEFAULT_RECENT_EVENTS_HOURS = 12
MIN_RECENT_EVENTS_HOURS = 1
MAX_RECENT_EVENTS_HOURS = 72
RECENT_EVENTS_LIMIT = 8
# How many committed operational facts a rendered picture may state. Bounded
# well below RECENT_EVENTS_LIMIT so a commander brief stays a brief and can
# never become a chronological dump.
FALLBACK_OPERATIONAL_FACT_LIMIT = 4
SPECIALIST_TIMEOUT_SECONDS = 25.0
PICTURE_MAX_LINES = 8
REASONING_MAX_FACTS = 2
REASONING_MAX_ASSESSMENTS = 1
REASONING_MAX_RECOMMENDATIONS = 1
REASONING_OUTPUT_TOKEN_BUDGET = 650
REASONING_FACT_TEXT_MAX = 45
REASONING_CONCLUSION_TEXT_MAX = 55
REASONING_QUALIFICATION_TEXT_MAX = 20
REASONING_RECOMMENDATION_TEXT_MAX = 55
REASONING_RATIONALE_TEXT_MAX = 20
REASONING_CAPABILITY_MAX = 32


@dataclass(frozen=True)
class SituationalQueryScope:
    """Typed request scope for an operational current-state query."""

    team: bool = False
    surveillance: bool = False
    drones: bool = False
    external_reports: bool = False
    overall: bool = False

    @classmethod
    def overall_scope(cls) -> "SituationalQueryScope":
        return cls(team=True, surveillance=True, drones=True, external_reports=True, overall=True)

    def as_dict(self) -> dict[str, bool]:
        return {
            "team": self.team,
            "surveillance": self.surveillance,
            "drones": self.drones,
            "external_reports": self.external_reports,
            "overall": self.overall,
        }

    @property
    def selected_domain_count(self) -> int:
        return sum((self.team, self.surveillance, self.drones, self.external_reports))

    @property
    def requires_bounded_reasoning(self) -> bool:
        return self.overall or self.selected_domain_count > 1


_SITUATIONAL_PICTURE_TERMS = (
    "\u05ea\u05de\u05d5\u05e0\u05ea \u05de\u05e6\u05d1",
    "\u05ea\u05de\u05d5\u05e0\u05ea \u05d4\u05de\u05e6\u05d1",
    "situational picture",
    "situation picture",
    "sector situational picture",
    "\u05de\u05e6\u05d1 \u05d4\u05d2\u05d6\u05e8\u05d4",
    "\u05e1\u05d8\u05d8\u05d5\u05e1 \u05d4\u05d2\u05d6\u05e8\u05d4",
    "sector status",
    "overall picture",
)
_OPERATIONAL_PICTURE_TERMS = (
    "\u05ea\u05de\u05d5\u05e0\u05d4",
    "picture",
    "overview",
    "brief",
    "\u05e1\u05e7\u05d9\u05e8\u05d4",
)
_OPERATIONAL_SIGNAL_TERMS = (
    "\u05d7\u05e9\u05d5\u05d3",
    "\u05d0\u05d9\u05d5\u05dd",
    "\u05d4\u05ea\u05e8\u05d0\u05d4",
    "\u05d0\u05d9\u05e8\u05d5\u05e2",
    "\u05e1\u05d9\u05db\u05d5\u05df",
    "suspicious",
    "threat",
    "alarm",
    "risk",
)
_OPERATIONAL_AREA_TERMS = (
    "\u05d2\u05d6\u05e8\u05d4",
    "\u05e9\u05e2\u05e8",
    "\u05d4\u05d9\u05e7\u05e4\u05d9\u05dd",
    "\u05d0\u05d6\u05d5\u05e8",
    "sector",
    "gate",
    "perimeter",
    "area",
)
_TEAM_SCOPE_TERMS = (
    "\u05e1\u05d3\u05db",
    "\u05db\u05d5\u05d7",
    "\u05e6\u05d5\u05d5\u05ea",
    "crew",
    "team",
    "\u05db\u05d5\u05e0\u05e0\u05d5\u05ea",
    "\u05d6\u05de\u05d9\u05e0",
    "\u05d7\u05e1\u05e8",
    "roster",
    "manpower",
    "personnel",
    "readiness team",
    "available",
    "missing",
)
_SURVEILLANCE_SCOPE_TERMS = (
    "\u05de\u05e6\u05dc\u05de",
    "\u05ea\u05e6\u05e4",
    "\u05d2\u05d3\u05e8",
    "camera",
    "surveillance",
    "perimeter",
    "observation",
)
_DRONE_SCOPE_TERMS = (
    "\u05e8\u05d7\u05e4\u05e0",
    "drone",
    "uav",
)
_EXTERNAL_REPORT_SCOPE_TERMS = (
    "\u05d3\u05d9\u05d5\u05d5\u05d7",
    "recent reports",
    "recent events",
    "\u05e8\u05db\u05d1",
    "\u05e8\u05db\u05d1\u05d9\u05dd",
    "\u05d0\u05d6\u05d4\u05e8\u05d4",
    "\u05e9\u05e8\u05d1",
    "\u05e9\u05e8\u05d9\u05e4\u05d4",
    "vehicle",
    "advisory",
    "incident",
    "fire",
)
_CURRENT_STATE_TERMS = (
    "\u05de\u05e6\u05d1",
    "\u05e1\u05d8\u05d8\u05d5\u05e1",
    "\u05e1\u05d9\u05db\u05d5\u05dd",
    "\u05ea\u05e6\u05d9\u05d2",
    "\u05ea\u05df",
    "\u05ea\u05e4\u05d9\u05e7",
    "status",
    "state",
    "summary",
    "show",
    "give",
    "what",
    "who",
)
_DAILY_SUMMARY_TERMS = (
    "\u05e1\u05d9\u05db\u05d5\u05dd",
    "\u05de\u05d4 \u05e7\u05e8\u05d4",
    "\u05de\u05d4 \u05d4\u05e9\u05ea\u05e0\u05d4",
    "\u05d4\u05d9\u05d5\u05dd",
    "\u05d9\u05d5\u05de\u05d9",
    "\u05de\u05de\u05dc\u05d9\u05e5",
    "\u05d4\u05de\u05dc\u05e6\u05d4",
    "\u05e1\u05d9\u05db\u05d5\u05dd",
    "\u05de\u05d4 \u05e7\u05e8\u05d4",
    "\u05de\u05d4 \u05d4\u05e9\u05ea\u05e0\u05d4",
    "\u05d4\u05d9\u05d5\u05dd",
    "\u05d9\u05d5\u05de\u05d9",
    "\u05de\u05de\u05dc\u05d9\u05e5",
    "\u05d4\u05de\u05dc\u05e6\u05d4",
    "daily",
    "today",
    "recommend",
    "recommendation",
)
_FOLLOW_UP_STATUS_TERMS = (
    "\u05e4\u05e2\u05d5\u05dc\u05d4",
    "\u05d1\u05e7\u05e9\u05d4",
    "\u05d0\u05d9\u05e9\u05d5\u05e8",
    "\u05d0\u05d9\u05e8\u05d5\u05e2 \u05e7\u05d5\u05d3\u05dd",
    "action",
    "request",
    "approval",
    "previous event",
)


def _normalize_query_terms(text: str) -> str:
    normalized = str(text or "").casefold()
    normalized = re.sub(r"[\"'\u05f3\u05f4]", "", normalized)
    return " ".join(re.sub(r"[^\w\u0590-\u05ff]+", " ", normalized).split())


def _contains_query_term(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


def classify_situational_query(text: str) -> SituationalQueryScope | None:
    """Recognize supported operational state questions and derive their scope.

    This is intentionally limited to supported state domains and excludes
    lifecycle/follow-up status questions before any protocol is selected.
    """

    normalized = _normalize_query_terms(text)
    if not normalized:
        return None

    explicit_picture_request = _contains_query_term(normalized, _SITUATIONAL_PICTURE_TERMS)
    has_picture_phrase = explicit_picture_request
    has_team = _contains_query_term(normalized, _TEAM_SCOPE_TERMS)
    has_surveillance = _contains_query_term(normalized, _SURVEILLANCE_SCOPE_TERMS)
    has_drones = _contains_query_term(normalized, _DRONE_SCOPE_TERMS)
    has_external_reports = _contains_query_term(normalized, _EXTERNAL_REPORT_SCOPE_TERMS)
    has_current_state = _contains_query_term(normalized, _CURRENT_STATE_TERMS) or "?" in str(text)
    has_daily_summary = _contains_query_term(normalized, _DAILY_SUMMARY_TERMS)
    has_operational_picture = _contains_query_term(normalized, _OPERATIONAL_PICTURE_TERMS)
    has_operational_context = (
        _contains_query_term(normalized, _OPERATIONAL_SIGNAL_TERMS)
        and _contains_query_term(normalized, _OPERATIONAL_AREA_TERMS)
    )

    if has_operational_picture and has_operational_context:
        has_picture_phrase = True
        # A generic cross-domain signal/area question is about current
        # surveillance and committed external indications. Keep the scope
        # bounded to those domains instead of widening it to drones/team
        # merely because the requester used the word "picture".
        if not any((has_team, has_surveillance, has_drones, has_external_reports)):
            has_surveillance = True
            has_external_reports = True

    if _contains_query_term(normalized, _FOLLOW_UP_STATUS_TERMS) and not has_picture_phrase:
        return None

    if not any((has_team, has_surveillance, has_drones, has_external_reports)):
        return SituationalQueryScope.overall_scope() if has_picture_phrase or has_daily_summary else None

    if not has_current_state and not has_picture_phrase and not has_daily_summary:
        return None

    # Asking for a situational picture is asking for the whole picture. Naming
    # domains inside such a request is emphasis, not a restriction, so it must
    # not hide a degraded camera from a commander who also mentioned manpower.
    # Only the explicit phrase widens; the generic "picture"/"brief" wording
    # handled above deliberately stays bounded to the domains it implies.
    if explicit_picture_request:
        return SituationalQueryScope.overall_scope()

    return SituationalQueryScope(
        team=has_team,
        surveillance=has_surveillance,
        drones=has_drones,
        external_reports=has_external_reports,
        overall=False,
    )

_PLAN_POLICY = InvocationPolicy(max_output_tokens=400, timeout_seconds=30.0, reasoning_effort="none")
_COMPOSE_POLICY = InvocationPolicy(max_output_tokens=450, timeout_seconds=45.0, reasoning_effort="none")
_REASONING_SCHEMA = {
    "type": "object",
    "properties": {
        "facts": {
            "type": "array",
            "maxItems": REASONING_MAX_FACTS,
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "minLength": 1, "maxLength": REASONING_FACT_TEXT_MAX},
                    "source_aliases": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 2,
                        "items": {"type": "string", "pattern": "^S[1-9][0-9]*$"},
                    },
                },
                "required": ["text", "source_aliases"],
                "additionalProperties": False,
            },
        },
        "assessments": {
            "type": "array",
            "maxItems": REASONING_MAX_ASSESSMENTS,
            "items": {
                "type": "object",
                "properties": {
                    "conclusion": {"type": "string", "minLength": 1, "maxLength": REASONING_CONCLUSION_TEXT_MAX},
                    "source_aliases": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 2,
                        "items": {"type": "string", "pattern": "^S[1-9][0-9]*$"},
                    },
                    "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
                    "qualification": {"type": "string", "maxLength": REASONING_QUALIFICATION_TEXT_MAX},
                    "affected_domains": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 2,
                        "items": {"type": "string", "enum": ["team", "surveillance", "drones", "external_reports", "cross_domain"]},
                    },
                    "priority": {"type": "string", "enum": ["low", "medium", "high"]},
                },
                "required": ["conclusion", "source_aliases", "confidence", "affected_domains", "priority"],
                "additionalProperties": False,
            },
        },
        "recommendations": {
            "type": "array",
            "maxItems": REASONING_MAX_RECOMMENDATIONS,
            "items": {
                "type": "object",
                "properties": {
                    "description": {"type": "string", "minLength": 1, "maxLength": REASONING_RECOMMENDATION_TEXT_MAX},
                    "rationale": {"type": "string", "minLength": 1, "maxLength": REASONING_RATIONALE_TEXT_MAX},
                    "source_aliases": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 2,
                        "items": {"type": "string", "pattern": "^S[1-9][0-9]*$"},
                    },
                    "priority": {"type": "string", "enum": ["low", "medium", "high"]},
                    "possible_capability": {"type": ["string", "null"], "maxLength": REASONING_CAPABILITY_MAX},
                    "requires_approval": {"type": "boolean"},
                },
                "required": ["description", "rationale", "source_aliases", "priority", "possible_capability", "requires_approval"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["facts", "assessments", "recommendations"],
    "additionalProperties": False,
}
_REASONING_POLICY = InvocationPolicy(
    max_output_tokens=REASONING_OUTPUT_TOKEN_BUDGET,
    timeout_seconds=45.0,
    reasoning_effort="none",
    response_schema={"name": "operational_sitrep", "schema": _REASONING_SCHEMA},
)


@dataclass(frozen=True)
class DomainBriefing:
    """One question the Main Agent decided to put to one specialist."""

    agent_name: str
    query: str


@dataclass(frozen=True)
class PicturePlan:
    briefings: tuple[DomainBriefing, ...]
    recent_events_hours: int
    planned_by_model: bool
    scope: SituationalQueryScope | None = None


@dataclass(frozen=True)
class DomainReport:
    """What one domain answered (or that it did not answer) for this picture."""

    domain: str
    query: str
    text: str
    succeeded: bool


@dataclass(frozen=True)
class SnapshotProvenance:
    """Typed source metadata attached to one snapshot section."""

    source: str
    as_of: str
    scope: str = "global"
    operational_scope: str = "LIVE"


@dataclass(frozen=True)
class CameraOperationalFact:
    """The bounded camera detail needed to explain an abnormal state."""

    camera_id: str
    area: str | None
    status: str


@dataclass(frozen=True)
class CameraSnapshot:
    total: int | None
    active: int | None
    inactive: int | None
    unknown: int | None
    status: str
    provenance: SnapshotProvenance
    degraded: int | None = None
    offline: int | None = None
    abnormal_cameras: tuple[CameraOperationalFact, ...] = ()


@dataclass(frozen=True)
class RecentOperationalReport:
    """A committed report safe for the shared picture (no internal identifiers)."""

    text: str
    source_ref: str
    received_at: str
    # The owning report domain, so a picture can tell which committed facts a
    # structured section already states authoritatively.
    domain: str = ""


@dataclass(frozen=True)
class DroneSnapshot:
    total: int | None
    ready: int | None
    airborne: int | None
    charging: int | None
    maintenance: int | None
    unknown: int | None
    active_missions: int | None
    status: str
    provenance: SnapshotProvenance


@dataclass(frozen=True)
class TeamSnapshot:
    total: int | None
    available: int | None
    unavailable: int | None
    not_reported: int | None
    pending_identity: int | None
    status: str
    provenance: SnapshotProvenance
    operational_manpower: int | None = None
    effective_manpower: int | None = None
    operational_resources: tuple[str, ...] = ()


FindingType = Literal["coverage", "readiness", "availability", "data_quality"]
FindingSeverity = Literal["info", "warning", "critical"]


@dataclass(frozen=True)
class OperationalFinding:
    """Deterministic implication derived from verified snapshot fields."""

    finding_type: FindingType
    severity: FindingSeverity
    source_refs: tuple[str, ...]
    message_key: str
    message_values: tuple[tuple[str, object], ...] = ()
    suggested_action_key: str | None = None
    suggested_action_values: tuple[tuple[str, object], ...] = ()


AssessmentPriority = Literal["low", "medium", "high"]
AssessmentConfidence = Literal["low", "medium", "high"]
ReasoningDomain = Literal["team", "surveillance", "drones", "external_reports", "cross_domain"]


@dataclass(frozen=True)
class OperationalAssessment:
    """A model-derived conclusion grounded in supplied source references."""

    conclusion: str
    supporting_source_refs: tuple[str, ...]
    confidence: AssessmentConfidence
    qualification: str
    affected_domains: tuple[ReasoningDomain, ...]
    priority: AssessmentPriority


@dataclass(frozen=True)
class RecommendedAction:
    """A display-only recommendation; it is not an execution request."""

    description: str
    rationale: str
    supporting_source_refs: tuple[str, ...]
    priority: AssessmentPriority
    possible_capability: str | None
    requires_approval: bool


def _compact_operational_text(value: str, *, limit: int = 140) -> str:
    text = " ".join(value.split())
    if len(text) <= limit:
        return text
    return f"{text[:limit - 1].rstrip()}…"


def _compact_source_aliases(context: "OperationalContext") -> dict[str, str]:
    return {source_ref: f"S{index}" for index, source_ref in enumerate(context.source_refs, start=1)}


def _compact_source_description(context: "OperationalContext", source_ref: str) -> str:
    if source_ref.startswith("state:cameras:"):
        return "authoritative camera state"
    if source_ref.startswith("state:drones:"):
        return "authoritative drone state"
    if source_ref.startswith("state:team:"):
        return "authoritative team state"
    for report in context.recent_reports:
        if report.source_ref == source_ref:
            return _compact_operational_text(report.text)
    return "current committed evidence"


def _compact_state_value(value: object) -> object:
    return value if value is not None else None


@dataclass(frozen=True)
class OperationalContext:
    """A bounded, typed input to one overall-picture reasoning call."""

    query_scope: SituationalQueryScope
    current_time: str
    cameras: CameraSnapshot | None
    drones: DroneSnapshot | None
    team: TeamSnapshot | None
    recent_reports: tuple[RecentOperationalReport, ...]
    findings: tuple[OperationalFinding, ...]
    inconsistencies: tuple[str, ...]
    source_refs: tuple[str, ...]
    operational_scope: str = "LIVE"

    def prompt_payload(self) -> dict:
        catalog = get_current_catalog()
        payload: dict[str, object] = {
            "query_scope": self.query_scope.as_dict(),
            "current_time": self.current_time,
            "operational_scope": self.operational_scope,
            "authoritative_facts": {},
            "current_run_operational_reports": [],
            "deterministic_findings": [],
            "uncertainties": list(self.inconsistencies),
            "source_refs": list(self.source_refs),
        }
        facts = payload["authoritative_facts"]
        assert isinstance(facts, dict)

        if self.cameras is not None:
            facts["surveillance"] = {
                "status": self.cameras.status,
                "total": self.cameras.total,
                "active": self.cameras.active,
                "degraded": self.cameras.degraded,
                "offline": self.cameras.offline,
                "unknown": self.cameras.unknown,
                "abnormal_entities": [
                    {
                        "camera_id": camera.camera_id,
                        "area": camera.area,
                        "status": camera.status,
                    }
                    for camera in self.cameras.abnormal_cameras
                ],
                "source_refs": [_state_source("cameras", self.cameras.provenance)],
            }
        if self.drones is not None:
            facts["drones"] = {
                "status": self.drones.status,
                "total": self.drones.total,
                "ready": self.drones.ready,
                "airborne": self.drones.airborne,
                "charging": self.drones.charging,
                "maintenance": self.drones.maintenance,
                "unknown": self.drones.unknown,
                "active_missions": self.drones.active_missions,
                "source_refs": [_state_source("drones", self.drones.provenance)],
            }
        if self.team is not None:
            facts["team"] = {
                "status": self.team.status,
                "total": self.team.total,
                "available": self.team.available,
                "unavailable": self.team.unavailable,
                "not_reported": self.team.not_reported,
                "pending_identity": self.team.pending_identity,
                "operational_manpower": self.team.operational_manpower,
                "effective_manpower": self.team.effective_manpower,
                "operational_resources": list(self.team.operational_resources),
                "source_refs": [_state_source("team", self.team.provenance)],
            }

        reports = payload["current_run_operational_reports"]
        assert isinstance(reports, list)
        reports.extend(
            {
                "text": report.text,
                "received_at": report.received_at,
                "source_ref": report.source_ref,
            }
            for report in self.recent_reports
        )

        findings = payload["deterministic_findings"]
        assert isinstance(findings, list)
        findings.extend(
            {
                "text": catalog.text(finding.message_key, **dict(finding.message_values)),
                "severity": finding.severity,
                "finding_type": finding.finding_type,
                "source_refs": list(finding.source_refs),
            }
            for finding in self.findings
        )
        return payload

    def provider_prompt_payload(self) -> dict:
        """Return the compact provider-only view; canonical refs stay internal."""

        aliases = _compact_source_aliases(self)
        payload: dict[str, object] = {
            "t": self.current_time,
            "q": self.query_scope.as_dict(),
            "o": self.operational_scope,
            "v": [aliases[source_ref] for source_ref in self.source_refs],
            "s": {},
            "r": [],
            "f": [],
            "u": [_compact_operational_text(item, limit=80) for item in self.inconsistencies],
        }
        states = payload["s"]
        assert isinstance(states, dict)

        if self.cameras is not None:
            camera_ref = _state_source("cameras", self.cameras.provenance)
            states["c"] = {
                "n": _compact_state_value(self.cameras.total),
                "a": _compact_state_value(self.cameras.active),
                "d": _compact_state_value(self.cameras.degraded),
                "o": _compact_state_value(self.cameras.offline),
                "u": _compact_state_value(self.cameras.unknown),
                "q": self.cameras.status,
                "x": [
                    {"i": item.camera_id, "a": item.area, "q": item.status}
                    for item in self.cameras.abnormal_cameras
                ],
                "z": aliases[camera_ref],
            }
        if self.drones is not None:
            drone_ref = _state_source("drones", self.drones.provenance)
            states["d"] = {
                "n": _compact_state_value(self.drones.total),
                "r": _compact_state_value(self.drones.ready),
                "b": _compact_state_value(self.drones.airborne),
                "c": _compact_state_value(self.drones.charging),
                "m": _compact_state_value(self.drones.maintenance),
                "u": _compact_state_value(self.drones.unknown),
                "x": _compact_state_value(self.drones.active_missions),
                "q": self.drones.status,
                "z": aliases[drone_ref],
            }
        if self.team is not None:
            team_ref = _state_source("team", self.team.provenance)
            states["m"] = {
                "n": _compact_state_value(self.team.total),
                "a": _compact_state_value(self.team.available),
                "u": _compact_state_value(self.team.unavailable),
                "nr": _compact_state_value(self.team.not_reported),
                "p": _compact_state_value(self.team.pending_identity),
                "q": self.team.status,
                "z": aliases[team_ref],
            }
            if self.team.operational_manpower is not None:
                states["m"]["om"] = self.team.operational_manpower
                states["m"]["em"] = self.team.effective_manpower
            if self.team.operational_resources:
                states["m"]["rs"] = list(self.team.operational_resources)

        reports = payload["r"]
        assert isinstance(reports, list)
        reports.extend(
            {
                "t": _compact_operational_text(report.text, limit=90),
                "s": [aliases[report.source_ref]],
            }
            for report in self.recent_reports[:4]
        )

        findings = payload["f"]
        assert isinstance(findings, list)
        severity_alias = {"info": "i", "warning": "w", "critical": "c"}
        findings.extend(
            {
                "t": finding.finding_type,
                "v": severity_alias[finding.severity],
                "m": _compact_operational_text(get_current_catalog().text(finding.message_key, **dict(finding.message_values)), limit=100),
                "s": [aliases[finding.source_refs[0]]],
            }
            for finding in self.findings
        )
        return payload


@dataclass(frozen=True)
class OperationalReasoning:
    """Validated reasoning output plus non-user-facing execution metadata."""

    facts: tuple[tuple[str, tuple[str, ...]], ...]
    assessments: tuple[OperationalAssessment, ...]
    recommendations: tuple[RecommendedAction, ...]
    model_call_count: int
    fallback: bool = False
    rejected_claim_count: int = 0
    failure_kind: str | None = None

    def provenance(self) -> dict:
        return {
            "model_call_count": self.model_call_count,
            "fallback": self.fallback,
            "rejected_claim_count": self.rejected_claim_count,
            "accepted_fact_count": len(self.facts),
            "accepted_assessment_count": len(self.assessments),
            "accepted_recommendation_count": len(self.recommendations),
            "accepted_source_refs": sorted({
                source_ref
                for _, refs in self.facts
                for source_ref in refs
            } | {
                source_ref
                for assessment in self.assessments
                for source_ref in assessment.supporting_source_refs
            } | {
                source_ref
                for recommendation in self.recommendations
                for source_ref in recommendation.supporting_source_refs
            }),
            "failure_kind": self.failure_kind,
        }


@dataclass(frozen=True)
class SituationalSnapshot:
    """Authoritative, structured state used by situational-picture rendering."""

    cameras: CameraSnapshot | None
    drones: DroneSnapshot | None
    team: TeamSnapshot | None
    recent_count: int | None
    relevant_recent_events: tuple[str, ...]
    inconsistencies: tuple[str, ...]
    generated_at: str
    findings: tuple[OperationalFinding, ...] = ()
    recent_reports: tuple[RecentOperationalReport, ...] = ()

    def provenance(self) -> dict:
        sections = {}
        for name in ("cameras", "drones", "team"):
            section = getattr(self, name)
            if section is not None:
                sections[name] = {
                    "source": section.provenance.source,
                    "as_of": section.provenance.as_of,
                    "scope": section.provenance.scope,
                    "operational_scope": section.provenance.operational_scope,
                    "status": section.status,
                }
        return {
            "generated_at": self.generated_at,
            "sections": sections,
            "inconsistencies": list(self.inconsistencies),
            "findings": [
                {
                    "finding_type": finding.finding_type,
                    "severity": finding.severity,
                    "source_refs": list(finding.source_refs),
                    "message_key": finding.message_key,
                    "suggested_action_key": finding.suggested_action_key,
                }
                for finding in self.findings
            ],
            "recent_reports": [
                {
                    "source_ref": report.source_ref,
                    "received_at": report.received_at,
                }
                for report in self.recent_reports
            ],
        }


@dataclass(frozen=True)
class SituationalPicture:
    text: str
    reports: tuple[DomainReport, ...]
    generated_at: str
    plan: PicturePlan
    snapshot: SituationalSnapshot | None = None
    reasoning: OperationalReasoning | None = None

    def provenance(self) -> dict:
        payload = {
            "generated_at": self.generated_at,
            "recent_events_hours": self.plan.recent_events_hours,
            "planned_by_model": self.plan.planned_by_model,
            "domains": [
                {"domain": report.domain, "query": report.query, "succeeded": report.succeeded}
                for report in self.reports
            ],
        }
        if self.plan.scope is not None:
            payload["query_scope"] = self.plan.scope.as_dict()
        if self.snapshot is not None:
            payload["snapshot"] = self.snapshot.provenance()
        if self.reasoning is not None:
            payload["reasoning"] = self.reasoning.provenance()
        return payload


def _current_time_label(history_query_service: "HistoryQueryService | None", now: datetime) -> str:
    context_factory = getattr(history_query_service, "planning_context", None)
    if callable(context_factory):
        try:
            context = context_factory()
            label = context.get("current_time_local")
            zone = context.get("timezone")
            if label:
                return f"{label} ({zone})" if zone else str(label)
        except Exception:  # pragma: no cover - a broken clock must not block the picture
            pass
    return now.astimezone(timezone.utc).isoformat(timespec="seconds")


def _registry_agent(registry: "AgentRegistry", name: str):
    try:
        return registry.get(name)
    except Exception:
        return None


def _section_provenance(source: str, now: datetime, scope: str = "global", operational_scope: str = "LIVE") -> SnapshotProvenance:
    return SnapshotProvenance(source=source, as_of=storage_timestamp(now), scope=scope, operational_scope=operational_scope)


def _read_scoped(store, method_name: str, *args, scope: OperationalScope, **kwargs):
    """Call a scoped store while retaining compatibility with read-only test doubles."""

    method = getattr(store, method_name)
    try:
        return method(*args, scope=scope, **kwargs)
    except TypeError as exc:
        if "scope" not in str(exc):
            raise
        return method(*args, **kwargs)


def _build_camera_snapshot(store, *, now: datetime, area: str | None, operational_scope: OperationalScope) -> CameraSnapshot:
    provenance = _section_provenance("surveillance_store.list_cameras", now, area or "global", operational_scope.key)
    try:
        cameras = list(_read_scoped(store, "list_cameras", area=area, scope=operational_scope))
    except Exception:
        return CameraSnapshot(None, None, None, None, "unknown", provenance, None, None)

    counts = {"active": 0, "degraded": 0, "offline": 0, "unknown": 0}
    abnormal_cameras: list[CameraOperationalFact] = []
    for camera in cameras:
        status = str(camera.get("status", "")).casefold()
        if status == "active":
            counts["active"] += 1
        elif status in {"degraded", "offline"}:
            counts[status] += 1
            abnormal_cameras.append(
                CameraOperationalFact(
                    camera_id=str(camera.get("camera_id") or "unknown"),
                    area=str(camera.get("area")) if camera.get("area") is not None else None,
                    status=status,
                )
            )
        else:
            counts["unknown"] += 1
            abnormal_cameras.append(
                CameraOperationalFact(
                    camera_id=str(camera.get("camera_id") or "unknown"),
                    area=str(camera.get("area")) if camera.get("area") is not None else None,
                    status="unknown",
                )
            )
    return CameraSnapshot(
        total=len(cameras),
        active=counts["active"],
        inactive=counts["degraded"] + counts["offline"],
        unknown=counts["unknown"],
        status="inconsistent" if sum(counts.values()) != len(cameras) else "ok",
        provenance=provenance,
        degraded=counts["degraded"],
        offline=counts["offline"],
        abnormal_cameras=tuple(abnormal_cameras),
    )


def _build_drone_snapshot(store, *, now: datetime, area: str | None, operational_scope: OperationalScope) -> DroneSnapshot:
    provenance = _section_provenance("surveillance_store.list_drones+get_active_missions", now, area or "global", operational_scope.key)
    try:
        drones = list(_read_scoped(store, "list_drones", scope=operational_scope))
        missions = list(_read_scoped(store, "get_active_missions", scope=operational_scope))
        if area:
            normalized_area = area.casefold()
            drones = [drone for drone in drones if str(drone.get("current_area", "")).casefold() == normalized_area]
            missions = [mission for mission in missions if str(mission.get("target_area", "")).casefold() == normalized_area]
    except Exception:
        return DroneSnapshot(None, None, None, None, None, None, None, "unknown", provenance)

    counts = {"ready": 0, "airborne": 0, "charging": 0, "maintenance": 0, "unknown": 0}
    by_id = {}
    for drone in drones:
        drone_id = drone.get("drone_id")
        if drone_id:
            by_id[str(drone_id)] = drone
        status = str(drone.get("status", "")).casefold()
        key = {
            "in_flight": "airborne",
            "ready": "ready",
            "charging": "charging",
            "maintenance": "maintenance",
        }.get(status, "unknown")
        counts[key] += 1

    active_ids = {str(mission.get("mission_id")) for mission in missions if mission.get("mission_id")}
    inconsistencies = []
    for drone in drones:
        assigned = drone.get("assigned_mission_id")
        if str(drone.get("status", "")).casefold() == "in_flight" and assigned and str(assigned) not in active_ids:
            inconsistencies.append("airborne drone references a mission absent from the active mission store")
    for mission in missions:
        drone = by_id.get(str(mission.get("drone_id")))
        if drone is None or str(drone.get("status", "")).casefold() != "in_flight":
            inconsistencies.append("active mission references a drone that is not airborne")

    return DroneSnapshot(
        total=len(drones),
        ready=counts["ready"],
        airborne=counts["airborne"],
        charging=counts["charging"],
        maintenance=counts["maintenance"],
        unknown=counts["unknown"],
        active_missions=len(missions),
        status="inconsistent" if inconsistencies else "ok",
        provenance=provenance,
    )


def _build_team_snapshot(store, *, now: datetime, operational_scope: OperationalScope) -> TeamSnapshot:
    provenance = _section_provenance("team_status_store.availability_snapshot", now, "global", operational_scope.key)
    try:
        entries = list(_read_scoped(store, "availability_snapshot", storage_timestamp(now), scope=operational_scope))
    except Exception:
        return TeamSnapshot(None, None, None, None, None, "unknown", provenance)

    counts = {"available": 0, "unavailable": 0, "not_reported": 0, "pending_identity": 0}
    for entry in entries:
        status = str(entry.get("availability", "")).casefold()
        if status == "awaiting_response":
            counts["not_reported"] += 1
        elif status in counts:
            counts[status] += 1
        else:
            counts["pending_identity"] += 1
    total = len(entries)
    consistent = sum(counts.values()) == total
    try:
        operational_state = _read_scoped(store, "operational_state", scope=operational_scope)
    except Exception:
        operational_state = None
    operational_manpower = operational_state.get("manpower_count") if isinstance(operational_state, dict) else None
    effective_manpower = max(0, operational_manpower - counts["unavailable"]) if isinstance(operational_manpower, int) else None
    resources = tuple(
        f"{item.get('name')}-{item.get('count')}"
        for item in (operational_state.get("resources", ()) if isinstance(operational_state, dict) else ())
        if isinstance(item, dict) and item.get("name") and item.get("count") is not None
    )
    return TeamSnapshot(
        total=total,
        available=counts["available"],
        unavailable=counts["unavailable"],
        not_reported=counts["not_reported"],
        pending_identity=counts["pending_identity"],
        status="ok" if consistent else "inconsistent",
        provenance=provenance,
        operational_manpower=operational_manpower,
        effective_manpower=effective_manpower,
        operational_resources=resources,
    )


def _state_source(section_name: str, provenance: SnapshotProvenance) -> str:
    return f"state:{section_name}:{provenance.source}"


def derive_operational_findings(
    cameras: CameraSnapshot | None,
    drones: DroneSnapshot | None,
    team: TeamSnapshot | None,
) -> tuple[OperationalFinding, ...]:
    """Derive only implications licensed by verified counts and section state."""

    findings: list[OperationalFinding] = []

    if cameras is not None:
        source_refs = (_state_source("cameras", cameras.provenance),)
        if cameras.status == "unknown" or cameras.unknown:
            findings.append(OperationalFinding(
                "data_quality", "warning", source_refs, "orchestrator.picture.finding.cameras_unknown"
            ))
        elif cameras.status == "inconsistent":
            findings.append(OperationalFinding(
                "data_quality", "warning", source_refs, "orchestrator.picture.finding.cameras_inconsistent"
            ))
        elif cameras.total and cameras.active == cameras.total:
            findings.append(OperationalFinding(
                "coverage",
                "info",
                source_refs,
                "orchestrator.picture.finding.cameras_all_active",
                (("count", cameras.total),),
            ))
        elif cameras.total is not None and cameras.active is not None:
            findings.append(OperationalFinding(
                "coverage",
                "warning",
                source_refs,
                "orchestrator.picture.finding.cameras_gap",
                (("active", cameras.active), ("total", cameras.total)),
            ))
        if cameras.degraded:
            findings.append(OperationalFinding(
                "coverage", "warning", source_refs,
                "orchestrator.picture.finding.cameras_degraded",
                (("count", cameras.degraded),),
            ))
        if cameras.offline:
            findings.append(OperationalFinding(
                "coverage", "warning", source_refs,
                "orchestrator.picture.finding.cameras_offline",
                (("count", cameras.offline),),
            ))

    if drones is not None:
        source_refs = (_state_source("drones", drones.provenance),)
        if drones.status == "unknown" or drones.unknown:
            findings.append(OperationalFinding(
                "data_quality", "warning", source_refs, "orchestrator.picture.finding.drones_unknown"
            ))
        elif drones.status == "inconsistent":
            findings.append(OperationalFinding(
                "data_quality", "warning", source_refs, "orchestrator.picture.finding.drones_inconsistent"
            ))
        elif drones.ready is not None and drones.ready >= 1:
            findings.append(OperationalFinding(
                "readiness",
                "info",
                source_refs,
                "orchestrator.picture.finding.drones_ready",
                (("count", drones.ready),),
            ))
        elif drones.ready == 0:
            findings.append(OperationalFinding(
                "readiness", "warning", source_refs, "orchestrator.picture.finding.drones_none_ready"
            ))

    if team is not None:
        source_refs = (_state_source("team", team.provenance),)
        if team.status == "unknown":
            findings.append(OperationalFinding(
                "data_quality", "warning", source_refs, "orchestrator.picture.finding.team_unknown"
            ))
        elif team.status == "inconsistent":
            findings.append(OperationalFinding(
                "data_quality", "warning", source_refs, "orchestrator.picture.finding.team_inconsistent"
            ))
        else:
            if team.available == 0:
                findings.append(OperationalFinding(
                    "availability", "warning", source_refs, "orchestrator.picture.finding.team_no_confirmed"
                ))
            elif team.available is not None:
                findings.append(OperationalFinding(
                    "availability",
                    "info",
                    source_refs,
                    "orchestrator.picture.finding.team_available",
                    (("count", team.available),),
                ))

            if team.not_reported is not None and team.not_reported > 0:
                values = (("count", team.not_reported),)
                findings.append(OperationalFinding(
                    "availability",
                    "warning",
                    source_refs,
                    "orchestrator.picture.finding.team_not_reported",
                    values,
                    "orchestrator.picture.recommendation.collect_availability",
                    values,
                ))
            if team.pending_identity is not None and team.pending_identity > 0:
                findings.append(OperationalFinding(
                    "data_quality",
                    "warning",
                    source_refs,
                    "orchestrator.picture.finding.team_pending_identity",
                    (("count", team.pending_identity),),
                ))

    return tuple(findings)


def _recent_committed_reports(
    history_query_service: "HistoryQueryService | None",
    *,
    now: datetime,
    sender_identity_filter: str | None,
    scenario_id: str | None,
    scenario_run_id: str | None,
) -> tuple[RecentOperationalReport, ...]:
    """Read committed report facts through the history service's public read path."""

    history_started = time.perf_counter()
    reader = getattr(history_query_service, "recent_committed_events", None)
    if not callable(reader):
        return ()
    try:
        events = reader(
            now=now,
            hours=DEFAULT_RECENT_EVENTS_HOURS,
            sender_identity_filter=sender_identity_filter,
            scenario_id=scenario_id,
            scenario_run_id=scenario_run_id,
            limit=RECENT_EVENTS_LIMIT,
        )
    except Exception:
        logger.warning(
            "committed report lookup failed for situational picture",
            extra={
                "event": "picture_committed_reports_failed",
                "history_seconds": round(time.perf_counter() - history_started, 6),
                "trace_id": get_trace_id(),
            },
        )
        return ()

    candidates: list[tuple[str, RecentOperationalReport]] = []
    seen_texts: set[str] = set()
    for event in events:
        if event.get("outcome") != "succeeded":
            continue
        # A retracted report is still history, but it is no longer current and
        # must never be stated to a commander as a live fact.
        if event.get("superseded_by_event_id"):
            continue
        classification = str(event.get("classification") or "").casefold()
        if not classification.endswith("_report"):
            continue
        text = str(event.get("description") or event.get("raw_text") or "").strip()
        if not text:
            continue
        dedup_key = " ".join(text.casefold().split())
        if dedup_key in seen_texts:
            continue
        seen_texts.add(dedup_key)
        domain = classification.removesuffix("_report") or "other"
        candidates.append(
            (domain, RecentOperationalReport(
                text=text,
                source_ref=f"event:{event.get('event_id', 'unknown')}",
                received_at=str(event.get("received_at") or ""),
                domain=domain,
            ))
        )
    # The simulation history reader returns a run in scenario order, while the
    # live reader returns newest-first. Select the latest committed evidence
    # per domain before filling the bounded report budget.
    ordered_candidates = list(reversed(candidates)) if scenario_id and scenario_run_id else candidates
    selected: list[RecentOperationalReport] = []
    selected_domains: set[str] = set()
    for domain, report in ordered_candidates:
        if domain not in selected_domains:
            selected.append(report)
            selected_domains.add(domain)
        if len(selected) >= RECENT_EVENTS_LIMIT:
            break
    if len(selected) < RECENT_EVENTS_LIMIT:
        selected_refs = {report.source_ref for report in selected}
        selected.extend(
            report for _, report in ordered_candidates
            if report.source_ref not in selected_refs and len(selected) < RECENT_EVENTS_LIMIT
        )
    reports = selected
    logger.info(
        "committed report context built",
        extra={
            "event": "picture_committed_reports_built",
            "history_seconds": round(time.perf_counter() - history_started, 6),
            "report_count": len(reports),
            "trace_id": get_trace_id(),
        },
    )
    return tuple(reports)


def build_typed_snapshot(
    registry: "AgentRegistry",
    *,
    now: datetime | None = None,
    area: str | None = None,
    history_query_service: "HistoryQueryService | None" = None,
    sender_identity_filter: str | None = None,
    scenario_id: str | None = None,
    scenario_run_id: str | None = None,
    scenario_time: str | None = None,
    scope: SituationalQueryScope | None = None,
    operational_scope: OperationalScope | None = None,
) -> SituationalSnapshot | None:
    """Read authoritative specialist stores and construct a validated snapshot.

    Missing stores return ``None`` so existing profiles with specialist fakes can
    continue using the legacy report pipeline; no model output is parsed as state.
    """

    if now is None and scenario_time:
        try:
            parsed_time = datetime.fromisoformat(str(scenario_time).replace("Z", "+00:00"))
            now = parsed_time if parsed_time.tzinfo else parsed_time.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            now = None
    now = now or datetime.now(timezone.utc)
    effective_scope = scope or SituationalQueryScope.overall_scope()
    if operational_scope is None and (scenario_id is not None or scenario_run_id is not None):
        operational_scope = OperationalScope.simulation(str(scenario_id or ""), str(scenario_run_id or ""))
    resolved_operational_scope = resolve_operational_scope(operational_scope)
    surveillance_agent = _registry_agent(registry, "surveillance_agent")
    team_agent = _registry_agent(registry, "team_status_agent")
    surveillance_store = getattr(surveillance_agent, "surveillance_store", None)
    team_store = getattr(team_agent, "status_store", None)
    if surveillance_store is None and team_store is None:
        return None

    cameras = (
        _build_camera_snapshot(surveillance_store, now=now, area=area, operational_scope=resolved_operational_scope)
        if surveillance_store and (effective_scope.overall or effective_scope.surveillance)
        else None
    )
    drones = (
        _build_drone_snapshot(surveillance_store, now=now, area=area, operational_scope=resolved_operational_scope)
        if surveillance_store and (effective_scope.overall or effective_scope.drones)
        else None
    )
    team = (
        _build_team_snapshot(team_store, now=now, operational_scope=resolved_operational_scope)
        if team_store and (effective_scope.overall or effective_scope.team)
        else None
    )
    inconsistencies: list[str] = []
    if drones is not None and drones.status == "inconsistent":
        inconsistencies.append("drone and mission stores disagree")
    if team is not None and team.status == "inconsistent":
        inconsistencies.append("team availability categories do not sum to total")
    if cameras is not None and cameras.status == "inconsistent":
        inconsistencies.append("camera status categories do not sum to total")

    findings = derive_operational_findings(cameras, drones, team)
    recent_reports = (
        _recent_committed_reports(
            history_query_service,
            now=now,
            sender_identity_filter=sender_identity_filter,
            scenario_id=resolved_operational_scope.scenario_id,
            scenario_run_id=resolved_operational_scope.scenario_run_id,
        )
        if effective_scope.overall or effective_scope.external_reports
        else ()
    )

    return SituationalSnapshot(
        cameras=cameras,
        drones=drones,
        team=team,
        recent_count=len(recent_reports),
        relevant_recent_events=tuple(report.text for report in recent_reports),
        inconsistencies=tuple(inconsistencies),
        generated_at=storage_timestamp(now),
        findings=findings,
        recent_reports=recent_reports,
    )


def build_operational_context(
    snapshot: SituationalSnapshot,
    *,
    query_scope: SituationalQueryScope,
    current_time: str,
    operational_scope: OperationalScope | None = None,
) -> OperationalContext:
    """Select only bounded typed facts and committed current-run reports."""

    source_refs: list[str] = []
    for section_name in ("cameras", "drones", "team"):
        section = getattr(snapshot, section_name)
        if section is not None:
            source_refs.append(_state_source(section_name, section.provenance))
    for report in snapshot.recent_reports:
        source_refs.append(report.source_ref)
    for finding in snapshot.findings:
        source_refs.extend(finding.source_refs)

    scope_key = operational_scope.key if operational_scope is not None else "LIVE"
    if operational_scope is None:
        for section_name in ("cameras", "drones", "team"):
            section = getattr(snapshot, section_name)
            if section is not None:
                scope_key = section.provenance.operational_scope
                break

    return OperationalContext(
        query_scope=query_scope,
        current_time=current_time,
        cameras=snapshot.cameras,
        drones=snapshot.drones,
        team=snapshot.team,
        recent_reports=snapshot.recent_reports,
        findings=snapshot.findings,
        inconsistencies=snapshot.inconsistencies,
        source_refs=tuple(dict.fromkeys(source_refs)),
        operational_scope=scope_key,
    )


def _reasoning_source_refs(context: OperationalContext) -> set[str]:
    return set(context.source_refs)


def _compact_text(value: object, *, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"reasoning field {field_name!r} must be a non-empty string")
    return " ".join(value.split())


def _execution_markers() -> tuple[str, ...]:
    markers: list[str] = []
    for language in ("en", "he"):
        from messages import get_catalog

        markers.extend(
            marker.strip().casefold()
            for marker in get_catalog(language).text(
                "orchestrator.picture.reasoning.execution_markers"
            ).split("|")
            if marker.strip()
        )
    return tuple(dict.fromkeys(markers))


def _contains_execution_claim(text: str) -> bool:
    normalized = " ".join(text.casefold().split())
    return any(marker in normalized for marker in _execution_markers())


def _validate_reasoning_refs(raw_refs: object, *, field_name: str, allowed: set[str]) -> tuple[str, ...]:
    if not isinstance(raw_refs, list) or not raw_refs or not all(isinstance(item, str) for item in raw_refs):
        trace = get_active_provider_diagnostic_trace()
        if trace is not None and trace.schema_validation_success is not False:
            trace.record_canonical_success()
            trace.record_provenance_failure(
                "missing_or_invalid_source_refs",
                missing_ref_count=1,
                claim_type=field_name.split("[", 1)[0],
            )
        raise ValueError(f"{field_name} must contain at least one source_ref")
    refs = tuple(dict.fromkeys(item.strip() for item in raw_refs if item.strip()))
    if not refs or any(item not in allowed for item in refs):
        trace = get_active_provider_diagnostic_trace()
        if trace is not None and trace.schema_validation_success is not False:
            unknown_count = sum(item not in allowed for item in refs)
            trace.record_canonical_success()
            trace.record_provenance_failure(
                "unknown_source_refs",
                invalid_ref_count=unknown_count,
                missing_ref_count=1 if not refs else 0,
                unknown_ref_count=unknown_count,
                claim_type=field_name.split("[", 1)[0],
            )
        raise ValueError(f"{field_name} contains an unknown source_ref")
    return refs


def _reasoning_schema_issue(value: object, schema: dict, path: str = "$") -> tuple[str, str] | None:
    expected = schema.get("type")
    expected_types = expected if isinstance(expected, list) else [expected]
    type_matches = any(
        (item == "object" and isinstance(value, dict))
        or (item == "array" and isinstance(value, list))
        or (item == "string" and isinstance(value, str))
        or (item == "boolean" and type(value) is bool)
        or (item == "null" and value is None)
        for item in expected_types
    )
    if expected is not None and not type_matches:
        return "type_mismatch", path
    enum = schema.get("enum")
    if enum is not None and value not in enum:
        return "enum_violation", path
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            return "string_too_short", path
        if len(value) > schema.get("maxLength", len(value)):
            return "string_too_long", path
        pattern = schema.get("pattern")
        if pattern is not None and re.fullmatch(pattern, value) is None:
            return "pattern_mismatch", path
    if isinstance(value, dict):
        for required in schema.get("required", ()):
            if required not in value:
                return "missing_required_field", f"{path}.{required}"
        if schema.get("additionalProperties") is False:
            allowed = set(schema.get("properties", {}))
            for key in value:
                if key not in allowed:
                    return "additional_property", f"{path}.{key}"
        for key, child_schema in schema.get("properties", {}).items():
            if key in value:
                issue = _reasoning_schema_issue(value[key], child_schema, f"{path}.{key}")
                if issue is not None:
                    return issue
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            return "array_too_short", path
        if len(value) > schema.get("maxItems", len(value)):
            return "array_too_long", path
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                issue = _reasoning_schema_issue(item, item_schema, f"{path}[{index}]")
                if issue is not None:
                    return issue
    return None


def _wire_source_refs(raw_refs: object, aliases: dict[str, str], *, field_name: str) -> list[str]:
    if not isinstance(raw_refs, list):
        raise ValueError(f"{field_name} source aliases are not a list")
    if len(raw_refs) != len(set(raw_refs)):
        trace = get_active_provider_diagnostic_trace()
        if trace is not None:
            trace.record_canonical_success()
            trace.record_provenance_failure("duplicate_source_alias", invalid_ref_count=1, claim_type=field_name)
        raise ValueError(f"{field_name} contains duplicate source aliases")
    return [aliases.get(alias, alias) if isinstance(alias, str) else alias for alias in raw_refs]


def _expand_reasoning_wire_payload(payload: dict, context: OperationalContext) -> dict:
    aliases = {alias: source_ref for source_ref, alias in _compact_source_aliases(context).items()}
    return {
        "facts": [
            {
                "text": item["text"],
                "source_refs": _wire_source_refs(item["source_aliases"], aliases, field_name=f"facts[{index}]"),
            }
            for index, item in enumerate(payload["facts"])
        ],
        "assessments": [
            {
                "conclusion": item["conclusion"],
                "supporting_source_refs": _wire_source_refs(item["source_aliases"], aliases, field_name=f"assessments[{index}]"),
                "confidence": item["confidence"],
                "qualification": item.get("qualification") or "",
                "affected_domains": item["affected_domains"],
                "priority": item["priority"],
            }
            for index, item in enumerate(payload["assessments"])
        ],
        "recommendations": [
            {
                "description": item["description"],
                "rationale": item["rationale"],
                "supporting_source_refs": _wire_source_refs(item["source_aliases"], aliases, field_name=f"recommendations[{index}]"),
                "priority": item["priority"],
                "possible_capability": item["possible_capability"],
                "requires_approval": item["requires_approval"],
            }
            for index, item in enumerate(payload["recommendations"])
        ],
    }


def _parse_reasoning_json(raw_text: str) -> dict:
    trace = get_active_provider_diagnostic_trace()
    if not isinstance(raw_text, str) or not raw_text.strip():
        if trace is not None:
            error = ValueError("reasoning response is empty")
            trace.record_json_failure(error, raw_text)
        raise ValueError("reasoning response is empty")
    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        if trace is not None:
            trace.record_json_failure(exc, raw_text)
        raise ValueError("reasoning response is not a JSON object") from exc
    if not isinstance(payload, dict):
        if trace is not None:
            error = ValueError("reasoning response is not a JSON object")
            trace.record_json_failure(error, raw_text)
        raise ValueError("reasoning response is not a JSON object")
    if trace is not None:
        trace.record_json_success(payload)
    return payload


def _reject_internal_reference_leak(text: str, refs: tuple[str, ...]) -> None:
    if any(source_ref in text for source_ref in refs):
        trace = get_active_provider_diagnostic_trace()
        if trace is not None and trace.schema_validation_success is not False:
            trace.record_canonical_success()
            trace.record_provenance_failure(
                "internal_source_ref_leak",
                invalid_ref_count=1,
                claim_type="text",
            )
        raise ValueError("reasoning output exposes an internal source_ref")


def _validate_reasoning_payload(
    payload: dict,
    context: OperationalContext,
    *,
    wire_schema_validated: bool = False,
) -> OperationalReasoning:
    trace = get_active_provider_diagnostic_trace()
    if trace is not None and not wire_schema_validated:
        schema_issue = _reasoning_schema_issue(payload, _REASONING_SCHEMA)
        if schema_issue is None:
            trace.record_schema_success()
        else:
            trace.record_schema_failure(*schema_issue)
    allowed_refs = _reasoning_source_refs(context)
    facts_payload = payload.get("facts")
    assessments_payload = payload.get("assessments")
    recommendations_payload = payload.get("recommendations")
    if not all(isinstance(value, list) for value in (facts_payload, assessments_payload, recommendations_payload)):
        raise ValueError("reasoning output arrays are required")
    if not facts_payload and not assessments_payload and not recommendations_payload:
        raise ValueError("reasoning output contains no grounded content")

    facts: list[tuple[str, tuple[str, ...]]] = []
    for index, item in enumerate(facts_payload):
        if not isinstance(item, dict):
            raise ValueError(f"fact {index} is not an object")
        text = _compact_text(item.get("text"), field_name=f"facts[{index}].text")
        if _contains_execution_claim(text):
            raise ValueError("fact contains an unsupported execution claim")
        refs = _validate_reasoning_refs(item.get("source_refs"), field_name=f"facts[{index}].source_refs", allowed=allowed_refs)
        _reject_internal_reference_leak(text, refs)
        facts.append((text, refs))

    assessments: list[OperationalAssessment] = []
    for index, item in enumerate(assessments_payload):
        if not isinstance(item, dict):
            raise ValueError(f"assessment {index} is not an object")
        conclusion = _compact_text(item.get("conclusion"), field_name=f"assessments[{index}].conclusion")
        qualification = item.get("qualification")
        if not isinstance(qualification, str):
            raise ValueError(f"assessment {index}.qualification must be a string")
        if _contains_execution_claim(conclusion):
            raise ValueError("assessment contains an unsupported execution claim")
        refs = _validate_reasoning_refs(
            item.get("supporting_source_refs"),
            field_name=f"assessments[{index}].supporting_source_refs",
            allowed=allowed_refs,
        )
        _reject_internal_reference_leak(conclusion, refs)
        _reject_internal_reference_leak(qualification, refs)
        domains = item.get("affected_domains")
        if not isinstance(domains, list) or not all(isinstance(domain, str) for domain in domains):
            raise ValueError(f"assessment {index}.affected_domains must be a list")
        if not all(domain in {"team", "surveillance", "drones", "external_reports", "cross_domain"} for domain in domains):
            raise ValueError(f"assessment {index}.affected_domains contains an unknown domain")
        confidence = item.get("confidence")
        priority = item.get("priority")
        if confidence not in {"low", "medium", "high"} or priority not in {"low", "medium", "high"}:
            raise ValueError(f"assessment {index} has an invalid confidence or priority")
        assessments.append(
            OperationalAssessment(
                conclusion=conclusion,
                supporting_source_refs=refs,
                confidence=confidence,
                qualification=" ".join(qualification.split()),
                affected_domains=tuple(dict.fromkeys(domains)),
                priority=priority,
            )
        )

    recommendations: list[RecommendedAction] = []
    for index, item in enumerate(recommendations_payload):
        if not isinstance(item, dict):
            raise ValueError(f"recommendation {index} is not an object")
        description = _compact_text(item.get("description"), field_name=f"recommendations[{index}].description")
        rationale = _compact_text(item.get("rationale"), field_name=f"recommendations[{index}].rationale")
        if _contains_execution_claim(description) or _contains_execution_claim(rationale):
            raise ValueError("recommendation contains an unsupported execution claim")
        refs = _validate_reasoning_refs(
            item.get("supporting_source_refs"),
            field_name=f"recommendations[{index}].supporting_source_refs",
            allowed=allowed_refs,
        )
        _reject_internal_reference_leak(description, refs)
        _reject_internal_reference_leak(rationale, refs)
        priority = item.get("priority")
        requires_approval = item.get("requires_approval")
        possible_capability = item.get("possible_capability")
        if priority not in {"low", "medium", "high"} or type(requires_approval) is not bool:
            raise ValueError(f"recommendation {index} has an invalid priority or approval flag")
        if possible_capability is not None and (
            not isinstance(possible_capability, str) or not possible_capability.strip()
        ):
            raise ValueError(f"recommendation {index}.possible_capability must be a string or null")
        if possible_capability:
            from orchestrator.capabilities import CAPABILITY_DESCRIPTORS

            descriptor_by_name = {descriptor.name: descriptor for descriptor in CAPABILITY_DESCRIPTORS}
            descriptor = descriptor_by_name.get(possible_capability.strip())
            if descriptor is None:
                raise ValueError(f"recommendation {index}.possible_capability is unknown")
            if (descriptor.has_side_effects or descriptor.requires_human_review) and not requires_approval:
                raise ValueError(f"recommendation {index}.possible_capability requires approval")
        recommendations.append(
            RecommendedAction(
                description=description,
                rationale=rationale,
                supporting_source_refs=refs,
                priority=priority,
                possible_capability=possible_capability.strip() if possible_capability else None,
                requires_approval=requires_approval,
            )
        )

    if trace is not None:
        trace.record_canonical_success()
        trace.record_provenance_success()
    return OperationalReasoning(
        facts=tuple(facts),
        assessments=tuple(assessments),
        recommendations=tuple(recommendations),
        model_call_count=1,
    )


def reason_over_operational_context(
    main_agent: "MainAgent",
    context: OperationalContext,
    *,
    raw_text: str,
) -> OperationalReasoning:
    """Make exactly one bounded call and return validated output or fallback metadata."""

    prompt = SITUATIONAL_PICTURE_REASONING_INSTRUCTION.format(
        request_json=json.dumps(raw_text or "", ensure_ascii=False),
        context_json=json.dumps(context.provider_prompt_payload(), ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        max_facts=REASONING_MAX_FACTS,
        max_assessments=REASONING_MAX_ASSESSMENTS,
        max_recommendations=REASONING_MAX_RECOMMENDATIONS,
    )
    started = time.perf_counter()
    schema_issue: tuple[str, str] | None = None
    try:
        with stage_context("situational_picture_reasoning"):
            result = main_agent.process(prompt, [], invocation_policy=_REASONING_POLICY)
        if result.status != "success":
            raise ValueError("model returned an unusable reasoning result")
        wire_payload = _parse_reasoning_json(result.text)
        schema_issue = _reasoning_schema_issue(wire_payload, _REASONING_SCHEMA)
        if schema_issue is not None:
            trace = get_active_provider_diagnostic_trace()
            if trace is not None:
                trace.record_schema_failure(*schema_issue)
            raise ValueError(f"reasoning wire schema invalid: {schema_issue[0]} at {schema_issue[1]}")
        trace = get_active_provider_diagnostic_trace()
        if trace is not None:
            trace.record_schema_success()
        payload = _expand_reasoning_wire_payload(wire_payload, context)
        reasoning = _validate_reasoning_payload(payload, context, wire_schema_validated=True)
        logger.info(
            "bounded situational reasoning completed",
            extra={
                "event": "situational_reasoning_completed",
                "model_call_count": 1,
                "reasoning_seconds": round(time.perf_counter() - started, 6),
                "accepted_assessments": len(reasoning.assessments),
                "accepted_recommendations": len(reasoning.recommendations),
                "trace_id": get_trace_id(),
            },
        )
        return reasoning
    except Exception as exc:
        trace = get_active_provider_diagnostic_trace()
        if trace is not None:
            if trace.first_failed_stage is None:
                trace.record_canonical_failure(type(exc).__name__)
            trace.record_fallback(
                trace.fallback_reason
                or {
                    "PROVIDER_RESPONSE": trace.provider_error_category or "provider_error",
                    "CONTENT_EXTRACTION": "response_extraction_failed",
                    "JSON_PARSE": trace.json_error_category or "json_parse_failed",
                    "SCHEMA_VALIDATION": "schema_validation_failed",
                    "CANONICAL_MODEL_VALIDATION": "canonical_validation_failed",
                    "PROVENANCE_VALIDATION": "provenance_validation_failed",
                }.get(trace.first_failed_stage or "", "unknown")
            )
        logger.warning(
            "bounded situational reasoning failed; using deterministic fallback: %s "
            "(stage=%s failure_kind=%s schema=%s@%s provenance=%s)",
            exc,
            trace.first_failed_stage if trace is not None else None,
            trace.fallback_reason if trace is not None else None,
            trace.schema_validation_error_category if trace is not None else None,
            trace.schema_validation_error_path if trace is not None else None,
            trace.provenance_claim_type if trace is not None else None,
            extra={
                "event": "situational_reasoning_fallback",
                "reason": str(exc),
                "schema_issue": schema_issue[0] if schema_issue is not None else None,
                "schema_path": schema_issue[1] if schema_issue is not None else None,
                "reasoning_seconds": round(time.perf_counter() - started, 6),
                "trace_id": get_trace_id(),
            },
        )
        if trace is not None:
            logger.warning(
                "situational reasoning fallback diagnostics stage=%s kind=%s schema=%s path=%s provenance=%s",
                trace.first_failed_stage,
                trace.fallback_reason,
                trace.schema_validation_error_category,
                trace.schema_validation_error_path,
                trace.provenance_claim_type,
                extra={"event": "situational_reasoning_fallback_diagnostics", "trace_id": get_trace_id()},
            )
        return OperationalReasoning(
            facts=(),
            assessments=(),
            recommendations=(),
            model_call_count=1,
            fallback=True,
            rejected_claim_count=1,
            failure_kind=(trace.fallback_reason if trace is not None else None)
            or "invalid_or_unavailable_model_output",
        )


def _render_reasoned_picture(context: OperationalContext, reasoning: OperationalReasoning) -> str:
    catalog = get_current_catalog()
    lines = [catalog.text("orchestrator.picture.reasoned.title")]
    if reasoning.facts:
        lines.append(catalog.text("orchestrator.picture.reasoned.facts_header"))
        lines.extend(f"- {text}" for text, _ in reasoning.facts)
    if reasoning.assessments:
        lines.append(catalog.text("orchestrator.picture.reasoned.assessments_header"))
        lines.extend(
            f"- {assessment.conclusion}{(' ' + assessment.qualification) if assessment.qualification else ''}"
            for assessment in reasoning.assessments
        )
    if reasoning.recommendations:
        lines.append(catalog.text("orchestrator.picture.reasoned.recommendations_header"))
        lines.extend(f"- {recommendation.description}" for recommendation in reasoning.recommendations)
    return "\n".join(line for line in lines if line)


def operational_facts_for_render(snapshot: SituationalSnapshot) -> tuple[RecentOperationalReport, ...]:
    """The committed facts a picture should state that no section already states.

    Everything here is already scoped, succeeded-only and bounded by the time it
    reaches the snapshot — this only removes what a structured section reports
    authoritatively, and keeps the remainder short enough to read. It performs
    no history lookup of its own.
    """

    represented: set[str] = set()

    if snapshot.cameras is not None and snapshot.cameras.status != "unknown":
        represented.add("surveillance")
    if snapshot.drones is not None and snapshot.drones.status != "unknown":
        represented.add("drone")
    if snapshot.team is not None and snapshot.team.status != "unknown":
        represented.add("team_attendance")
        if snapshot.team.operational_manpower is not None or snapshot.team.operational_resources:
            represented.add("team_resource")

    kept = [report for report in snapshot.recent_reports if report.domain not in represented]

    return tuple(kept[:FALLBACK_OPERATIONAL_FACT_LIMIT])


def _render_reasoning_fallback(snapshot: SituationalSnapshot) -> str:
    """Render the picture when bounded reasoning is unavailable.

    Committed operational facts are authoritative state already selected into
    this scoped snapshot, not a history query and not model output, so they are
    stated. Nothing is advised: no recommendation is offered when nothing
    reasoned, and no action is claimed.
    """

    return render_typed_snapshot(
        snapshot,
        include_findings=True,
        include_recent_reports=True,
        include_recommendations=False,
    )


def render_typed_snapshot(
    snapshot: SituationalSnapshot,
    *,
    include_findings: bool = True,
    include_recent_reports: bool = True,
    include_recommendations: bool = True,
) -> str:
    """Render localized facts and findings without model-authored claims."""

    catalog = get_current_catalog()
    # The roster label belongs to the active organization type: a fire crew is
    # not a readiness team, and rendering one as the other produced the
    # contradictory picture this was changed to prevent.
    profile = current_operational_profile()
    roster_label = catalog.text(
        profile.roster_label_key if profile is not None else "profile.response_team.roster"
    )
    lines = [catalog.text("orchestrator.picture.typed.title")]

    if snapshot.cameras is not None:
        section = snapshot.cameras
        if section.status == "unknown":
            lines.append(catalog.text("orchestrator.picture.typed.cameras_unknown"))
        else:
            lines.append(catalog.text(
                "orchestrator.picture.typed.cameras",
                total=section.total,
                active=section.active,
                degraded=section.degraded,
                offline=section.offline,
                unknown=section.unknown,
            ))

    if snapshot.drones is not None:
        section = snapshot.drones
        if section.status == "unknown":
            lines.append(catalog.text("orchestrator.picture.typed.drones_unknown"))
        else:
            lines.append(catalog.text(
                "orchestrator.picture.typed.drones",
                total=section.total,
                ready=section.ready,
                airborne=section.airborne,
                charging=section.charging,
                maintenance=section.maintenance,
                unknown=section.unknown,
                active_missions=section.active_missions,
            ))

    if snapshot.team is not None:
        section = snapshot.team
        if section.status == "unknown":
            lines.append(catalog.text("orchestrator.picture.typed.team_unknown", roster=roster_label))
        else:
            lines.append(catalog.text(
                "orchestrator.picture.typed.team",
                roster=roster_label,
                total=section.total,
                available=section.available,
                unavailable=section.unavailable,
                not_reported=section.not_reported,
                pending_identity=section.pending_identity,
            ))
            # Roster attendance and reported manpower are different facts. A
            # commander asking about forces and vehicles needs the committed
            # operational state too, not only who answered the daily check.
            if section.operational_manpower is not None:
                lines.append(catalog.text(
                    "orchestrator.picture.typed.manpower",
                    effective=section.effective_manpower,
                    reported=section.operational_manpower,
                ))
            if section.operational_resources:
                lines.append(catalog.text(
                    "orchestrator.picture.typed.resources",
                    resources=", ".join(section.operational_resources),
                ))

    if include_findings and snapshot.findings:
        lines.extend(("", catalog.text("orchestrator.picture.typed.findings_header")))
        lines.extend(
            catalog.text(
                "orchestrator.picture.typed.finding_line",
                text=catalog.text(finding.message_key, **dict(finding.message_values)),
            )
            for finding in snapshot.findings
        )

    operational_facts = operational_facts_for_render(snapshot) if include_recent_reports else ()
    if operational_facts:
        lines.extend(("", catalog.text("orchestrator.picture.typed.recent_reports_header")))
        lines.extend(
            catalog.text("orchestrator.picture.typed.recent_report", text=report.text)
            for report in operational_facts
        )

    recommendations = (
        [finding for finding in snapshot.findings if finding.suggested_action_key]
        if include_findings and include_recommendations
        else []
    )
    if recommendations:
        lines.extend(("", catalog.text("orchestrator.picture.typed.recommendations_header")))
        lines.extend(
            catalog.text(
                "orchestrator.picture.typed.recommendation_line",
                text=catalog.text(
                    finding.suggested_action_key,
                    **dict(finding.suggested_action_values),
                ),
            )
            for finding in recommendations
        )

    return "\n".join(lines)


def _readable_tools(agent, protocol: "Protocol") -> list[str]:
    approved = set(protocol.approved_tools)
    exposed = list(agent.exposed_tools())
    tools = [tool.name for tool in exposed if tool.name in approved and not tool.side_effecting]
    if not tools:
        tools = [tool.name for tool in exposed if not tool.side_effecting]
    return tools


def _specialists_json(protocol: "Protocol", registry: "AgentRegistry") -> str:
    specialists = []
    for agent_name in protocol.participating_agents:
        agent = registry.get(agent_name)
        readable = set(_readable_tools(agent, protocol))
        specialists.append(
            {
                "agent": agent_name,
                "role": agent.role,
                "tools": [
                    {"name": tool.name, "description": tool.description}
                    for tool in agent.exposed_tools()
                    if tool.name in readable
                ],
            }
        )
    return json.dumps(specialists, ensure_ascii=False, sort_keys=True)


def _default_plan(protocol: "Protocol") -> PicturePlan:
    default_query = get_current_catalog().text("orchestrator.picture.default_domain_query")
    return PicturePlan(
        briefings=tuple(DomainBriefing(agent_name, default_query) for agent_name in protocol.participating_agents),
        recent_events_hours=DEFAULT_RECENT_EVENTS_HOURS,
        planned_by_model=False,
    )


def _scoped_plan(protocol: "Protocol", scope: SituationalQueryScope) -> PicturePlan:
    if scope.overall:
        selected_agents = protocol.participating_agents
    else:
        selected_agents = tuple(
            agent_name
            for agent_name in protocol.participating_agents
            if (
                agent_name == "team_status_agent" and scope.team
            )
            or (
                agent_name == "surveillance_agent" and (scope.surveillance or scope.drones)
            )
        )
    default_query = get_current_catalog().text("orchestrator.picture.default_domain_query")
    return PicturePlan(
        briefings=tuple(DomainBriefing(agent_name, default_query) for agent_name in selected_agents),
        recent_events_hours=DEFAULT_RECENT_EVENTS_HOURS if (scope.overall or scope.external_reports) else 0,
        planned_by_model=False,
        scope=scope,
    )


def _extract_json_object(raw_text: str) -> dict:
    match = re.search(r"\{.*\}", raw_text, flags=re.DOTALL)
    if match is None:
        raise ValueError("no JSON object in plan response")
    parsed = json.loads(match.group(0))
    if not isinstance(parsed, dict):
        raise ValueError("plan response is not a JSON object")
    return parsed


def parse_picture_plan(raw_text: str, protocol: "Protocol") -> PicturePlan:
    """Turn the Main Agent's planning answer into a plan covering every participating agent.

    A specialist the model skipped gets the catalog's default domain question so no
    domain is ever silently dropped; an agent that does not participate is ignored;
    an out-of-range or missing history window falls back to the default window."""

    payload = _extract_json_object(raw_text)
    raw_domains = payload.get("domains")
    if not isinstance(raw_domains, list):
        raise ValueError("plan response has no 'domains' list")

    queries: dict[str, str] = {}
    for item in raw_domains:
        if not isinstance(item, dict):
            continue
        agent_name = str(item.get("agent", "")).strip()
        query = str(item.get("query", "")).strip()
        if agent_name in protocol.participating_agents and query and agent_name not in queries:
            queries[agent_name] = query

    default_query = get_current_catalog().text("orchestrator.picture.default_domain_query")
    briefings = tuple(
        DomainBriefing(agent_name, queries.get(agent_name, default_query))
        for agent_name in protocol.participating_agents
    )

    hours = payload.get("recent_events_hours", DEFAULT_RECENT_EVENTS_HOURS)
    try:
        hours = int(hours)
    except (TypeError, ValueError):
        hours = DEFAULT_RECENT_EVENTS_HOURS
    hours = max(MIN_RECENT_EVENTS_HOURS, min(MAX_RECENT_EVENTS_HOURS, hours))

    return PicturePlan(briefings=briefings, recent_events_hours=hours, planned_by_model=bool(queries))


def plan_situational_picture(
    main_agent: "MainAgent",
    protocol: "Protocol",
    registry: "AgentRegistry",
    raw_text: str,
    *,
    current_time: str,
) -> PicturePlan:
    """Ask the Main Agent what it needs from each specialist for this request."""

    prompt = SITUATIONAL_PICTURE_PLAN_INSTRUCTION.format(
        request_json=json.dumps(raw_text or protocol.description, ensure_ascii=False),
        current_time=current_time,
        specialists_json=_specialists_json(protocol, registry),
    )
    try:
        with stage_context("picture_planning"):
            result = main_agent.process(prompt, [], invocation_policy=_PLAN_POLICY)
        if result.status != "success":
            raise ValueError(f"planner did not answer: {result.text}")
        plan = parse_picture_plan(result.text, protocol)
    except Exception as exc:
        logger.warning(
            "situational picture planning failed; using default domain questions",
            extra={"event": "picture_plan_fallback", "reason": str(exc), "trace_id": get_trace_id()},
        )
        return _default_plan(protocol)

    logger.info(
        "situational picture planned",
        extra={
            "event": "picture_planned",
            "protocol": protocol.name,
            "recent_events_hours": plan.recent_events_hours,
            "domains": {briefing.agent_name: briefing.query for briefing in plan.briefings},
            "trace_id": get_trace_id(),
        },
    )
    return plan


def collect_recent_events(
    history_query_service: "HistoryQueryService",
    *,
    hours: int,
    now: datetime,
    sender_identity_filter: str | None,
    scenario_id: str | None = None,
    scenario_run_id: str | None = None,
    limit: int = RECENT_EVENTS_LIMIT,
) -> DomainReport:
    """Ask history for what was recorded in the last `hours` hours (newest first, by receipt time)."""

    catalog = get_current_catalog()
    question = catalog.text("orchestrator.picture.recent_events_question", hours=hours)
    spec = HistoryQuerySpec(
        operation="list",
        time_start=storage_timestamp(now - timedelta(hours=hours)),
        time_end=storage_timestamp(now),
        time_basis="received_at",
        scenario_id=scenario_id,
        scenario_run_id=scenario_run_id,
        order="newest",
        limit=limit,
    )
    try:
        with stage_context("picture_recent_events"):
            answer = history_query_service.query_spec(question, spec, sender_identity_filter=sender_identity_filter)
    except HistoryQueryError as exc:
        if "no stored events" in str(exc).lower():
            return DomainReport(
                RECENT_EVENTS_DOMAIN, question, catalog.text("orchestrator.picture.no_recent_events", hours=hours), True
            )
        logger.warning(
            "recent events unavailable for situational picture",
            extra={"event": "picture_recent_events_failed", "reason": str(exc), "trace_id": get_trace_id()},
        )
        return DomainReport(RECENT_EVENTS_DOMAIN, question, str(exc), False)
    except Exception as exc:
        logger.warning(
            "recent events unavailable for situational picture",
            extra={"event": "picture_recent_events_failed", "reason": str(exc), "trace_id": get_trace_id()},
        )
        return DomainReport(RECENT_EVENTS_DOMAIN, question, str(exc), False)
    return DomainReport(RECENT_EVENTS_DOMAIN, question, answer.answer, True)


def collect_domain_reports(
    plan: PicturePlan,
    protocol: "Protocol",
    registry: "AgentRegistry",
    history_query_service: "HistoryQueryService",
    *,
    caller_identity: str | None,
    sender_identity_filter: str | None,
    now: datetime,
    operational_scope: OperationalScope | None = None,
    timeout_per_specialist: float = SPECIALIST_TIMEOUT_SECONDS,
) -> tuple[DomainReport, ...]:
    """Put every planned question to its specialist and pull the recent events, all concurrently."""

    outcomes: dict[str, tuple[str, bool]] = {}
    resolved_operational_scope = resolve_operational_scope(operational_scope)

    def _specialist_runner(briefing: DomainBriefing) -> Callable[[], tuple[str, str]]:
        def _run() -> tuple[str, str]:
            agent = registry.get(briefing.agent_name)
            tools = _readable_tools(agent, protocol)
            try:
                with authenticated_request_identity(caller_identity), operational_scope_context(resolved_operational_scope), operational_time_context(now), operational_profile_context(current_operational_profile()), stage_context("picture_specialist"):
                    result = agent.process(briefing.query, tools)
            except Exception as exc:
                outcomes[briefing.agent_name] = (str(exc), False)
                return briefing.agent_name, str(exc)
            succeeded = result.status == "success" and bool(result.text.strip())
            outcomes[briefing.agent_name] = (result.text, succeeded)
            return briefing.agent_name, result.text

        return _run

    def _history_runner() -> tuple[str, str]:
        report = collect_recent_events(
            history_query_service, hours=plan.recent_events_hours, now=now, sender_identity_filter=sender_identity_filter,
            scenario_id=resolved_operational_scope.scenario_id,
            scenario_run_id=resolved_operational_scope.scenario_run_id,
        )
        outcomes[RECENT_EVENTS_DOMAIN] = (report.text, report.succeeded)
        return RECENT_EVENTS_DOMAIN, report.text

    runners: list[tuple[str, Callable[[], tuple[str, str]]]] = [
        (briefing.agent_name, _specialist_runner(briefing)) for briefing in plan.briefings
    ]
    runners.append((RECENT_EVENTS_DOMAIN, _history_runner))
    raw_answers = run_parallel_specialists(runners, max_workers=len(runners), timeout_per_specialist=timeout_per_specialist)

    catalog = get_current_catalog()
    reports: list[DomainReport] = []
    queries = {briefing.agent_name: briefing.query for briefing in plan.briefings}
    queries[RECENT_EVENTS_DOMAIN] = catalog.text(
        "orchestrator.picture.recent_events_question", hours=plan.recent_events_hours
    )
    for domain, _runner in runners:
        if domain in outcomes:
            text, succeeded = outcomes[domain]
        else:
            # The runner never finished (timeout) - run_parallel_specialists left its own marker.
            text, succeeded = raw_answers.get(domain, ""), False
        reports.append(DomainReport(domain, queries[domain], text, succeeded))
    return tuple(reports)


def _reports_json(reports: tuple[DomainReport, ...]) -> str:
    return json.dumps(
        [
            {
                "domain": report.domain,
                "asked": report.query,
                "status": "reported" if report.succeeded else "unavailable",
                "report": report.text if report.succeeded else "",
            }
            for report in reports
            if report.domain != RECENT_EVENTS_DOMAIN
        ],
        ensure_ascii=False,
    )


def _recent_events_block(reports: tuple[DomainReport, ...]) -> str:
    for report in reports:
        if report.domain == RECENT_EVENTS_DOMAIN:
            return report.text if report.succeeded else "(unavailable right now)"
    return "(not requested)"


def _fallback_text(reports: tuple[DomainReport, ...], current_time: str, hours: int) -> str:
    catalog = get_current_catalog()
    lines = [catalog.text("orchestrator.picture.fallback_header", time=current_time)]
    for report in reports:
        if report.domain == RECENT_EVENTS_DOMAIN:
            continue
        if report.succeeded:
            lines.append(report.text.strip())
        else:
            lines.append(catalog.text("orchestrator.picture.domain_unavailable", domain=report.domain))
    for report in reports:
        if report.domain == RECENT_EVENTS_DOMAIN:
            label = catalog.text("orchestrator.picture.recent_events_label", hours=hours)
            body = report.text.strip() if report.succeeded else catalog.text(
                "orchestrator.picture.domain_unavailable", domain=report.domain
            )
            lines.append(f"{label}: {body}")
    return "\n".join(line for line in lines if line)


def compose_situational_picture(
    main_agent: "MainAgent",
    reports: tuple[DomainReport, ...],
    raw_text: str,
    *,
    current_time: str,
    recent_events_hours: int,
    max_lines: int = PICTURE_MAX_LINES,
) -> str:
    """Have the Main Agent write the picture from the collected findings only."""

    if not any(report.succeeded for report in reports):
        return _fallback_text(reports, current_time, recent_events_hours)

    prompt = SITUATIONAL_PICTURE_COMPOSE_INSTRUCTION.format(
        max_lines=max_lines,
        request_json=json.dumps(raw_text, ensure_ascii=False),
        current_time=current_time,
        reports_json=_reports_json(reports),
        recent_events=_recent_events_block(reports),
    )
    try:
        with stage_context("picture_composition"):
            result = main_agent.process(prompt, [], invocation_policy=_COMPOSE_POLICY)
    except Exception as exc:
        logger.warning(
            "situational picture composition failed; returning collected findings",
            extra={"event": "picture_compose_fallback", "reason": str(exc), "trace_id": get_trace_id()},
        )
        return _fallback_text(reports, current_time, recent_events_hours)
    if result.status != "success" or not result.text.strip():
        return _fallback_text(reports, current_time, recent_events_hours)

    text = result.text.strip()
    missing = [report.domain for report in reports if not report.succeeded]
    if missing:
        note = get_current_catalog().text("orchestrator.picture.missing_note", domains=", ".join(missing))
        text = f"{text}\n{note}"
    return text


def build_situational_picture(
    main_agent: "MainAgent",
    protocol: "Protocol",
    registry: "AgentRegistry",
    history_query_service: "HistoryQueryService",
    raw_text: str,
    *,
    caller_identity: str | None,
    sender_identity_filter: str | None,
    scenario_id: str | None = None,
    scenario_run_id: str | None = None,
    scenario_time: str | None = None,
    now: datetime | None = None,
    scope: SituationalQueryScope | None = None,
    operational_scope: OperationalScope | None = None,
) -> SituationalPicture:
    """Plan, collect, and compose one live picture for `raw_text` under `protocol`."""

    if now is None and scenario_time:
        try:
            parsed_time = datetime.fromisoformat(str(scenario_time).replace("Z", "+00:00"))
            now = parsed_time if parsed_time.tzinfo else parsed_time.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            now = None
    now = now or datetime.now(timezone.utc)
    effective_scope = scope or SituationalQueryScope.overall_scope()
    if operational_scope is None and (scenario_id is not None or scenario_run_id is not None):
        operational_scope = OperationalScope.simulation(str(scenario_id or ""), str(scenario_run_id or ""))
    resolved_operational_scope = resolve_operational_scope(operational_scope)
    total_started = time.perf_counter()
    snapshot_started = time.perf_counter()
    typed_snapshot = build_typed_snapshot(
        registry,
        now=now,
        history_query_service=history_query_service,
        sender_identity_filter=sender_identity_filter,
        scenario_id=scenario_id,
        scenario_run_id=scenario_run_id,
        scenario_time=scenario_time,
        scope=effective_scope,
        operational_scope=resolved_operational_scope,
    )
    if typed_snapshot is not None:
        snapshot_seconds = time.perf_counter() - snapshot_started
        plan = _scoped_plan(protocol, effective_scope)
        current_time = _current_time_label(history_query_service, now)
        context_started = time.perf_counter()
        operational_context = build_operational_context(
            typed_snapshot,
            query_scope=effective_scope,
            current_time=current_time,
            operational_scope=resolved_operational_scope,
        )
        context_seconds = time.perf_counter() - context_started
        reasoning = None
        if effective_scope.requires_bounded_reasoning:
            reasoning = reason_over_operational_context(
                main_agent,
                operational_context,
                raw_text=raw_text,
            )
            picture_text = (
                _render_reasoned_picture(operational_context, reasoning)
                if not reasoning.fallback
                else _render_reasoning_fallback(typed_snapshot)
            )
            diagnostic_trace = get_active_provider_diagnostic_trace()
            if diagnostic_trace is not None:
                diagnostic_trace.record_render()
        else:
            picture_text = render_typed_snapshot(typed_snapshot, include_findings=True)
        logger.info(
            "typed situational picture completed",
            extra={
                "event": "typed_picture_completed",
                "bounded_reasoning": bool(effective_scope.requires_bounded_reasoning),
                "snapshot_seconds": round(snapshot_seconds, 6),
                "context_seconds": round(context_seconds, 6),
                "reasoning_seconds": round(
                    time.perf_counter() - context_started - context_seconds, 6
                ) if reasoning is not None else 0.0,
                "total_seconds": round(time.perf_counter() - total_started, 6),
                "trace_id": get_trace_id(),
            },
        )
        return SituationalPicture(
            text=picture_text,
            reports=(),
            generated_at=typed_snapshot.generated_at,
            plan=plan,
            snapshot=typed_snapshot,
            reasoning=reasoning,
        )

    current_time = _current_time_label(history_query_service, now)
    plan = plan_situational_picture(main_agent, protocol, registry, raw_text, current_time=current_time)
    reports = collect_domain_reports(
        plan,
        protocol,
        registry,
        history_query_service,
        caller_identity=caller_identity,
        sender_identity_filter=sender_identity_filter,
        now=now,
        operational_scope=resolved_operational_scope,
    )
    text = compose_situational_picture(
        main_agent,
        reports,
        raw_text or protocol.description,
        current_time=current_time,
        recent_events_hours=plan.recent_events_hours,
    )
    logger.info(
        "situational picture composed",
        extra={
            "event": "picture_composed",
            "protocol": protocol.name,
            "domains": {report.domain: report.succeeded for report in reports},
            "trace_id": get_trace_id(),
        },
    )
    return SituationalPicture(text=text, reports=reports, generated_at=storage_timestamp(now), plan=plan)


def compose_picture_from_step_outcomes(
    main_agent: "MainAgent",
    protocol: "Protocol",
    step_outcomes: tuple["StepOutcome", ...],
    raw_text: str,
    history_query_service: "HistoryQueryService",
    *,
    sender_identity_filter: str | None,
    scenario_id: str | None = None,
    scenario_run_id: str | None = None,
    now: datetime | None = None,
) -> str:
    """The queued-pipeline variant: the specialists already ran their formulated tasks, so only
    the recent events are fetched here before the Main Agent composes the picture."""

    now = now or datetime.now(timezone.utc)
    current_time = _current_time_label(history_query_service, now)
    reports = [
        DomainReport(
            outcome.step.agent_name,
            outcome.step.task_text,
            outcome.result_text or "",
            bool(outcome.succeeded and outcome.result_text),
        )
        for outcome in step_outcomes
    ]
    reports.append(
        collect_recent_events(
            history_query_service,
            hours=DEFAULT_RECENT_EVENTS_HOURS,
            now=now,
            sender_identity_filter=sender_identity_filter,
            scenario_id=scenario_id,
            scenario_run_id=scenario_run_id,
        )
    )
    return compose_situational_picture(
        main_agent,
        tuple(reports),
        raw_text or protocol.description,
        current_time=current_time,
        recent_events_hours=DEFAULT_RECENT_EVENTS_HOURS,
    )
