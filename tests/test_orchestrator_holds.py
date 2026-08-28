import pytest

from auth.permissions import PermissionLevel
from history.extraction import ExtractionResult
from orchestrator.holds import (
    answer_approval_hold,
    answer_clarification_hold,
    create_approval_hold,
    create_clarification_hold,
    determine_approval_hold,
    determine_clarification_hold,
)
from orchestrator.main_agent import RiskAssessment
from orchestrator.main_agent import ProtocolSelectionResult
from persistence.sqlite_store import SQLitePersistence
from protocols.model import CriticalityLevel, Protocol
from profiles import EventTypeRegistry


def _protocol(name, approval_flag):
    return Protocol(
        name=name,
        description="d",
        participating_agents=(),
        approved_tools=(),
        expected_success_output="x",
        criticality=CriticalityLevel.LOW,
        approval_flag=approval_flag,
    )


@pytest.fixture
def store(tmp_path):
    backend = SQLitePersistence(str(tmp_path / "test.db"))
    yield backend
    backend.close()


# -- determine_approval_hold --------------------------------------------


def test_flagged_protocol_not_commander_holds():
    selection = ProtocolSelectionResult(status="selected", protocol_name="p", reason="r")
    protocols = {"p": _protocol("p", approval_flag=True)}

    assert determine_approval_hold(selection, protocols, originated_from_commander=False) == "flagged_protocol"


def test_flagged_protocol_commander_bypasses_the_flag():
    selection = ProtocolSelectionResult(status="selected", protocol_name="p", reason="r")
    protocols = {"p": _protocol("p", approval_flag=True)}

    assert determine_approval_hold(selection, protocols, originated_from_commander=True) is None


def test_ambiguous_not_commander_holds():
    selection = ProtocolSelectionResult(status="ambiguous", candidate_names=("a", "b"), reason="r")

    assert determine_approval_hold(selection, {}, originated_from_commander=False) == "ambiguous_selection"


def test_ambiguous_commander_still_holds():
    # A commander's authority bypasses the approval flag, not ambiguity —
    # there's no protocol yet for their authority to authorize.
    selection = ProtocolSelectionResult(status="ambiguous", candidate_names=("a", "b"), reason="r")

    assert determine_approval_hold(selection, {}, originated_from_commander=True) == "ambiguous_selection"


def test_unflagged_selected_protocol_never_holds():
    selection = ProtocolSelectionResult(status="selected", protocol_name="p", reason="r")
    protocols = {"p": _protocol("p", approval_flag=False)}

    assert determine_approval_hold(selection, protocols, originated_from_commander=False) is None


# -- create_approval_hold / answer_approval_hold -------------------------


def _selection():
    return ProtocolSelectionResult(status="selected", protocol_name="dispatch_response", reason="matches")


def _risk():
    return RiskAssessment(score=0.8, level="high", reason="active fire")


def test_create_and_answer_round_trip_approved(store):
    hold_id = create_approval_hold(store, "evt-1", "flagged_protocol", _selection(), _risk())

    [held] = store.list_held_events("approval")
    assert held["hold_id"] == hold_id
    assert held["event_id"] == "evt-1"
    assert held["selected_protocol_name"] == "dispatch_response"

    result = answer_approval_hold(store, hold_id, "commander-1", PermissionLevel.COMMANDER, "approved")

    assert result.status == "approved"
    assert result.hold["hold_id"] == hold_id
    assert store.list_held_events("approval") == []


def test_answer_rejected_decision(store):
    hold_id = create_approval_hold(store, "evt-1", "flagged_protocol", _selection(), _risk())

    result = answer_approval_hold(store, hold_id, "commander-1", PermissionLevel.COMMANDER, "rejected")

    assert result.status == "rejected"


def test_viewer_cannot_answer(store):
    hold_id = create_approval_hold(store, "evt-1", "flagged_protocol", _selection(), _risk())

    result = answer_approval_hold(store, hold_id, "viewer-1", PermissionLevel.VIEWER, "approved")

    assert result.status == "unauthorized"
    # the hold is untouched — still unresolved
    assert len(store.list_held_events("approval")) == 1


