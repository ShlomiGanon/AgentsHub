"""9.14 — Test retry and idempotency (work_plan.md §9.14).

The first three bullets (idempotency blocking retry after a side-effecting
tool acted, read-only retry to the limit, the limit read live) are already
covered directly against `protocols.retry.execute_step_with_retry` in
`tests/test_protocol_retry.py`. `tests/test_api_jobs.py` already covers
"keeps the successful steps' results" at the rendering level. What's
missing, and what this file adds: the real retry-exhaustion path, through
the real (mocked-at-the-crewai-boundary) executor, actually notifying the
originator and letting the next event proceed — not simulated by writing
outcomes to persistence directly.
"""

import types

import pytest

from agents import adapter
from tests.api_fakes import SENSOR_IDENTITY, RunningApiServer, build_context, happy_path_agent
from tools.simulator import _post_event


@pytest.fixture(autouse=True)
def _mock_crewai_always_fails(monkeypatch):
    class _FakeCrewAgent:
        def __init__(self, **kwargs):
            pass

        def kickoff(self, text):
            raise RuntimeError("scripted model failure")

    fake_module = types.SimpleNamespace(Agent=_FakeCrewAgent, tools=types.SimpleNamespace(BaseTool=object))
    monkeypatch.setattr(adapter, "_get_crewai", lambda: fake_module)


def test_retry_exhaustion_notifies_the_originator_and_the_next_event_still_proceeds(tmp_path):
    agent = happy_path_agent(risk_score="0.1", selected="status_check")
    ctx = build_context(tmp_path, main_agent=agent)
    ctx.deps.persistence.write_user("bot-service", "commander")

    with RunningApiServer(ctx) as server:
        failing = _post_event(server.base_url, SENSOR_IDENTITY, "fire at gate 3")
        assert failing["status_code"] == 202
        ctx.queue.wait_until_idle()

        failed_event = ctx.deps.persistence.fetch_event(failing["event_id"])
        assert failed_event["outcome"] == "failed"
        assert failed_event["outcome_failure_reason"]

        notifications_resp = _get_notifications(server.base_url)
        by_event = {n["payload"]["job_id"]: n for n in notifications_resp["notifications"]}
        failure_notification = by_event[failing["event_id"]]
        assert failure_notification["kind"] == "job_failed"
        assert failure_notification["target_chat_ids"] == [SENSOR_IDENTITY]
        assert failure_notification["payload"]["failed_step_agent_name"] == "reference_agent"

        # The queue is not stuck: submit again, this time expecting it to
        # (still fail here, since the crewai mock still always raises, but
        # crucially) be *picked up and processed*, not left queued forever
        # behind the first one.
        second = _post_event(server.base_url, SENSOR_IDENTITY, "fire at gate 4")
        assert second["status_code"] == 202
        ctx.queue.wait_until_idle()

        second_event = ctx.deps.persistence.fetch_event(second["event_id"])
        assert second_event["outcome"] is not None  # reached a real terminal outcome, not left "queued" forever


def _get_notifications(base_url: str) -> dict:
    import json
    import urllib.request

    request = urllib.request.Request(f"{base_url}/Notifications", headers={"X-Identity": "bot-service"})
    with urllib.request.urlopen(request) as response:
        return json.loads(response.read())
