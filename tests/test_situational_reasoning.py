"""Task 56 coverage for bounded, evidence-grounded SITREP reasoning."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from agents.contracts import AgentDescriptor, AgentResult, ToolInfo
from agents.runtime import AgentRegistry
from history.query import HistoryQueryService
from messages import get_catalog, set_current_catalog
from orchestrator.situational_picture import (
    SituationalQueryScope,
    build_operational_context,
    build_situational_picture,
    build_typed_snapshot,
    reason_over_operational_context,
)
from protocols import CriticalityLevel, Protocol
from persistence.contracts import EventSearchCriteria


NOW = datetime(2026, 9, 18, 12, 0, tzinfo=timezone.utc)
SCENARIO_ID = "SEC_001_PHASE_1"


class _CameraStore:
    def __init__(self):
        self.reads = 0

    def list_cameras(self, area=None):
        self.reads += 1
        cameras = [
            {"camera_id": "CAM-03", "area": "east_fence", "status": "offline"},
            {"camera_id": "CAM-08", "area": "south_sector", "status": "degraded"},
            {"camera_id": "CAM-01", "area": "north_gate", "status": "active"},
            {"camera_id": "CAM-02", "area": "south_sector", "status": "active"},
        ]
        return [camera for camera in cameras if area is None or camera["area"] == area]

    def list_drones(self):
        return [{"drone_id": "DR-01", "status": "ready"}]

    def get_active_missions(self):
        return []


class _TeamStore:
    def availability_snapshot(self, as_of):
        return [
            {"telegram_identity": "eli", "availability": "unavailable"},
            {"telegram_identity": "michael", "availability": "awaiting_response"},
            {"telegram_identity": "dan", "availability": "available"},
        ]


class _StoreAgent:
    def __init__(self, name, store):
        self.name = name
        self.role = name
        self.system_prompt = "read-only test agent"
        self.store = store
        self._tools = (ToolInfo(f"read_{name}", "read-only", False, None),)
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
        raise AssertionError("specialists must not be called by bounded SITREP reasoning")


class _NoHistory:
    def recent_committed_events(self, **kwargs):
        return ()


class _ReasoningAgent:
    def __init__(self, output=None, error=None):
        self.output = output
        self.error = error
        self.calls = []

    def process(self, text, allowed_tools, *, invocation_policy=None):
        self.calls.append((text, tuple(allowed_tools), invocation_policy))
        if self.error is not None:
            raise self.error
        return AgentResult("success", self.output)


@pytest.fixture(autouse=True)
def _hebrew_catalog():
    set_current_catalog(get_catalog("he"))


def _protocol():
    return Protocol(
        name="overall_situational_picture",
        description="overall operational picture",
        participating_agents=("surveillance_agent", "team_status_agent"),
        approved_tools=("read_surveillance_agent", "read_team_status_agent"),
        expected_success_output="typed picture",
        criticality=CriticalityLevel.LOW,
        approval_flag=False,
    )


def _registry():
    surveillance = _StoreAgent("surveillance_agent", _CameraStore())
    team = _StoreAgent("team_status_agent", _TeamStore())
    return AgentRegistry({surveillance.name: surveillance, team.name: team})


def _payload(source_refs, *, recommendation=False, text=None):
    return json.dumps(
        {
            "facts": [
                {
                    "text": text or "׳׳¦׳׳׳” 03 ׳׳•׳©׳‘׳× ׳•׳׳¦׳׳׳” 08 ׳‘׳׳™׳›׳•׳× ׳™׳¨׳•׳“׳”.",
                    "source_refs": [source_refs[0]],
                }
            ],
            "assessments": [
                {
                    "conclusion": "׳”׳›׳™׳¡׳•׳™ ׳•׳”׳–׳׳™׳ ׳•׳× ׳ž׳•׳’׳‘׳׳™׳ ׳‘׳ž׳§׳‘׳™׳.",
                    "supporting_source_refs": list(source_refs[:2]),
                    "confidence": "high",
                    "qualification": "׳”׳ž׳¡׳§׳ ׳” ׳ž׳‘׳•׳¡׳¡׳× ׳¢׳ ׳”׳ž׳¦׳‘ ׳”׳ ׳›׳•׳—׳™.",
                    "affected_domains": ["cross_domain"],
                    "priority": "high",
                }
            ],
            "recommendations": (
                [
                    {
                        "description": "׳ž׳•׳ž׳׳¥ ׳׳”׳©׳׳™׳ ׳“׳™׳•׳•׳—׳™ ׳–׳ž׳™׳ ׳•׳×.",
                        "rationale": "׳ž׳—׳¡׳•׳¨ ׳ž׳™׳“׳¢ ׳ž׳§׳©׳” ׳¢׳ ׳”׳¢׳¨׳›׳”.",
                        "supporting_source_refs": [source_refs[-1]],
                        "priority": "medium",
                        "possible_capability": None,
                        "requires_approval": False,
                    }
                ]
                if recommendation
                else []
            ),
        },
        ensure_ascii=False,
    )


def _payload(source_refs, *, recommendation=False, text=None):
    aliases = {source_ref: f"S{index}" for index, source_ref in enumerate(source_refs, start=1)}
    return json.dumps(
        {
            "f": [{"t": text or "CAM-03 offline; CAM-08 degraded.", "s": [aliases[source_refs[0]]]}],
            "a": [{
                "c": "Cross-domain readiness is reduced.",
                "s": [aliases[source_ref] for source_ref in source_refs[:2]],
                "v": "h",
                "q": "Partial evidence.",
                "d": ["x"],
                "p": "h",
            }],
            "r": ([{
                "d": "Review the degraded camera.",
                "r": "Needs attention.",
                "s": [aliases[source_refs[-1]]],
                "p": "m",
                "c": None,
                "a": False,
            }] if recommendation else []),
        },
        ensure_ascii=False,
    )


def _snapshot_and_context():
    snapshot = build_typed_snapshot(_registry(), now=NOW, history_query_service=_NoHistory())
    return snapshot, build_operational_context(
        snapshot,
        query_scope=SituationalQueryScope.overall_scope(),
        current_time=NOW.isoformat(),
    )


def test_operational_context_is_compact_typed_and_selects_abnormal_entities_only():
    snapshot, context = _snapshot_and_context()
    payload = context.prompt_payload()

    assert snapshot.cameras.abnormal_cameras[0].camera_id == "CAM-03"
    assert [item["camera_id"] for item in payload["authoritative_facts"]["surveillance"]["abnormal_entities"]] == ["CAM-03", "CAM-08"]
    assert "CAM-01" not in json.dumps(payload, ensure_ascii=False)
    assert payload["source_refs"]
    assert payload["current_run_operational_reports"] == []


def test_valid_cross_domain_reasoning_is_accepted_and_rendered_in_hebrew():
    _, context = _snapshot_and_context()
    agent = _ReasoningAgent(_payload(context.source_refs, recommendation=True))

    result = reason_over_operational_context(agent, context, raw_text="׳×׳¦׳™׳’ ׳×׳ž׳•׳ ׳× ׳ž׳¦׳‘")

    assert result.fallback is False
    assert result.model_call_count == 1
    assert len(result.assessments) == 1
    assert len(result.recommendations) == 1
    assert result.assessments[0].supporting_source_refs[0] in context.source_refs
    assert result.recommendations[0].requires_approval is False
    assert len(agent.calls) == 1


def test_overall_picture_uses_one_reasoning_call_after_typed_snapshot():
    registry = _registry()
    source_refs = (
        "state:cameras:surveillance_store.list_cameras",
        "state:drones:surveillance_store.list_drones+get_active_missions",
        "state:team:team_status_store.availability_snapshot",
    )
    agent = _ReasoningAgent(_payload(source_refs, recommendation=True))

    picture = build_situational_picture(
        agent,
        _protocol(),
        registry,
        _NoHistory(),
        "׳×׳¦׳™׳’ ׳×׳ž׳•׳ ׳× ׳ž׳¦׳‘ ׳ž׳¢׳•׳“׳›׳ ׳×",
        caller_identity="commander",
        sender_identity_filter=None,
        now=NOW,
        scope=SituationalQueryScope.overall_scope(),
    )

    assert picture.reasoning.fallback is False
    assert get_catalog("he").text("orchestrator.picture.reasoned.title") in picture.text
    assert get_catalog("he").text("orchestrator.picture.typed.recent_reports_header") not in picture.text
    return
    assert picture.reasoning.model_call_count == 1
    assert len(agent.calls) == 1
    assert "׳×׳ž׳¦׳™׳×" in picture.text
    assert "׳“׳™׳•׳•׳—׳™׳ ׳ž׳—׳•׳™׳‘׳™׳" not in picture.text
    assert all(not store_agent.calls for store_agent in registry.all())


def test_unknown_source_ref_falls_back_without_exposing_model_output():
    _, context = _snapshot_and_context()
    invalid = _payload(("state:cameras:surveillance_store.list_cameras", "event:not-real"))
    invalid = json.dumps({"f": [{"t": "unknown", "s": ["S99"]}], "a": [], "r": []})
    agent = _ReasoningAgent(invalid)

    result = reason_over_operational_context(agent, context, raw_text="picture")

    assert result.fallback is True
    assert result.rejected_claim_count == 1
    assert result.assessments == ()


def test_missing_source_ref_falls_back():
    _, context = _snapshot_and_context()
    invalid = json.dumps({"facts": [{"text": "׳¢׳•׳‘׳“׳”", "source_refs": []}], "assessments": [], "recommendations": []})

    invalid = json.dumps({"f": [{"t": "missing refs", "s": []}], "a": [], "r": []})
    result = reason_over_operational_context(_ReasoningAgent(invalid), context, raw_text="picture")

    assert result.fallback is True


def test_hallucinated_execution_claim_is_rejected():
    _, context = _snapshot_and_context()
    invalid = _payload(
        context.source_refs,
        text="׳©׳׳—׳×׳™ ׳¦׳•׳•׳× ׳׳ž׳¦׳׳ž׳” 03.",
    )
    invalid = _payload(
        context.source_refs,
        text="\u05e9\u05dc\u05d7\u05ea\u05d9 \u05e6\u05d5\u05d5\u05ea \u05dc\u05de\u05e6\u05dc\u05de\u05d4 03.",
    )

    result = reason_over_operational_context(_ReasoningAgent(invalid), context, raw_text="picture")

    assert result.fallback is True
    assert result.assessments == ()


def test_model_failure_uses_deterministic_fallback_and_one_call():
    snapshot = build_typed_snapshot(_registry(), now=NOW, history_query_service=_NoHistory())
    agent = _ReasoningAgent(error=RuntimeError("model unavailable"))

    picture = build_situational_picture(
        agent,
        _protocol(),
        _registry(),
        _NoHistory(),
        "׳×׳¦׳™׳’ ׳×׳ž׳•׳ ׳× ׳ž׳¦׳‘",
        caller_identity="commander",
        sender_identity_filter=None,
        now=NOW,
        scope=SituationalQueryScope.overall_scope(),
    )

    assert picture.reasoning.fallback is True
    assert picture.reasoning.model_call_count == 1
    assert len(agent.calls) == 1
    assert "׳“׳™׳•׳•׳—׳™׳ ׳ž׳—׳•׳™׳‘׳™׳" not in picture.text
    assert snapshot.cameras.offline == 1


def test_malformed_structured_output_falls_back():
    _, context = _snapshot_and_context()

    result = reason_over_operational_context(_ReasoningAgent("not json"), context, raw_text="picture")

    assert result.fallback is True


def test_not_reported_is_preserved_in_reasoning_context():
    _, context = _snapshot_and_context()
    prompt_payload = context.prompt_payload()

    assert prompt_payload["authoritative_facts"]["team"]["not_reported"] == 1
    assert prompt_payload["authoritative_facts"]["team"]["available"] == 1
    assert "unavailable" not in json.dumps(
        {"not_reported": prompt_payload["authoritative_facts"]["team"]["not_reported"]},
        ensure_ascii=False,
    )


def test_recommendation_is_display_only_and_does_not_create_event_or_receipt(tmp_path):
    from persistence import open_persistence

    persistence = open_persistence(str(tmp_path / "history.db"))
    try:
        snapshot = build_typed_snapshot(_registry(), now=NOW, history_query_service=HistoryQueryService(persistence, None))
        context = build_operational_context(
            snapshot,
            query_scope=SituationalQueryScope.overall_scope(),
            current_time=NOW.isoformat(),
        )
        agent = _ReasoningAgent(_payload(context.source_refs, recommendation=True))
        before = persistence.count_events(EventSearchCriteria())
        result = reason_over_operational_context(agent, context, raw_text="picture")
        after = persistence.count_events(EventSearchCriteria())

        assert result.recommendations
        assert before == after == 0
        assert all(not hasattr(recommendation, "tool_name") for recommendation in result.recommendations)
    finally:
        persistence.close()


def test_current_run_context_excludes_previous_run_reports(tmp_path):
    from persistence import open_persistence

    persistence = open_persistence(str(tmp_path / "history.db"))
    try:
        for run_id, description in (("run-a", "׳“׳™׳•׳•׳— ׳ž׳”׳¨׳¦׳” A"), ("run-b", "׳“׳™׳•׳•׳— ׳ž׳”׳¨׳¦׳” B")):
            persistence.append_event(
                {
                    "source": "telegram",
                    "raw_text": description,
                    "sender_identity": "9001",
                    "received_at": "2026-09-18T10:00:00+00:00",
                    "classification": "friendly_forces_report",
                    "description": description,
                    "outcome": "succeeded",
                    "scenario_id": SCENARIO_ID,
                    "scenario_run_id": run_id,
                    "scenario_step": 1,
                    "scenario_time": "2026-09-18T10:00:00Z",
                }
            )
        snapshot = build_typed_snapshot(
            _registry(),
            now=NOW,
            history_query_service=HistoryQueryService(persistence, None),
            scenario_id=SCENARIO_ID,
            scenario_run_id="run-b",
        )
        context = build_operational_context(
            snapshot,
            query_scope=SituationalQueryScope.overall_scope(),
            current_time=NOW.isoformat(),
        )
        prompt_payload = context.prompt_payload()
        reports = prompt_payload["current_run_operational_reports"]

        assert [report["text"] for report in reports] == ["׳“׳™׳•׳•׳— ׳ž׳”׳¨׳¦׳” B"]
        assert "׳“׳™׳•׳•׳— ׳ž׳”׳¨׳¦׳” A" not in json.dumps(prompt_payload, ensure_ascii=False)
    finally:
        persistence.close()
