"""Regression coverage for scoped, authoritative Main Agent state queries."""

from __future__ import annotations

import dataclasses
import json

import pytest

from agents.contracts import AgentDescriptor, AgentResult, ToolInfo
from agents.runtime import AgentRegistry
from api.app import build_app, build_group_routing
from messages import get_catalog, set_current_catalog
from orchestrator.situational_picture import (
    SituationalQueryScope,
    build_situational_picture,
    classify_situational_query,
)
from protocols import CriticalityLevel, Protocol, ProtocolSet
from tests.api_fakes import COMMANDER_IDENTITY, auth_headers, build_context


STEP_5 = (
    "\u05e2\u05e8\u05d1 \u05d8\u05d5\u05d1, \u05ea\u05e4\u05d9\u05e7 \u05dc\u05d9 \u05e1\u05d9\u05db\u05d5\u05dd \u05d9\u05d5\u05de\u05d9: "
    "\u05de\u05d9 \u05d7\u05e1\u05e8 \u05d1\u05e1\u05d3\"\u05db \u05dc\u05dc\u05d9\u05dc\u05d4 \u05d5\u05de\u05d4 \u05d4\u05e1\u05d8\u05d8\u05d5\u05e1 \u05e9\u05dc \u05de\u05e6\u05dc\u05de\u05d5\u05ea \u05d4\u05d2\u05d3\u05e8?"
)
STEP_9 = "\u05ea\u05e6\u05d9\u05d2 \u05dc\u05d9 \u05ea\u05d2\u05d6\u05d9\u05e8 \u05ea\u05de\u05d5\u05e0\u05ea \u05de\u05e6\u05d1 \u05de\u05e2\u05d5\u05d3\u05db\u05e0\u05ea \u05dc\u05e7\u05e8\u05d0\u05ea \u05d4\u05dc\u05d9\u05dc\u05d4."


class _ReadOnlyStore:
    def __init__(self):
        self.camera_reads = 0
        self.drone_reads = 0
        self.mission_reads = 0

    def list_cameras(self, area=None):
        self.camera_reads += 1
        cameras = [
            {"camera_id": "CAM-01", "area": "north_gate", "status": "active"},
            {"camera_id": "CAM-02", "area": "south_sector", "status": "active"},
            {"camera_id": "CAM-03", "area": "east_fence", "status": "offline"},
            {"camera_id": "CAM-04", "area": "central_hub", "status": "active"},
            {"camera_id": "CAM-05", "area": "west_hill", "status": "active"},
            {"camera_id": "CAM-08", "area": "south_sector", "status": "degraded"},
        ]
        return [camera for camera in cameras if area is None or camera["area"] == area]

    def list_drones(self):
        self.drone_reads += 1
        return [{"drone_id": "DR-01", "status": "ready"}]

    def get_active_missions(self):
        self.mission_reads += 1
        return []


class _TeamStore:
    def __init__(self):
        self.reads = 0

    def availability_snapshot(self, as_of):
        self.reads += 1
        return [
            {"telegram_identity": "eli", "availability": "unavailable"},
            {"telegram_identity": "michael", "availability": "awaiting_response"},
            {"telegram_identity": "dan", "availability": "available"},
        ]


class _StoreAgent:
    def __init__(self, name, store, tool_name):
        self.name = name
        self.role = name
        self.system_prompt = "test"
        self.store = store
        self._tools = (ToolInfo(tool_name, "read-only", False, None),)
        self.calls = []

    @property
    def descriptor(self):
        return AgentDescriptor(self.name, self.role, self.system_prompt, self._tools, "test")

    @property
    def surveillance_store(self):
        return self.store if self.name == "surveillance_agent" else None

    @property
    def status_store(self):
        return self.store if self.name == "team_status_agent" else None

    def exposed_tools(self):
        return self._tools

    def process(self, text, allowed_tools, *, invocation_policy=None):
        self.calls.append((text, tuple(allowed_tools)))
        raise AssertionError("authoritative scoped state must bypass specialist prose")


