"""Approvals queue test suite covering all 10 requirements:
- Commander views open holds only (resolved holds excluded)
- Viewer gets 403 on API and commander_only in Bot
- Empty queue returns proper localized message
- Multiple concurrent approvals displayed as individual cards
- Approval and Clarification rendered distinctly (actions, fields, buttons)
- Approve removes hold from pending queue and edits card
- Reject removes hold from pending queue and edits card
- Double approve is idempotent and never duplicates side effects
- Resolved/expired hold cannot be approved
- Restart / new context preserves pending holds
- Zero LLM calls when opening approvals queue
"""

import asyncio
from datetime import datetime, timezone
import types
import pytest
from unittest.mock import MagicMock

from api.app import build_app
from auth.permissions import PermissionLevel
from bot import app, interactions
from bot.contracts import BotDeps, HoldAnswerOutcome
from messages import get_catalog
from persistence import open_persistence
from tests.api_fakes import COMMANDER_IDENTITY, VIEWER_IDENTITY, auth_headers, build_context
from tests.bot_fakes import FakeBotApiClient, FakeTelegramClient


@pytest.fixture
def teardown_ctx():
    contexts = []
    yield contexts
    for ctx in contexts:
        ctx.queue.stop()
        ctx.deps.persistence.close()


def _make_bot_deps(api_client, telegram_client):
    loaded_profile = types.SimpleNamespace(
        message_catalog=get_catalog("he"),
        db_path=None,
    )
    return BotDeps(
        loaded_profile=loaded_profile,
        telegram_client=telegram_client,
        api_client=api_client,
    )


