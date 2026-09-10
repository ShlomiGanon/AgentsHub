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
from typing import TYPE_CHECKING, Callable

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


@dataclass(frozen=True)
class DomainReport:
    """What one domain answered (or that it did not answer) for this picture."""

    domain: str
    query: str
    text: str
    succeeded: bool


@dataclass(frozen=True)
class SituationalPicture:
    text: str
    reports: tuple[DomainReport, ...]
    generated_at: str
    plan: PicturePlan

    def provenance(self) -> dict:
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
    now: datetime | None = None,
) -> SituationalPicture:
    """Plan, collect, and compose one live picture for `raw_text` under `protocol`."""

    now = now or datetime.now(timezone.utc)
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
    )
