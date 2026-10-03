"""Job-status and user-lookup HTTP routes."""

import threading
import time

import pytest

from api.app import build_app
from api.operations import job_status
from history.interface import record_event_outcome, record_event_state, record_initial_event, record_step_execution
from history.interface import InitialEventEnvelope, StepExecutionEnvelope
from orchestrator.holds import create_approval_hold, create_clarification_hold
from orchestrator.main_agent import RiskAssessment
from orchestrator.main_agent import ProtocolSelectionResult
from tests.api_fakes import VIEWER_IDENTITY, auth_headers, build_context, teardown_ctx
from tests.crewai_fakes import install_crewai_stub


@pytest.fixture(autouse=True)
def _mock_crewai(monkeypatch):
    """Mock crewai."""
    install_crewai_stub(monkeypatch)


@pytest.fixture
def ctx(tmp_path):
    """Ctx."""
    context = build_context(tmp_path)
    yield context
    context.queue.stop()
    context.deps.persistence.close()


def _new_event(ctx, **overrides) -> str:
    """New event."""
    envelope = InitialEventEnvelope(raw_text="text", source="telegram", received_at="2026-08-24T10:00:00", sender_identity="viewer-1")
    event_id = record_initial_event(ctx.deps.persistence, envelope)
    if overrides:
        record_event_state(ctx.deps.persistence, event_id, overrides)
    return event_id


def test_job_status_is_queued_for_an_event_not_yet_picked_up(ctx):
    """Job status is queued for an event not yet picked up."""
    ctx.queue.stop()  # nothing draining the queue
    event_id = _new_event(ctx)
    ctx.queue.submit((event_id, lambda: None))

    assert job_status(ctx, event_id) == {"event_id": event_id, "status": "queued", "trace_id": None}


def test_job_status_is_running_while_the_worker_is_on_it(ctx):
    """Job status is running while the worker is on it."""
    event_id = _new_event(ctx)
    release = threading.Event()

    ctx.queue.submit((event_id, release.wait))
    try:
        for _ in range(200):
            if ctx.queue.currently_processing() is not None:
                break
            time.sleep(0.01)

        # job_status always includes trace_id (139de27) -- None here since _new_event's
        # InitialEventEnvelope never sets one. A pre-existing mismatch: this assertion never
        # accounted for that field, which made it fail unconditionally and then deadlock the
        # ctx fixture's teardown (queue.stop()'s un-timed-out thread.join()) forever, since
        # release.set() below was never reached to unblock the worker.
        assert job_status(ctx, event_id) == {"event_id": event_id, "status": "running", "trace_id": None}
    finally:
        release.set()


def test_job_status_reports_held_for_clarification(ctx):
    """Job status reports held for clarification."""
    event_id = _new_event(ctx)
    create_clarification_hold(ctx.deps.persistence, event_id, "raw text")
    record_event_state(ctx.deps.persistence, event_id, {"clarification_held": True})

    status = job_status(ctx, event_id)

    assert status["status"] == "held_for_clarification"
    assert status["unresolved_field"] == "classification"


def test_job_status_reports_held_for_approval_with_its_reason(ctx):
    """Job status reports held for approval with its reason."""
    event_id = _new_event(ctx)
    selection = ProtocolSelectionResult(status="selected", protocol_name="dispatch_response", reason="matches")
    risk = RiskAssessment(score=0.9, level="high", reason="active fire")
    create_approval_hold(ctx.deps.persistence, event_id, "flagged_protocol", selection, risk)
    record_event_state(ctx.deps.persistence, event_id, {"approval_held": True})

    status = job_status(ctx, event_id)

    assert status["status"] == "held_for_approval"
    assert status["reason"] == "flagged_protocol"


def test_job_status_reports_a_resolved_hold_as_no_longer_held(ctx):
    # A resolved hold must not be reported as still held, even though the
    # event's own clarification_held/approval_held columns never clear —
    # this is exactly what fetch_held_event (§2.13) exists to distinguish.
    """Job status reports a resolved hold as no longer held."""
    event_id = _new_event(ctx)
    hold_id = create_clarification_hold(ctx.deps.persistence, event_id, "raw text")
    record_event_state(ctx.deps.persistence, event_id, {"clarification_held": True})
    ctx.deps.persistence.resolve_held_event("clarification", hold_id, {"resolved_by": "commander-1", "chosen_classification": "fire"})

    status = job_status(ctx, event_id)

    assert status["status"] != "held_for_clarification"


