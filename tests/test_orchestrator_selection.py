import types

import pytest

from agents import adapter
from orchestrator.errors import OrchestrationParseError
from orchestrator.selection import ProtocolSelectionResult, _parse_selection_response, select_protocol
from protocols.model import CriticalityLevel, Protocol


def _protocol(name, criticality=CriticalityLevel.LOW):
    return Protocol(
        name=name,
        description=f"description for {name}",
        participating_agents=(),
        approved_tools=(),
        expected_success_output="x",
        criticality=criticality,
        approval_flag=False,
    )


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


# -- Parser -------------------------------------------------------------


def test_parse_selected_response():
    result = _parse_selection_response("SELECTED: status_check\nREASON: clear match")

    assert result == ProtocolSelectionResult(status="selected", protocol_name="status_check", reason="clear match")


def test_parse_ambiguous_response_splits_candidate_names():
    result = _parse_selection_response("AMBIGUOUS: a, b, c\nREASON: all fit equally")

    assert result.status == "ambiguous"
    assert result.candidate_names == ("a", "b", "c")


def test_parse_rejects_unrecognized_response():
    with pytest.raises(OrchestrationParseError):
        _parse_selection_response("I'm not sure what to pick")


# -- select_protocol ----------------------------------------------------


def test_clear_selection_passes_through():
    agent = _ScriptedMainAgent("SELECTED: status_check\nREASON: fits")
    protocols = (_protocol("status_check"),)

    result = select_protocol(agent, "raw", "fire", "north", "d", protocols, risk_level="low")

    assert result.status == "selected"
    assert result.protocol_name == "status_check"


def test_low_risk_ambiguous_passes_through_unresolved():
    agent = _ScriptedMainAgent("AMBIGUOUS: a, b\nREASON: tied")
    protocols = (_protocol("a"), _protocol("b"))

    result = select_protocol(agent, "raw", "fire", "north", "d", protocols, risk_level="low")

    assert result.status == "ambiguous"
    assert result.candidate_names == ("a", "b")


def test_high_risk_ambiguous_resolves_to_most_critical_candidate():
    agent = _ScriptedMainAgent("AMBIGUOUS: a, b\nREASON: tied")
    protocols = (_protocol("a", CriticalityLevel.MEDIUM), _protocol("b", CriticalityLevel.HIGH))

    result = select_protocol(agent, "raw", "fire", "north", "d", protocols, risk_level="high")

    assert result.status == "selected"
    assert result.protocol_name == "b"  # HIGH beats MEDIUM


def test_select_protocol_passes_no_tools():
    agent = _ScriptedMainAgent("SELECTED: a\nREASON: r")

    select_protocol(agent, "raw", "fire", "north", "d", (_protocol("a"),), risk_level="low")

    assert agent.calls[0][1] == []


def test_unclear_task_status_raises():
    agent = _ScriptedMainAgent("missing info", status="unclear_task")

    with pytest.raises(OrchestrationParseError):
        select_protocol(agent, "raw", "fire", "north", "d", (_protocol("a"),), risk_level="low")


def test_end_to_end_through_the_mocked_adapter(monkeypatch):
    from agents.reference import ReferenceAgent

    class _FakeOutput:
        def __init__(self, raw):
            self.raw = raw

    class _FakeAgent:
        def __init__(self, **kwargs):
            pass

        def kickoff(self, text):
            return _FakeOutput("SELECTED: status_check\nREASON: matches the report exactly")

    fake_module = types.SimpleNamespace(Agent=_FakeAgent, tools=types.SimpleNamespace(BaseTool=object))
    monkeypatch.setattr(adapter, "_get_crewai", lambda: fake_module)

    from orchestrator.main_agent import MainAgent

    main_agent = MainAgent(model="fake-model")
    result = select_protocol(main_agent, "raw text", "fire", "north", "d", (_protocol("status_check"),), risk_level="low")

    assert result.status == "selected"
    assert result.protocol_name == "status_check"
