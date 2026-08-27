"""GET /Notifications (work_plan.md §8.12)."""

import pytest

from api.app import build_app
from history.write import record_event_outcome
from orchestrator.holds import create_approval_hold, create_clarification_hold
from orchestrator.main_agent import RiskAssessment
from orchestrator.main_agent import ProtocolSelectionResult
from tests.api_fakes import COMMANDER_IDENTITY, VIEWER_IDENTITY, auth_headers, build_context


@pytest.fixture
def teardown_ctx():
    contexts = []
    yield contexts
    for ctx in contexts:
        ctx.queue.stop()
        ctx.deps.persistence.close()


def _minimal_event(persistence, **overrides):
    event = {
        "received_at": "2026-08-24T10:00:00",
        "source": "sensor",
        "sender_identity": "submitter-1",
        "occurred_at": "2026-08-24T10:00:00",
        "raw_text": "text",
    }
    event.update(overrides)
    return persistence.append_event(event)


def test_requires_authentication(tmp_path, teardown_ctx):
    ctx = build_context(tmp_path)
    teardown_ctx.append(ctx)
    client = build_app(ctx).test_client()

    resp = client.get("/Notifications")

    assert resp.status_code == 401


def test_viewer_is_refused(tmp_path, teardown_ctx):
    ctx = build_context(tmp_path)
    teardown_ctx.append(ctx)
    client = build_app(ctx).test_client()

    resp = client.get("/Notifications", headers=auth_headers(VIEWER_IDENTITY))

    assert resp.status_code == 403


def test_commander_sees_hold_notifications(tmp_path, teardown_ctx):
    ctx = build_context(tmp_path)
    teardown_ctx.append(ctx)
    client = build_app(ctx).test_client()

    event_id = _minimal_event(ctx.deps.persistence)
    create_clarification_hold(ctx.deps.persistence, event_id, "raw text")

    resp = client.get("/Notifications", headers=auth_headers(COMMANDER_IDENTITY))

    assert resp.status_code == 200
    body = resp.get_json()
    assert len(body["notifications"]) == 1
    assert body["notifications"][0]["kind"] == "clarification_hold"
    assert body["notifications"][0]["payload"]["event_id"] == event_id


# §8.12's own design settled the viewer/commander question differently
# than an earlier draft: there is exactly one real caller (the bot's own
# service identity, COMMANDER level — docs/api_spec.md's "Service
# identity" section), so a viewer is refused outright
# (test_viewer_is_refused above), never given a filtered view.


def test_job_finished_and_job_failed_are_delivered_to_the_original_submitter(tmp_path, teardown_ctx):
    ctx = build_context(tmp_path)
    teardown_ctx.append(ctx)
    client = build_app(ctx).test_client()

    finished_id = _minimal_event(ctx.deps.persistence, sender_identity="alice")
    record_event_outcome(ctx.deps.persistence, finished_id, "succeeded", insight_text="all good")

    failed_id = _minimal_event(ctx.deps.persistence, sender_identity="bob")
    record_event_outcome(ctx.deps.persistence, failed_id, "failed", failure_reason="boom")

    resp = client.get("/Notifications", headers=auth_headers(COMMANDER_IDENTITY))
    body = resp.get_json()

    by_event = {n["payload"]["job_id"]: n for n in body["notifications"]}
    assert by_event[finished_id]["kind"] == "job_finished"
    assert by_event[finished_id]["target_chat_ids"] == ["alice"]
    assert by_event[failed_id]["kind"] == "job_failed"
    assert by_event[failed_id]["target_chat_ids"] == ["bob"]


