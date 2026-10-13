"""A situational picture assembled at request time, never from prepared text.

When every read tool the picture protocol approves runs without arguments,
each participating domain's live state is read directly from those tools,
concurrently with the recent event log, and the Main Agent writes the
picture from exactly that data -- no planning call and no specialist model
round trip. Otherwise the Main Agent first decides what it needs to know
from every participating specialist (one concrete question per domain),
those questions are put to the specialists concurrently so each one answers
from its own read-only tools, and the picture is written from their
findings. A domain that fails or times out is reported as unavailable
rather than papered over, and when the model cannot compose, the findings
are still returned verbatim under a timestamped header.
"""

from __future__ import annotations

import inspect
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Callable

from agents import InvocationPolicy, authenticated_request_identity
from history import HistoryQuerySpec, storage_timestamp
from history.query import HistoryQueryError
from messages import SUPPORTED_LANGUAGES, get_catalog, get_current_catalog
from messages.model_messages import (
    SITUATIONAL_PICTURE_COMPOSE_INSTRUCTION,
    SITUATIONAL_PICTURE_PLAN_INSTRUCTION,
)
from orchestrator.reasoning import SpecialistResult, run_parallel_specialists
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
PICTURE_MAX_LINES = 12

# Planning is optional (the default plan is a sound fallback), so it gets a short leash and the
# saved time goes to composition: a Hebrew picture of 12 lines plus an answer and a
# recommendation does not fit the old 450-token cap, and a capped reply used to surface as the
# raw specialist dump instead of a picture.
_PLAN_POLICY = InvocationPolicy(max_output_tokens=400, timeout_seconds=15.0, reasoning_effort="none")
_COMPOSE_POLICY = InvocationPolicy(max_output_tokens=1600, timeout_seconds=60.0, reasoning_effort="none")


# --- picture types ---

@dataclass(frozen=True)
class DomainBriefing:
    """One question the Main Agent decided to put to one specialist."""

    agent_name: str
    query: str


@dataclass(frozen=True)
class PicturePlan:
    """Which specialists to ask and how far back to pull recent events."""
    briefings: tuple[DomainBriefing, ...]
    recent_events_hours: int
    planned_by_model: bool


@dataclass(frozen=True)
class DomainReport:
    """What one domain answered (or that it did not answer) for this picture."""

    domain: str
    query: str
    text: str
    succeeded: bool


@dataclass(frozen=True)
class SituationalPicture:
    """Composed operational picture plus the specialist reports it was built from."""
    text: str
    reports: tuple[DomainReport, ...]
    generated_at: str
    plan: PicturePlan

    def provenance(self) -> dict:
        """Stable metadata about when and from which domains this picture was built."""

        return {
            "generated_at": self.generated_at,
            "recent_events_hours": self.plan.recent_events_hours,
            "planned_by_model": self.plan.planned_by_model,
            "domains": [
                {"domain": report.domain, "query": report.query, "succeeded": report.succeeded}
                for report in self.reports
            ],
        }


def _current_time_label(history_query_service: "HistoryQueryService | None", now: datetime) -> str:
    """Local clock label from history planning context, or a UTC fallback."""

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


def _readable_tools(agent, protocol: "Protocol") -> list[str]:
    """Read-only tools this specialist may use for a picture query."""

    approved = set(protocol.approved_tools)
    exposed = list(agent.exposed_tools())
    tools = [tool.name for tool in exposed if tool.name in approved and not tool.side_effecting]
    if not tools:
        tools = [tool.name for tool in exposed if not tool.side_effecting]
    return tools


def _specialists_json(protocol: "Protocol", registry: "AgentRegistry") -> str:
    """JSON describing each participating specialist and its readable tools."""

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


def _default_query(raw_text: str | None) -> str:
    """The catalog's whole-domain question, followed by the request it must serve."""

    default_query = get_current_catalog().text("orchestrator.picture.default_domain_query")
    if raw_text:
        return f"{default_query}\n{json.dumps(raw_text, ensure_ascii=False)}"
    return default_query


def _default_plan(protocol: "Protocol", raw_text: str | None = None) -> PicturePlan:
    """Fallback plan: the catalog default question, plus the request, for every participating agent."""

    default_query = _default_query(raw_text)
    return PicturePlan(
        briefings=tuple(DomainBriefing(agent_name, default_query) for agent_name in protocol.participating_agents),
        recent_events_hours=DEFAULT_RECENT_EVENTS_HOURS,
        planned_by_model=False,
    )