class _NoMainCalls:
    def __init__(self):
        self.calls = []

    def process(self, text, allowed_tools, *, invocation_policy=None):
        self.calls.append((text, tuple(allowed_tools)))
        raise AssertionError("typed state must not invoke Main Agent composition")


class _BoundedReasoningMain:
    def __init__(self):
        self.calls = []

    def process(self, text, allowed_tools, *, invocation_policy=None):
        self.calls.append((text, tuple(allowed_tools), invocation_policy))
        return AgentResult(
            "success",
            json.dumps({
                "facts": [{"text": "Scoped operational picture.", "source_aliases": ["S1"]}],
                "assessments": [],
                "recommendations": [],
            }),
        )


class _NoHistory:
    def recent_committed_events(self, **kwargs):
        raise AssertionError("narrow current-state queries must not read recent reports")


@pytest.fixture(autouse=True)
def _english_catalog():
    set_current_catalog(get_catalog("en"))


def _protocol():
    return Protocol(
        name="overall_situational_picture",
        description="overall picture",
        participating_agents=("surveillance_agent", "team_status_agent"),
        approved_tools=("read_surveillance", "read_team"),
        expected_success_output="typed picture",
        criticality=CriticalityLevel.LOW,
        approval_flag=False,
    )


def _typed_registry():
    surveillance_store = _ReadOnlyStore()
    team_store = _TeamStore()
    surveillance = _StoreAgent("surveillance_agent", surveillance_store, "read_surveillance")
    team = _StoreAgent("team_status_agent", team_store, "read_team")
    return AgentRegistry({surveillance.name: surveillance, team.name: team}), surveillance, team


def test_operational_query_scopes_cover_step5_variants_and_reject_follow_up_status():
    assert classify_situational_query(STEP_5) == SituationalQueryScope(team=True, surveillance=True)
    assert classify_situational_query("\u05de\u05d9 \u05d7\u05e1\u05e8 \u05d1\u05e1\u05d3\"\u05db \u05dc\u05dc\u05d9\u05dc\u05d4?") == SituationalQueryScope(team=True)
    assert classify_situational_query("\u05de\u05d4 \u05d4\u05e1\u05d8\u05d8\u05d5\u05e1 \u05e9\u05dc \u05de\u05e6\u05dc\u05de\u05d5\u05ea \u05d4\u05d2\u05d3\u05e8?") == SituationalQueryScope(surveillance=True)
    assert classify_situational_query("\u05de\u05d4 \u05de\u05e6\u05d1 \u05d4\u05db\u05d5\u05d7 \u05d5\u05d4\u05de\u05e6\u05dc\u05de\u05d5\u05ea?") == SituationalQueryScope(team=True, surveillance=True)
    assert classify_situational_query(STEP_9) == SituationalQueryScope.overall_scope()
    assert classify_situational_query("\u05de\u05d4 \u05de\u05e6\u05d1 \u05d4\u05e4\u05e2\u05d5\u05dc\u05d4 \u05e9\u05d1\u05d9\u05e7\u05e9\u05ea\u05d9?") is None
    assert classify_situational_query("\u05de\u05d4 \u05de\u05e6\u05d1 \u05d4\u05d0\u05d9\u05e9\u05d5\u05e8?") is None
    assert classify_situational_query("\u05de\u05d4 \u05de\u05e6\u05d1 \u05de\u05d6\u05d2 \u05d4\u05d0\u05d5\u05d5\u05d9\u05e8?") is None
    assert classify_situational_query(
        "\u05ea\u05e6\u05d9\u05e3 \u05dc\u05d9 \u05ea\u05de\u05d5\u05e0\u05d4 \u05de\u05d4\u05d9\u05e8\u05d4: \u05d9\u05e9 \u05de\u05e9\u05d4\u05d5 \u05d7\u05e9\u05d5\u05d3 \u05d1\u05d2\u05d6\u05e8\u05d4 \u05d4\u05de\u05d6\u05e8\u05d7\u05d9\u05ea?") == SituationalQueryScope(
        surveillance=True,
        external_reports=True,
    )


