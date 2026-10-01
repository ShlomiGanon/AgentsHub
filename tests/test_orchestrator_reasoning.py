"""Core orchestration reasoning behavior."""

import types

import pytest

from agents import adapter
from config.base import BaseConfig, TierModel
from messages import get_catalog
from orchestrator.main_agent import OrchestrationParseError
from orchestrator.main_agent import (
    MainAgent,
    RiskAssessment,
    _build_risk_assessment_prompt,
    _parse_risk_assessment_response,
    assess_risk,
    construct_core_agents,
    formulate_event_data_question,
    extract_and_decide,
    make_operational_decision,
)
from profiles import AreaRegistry, EventTypeRegistry
from protocols.model import CriticalityLevel, Protocol


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


def test_intent_prompt_no_longer_hardcodes_a_single_evidence_key_example():
    from orchestrator.main_agent import _build_intent_prompt

    prompt = _build_intent_prompt("smoke observed near gate 3", ())

    assert '"evidence":{"question":"exact quote from message"}' not in prompt
    assert "evidence" in prompt and "primary_intent" in prompt


def test_structured_intent_accepts_evidence_keyed_differently_from_primary_intent():
    """Regression test for the bug where the model copies the prompt's own example evidence
    key ("question") verbatim regardless of the real primary_intent — a report/request
    classification with genuine, message-quoted evidence must not be rejected just because the
    evidence dict's key doesn't literally match primary_intent's value (docs/IMPROVES/
    CRITICAL_FIXES_PLAN.MD item 2)."""

    from orchestrator.main_agent import _parse_structured_intent_response

    payload = {
        "primary_intent": "report", "asks_for_information": False, "reports_occurrence": True,
        "requests_action": False, "social_only": False, "is_quoted": False, "is_hypothetical": False,
        "is_followup_without_context": False,
        "evidence": {"question": "smoke observed near gate 3"},  # mismatched key, real quote
        "matched_protocol_names": [], "reason": "reports smoke at gate 3",
        "ambiguity_reason": None, "clarification_question": None,
    }

    result = _parse_structured_intent_response(
        __import__("json").dumps(payload), "smoke observed near gate 3", ()
    )

    assert result.intent == "report"


def test_structured_intent_still_rejects_an_operational_intent_with_no_evidence_at_all():
    from orchestrator.main_agent import _parse_structured_intent_response

    payload = {
        "primary_intent": "report", "asks_for_information": False, "reports_occurrence": True,
        "requests_action": False, "social_only": False, "is_quoted": False, "is_hypothetical": False,
        "is_followup_without_context": False, "evidence": {},
        "matched_protocol_names": [], "reason": "reports smoke at gate 3",
        "ambiguity_reason": None, "clarification_question": None,
    }

    with pytest.raises(OrchestrationParseError, match="requires exact evidence"):
        _parse_structured_intent_response(
            __import__("json").dumps(payload), "smoke observed near gate 3", ()
        )


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

    def process(self, text, allowed_tools, *, invocation_policy=None):
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


# -- make_operational_decision: fence unwrapping / protocol_status alias -----


def _one_protocol():
    return (
        Protocol(
            name="record_attendance",
            description="applies to an attendance report",
            participating_agents=("reference_agent",),
            approved_tools=("record_action",),
            expected_success_output="confirmation attendance was recorded",
            criticality=CriticalityLevel.LOW,
            approval_flag=False,
        ),
    )


def test_make_operational_decision_parses_a_markdown_fenced_response_on_the_first_attempt():
    # A model asked for bare JSON commonly wraps it in a ```json fence anyway -- this must
    # not cost a wasted repair call (_structured_call_with_one_repair's shared parsing path),
    # since the fenced content itself is already well-formed.
    fenced = (
        "```json\n"
        '{"risk_score": 0.1, "risk_reason": "routine", "protocol_status": "selected", '
        '"protocol_name": "record_attendance", "candidate_names": [], "protocol_reason": "matches"}'
        "\n```"
    )
    agent = _ScriptedMainAgent(fenced)

    decision = make_operational_decision(agent, "x", "attendance", "north", "d", None, _one_protocol(), risk_threshold=0.5)

    assert decision.risk.level == "low"
    assert decision.selection.status == "selected"
    assert decision.selection.protocol_name == "record_attendance"
    assert len(agent.calls) == 1  # parsed on the first attempt -- no repair call spent