def test_job_finished_and_job_failed_carry_the_real_originating_message_id(tmp_path, teardown_ctx):
    # Problem 2's own fix: reply_to_message_id must be the *actual*
    # source_message_id recorded when the triggering Telegram message was
    # first submitted — sourced here from two deliberately different,
    # distinctive fixture values, not a coincidental match (e.g. both
    # events sharing one hardcoded ID, or a null that happens to compare
    # equal to a missing key).
    ctx = build_context(tmp_path)
    teardown_ctx.append(ctx)
    client = build_app(ctx).test_client()

    finished_id = _minimal_event(
        ctx.deps.persistence, source="telegram", sender_identity="alice", source_message_id="original-msg-30201"
    )
    record_event_outcome(ctx.deps.persistence, finished_id, "succeeded", insight_text="all good")

    failed_id = _minimal_event(
        ctx.deps.persistence, source="telegram", sender_identity="bob", source_message_id="original-msg-59512"
    )
    record_event_outcome(ctx.deps.persistence, failed_id, "failed", failure_reason="boom")

    resp = client.get("/Notifications", headers=auth_headers(COMMANDER_IDENTITY))
    by_event = {n["payload"]["job_id"]: n for n in resp.get_json()["notifications"]}

    assert by_event[finished_id]["reply_to_message_id"] == "original-msg-30201"
    assert by_event[failed_id]["reply_to_message_id"] == "original-msg-59512"
    # Not each other's, and not swapped.
    assert by_event[finished_id]["reply_to_message_id"] != by_event[failed_id]["reply_to_message_id"]


def test_a_sensor_sourced_event_has_no_reply_to_message_id(tmp_path, teardown_ctx):
    # A sensor report has no Telegram message to reference at all —
    # source_message_id is never set for it, and the notification must
    # say so honestly (null), not fabricate or omit misleadingly.
    ctx = build_context(tmp_path)
    teardown_ctx.append(ctx)
    client = build_app(ctx).test_client()

    event_id = _minimal_event(ctx.deps.persistence, source="sensor")
    record_event_outcome(ctx.deps.persistence, event_id, "succeeded")

    resp = client.get("/Notifications", headers=auth_headers(COMMANDER_IDENTITY))
    [notification] = resp.get_json()["notifications"]

    assert notification["reply_to_message_id"] is None


def test_uncertain_produces_both_a_job_finished_and_an_uncertain_verdict_entry(tmp_path, teardown_ctx):
    ctx = build_context(tmp_path)
    teardown_ctx.append(ctx)
    client = build_app(ctx).test_client()

    event_id = _minimal_event(ctx.deps.persistence)
    record_event_outcome(ctx.deps.persistence, event_id, "uncertain", insight_text="mixed signal")

    resp = client.get("/Notifications", headers=auth_headers(COMMANDER_IDENTITY))
    kinds = {n["kind"] for n in resp.get_json()["notifications"]}

    assert kinds == {"job_finished", "uncertain_verdict"}


def test_closed_on_precedent_produces_both_a_job_finished_and_a_precedent_closure_entry(tmp_path, teardown_ctx):
    ctx = build_context(tmp_path)
    teardown_ctx.append(ctx)
    client = build_app(ctx).test_client()

    precedent_id = _minimal_event(ctx.deps.persistence)
    record_event_outcome(ctx.deps.persistence, precedent_id, "succeeded")

    closed_id = _minimal_event(ctx.deps.persistence)
    ctx.deps.persistence.update_event(closed_id, {"precedent_closed_by_event_id": precedent_id})
    record_event_outcome(ctx.deps.persistence, closed_id, "closed_on_precedent")

    resp = client.get("/Notifications", headers=auth_headers(COMMANDER_IDENTITY))
    notifications = resp.get_json()["notifications"]

    closure = next(n for n in notifications if n["kind"] == "precedent_closure")
    assert closure["payload"]["matched_precedent_event_id"] == precedent_id
    assert closure["payload"]["precedent_ending"] == "succeeded"


