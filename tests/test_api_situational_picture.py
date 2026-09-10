"""`POST /Msg` answers a multi-domain picture protocol from live specialist data gathered for
that request - with the Main Agent's own per-domain questions and the caller-scoped recent
event log - whether the picture was asked for by button/hint or in plain words."""

from __future__ import annotations

import dataclasses
import json
import types

import pytest

from agents import adapter
from agents.contracts import AgentDescriptor, AgentResult, ToolInfo
from agents.runtime import AgentRegistry
from api.app import build_app, build_group_routing
from api.routes import SITUATIONAL_PICTURE_PROTOCOL, _is_situational_picture_query
from history import HistoryAnswer
from history.query import HistoryQueryError
from protocols import CriticalityLevel, Protocol, ProtocolSet
from tests.api_fakes import COMMANDER_IDENTITY, VIEWER_IDENTITY, auth_headers, build_context


@pytest.fixture(autouse=True)
def _mock_crewai(monkeypatch):
    class _FakeOutput:
        def __init__(self, raw):
            self.raw = raw

    class _FakeCrewAgent:
        def __init__(self, **kwargs):
            pass

        def kickoff(self, text):
            return _FakeOutput("status nominal, no anomalies")

    fake_module = types.SimpleNamespace(
        Agent=_FakeCrewAgent, LLM=lambda **kwargs: kwargs["model"], tools=types.SimpleNamespace(BaseTool=object)
    )
    monkeypatch.setattr(adapter, "_get_crewai", lambda: fake_module)


@pytest.fixture
def teardown_ctx():
    contexts = []
    yield contexts
    for ctx in contexts:
        ctx.queue.stop()
        ctx.deps.persistence.close()


class _LiveSpecialist:
    def __init__(self, name: str, tool_name: str, answer: str):
        self.name = name
        self.role = f"{name} role"
        self.system_prompt = "fake"
        self._tools = (ToolInfo(tool_name, f"{tool_name} description", False, None),)
        self.answer = answer
        self.calls: list[tuple[str, list[str]]] = []

    @property
    def descriptor(self) -> AgentDescriptor:
        return AgentDescriptor(self.name, self.role, self.system_prompt, self._tools, "m")

    def exposed_tools(self):
        return self._tools

    def process(self, text, allowed_tools, *, invocation_policy=None):
        self.calls.append((text, list(allowed_tools)))
        return AgentResult("success", self.answer)


class _PictureMainAgent:
    """Plans one question per specialist, then composes by echoing the live facts it was given."""

    def __init__(self):
        self.prompts: list[str] = []

    def process(self, text, allowed_tools, *, invocation_policy=None):
        self.prompts.append(text)
        if "Specialists JSON" in text:
            return AgentResult(
                "success",
                json.dumps(
                    {
                        "domains": [
                            {"agent": "surveillance_agent", "query": "drones and cameras now?"},
                            {"agent": "team_status_agent", "query": "who is available now?"},
                        ],
                        "recent_events_hours": 3,
                    }
                ),
            )
        if "Specialist reports JSON" in text:
            reports = json.loads(text.split("Specialist reports JSON: ", 1)[1].split("\nRecent events log:", 1)[0])
            recent = text.split("Recent events log: ", 1)[1].split("\n\nRespond", 1)[0]
            return AgentResult("success", " / ".join(r["report"] for r in reports) + f" / {recent}")
        raise AssertionError(f"the picture must not need any other Main Agent judgment: {text[:100]!r}")


class _RecordingHistory:
    def __init__(self):
        self.calls = []

    def planning_context(self):
        return {"current_time_local": "2026-09-10T15:00:00+03:00", "timezone": "Asia/Jerusalem"}

    def query_spec(self, question, spec, *, sender_identity_filter=None):
        self.calls.append((spec, sender_identity_filter))
        if sender_identity_filter is not None:
            raise HistoryQueryError("no stored events match the requested history filters")
        return HistoryAnswer("1. Event e1: smoke at gate 3, succeeded.", (), spec.time_start, spec.time_end, 1)


def _picture_ctx(tmp_path):
    ctx = build_context(tmp_path, main_agent=_PictureMainAgent())
    surveillance = _LiveSpecialist("surveillance_agent", "get_surveillance_overview", "Drones: 2 ready, 1 in flight. Cameras 3/4 active.")
    team = _LiveSpecialist("team_status_agent", "get_team_status_roster", "Roster 6: available 1, awaiting 5.")
    existing = {agent.name: agent for agent in ctx.deps.registry.all()}
    registry = AgentRegistry({**existing, surveillance.name: surveillance, team.name: team})
    picture_protocol = Protocol(
        name=SITUATIONAL_PICTURE_PROTOCOL,
        description="overall picture",
        participating_agents=("surveillance_agent", "team_status_agent"),
        approved_tools=("get_surveillance_overview", "get_team_status_roster"),
        expected_success_output="a unified picture",
        criticality=CriticalityLevel.LOW,
        approval_flag=False,
    )
    history = _RecordingHistory()
    deps = dataclasses.replace(
        ctx.deps,
        registry=registry,
        protocol_set=ProtocolSet((*ctx.deps.protocol_set.all(), picture_protocol)),
        history_query_service=history,
    )
    ctx = dataclasses.replace(ctx, deps=deps, group_routing=build_group_routing(ctx.deps.persistence, registry))
    return ctx, surveillance, team, history


