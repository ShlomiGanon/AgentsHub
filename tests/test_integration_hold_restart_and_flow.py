"""9.7 / 9.9 — restart-mid-hold and continued-processing-behind-a-hold
(work_plan.md §9.7, §9.9).

Most of §9.7/§9.8/§9.9/§9.10/§9.11/§9.12/§9.13's own bullets are already
covered, bullet for bullet, by extensive pre-existing test suites:
`tests/test_orchestrator_holds.py`, `tests/test_orchestrator_precedent.py`,
`tests/test_orchestrator_formulation.py`, `tests/test_orchestrator_judgment.py`
(all Mission 6, real orchestrator functions, no HTTP), and
`tests/test_api_holds.py`/`tests/test_api_messages.py` (Mission 7, real
HTTP against a fake `ApiContext`). `tests/test_orchestrator_flows.py::
test_a_held_event_resumes_correctly_after_a_simulated_restart` already
proves restart-survival for an *approval* hold at the orchestrator level.

What none of those combine: restart-survival proven through the real
`api/*` HTTP routes specifically (a fresh process boundary reaching the
hold through `POST /Clarify`/`POST /Approve`, not by calling
`resume_after_approval` directly), for *both* hold kinds, and the claim
that events behind a held one keep flowing rather than blocking on it.
This file is exactly that remaining, non-redundant scope.
"""

import types

import pytest

from agents import adapter
from orchestrator.holds import create_approval_hold, create_clarification_hold
from orchestrator.main_agent import RiskAssessment
from orchestrator.main_agent import ProtocolSelectionResult
from tests.api_fakes import COMMANDER_IDENTITY, RunningApiServer, build_context, happy_path_agent


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

    fake_module = types.SimpleNamespace(Agent=_FakeCrewAgent, LLM=lambda **kwargs: kwargs["model"], tools=types.SimpleNamespace(BaseTool=object))
    monkeypatch.setattr(adapter, "_get_crewai", lambda: fake_module)


def test_a_clarification_hold_survives_a_real_restart_and_is_resolvable_through_the_real_api(tmp_path):
    ctx1 = build_context(tmp_path)
    event_id = ctx1.deps.persistence.append_event({
        "received_at": "2026-08-24T10:00:00", "source": "telegram", "sender_identity": "viewer-1", "raw_text": "unclear report",
    })
    create_clarification_hold(ctx1.deps.persistence, event_id, "unclear report")
    ctx1.queue.stop()
    ctx1.deps.persistence.close()  # simulates the old process exiting

    # "Restart": a fresh ApiContext, a fresh SQLitePersistence, against the
    # exact same file, no in-memory state carried over.
    ctx2 = build_context(tmp_path, main_agent=happy_path_agent(risk_score="0.1", selected="status_check"))
    with RunningApiServer(ctx2) as server2:
        client_resp = _post_json(server2.base_url, f"/Clarify/{event_id}", COMMANDER_IDENTITY, {"classification": "fire"})
        assert client_resp["status_code"] == 202
        ctx2.queue.wait_until_idle()

        hold = ctx2.deps.persistence.fetch_held_event("clarification", event_id)
        assert hold["resolved"] is True
        assert hold["resolved_by"] == COMMANDER_IDENTITY


def test_an_approval_hold_survives_a_real_restart_and_is_resolvable_through_the_real_api(tmp_path):
    ctx1 = build_context(tmp_path)
    event_id = ctx1.deps.persistence.append_event({
        "received_at": "2026-08-24T10:00:00", "source": "telegram", "sender_identity": "viewer-1",
        "occurred_at": "2026-08-24T10:00:00", "raw_text": "needs dispatch",
    })
    selection = ProtocolSelectionResult(status="selected", protocol_name="dispatch_response", reason="matched")
    risk = RiskAssessment(level="high", score=0.9, reason="side-effecting")
    create_approval_hold(ctx1.deps.persistence, event_id, "flagged_protocol", selection, risk)
    ctx1.queue.stop()
    ctx1.deps.persistence.close()

    ctx2 = build_context(tmp_path, main_agent=happy_path_agent(risk_score="0.1", selected="status_check"))
    with RunningApiServer(ctx2) as server2:
        result = _post_json(server2.base_url, f"/Approve/{event_id}", COMMANDER_IDENTITY, {"decision": "approved"})
        assert result["status_code"] == 202
        ctx2.queue.wait_until_idle()

        hold = ctx2.deps.persistence.fetch_held_event("approval", event_id)
        assert hold["resolved"] is True
        assert hold["resolved_by"] == COMMANDER_IDENTITY


def test_events_behind_a_held_event_continue_processing_while_it_waits(tmp_path):
    # An event that will hold (unclassifiable text) submitted first, then
    # an event that won't (a clear match) submitted right after — the
    # second must complete even though the first is still pending.
    agent = happy_path_agent(risk_score="0.1", selected="status_check")
    agent._dispatch["Extract this operational event"] = (
        '{"classification": null, "area": null, "entities": [], "description": null, "severity": null, "occurred_at": null}'
    )

    ctx = build_context(tmp_path, main_agent=agent)
    with RunningApiServer(ctx) as server:
        from tools.simulator import _post_event

        held_result = _post_event(server.base_url, "sensor-1", "something unclear near the fence")
        assert held_result["status_code"] == 202
        ctx.queue.wait_until_idle()

        held_event = ctx.deps.persistence.fetch_event(held_result["event_id"])
        assert held_event["clarification_held"] is True
        assert held_event["outcome"] is None  # still pending, not abandoned

        # Now switch the scripted agent to a normal, non-holding response
        # for the second event and confirm it completes independently.
        agent._dispatch["Extract this operational event"] = (
            '{"classification": "fire", "area": "north_sector", "entities": [], '
            '"description": "smoke", "severity": "minor", "occurred_at": "2026-08-20T09:00:00"}'
        )

        clear_result = _post_event(server.base_url, "sensor-1", "smoke at gate 3, north_sector")
        assert clear_result["status_code"] == 202
        ctx.queue.wait_until_idle()

        clear_event = ctx.deps.persistence.fetch_event(clear_result["event_id"])
        assert clear_event["outcome"] == "succeeded"
        # The first event is still exactly where it was — not blocked, not
        # silently abandoned either.
        assert ctx.deps.persistence.fetch_event(held_result["event_id"])["outcome"] is None


def _post_json(base_url: str, path: str, identity: str, body: dict) -> dict:
    import json
    import urllib.error
    import urllib.request

    data = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(f"{base_url}{path}", data=data, method="POST", headers={"X-Identity": identity, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request) as response:
            return {"status_code": response.status, **json.loads(response.read())}
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        payload = json.loads(raw) if raw else {}
        return {"status_code": exc.code, **payload}