def test_answering_an_unknown_hold_is_not_found(store):
    result = answer_approval_hold(store, "never-existed", "commander-1", PermissionLevel.COMMANDER, "approved")

    assert result.status == "not_found"


def test_answering_an_already_resolved_hold_is_reported_distinctly(store):
    hold_id = create_approval_hold(store, "evt-1", "flagged_protocol", _selection(), _risk())
    first = answer_approval_hold(store, hold_id, "commander-1", PermissionLevel.COMMANDER, "approved")
    second = answer_approval_hold(store, hold_id, "commander-2", PermissionLevel.COMMANDER, "approved")

    assert first.status == "approved"
    assert second.status == "not_found"  # not silently accepted as a fresh answer


# -- Ambiguous-selection holds: a candidate-protocol decision (§6.4/§6.7) -


def _ambiguous_selection():
    return ProtocolSelectionResult(status="ambiguous", candidate_names=("status_check", "dispatch_response"), reason="both fit")


def test_a_valid_candidate_name_resolves_and_records_the_selection(store):
    hold_id = create_approval_hold(store, "evt-1", "ambiguous_selection", _ambiguous_selection(), _risk())
    [held] = store.list_held_events("approval")
    assert held["selected_protocol_name"] is None  # nothing chosen yet

    result = answer_approval_hold(store, hold_id, "commander-1", PermissionLevel.COMMANDER, "dispatch_response")

    assert result.status == "approved"
    assert result.hold["selected_protocol_name"] == "dispatch_response"
    assert store.list_held_events("approval") == []


def test_a_name_outside_the_holds_own_candidates_is_rejected(store):
    hold_id = create_approval_hold(store, "evt-1", "ambiguous_selection", _ambiguous_selection(), _risk())

    result = answer_approval_hold(store, hold_id, "commander-1", PermissionLevel.COMMANDER, "not_a_real_candidate")

    assert result.status == "invalid_candidate"
    assert "status_check" in result.message and "dispatch_response" in result.message
    # the hold is untouched — still unresolved
    assert len(store.list_held_events("approval")) == 1


def test_approved_and_rejected_are_not_meaningful_answers_to_an_ambiguous_hold(store):
    hold_id = create_approval_hold(store, "evt-1", "ambiguous_selection", _ambiguous_selection(), _risk())

    approved = answer_approval_hold(store, hold_id, "commander-1", PermissionLevel.COMMANDER, "approved")
    assert approved.status == "invalid_candidate"

    rejected = answer_approval_hold(store, hold_id, "commander-1", PermissionLevel.COMMANDER, "rejected")
    assert rejected.status == "invalid_candidate"


def test_flagged_protocol_holds_are_unaffected_by_the_ambiguous_selection_path(store):
    # A flagged_protocol hold's own approve/reject handling is reached
    # exactly as before, whatever `decision` is passed — confirming the
    # widening in answer_approval_hold is additive, not a behavior change.
    hold_id = create_approval_hold(store, "evt-1", "flagged_protocol", _selection(), _risk())

    result = answer_approval_hold(store, hold_id, "commander-1", PermissionLevel.COMMANDER, "approved")

    assert result.status == "approved"
    assert result.hold["selected_protocol_name"] == "dispatch_response"


# -- Clarification holds (§6.2) ------------------------------------------


def _registry():
    return EventTypeRegistry(types=("fire", "medical", "human_activation"))


def _unresolved_extraction():
    return ExtractionResult(
        classification=None,
        classification_status="unresolved",
        area="north_sector",
        entities=(),
        description="something happened, unclear what",
        severity=None,
        occurred_at="2026-08-20T10:00:00",
        occurred_at_is_fallback=False,
        missing_fields=("classification",),
    )


def _resolved_extraction():
    return ExtractionResult(
        classification="fire",
        classification_status="resolved",
        area="north_sector",
        entities=(),
        description="d",
        severity="moderate",
        occurred_at="2026-08-20T10:00:00",
        occurred_at_is_fallback=False,
        missing_fields=(),
    )


