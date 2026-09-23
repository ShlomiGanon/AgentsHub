import dataclasses
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from agents.history import HistoryAgent
from agents.runtime import AgentRegistry
from api.app import build_app
from auth.permissions import PermissionLevel
from orchestrator.flows import (
    FlowDeps,
    action_protocols_for_request,
    begin_request,
    continue_from_risk_assessment,
    continue_after_approval,
    enforce_action_routing_guard,
    prepare_fast_path_report,
    protocol_has_side_effects,
    resolve_approval,
)
from orchestrator.reasoning import (
    FormulationResult,
    IntentResult,
    OperationalDecision,
    ProtocolSelectionResult,
    RiskAssessment,
    SuccessVerdict,
)
from persistence import OperationalScope, open_persistence
from profiles import AreaRegistry, EventTypeRegistry, OptimizationPolicy, RESPONSE_TEAM
from protocols import ProtocolSet, Step
from tests.api_fakes import COMMANDER_IDENTITY, VIEWER_IDENTITY, auth_headers, build_context


class _Result:
    status = "success"

    def __init__(self, text):
        self.text = text


class _SelectionAgent:
    def __init__(self, selected_protocol):
        self.selected_protocol = selected_protocol
        self.calls = []

    def process(self, prompt, allowed_tools, **kwargs):
        self.calls.append(prompt)
        if "RISK_SCORE" in prompt:
            return _Result("RISK_SCORE: 0.9\nREASON: action risk")
        if "Choose the protocol" in prompt:
            return _Result(f"SELECTED: {self.selected_protocol}\nREASON: requested action")
        raise AssertionError(f"unexpected prompt: {prompt[:120]!r}")


class _IntakeAgent:
    def __init__(self, payload):
        self.payload = payload
        self.calls = 0

    def process(self, prompt, allowed_tools, **kwargs):
        self.calls += 1
        return _Result(json.dumps(self.payload))


@pytest.fixture
def unified_action_deps(monkeypatch, tmp_path):
    from profiles import unified_test

    history_path = str(tmp_path / "history.db")
    surveillance_path = str(tmp_path / "surveillance.db")
    team_path = str(tmp_path / "team-status.db")
    monkeypatch.setattr(unified_test, "DB_PATH", history_path)
    monkeypatch.setattr(unified_test, "UNIFIED_SURVEILLANCE_DB_PATH", surveillance_path)
    monkeypatch.setattr(unified_test, "UNIFIED_TEAM_STATUS_DB_PATH", team_path)
    monkeypatch.setattr(unified_test.UnifiedSurveillanceAgent, "surveillance_db_path", surveillance_path)
    monkeypatch.setattr(unified_test.UnifiedTeamStatusAgent, "status_db_path", team_path)
    unified_test.ensure_seed_data()

    surveillance = unified_test.UnifiedSurveillanceAgent(model="mock")
    team_status = unified_test.UnifiedTeamStatusAgent(model="mock")
    friendly_forces = unified_test.UnifiedFriendlyForcesAgent(model="mock")
    persistence = open_persistence(history_path)
    settings = MagicMock()
    settings.get_risk_threshold.return_value = 0.6
    deps = FlowDeps(
        persistence=persistence,
        settings_store=settings,
        registry=AgentRegistry({
            "surveillance_agent": surveillance,
            "team_status_agent": team_status,
            "friendly_forces_agent": friendly_forces,
            "history_agent": HistoryAgent(model="mock"),
        }),
        protocol_set=ProtocolSet(unified_test.PROTOCOLS),
        event_type_registry=EventTypeRegistry(tuple(unified_test.EVENT_TYPES)),
        area_registry=AreaRegistry(tuple(unified_test.AREAS)),
        history_query_service=MagicMock(),
        optimization_policy=OptimizationPolicy(
            operational_decision_mode="separate",
            operational_intake_mode="single",
            deterministic_execution_mode="direct",
        ),
        timezone_name=unified_test.TIMEZONE,
        loaded_profile=SimpleNamespace(
            live_operational_profile=RESPONSE_TEAM,
            operational_profile_protocol_gating=True,
            simulations=unified_test.SIMULATIONS,
        ),
    )
    yield deps, unified_test
    persistence.close()


def _structured_action_as_question(text):
    return json.dumps({
        "primary_intent": "question",
        "asks_for_information": True,
        "reports_occurrence": False,
        "requests_action": True,
        "social_only": False,
        "is_quoted": False,
        "is_hypothetical": False,
        "is_followup_without_context": False,
        "evidence": {"question": text},
        "matched_protocol_names": ["dispatch_response"],
        "reason": "the user asked for an action",
        "ambiguity_reason": None,
        "clarification_question": None,
    })