def test_make_operational_decision_accepts_match_as_an_alias_for_selected():
    # Observed in a live model response in place of the literal "selected" -- accepted as
    # an alias rather than failing a decision the model otherwise expressed correctly.
    agent = _ScriptedMainAgent(
        '{"risk_score": 0.6, "risk_reason": "confirmed", "protocol_status": "MATCH", '
        '"protocol_name": "record_attendance", "candidate_names": [], "protocol_reason": "matches"}'
    )

    decision = make_operational_decision(agent, "x", "attendance", "north", "d", None, _one_protocol(), risk_threshold=0.5)

    assert decision.selection.status == "selected"
    assert decision.selection.protocol_name == "record_attendance"
    assert len(agent.calls) == 1


def test_make_operational_decision_auto_resolves_a_high_risk_ambiguous_selection_to_the_most_critical_candidate():
    # #20's exact live output from this session's own isolated-stack verification: a real
    # ambiguous decision between report_security_incident (HIGH) and report_team_movement
    # (LOW) at risk_score=0.93 -- must not stop for clarification on a message like this,
    # mirroring select_protocol's own high-risk auto-resolve on the separate path.
    agent = _ScriptedMainAgent(
        '{"risk_score": 0.93, "risk_reason": "gunfire reported near the west gate", '
        '"protocol_status": "ambiguous", "protocol_name": null, '
        '"candidate_names": ["report_security_incident", "report_team_movement"], '
        '"protocol_reason": "could not discriminate between an active incident report and a team movement update"}'
    )
    protocols = (
        Protocol(
            name="report_security_incident", description="applies to a security incident",
            participating_agents=("surveillance_agent",), approved_tools=("dispatch_drone_to_area",),
            expected_success_output="a logged incident", criticality=CriticalityLevel.HIGH, approval_flag=False,
        ),
        Protocol(
            name="report_team_movement", description="applies to a team movement update",
            participating_agents=("reference_agent",), approved_tools=("report_team_movement",),
            expected_success_output="a logged movement", criticality=CriticalityLevel.LOW, approval_flag=False,
        ),
    )

    decision = make_operational_decision(agent, "x", None, "west_gate", "d", None, protocols, risk_threshold=0.5)

    assert decision.risk.level == "high"
    assert decision.selection.status == "selected"
    assert decision.selection.protocol_name == "report_security_incident"
    assert "high risk" in decision.selection.reason


def test_make_operational_decision_auto_resolves_an_ambiguous_selection_with_a_safety_critical_candidate_even_at_low_risk():
    agent = _ScriptedMainAgent(
        '{"risk_score": 0.1, "risk_reason": "seems routine", '
        '"protocol_status": "ambiguous", "protocol_name": null, '
        '"candidate_names": ["report_security_incident", "report_team_movement"], '
        '"protocol_reason": "could not discriminate"}'
    )
    protocols = (
        Protocol(
            name="report_security_incident", description="applies to a security incident",
            participating_agents=("surveillance_agent",), approved_tools=("dispatch_drone_to_area",),
            expected_success_output="a logged incident", criticality=CriticalityLevel.HIGH, approval_flag=False,
            safety_critical=True,
        ),
        Protocol(
            name="report_team_movement", description="applies to a team movement update",
            participating_agents=("reference_agent",), approved_tools=("report_team_movement",),
            expected_success_output="a logged movement", criticality=CriticalityLevel.LOW, approval_flag=False,
        ),
    )

    decision = make_operational_decision(agent, "x", None, "west_gate", "d", None, protocols, risk_threshold=0.5)

    assert decision.risk.level == "low"  # the safety_critical candidate is what must trigger this, not risk
    assert decision.selection.status == "selected"
    assert decision.selection.protocol_name == "report_security_incident"