@pytest.mark.parametrize(
    "outcome,expected_detail_key",
    [
        ("succeeded", None),
        ("failed", "outcome_failure_reason"),
        ("uncertain", None),
        ("declined", None),
        ("closed_on_precedent", "precedent_closed_by_event_id"),
        ("no_match_protocol", "outcome_failure_reason"),
    ],
)
def test_job_status_reports_every_terminal_outcome(ctx, outcome, expected_detail_key):
    """Job status reports every terminal outcome."""
    event_id = _new_event(ctx)
    kwargs = {}
    if outcome in ("failed", "no_match_protocol"):
        kwargs["failure_reason"] = "no loaded protocol handles this kind of request" if outcome == "no_match_protocol" else "the model timed out"
    if outcome == "closed_on_precedent":
        record_event_state(ctx.deps.persistence, event_id, {"precedent_closed_by_event_id": "evt-old"})
    record_event_outcome(ctx.deps.persistence, event_id, outcome, **kwargs)

    status = job_status(ctx, event_id)

    assert status["status"] == outcome
    if expected_detail_key:
        assert "detail" in status


def test_job_status_reports_the_stored_report_text_when_present(ctx):
    """Job status reports the stored report text when present."""
    event_id = _new_event(ctx)
    record_event_outcome(ctx.deps.persistence, event_id, "succeeded", report_text="We checked the gate; all clear.")

    status = job_status(ctx, event_id)

    assert status["report_text"] == "We checked the gate; all clear."


def test_job_status_omits_report_text_when_rich_reporting_was_disabled(ctx):
    """Job status omits report text when rich reporting was disabled."""
    event_id = _new_event(ctx)
    record_event_outcome(ctx.deps.persistence, event_id, "succeeded")  # report_text defaults to None

    status = job_status(ctx, event_id)

    assert "report_text" not in status


def test_job_status_reports_steps_completed_on_a_successful_run(ctx):
    """Job status reports steps completed on a successful run."""
    event_id = _new_event(ctx)
    record_step_execution(ctx.deps.persistence, event_id, StepExecutionEnvelope(0, "reference_agent", "check gate 3", ["check_status"], "gate 3 is nominal", 1))
    record_step_execution(ctx.deps.persistence, event_id, StepExecutionEnvelope(1, "dispatch_agent", "dispatch", ["record_action"], "dispatched", 1))
    record_event_outcome(ctx.deps.persistence, event_id, "succeeded", insight_text="all clear")

    status = job_status(ctx, event_id)

    assert status["steps_completed"] == ["reference_agent: gate 3 is nominal", "dispatch_agent: dispatched"]


def test_job_status_reports_the_failed_step_agent_and_steps_completed_before_it(ctx):
    """Job status reports the failed step agent and steps completed before it."""
    event_id = _new_event(ctx)
    record_step_execution(ctx.deps.persistence, event_id, StepExecutionEnvelope(0, "reference_agent", "check gate 3", ["check_status"], "gate 3 is nominal", 1))
    record_step_execution(ctx.deps.persistence, event_id, StepExecutionEnvelope(1, "dispatch_agent", "dispatch", ["record_action"], None, 3))
    record_event_outcome(ctx.deps.persistence, event_id, "failed", failure_reason="attempt limit exhausted")

    status = job_status(ctx, event_id)

    assert status["failed_step_agent_name"] == "dispatch_agent"
    assert status["steps_completed"] == ["reference_agent: gate 3 is nominal"]
    assert status["detail"] == "attempt limit exhausted"


def test_job_status_omits_steps_completed_and_failed_agent_when_no_step_ever_ran(ctx):
    # closed_on_precedent never reaches the executor — nothing to report.
    """Job status omits steps completed and failed agent when no step ever ran."""
    event_id = _new_event(ctx)
    record_event_state(ctx.deps.persistence, event_id, {"precedent_closed_by_event_id": "evt-old"})
    record_event_outcome(ctx.deps.persistence, event_id, "closed_on_precedent")

    status = job_status(ctx, event_id)

    assert "steps_completed" not in status
    assert "failed_step_agent_name" not in status


def test_get_job_route_includes_steps_completed_in_the_response_body(ctx):
    """Get job route includes steps completed in the response body."""
    client = build_app(ctx).test_client()
    event_id = _new_event(ctx)
    record_step_execution(ctx.deps.persistence, event_id, StepExecutionEnvelope(0, "reference_agent", "check gate 3", ["check_status"], "gate 3 is nominal", 1))
    record_event_outcome(ctx.deps.persistence, event_id, "succeeded", insight_text="all clear")

    resp = client.get(f"/Job/{event_id}", headers=auth_headers(VIEWER_IDENTITY))

    assert resp.get_json()["steps_completed"] == ["reference_agent: gate 3 is nominal"]


