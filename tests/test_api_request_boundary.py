"""API request-boundary error behavior."""

from flask import Flask

from api.errors import ApiError, InvalidInputError, RunFailureError, register_error_handlers


def _app():
    """App."""
    app = Flask(__name__)
    register_error_handlers(app)
    return app


def test_api_error_produces_the_one_fixed_shape():
    """Api error produces the one fixed shape."""
    app = _app()

    @app.route("/boom")
    def boom():
        raise InvalidInputError("'x' is required", field="x")

    client = app.test_client()
    resp = client.get("/boom")

    assert resp.status_code == 400
    assert resp.get_json() == {
        "error_class": "invalid_input",
        "error_code": "invalid_input",
        "message": "'x' is required",
        "field": "x",
    }


def test_api_error_without_a_field_omits_the_key():
    """Api error without a field omits the key."""
    app = _app()

    @app.route("/boom")
    def boom():
        raise InvalidInputError("bad request")

    resp = app.test_client().get("/boom")

    assert "field" not in resp.get_json()


def test_run_failure_error_has_its_own_class_and_status():
    """Run failure error has its own class and status."""
    app = _app()

    @app.route("/boom")
    def boom():
        raise RunFailureError("the main agent could not answer")

    resp = app.test_client().get("/boom")

    assert resp.status_code == 422
    assert resp.get_json()["error_class"] == "run_failure"


def test_unhandled_exception_becomes_a_generic_internal_error_never_leaking_details():
    """Unhandled exception becomes a generic internal error never leaking details."""
    app = _app()
    app.config["PROPAGATE_EXCEPTIONS"] = False
    app.testing = False  # a testing Flask app re-raises by default; force it through the handler

    @app.route("/boom")
    def boom():
        raise ValueError("some secret internal detail")

    resp = app.test_client().get("/boom")

    assert resp.status_code == 500
    body = resp.get_json()
    assert body["error_class"] == "internal_error"
    assert "secret" not in body["message"]
    assert body["message"] == "an internal error occurred"


def test_an_unmapped_route_returns_the_same_shape_not_werkzeugs_html_page():
    """An unmapped route returns the same shape not werkzeugs html page."""
    app = _app()

    resp = app.test_client().get("/does-not-exist")

    assert resp.status_code == 404
    assert resp.content_type.startswith("application/json")
    assert resp.get_json()["error_class"] == "invalid_input"


def test_a_wrong_method_returns_the_same_shape():
    """A wrong method returns the same shape."""
    app = _app()

    @app.route("/only-get", methods=["GET"])
    def only_get():
        return "ok"

    resp = app.test_client().post("/only-get")

    assert resp.status_code == 405
    assert resp.get_json()["error_class"] == "invalid_input"


def test_api_error_subclasses_carry_their_own_status_code():
    """Api error subclasses carry their own status code."""
    assert InvalidInputError("x").status_code == 400
    assert RunFailureError("x").status_code == 422


def test_api_error_is_the_common_base():
    """Api error is the common base."""
    assert issubclass(InvalidInputError, ApiError)
    assert issubclass(RunFailureError, ApiError)

import dataclasses
import types

import pytest

from agents import adapter
from api.app import build_app
from api.operations import job_status
from protocols import ProtocolSet
from tests.api_fakes import COMMANDER_IDENTITY, SENSOR_IDENTITY, VIEWER_IDENTITY, auth_headers, build_context, happy_path_agent
from tests.crewai_fakes import install_crewai_stub


@pytest.fixture(autouse=True)
def _mock_crewai(monkeypatch):
    """Mock crewai."""
    install_crewai_stub(monkeypatch)


@pytest.fixture
def ctx(tmp_path):
    """Ctx."""
    context = build_context(tmp_path, main_agent=happy_path_agent(risk_score="0.1", selected="status_check"))
    yield context
    context.queue.stop()
    context.deps.persistence.close()


def test_post_event_returns_202_with_a_job_id_immediately(ctx):
    """Post event returns 202 with a job id immediately."""
    client = build_app(ctx).test_client()

    resp = client.post("/Event", headers=auth_headers(SENSOR_IDENTITY), json={"text": "smoke at gate 3", "sender_identity": SENSOR_IDENTITY})

    assert resp.status_code == 202
    event = ctx.deps.persistence.fetch_event(resp.get_json()["event_id"])
    assert event["sender_permission_level"] == "viewer"
    body = resp.get_json()
    assert body["status"] == "queued"
    assert body["event_id"]


def test_post_event_records_source_as_sensor_with_occurred_at_equal_to_received_at(ctx):
    """Post event records source as sensor with occurred at equal to received at."""
    client = build_app(ctx).test_client()

    resp = client.post("/Event", headers=auth_headers(SENSOR_IDENTITY), json={"text": "smoke at gate 3", "sender_identity": SENSOR_IDENTITY})
    event_id = resp.get_json()["event_id"]
    ctx.queue.wait_until_idle()  # occurred_at is set during extraction, not at submission

    event = ctx.deps.persistence.fetch_event(event_id)
    assert event["source"] == "sensor"
    assert event["occurred_at"] == event["received_at"]


def test_post_event_uses_an_optional_timestamp_as_received_at(ctx):
    """Post event uses an optional timestamp as received at."""
    client = build_app(ctx).test_client()

    resp = client.post(
        "/Event",
        headers=auth_headers(SENSOR_IDENTITY),
        json={
            "text": "smoke at gate 3",
            "sender_identity": SENSOR_IDENTITY,
            "timestamp": "2026-09-06T07:30:00Z",
        },
    )

    assert resp.status_code == 202
    event = ctx.deps.persistence.fetch_event(resp.get_json()["event_id"])
    assert event["received_at"] == "2026-09-06T07:30:00"