def test_structured_action_signal_cannot_enter_question_response_branch(tmp_path):
    text = "dispatch a response to the north gate"
    agent = MagicMock()
    agent.process.return_value = _Result(_structured_action_as_question(text))
    ctx = build_context(tmp_path, main_agent=agent)
    client = build_app(ctx).test_client()

    response = client.post(
        "/Msg",
        headers=auth_headers(VIEWER_IDENTITY),
        json={"text": text, "sender_identity": VIEWER_IDENTITY},
    )

    assert response.status_code == 202
    assert response.get_json()["taken_as"] == "request"
    event = ctx.deps.persistence.fetch_event(response.get_json()["event_id"])
    assert event["classification"] == "human_activation"
    ctx.queue.stop()
    ctx.deps.persistence.close()


def test_action_guard_uses_typed_intent_signal_not_message_keywords(unified_action_deps):
    deps, _unified_test = unified_action_deps
    result = enforce_action_routing_guard(
        deps,
        IntentResult(
            "question",
            "model primary intent was inconsistent",
            requests_action=True,
            matched_protocol_names=("query_drone_fleet_status",),
        ),
    )

    assert result.intent == "request"
    assert result.matched_protocol_names == ("query_drone_fleet_status",)


def test_unified_test_action_candidates_are_side_effecting_and_queries_are_not(unified_action_deps):
    deps, unified_test = unified_action_deps
    action_names = {protocol.name for protocol in action_protocols_for_request(deps)}

    assert {
        "dispatch_emergency_forces",
        "dispatch_drone_to_incident",
        "recall_drone_to_base",
    } <= action_names
    assert "dispatch_mutual_aid" not in action_names
    for name in (
        "overall_situational_picture",
        "query_drone_fleet_status",
        "query_camera_status",
        "report_team_availability",
    ):
        protocol = next(item for item in unified_test.PROTOCOLS if item.name == name)
        assert protocol_has_side_effects(deps, protocol) is False
        assert name not in action_names


def test_fire_scope_action_candidates_include_mutual_aid(unified_action_deps):
    deps, _unified_test = unified_action_deps
    scope = OperationalScope.simulation("FIRE_002_PHASE_1", "run-profile-gate")

    action_names = {
        protocol.name for protocol in action_protocols_for_request(deps, scope)
    }

    assert "dispatch_mutual_aid" in action_names


def test_generic_selection_cannot_return_mutual_aid_for_response_team(
    unified_action_deps, monkeypatch
):
    deps, _unified_test = unified_action_deps
    monkeypatch.setattr(
        "orchestrator.flows.assess_risk",
        lambda *args, **kwargs: RiskAssessment(0.9, "high", "action risk"),
    )
    monkeypatch.setattr(
        "orchestrator.flows.select_protocol",
        lambda *args, **kwargs: ProtocolSelectionResult(
            status="selected",
            protocol_name="dispatch_mutual_aid",
            reason="attempted global-catalogue bypass",
        ),
    )
    event_id = begin_request(
        deps,
        "request fire mutual aid",
        datetime.now(timezone.utc).isoformat(),
        VIEWER_IDENTITY,
    )

    result = continue_from_risk_assessment(
        deps, event_id, MagicMock(), MagicMock()
    )

    assert result.outcome == "no_match_protocol"
    assert deps.persistence.fetch_event(event_id)["selected_protocol"] is None


def test_deterministic_selection_cannot_bypass_response_team_profile(
    unified_action_deps, monkeypatch
):
    deps, unified_test = unified_action_deps
    protocol = next(
        item for item in unified_test.PROTOCOLS
        if item.name == "dispatch_mutual_aid"
    )
    monkeypatch.setattr("orchestrator.flows.look_up_precedent", lambda *args, **kwargs: ())
    event_id = begin_request(
        deps,
        "request fire mutual aid",
        datetime.now(timezone.utc).isoformat(),
        VIEWER_IDENTITY,
    )

    result = continue_from_risk_assessment(
        deps,
        event_id,
        MagicMock(),
        MagicMock(),
        selected_protocol=protocol,
    )

    assert result.outcome == "no_match_protocol"
    assert deps.persistence.fetch_event(event_id)["action_state"] is None


