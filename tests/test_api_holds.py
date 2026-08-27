import types

import pytest

from agents import adapter
from api.app import build_app
from api.operations import job_status
from tests.api_fakes import COMMANDER_IDENTITY, VIEWER_IDENTITY, ScriptedAgent, auth_headers, build_context


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


def _agent(dispatch):
    return ScriptedAgent(dispatch)


@pytest.fixture
def teardown_ctx():
    contexts = []
    yield contexts
    for ctx in contexts:
        ctx.queue.stop()
        ctx.deps.persistence.close()


def _submit_report(client, text="report text"):
    resp = client.post("/Event", headers=auth_headers(VIEWER_IDENTITY), json={"text": text, "sender_identity": VIEWER_IDENTITY})
    assert resp.status_code == 202
    return resp.get_json()["event_id"]


# -- Clarify ----------------------------------------------------------------


def _clarification_agent():
    return _agent({"Extract this operational event": '{"classification": null, "area": null, "entities": [], "description": null, "severity": null, "occurred_at": null}'})


def _make_clarification_hold(tmp_path, teardown_ctx):
    ctx = build_context(tmp_path, main_agent=_clarification_agent())
    teardown_ctx.append(ctx)
    client = build_app(ctx).test_client()
    event_id = _submit_report(client, "something unclear happened")
    ctx.queue.wait_until_idle()
    assert job_status(ctx, event_id)["status"] == "held_for_clarification"
    return ctx, client, event_id


def test_clarify_with_a_valid_type_returns_202_and_resumes(tmp_path, teardown_ctx):
    ctx, client, event_id = _make_clarification_hold(tmp_path, teardown_ctx)
    # Continuing past extraction needs the full happy-path prompts too.
    ctx.main_agent._dispatch.update({
        "RISK_SCORE": "RISK_SCORE: 0.1\nREASON: low",
        "Choose the protocol": "SELECTED: status_check\nREASON: fits",
        "participating in the": "AGENT: reference_agent\nTASK: check it",
        "VERDICT:": "VERDICT: success\nREASONING: matches",
    })

    resp = client.post(f"/Clarify/{event_id}", headers=auth_headers(COMMANDER_IDENTITY), json={"classification": "fire"})

    assert resp.status_code == 202
    assert resp.get_json()["status"] == "queued"

    ctx.queue.wait_until_idle()
    status = job_status(ctx, event_id)
    assert status["status"] == "succeeded"
    event = ctx.deps.persistence.fetch_event(event_id)
    assert event["classification"] == "fire"


def test_clarify_with_a_value_outside_the_registry_is_rejected(tmp_path, teardown_ctx):
    ctx, client, event_id = _make_clarification_hold(tmp_path, teardown_ctx)

    resp = client.post(f"/Clarify/{event_id}", headers=auth_headers(COMMANDER_IDENTITY), json={"classification": "not_a_real_type"})

    assert resp.status_code == 400
    assert resp.get_json()["field"] == "classification"


def test_clarify_rejects_a_missing_classification_field(tmp_path, teardown_ctx):
    ctx, client, event_id = _make_clarification_hold(tmp_path, teardown_ctx)

    resp = client.post(f"/Clarify/{event_id}", headers=auth_headers(COMMANDER_IDENTITY), json={})

    assert resp.status_code == 400
    assert resp.get_json()["field"] == "classification"


def test_clarify_requires_commander_level(tmp_path, teardown_ctx):
    ctx, client, event_id = _make_clarification_hold(tmp_path, teardown_ctx)

    resp = client.post(f"/Clarify/{event_id}", headers=auth_headers(VIEWER_IDENTITY), json={"classification": "fire"})

    assert resp.status_code == 403


def test_clarify_on_an_event_with_no_hold_is_not_found(tmp_path, teardown_ctx):
    ctx = build_context(tmp_path)
    teardown_ctx.append(ctx)
    client = build_app(ctx).test_client()

    resp = client.post("/Clarify/no-such-event", headers=auth_headers(COMMANDER_IDENTITY), json={"classification": "fire"})

    assert resp.status_code == 404


