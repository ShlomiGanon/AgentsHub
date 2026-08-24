import types

import pytest

from agents import adapter
from api.app import build_app
from api.jobs import job_status
from tests.api_fakes import COMMANDER_IDENTITY, SENSOR_IDENTITY, VIEWER_IDENTITY, auth_headers, build_context, happy_path_agent


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

    fake_module = types.SimpleNamespace(Agent=_FakeCrewAgent, tools=types.SimpleNamespace(BaseTool=object))
    monkeypatch.setattr(adapter, "_get_crewai", lambda: fake_module)


@pytest.fixture
def ctx(tmp_path):
    context = build_context(tmp_path, main_agent=happy_path_agent(risk_score="0.1", selected="status_check"))
    yield context
    context.queue.stop()
    context.deps.persistence.close()


def test_post_event_returns_202_with_a_job_id_immediately(ctx):
    client = build_app(ctx).test_client()

    resp = client.post("/Event", headers=auth_headers(SENSOR_IDENTITY), json={"text": "smoke at gate 3", "sender_identity": SENSOR_IDENTITY})

    assert resp.status_code == 202
    body = resp.get_json()
    assert body["status"] == "queued"
    assert body["event_id"]


def test_post_event_records_source_as_sensor_with_occurred_at_equal_to_received_at(ctx):
    client = build_app(ctx).test_client()

    resp = client.post("/Event", headers=auth_headers(SENSOR_IDENTITY), json={"text": "smoke at gate 3", "sender_identity": SENSOR_IDENTITY})
    event_id = resp.get_json()["event_id"]
    ctx.queue.wait_until_idle()  # occurred_at is set during extraction, not at submission

    event = ctx.deps.persistence.fetch_event(event_id)
    assert event["source"] == "sensor"
    assert event["occurred_at"] == event["received_at"]


def test_post_event_runs_to_completion_through_the_queue(ctx):
    client = build_app(ctx).test_client()

    resp = client.post("/Event", headers=auth_headers(SENSOR_IDENTITY), json={"text": "smoke at gate 3", "sender_identity": SENSOR_IDENTITY})
    event_id = resp.get_json()["event_id"]
    ctx.queue.wait_until_idle()

    status = job_status(ctx, event_id)
    assert status["status"] == "succeeded"


def test_post_event_rejects_a_missing_text(ctx):
    client = build_app(ctx).test_client()

    resp = client.post("/Event", headers=auth_headers(SENSOR_IDENTITY), json={"sender_identity": SENSOR_IDENTITY})

    assert resp.status_code == 400
    assert resp.get_json()["field"] == "text"


def test_post_event_rejects_a_missing_sender_identity(ctx):
    client = build_app(ctx).test_client()

    resp = client.post("/Event", headers=auth_headers(SENSOR_IDENTITY), json={"text": "smoke at gate 3"})

    assert resp.status_code == 400
    assert resp.get_json()["field"] == "sender_identity"


def test_post_event_requires_authentication(ctx):
    client = build_app(ctx).test_client()

    resp = client.post("/Event", json={"text": "smoke at gate 3", "sender_identity": SENSOR_IDENTITY})

    assert resp.status_code == 401


def test_post_event_authenticates_the_sensor_as_a_real_registered_identity_never_a_bypass(ctx):
    client = build_app(ctx).test_client()

    resp = client.post("/Event", headers=auth_headers("some-unregistered-sensor"), json={"text": "smoke at gate 3", "sender_identity": "some-unregistered-sensor"})

    assert resp.status_code == 401


def test_post_event_permits_a_viewer_level_sensor_identity(ctx):
    # send_message is VIEWER-level — the sensor identity is registered as
    # viewer in tests/api_fakes.py, matching how a real deployment would
    # provision it (no elevated privilege needed just to submit a report).
    client = build_app(ctx).test_client()

    resp = client.post("/Event", headers=auth_headers(SENSOR_IDENTITY), json={"text": "smoke at gate 3", "sender_identity": SENSOR_IDENTITY})

    assert resp.status_code == 202


def test_post_event_works_for_any_registered_identity_not_only_the_sensor_one(ctx):
    client = build_app(ctx).test_client()

    resp = client.post("/Event", headers=auth_headers(VIEWER_IDENTITY), json={"text": "smoke at gate 3", "sender_identity": VIEWER_IDENTITY})

    assert resp.status_code == 202


def test_post_event_works_for_a_commander_identity_too(ctx):
    client = build_app(ctx).test_client()

    resp = client.post("/Event", headers=auth_headers(COMMANDER_IDENTITY), json={"text": "smoke at gate 3", "sender_identity": COMMANDER_IDENTITY})

    assert resp.status_code == 202