def test_unresolved_classification_holds():
    assert determine_clarification_hold(_unresolved_extraction()) is True


def test_resolved_classification_does_not_hold():
    assert determine_clarification_hold(_resolved_extraction()) is False


def test_create_and_answer_clarification_hold_round_trip(store):
    hold_id = create_clarification_hold(store, "evt-1", "something happened, unclear what")

    [held] = store.list_held_events("clarification")
    assert held["hold_id"] == hold_id
    assert held["unresolved_field"] == "classification"
    assert held["raw_text"] == "something happened, unclear what"

    result = answer_clarification_hold(store, hold_id, "commander-1", PermissionLevel.COMMANDER, "fire", _registry())

    assert result.status == "resolved"
    assert store.list_held_events("clarification") == []


def test_free_text_outside_the_registry_is_rejected(store):
    hold_id = create_clarification_hold(store, "evt-1", "raw text")

    result = answer_clarification_hold(store, hold_id, "commander-1", PermissionLevel.COMMANDER, "not_a_real_type", _registry())

    assert result.status == "invalid_classification"
    # rejected, not silently resolved — the hold is still open
    assert len(store.list_held_events("clarification")) == 1


def test_viewer_cannot_resolve_a_clarification_hold(store):
    hold_id = create_clarification_hold(store, "evt-1", "raw text")

    result = answer_clarification_hold(store, hold_id, "viewer-1", PermissionLevel.VIEWER, "fire", _registry())

    assert result.status == "unauthorized"
    assert len(store.list_held_events("clarification")) == 1


def test_answering_an_unknown_clarification_hold_is_not_found(store):
    result = answer_clarification_hold(store, "never-existed", "commander-1", PermissionLevel.COMMANDER, "fire", _registry())

    assert result.status == "not_found"


def test_clarification_and_approval_holds_do_not_interfere(store):
    # Both kinds share one table — confirm resolving one never touches the other.
    clar_id = create_clarification_hold(store, "evt-1", "raw text")
    appr_id = create_approval_hold(store, "evt-2", "flagged_protocol", _selection(), _risk())

    answer_clarification_hold(store, clar_id, "commander-1", PermissionLevel.COMMANDER, "fire", _registry())

    assert store.list_held_events("clarification") == []
    assert len(store.list_held_events("approval")) == 1
    assert store.list_held_events("approval")[0]["hold_id"] == appr_id

import types

import pytest

from agents import adapter
from orchestrator.main_agent import IntentResult, OrchestrationParseError, _parse_intent_response, answer_conversationally, classify_intent
from protocols.model import CriticalityLevel, Protocol


class _ScriptedMainAgent:
    def __init__(self, response_text, status="success"):
        self._response_text = response_text
        self._status = status
        self.calls = []

    def process(self, text, allowed_tools):
        self.calls.append((text, allowed_tools))

        class _Result:
            status = self._status
            text = self._response_text

        return _Result()


def _protocols():
    return (
        Protocol(
            name="dispatch_response",
            description="applies when a response must be dispatched",
            participating_agents=(),
            approved_tools=(),
            expected_success_output="x",
            criticality=CriticalityLevel.HIGH,
            approval_flag=True,
        ),
    )


# -- Parser -------------------------------------------------------------


@pytest.mark.parametrize("intent", ["question", "report", "request", "conversational"])
def test_parse_each_intent(intent):
    result = _parse_intent_response(f"INTENT: {intent}\nREASON: because")

    assert result == IntentResult(intent=intent, reason="because")


def test_parse_is_case_insensitive():
    result = _parse_intent_response("INTENT: REQUEST\nREASON: r")

    assert result.intent == "request"


def test_parse_rejects_an_invalid_intent_word():
    with pytest.raises(OrchestrationParseError):
        _parse_intent_response("INTENT: complaint\nREASON: r")


def test_parse_rejects_missing_reason():
    with pytest.raises(OrchestrationParseError):
        _parse_intent_response("INTENT: question")


# -- classify_intent ------------------------------------------------------