def test_explicit_protocol_hint_cannot_bypass_response_team_profile(
    unified_action_deps, tmp_path
):
    deps, _unified_test = unified_action_deps
    deps.persistence.write_user(COMMANDER_IDENTITY, "commander")
    base_ctx = build_context(tmp_path)
    base_ctx.queue.stop()
    base_ctx.deps.persistence.close()
    base_ctx.loaded_profile.live_operational_profile = RESPONSE_TEAM
    base_ctx.loaded_profile.operational_profile_protocol_gating = True
    base_ctx.loaded_profile.simulations = deps.loaded_profile.simulations
    scoped_deps = dataclasses.replace(
        deps,
        loaded_profile=base_ctx.loaded_profile,
    )
    ctx = dataclasses.replace(base_ctx, deps=scoped_deps)
    ctx.queue.start()
    client = build_app(ctx).test_client()

    response = client.post(
        "/Msg",
        headers=auth_headers(COMMANDER_IDENTITY),
        json={
            "text": "dispatch mutual aid",
            "sender_identity": COMMANDER_IDENTITY,
            "protocol_hint": "dispatch_mutual_aid",
        },
    )

    assert response.status_code == 400
    assert response.get_json()["field"] == "protocol_hint"
    assert "dispatch_mutual_aid" in response.get_json()["message"]
    assert deps.registry.get("friendly_forces_agent").dispatches_recorded == []
    ctx.queue.stop()


def test_fire_scope_mutual_aid_reaches_approval_without_tool_execution(
    unified_action_deps, monkeypatch
):
    deps, unified_test = unified_action_deps
    protocol = next(
        item for item in unified_test.PROTOCOLS
        if item.name == "dispatch_mutual_aid"
    )
    forces = deps.registry.get("friendly_forces_agent")
    monkeypatch.setattr("orchestrator.flows.look_up_precedent", lambda *args, **kwargs: ())
    event_id = begin_request(
        deps,
        "request two water tankers",
        datetime.now(timezone.utc).isoformat(),
        VIEWER_IDENTITY,
        simulation_context=SimpleNamespace(
            scenario_id="FIRE_002_PHASE_1",
            scenario_run_id="run-profile-gate",
            scenario_time="2026-09-09T07:30:00+00:00",
        ),
    )

    result = continue_from_risk_assessment(
        deps,
        event_id,
        MagicMock(),
        MagicMock(),
        selected_protocol=protocol,
    )

    event = deps.persistence.fetch_event(event_id)
    assert result.outcome == "held_for_approval"
    assert event["selected_protocol"] == "dispatch_mutual_aid"
    assert event["action_state"] == "pending_approval"
    assert forces.dispatches_recorded == []


def test_approved_fire_mutual_aid_uses_normal_lifecycle_and_receipt(
    unified_action_deps, monkeypatch
):
    deps, unified_test = unified_action_deps
    protocol = next(
        item for item in unified_test.PROTOCOLS
        if item.name == "dispatch_mutual_aid"
    )
    forces = deps.registry.get("friendly_forces_agent")
    deps.settings_store.get_retry_count.return_value = 3
    monkeypatch.setattr("orchestrator.flows.look_up_precedent", lambda *args, **kwargs: ())
    event_id = begin_request(
        deps,
        "request two water tankers",
        datetime.now(timezone.utc).isoformat(),
        VIEWER_IDENTITY,
        simulation_context=SimpleNamespace(
            scenario_id="FIRE_002_PHASE_1",
            scenario_run_id="run-approved-profile-gate",
            scenario_time="2026-09-09T07:30:00+00:00",
        ),
    )
    held = continue_from_risk_assessment(
        deps,
        event_id,
        MagicMock(),
        MagicMock(),
        selected_protocol=protocol,
    )
    assert held.outcome == "held_for_approval"
    assert forces.dispatches_recorded == []

    [hold] = deps.persistence.list_held_events("approval")
    answer = resolve_approval(
        deps,
        hold["hold_id"],
        "commander-1",
        PermissionLevel.COMMANDER,
        "approved",
    )
    assert answer.status == "approved"
    assert forces.dispatches_recorded == []

    monkeypatch.setattr(
        "orchestrator.flows.formulate_tasks",
        lambda *args, **kwargs: FormulationResult(
            steps=(
                Step(
                    agent_name="friendly_forces_agent",
                    task_text="dispatch two water tankers",
                    allowed_tools=("dispatch_water_tankers",),
                    step_id="mutual-aid-1",
                    direct_tool_name="dispatch_water_tankers",
                    direct_tool_arguments={
                        "location": "north_sector",
                        "tanker_count": 2,
                    },
                ),
            )
        ),
    )
    monkeypatch.setattr("orchestrator.flows.build_insight", lambda *args, **kwargs: "recorded")
    monkeypatch.setattr(
        "orchestrator.flows.judge_success",
        lambda *args, **kwargs: SuccessVerdict("success", "verified receipt"),
    )

    result = continue_after_approval(
        deps,
        event_id,
        MagicMock(),
        MagicMock(),
        "dispatch_mutual_aid",
    )

    event = deps.persistence.fetch_event(event_id)
    assert result.outcome == "succeeded"
    assert event["action_state"] == "executed"
    assert event["action_tool_receipts"][0]["tool_name"] == "dispatch_water_tankers"
    assert event["steps"][0]["tool_receipts"][0]["status"] == "succeeded"
    assert len(forces.dispatches_recorded) == 1