def test_job_status_returns_none_for_an_unknown_event_id(ctx):
    """Job status returns none for an unknown event id."""
    assert job_status(ctx, "does-not-exist") is None


def test_get_job_route_returns_404_for_an_unknown_event(ctx):
    """Get job route returns 404 for an unknown event."""
    client = build_app(ctx).test_client()

    resp = client.get("/Job/does-not-exist", headers=auth_headers(VIEWER_IDENTITY))

    assert resp.status_code == 404
    assert resp.get_json()["error_class"] == "invalid_input"


def test_get_job_route_requires_authentication(ctx):
    """Get job route requires authentication."""
    client = build_app(ctx).test_client()
    event_id = _new_event(ctx)

    resp = client.get(f"/Job/{event_id}")

    assert resp.status_code == 401


def test_get_job_route_rejects_an_invalid_wait_seconds(ctx):
    """Get job route rejects an invalid wait seconds."""
    client = build_app(ctx).test_client()
    event_id = _new_event(ctx)

    resp = client.get(f"/Job/{event_id}?wait_seconds=99", headers=auth_headers(VIEWER_IDENTITY))

    assert resp.status_code == 400


def test_get_job_route_wait_returns_when_the_job_finishes(ctx):
    """Get job route wait returns when the job finishes."""
    client = build_app(ctx).test_client()
    event_id = _new_event(ctx)

    def _finish():
        time.sleep(0.05)
        record_event_outcome(ctx.deps.persistence, event_id, "succeeded", insight_text="done")

    worker = threading.Thread(target=_finish)
    worker.start()
    resp = client.get(f"/Job/{event_id}?wait_seconds=2", headers=auth_headers(VIEWER_IDENTITY))
    worker.join()

    assert resp.status_code == 200
    assert resp.get_json()["status"] == "succeeded"


def test_get_job_route_returns_the_status_body(ctx):
    """Get job route returns the status body."""
    client = build_app(ctx).test_client()
    event_id = _new_event(ctx)
    record_event_outcome(ctx.deps.persistence, event_id, "succeeded", insight_text="all clear")

    resp = client.get(f"/Job/{event_id}", headers=auth_headers(VIEWER_IDENTITY))

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "succeeded"
    assert body["insight_text"] == "all clear"


def test_get_job_route_denies_a_viewer_for_someone_elses_job(ctx):
    # Decision record: view_job_status is ownership
    # scoped for a viewer to events they themselves submitted. 404 (not
    # 403) is returned, matching the "unknown job" response — it does not
    # confirm that a job belonging to another sender exists.
    """Get job route denies a viewer for someone elses job."""
    client = build_app(ctx).test_client()
    envelope = InitialEventEnvelope(raw_text="text", source="telegram", received_at="2026-08-24T10:00:00", sender_identity="someone-else")
    event_id = record_initial_event(ctx.deps.persistence, envelope)
    record_event_outcome(ctx.deps.persistence, event_id, "succeeded", insight_text="all clear")

    resp = client.get(f"/Job/{event_id}", headers=auth_headers(VIEWER_IDENTITY))

    assert resp.status_code == 404


def test_get_job_route_commander_sees_any_viewers_job(ctx):
    """Get job route commander sees any viewers job."""
    client = build_app(ctx).test_client()
    event_id = _new_event(ctx)  # sender_identity="viewer-1"
    record_event_outcome(ctx.deps.persistence, event_id, "succeeded", insight_text="all clear")

    resp = client.get(f"/Job/{event_id}", headers=auth_headers(COMMANDER_IDENTITY))

    assert resp.status_code == 200

"""GET /User/<identity> and GET /Commanders user-lookup routes."""

import pytest

from api.app import build_app
from tests.api_fakes import COMMANDER_IDENTITY, VIEWER_IDENTITY, auth_headers, build_context



# -- GET /User/<identity> ----------------------------------------------------


def test_a_known_identity_reports_registered_and_its_level(tmp_path, teardown_ctx):
    """A known identity reports registered and its level."""
    ctx = build_context(tmp_path)
    teardown_ctx.append(ctx)
    client = build_app(ctx).test_client()

    resp = client.get(f"/User/{VIEWER_IDENTITY}", headers=auth_headers(COMMANDER_IDENTITY))

    assert resp.status_code == 200
    assert resp.get_json() == {"registered": True, "permission_level": "viewer", "full_name": "", "auto_register": False}


