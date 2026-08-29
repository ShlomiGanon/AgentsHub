import types

import pytest

from agents import adapter
from orchestrator.main_agent import OrchestrationParseError, SuccessVerdict, _parse_judgment_response, judge_success
from protocols.model import CriticalityLevel, Protocol, Step
from protocols.executor import StepOutcome


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


def _protocol():
    return Protocol(
        name="p",
        description="d",
        participating_agents=("a1",),
        approved_tools=(),
        expected_success_output="the location is confirmed clear",
        criticality=CriticalityLevel.LOW,
        approval_flag=False,
    )


def _outcomes():
    step = Step(agent_name="a1", task_text="check gate 3", allowed_tools=())
    return (StepOutcome(step=step, result_text="gate 3 is clear", attempt_count=1, succeeded=True),)


# -- Parser -------------------------------------------------------------


@pytest.mark.parametrize("verdict", ["success", "failure", "uncertain"])
def test_parse_each_verdict(verdict):
    result = _parse_judgment_response(f"VERDICT: {verdict}\nREASONING: because")

    assert result == SuccessVerdict(verdict=verdict, reasoning="because")


def test_parse_is_case_insensitive_on_verdict():
    result = _parse_judgment_response("VERDICT: SUCCESS\nREASONING: r")

    assert result.verdict == "success"


def test_parse_rejects_an_invalid_verdict_word():
    with pytest.raises(OrchestrationParseError):
        _parse_judgment_response("VERDICT: maybe\nREASONING: r")


def test_parse_rejects_missing_reasoning():
    with pytest.raises(OrchestrationParseError):
        _parse_judgment_response("VERDICT: success")


# -- judge_success ------------------------------------------------------


def test_judge_success_returns_the_parsed_verdict():
    agent = _ScriptedMainAgent("VERDICT: success\nREASONING: matches expected output")

    verdict = judge_success(agent, _protocol(), _outcomes())

    assert verdict.verdict == "success"


def test_judge_success_works_with_default_empty_insight_text():
    agent = _ScriptedMainAgent("VERDICT: uncertain\nREASONING: r")

    verdict = judge_success(agent, _protocol(), _outcomes())  # insight_text not passed

    assert verdict.verdict == "uncertain"
    assert "Insight from comparing" not in agent.calls[0][0]


def test_insight_text_appears_in_the_prompt_when_given():
    agent = _ScriptedMainAgent("VERDICT: success\nREASONING: r")

    judge_success(agent, _protocol(), _outcomes(), insight_text="matches a resolved precedent")

    assert "matches a resolved precedent" in agent.calls[0][0]


def test_judge_success_passes_no_tools():
    agent = _ScriptedMainAgent("VERDICT: success\nREASONING: r")

    judge_success(agent, _protocol(), _outcomes())

    assert agent.calls[0][1] == []


def test_unclear_task_status_raises():
    agent = _ScriptedMainAgent("missing info", status="unclear_task")

    with pytest.raises(OrchestrationParseError):
        judge_success(agent, _protocol(), _outcomes())


def test_end_to_end_through_the_mocked_adapter(monkeypatch):
    from orchestrator.main_agent import MainAgent

    class _FakeOutput:
        def __init__(self, raw):
            self.raw = raw

    class _FakeAgent:
        def __init__(self, **kwargs):
            pass

        def kickoff(self, text):
            return _FakeOutput("VERDICT: failure\nREASONING: gate was not actually checked")

    fake_module = types.SimpleNamespace(Agent=_FakeAgent, LLM=lambda **kwargs: kwargs["model"], tools=types.SimpleNamespace(BaseTool=object))
    monkeypatch.setattr(adapter, "_get_crewai", lambda: fake_module)

    main_agent = MainAgent(model="fake-model")
    verdict = judge_success(main_agent, _protocol(), _outcomes())

    assert verdict.verdict == "failure"

from history.query import PrecedentMatch
from orchestrator.precedent import determine_closure, look_up_precedent


def _match(event_id, resolved):
    return PrecedentMatch(
        event_id=event_id,
        classification="fire",
        area="north_sector",
        occurred_at="2026-08-01T00:00:00",
        protocol_name="status_check",
        steps_summary=[],
        outcome="succeeded" if resolved else None,
        resolved=resolved,
    )


class _ScriptedHistoryQueryService:
    def __init__(self, matches):
        self._matches = matches
        self.calls = []

    def search_precedents(self, target_event_id, classification, area, target_event_occurred_at):
        self.calls.append((target_event_id, classification, area, target_event_occurred_at))
        return self._matches


# -- look_up_precedent ----------------------------------------------------


def test_look_up_precedent_passes_arguments_through():
    service = _ScriptedHistoryQueryService([_match("evt-old", True)])

    result = look_up_precedent(service, "evt-new", "fire", "north_sector", "2026-08-20T10:00:00")

    assert service.calls == [("evt-new", "fire", "north_sector", "2026-08-20T10:00:00")]
    assert result == (_match("evt-old", True),)


def test_look_up_precedent_returns_empty_tuple_for_no_matches():
    service = _ScriptedHistoryQueryService([])

    assert look_up_precedent(service, "evt-new", "fire", "north", "t") == ()


# -- determine_closure ------------------------------------------------------


def test_high_risk_never_closes_even_with_a_resolved_match():
    precedents = (_match("evt-old", resolved=True),)

    assert determine_closure("high", "fire", precedents) is None


def test_low_risk_with_resolved_match_closes():
    precedents = (_match("evt-old", resolved=True),)

    assert determine_closure("low", "fire", precedents) == "evt-old"


def test_low_risk_with_only_unresolved_match_does_not_close():
    precedents = (_match("evt-old", resolved=False),)

    assert determine_closure("low", "fire", precedents) is None


def test_low_risk_with_no_matches_does_not_close():
    assert determine_closure("low", "fire", ()) is None


def test_human_activation_never_closes_even_with_a_resolved_match():
    precedents = (_match("evt-old", resolved=True),)

    assert determine_closure("low", "human_activation", precedents) is None


def test_most_recent_resolved_match_is_used_among_several():
    # search_precedents already returns most-recent-first; the first
    # resolved one encountered is used.
    precedents = (_match("evt-unresolved-recent", resolved=False), _match("evt-resolved-older", resolved=True))

    assert determine_closure("low", "fire", precedents) == "evt-resolved-older"