def test_scoped_step5_reads_only_team_and_surveillance_and_renders_no_unrelated_sections():
    registry, surveillance, team = _typed_registry()
    main_agent = _BoundedReasoningMain()
    scope = classify_situational_query(STEP_5)

    picture = build_situational_picture(
        main_agent,
        _protocol(),
        registry,
        _NoHistory(),
        STEP_5,
        caller_identity=COMMANDER_IDENTITY,
        sender_identity_filter=None,
        scope=scope,
    )

    assert picture.snapshot is not None
    assert picture.snapshot.cameras.degraded == 1
    assert picture.snapshot.cameras.offline == 1
    assert picture.snapshot.team.available == 1
    assert picture.snapshot.team.unavailable == 1
    assert picture.snapshot.team.not_reported == 1
    assert picture.snapshot.drones is None
    assert picture.snapshot.recent_reports == ()
    assert surveillance.store.camera_reads == 1
    assert surveillance.store.drone_reads == 0
    assert surveillance.store.mission_reads == 0
    assert team.store.reads == 1
    assert surveillance.calls == []
    assert team.calls == []
    assert len(main_agent.calls) == 1
    assert picture.reasoning is not None
    assert picture.reasoning.fallback is False
    assert "Scoped operational picture." in picture.text
    assert "Drones:" not in picture.text
    assert "Recent committed reports:" not in picture.text
    assert picture.plan.scope == scope
    assert picture.plan.recent_events_hours == 0


def test_overall_step9_keeps_the_existing_typed_picture_scope():
    registry, surveillance, team = _typed_registry()
    main_agent = _NoMainCalls()
    scope = classify_situational_query(STEP_9)

    picture = build_situational_picture(
        main_agent,
        _protocol(),
        registry,
        _NoHistory(),
        STEP_9,
        caller_identity=COMMANDER_IDENTITY,
        sender_identity_filter=None,
        scope=scope,
    )

    assert scope.overall is True
    assert picture.snapshot.drones is not None
    assert "Drones: 1 ready" in picture.text
    assert surveillance.store.drone_reads == 1
    assert surveillance.store.mission_reads == 1
    assert picture.plan.recent_events_hours > 0
    assert len(main_agent.calls) == 1
    assert picture.reasoning is not None
    assert picture.reasoning.model_call_count == 1
    assert picture.reasoning.fallback is True


def test_api_routes_step5_through_typed_picture_without_event_or_specialist_calls(tmp_path):
    ctx = build_context(tmp_path, main_agent=_BoundedReasoningMain())
    surveillance_store = _ReadOnlyStore()
    team_store = _TeamStore()
    surveillance = _StoreAgent("surveillance_agent", surveillance_store, "read_surveillance")
    team = _StoreAgent("team_status_agent", team_store, "read_team")
    registry = AgentRegistry({surveillance.name: surveillance, team.name: team})
    picture_protocol = _protocol()
    deps = dataclasses.replace(
        ctx.deps,
        registry=registry,
        protocol_set=ProtocolSet((*ctx.deps.protocol_set.all(), picture_protocol)),
        history_query_service=_NoHistory(),
    )
    ctx = dataclasses.replace(ctx, deps=deps, group_routing=build_group_routing(ctx.deps.persistence, registry))
    client = build_app(ctx).test_client()

    response = client.post(
        "/Msg",
        headers=auth_headers(COMMANDER_IDENTITY),
        json={"text": STEP_5, "sender_identity": COMMANDER_IDENTITY},
    )

    assert response.status_code == 200
    body = response.get_json()
    assert body["taken_as"] == "question"
    assert body["provenance"]["query_scope"] == {
        "team": True,
        "surveillance": True,
        "drones": False,
        "external_reports": False,
        "overall": False,
    }
    assert "Scoped operational picture." in body["answer"]
    assert "Drones:" not in body["answer"]
    assert "Recent committed reports:" not in body["answer"]
    assert surveillance.calls == []
    assert team.calls == []
    assert len(ctx.main_agent.calls) == 1
    assert ctx.deps.persistence.fetch_events_range("2000-01-01", "2100-01-01") == []
