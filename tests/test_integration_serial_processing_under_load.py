"""9.19 — Test serial processing under load (work_plan.md §9.19).

Distinct from `tests/test_persistence_sqlite_backend.py`'s own
concurrency suite, which proves the serialized-writer design holds under
raw concurrent `persistence` calls in isolation. This file proves it
holds under a real simulator-driven burst through the *entire* flow —
extraction, risk assessment, protocol selection, the Insights Agent, and
a real running summary scheduler all at once — reusing that file's own
real-threads-plus-timeout technique for detecting a hang (`orchestrator.
queue.SerialEventQueue.wait_until_idle` already provides the equivalent
here, since everything routes through one real queue).
"""

import threading
import types

import pytest

from agents import adapter
from tests.api_fakes import RunningApiServer, build_context, happy_path_agent
from tools.simulator import main as simulator_main


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


class _OverlapDetectingAgent:
    """Wraps a real scripted main agent, recording whether any two calls
    to `.process()` were ever concurrently in flight — the direct proof
    that events are processed strictly one at a time, not merely that
    the final counts happen to add up.
    """

    def __init__(self, inner):
        self._inner = inner
        self._lock = threading.Lock()
        self.overlap_detected = False
        self.call_count = 0

    def process(self, text, allowed_tools):
        if not self._lock.acquire(blocking=False):
            self.overlap_detected = True
            self._lock.acquire()  # don't deadlock the rest of the test
        try:
            self.call_count += 1
            return self._inner.process(text, allowed_tools)
        finally:
            self._lock.release()


def test_a_burst_is_processed_one_at_a_time_with_no_lost_writes_and_no_lock_errors(tmp_path):
    inner_agent = happy_path_agent(risk_score="0.1", selected="status_check")
    agent = _OverlapDetectingAgent(inner_agent)

    ctx = build_context(tmp_path, main_agent=agent)
    ctx.scheduler.start()

    try:
        with RunningApiServer(ctx) as server:
            burst_size = 25
            exit_code = simulator_main([
                "--host", "127.0.0.1", "--port", str(server.port), "--identity", "sensor-1",
                "--count", str(burst_size), "--burst-size", str(burst_size), "--rate", "0",
                "--unclassifiable-rate", "0", "--repeat-rate", "0", "--seed", "7", "--quiet",
            ])
            assert exit_code == 0

            ctx.queue.wait_until_idle()

            all_events = ctx.deps.persistence.fetch_events_range("2000-01-01T00:00:00", "2100-01-01T00:00:00")
            assert len(all_events) == burst_size, "event count in the database must match the count emitted, none lost"
            # happy_path_agent's extraction is a fixed canned response, so
            # every event in the burst shares one classification/area —
            # after the first genuinely runs, the rest correctly close on
            # precedent instead (real, correct behavior, not an error).
            # Either terminal outcome is fine; "failed" is not.
            assert all(e["outcome"] in ("succeeded", "closed_on_precedent") for e in all_events), "no SQLite lock error or write failure along the way"
            assert any(e["outcome"] == "succeeded" for e in all_events)

        assert agent.overlap_detected is False, "two events were processed concurrently — serial processing was violated"
        assert agent.call_count >= burst_size  # at least one main-agent call per event (several per event in practice)
    finally:
        ctx.scheduler.stop()