def test_a_second_commander_answering_an_already_resolved_clarification_gets_a_named_conflict(tmp_path, teardown_ctx):
    ctx, client, event_id = _make_clarification_hold(tmp_path, teardown_ctx)
    ctx.main_agent._dispatch.update({
        "RISK_SCORE": "RISK_SCORE: 0.1\nREASON: low",
        "Choose the protocol": "SELECTED: status_check\nREASON: fits",
        "participating in the": "AGENT: reference_agent\nTASK: check it",
        "VERDICT:": "VERDICT: success\nREASONING: matches",
    })
    ctx.deps.persistence.write_user("commander-2", "commander")
    first = client.post(f"/Clarify/{event_id}", headers=auth_headers(COMMANDER_IDENTITY), json={"classification": "fire"})
    assert first.status_code == 202

    second = client.post(f"/Clarify/{event_id}", headers={"X-Identity": "commander-2"}, json={"classification": "medical"})

    assert second.status_code == 409
    body = second.get_json()
    assert COMMANDER_IDENTITY in body["message"]


# -- Approve: flagged_protocol (approve / reject) ----------------------------


def _flagged_approval_agent():
    return _agent(
        {
            "Extract this operational event": '{"classification": "fire", "area": "north_sector", "entities": [], "description": "d", "severity": "high", "occurred_at": "2026-08-24T09:00:00"}',
            "RISK_SCORE": "RISK_SCORE: 0.9\nREASON: high",
            "Choose the protocol": "SELECTED: dispatch_response\nREASON: fits",
            "participating in the": "AGENT: reference_agent\nTASK: dispatch",
            "VERDICT:": "VERDICT: success\nREASONING: matches",
        }
    )


def _make_flagged_approval_hold(tmp_path, teardown_ctx):
    ctx = build_context(tmp_path, main_agent=_flagged_approval_agent())
    teardown_ctx.append(ctx)
    client = build_app(ctx).test_client()
    event_id = _submit_report(client, "fire at gate 3")
    ctx.queue.wait_until_idle()
    assert job_status(ctx, event_id)["status"] == "held_for_approval"
    return ctx, client, event_id


def test_approve_returns_202_and_resumes_to_success(tmp_path, teardown_ctx):
    ctx, client, event_id = _make_flagged_approval_hold(tmp_path, teardown_ctx)

    resp = client.post(f"/Approve/{event_id}", headers=auth_headers(COMMANDER_IDENTITY), json={"decision": "approved"})

    assert resp.status_code == 202
    assert resp.get_json()["status"] == "queued"

    ctx.queue.wait_until_idle()
    assert job_status(ctx, event_id)["status"] == "succeeded"


def test_reject_returns_declined_synchronously_with_no_job_left_running(tmp_path, teardown_ctx):
    ctx, client, event_id = _make_flagged_approval_hold(tmp_path, teardown_ctx)

    resp = client.post(f"/Approve/{event_id}", headers=auth_headers(COMMANDER_IDENTITY), json={"decision": "rejected"})

    assert resp.status_code == 200
    assert resp.get_json() == {"event_id": event_id, "status": "declined"}
    assert ctx.queue.currently_processing() is None
    assert ctx.queue.qsize() == 0
    assert job_status(ctx, event_id)["status"] == "declined"


def test_approve_requires_commander_level(tmp_path, teardown_ctx):
    ctx, client, event_id = _make_flagged_approval_hold(tmp_path, teardown_ctx)

    resp = client.post(f"/Approve/{event_id}", headers=auth_headers(VIEWER_IDENTITY), json={"decision": "approved"})

    assert resp.status_code == 403


def test_approve_on_an_event_with_no_hold_is_not_found(tmp_path, teardown_ctx):
    ctx = build_context(tmp_path)
    teardown_ctx.append(ctx)
    client = build_app(ctx).test_client()

    resp = client.post("/Approve/no-such-event", headers=auth_headers(COMMANDER_IDENTITY), json={"decision": "approved"})

    assert resp.status_code == 404


