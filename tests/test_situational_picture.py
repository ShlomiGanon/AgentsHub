"""The situational picture is assembled at request time from what the specialists and the
event log report right now - the Main Agent decides what to ask each domain, every domain
answers from live data, and the picture is written from those answers only."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from agents.contracts import AgentDescriptor, AgentResult, ToolInfo
from agents.runtime import AgentRegistry
from history import HistoryAnswer, parse_timestamp
from history.query import HistoryQueryError
from messages import get_catalog, set_current_catalog
from orchestrator.situational_picture import (
    DEFAULT_RECENT_EVENTS_HOURS,
    MAX_RECENT_EVENTS_HOURS,
    RECENT_EVENTS_DOMAIN,
    build_situational_picture,
    collect_recent_events,
    compose_situational_picture,
    DomainReport,
    parse_picture_plan,
)
from protocols import CriticalityLevel, Protocol

NOW = datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _english_catalog():
    set_current_catalog(get_catalog("en"))


class FakeSpecialist:
    """A duck-typed specialist: records the exact question it was asked and the tools it was
    allowed, and answers with whatever live text the test hands it."""

    def __init__(self, name: str, tools: tuple[ToolInfo, ...], answer: str, *, fail: Exception | None = None):
        self.name = name
        self.role = f"{name} role"
        self.system_prompt = "fake"
        self._tools = tools
        self.answer = answer
        self.fail = fail
        self.calls: list[tuple[str, list[str]]] = []

    @property
    def descriptor(self) -> AgentDescriptor:
        return AgentDescriptor(self.name, self.role, self.system_prompt, self._tools, "m")

    def exposed_tools(self):
        return self._tools

    def process(self, text, allowed_tools, *, invocation_policy=None):
        self.calls.append((text, list(allowed_tools)))
        if self.fail is not None:
            raise self.fail
        return AgentResult("success", self.answer)


class FakeMainAgent:
    """Scripted Main Agent: the planning prompt gets `plan_text`, the composition prompt echoes
    the facts it was given so tests can see the picture depends on them."""

    def __init__(self, plan_text: str | None = None, compose_text: str | None = None, compose_status="success"):
        self.plan_text = plan_text
        self.compose_text = compose_text
        self.compose_status = compose_status
        self.prompts: list[str] = []

    def process(self, text, allowed_tools, *, invocation_policy=None):
        self.prompts.append(text)
        if "Specialists JSON" in text:
            if self.plan_text is None:
                raise RuntimeError("planner unavailable")
            return AgentResult("success", self.plan_text)
        if "Specialist reports JSON" in text:
            if self.compose_text is not None:
                return AgentResult(self.compose_status, self.compose_text)
            reports = json.loads(text.split("Specialist reports JSON: ", 1)[1].split("\nRecent events log:", 1)[0])
            facts = " | ".join(f"{r['domain']}: {r['report'] or 'unavailable'}" for r in reports)
            recent = text.split("Recent events log: ", 1)[1].split("\n\nRespond", 1)[0]
            return AgentResult("success", f"PICTURE[{facts} | recent: {recent}]")
        raise AssertionError(f"unexpected Main Agent prompt: {text[:120]!r}")


class FakeHistoryService:
    def __init__(self, answer: str | None = "1. Event abc: smoke at gate 3, handled."):
        self.answer = answer
        self.calls: list[tuple[str, object, str | None]] = []

    def planning_context(self):
        return {"current_time_local": "2026-09-10T15:00:00+03:00", "timezone": "Asia/Jerusalem"}

    def query_spec(self, question, spec, *, sender_identity_filter=None):
        self.calls.append((question, spec, sender_identity_filter))
        if self.answer is None:
            raise HistoryQueryError("no stored events match the requested history filters")
        return HistoryAnswer(self.answer, (), spec.time_start, spec.time_end, 1)


def _tool(name: str, side_effecting: bool = False) -> ToolInfo:
    return ToolInfo(name, f"{name} description", side_effecting, False if side_effecting else None)


def _protocol(*agents: str, approved: tuple[str, ...] = ("get_overview", "get_roster")) -> Protocol:
    return Protocol(
        name="overall_situational_picture",
        description="overall picture",
        participating_agents=tuple(agents),
        approved_tools=approved,
        expected_success_output="a unified picture",
        criticality=CriticalityLevel.LOW,
        approval_flag=False,
    )


def _registry(*agents: FakeSpecialist) -> AgentRegistry:
    return AgentRegistry({agent.name: agent for agent in agents})


def _plan_json(**queries: str) -> str:
    return json.dumps(
        {"domains": [{"agent": name, "query": query} for name, query in queries.items()], "recent_events_hours": 6}
    )


def test_plan_keeps_every_participant_and_clamps_the_window():
    protocol = _protocol("surveillance_agent", "team_status_agent")
    raw = (
        'Sure: {"domains": [{"agent": "surveillance_agent", "query": "drones?"}, '
        '{"agent": "friendly_forces_agent", "query": "ignored"}], "recent_events_hours": 500}'
    )

    plan = parse_picture_plan(raw, protocol)

    assert [b.agent_name for b in plan.briefings] == ["surveillance_agent", "team_status_agent"]
    assert plan.briefings[0].query == "drones?"
    # A skipped specialist is still asked, with the catalog's default domain question.
    assert plan.briefings[1].query == get_catalog("en").text("orchestrator.picture.default_domain_query")
    assert plan.recent_events_hours == MAX_RECENT_EVENTS_HOURS
    assert plan.planned_by_model is True


def test_plan_rejects_non_json_so_the_caller_falls_back():
    with pytest.raises(ValueError):
        parse_picture_plan("I cannot plan this", _protocol("surveillance_agent"))


def test_main_agent_asks_each_specialist_its_own_live_question_and_composes_from_the_answers():
    surveillance = FakeSpecialist(
        "surveillance_agent",
        (_tool("get_overview"), _tool("dispatch_drone", side_effecting=True)),
        "Cameras 3/4 active. Drones: 2 ready, 1 in flight. Active missions: 1 (M-77 to gate 3).",
    )
    team = FakeSpecialist(
        "team_status_agent",
        (_tool("get_roster"),),
        "Roster 6: available 1 (Dan Levi), unavailable 0, awaiting response 5.",
    )
    main_agent = FakeMainAgent(plan_text=_plan_json(surveillance_agent="What is the drone and camera state now?", team_status_agent="Who is available right now?"))
    history = FakeHistoryService()
    protocol = _protocol("surveillance_agent", "team_status_agent")

    picture = build_situational_picture(
        main_agent, protocol, _registry(surveillance, team), history, "what is the situational picture?",
        caller_identity="commander-1", sender_identity_filter=None, now=NOW,
    )

    # Planning saw the specialists and only their approved read-only tools.
    plan_prompt = main_agent.prompts[0]
    assert "get_overview" in plan_prompt and "get_roster" in plan_prompt and "dispatch_drone" not in plan_prompt
    assert "2026-09-10T15:00:00+03:00 (Asia/Jerusalem)" in plan_prompt

    # Each specialist got the Main Agent's question for its own domain, with read-only tools only.
    assert surveillance.calls == [("What is the drone and camera state now?", ["get_overview"])]
    assert team.calls == [("Who is available right now?", ["get_roster"])]

    # The picture is written from exactly those live answers plus the recent event log.
    assert "2 ready, 1 in flight" in picture.text
    assert "awaiting response 5" in picture.text
    assert "Event abc" in picture.text
    assert picture.plan.recent_events_hours == 6
    assert parse_timestamp(picture.generated_at) == NOW
    provenance = picture.provenance()
    assert [d["domain"] for d in provenance["domains"]] == ["surveillance_agent", "team_status_agent", RECENT_EVENTS_DOMAIN]
    assert all(d["succeeded"] for d in provenance["domains"])
    assert provenance["planned_by_model"] is True


def test_picture_follows_the_live_state_rather_than_a_prepared_text():
    def _run(drone_text: str, roster_text: str) -> str:
        surveillance = FakeSpecialist("surveillance_agent", (_tool("get_overview"),), drone_text)
        team = FakeSpecialist("team_status_agent", (_tool("get_roster"),), roster_text)
        main_agent = FakeMainAgent(plan_text=_plan_json(surveillance_agent="q1", team_status_agent="q2"))
        return build_situational_picture(
            main_agent, _protocol("surveillance_agent", "team_status_agent"), _registry(surveillance, team),
            FakeHistoryService(), "picture", caller_identity="c", sender_identity_filter=None, now=NOW,
        ).text

    before = _run("Drones: 2 ready, 0 in flight.", "available 1, awaiting 5")
    after = _run("Drones: 1 ready, 1 in flight (M-78).", "available 4, awaiting 2")

    assert before != after
    assert "2 ready" in before and "1 in flight (M-78)" in after
    assert "awaiting 5" in before and "available 4" in after


def test_a_failed_specialist_is_reported_unavailable_and_the_rest_still_form_the_picture():
    surveillance = FakeSpecialist("surveillance_agent", (_tool("get_overview"),), "Drones: 2 ready.")
    team = FakeSpecialist("team_status_agent", (_tool("get_roster"),), "", fail=RuntimeError("store locked"))
    main_agent = FakeMainAgent(plan_text=_plan_json(surveillance_agent="q1", team_status_agent="q2"))

    picture = build_situational_picture(
        main_agent, _protocol("surveillance_agent", "team_status_agent"), _registry(surveillance, team),
        FakeHistoryService(), "picture", caller_identity="c", sender_identity_filter=None, now=NOW,
    )

    by_domain = {report.domain: report for report in picture.reports}
    assert by_domain["surveillance_agent"].succeeded is True
    assert by_domain["team_status_agent"].succeeded is False
    assert "Drones: 2 ready." in picture.text
    assert "team_status_agent: unavailable" in picture.text
    assert picture.text.endswith(get_catalog("en").text("orchestrator.picture.missing_note", domains="team_status_agent"))


def test_planner_failure_falls_back_to_default_domain_questions_and_still_asks_live():
    surveillance = FakeSpecialist("surveillance_agent", (_tool("get_overview"),), "Drones: 2 ready.")
    team = FakeSpecialist("team_status_agent", (_tool("get_roster"),), "available 1")
    main_agent = FakeMainAgent(plan_text=None)

    picture = build_situational_picture(
        main_agent, _protocol("surveillance_agent", "team_status_agent"), _registry(surveillance, team),
        FakeHistoryService(), "picture", caller_identity="c", sender_identity_filter=None, now=NOW,
    )

    default_query = get_catalog("en").text("orchestrator.picture.default_domain_query")
    assert surveillance.calls[0][0] == default_query and team.calls[0][0] == default_query
    assert picture.plan.planned_by_model is False
    assert picture.plan.recent_events_hours == DEFAULT_RECENT_EVENTS_HOURS
    assert "Drones: 2 ready." in picture.text and "available 1" in picture.text


def test_recent_events_window_follows_the_plan_and_the_caller_ownership_scope():
    surveillance = FakeSpecialist("surveillance_agent", (_tool("get_overview"),), "Drones: 2 ready.")
    team = FakeSpecialist("team_status_agent", (_tool("get_roster"),), "available 1")
    history = FakeHistoryService()
    main_agent = FakeMainAgent(plan_text=_plan_json(surveillance_agent="q1", team_status_agent="q2"))

    build_situational_picture(
        main_agent, _protocol("surveillance_agent", "team_status_agent"), _registry(surveillance, team), history,
        "picture", caller_identity="viewer-1", sender_identity_filter="viewer-1", now=NOW,
    )

    (question, spec, sender_filter), = history.calls
    assert question == get_catalog("en").text("orchestrator.picture.recent_events_question", hours=6)
    assert spec.operation == "list" and spec.order == "newest" and spec.time_basis == "received_at"
    assert parse_timestamp(spec.time_start) == NOW - timedelta(hours=6)
    assert parse_timestamp(spec.time_end) == NOW
    assert sender_filter == "viewer-1"


def test_no_recent_events_is_a_fact_not_a_failure():
    report = collect_recent_events(FakeHistoryService(answer=None), hours=6, now=NOW, sender_identity_filter=None)

    assert report.domain == RECENT_EVENTS_DOMAIN
    assert report.succeeded is True
    assert report.text == get_catalog("en").text("orchestrator.picture.no_recent_events", hours=6)


def test_composition_falls_back_to_the_collected_findings_when_the_model_cannot_write():
    reports = (
        DomainReport("surveillance_agent", "q1", "Drones: 2 ready.", True),
        DomainReport("team_status_agent", "q2", "", False),
        DomainReport(RECENT_EVENTS_DOMAIN, "recent?", "1. Event abc.", True),
    )
    main_agent = FakeMainAgent(compose_text="", compose_status="success")

    text = compose_situational_picture(main_agent, reports, "picture", current_time="T", recent_events_hours=6)

    catalog = get_catalog("en")
    assert text.splitlines()[0] == catalog.text("orchestrator.picture.fallback_header", time="T")
    assert "Drones: 2 ready." in text
    assert catalog.text("orchestrator.picture.domain_unavailable", domain="team_status_agent") in text
    assert catalog.text("orchestrator.picture.recent_events_label", hours=6) + ": 1. Event abc." in text


def test_nothing_collected_skips_the_model_and_reports_every_domain_unavailable():
    reports = (
        DomainReport("surveillance_agent", "q1", "boom", False),
        DomainReport(RECENT_EVENTS_DOMAIN, "recent?", "down", False),
    )
    main_agent = FakeMainAgent()

    text = compose_situational_picture(main_agent, reports, "picture", current_time="T", recent_events_hours=6)

    assert main_agent.prompts == []
    assert get_catalog("en").text("orchestrator.picture.domain_unavailable", domain="surveillance_agent") in text
    assert get_catalog("en").text("orchestrator.picture.domain_unavailable", domain=RECENT_EVENTS_DOMAIN) in text