def test_an_unknown_identity_reports_unregistered_not_an_error(tmp_path, teardown_ctx):
    """An unknown identity reports unregistered not an error."""
    ctx = build_context(tmp_path)
    teardown_ctx.append(ctx)
    client = build_app(ctx).test_client()

    resp = client.get("/User/nobody", headers=auth_headers(COMMANDER_IDENTITY))

    assert resp.status_code == 200
    assert resp.get_json() == {"registered": False, "permission_level": None, "full_name": None}


def test_viewer_can_resolve_their_own_identity(tmp_path, teardown_ctx):
    """Viewer can resolve their own identity."""
    ctx = build_context(tmp_path)
    teardown_ctx.append(ctx)
    client = build_app(ctx).test_client()

    resp = client.get(f"/User/{VIEWER_IDENTITY}", headers=auth_headers(VIEWER_IDENTITY))

    assert resp.status_code == 200
    assert resp.get_json() == {"registered": True, "permission_level": "viewer", "full_name": ""}


def test_user_can_update_only_their_own_normalized_full_name(tmp_path, teardown_ctx):
    """User can update only their own normalized full name."""
    ctx = build_context(tmp_path)
    teardown_ctx.append(ctx)
    client = build_app(ctx).test_client()

    updated = client.put(
        f"/User/{VIEWER_IDENTITY}/name",
        headers=auth_headers(VIEWER_IDENTITY),
        json={"full_name": "  Dana   Levi  "},
    )
    forbidden = client.put(
        f"/User/{COMMANDER_IDENTITY}/name",
        headers=auth_headers(VIEWER_IDENTITY),
        json={"full_name": "Other Person"},
    )

    assert updated.status_code == 200
    assert updated.get_json()["full_name"] == "Dana Levi"
    assert ctx.deps.persistence.read_user(VIEWER_IDENTITY)["full_name"] == "Dana Levi"
    assert forbidden.status_code == 403


def test_full_name_update_rejects_an_unclear_name(tmp_path, teardown_ctx):
    """Full name update rejects an unclear name."""
    ctx = build_context(tmp_path)
    teardown_ctx.append(ctx)
    client = build_app(ctx).test_client()

    response = client.put(
        f"/User/{VIEWER_IDENTITY}/name",
        headers=auth_headers(VIEWER_IDENTITY),
        json={"full_name": "Dana"},
    )

    assert response.status_code == 400
    assert response.get_json()["field"] == "full_name"


def test_viewer_is_denied_resolving_another_identity(tmp_path, teardown_ctx):
    # Decision record: view_user_registration is
    # ownership-scoped for a viewer to their own identity only. A commander
    # (e.g. bot-service, resolving arbitrary callers) is unrestricted.
    """Viewer is denied resolving another identity."""
    ctx = build_context(tmp_path)
    teardown_ctx.append(ctx)
    client = build_app(ctx).test_client()

    resp = client.get(f"/User/{COMMANDER_IDENTITY}", headers=auth_headers(VIEWER_IDENTITY))

    assert resp.status_code == 403


def test_requires_authentication(tmp_path, teardown_ctx):
    """Requires authentication."""
    ctx = build_context(tmp_path)
    teardown_ctx.append(ctx)
    client = build_app(ctx).test_client()

    resp = client.get(f"/User/{COMMANDER_IDENTITY}")

    assert resp.status_code == 401


# -- GET /Commanders ----------------------------------------------------------


def test_commander_gets_the_full_roster(tmp_path, teardown_ctx):
    """Commander gets the full roster."""
    ctx = build_context(tmp_path)
    teardown_ctx.append(ctx)
    client = build_app(ctx).test_client()

    resp = client.get("/Commanders", headers=auth_headers(COMMANDER_IDENTITY))

    assert resp.status_code == 200
    assert resp.get_json() == {"commanders": [{"telegram_identity": COMMANDER_IDENTITY}]}


def test_viewer_is_refused_the_roster(tmp_path, teardown_ctx):
    """Viewer is refused the roster."""
    ctx = build_context(tmp_path)
    teardown_ctx.append(ctx)
    client = build_app(ctx).test_client()

    resp = client.get("/Commanders", headers=auth_headers(VIEWER_IDENTITY))

    assert resp.status_code == 403


def test_the_roster_excludes_viewers(tmp_path, teardown_ctx):
    """The roster excludes viewers."""
    ctx = build_context(tmp_path)
    teardown_ctx.append(ctx)
    ctx.deps.persistence.write_user("commander-2", "commander")
    client = build_app(ctx).test_client()

    resp = client.get("/Commanders", headers=auth_headers(COMMANDER_IDENTITY))

    identities = {c["telegram_identity"] for c in resp.get_json()["commanders"]}
    assert identities == {COMMANDER_IDENTITY, "commander-2"}
    assert VIEWER_IDENTITY not in identities
