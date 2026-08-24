"""9.20 — Review cost and latency (work_plan.md §9.20).

This subtask is a review, not a correctness test — its own bullets ask
for measurement and analysis, not a pass/fail claim. This file is the
real instrumentation that produces the numbers `docs/cost_latency_review.md`
reports; it exists so those numbers are reproducible and re-checkable,
not hand-typed guesses. It fails only if the instrumentation itself
breaks (e.g. a stage's call count regresses to zero) — it is not a
tuning target in itself.
"""

import time
import types

import pytest

from agents import adapter
from tests.api_fakes import RunningApiServer, build_context, happy_path_agent
from tools.simulator import _post_event

_STAGE_MARKERS = {
    "extraction": "Extract this operational event",
    "risk_assessment": "RISK_SCORE",
    "protocol_selection": "Choose the protocol",
    "task_formulation": "participating in the",
    "judgment": "VERDICT:",
}


class _CountingAgent:
    def __init__(self, inner):
        self._inner = inner
        self.calls_by_stage: dict[str, int] = {name: 0 for name in _STAGE_MARKERS}
        self.uncategorized_calls = 0

    def process(self, text, allowed_tools):
        for stage, marker in _STAGE_MARKERS.items():
            if marker in text:
                self.calls_by_stage[stage] += 1
                break
        else:
            self.uncategorized_calls += 1
        return self._inner.process(text, allowed_tools)

    @property
    def total_calls(self) -> int:
        return sum(self.calls_by_stage.values()) + self.uncategorized_calls


class _CountingInsightsAgent:
    def __init__(self):
        self.call_count = 0

    def process(self, text, allowed_tools):
        from agents.results import AgentResult

        self.call_count += 1
        return AgentResult("success", "VERDICT: success\nREASONING: matches expected output")


@pytest.fixture(autouse=True)
def _mock_crewai(monkeypatch):
    class _FakeOutput:
        def __init__(self, raw):
            self.raw = raw

    class _FakeCrewAgent:
        def __init__(self, **kwargs):
            pass

        def kickoff(self, text):
            return _FakeOutput("status nominal")

    fake_module = types.SimpleNamespace(Agent=_FakeCrewAgent, tools=types.SimpleNamespace(BaseTool=object))
    monkeypatch.setattr(adapter, "_get_crewai", lambda: fake_module)


def test_model_calls_per_event_and_precedent_savings_are_measured(tmp_path):
    inner = happy_path_agent(risk_score="0.1", selected="status_check")
    main_agent = _CountingAgent(inner)
    insights_agent = _CountingInsightsAgent()

    ctx = build_context(tmp_path, main_agent=main_agent, insights_agent=insights_agent)

    with RunningApiServer(ctx) as server:
        # First event: nothing to match yet, runs the full chain.
        start = time.monotonic()
        first = _post_event(server.base_url, "sensor-1", "fire at gate 3")
        ctx.queue.wait_until_idle()
        first_latency = time.monotonic() - start

        first_event = ctx.deps.persistence.fetch_event(first["event_id"])
        assert first_event["outcome"] == "succeeded"
        full_run_calls = dict(main_agent.calls_by_stage)
        full_run_insights_calls = insights_agent.call_count

        # Every stage genuinely ran at least once.
        for stage, count in full_run_calls.items():
            assert count >= 1, f"{stage} never fired — instrumentation or the flow itself is broken"
        assert full_run_insights_calls == 1

        # Second, identical-shape event: closes on precedent instead.
        main_agent.calls_by_stage = {name: 0 for name in _STAGE_MARKERS}
        insights_agent.call_count = 0

        second = _post_event(server.base_url, "sensor-1", "fire at gate 4")
        ctx.queue.wait_until_idle()

        second_event = ctx.deps.persistence.fetch_event(second["event_id"])
        assert second_event["outcome"] == "closed_on_precedent"
        precedent_run_calls = dict(main_agent.calls_by_stage)
        precedent_run_insights_calls = insights_agent.call_count

        # Closure skips formulation, execution, and judgment entirely —
        # the whole point of the path, made concrete.
        assert precedent_run_calls["task_formulation"] == 0
        assert precedent_run_calls["judgment"] == 0
        assert precedent_run_insights_calls == 0
        assert sum(precedent_run_calls.values()) < sum(full_run_calls.values())

        # Findings from these measurements are written up in
        # docs/cost_latency_review.md — this test is the reproducible
        # instrumentation behind that document's numbers, re-run whenever
        # the decision chain's shape changes rather than trusted as fixed.
        print(f"\nfull run calls: {full_run_calls}, insights: {full_run_insights_calls}, latency: {first_latency * 1000:.1f}ms")
        print(f"precedent-closed calls: {precedent_run_calls}, insights: {precedent_run_insights_calls}")
