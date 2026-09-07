"""§7.5's own convergence proof: /Event and /Msg-as-report must converge
on the same new-event flow the moment the text is known to be an event,
with exactly two differences — the recorded source, and whether the
occurrence timestamp is extracted from the text or set to the received
timestamp. Verified with a test, not by inspection, per §7.5's own rule.
"""

import types

import pytest

from agents import adapter
from api.app import build_app
from api.operations import job_status
from tests.api_fakes import VIEWER_IDENTITY, auth_headers, build_context, happy_path_agent


@pytest.fixture(autouse=True)
def _mock_crewai(monkeypatch):
    class _FakeOutput:
        def __init__(self, raw):
            self.raw = raw

    class _FakeCrewAgent:
        def __init__(self, **kwargs):
            pass

        def kickoff(self, text):
            return _FakeOutput("status nominal, no anomalies")

    fake_module = types.SimpleNamespace(Agent=_FakeCrewAgent, LLM=lambda **kwargs: kwargs["model"], tools=types.SimpleNamespace(BaseTool=object))
    monkeypatch.setattr(adapter, "_get_crewai", lambda: fake_module)


@pytest.fixture
def teardown_ctx():
    contexts = []
    yield contexts
    for ctx in contexts:
        ctx.queue.stop()
        ctx.deps.persistence.close()


def _agent():
    return happy_path_agent(risk_score="0.1", selected="status_check", intent="report")


def test_event_and_msg_report_converge_on_identical_downstream_fields(tmp_path, teardown_ctx):
    event_ctx = build_context(tmp_path, main_agent=_agent())
    teardown_ctx.append(event_ctx)
    event_client = build_app(event_ctx).test_client()

    msg_ctx = build_context(tmp_path, main_agent=_agent())
    teardown_ctx.append(msg_ctx)
    msg_client = build_app(msg_ctx).test_client()

    same_text = "smoke at gate 3"

    event_resp = event_client.post("/Event", headers=auth_headers(VIEWER_IDENTITY), json={"text": same_text, "sender_identity": VIEWER_IDENTITY})
    event_id_via_event = event_resp.get_json()["event_id"]
    event_ctx.queue.wait_until_idle()

    msg_resp = msg_client.post("/Msg", headers=auth_headers(VIEWER_IDENTITY), json={"text": same_text, "sender_identity": VIEWER_IDENTITY})
    assert msg_resp.get_json()["taken_as"] == "report"
    event_id_via_msg = msg_resp.get_json()["event_id"]
    msg_ctx.queue.wait_until_idle()

    via_event = event_ctx.deps.persistence.fetch_event(event_id_via_event)
    via_msg = msg_ctx.deps.persistence.fetch_event(event_id_via_msg)

    # Converge: every extracted/decided field the flow produces is identical.
    for field in ("classification", "area", "description", "severity", "risk_level", "selected_protocol", "outcome"):
        assert via_event[field] == via_msg[field], f"{field} diverged: {via_event[field]!r} != {via_msg[field]!r}"

    assert job_status(event_ctx, event_id_via_event)["status"] == "succeeded"
    assert job_status(msg_ctx, event_id_via_msg)["status"] == "succeeded"

    # The only two differences §7.5 permits.
    assert via_event["source"] == "sensor"
    assert via_msg["source"] == "telegram"
    assert via_event["occurred_at"] == via_event["received_at"]  # sensor: set, not extracted
    assert via_msg["occurred_at"] == "2026-08-20T09:00:00"  # telegram: extracted from the model's response


def test_event_and_msg_report_call_the_same_orchestrator_functions(tmp_path, teardown_ctx, monkeypatch):
    # Not just same-shaped output — the same code path. Both endpoints
    # must call orchestrator.flows.begin_report/run_report_extraction;
    # neither may implement a second, parallel sequence.
    calls = []

    import api.ingestion as events_module
    import api.ingestion as messages_module

    original_begin_report = events_module.begin_report

    def _tracking_begin_report(*args, **kwargs):
        calls.append(("begin_report", args[2]))  # args[2] is `source`
        return original_begin_report(*args, **kwargs)

    monkeypatch.setattr(events_module, "begin_report", _tracking_begin_report)
    monkeypatch.setattr(messages_module, "begin_report", _tracking_begin_report)

    ctx = build_context(tmp_path, main_agent=_agent())
    teardown_ctx.append(ctx)
    client = build_app(ctx).test_client()

    client.post("/Event", headers=auth_headers(VIEWER_IDENTITY), json={"text": "smoke at gate 3", "sender_identity": VIEWER_IDENTITY})
    ctx.queue.wait_until_idle()
    client.post("/Msg", headers=auth_headers(VIEWER_IDENTITY), json={"text": "smoke at gate 4", "sender_identity": VIEWER_IDENTITY})
    ctx.queue.wait_until_idle()

    assert calls == [("begin_report", "sensor"), ("begin_report", "telegram")]