def test_post_event_rejects_an_invalid_timestamp(ctx):
    """Post event rejects an invalid timestamp."""
    client = build_app(ctx).test_client()

    resp = client.post(
        "/Event",
        headers=auth_headers(SENSOR_IDENTITY),
        json={
            "text": "smoke at gate 3",
            "sender_identity": SENSOR_IDENTITY,
            "timestamp": "not-a-timestamp",
        },
    )

    assert resp.status_code == 400
    assert resp.get_json()["field"] == "timestamp"
    assert ctx.deps.persistence.fetch_events_range("2000-01-01", "2100-01-01") == []


def test_post_event_runs_to_completion_through_the_queue(ctx):
    """Post event runs to completion through the queue."""
    client = build_app(ctx).test_client()

    resp = client.post("/Event", headers=auth_headers(SENSOR_IDENTITY), json={"text": "smoke at gate 3", "sender_identity": SENSOR_IDENTITY})
    event_id = resp.get_json()["event_id"]
    ctx.queue.wait_until_idle()

    status = job_status(ctx, event_id)
    assert status["status"] == "succeeded"


def test_post_event_rejects_a_missing_text(ctx):
    """Post event rejects a missing text."""
    client = build_app(ctx).test_client()

    resp = client.post("/Event", headers=auth_headers(SENSOR_IDENTITY), json={"sender_identity": SENSOR_IDENTITY})

    assert resp.status_code == 400
    assert resp.get_json()["field"] == "text"


def test_post_event_rejects_a_missing_sender_identity(ctx):
    """Post event rejects a missing sender identity."""
    client = build_app(ctx).test_client()

    resp = client.post("/Event", headers=auth_headers(SENSOR_IDENTITY), json={"text": "smoke at gate 3"})

    assert resp.status_code == 400
    assert resp.get_json()["field"] == "sender_identity"


def test_post_event_rejects_sender_identity_impersonation_before_writing(ctx):
    """Post event rejects sender identity impersonation before writing."""
    client = build_app(ctx).test_client()

    resp = client.post(
        "/Event",
        headers=auth_headers(VIEWER_IDENTITY),
        json={"text": "smoke at gate 3", "sender_identity": SENSOR_IDENTITY},
    )

    assert resp.status_code == 403
    assert ctx.deps.persistence.fetch_events_range("2000-01-01", "2100-01-01") == []


def test_post_event_requires_authentication(ctx):
    """Post event requires authentication."""
    client = build_app(ctx).test_client()

    resp = client.post("/Event", json={"text": "smoke at gate 3", "sender_identity": SENSOR_IDENTITY})

    assert resp.status_code == 401


def test_post_event_authenticates_the_sensor_as_a_real_registered_identity_never_a_bypass(ctx):
    """Post event authenticates the sensor as a real registered identity never a bypass."""
    client = build_app(ctx).test_client()

    resp = client.post("/Event", headers=auth_headers("some-unregistered-sensor"), json={"text": "smoke at gate 3", "sender_identity": "some-unregistered-sensor"})

    assert resp.status_code == 401


def test_post_event_permits_a_viewer_level_sensor_identity(ctx):
    # send_message is VIEWER-level — the sensor identity is registered as
    # viewer in tests/api_fakes.py, matching how a real deployment would
    # provision it (no elevated privilege needed just to submit a report).
    """Post event permits a viewer level sensor identity."""
    client = build_app(ctx).test_client()

    resp = client.post("/Event", headers=auth_headers(SENSOR_IDENTITY), json={"text": "smoke at gate 3", "sender_identity": SENSOR_IDENTITY})

    assert resp.status_code == 202


def test_viewer_sensor_event_selecting_commander_only_protocol_is_held(ctx):
    """Viewer sensor event selecting commander only protocol is held."""
    protocols = tuple(
        dataclasses.replace(
            protocol,
            approval_flag=False,
            commander_only=True,
            requires_confirmation=False,
        )
        if protocol.name == "status_check"
        else protocol
        for protocol in ctx.deps.protocol_set.all()
    )
    scoped_ctx = dataclasses.replace(
        ctx,
        deps=dataclasses.replace(ctx.deps, protocol_set=ProtocolSet(protocols)),
    )
    client = build_app(scoped_ctx).test_client()

    resp = client.post(
        "/Event",
        headers=auth_headers(SENSOR_IDENTITY),
        json={"text": "smoke observed at gate 3", "sender_identity": SENSOR_IDENTITY},
    )

    assert resp.status_code == 202
    event_id = resp.get_json()["event_id"]
    ctx.queue.wait_until_idle()
    assert job_status(scoped_ctx, event_id)["status"] == "held_for_approval"
    assert scoped_ctx.deps.persistence.fetch_event(event_id)["outcome"] is None


def test_post_event_works_for_any_registered_identity_not_only_the_sensor_one(ctx):
    """Post event works for any registered identity not only the sensor one."""
    client = build_app(ctx).test_client()

    resp = client.post("/Event", headers=auth_headers(VIEWER_IDENTITY), json={"text": "smoke at gate 3", "sender_identity": VIEWER_IDENTITY})

    assert resp.status_code == 202


def test_post_event_works_for_a_commander_identity_too(ctx):
    """Post event works for a commander identity too."""
    client = build_app(ctx).test_client()

    resp = client.post("/Event", headers=auth_headers(COMMANDER_IDENTITY), json={"text": "smoke at gate 3", "sender_identity": COMMANDER_IDENTITY})

    assert resp.status_code == 202
    event = ctx.deps.persistence.fetch_event(resp.get_json()["event_id"])
    assert event["sender_permission_level"] == "commander"