def test_hinted_picture_is_built_from_live_specialist_answers_and_recent_events(tmp_path, teardown_ctx):
    ctx, surveillance, team, history = _picture_ctx(tmp_path)
    teardown_ctx.append(ctx)
    client = build_app(ctx).test_client()

    resp = client.post(
        "/Msg",
        headers=auth_headers(COMMANDER_IDENTITY),
        json={"text": "picture", "sender_identity": COMMANDER_IDENTITY, "protocol_hint": SITUATIONAL_PICTURE_PROTOCOL},
    )

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["taken_as"] == "question"
    assert body["protocol"] == SITUATIONAL_PICTURE_PROTOCOL
    assert "2 ready, 1 in flight" in body["answer"]
    assert "available 1, awaiting 5" in body["answer"]
    assert "Event e1" in body["answer"]
    assert surveillance.calls == [("drones and cameras now?", ["get_surveillance_overview"])]
    assert team.calls == [("who is available now?", ["get_team_status_roster"])]
    assert body["provenance"]["recent_events_hours"] == 3
    assert [d["domain"] for d in body["provenance"]["domains"]] == ["surveillance_agent", "team_status_agent", "recent_events"]
    assert history.calls[0][1] is None  # a commander's recent events are unscoped
    assert ctx.deps.persistence.fetch_events_range("2000-01-01", "2100-01-01") == []  # read-only, no event written


def test_plain_words_asking_for_the_picture_route_to_the_live_picture(tmp_path, teardown_ctx):
    ctx, surveillance, team, history = _picture_ctx(tmp_path)
    teardown_ctx.append(ctx)
    client = build_app(ctx).test_client()

    resp = client.post(
        "/Msg",
        headers=auth_headers(VIEWER_IDENTITY),
        json={"text": "\u05de\u05d4 \u05ea\u05de\u05d5\u05e0\u05ea \u05d4\u05de\u05e6\u05d1 \u05db\u05e8\u05d2\u05e2?", "sender_identity": VIEWER_IDENTITY},
    )

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["protocol"] == SITUATIONAL_PICTURE_PROTOCOL
    assert surveillance.calls and team.calls
    # A viewer's recent-events view keeps their ownership scope, and "no events" is a fact in the picture.
    assert history.calls[0][1] == VIEWER_IDENTITY
    assert ctx.loaded_profile.message_catalog.text("orchestrator.picture.no_recent_events", hours=3) in body["answer"]
    assert body["provenance"]["domains"][-1]["succeeded"] is True


def test_picture_phrase_detection_is_conservative():
    assert _is_situational_picture_query("\u05de\u05d4 \u05ea\u05de\u05d5\u05e0\u05ea \u05d4\u05de\u05e6\u05d1 \u05db\u05e8\u05d2\u05e2?")
    assert _is_situational_picture_query("  \u05ea\u05de\u05d5\u05e0\u05ea   \u05de\u05e6\u05d1 ")
    assert _is_situational_picture_query("Give me the situational picture")
    assert not _is_situational_picture_query("\u05de\u05d4 \u05de\u05e6\u05d1 \u05d4\u05e8\u05d7\u05e4\u05e0\u05d9\u05dd?")
    assert not _is_situational_picture_query("what happened yesterday?")


def test_picture_phrase_without_the_protocol_in_scope_falls_through(tmp_path, teardown_ctx):
    """A profile with no multi-domain picture protocol keeps its normal question routing."""

    from tests.api_fakes import happy_path_agent

    agent = happy_path_agent(intent="question")
    agent._dispatch["Decide which of the following agents"] = "AGENT: reference_agent\nTASK: status?"
    ctx = build_context(tmp_path, main_agent=agent)
    teardown_ctx.append(ctx)
    client = build_app(ctx).test_client()

    resp = client.post(
        "/Msg", headers=auth_headers(VIEWER_IDENTITY),
        json={"text": "situational picture please", "sender_identity": VIEWER_IDENTITY},
    )

    assert resp.status_code == 200
    assert resp.get_json()["taken_as"] == "question"
    assert "protocol" not in resp.get_json()