def test_no_match_produces_both_a_job_finished_and_a_no_match_notice_entry(tmp_path, teardown_ctx):
    # NO_MATCH is a real terminal outcome plus a one-way notification now
    # (never a held_events row) — same shape as uncertain/closed_on_precedent.
    ctx = build_context(tmp_path)
    teardown_ctx.append(ctx)
    client = build_app(ctx).test_client()

    event_id = _minimal_event(ctx.deps.persistence, raw_text="tell the viewer they need to come to me")
    ctx.deps.persistence.update_event(event_id, {"risk_level": "low", "risk_reason": "informational request"})
    record_event_outcome(ctx.deps.persistence, event_id, "no_match_protocol", failure_reason="no loaded protocol handles this kind of request")

    resp = client.get("/Notifications", headers=auth_headers(COMMANDER_IDENTITY))
    notifications = resp.get_json()["notifications"]

    kinds = {n["kind"] for n in notifications}
    assert kinds == {"job_finished", "no_match_notice"}

    notice = next(n for n in notifications if n["kind"] == "no_match_notice")
    assert notice["payload"]["event_id"] == event_id
    assert notice["payload"]["raw_text"] == "tell the viewer they need to come to me"
    assert notice["payload"]["reason"] == "no loaded protocol handles this kind of request"
    assert notice["payload"]["risk_level"] == "low"


def test_polling_twice_at_the_same_cursor_returns_nothing_new(tmp_path, teardown_ctx):
    ctx = build_context(tmp_path)
    teardown_ctx.append(ctx)
    client = build_app(ctx).test_client()

    event_id = _minimal_event(ctx.deps.persistence)
    create_clarification_hold(ctx.deps.persistence, event_id, "raw text")

    first = client.get("/Notifications", headers=auth_headers(COMMANDER_IDENTITY)).get_json()
    cursor = first["next_cursor"]
    assert len(first["notifications"]) == 1

    second = client.get(f"/Notifications?since={cursor}", headers=auth_headers(COMMANDER_IDENTITY)).get_json()

    assert second["notifications"] == []
    assert second["next_cursor"] == cursor


def test_a_new_hold_since_the_last_cursor_returns_exactly_that_one_item(tmp_path, teardown_ctx):
    ctx = build_context(tmp_path)
    teardown_ctx.append(ctx)
    client = build_app(ctx).test_client()

    first_event = _minimal_event(ctx.deps.persistence)
    create_clarification_hold(ctx.deps.persistence, first_event, "raw text one")
    cursor = client.get("/Notifications", headers=auth_headers(COMMANDER_IDENTITY)).get_json()["next_cursor"]

    second_event = _minimal_event(ctx.deps.persistence)
    create_clarification_hold(ctx.deps.persistence, second_event, "raw text two")

    resp = client.get(f"/Notifications?since={cursor}", headers=auth_headers(COMMANDER_IDENTITY)).get_json()

    assert len(resp["notifications"]) == 1
    assert resp["notifications"][0]["payload"]["event_id"] == second_event
    assert resp["next_cursor"] > cursor


def test_a_malformed_cursor_is_rejected(tmp_path, teardown_ctx):
    ctx = build_context(tmp_path)
    teardown_ctx.append(ctx)
    client = build_app(ctx).test_client()

    resp = client.get("/Notifications?since=not-a-number", headers=auth_headers(COMMANDER_IDENTITY))

    assert resp.status_code == 400
    assert resp.get_json()["field"] == "since"


def test_approval_hold_notification_carries_the_hold_detail(tmp_path, teardown_ctx):
    ctx = build_context(tmp_path)
    teardown_ctx.append(ctx)
    client = build_app(ctx).test_client()

    event_id = _minimal_event(ctx.deps.persistence)
    selection = ProtocolSelectionResult(status="selected", protocol_name="dispatch_response", reason="matched")
    risk = RiskAssessment(level="high", score=0.9, reason="side-effecting")
    create_approval_hold(ctx.deps.persistence, event_id, "flagged_protocol", selection, risk)

    resp = client.get("/Notifications", headers=auth_headers(COMMANDER_IDENTITY))
    [notification] = resp.get_json()["notifications"]

    assert notification["kind"] == "approval_hold"
    assert notification["payload"]["reason"] == "flagged_protocol"
    assert notification["payload"]["selected_protocol_name"] == "dispatch_response"
    assert notification["payload"]["risk_level"] == "high"