def test_approve_rejects_a_missing_decision_field(tmp_path, teardown_ctx):
    ctx, client, event_id = _make_flagged_approval_hold(tmp_path, teardown_ctx)

    resp = client.post(f"/Approve/{event_id}", headers=auth_headers(COMMANDER_IDENTITY), json={})

    assert resp.status_code == 400
    assert resp.get_json()["field"] == "decision"


def test_a_second_commander_answering_an_already_resolved_approval_gets_a_named_conflict(tmp_path, teardown_ctx):
    ctx, client, event_id = _make_flagged_approval_hold(tmp_path, teardown_ctx)
    ctx.deps.persistence.write_user("commander-2", "commander")
    first = client.post(f"/Approve/{event_id}", headers=auth_headers(COMMANDER_IDENTITY), json={"decision": "rejected"})
    assert first.status_code == 200

    second = client.post(f"/Approve/{event_id}", headers={"X-Identity": "commander-2"}, json={"decision": "approved"})

    assert second.status_code == 409
    body = second.get_json()
    assert COMMANDER_IDENTITY in body["message"]


# -- Approve: ambiguous_selection (candidate protocol name) ------------------


def _ambiguous_approval_agent():
    return _agent(
        {
            "Extract this operational event": '{"classification": "fire", "area": "north_sector", "entities": [], "description": "d", "severity": "low", "occurred_at": "2026-08-24T09:00:00"}',
            "RISK_SCORE": "RISK_SCORE: 0.1\nREASON: low, but ambiguous",
            "Choose the protocol": "AMBIGUOUS: status_check,dispatch_response\nREASON: both fit",
            "participating in the": "AGENT: reference_agent\nTASK: check it",
            "VERDICT:": "VERDICT: success\nREASONING: matches",
        }
    )


def _make_ambiguous_approval_hold(tmp_path, teardown_ctx):
    ctx = build_context(tmp_path, main_agent=_ambiguous_approval_agent())
    teardown_ctx.append(ctx)
    client = build_app(ctx).test_client()
    event_id = _submit_report(client, "smoke at gate 3, unclear severity")
    ctx.queue.wait_until_idle()
    status = job_status(ctx, event_id)
    assert status["status"] == "held_for_approval"
    return ctx, client, event_id


def test_a_valid_candidate_name_returns_202_and_resumes_with_that_protocol(tmp_path, teardown_ctx):
    ctx, client, event_id = _make_ambiguous_approval_hold(tmp_path, teardown_ctx)

    resp = client.post(f"/Approve/{event_id}", headers=auth_headers(COMMANDER_IDENTITY), json={"decision": "status_check"})

    assert resp.status_code == 202
    assert resp.get_json()["status"] == "queued"

    ctx.queue.wait_until_idle()
    assert job_status(ctx, event_id)["status"] == "succeeded"
    event = ctx.deps.persistence.fetch_event(event_id)
    assert event["selected_protocol"] == "status_check"


def test_a_name_outside_the_holds_candidates_is_rejected(tmp_path, teardown_ctx):
    ctx, client, event_id = _make_ambiguous_approval_hold(tmp_path, teardown_ctx)

    resp = client.post(f"/Approve/{event_id}", headers=auth_headers(COMMANDER_IDENTITY), json={"decision": "not_a_real_protocol"})

    assert resp.status_code == 400
    body = resp.get_json()
    assert body["field"] == "decision"
    assert "status_check" in body["message"] and "dispatch_response" in body["message"]

    # the hold is untouched — still pending, confirmed by a valid answer still working
    retry = client.post(f"/Approve/{event_id}", headers=auth_headers(COMMANDER_IDENTITY), json={"decision": "dispatch_response"})
    assert retry.status_code == 202


def test_approved_and_rejected_are_not_valid_answers_to_an_ambiguous_hold(tmp_path, teardown_ctx):
    ctx, client, event_id = _make_ambiguous_approval_hold(tmp_path, teardown_ctx)

    approved = client.post(f"/Approve/{event_id}", headers=auth_headers(COMMANDER_IDENTITY), json={"decision": "approved"})
    assert approved.status_code == 400

    rejected = client.post(f"/Approve/{event_id}", headers=auth_headers(COMMANDER_IDENTITY), json={"decision": "rejected"})
    assert rejected.status_code == 400