def _extract_and_decide_payload(**overrides):
    payload = {
        "classification": "attendance",
        "area": "north",
        "entities": [],
        "description": "available today",
        "severity": None,
        "occurred_at": None,
        "availability_start": None,
        "availability_end": None,
        "absence_reason": None,
        "risk_score": 0.1,
        "risk_reason": "routine attendance",
        "protocol_status": "selected",
        "protocol_name": "record_attendance",
        "candidate_names": [],
        "protocol_reason": "matches attendance",
    }
    payload.update(overrides)
    import json
    return json.dumps(payload)


def test_extract_and_decide_returns_the_same_fields_as_the_two_step_path():
    agent = _ScriptedMainAgent(_extract_and_decide_payload())
    extraction, decision = extract_and_decide(
        agent,
        "available today",
        "telegram",
        "2026-08-20T10:00:00",
        EventTypeRegistry(("attendance", "fire")),
        AreaRegistry(("north",)),
        _one_protocol(),
        risk_threshold=0.5,
    )

    assert extraction.classification == "attendance"
    assert extraction.area == "north"
    assert extraction.description == "available today"
    assert decision is not None
    assert decision.risk.level == "low"
    assert decision.selection.status == "selected"
    assert decision.selection.protocol_name == "record_attendance"
    assert len(agent.calls) == 1


def test_extract_and_decide_falls_back_to_extraction_only_when_operational_fields_are_unusable():
    agent = _ScriptedMainAgent(_extract_and_decide_payload(risk_score=2, protocol_reason=""))
    extraction, decision = extract_and_decide(
        agent,
        "available today",
        "telegram",
        "2026-08-20T10:00:00",
        EventTypeRegistry(("attendance", "fire")),
        AreaRegistry(("north",)),
        _one_protocol(),
        risk_threshold=0.5,
    )

    assert extraction.classification == "attendance"
    assert decision is None


def test_make_operational_decision_leaves_a_low_risk_non_safety_critical_ambiguity_unresolved():
    agent = _ScriptedMainAgent(
        '{"risk_score": 0.1, "risk_reason": "seems routine", '
        '"protocol_status": "ambiguous", "protocol_name": null, '
        '"candidate_names": ["record_attendance", "report_team_movement"], '
        '"protocol_reason": "could not discriminate"}'
    )
    protocols = (
        Protocol(
            name="record_attendance", description="applies to an attendance report",
            participating_agents=("reference_agent",), approved_tools=("record_action",),
            expected_success_output="confirmation", criticality=CriticalityLevel.LOW, approval_flag=False,
        ),
        Protocol(
            name="report_team_movement", description="applies to a team movement update",
            participating_agents=("reference_agent",), approved_tools=("report_team_movement",),
            expected_success_output="a logged movement", criticality=CriticalityLevel.LOW, approval_flag=False,
        ),
    )

    decision = make_operational_decision(agent, "x", None, "west_gate", "d", None, protocols, risk_threshold=0.5)

    assert decision.selection.status == "ambiguous"
    assert decision.selection.candidate_names == ("record_attendance", "report_team_movement")


# -- construct_core_agents ---------------------------------------------------


def test_construct_core_agents_returns_the_main_agent_with_the_configured_model():
    base_config = BaseConfig(core_model=TierModel(model="the-main-model", api_key="the-core-key"))

    core_agents = construct_core_agents(base_config)

    assert set(core_agents) == {"main_agent"}
    assert core_agents["main_agent"].model == "the-main-model"
    assert core_agents["main_agent"].descriptor.api_key == "the-core-key"
    assert isinstance(core_agents["main_agent"], MainAgent)


