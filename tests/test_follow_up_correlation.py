from persistence import open_persistence
from orchestrator.follow_up import resolve_follow_up


def _event(store, event_id, *, conversation="conversation-1", sender="user-1", received="2026-09-17T10:00:00+00:00", state="failed", outcome="failed", protocol="dispatch_drone_to_incident", reason="drone unavailable", receipts=None):
    store.append_event(
        {
            "event_id": event_id,
            "received_at": received,
            "source": "telegram",
            "sender_identity": sender,
            "source_message_id": f"message-{event_id}",
            "conversation_id": conversation,
            "raw_text": "dispatch drone",
            "selected_protocol": protocol,
            "action_state": state,
            "action_failure_reason": reason,
            "outcome": outcome,
            "outcome_failure_reason": reason,
            "action_tool_receipts": receipts,
        }
    )


def test_failed_action_follow_up_uses_persisted_failure_reason(tmp_path):
    store = open_persistence(str(tmp_path / "history.db"))
    _event(store, "event-1")

    result = resolve_follow_up(store, "conversation-1", "user-1", "למה?")

    assert result.kind == "failed"
    assert result.event.event_id == "event-1"
    assert result.event.failure_reason == "drone unavailable"
    store.close()


def test_verified_success_follow_up_does_not_invoke_a_tool(tmp_path):
    store = open_persistence(str(tmp_path / "history.db"))
    _event(
        store,
        "event-1",
        state="executed",
        outcome="succeeded",
        reason=None,
        receipts=[{"tool_name": "dispatch_drone", "status": "succeeded", "success": True}],
    )

    result = resolve_follow_up(store, "conversation-1", "user-1", "זה בוצע?")

    assert result.kind == "executed"
    assert result.event.tool_receipts[0]["tool_name"] == "dispatch_drone"
    store.close()


def test_executed_text_without_verified_receipt_is_not_reported_as_executed(tmp_path):
    store = open_persistence(str(tmp_path / "history.db"))
    _event(store, "event-1", state="executed", outcome="succeeded", reason=None, receipts=[])

    result = resolve_follow_up(store, "conversation-1", "user-1", "זה בוצע?")

    assert result.kind == "unverified"
    store.close()


def test_successful_read_only_event_is_not_action_referent(tmp_path):
    store = open_persistence(str(tmp_path / "history.db"))
    _event(
        store,
        "event-1",
        state=None,
        outcome="succeeded",
        protocol="report_team_availability",
        reason=None,
        receipts=[],
    )

    result = resolve_follow_up(store, "conversation-1", "user-1", "זה בוצע?")

    assert result.kind == "none"
    store.close()


def test_pending_approval_follow_up_stays_pending(tmp_path):
    store = open_persistence(str(tmp_path / "history.db"))
    _event(store, "event-1", state="pending_approval", outcome=None)

    result = resolve_follow_up(store, "conversation-1", "user-1", "מה עכשיו?")

    assert result.kind == "pending_approval"
    store.close()


def test_two_recent_events_are_ambiguous_but_other_conversation_is_excluded(tmp_path):
    store = open_persistence(str(tmp_path / "history.db"))
    _event(store, "event-old", received="2026-09-17T09:00:00+00:00")
    _event(store, "event-new", received="2026-09-17T10:00:00+00:00")
    _event(store, "other-conversation", conversation="conversation-2", received="2026-09-17T11:00:00+00:00")

    result = resolve_follow_up(store, "conversation-1", "user-1", "למה?")

    assert result.kind == "ambiguous"
    assert result.candidate_event_ids == ("event-new", "event-old")
    store.close()


def test_old_event_does_not_override_new_operational_event(tmp_path):
    store = open_persistence(str(tmp_path / "history.db"))
    _event(store, "event-old", received="2026-09-15T10:00:00+00:00")
    _event(store, "event-new", received="2026-09-17T10:00:00+00:00", reason="new failure")

    result = resolve_follow_up(store, "conversation-1", "user-1", "למה?")

    assert result.kind == "failed"
    assert result.event.event_id == "event-new"
    store.close()


def test_approval_yes_is_correlated_and_does_not_bypass_authentication(tmp_path):
    store = open_persistence(str(tmp_path / "history.db"))
    _event(store, "event-1", state="pending_approval", outcome=None)
    store.store_held_event("approval", {"event_id": "event-1", "reason": "flagged_protocol"})

    result = resolve_follow_up(store, "conversation-1", "user-1", "כן")

    assert result.kind == "pending_approval"
    assert result.hold["event_id"] == "event-1"
    assert not result.hold["resolved"]
    store.close()


def test_correlation_survives_persistence_reload(tmp_path):
    path = str(tmp_path / "history.db")
    store = open_persistence(path)
    _event(store, "event-1")
    store.close()

    reloaded = open_persistence(path)
    result = resolve_follow_up(reloaded, "conversation-1", "user-1", "מה קרה?")

    assert result.event.event_id == "event-1"
    assert result.kind == "failed"
    reloaded.close()
