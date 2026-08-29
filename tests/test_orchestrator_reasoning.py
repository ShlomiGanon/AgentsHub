"""Core orchestration reasoning behavior."""

import types

import pytest

from agents import adapter
from config.base import BaseConfig, TierModel
from orchestrator.main_agent import OrchestrationParseError
from orchestrator.main_agent import (
    MainAgent,
    RiskAssessment,
    _build_risk_assessment_prompt,
    _parse_risk_assessment_response,
    assess_risk,
    construct_core_agents,
)


def test_main_agent_has_no_tools_of_its_own():
    agent = MainAgent(model="m")

    assert agent.exposed_tools() == ()


def test_main_agent_construction_requires_no_special_setup():
    agent = MainAgent(model="some-model")

    assert agent.name == "main_agent"
    assert agent.model == "some-model"


# -- Pure prompt/parse functions ------------------------------------------


def test_build_prompt_includes_all_fields():
    prompt = _build_risk_assessment_prompt("fire", "north_sector", "smoke at gate 3", "moderate")

    assert "fire" in prompt
    assert "north_sector" in prompt
    assert "smoke at gate 3" in prompt
    assert "moderate" in prompt


def test_build_prompt_handles_missing_fields():
    prompt = _build_risk_assessment_prompt(None, None, None, None)

    assert "unresolved" in prompt
    assert "none provided" in prompt


def test_parse_valid_response():
    score, reason = _parse_risk_assessment_response("RISK_SCORE: 0.8\nREASON: multiple prior incidents nearby")

    assert score == 0.8
    assert reason == "multiple prior incidents nearby"


def test_parse_rejects_missing_score():
    with pytest.raises(OrchestrationParseError):
        _parse_risk_assessment_response("REASON: no score given")


# --- Stage 6 (docs/Next_Plan.md §11): refusal of unknown model-generated
# operations. A model never emits a `RequestedOperation` directly — it only
# ever emits `primary_intent` (message-plan JSON) or `operation`
# (history-query JSON), both closed vocabularies the application validates
# before any operation-mapping or execution happens; RequestedOperation
# itself is decided by application code (api/routes.py), never parsed from
# model output. These two closed vocabularies are what "requested-operation
# parsing" actually means in this architecture.


def test_structured_intent_rejects_an_invented_primary_intent():
    from orchestrator.main_agent import _parse_structured_intent_response

    payload = '{"primary_intent": "delete_everything"}'

    with pytest.raises(OrchestrationParseError, match="invalid primary_intent"):
        _parse_structured_intent_response(payload, "some message", ())


def test_structured_intent_accepts_every_valid_primary_intent_shape():
    from orchestrator.main_agent import _parse_structured_intent_response

    for intent in ("question", "report", "request", "conversational"):
        flag_field = {
            "question": "asks_for_information", "report": "reports_occurrence",
            "request": "requests_action", "conversational": "social_only",
        }[intent]
        payload = {
            "primary_intent": intent, "asks_for_information": False, "reports_occurrence": False,
            "requests_action": False, "social_only": False, "is_quoted": False, "is_hypothetical": False,
            "is_followup_without_context": False, "evidence": {intent: "gate 3"} if intent != "conversational" else {},
            "matched_protocol_names": [], "reason": "matches", "ambiguity_reason": None, "clarification_question": None,
        }
        payload[flag_field] = True
        result = _parse_structured_intent_response(
            __import__("json").dumps(payload), "status at gate 3", ()
        )
        assert result.intent == intent


def test_history_query_spec_rejects_an_invented_operation():
    from orchestrator.main_agent import _history_query_spec_from_payload

    with pytest.raises(OrchestrationParseError, match="invalid history operation"):
        _history_query_spec_from_payload({"operation": "delete_all_events"})