# -- formulate_event_data_question: banned-opener retry/fallback -------------


class _SequentialScriptedMainAgent:
    def __init__(self, response_texts):
        self._responses = list(response_texts)
        self.calls = []

    def process(self, text, allowed_tools):
        self.calls.append((text, allowed_tools))

        class _Result:
            status = "success"
            text = self._responses[len(self.calls) - 1]

        return _Result()


def test_event_data_question_retries_once_after_a_banned_opener():
    agent = _SequentialScriptedMainAgent([
        "Your report was received. Please provide the missing area.",
        "Which area were you reporting from?",
    ])

    question = formulate_event_data_question(agent, {"raw_text": "camera issue"}, ("area",), (), get_catalog("en"))

    assert question == "Which area were you reporting from?"
    assert len(agent.calls) == 2
    assert "banned phrase" in agent.calls[1][0]


def test_event_data_question_falls_back_to_the_deterministic_catalog_template():
    agent = _SequentialScriptedMainAgent([
        "Your report was received, please clarify the area.",
        "Your report was received once more, still missing the area.",
    ])

    question = formulate_event_data_question(agent, {"raw_text": "camera issue"}, ("area",), (), get_catalog("en"))

    assert len(agent.calls) == 2
    assert not question.startswith("Your report was received")
    assert "Additional details are needed" in question


def test_event_data_question_without_a_catalog_skips_the_tone_check():
    agent = _SequentialScriptedMainAgent(["Your report was received, please clarify the area."])

    question = formulate_event_data_question(agent, {"raw_text": "camera issue"}, ("area",), ())

    assert question == "Your report was received, please clarify the area."
    assert len(agent.calls) == 1


def test_message_plan_prompt_instructs_wide_scope_narrative_routing_for_a_debrief_request():
    """Memory/continuity audit fix 7 (debrief coherence): the plan prompt must steer a
    debrief/timeline request to a wide, unfiltered history query -- covering every event type
    across the whole incident -- rather than whatever narrow scope the model would otherwise
    guess, so the debrief isn't accidentally limited to the single most recent event."""

    import json as _json

    from agents.contracts import AgentResult
    from agents.runtime import AgentRegistry
    from orchestrator.main_agent import plan_message

    class _FakeMainAgent:
        def __init__(self):
            self.prompts: list[str] = []

        def process(self, text, allowed_tools, *, invocation_policy=None):
            self.prompts.append(text)
            payload = {
                "primary_intent": "question", "asks_for_information": True, "reports_occurrence": False,
                "requests_action": False, "social_only": False, "is_quoted": False, "is_hypothetical": False,
                "is_followup_without_context": False,
                "evidence": {"question": "produce an initial debrief", "report": "", "request": ""},
                "matched_protocol_names": [], "reason": "debrief request", "ambiguity_reason": None,
                "clarification_question": None,
                "question_plan": {
                    "route": "history", "reason": "debrief",
                    "history_query": {
                        "operation": "narrative", "time_start": None, "time_end": None,
                        "time_basis": "occurred_at", "classifications": [], "areas": [], "outcomes": [],
                        "protocol_names": [], "event_ids": [], "risk_levels": [], "order": "oldest",
                        "group_by": "none", "limit": 50,
                    },
                    "tasks": [],
                },
                "conversational_reply": None,
            }
            return AgentResult("success", _json.dumps(payload))

    main_agent = _FakeMainAgent()
    registry = AgentRegistry({})
    protocol = Protocol(
        name="query_historical_incidents", description="debrief",
        participating_agents=("history_agent",), approved_tools=(), expected_success_output="x",
        criticality=CriticalityLevel.LOW, approval_flag=False,
    )

    plan_message(main_agent, (protocol,), "produce an initial debrief", registry, history_query_service=None)

    prompt = main_agent.prompts[0]
    assert 'operation="narrative"' in prompt
    assert "Leave classifications and areas empty" in prompt
    assert "not just the most recent event" in prompt