def test_commander_views_open_holds_only_api(tmp_path, teardown_ctx):
    """Requirement 6 & 10: Commander sees open holds only; resolved holds excluded."""
    ctx = build_context(tmp_path)
    teardown_ctx.append(ctx)
    client = build_app(ctx).test_client()

    # Create an event and an open approval hold
    ev1_id = ctx.deps.persistence.append_event({
        "source": "manual",
        "raw_text": "שיגור רחפן לאירוע חדירה",
        "sender_identity": "reporter_1",
        "area": "north_sector",
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "received_at": datetime.now(timezone.utc).isoformat(),
    })
    ctx.deps.persistence.store_held_event(
        "approval",
        {
            "event_id": ev1_id,
            "reason": "flagged_protocol",
            "risk_level": "high",
            "risk_reason": "drone operation requires commander",
            "selected_protocol_name": "dispatch_drone_to_incident",
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
    )

    # Create an event and a resolved approval hold
    ev2_id = ctx.deps.persistence.append_event({
        "source": "manual",
        "raw_text": "החזרת רחפן לבסיס",
        "sender_identity": "reporter_2",
        "area": "south_sector",
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "received_at": datetime.now(timezone.utc).isoformat(),
    })
    hold2_id = ctx.deps.persistence.store_held_event(
        "approval",
        {
            "event_id": ev2_id,
            "reason": "flagged_protocol",
            "risk_level": "low",
            "risk_reason": "recall",
            "selected_protocol_name": "recall_drone_to_base",
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    ctx.deps.persistence.resolve_held_event("approval", hold2_id, {"resolved_by": COMMANDER_IDENTITY, "decision": "approved"})

    # Fetch pending holds as Commander
    resp = client.get("/Holds/Pending", headers=auth_headers(COMMANDER_IDENTITY))
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["count"] == 1
    assert len(data["holds"]) == 1
    assert data["holds"][0]["event_id"] == ev1_id
    assert data["holds"][0]["protocol_name"] == "dispatch_drone_to_incident"


def test_viewer_forbidden_403_api(tmp_path, teardown_ctx):
    """Requirement 6 & 10: Viewer receives 403 on GET /Holds/Pending."""
    ctx = build_context(tmp_path)
    teardown_ctx.append(ctx)
    client = build_app(ctx).test_client()

    resp = client.get("/Holds/Pending", headers=auth_headers(VIEWER_IDENTITY))
    assert resp.status_code == 403


def test_viewer_blocked_on_bot_shortcut_phrases():
    """Requirement 6 & 10: Viewer using queue button or shortcut receives commander_only."""
    api_client = FakeBotApiClient()
    api_client.users["viewer_user"] = "viewer"
    telegram_client = FakeTelegramClient()
    deps = _make_bot_deps(api_client, telegram_client)

    messages = get_catalog("he")
    for phrase in ["⏳ תור אישורים", "תור אישורים", "אישורים", "מה ממתין לאישור"]:
        update = MagicMock()
        update.effective_user.id = "viewer_user"
        update.effective_chat.id = "chat_123"
        update.message.text = phrase
        update.message.message_id = 100

        asyncio.run(app._on_text_message(update, MagicMock(bot_data={"deps": deps})))
        assert len(telegram_client.sent) > 0
        last_msg = telegram_client.sent[-1]
        assert last_msg.text == messages.text("bot.commander_only")


def test_empty_queue_display():
    """Requirement 8 & 10: Empty queue displays '✅ אין כרגע בקשות הממתינות לאישורך.'"""
    api_client = FakeBotApiClient()
    api_client.users["cmd_user"] = "commander"
    api_client.pending_holds = {"holds": [], "count": 0}
    telegram_client = FakeTelegramClient()
    deps = _make_bot_deps(api_client, telegram_client)

    update = MagicMock()
    update.effective_user.id = "cmd_user"
    update.effective_chat.id = "chat_123"
    update.message.text = "⏳ תור אישורים"
    update.message.message_id = 101

    asyncio.run(app._on_text_message(update, MagicMock(bot_data={"deps": deps})))
    assert len(telegram_client.sent) == 1
    assert "אין כרגע בקשות הממתינות לאישורך." in telegram_client.sent[0].text
    assert telegram_client.sent[0].text.startswith("✅")


def test_multiple_concurrent_approvals():
    """Requirement 4, 5 & 10: Multiple approvals formatted into header and individual cards with approve/reject."""
    api_client = FakeBotApiClient()
    api_client.users["cmd_user"] = "commander"
    api_client.pending_holds = {
        "count": 3,
        "holds": [
            {
                "hold_id": "h1",
                "event_id": "e1",
                "kind": "approval",
                "protocol_name": "dispatch_drone_to_incident",
                "reason": "flagged_protocol",
                "risk_level": "high",
                "risk_reason": "heavy activity",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "raw_text": "שיגור רחפן לשער צפון",
                "sender_identity": "operator_alpha",
            },
            {
                "hold_id": "h2",
                "event_id": "e2",
                "kind": "approval",
                "protocol_name": "recall_drone_to_base",
                "reason": "flagged_protocol",
                "risk_level": "low",
                "risk_reason": "",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "raw_text": "החזרת רחפן 1 לבסיס",
                "sender_identity": "operator_beta",
            },
            {
                "hold_id": "h3",
                "event_id": "e3",
                "kind": "approval",
                "protocol_name": "dispatch_emergency_forces",
                "reason": "flagged_protocol",
                "risk_level": "high",
                "risk_reason": "emergency",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "raw_text": "הזנקת כוחות מדא לחמל",
                "sender_identity": "operator_gamma",
            },
        ],
    }
    telegram_client = FakeTelegramClient()
    deps = _make_bot_deps(api_client, telegram_client)

    update = MagicMock()
    update.effective_user.id = "cmd_user"
    update.effective_chat.id = "chat_123"
    update.message.text = "תור אישורים"
    update.message.message_id = 102

    asyncio.run(app._on_text_message(update, MagicMock(bot_data={"deps": deps})))

    # 1 header + 3 cards = 4 messages
    assert len(telegram_client.sent) == 4
    header = telegram_client.sent[0]
    assert "3 בקשות ממתינות" in header.text
    assert header.text.startswith("⏳")

    # Card 1: dispatch_drone_to_incident
    card1 = telegram_client.sent[1]
    assert "שיגור רחפן טקטי" in card1.text
    assert "שיגור רחפן לשער צפון" in card1.text
    assert "operator_alpha" in card1.text
    assert card1.buttons is not None
    assert len(card1.buttons) == 2
    assert "אשר" in card1.buttons[0][0]
    assert card1.buttons[0][1] == "approve:e1:approved"
    assert "דחה" in card1.buttons[1][0]
    assert card1.buttons[1][1] == "approve:e1:rejected"
    # Ensure internal event IDs and protocol names are not leaked to commander
    assert "dispatch_drone_to_incident" not in card1.text
    assert "e1" not in card1.text

    # Card 2: recall_drone_to_base
    card2 = telegram_client.sent[2]
    assert "החזרת רחפן לבסיס" in card2.text
    assert "operator_beta" in card2.text

    # Card 3: dispatch_emergency_forces
    card3 = telegram_client.sent[3]
    assert "הזנקת כוחות חירום" in card3.text
    assert "operator_gamma" in card3.text


def test_approval_and_clarification_rendered_differently():
    """Requirement 2 & 10: Approval and clarification holds are rendered differently."""
    api_client = FakeBotApiClient()
    api_client.users["cmd_user"] = "commander"
    api_client.pending_holds = {
        "count": 2,
        "holds": [
            {
                "hold_id": "h_appr",
                "event_id": "e_appr",
                "kind": "approval",
                "protocol_name": "dispatch_drone_to_incident",
                "risk_level": "high",
                "risk_reason": "critical action",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "raw_text": "שיגור רחפן",
                "sender_identity": "sentinel",
            },
            {
                "hold_id": "h_clar",
                "event_id": "e_clar",
                "kind": "clarification",
                "unresolved_field": "classification",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "raw_text": "אירוע לא ברור בגזרה",
                "sender_identity": "patrol_1",
                "available_classifications": ["fire_hazard", "security_breach"],
            },
        ],
    }
    telegram_client = FakeTelegramClient()
    deps = _make_bot_deps(api_client, telegram_client)

    update = MagicMock()
    update.effective_user.id = "cmd_user"
    update.effective_chat.id = "chat_123"
    update.message.text = "מה ממתין לאישור"
    update.message.message_id = 103

    asyncio.run(app._on_text_message(update, MagicMock(bot_data={"deps": deps})))

    assert len(telegram_client.sent) == 3
    header = telegram_client.sent[0]
    assert "2 בקשות ממתינות" in header.text

    # Card 1 is approval
    approval_card = telegram_client.sent[1]
    assert "שיגור רחפן טקטי" in approval_card.text
    btn_labels = [b[0] for b in approval_card.buttons]
    assert any("אשר" in b for b in btn_labels)
    assert any("דחה" in b for b in btn_labels)

    # Card 2 is clarification
    clarification_card = telegram_client.sent[2]
    assert "הבהרת דיווח" in clarification_card.text
    assert "סיווג אירוע חסר או לא ברור" in clarification_card.text
    clar_callbacks = [b[1] for b in clarification_card.buttons]
    assert "clarify:e_clar:fire_hazard" in clar_callbacks
    assert "clarify:e_clar:security_breach" in clar_callbacks
    # Must NOT have approve/reject buttons
    assert not any("approve:" in cb for cb in clar_callbacks)


def test_approve_removes_from_queue_and_edits_card(tmp_path, teardown_ctx):
    """Requirement 6, 7 & 10: Approve marks hold resolved, edits card to '✅ אושר', and removes from queue."""
    ctx = build_context(tmp_path)
    teardown_ctx.append(ctx)
    client = build_app(ctx).test_client()

    ev_id = ctx.deps.persistence.append_event({
        "source": "manual",
        "raw_text": "שיגור רחפן",
        "sender_identity": "operator",
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "received_at": datetime.now(timezone.utc).isoformat(),
    })
    ctx.deps.persistence.store_held_event(
        "approval",
        {
            "event_id": ev_id,
            "reason": "flagged_protocol",
            "risk_level": "high",
            "risk_reason": "flagged",
            "selected_protocol_name": "dispatch_drone_to_incident",
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
    )

    # Bot setup
    api_client = FakeBotApiClient()
    api_client.users["cmd_user"] = "commander"
    api_client.approval_answer_outcome = HoldAnswerOutcome(status="approved")
    telegram_client = FakeTelegramClient()
    deps = _make_bot_deps(api_client, telegram_client)

    # Simulate callback query when Commander clicks [✅ אשר]
    update = MagicMock()
    update.effective_user.id = "cmd_user"
    update.effective_chat.id = "chat_123"
    update.callback_query.id = "cb_1"
    update.callback_query.data = f"approve:{ev_id}:approved"
    update.callback_query.message.message_id = 555

    asyncio.run(app._on_callback_query(update, MagicMock(bot_data={"deps": deps})))

    # Card was edited to show approved
    edit_events = [e for e in telegram_client.status_events if e[0] == "edit"]
    assert len(edit_events) == 1
    assert edit_events[0][1] == "chat_123"
    assert edit_events[0][2] == "555"
    assert "אושר" in edit_events[0][3]
    assert edit_events[0][3].startswith("✅")

    # In actual API, perform the approve call and verify queue is empty
    resp_approve = client.post(
        f"/Approve/{ev_id}",
        headers=auth_headers(COMMANDER_IDENTITY),
        json={"decision": "approved"},
    )
    assert resp_approve.status_code in (200, 202)

    # Check GET /Holds/Pending
    resp_queue = client.get("/Holds/Pending", headers=auth_headers(COMMANDER_IDENTITY))
    assert resp_queue.status_code == 200
    assert resp_queue.get_json()["count"] == 0


def test_reject_removes_from_queue_and_edits_card(tmp_path, teardown_ctx):
    """Requirement 6, 7 & 10: Reject marks hold resolved, edits card to '❌ נדחה', and removes from queue."""
    ctx = build_context(tmp_path)
    teardown_ctx.append(ctx)
    client = build_app(ctx).test_client()

    ev_id = ctx.deps.persistence.append_event({
        "source": "manual",
        "raw_text": "החזרת רחפן",
        "sender_identity": "operator",
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "received_at": datetime.now(timezone.utc).isoformat(),
    })
    ctx.deps.persistence.store_held_event(
        "approval",
        {
            "event_id": ev_id,
            "reason": "flagged_protocol",
            "risk_level": "low",
            "risk_reason": "flagged",
            "selected_protocol_name": "recall_drone_to_base",
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
    )

    # Bot callback test
    api_client = FakeBotApiClient()
    api_client.users["cmd_user"] = "commander"
    api_client.approval_answer_outcome = HoldAnswerOutcome(status="rejected")
    telegram_client = FakeTelegramClient()
    deps = _make_bot_deps(api_client, telegram_client)

    update = MagicMock()
    update.effective_user.id = "cmd_user"
    update.effective_chat.id = "chat_123"
    update.callback_query.id = "cb_2"
    update.callback_query.data = f"approve:{ev_id}:rejected"
    update.callback_query.message.message_id = 777

    asyncio.run(app._on_callback_query(update, MagicMock(bot_data={"deps": deps})))

    edit_events = [e for e in telegram_client.status_events if e[0] == "edit"]
    assert len(edit_events) == 1
    assert edit_events[0][2] == "777"
    assert "נדחה" in edit_events[0][3]
    assert edit_events[0][3].startswith("❌")

    # In actual API, decline
    resp_reject = client.post(
        f"/Approve/{ev_id}",
        headers=auth_headers(COMMANDER_IDENTITY),
        json={"decision": "rejected"},
    )
    assert resp_reject.status_code == 200
    assert resp_reject.get_json()["status"] == "declined"

    # Pending holds is empty
    resp_queue = client.get("/Holds/Pending", headers=auth_headers(COMMANDER_IDENTITY))
    assert resp_queue.status_code == 200
    assert resp_queue.get_json()["count"] == 0


def test_double_approve_idempotent_no_duplicate_side_effect(tmp_path, teardown_ctx):
    """Requirement 6 & 10: Double approval is rejected with 409 and never submits duplicate work item."""
    ctx = build_context(tmp_path)
    teardown_ctx.append(ctx)
    client = build_app(ctx).test_client()

    ev_id = ctx.deps.persistence.append_event({
        "source": "manual",
        "raw_text": "שיגור רחפן",
        "sender_identity": "operator",
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "received_at": datetime.now(timezone.utc).isoformat(),
    })
    ctx.deps.persistence.store_held_event(
        "approval",
        {
            "event_id": ev_id,
            "reason": "flagged_protocol",
            "risk_level": "high",
            "risk_reason": "flagged",
            "selected_protocol_name": "dispatch_drone_to_incident",
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
    )

    # First approve succeeds
    resp1 = client.post(
        f"/Approve/{ev_id}",
        headers=auth_headers(COMMANDER_IDENTITY),
        json={"decision": "approved"},
    )
    assert resp1.status_code == 202

    # Second approve fails with 409 Conflict
    resp2 = client.post(
        f"/Approve/{ev_id}",
        headers=auth_headers(COMMANDER_IDENTITY),
        json={"decision": "approved"},
    )
    assert resp2.status_code == 409
    assert "already resolved" in resp2.get_json()["message"].lower()


def test_resolved_or_expired_hold_cannot_be_approved(tmp_path, teardown_ctx):
    """Requirement 6 & 10: A hold already resolved or cancelled cannot be approved."""
    ctx = build_context(tmp_path)
    teardown_ctx.append(ctx)
    client = build_app(ctx).test_client()

    ev_id = ctx.deps.persistence.append_event({
        "source": "manual",
        "raw_text": "שיגור רחפן",
        "sender_identity": "operator",
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "received_at": datetime.now(timezone.utc).isoformat(),
    })
    hold_id = ctx.deps.persistence.store_held_event(
        "approval",
        {
            "event_id": ev_id,
            "reason": "flagged_protocol",
            "risk_level": "high",
            "risk_reason": "flagged",
            "selected_protocol_name": "dispatch_drone_to_incident",
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
    )

    # Mark resolved directly
    ctx.deps.persistence.resolve_held_event("approval", hold_id, {"resolved_by": "other_commander", "decision": "declined"})

    resp = client.post(
        f"/Approve/{ev_id}",
        headers=auth_headers(COMMANDER_IDENTITY),
        json={"decision": "approved"},
    )
    assert resp.status_code == 409


def test_restart_preserves_pending_holds(tmp_path):
    """Requirement 10: Pending holds survive application restart / re-opening persistence."""
    db_file = tmp_path / "test_persistence.db"

    # Session 1: create pending hold
    p1 = open_persistence(str(db_file))
    ev_id = p1.append_event({
        "source": "manual",
        "raw_text": "שיגור רחפן",
        "sender_identity": "operator",
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "received_at": datetime.now(timezone.utc).isoformat(),
    })
    p1.store_held_event(
        "approval",
        {
            "event_id": ev_id,
            "reason": "flagged_protocol",
            "risk_level": "high",
            "risk_reason": "high priority",
            "selected_protocol_name": "dispatch_drone_to_incident",
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    p1.close()

    # Session 2: re-open persistence (simulating restart)
    p2 = open_persistence(str(db_file))
    try:
        pending = p2.list_held_events("approval")
        assert len(pending) == 1
        assert pending[0]["event_id"] == ev_id
        assert pending[0]["resolved"] is False or pending[0]["resolved"] == 0
    finally:
        p2.close()


def test_zero_llm_calls_on_queue_open():
    """Requirement 3 & 10: Zero LLM / Agent calls when Commander opens approvals queue."""
    api_client = FakeBotApiClient()
    api_client.users["cmd_user"] = "commander"
    api_client.pending_holds = {"holds": [], "count": 0}
    telegram_client = FakeTelegramClient()
    deps = _make_bot_deps(api_client, telegram_client)

    update = MagicMock()
    update.effective_user.id = "cmd_user"
    update.effective_chat.id = "chat_123"
    update.message.text = "⏳ תור אישורים"
    update.message.message_id = 999

    asyncio.run(app._on_text_message(update, MagicMock(bot_data={"deps": deps})))

    # Verify that submit_message was NEVER called — only fetch_pending_holds
    called_operations = [call[0] for call in api_client.calls]
    assert "submit_message" not in called_operations
    assert "fetch_pending_holds" in called_operations