@pytest.mark.parametrize(
    ("selected_protocol", "text"),
    (
        ("dispatch_emergency_forces", "send emergency forces to the north gate"),
        ("dispatch_drone_to_incident", "dispatch a drone to the north gate"),
        ("recall_drone_to_base", "return the drone to base"),
    ),
)
def test_unified_test_action_requests_stay_on_action_lifecycle_path(
    unified_action_deps, monkeypatch, selected_protocol, text
):
    deps, _unified_test = unified_action_deps
    monkeypatch.setattr("orchestrator.flows.look_up_precedent", lambda *args, **kwargs: ())
    event_id = begin_request(
        deps,
        text,
        datetime.now(timezone.utc).isoformat(),
        VIEWER_IDENTITY,
        sender_permission_level="viewer",
    )
    agent = _SelectionAgent(selected_protocol)

    result = continue_from_risk_assessment(deps, event_id, agent, MagicMock())

    assert result.outcome == "held_for_approval"
    event = deps.persistence.fetch_event(event_id)
    assert event["selected_protocol"] == selected_protocol
    assert event["action_state"] == "pending_approval"
    selection_prompt = next(prompt for prompt in agent.calls if "Choose the protocol" in prompt)
    assert "query_drone_fleet_status" not in selection_prompt
    assert "query_camera_status" not in selection_prompt


def test_fallback_rejects_a_read_only_protocol_for_an_action_request(unified_action_deps, monkeypatch):
    deps, _unified_test = unified_action_deps
    monkeypatch.setattr("orchestrator.flows.look_up_precedent", lambda *args, **kwargs: ())
    event_id = begin_request(
        deps,
        "perform an unsupported action",
        datetime.now(timezone.utc).isoformat(),
        VIEWER_IDENTITY,
    )
    decision = OperationalDecision(
        RiskAssessment(score=0.1, level="low", reason="low risk"),
        ProtocolSelectionResult(status="selected", protocol_name="query_drone_fleet_status", reason="incorrect read-only match"),
    )

    result = continue_from_risk_assessment(deps, event_id, MagicMock(), MagicMock(), operational_decision=decision)

    assert result.outcome == "no_match_protocol"
    event = deps.persistence.fetch_event(event_id)
    assert event["selected_protocol"] is None
    assert event["action_state"] is None


def test_fast_path_does_not_direct_execute_a_typed_action(unified_action_deps):
    deps, unified_test = unified_action_deps
    payload = {
        "intent": {
            "value": "request",
            "confident": True,
            "asks_for_information": False,
            "reports_occurrence": False,
            "requests_action": True,
            "social_only": False,
            "is_quoted": False,
            "is_hypothetical": False,
            "evidence": "dispatch a drone",
        },
        "classification": {
            "name": None,
            "confident": False,
            "area": None,
            "entities": [],
            "description": None,
            "severity": None,
            "occurred_at": None,
        },
        "business_fields": {"availability": None, "reason": None},
        "risk": {"risk_score": 0.9, "risk_reason": "action"},
        "protocol": {
            "status": "selected",
            "name": "dispatch_drone_to_incident",
            "candidate_names": ["dispatch_drone_to_incident"],
            "reason": "requested action",
        },
        "temporal": {"expression": None, "availability_start": None, "availability_end": None},
    }
    agent = _IntakeAgent(payload)

    plan = prepare_fast_path_report(
        deps,
        agent,
        "dispatch a drone",
        datetime.now(timezone.utc).isoformat(),
        False,
    )

    assert unified_test.OPTIMIZATION_POLICY.operational_intake_mode == "single"
    assert unified_test.OPTIMIZATION_POLICY.deterministic_execution_mode == "direct"
    assert agent.calls == 1
    assert plan is None