def test_history_query_spec_accepts_every_valid_operation():
    from orchestrator.main_agent import _history_query_spec_from_payload

    for operation in ("latest", "event_details", "list", "count", "aggregate", "compare", "similar_cases", "narrative"):
        spec = _history_query_spec_from_payload({"operation": operation})
        assert spec.operation == operation


def test_history_query_spec_rejects_an_invented_time_basis():
    from orchestrator.main_agent import _history_query_spec_from_payload

    with pytest.raises(OrchestrationParseError):
        _history_query_spec_from_payload({"operation": "latest", "time_basis": "invented_at"})


def test_parse_rejects_missing_reason():
    with pytest.raises(OrchestrationParseError):
        _parse_risk_assessment_response("RISK_SCORE: 0.5")


def test_parse_rejects_out_of_range_score():
    with pytest.raises(OrchestrationParseError):
        _parse_risk_assessment_response("RISK_SCORE: 1.5\nREASON: too high")


# -- assess_risk ------------------------------------------------------------


class _ScriptedMainAgent:
    def __init__(self, response_text):
        self._response_text = response_text
        self.calls = []

    def process(self, text, allowed_tools):
        self.calls.append((text, allowed_tools))

        class _Result:
            status = "success"
            text = self._response_text

        return _Result()


def test_assess_risk_derives_high_when_score_meets_threshold():
    agent = _ScriptedMainAgent("RISK_SCORE: 0.6\nREASON: matches threshold exactly")

    assessment = assess_risk(agent, "fire", "north", "d", "s", risk_threshold=0.6)

    assert assessment == RiskAssessment(score=0.6, level="high", reason="matches threshold exactly")


def test_assess_risk_derives_low_when_score_is_below_threshold():
    agent = _ScriptedMainAgent("RISK_SCORE: 0.2\nREASON: minor")

    assessment = assess_risk(agent, "fire", "north", "d", "s", risk_threshold=0.6)

    assert assessment.level == "low"


def test_assess_risk_passes_no_tools():
    agent = _ScriptedMainAgent("RISK_SCORE: 0.5\nREASON: r")

    assess_risk(agent, "fire", "north", "d", "s", risk_threshold=0.5)

    assert agent.calls[0][1] == []


def test_assess_risk_raises_when_the_agent_reports_the_task_unclear():
    class _UnclearAgent:
        def process(self, text, allowed_tools):
            class _Result:
                status = "unclear_task"
                text = "missing context"

            return _Result()

    with pytest.raises(OrchestrationParseError):
        assess_risk(_UnclearAgent(), "fire", "north", "d", "s", risk_threshold=0.5)


def test_assess_risk_end_to_end_through_the_mocked_adapter(monkeypatch):
    class _FakeOutput:
        def __init__(self, raw):
            self.raw = raw

    class _FakeAgent:
        def __init__(self, **kwargs):
            pass

        def kickoff(self, text):
            return _FakeOutput("RISK_SCORE: 0.9\nREASON: sensor confirms active fire")

    fake_module = types.SimpleNamespace(Agent=_FakeAgent, LLM=lambda **kwargs: kwargs["model"], tools=types.SimpleNamespace(BaseTool=object))
    monkeypatch.setattr(adapter, "_get_crewai", lambda: fake_module)

    main_agent = MainAgent(model="fake-model")
    assessment = assess_risk(main_agent, "fire", "north_sector", "active fire", "high", risk_threshold=0.5)

    assert assessment.level == "high"
    assert assessment.score == 0.9


# -- construct_core_agents ---------------------------------------------------


def test_construct_core_agents_returns_the_main_agent_with_the_configured_model():
    base_config = BaseConfig(core_model=TierModel(model="the-main-model", api_key="the-core-key"))

    core_agents = construct_core_agents(base_config)

    assert set(core_agents) == {"main_agent"}
    assert core_agents["main_agent"].model == "the-main-model"
    assert core_agents["main_agent"].descriptor.api_key == "the-core-key"
    assert isinstance(core_agents["main_agent"], MainAgent)
