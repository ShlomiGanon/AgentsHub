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
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Callable, Literal

from agents import InvocationPolicy, authenticated_request_identity
from history import HistoryQuerySpec, storage_timestamp
from history.query import HistoryQueryError
from messages import get_current_catalog
from messages.model_messages import (
    SITUATIONAL_PICTURE_COMPOSE_INSTRUCTION,
    SITUATIONAL_PICTURE_PLAN_INSTRUCTION,
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
SPECIALIST_TIMEOUT_SECONDS = 25.0
PICTURE_MAX_LINES = 8


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
_TEAM_SCOPE_TERMS = (
    "\u05e1\u05d3\u05db",
    "\u05db\u05d5\u05d7",
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

    has_picture_phrase = _contains_query_term(normalized, _SITUATIONAL_PICTURE_TERMS)
    has_team = _contains_query_term(normalized, _TEAM_SCOPE_TERMS)
    has_surveillance = _contains_query_term(normalized, _SURVEILLANCE_SCOPE_TERMS)
    has_drones = _contains_query_term(normalized, _DRONE_SCOPE_TERMS)
    has_external_reports = _contains_query_term(normalized, _EXTERNAL_REPORT_SCOPE_TERMS)
    has_current_state = _contains_query_term(normalized, _CURRENT_STATE_TERMS) or "?" in str(text)

    if _contains_query_term(normalized, _FOLLOW_UP_STATUS_TERMS) and not has_picture_phrase:
        return None

    if not any((has_team, has_surveillance, has_drones, has_external_reports)):
        return SituationalQueryScope.overall_scope() if has_picture_phrase else None

    if not has_current_state and not has_picture_phrase:
        return None

    return SituationalQueryScope(
        team=has_team,
        surveillance=has_surveillance,
        drones=has_drones,
        external_reports=has_external_reports,
        overall=False,
    )

_PLAN_POLICY = InvocationPolicy(max_output_tokens=400, timeout_seconds=30.0, reasoning_effort="none")
_COMPOSE_POLICY = InvocationPolicy(max_output_tokens=450, timeout_seconds=45.0, reasoning_effort="none")


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


@dataclass(frozen=True)
class RecentOperationalReport:
    """A committed report safe for the shared picture (no internal identifiers)."""

    text: str
    source_ref: str
    received_at: str


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


def _section_provenance(source: str, now: datetime, scope: str = "global") -> SnapshotProvenance:
    return SnapshotProvenance(source=source, as_of=storage_timestamp(now), scope=scope)


def _build_camera_snapshot(store, *, now: datetime, area: str | None) -> CameraSnapshot:
    provenance = _section_provenance("surveillance_store.list_cameras", now, area or "global")
    try:
        cameras = list(store.list_cameras(area=area))
    except Exception:
        return CameraSnapshot(None, None, None, None, "unknown", provenance, None, None)

    counts = {"active": 0, "degraded": 0, "offline": 0, "unknown": 0}
    for camera in cameras:
        status = str(camera.get("status", "")).casefold()
        if status == "active":
            counts["active"] += 1
        elif status in {"degraded", "offline"}:
            counts[status] += 1
        else:
            counts["unknown"] += 1
    return CameraSnapshot(
        total=len(cameras),
        active=counts["active"],
        inactive=counts["degraded"] + counts["offline"],
        unknown=counts["unknown"],
        status="inconsistent" if sum(counts.values()) != len(cameras) else "ok",
        provenance=provenance,
        degraded=counts["degraded"],
        offline=counts["offline"],
    )


def _build_drone_snapshot(store, *, now: datetime, area: str | None) -> DroneSnapshot:
    provenance = _section_provenance("surveillance_store.list_drones+get_active_missions", now, area or "global")
    try:
        drones = list(store.list_drones())
        missions = list(store.get_active_missions())
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


def _build_team_snapshot(store, *, now: datetime) -> TeamSnapshot:
    provenance = _section_provenance("team_status_store.availability_snapshot", now)
    try:
        entries = list(store.availability_snapshot(storage_timestamp(now)))
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
    return TeamSnapshot(
        total=total,
        available=counts["available"],
        unavailable=counts["unavailable"],
        not_reported=counts["not_reported"],
        pending_identity=counts["pending_identity"],
        status="ok" if consistent else "inconsistent",
        provenance=provenance,
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
            extra={"event": "picture_committed_reports_failed", "trace_id": get_trace_id()},
        )
        return ()

    reports: list[RecentOperationalReport] = []
    for event in events:
        if event.get("outcome") != "succeeded":
            continue
        classification = str(event.get("classification") or "").casefold()
        if not classification.endswith("_report"):
            continue
        text = str(event.get("description") or event.get("raw_text") or "").strip()
        if not text:
            continue
        reports.append(
            RecentOperationalReport(
                text=text,
                source_ref=f"event:{event.get('event_id', 'unknown')}",
                received_at=str(event.get("received_at") or ""),
            )
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
    scope: SituationalQueryScope | None = None,
) -> SituationalSnapshot | None:
    """Read authoritative specialist stores and construct a validated snapshot.

    Missing stores return ``None`` so existing profiles with specialist fakes can
    continue using the legacy report pipeline; no model output is parsed as state.
    """

    now = now or datetime.now(timezone.utc)
    effective_scope = scope or SituationalQueryScope.overall_scope()
    surveillance_agent = _registry_agent(registry, "surveillance_agent")
    team_agent = _registry_agent(registry, "team_status_agent")
    surveillance_store = getattr(surveillance_agent, "surveillance_store", None)
    team_store = getattr(team_agent, "status_store", None)
    if surveillance_store is None and team_store is None:
        return None

    cameras = (
        _build_camera_snapshot(surveillance_store, now=now, area=area)
        if surveillance_store and (effective_scope.overall or effective_scope.surveillance)
        else None
    )
    drones = (
        _build_drone_snapshot(surveillance_store, now=now, area=area)
        if surveillance_store and (effective_scope.overall or effective_scope.drones)
        else None
    )
    team = (
        _build_team_snapshot(team_store, now=now)
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
            scenario_id=scenario_id,
            scenario_run_id=scenario_run_id,
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


def render_typed_snapshot(snapshot: SituationalSnapshot, *, include_findings: bool = True) -> str:
    """Render localized facts and findings without model-authored claims."""

    catalog = get_current_catalog()
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
            lines.append(catalog.text("orchestrator.picture.typed.team_unknown"))
        else:
            lines.append(catalog.text(
                "orchestrator.picture.typed.team",
                total=section.total,
                available=section.available,
                unavailable=section.unavailable,
                not_reported=section.not_reported,
                pending_identity=section.pending_identity,
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

    if snapshot.recent_reports:
        lines.extend(("", catalog.text("orchestrator.picture.typed.recent_reports_header")))
        lines.extend(
            catalog.text("orchestrator.picture.typed.recent_report", text=report.text)
            for report in snapshot.recent_reports
        )

    recommendations = [finding for finding in snapshot.findings if finding.suggested_action_key] if include_findings else []
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
    timeout_per_specialist: float = SPECIALIST_TIMEOUT_SECONDS,
) -> tuple[DomainReport, ...]:
    """Put every planned question to its specialist and pull the recent events, all concurrently."""

    outcomes: dict[str, tuple[str, bool]] = {}

    def _specialist_runner(briefing: DomainBriefing) -> Callable[[], tuple[str, str]]:
        def _run() -> tuple[str, str]:
            agent = registry.get(briefing.agent_name)
            tools = _readable_tools(agent, protocol)
            try:
                with authenticated_request_identity(caller_identity), stage_context("picture_specialist"):
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
            history_query_service, hours=plan.recent_events_hours, now=now, sender_identity_filter=sender_identity_filter
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
    now: datetime | None = None,
    scope: SituationalQueryScope | None = None,
) -> SituationalPicture:
    """Plan, collect, and compose one live picture for `raw_text` under `protocol`."""

    now = now or datetime.now(timezone.utc)
    effective_scope = scope or SituationalQueryScope.overall_scope()
    typed_snapshot = build_typed_snapshot(
        registry,
        now=now,
        history_query_service=history_query_service,
        sender_identity_filter=sender_identity_filter,
        scenario_id=scenario_id,
        scenario_run_id=scenario_run_id,
        scope=effective_scope,
    )
    if typed_snapshot is not None:
        plan = _scoped_plan(protocol, effective_scope)
        return SituationalPicture(
            text=render_typed_snapshot(typed_snapshot, include_findings=effective_scope.overall),
            reports=(),
            generated_at=typed_snapshot.generated_at,
            plan=plan,
            snapshot=typed_snapshot,
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