def _extract_json_object(raw_text: str) -> dict:
    """Pull the first JSON object out of a planning reply."""

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
        return _default_plan(protocol, raw_text)

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


def render_recent_entries(entries: list[dict]) -> str:
    """One compact line per recent event: time, channel, sender, message, area, status."""

    lines = []
    for entry in entries:
        parts = [entry.get("time") or "", entry.get("channel") or ""]
        message = entry.get("message") or ""
        parts.append(f"{entry.get('from')}: {message}" if entry.get("from") else message)
        if entry.get("area"):
            parts.append(str(entry["area"]))
        status = entry.get("status") or ""
        if entry.get("retracted_as_false"):
            status = f"{status}; retracted as false" if status else "retracted as false"
        if status:
            parts.append(status)
        lines.append(" | ".join(part for part in parts if part))
    return "\n".join(lines)


def collect_recent_events(
    history_query_service: "HistoryQueryService",
    *,
    hours: int,
    now: datetime,
    sender_identity_filter: str | None,
    limit: int = RECENT_EVENTS_LIMIT,
) -> DomainReport:
    """Ask history for what was recorded in the last `hours` hours (newest first, by receipt time)."""

    catalog = get_current_catalog()
    question = catalog.text("orchestrator.picture.recent_events_question", hours=hours)
    reader = getattr(history_query_service, "operational_context", None)
    if callable(reader):
        # Straight from the event log: what was reported, where, by whom, and what became of it.
        # A model-written history list here used to invent "unspecified" fields, raw ids and dates.
        try:
            with stage_context("picture_recent_events"):
                entries = reader(hours=hours, limit=limit, sender_identity_filter=sender_identity_filter)
        except Exception as exc:
            logger.warning(
                "recent events unavailable for situational picture",
                extra={"event": "picture_recent_events_failed", "reason": str(exc), "trace_id": get_trace_id()},
            )
            return DomainReport(RECENT_EVENTS_DOMAIN, question, str(exc), False)
        if isinstance(entries, list):
            if not entries:
                return DomainReport(
                    RECENT_EVENTS_DOMAIN, question, catalog.text("orchestrator.picture.no_recent_events", hours=hours), True
                )
            return DomainReport(RECENT_EVENTS_DOMAIN, question, render_recent_entries(entries), True)
    spec = HistoryQuerySpec(
        operation="list",
        time_start=storage_timestamp(now - timedelta(hours=hours)),
        time_end=storage_timestamp(now),
        time_basis="received_at",
        order="newest",
        limit=limit,
    )
    try:
        with stage_context("picture_recent_events"):
            answer = history_query_service.query_spec(question, spec, sender_identity_filter=sender_identity_filter)
    except HistoryQueryError as exc:
        if exc.empty:
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

    def _specialist_runner(briefing: DomainBriefing) -> Callable[[], SpecialistResult]:
        """Build a runner that asks one specialist the planned domain question."""

        def _run() -> SpecialistResult:
            """Ask this specialist and record whether it answered."""

            agent = registry.get(briefing.agent_name)
            tools = _readable_tools(agent, protocol)
            try:
                with authenticated_request_identity(caller_identity), stage_context("picture_specialist"):
                    result = agent.process(briefing.query, tools)
            except Exception as exc:
                outcomes[briefing.agent_name] = (str(exc), False)
                return SpecialistResult(answer=str(exc))
            succeeded = result.status == "success" and bool(result.text.strip())
            outcomes[briefing.agent_name] = (result.text, succeeded)
            return SpecialistResult(answer=result.text)

        return _run

    def _history_runner() -> SpecialistResult:
        """Fetch recent events as one concurrent picture domain."""

        report = collect_recent_events(
            history_query_service, hours=plan.recent_events_hours, now=now, sender_identity_filter=sender_identity_filter
        )
        outcomes[RECENT_EVENTS_DOMAIN] = (report.text, report.succeeded)
        return SpecialistResult(answer=report.text)

    runners: list[tuple[str, Callable[[], SpecialistResult]]] = [
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
            result = raw_answers.get(domain)
            text, succeeded = (result.answer if result else ""), False
        reports.append(DomainReport(domain, queries[domain], text, succeeded))
    return tuple(reports)


def _runs_without_arguments(method) -> bool:
    """True when a bound tool method can be called with no arguments at all."""

    if not callable(method):
        return False
    try:
        signature = inspect.signature(method)
    except (TypeError, ValueError):
        return False
    return all(
        parameter.default is not inspect.Parameter.empty
        or parameter.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
        for parameter in signature.parameters.values()
    )


def direct_read_tools(protocol: "Protocol", registry: "AgentRegistry") -> dict[str, tuple[str, ...]] | None:
    """Each participating agent's approved tools, when all of them are read-only and run without
    arguments -- the picture is then read straight from live state. None otherwise."""

    tools_by_agent: dict[str, tuple[str, ...]] = {}
    for agent_name in protocol.participating_agents:
        agent = registry.get(agent_name)
        exposed = {tool.name: tool for tool in agent.exposed_tools()}
        owned = tuple(name for name in protocol.approved_tools if name in exposed)
        if not owned:
            return None
        for name in owned:
            if exposed[name].side_effecting or not _runs_without_arguments(getattr(agent, name, None)):
                return None
        tools_by_agent[agent_name] = owned
    return tools_by_agent


def collect_direct_domain_reports(
    tools_by_agent: dict[str, tuple[str, ...]],
    registry: "AgentRegistry",
    history_query_service: "HistoryQueryService",
    *,
    caller_identity: str | None,
    sender_identity_filter: str | None,
    now: datetime,
    hours: int = DEFAULT_RECENT_EVENTS_HOURS,
    timeout_per_specialist: float = SPECIALIST_TIMEOUT_SECONDS,
) -> tuple[DomainReport, ...]:
    """Read every domain's approved tools and the recent events, all concurrently, with no model call."""

    outcomes: dict[str, tuple[str, bool]] = {}

    def _domain_reader(agent_name: str, tool_names: tuple[str, ...]) -> Callable[[], SpecialistResult]:
        """Build a runner that reads one domain's live state from its tools."""

        def _run() -> SpecialistResult:
            """Call each tool and keep its text; any failure marks the domain unavailable."""

            agent = registry.get(agent_name)
            parts: list[str] = []
            succeeded = True
            try:
                with authenticated_request_identity(caller_identity), stage_context("picture_direct_read"):
                    for tool_name in tool_names:
                        raw = getattr(agent, tool_name)()
                        succeeded = succeeded and getattr(raw, "ok", True)
                        text = str(getattr(raw, "text", raw) or "").strip()
                        if text:
                            parts.append(text)
            except Exception as exc:
                outcomes[agent_name] = (str(exc), False)
                return SpecialistResult(answer=str(exc))
            text = "\n".join(parts)
            outcomes[agent_name] = (text, succeeded and bool(text))
            return SpecialistResult(answer=text)

        return _run

    def _history_runner() -> SpecialistResult:
        """Fetch recent events as one concurrent picture domain."""

        report = collect_recent_events(
            history_query_service, hours=hours, now=now, sender_identity_filter=sender_identity_filter
        )
        outcomes[RECENT_EVENTS_DOMAIN] = (report.text, report.succeeded)
        return SpecialistResult(answer=report.text)

    runners: list[tuple[str, Callable[[], SpecialistResult]]] = [
        (agent_name, _domain_reader(agent_name, tool_names)) for agent_name, tool_names in tools_by_agent.items()
    ]
    runners.append((RECENT_EVENTS_DOMAIN, _history_runner))
    raw_answers = run_parallel_specialists(runners, max_workers=len(runners), timeout_per_specialist=timeout_per_specialist)

    catalog = get_current_catalog()
    default_query = catalog.text("orchestrator.picture.default_domain_query")
    reports: list[DomainReport] = []
    for domain, _runner in runners:
        query = (
            catalog.text("orchestrator.picture.recent_events_question", hours=hours)
            if domain == RECENT_EVENTS_DOMAIN
            else default_query
        )
        if domain in outcomes:
            text, succeeded = outcomes[domain]
        else:
            result = raw_answers.get(domain)
            text, succeeded = (result.answer if result else ""), False
        reports.append(DomainReport(domain, query, text, succeeded))
    return tuple(reports)


def _reports_json(reports: tuple[DomainReport, ...]) -> str:
    """JSON of specialist reports, excluding the recent-events domain."""

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
    """Recent-events text for the compose prompt, or an unavailable marker."""

    for report in reports:
        if report.domain == RECENT_EVENTS_DOMAIN:
            return report.text if report.succeeded else "(unavailable right now)"
    return "(not requested)"


def _fallback_text(reports: tuple[DomainReport, ...], current_time: str, hours: int) -> str:
    """Deterministic picture built from collected findings when the model cannot compose."""

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


def _normalize_picture_text(text: str) -> str:
    """Keep the operator-facing picture plain and readable even if the model adds markup."""

    lines: list[str] = []
    for raw_line in str(text or "").replace("\r\n", "\n").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        line = line.replace("**", "").replace("__", "")
        line = re.sub(r"^\s*#{1,6}\s*", "", line)
        line = re.sub(r"^\s*[-*•]\s+", "", line)
        line = re.sub(r"^\[([^\]]+)\]\s*$", r"\1", line)
        line = line.replace("\\_", "_")
        line = re.sub(r"[ \t]+", " ", line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


def compose_situational_picture(
    main_agent: "MainAgent",
    reports: tuple[DomainReport, ...],
    raw_text: str,
    *,
    current_time: str,
    recent_events_hours: int,
    max_lines: int = PICTURE_MAX_LINES,
    area_labels: dict[str, str] | None = None,
) -> str:
    """Have the Main Agent write the picture from the collected findings only."""

    if not any(report.succeeded for report in reports):
        return _normalize_picture_text(_fallback_text(reports, current_time, recent_events_hours))

    prompt = SITUATIONAL_PICTURE_COMPOSE_INSTRUCTION.format(
        max_lines=max_lines,
        request_json=json.dumps(raw_text, ensure_ascii=False),
        current_time=current_time,
        area_names_json=json.dumps(area_labels or {}, ensure_ascii=False, sort_keys=True),
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
        return _normalize_picture_text(_fallback_text(reports, current_time, recent_events_hours))
    if result.status != "success" or not result.text.strip():
        return _normalize_picture_text(_fallback_text(reports, current_time, recent_events_hours))

    text = _normalize_picture_text(result.text)
    missing = [report.domain for report in reports if not report.succeeded]
    if missing:
        note = get_current_catalog().text("orchestrator.picture.missing_note", domains=", ".join(missing))
        text = f"{text}\n{note}"
    return _normalize_picture_text(text)


def build_situational_picture(
    main_agent: "MainAgent",
    protocol: "Protocol",
    registry: "AgentRegistry",
    history_query_service: "HistoryQueryService",
    raw_text: str,
    *,
    caller_identity: str | None,
    sender_identity_filter: str | None,
    now: datetime | None = None,
) -> SituationalPicture:
    """Plan, collect, and compose one live picture for `raw_text` under `protocol`."""

    now = now or datetime.now(timezone.utc)
    current_time = _current_time_label(history_query_service, now)
    tools_by_agent = direct_read_tools(protocol, registry)
    if tools_by_agent is not None:
        # Every domain's whole live state is one argument-free read away: read it directly and let
        # the composer answer the request from the complete data, instead of first planning and
        # then asking each specialist model to summarize its own tools.
        plan = _default_plan(protocol)
        reports = collect_direct_domain_reports(
            tools_by_agent,
            registry,
            history_query_service,
            caller_identity=caller_identity,
            sender_identity_filter=sender_identity_filter,
            now=now,
            hours=plan.recent_events_hours,
        )
    else:
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
        area_labels=getattr(history_query_service, "area_labels", None),
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
        )
    )
    return compose_situational_picture(
        main_agent,
        tuple(reports),
        raw_text or protocol.description,
        current_time=current_time,
        recent_events_hours=DEFAULT_RECENT_EVENTS_HOURS,
        area_labels=getattr(history_query_service, "area_labels", None),
    )


_PICTURE_PROTOCOL_NAMES = ("query_situational_picture", "overall_situational_picture")


def question_requests_picture(question: str, stem: str) -> bool:
    """True when the question contains a catalog picture marker for this profile."""

    folded = question.casefold()
    key = f"{stem}.picture.markers"
    for language in SUPPORTED_LANGUAGES:
        catalog = get_catalog(language)
        if key not in catalog.messages:
            continue
        for marker in catalog.text(key).split("|"):
            marker = marker.strip()
            if marker and marker.casefold() in folded:
                return True
    return False


def picture_protocol(protocol_set):
    """The profile's read-only picture protocol, if it declares one."""

    for name in _PICTURE_PROTOCOL_NAMES:
        protocol = protocol_set.get(name)
        if protocol is not None:
            return protocol
    return None


def read_picture_directly(protocol, registry) -> str:
    """Call the picture protocol's read tools in Python. No subagent and no model call."""

    lines: list[str] = []
    for tool_name in protocol.approved_tools:
        agent = None
        for agent_name in protocol.participating_agents:
            candidate = registry.get(agent_name)
            if hasattr(candidate, tool_name):
                agent = candidate
                break
        if agent is None:
            continue
        raw = getattr(agent, tool_name)()
        text = getattr(raw, "text", None)
        if text is None:
            text = "" if raw is None else str(raw)
        if text.strip():
            lines.append(text.strip())
    return "\n".join(lines)