def test_classify_intent_returns_the_parsed_result():
    agent = _ScriptedMainAgent("INTENT: request\nREASON: asks for a response to be dispatched")

    result = classify_intent(agent, _protocols(), "please send someone to gate 3")

    assert result.intent == "request"


def test_protocols_appear_in_the_prompt():
    agent = _ScriptedMainAgent("INTENT: question\nREASON: r")

    classify_intent(agent, _protocols(), "is gate 3 ok?")

    assert "dispatch_response" in agent.calls[0][0]
    assert "applies when a response must be dispatched" in agent.calls[0][0]


def test_classify_intent_passes_no_tools():
    agent = _ScriptedMainAgent("INTENT: question\nREASON: r")

    classify_intent(agent, _protocols(), "is gate 3 ok?")

    assert agent.calls[0][1] == []


def test_unclear_task_status_raises():
    agent = _ScriptedMainAgent("missing info", status="unclear_task")

    with pytest.raises(OrchestrationParseError):
        classify_intent(agent, _protocols(), "some message")


def test_a_greeting_classifies_as_conversational():
    agent = _ScriptedMainAgent("INTENT: conversational\nREASON: purely social, nothing to look up or act on")

    result = classify_intent(agent, _protocols(), "hey, how are you?")

    assert result.intent == "conversational"


def test_a_question_asking_for_real_capability_stays_a_question_not_conversational():
    # The line this session's fix must draw correctly: "do I have any
    # tasks?" asks the system to check something real — it's a QUESTION
    # even though no agent here can actually answer it — never
    # CONVERSATIONAL, whatever the eventual answer turns out to be.
    agent = _ScriptedMainAgent("INTENT: question\nREASON: asks the system to check something real")

    result = classify_intent(agent, _protocols(), "do I have any tasks?")

    assert result.intent == "question"


def test_conversational_prompt_explicitly_distinguishes_from_a_real_question():
    agent = _ScriptedMainAgent("INTENT: conversational\nREASON: r")

    classify_intent(agent, _protocols(), "hey, how are you?")

    prompt = agent.calls[0][0]
    assert "do i have any tasks?" in prompt.lower()  # the drawn-line example is actually in the prompt
    assert "CONVERSATIONAL" in prompt


# -- answer_conversationally ----------------------------------------------


def test_answer_conversationally_returns_the_agents_direct_reply():
    agent = _ScriptedMainAgent("Doing well, thanks for asking! How can I help?")

    reply = answer_conversationally(agent, "hey, how are you?")

    assert reply == "Doing well, thanks for asking! How can I help?"


def test_answer_conversationally_passes_no_tools():
    agent = _ScriptedMainAgent("hi there")

    answer_conversationally(agent, "hey")

    assert agent.calls[0][1] == []


def test_answer_conversationally_prompt_carries_an_honesty_constraint():
    agent = _ScriptedMainAgent("hi there")

    answer_conversationally(agent, "hey")

    prompt = agent.calls[0][0]
    assert "invent" in prompt.lower() or "fabricate" in prompt.lower()


def test_answer_conversationally_raises_on_an_unusable_response():
    agent = _ScriptedMainAgent("missing info", status="unclear_task")

    with pytest.raises(OrchestrationParseError):
        answer_conversationally(agent, "hey")


def test_end_to_end_through_the_mocked_adapter(monkeypatch):
    from orchestrator.main_agent import MainAgent

    class _FakeOutput:
        def __init__(self, raw):
            self.raw = raw

    class _FakeAgent:
        def __init__(self, **kwargs):
            pass

        def kickoff(self, text):
            return _FakeOutput("INTENT: report\nREASON: describes something that happened")

    fake_module = types.SimpleNamespace(Agent=_FakeAgent, tools=types.SimpleNamespace(BaseTool=object))
    monkeypatch.setattr(adapter, "_get_crewai", lambda: fake_module)

    main_agent = MainAgent(model="fake-model")
    result = classify_intent(main_agent, _protocols(), "smoke seen near gate 3")

    assert result.intent == "report"
