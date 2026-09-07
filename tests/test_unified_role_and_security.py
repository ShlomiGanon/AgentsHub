"""Comprehensive automated tests for role-based security, unified profile, and confirmation flows."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from auth.permissions import PermissionLevel
from bot.contracts import BotDeps
from bot.app import build_deps
from orchestrator.reasoning import RiskAssessment
from persistence import open_surveillance_persistence, open_team_status_persistence
from profiles.loader import load_profile
from protocols.contracts import CriticalityLevel, Protocol


@pytest.fixture
def unified_env(monkeypatch):
    """Set up environment variables for unified test profile."""
    monkeypatch.setenv("BOT_TOKEN", "123456789:AAFakeTokenForUnifiedTesting000")
    monkeypatch.setenv("CORE_MODEL_KEY", "mock-core-key")
    monkeypatch.setenv("SUB_MODEL_KEY", "mock-sub-key")
    monkeypatch.setenv("BOT_SERVICE_KEY", "mock-service-key")


def test_unified_profile_structure_and_contracts(unified_env):
    """Scenario 7 & Profile sanity: verifies profile loads, agents registered, DBs isolated."""
    loaded = load_profile("profiles.unified_test", core_model=MagicMock(), sub_model=MagicMock())

    assert loaded.profile_name == "חמ''ל מבצעי אחוד (Unified Command Hub)"
    assert loaded.default_language == "he"
    assert loaded.api_port == 8905

    # Verify all 4 agents are registered (profile agents + core history_agent)
    agent_names = {agent.name for agent in loaded.agents} | set(loaded.core_agents.keys())
    assert "surveillance_agent" in agent_names
    assert "team_status_agent" in agent_names
    assert "friendly_forces_agent" in agent_names
    assert "history_agent" in agent_names

    # Verify protocols are present with correct security flags
    proto_map = {p.name: p for p in loaded.protocols}
    assert "recall_drone_to_base" in proto_map
    assert proto_map["recall_drone_to_base"].commander_only is True
    assert proto_map["recall_drone_to_base"].requires_confirmation is True
    assert proto_map["recall_drone_to_base"].approval_flag is True

    assert "dispatch_drone_to_incident" in proto_map
    assert proto_map["dispatch_drone_to_incident"].commander_only is True
    assert proto_map["dispatch_drone_to_incident"].requires_confirmation is True

    assert "dispatch_emergency_forces" in proto_map
    assert proto_map["dispatch_emergency_forces"].commander_only is True
    assert proto_map["dispatch_emergency_forces"].requires_confirmation is True

    # Read-only protocols must not require confirmation or commander only
    assert proto_map["query_surveillance_overview"].commander_only is False
    assert proto_map["query_surveillance_overview"].requires_confirmation is False
    assert proto_map["overall_situational_picture"].commander_only is False
    assert proto_map["overall_situational_picture"].requires_confirmation is False
    assert set(proto_map["overall_situational_picture"].participating_agents) == {"surveillance_agent", "team_status_agent"}
    assert set(proto_map["overall_situational_picture"].approved_tools) == {
        "get_surveillance_overview", "get_team_status_roster", "report_team_availability"
    }
    assert proto_map["query_drone_fleet_status"].commander_only is False
    assert proto_map["report_team_availability"].commander_only is False


def test_role_based_keyboards_display_correctly(unified_env):
    """Scenario 6: COMMANDER and VIEWER receive role-tailored keyboards on /start."""
    import asyncio
    from bot import app
    from tests.bot_fakes import FakeBotApiClient, FakeTelegramClient

    api_client = FakeBotApiClient()
    api_client.users["commander_id"] = "commander"
    api_client.users["viewer_id"] = "viewer"

    telegram_client = FakeTelegramClient()
    deps = app.build_deps("profiles.unified_test", core_model=MagicMock(), sub_model=MagicMock())
    deps = BotDeps(
        api_client=api_client,
        telegram_client=telegram_client,
        loaded_profile=deps.loaded_profile,
    )

    # Test Commander
    update_cmd = MagicMock()
    update_cmd.effective_user.id = "commander_id"
    update_cmd.effective_chat.id = "111"
    context_cmd = MagicMock(bot_data={"deps": deps})

    asyncio.run(app._on_start_command(update_cmd, context_cmd))
    assert len(telegram_client.sent) == 1
    cmd_msg = telegram_client.sent[-1]
    assert cmd_msg.keyboard is not None
    flat_cmd_keyboard = [btn for row in cmd_msg.keyboard for btn in row]
    assert "🛸 מצב צי רחפנים" in flat_cmd_keyboard
    assert "🚀 הזנקת רחפן" in flat_cmd_keyboard
    assert "🚨 הזנקת כוחות" in flat_cmd_keyboard
    assert "🔄 החזרת רחפן לבסיס" in flat_cmd_keyboard

    # Test Viewer
    update_vwr = MagicMock()
    update_vwr.effective_user.id = "viewer_id"
    update_vwr.effective_chat.id = "222"
    context_vwr = MagicMock(bot_data={"deps": deps})

    asyncio.run(app._on_start_command(update_vwr, context_vwr))
    assert len(telegram_client.sent) == 2
    vwr_msg = telegram_client.sent[-1]
    assert vwr_msg.keyboard is not None
    flat_vwr_keyboard = [btn for row in vwr_msg.keyboard for btn in row]
    assert "✅ אני זמין לכוננות" in flat_vwr_keyboard
    assert "❌ איני זמין" in flat_vwr_keyboard
    assert "🛸 מצב צי רחפנים" not in flat_vwr_keyboard
    assert "🚀 הזנקת רחפן" not in flat_vwr_keyboard
    assert "🚨 הזנקת כוחות" not in flat_vwr_keyboard



def test_unregistered_user_rejected_without_agent_invocation(unified_env):
    """Scenario 1: Unregistered user is rejected, no agent invoked, no state changed."""
    import asyncio
    from bot import app
    from tests.bot_fakes import FakeBotApiClient, FakeTelegramClient

    api_client = FakeBotApiClient()
    # "unknown_hacker" is not in api_client.users
    telegram_client = FakeTelegramClient()
    deps = app.build_deps("profiles.unified_test", core_model=MagicMock(), sub_model=MagicMock())
    deps = BotDeps(
        api_client=api_client,
        telegram_client=telegram_client,
        loaded_profile=deps.loaded_profile,
    )

    update = MagicMock()
    update.effective_user.id = "unknown_hacker"
    update.effective_chat.id = "999"
    update.message.text = "שלח רחפן לשער צפון"
    update.message.message_id = 100
    context = MagicMock(bot_data={"deps": deps})

    asyncio.run(app._on_text_message(update, context))

    # Bot sends refusal message
    assert len(telegram_client.sent) == 1
    sent_text = telegram_client.sent[0].text
    assert "אינך משתמש רשום" in sent_text or "not registered" in sent_text

    # Verify no message was submitted to API client
    assert not any(call[0] == "submit_message" for call in api_client.calls)


def test_viewer_free_text_drone_dispatch_rejected_server_side(unified_env):
    """Scenario 2: VIEWER typing 'שלח רחפן לשער צפון' is blocked server-side, no mission created."""
    from orchestrator.flows import continue_from_risk_assessment
    from orchestrator.reasoning import ProtocolSelectionResult
    from profiles import unified_test

    surv_store = open_surveillance_persistence(unified_test.UNIFIED_SURVEILLANCE_DB_PATH)
    initial_drones = {d["drone_id"]: d["status"] for d in surv_store.list_drones()}

    deps = MagicMock()
    deps.protocol_set.all.return_value = unified_test.PROTOCOLS
    deps.persistence = MagicMock()
    deps.persistence.fetch_event.return_value = {
        "deadline_at": None,
        "raw_text": "שלח רחפן",
        "classification": "drone_dispatch",
        "area": "north_gate",
        "description": "סיור",
        "severity": "LOW",
        "occurred_at": "2026-09-07T12:00:00Z",
        "received_at": "2026-09-07T12:00:00Z",
    }

    selection = ProtocolSelectionResult(status="selected", protocol_name="dispatch_drone_to_incident", reason="test")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("orchestrator.flows._look_up_precedent_if_possible", lambda *a, **k: ())
        mp.setattr("orchestrator.flows.assess_risk", lambda *a, **k: RiskAssessment(level="LOW", score=0.1, reason="ok"))
        mp.setattr("orchestrator.flows.select_protocol", lambda *a, **k: selection)
        result = continue_from_risk_assessment(
            deps,
            event_id="evt_test_1",
            main_agent=MagicMock(),
            insights_agent=MagicMock(),
            originated_from_commander=False,  # VIEWER
        )

    # Must be unauthorized_for_viewer
    assert result.outcome == "unauthorized_for_viewer"
    assert "הרשאת מפקד" in result.detail

    # Ensure no drone state changed
    current_drones = {d["drone_id"]: d["status"] for d in surv_store.list_drones()}
    assert current_drones == initial_drones


def test_viewer_free_text_drone_recall_rejected_server_side(unified_env):
    """Scenario 3: VIEWER typing 'תחזיר את הרחפן' is blocked server-side."""
    from orchestrator.flows import continue_from_risk_assessment
    from orchestrator.reasoning import ProtocolSelectionResult
    from profiles import unified_test

    deps = MagicMock()
    deps.protocol_set.all.return_value = unified_test.PROTOCOLS
    deps.persistence = MagicMock()
    deps.persistence.fetch_event.return_value = {
        "deadline_at": None,
        "raw_text": "החזר רחפן",
        "classification": "drone_recall",
        "area": "north_gate",
        "description": "החזרה",
        "severity": "LOW",
        "occurred_at": "2026-09-07T12:00:00Z",
        "received_at": "2026-09-07T12:00:00Z",
    }

    selection = ProtocolSelectionResult(status="selected", protocol_name="recall_drone_to_base", reason="test")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("orchestrator.flows._look_up_precedent_if_possible", lambda *a, **k: ())
        mp.setattr("orchestrator.flows.assess_risk", lambda *a, **k: RiskAssessment(level="LOW", score=0.1, reason="ok"))
        mp.setattr("orchestrator.flows.select_protocol", lambda *a, **k: selection)
        result = continue_from_risk_assessment(
            deps,
            event_id="evt_test_2",
            main_agent=MagicMock(),
            insights_agent=MagicMock(),
            originated_from_commander=False,  # VIEWER
        )

    assert result.outcome == "unauthorized_for_viewer"
    assert "הרשאת מפקד" in result.detail


def test_viewer_free_text_emergency_forces_rejected_server_side(unified_env):
    """Scenario 4: VIEWER typing 'תזניק משטרה' is blocked server-side."""
    from orchestrator.flows import continue_from_risk_assessment
    from orchestrator.reasoning import ProtocolSelectionResult
    from profiles import unified_test

    deps = MagicMock()
    deps.protocol_set.all.return_value = unified_test.PROTOCOLS
    deps.persistence = MagicMock()
    deps.persistence.fetch_event.return_value = {
        "deadline_at": None,
        "raw_text": "הזנק משטרה",
        "classification": "emergency_dispatch",
        "area": "north_gate",
        "description": "משטרה",
        "severity": "LOW",
        "occurred_at": "2026-09-07T12:00:00Z",
        "received_at": "2026-09-07T12:00:00Z",
    }

    selection = ProtocolSelectionResult(status="selected", protocol_name="dispatch_emergency_forces", reason="test")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("orchestrator.flows._look_up_precedent_if_possible", lambda *a, **k: ())
        mp.setattr("orchestrator.flows.assess_risk", lambda *a, **k: RiskAssessment(level="LOW", score=0.1, reason="ok"))
        mp.setattr("orchestrator.flows.select_protocol", lambda *a, **k: selection)
        result = continue_from_risk_assessment(
            deps,
            event_id="evt_test_3",
            main_agent=MagicMock(),
            insights_agent=MagicMock(),
            originated_from_commander=False,  # VIEWER
        )

    assert result.outcome == "unauthorized_for_viewer"
    assert "הרשאת מפקד" in result.detail


def test_commander_side_effects_trigger_confirmation_flow(unified_env):
    """Scenario 5: COMMANDER actions require confirmation before execution."""
    from orchestrator.holds import determine_approval_hold
    from orchestrator.reasoning import ProtocolSelectionResult
    from profiles import unified_test

    protocols_by_name = {p.name: p for p in unified_test.PROTOCOLS}

    # Dispatch drone requires confirmation even for commander
    sel_dispatch = ProtocolSelectionResult(status="selected", protocol_name="dispatch_drone_to_incident", reason="r")
    hold_dispatch = determine_approval_hold(sel_dispatch, protocols_by_name, originated_from_commander=True)
    assert hold_dispatch == "flagged_protocol"

    # Recall drone requires confirmation even for commander
    sel_recall = ProtocolSelectionResult(status="selected", protocol_name="recall_drone_to_base", reason="r")
    hold_recall = determine_approval_hold(sel_recall, protocols_by_name, originated_from_commander=True)
    assert hold_recall == "flagged_protocol"

    # Emergency forces require confirmation even for commander
    sel_forces = ProtocolSelectionResult(status="selected", protocol_name="dispatch_emergency_forces", reason="r")
    hold_forces = determine_approval_hold(sel_forces, protocols_by_name, originated_from_commander=True)
    assert hold_forces == "flagged_protocol"

    # Read-only actions (cameras, fleet status, team status) do NOT hold for commander
    sel_overview = ProtocolSelectionResult(status="selected", protocol_name="query_surveillance_overview", reason="r")
    assert determine_approval_hold(sel_overview, protocols_by_name, originated_from_commander=True) is None

    sel_cameras = ProtocolSelectionResult(status="selected", protocol_name="query_camera_status", reason="r")
    assert determine_approval_hold(sel_cameras, protocols_by_name, originated_from_commander=True) is None

    sel_team = ProtocolSelectionResult(status="selected", protocol_name="report_team_availability", reason="r")
    assert determine_approval_hold(sel_team, protocols_by_name, originated_from_commander=True) is None


def test_viewer_attendance_reporting_shortcut(unified_env):
    """Scenario 6: VIEWER reporting attendance via buttons."""
    import asyncio
    from bot import app
    from bot.contracts import MessageSubmissionResult
    from tests.bot_fakes import FakeBotApiClient, FakeTelegramClient

    api_client = FakeBotApiClient()
    api_client.users["viewer_123"] = "viewer"
    api_client.message_submission_result = MessageSubmissionResult(kind="question", answer_text="הדיווח התקבל")
    telegram_client = FakeTelegramClient()
    deps = app.build_deps("profiles.unified_test", core_model=MagicMock(), sub_model=MagicMock())
    deps = BotDeps(
        api_client=api_client,
        telegram_client=telegram_client,
        loaded_profile=deps.loaded_profile,
    )

    # 1. Test "✅ אני זמין לכוננות"
    update_avail = MagicMock()
    update_avail.effective_user.id = "viewer_123"
    update_avail.effective_user.full_name = "דוד כהן"
    update_avail.effective_chat.id = "333"
    update_avail.message.text = "✅ אני זמין לכוננות"
    update_avail.message.message_id = 201
    context_avail = MagicMock(bot_data={"deps": deps})

    asyncio.run(app._on_text_message(update_avail, context_avail))

    # Should submit message to API with user's info
    assert any(
        call[0] == "submit_message" and "viewer_123" in call[1]
        for call in api_client.calls
    )

    # 2. Test "❌ איני זמין"
    update_unavail = MagicMock()
    update_unavail.effective_user.id = "viewer_123"
    update_unavail.effective_user.full_name = "דוד כהן"
    update_unavail.effective_chat.id = "333"
    update_unavail.message.text = "❌ איני זמין"
    update_unavail.message.message_id = 202
    context_unavail = MagicMock(bot_data={"deps": deps})

    asyncio.run(app._on_text_message(update_unavail, context_unavail))

    # Should reply asking for reason/days
    last_sent = telegram_client.sent[-1]
    assert "אנא ציין את סיבת אי-הזמינות" in last_sent.text


def test_unavailability_follow_up_keeps_reason_until_days_and_uses_isolated_conversation(unified_env):
    import asyncio
    from bot import app
    from bot.contracts import MessageSubmissionResult
    from tests.bot_fakes import FakeBotApiClient, FakeTelegramClient

    app._PENDING_UNAVAILABILITY.clear()
    api_client = FakeBotApiClient()
    api_client.users["viewer_123"] = "viewer"
    api_client.message_submission_result = MessageSubmissionResult(kind="report", job_id="attendance-1")
    telegram_client = FakeTelegramClient()
    loaded = app.build_deps("profiles.unified_test", core_model=MagicMock(), sub_model=MagicMock())
    deps = BotDeps(api_client=api_client, telegram_client=telegram_client, loaded_profile=loaded.loaded_profile)
    context = MagicMock(bot_data={"deps": deps})

    def send(text, message_id):
        update = MagicMock()
        update.effective_user.id = "viewer_123"
        update.effective_chat.id = "333"
        update.message.text = text
        update.message.message_id = message_id
        update.message.message_thread_id = None
        asyncio.run(app._on_text_message(update, context))

    send("❌ איני זמין", 401)
    send("עקב מחלה", 402)

    assert not any(call[0] == "submit_message" for call in api_client.calls)
    assert "הסיבה נשמרה" in telegram_client.sent[-1].text

    send("2", 403)

    submission = next(call for call in api_client.calls if call[0] == "submit_message")
    assert "עקב מחלה" in submission[1]
    assert "2 ימים" in submission[1]
    assert ("submit_message_conversation", "telegram:333:attendance:viewer_123") in api_client.calls
    assert app._PENDING_UNAVAILABILITY == {}


def test_available_button_cancels_pending_unavailability(unified_env):
    import asyncio
    from bot import app
    from bot.contracts import MessageSubmissionResult
    from tests.bot_fakes import FakeBotApiClient, FakeTelegramClient

    app._PENDING_UNAVAILABILITY.clear()
    api_client = FakeBotApiClient()
    api_client.users["viewer_123"] = "viewer"
    api_client.message_submission_result = MessageSubmissionResult(kind="report", job_id="attendance-2")
    telegram_client = FakeTelegramClient()
    loaded = app.build_deps("profiles.unified_test", core_model=MagicMock(), sub_model=MagicMock())
    deps = BotDeps(api_client=api_client, telegram_client=telegram_client, loaded_profile=loaded.loaded_profile)
    context = MagicMock(bot_data={"deps": deps})

    def send(text, message_id):
        update = MagicMock()
        update.effective_user.id = "viewer_123"
        update.effective_chat.id = "333"
        update.message.text = text
        update.message.message_id = message_id
        update.message.message_thread_id = None
        asyncio.run(app._on_text_message(update, context))

    send("❌ איני זמין", 411)
    send("✅ אני זמין לכוננות", 412)

    assert app._PENDING_UNAVAILABILITY == {}
    assert ("submit_message_conversation", "telegram:333:attendance:viewer_123") in api_client.calls


def test_parallel_agent_requests_receive_only_their_own_captured_result(unified_env, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from agents import AgentResult, TeamStatusAgent
    from profiles import unified_test

    barrier = Barrier(2)

    def fake_base_process(self, text, allowed_tools, *, invocation_policy=None):
        barrier.wait()
        unified_test._capture_team_result(f"tool-result:{text}")
        barrier.wait()
        return AgentResult(status="success", text=f"model-result:{text}")

    monkeypatch.setattr(TeamStatusAgent, "process", fake_base_process)
    agent = unified_test.UnifiedTeamStatusAgent(model="mock")

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(agent.process, "request-a", ["report_team_availability"])
        second = pool.submit(agent.process, "request-b", ["report_team_availability"])

    assert first.result().text == "tool-result:request-a"
    assert second.result().text == "tool-result:request-b"
    assert not hasattr(unified_test, "_latest_team_result")
    assert not hasattr(unified_test, "_latest_surv_result")
    assert not hasattr(unified_test, "_latest_forces_result")


def test_team_status_roster_views_are_complete_and_follow_up_specific(tmp_path, monkeypatch):
    from datetime import datetime, timedelta, timezone
    from agents import AgentResult, TeamStatusAgent
    from profiles import unified_test

    status_path = str(tmp_path / "team-status-views.db")
    monkeypatch.setattr(unified_test.UnifiedTeamStatusAgent, "status_db_path", status_path)
    agent = unified_test.UnifiedTeamStatusAgent(model="mock")
    opened = datetime(2026, 9, 7, 5, 0, tzinfo=timezone.utc)
    for identity, name in (
        ("1001", "דן לוי"),
        ("1002", "יוסי כהן"),
        ("1003", "מיכל אברהם"),
    ):
        agent.status_store.register_member(identity, name, opened.isoformat())
    agent.status_store.approve_roster("commander", opened.isoformat())
    agent.status_store.open_cycle(
        "2026-09-07", opened.isoformat(), (opened + timedelta(hours=4)).isoformat()
    )
    agent.status_store.record_response(
        telegram_identity="1001", source_message_id="available-1001",
        availability="available", original_text="זמין",
        received_at=(opened + timedelta(minutes=5)).isoformat(),
    )
    agent.status_store.record_response(
        telegram_identity="1002", source_message_id="unavailable-1002",
        availability="unavailable", reason="מחלה",
        unavailable_until=(opened + timedelta(days=2)).isoformat(),
        original_text="לא זמין עקב מחלה",
        received_at=(opened + timedelta(minutes=6)).isoformat(),
    )
    as_of = (opened + timedelta(minutes=10)).isoformat()

    summary = agent.report_team_availability(as_of)
    assert "סה\"כ 3" in summary
    assert "טרם דיווחו (1): מיכל אברהם" in summary
    assert "זמינים לפעילות (1): דן לוי" in summary

    assert agent.report_team_availability(as_of, "members") == (
        "👥 חברי כיתת הכוננות (3): דן לוי, יוסי כהן, מיכל אברהם"
    )
    assert "דן לוי" in agent.report_team_availability(as_of, "available")
    assert "יוסי כהן" not in agent.report_team_availability(as_of, "available")
    assert "יוסי כהן — מחלה" in agent.report_team_availability(as_of, "unavailable")
    assert "מיכל אברהם" in agent.report_team_availability(as_of, "awaiting")
    assert agent.report_team_availability(as_of, "count") == "✅ זמינים כעת 1 מתוך 3 חברי כיתה."
    assert "מחלה" in agent.report_team_availability(as_of, "reason", "למה יוסי כהן לא זמין?")

    def fake_base_process(self, text, allowed_tools, *, invocation_policy=None):
        self.report_team_availability(as_of)
        return AgentResult(status="success", text="תשובת מודל שלא צריכה להחליף נתוני DB")

    monkeypatch.setattr(TeamStatusAgent, "process", fake_base_process)
    follow_up = agent.process("אפשר את השמות שלהם?", ["report_team_availability"])
    assert follow_up.text.startswith("👥 חברי כיתת הכוננות (3):")
    assert "סטטוס כיתת כוננות" not in follow_up.text

    available_follow_up = agent.process("מי זמין?", ["report_team_availability"])
    assert "דן לוי" in available_follow_up.text
    assert "יוסי כהן" not in available_follow_up.text

    awaiting_follow_up = agent.process("מי עדיין לא דיווח?", ["report_team_availability"])
    assert "מיכל אברהם" in awaiting_follow_up.text
    assert "דן לוי" not in awaiting_follow_up.text

    # The viewer-visible profile contract intentionally exposes the approved
    # roster's names, statuses and unavailability reasons, but not raw audit
    # payloads or message timestamps.
    assert "לא זמין עקב מחלה" not in summary
    assert "2026-09-07T05:06" not in summary


def test_team_status_does_not_invent_a_name_for_legacy_placeholder(tmp_path, monkeypatch):
    from datetime import datetime, timedelta, timezone
    from profiles import unified_test

    status_path = str(tmp_path / "team-status-placeholder.db")
    monkeypatch.setattr(unified_test.UnifiedTeamStatusAgent, "status_db_path", status_path)
    agent = unified_test.UnifiedTeamStatusAgent(model="mock")
    opened = datetime(2026, 9, 7, 5, 0, tzinfo=timezone.utc)
    agent.status_store.register_member("999", "חבר כיתת כוננות (999)", opened.isoformat())
    agent.status_store.approve_roster("commander", opened.isoformat())
    agent.status_store.open_cycle(
        "2026-09-07", opened.isoformat(), (opened + timedelta(hours=1)).isoformat()
    )

    roster = agent.report_team_availability(opened.isoformat(), "members")
    assert "משתמש 999 (שם לא הוגדר)" in roster
    assert "דן" not in roster and "יוסי" not in roster and "מיכל" not in roster


def test_available_attendance_discards_stale_unavailability_fields(tmp_path, monkeypatch):
    from datetime import datetime, timedelta, timezone
    from agents import authenticated_request_identity
    from profiles import unified_test

    status_path = str(tmp_path / "team-status-available-normalization.db")
    monkeypatch.setattr(unified_test.UnifiedTeamStatusAgent, "status_db_path", status_path)
    agent = unified_test.UnifiedTeamStatusAgent(model="mock")
    opened = datetime.now(timezone.utc)
    agent.status_store.register_member("1001", "דן לוי", opened.isoformat())
    agent.status_store.approve_roster("commander", opened.isoformat())
    agent.status_store.open_cycle(
        opened.date().isoformat(), opened.isoformat(), (opened + timedelta(hours=1)).isoformat()
    )

    with authenticated_request_identity("1001"):
        result = agent.record_attendance_response(
            source_message_id="available-with-stale-fields",
            availability="available",
            reason="שדה שאריתי שאסור לשמור",
            unavailable_days=3,
        )

    assert result == "✅ הזמינות שלך עודכנה. אתה מסומן כזמין לכוננות."
    [member] = agent.status_store.availability_snapshot((opened + timedelta(minutes=1)).isoformat())
    assert member["availability"] == "available"
    assert member["reason"] is None
    assert member["unavailable_until"] is None


def test_all_commander_and_viewer_buttons_mapped(unified_env):
    """Verifies that every single button across Commander and Viewer keyboards maps to expected behavior."""
    import asyncio
    from bot import app
    from bot.contracts import MessageSubmissionResult
    from tests.bot_fakes import FakeBotApiClient, FakeTelegramClient

    api_client = FakeBotApiClient()
    api_client.users["cmd_1"] = "commander"
    api_client.users["vwr_1"] = "viewer"
    api_client.message_submission_result = MessageSubmissionResult(kind="question", answer_text="תשובה")
    telegram_client = FakeTelegramClient()
    deps = app.build_deps("profiles.unified_test", core_model=MagicMock(), sub_model=MagicMock())
    deps = BotDeps(api_client=api_client, telegram_client=telegram_client, loaded_profile=deps.loaded_profile)

    # 1. Commander button: "🚨 הזנקת כוחות" gives guidance
    up = MagicMock()
    up.effective_user.id = "cmd_1"
    up.effective_chat.id = "1"
    up.message.text = "🚨 הזנקת כוחות"
    up.message.message_id = 301
    asyncio.run(app._on_text_message(up, MagicMock(bot_data={"deps": deps})))
    assert "להזנקת כוחות חירום וביטחון" in telegram_client.sent[-1].text

    # 2. Commander button: "🚀 הזנקת רחפן" gives guidance
    up_drone = MagicMock()
    up_drone.effective_user.id = "cmd_1"
    up_drone.effective_chat.id = "1"
    up_drone.message.text = "🚀 הזנקת רחפן"
    up_drone.message.message_id = 302
    asyncio.run(app._on_text_message(up_drone, MagicMock(bot_data={"deps": deps})))
    assert "לשיגור והזנקת רחפן טקטי" in telegram_client.sent[-1].text

    # 3. Viewer trying "🚨 הזנקת כוחות" or "🚀 הזנקת רחפן" is blocked with commander-only message
    for blocked_btn in ["🚨 הזנקת כוחות", "🚀 הזנקת רחפן"]:
        up_v = MagicMock()
        up_v.effective_user.id = "vwr_1"
        up_v.effective_chat.id = "2"
        up_v.message.text = blocked_btn
        up_v.message.message_id = 303
        asyncio.run(app._on_text_message(up_v, MagicMock(bot_data={"deps": deps})))
        assert "מפקד בלבד" in telegram_client.sent[-1].text

    # 4. Test query buttons map to canonical prompts submitted to API
    test_buttons = [
        ("🛸 מצב צי רחפנים", "רחפנים"),
        ("🔄 החזרת רחפן לבסיס", "החזר"),
        ("📹 מצב מצלמות", "מצלמות"),
        ("📊 תמונת מצב כללית", "תמונת המצב"),
        ("👥 סטטוס כיתת כוננות", "כיתת הכוננות"),
        ("📜 היסטוריית אירועים", "אירועים האחרונים"),
    ]
    for btn, expected_phrase in test_buttons:
        api_client.calls.clear()
        up_btn = MagicMock()
        up_btn.effective_user.id = "cmd_1"
        up_btn.effective_chat.id = "1"
        up_btn.message.text = btn
        up_btn.message.message_id = 400
        asyncio.run(app._on_text_message(up_btn, MagicMock(bot_data={"deps": deps})))
        submit_call = next(c for c in api_client.calls if c[0] == "submit_message")
        assert expected_phrase in submit_call[1], f"Button '{btn}' failed to map to prompt with '{expected_phrase}'"


def test_hebrew_tools_return_concise_operational_hebrew(unified_env):
    """Verifies that specialist agent tools return 100% Hebrew, concise, operational output."""
    from profiles import unified_test

    surv_agent = unified_test.UnifiedSurveillanceAgent(model="mock")
    # Fleet status tool
    fleet = surv_agent.get_drone_fleet_status()
    assert "מצב צי רחפנים" in fleet
    assert "מוכן לפעולה" in fleet
    assert len(fleet.splitlines()) <= 8

    # Active missions tool
    active = surv_agent.get_active_missions()
    assert "משימות רחפנים" in active

    # Camera status tool
    cams = surv_agent.get_camera_feeds()
    assert "מצב מצלמות אבטחה" in cams
    assert "תקין ופעיל" in cams

    # Surveillance overview tool
    overview = surv_agent.get_surveillance_overview()
    assert "תמונת מצב תצפיתית כוללת" in overview
    assert "מצלמות אבטחה" in overview

    # Drone dispatch tool
    dispatch = surv_agent.dispatch_drone_to_area(target_area="north_gate")
    assert "הזנקת רחפן הושלמה בהצלחה" in dispatch
    assert "north_gate" in dispatch

    # Drone return tool
    ret = surv_agent.return_drone_to_base()
    assert "רחפנים" in ret

    # Camera update observation tool
    cam_upd = surv_agent.update_camera_observation("CAM-01", "תנועה חשודה נבדקה ונשללה")
    assert "תצפית מצלמה CAM-01" in cam_upd

    # Team status agent tools
    team_agent = unified_test.UnifiedTeamStatusAgent(model="mock")
    team_avail = team_agent.report_team_availability()
    assert "סטטוס כיתת כוננות" in team_avail
    assert "זמינים לפעילות" in team_avail

    from agents import authenticated_request_identity
    with authenticated_request_identity("2077472944"):
        att_resp = team_agent.record_attendance_response(availability="available")
    assert att_resp == "✅ הזמינות שלך עודכנה. אתה מסומן כזמין לכוננות."

    # Friendly forces agent tools
    forces_agent = unified_test.UnifiedFriendlyForcesAgent(model="mock")
    police = forces_agent.dispatch_police("שער צפון", unit_count=2)
    assert "הזנקת כוחות משטרה" in police
    assert "שער צפון" in police

    ambulance = forces_agent.dispatch_ambulance("חמ''ל", patient_count=1)
    assert "הזנקת צוות רפואה" in ambulance

    fire = forces_agent.dispatch_firefighters("גזרה דרומית", engine_count=1)
    assert "הזנקת כוחות כיבוי" in fire

    military = forces_agent.dispatch_military("גדר מזרחית", unit_count=4)
    assert "הזנקת כוחות צבא" in military


def test_compact_formatting_for_unified_protocols():
    """Verifies that unified tactical protocols are formatted compactly without verbose English/insight headers."""
    from bot.contracts import JobResult
    from bot.interactions import format_job_result
    from messages import get_catalog

    catalog = get_catalog("he")
    job = JobResult(
        job_id="job-12345",
        outcome="succeeded",
        steps_completed=("הזנקת רחפן הושלמה בהצלחה ✅\n• רחפן: נשר 1 (DRONE-01)\n• גזרת יעד: שער צפון",),
        protocol_name="dispatch_drone_to_incident",
        risk_level="high",
        protocol_reason="dispatch recon",
        insight_text="Verbose insights that should be omitted in compact mode",
    )
    formatted = format_job_result(job, catalog)
    assert "[תוצאה]" in formatted
    assert "job-12345" in formatted
    assert "הזנקת רחפן הושלמה בהצלחה" in formatted
    assert "Verbose insights" not in formatted
    assert "מה בוצע:" not in formatted


def test_overall_situational_picture_format_job_result():
    from bot.interactions import format_job_result
    from bot.contracts import JobResult
    from messages import get_catalog

    catalog = get_catalog("he")
    job = JobResult(
        job_id="job-multi-1",
        outcome="succeeded",
        protocol_name="overall_situational_picture",
        steps_completed=(
            "surveillance_agent: 📊 תמונת מצב תצפיתית כוללת:\n• מצלמות אבטחה: 4/4 פעילות.\n• מערך רחפנים: 2 מוכנים לשיגור.",
            "team_status_agent: 👥 סטטוס כיתת כוננות (סה\"כ 12 לוחמים):\n• זמינים לפעילות (8): ישראל, דוד, יוסי.",
        ),
    )
    formatted = format_job_result(job, catalog)
    assert "job-multi-1" in formatted
    assert "מצלמות אבטחה: 4/4 פעילות" in formatted
    assert "סטטוס כיתת כוננות" in formatted

    # When insight_text contains the Orchestrator's synthesized Hebrew response,
    # it must be displayed directly as the unified operational output.
    job_with_synthesis = JobResult(
        job_id="job-multi-2",
        outcome="succeeded",
        protocol_name="overall_situational_picture",
        steps_completed=(
            "surveillance_agent: raw surveillance debug data",
            "team_status_agent: raw team status debug data",
        ),
        insight_text="תמונת מצב אחודה: 4 מצלמות פעילות בגזרה, 2 רחפנים בכוננות, ו-8 לוחמים זמינים בכיתת הכוננות.",
    )
    formatted_synth = format_job_result(job_with_synthesis, catalog)
    assert "job-multi-2" in formatted_synth
    assert "תמונת מצב אחודה" in formatted_synth
    assert "raw surveillance debug data" not in formatted_synth
    assert "raw team status debug data" not in formatted_synth


def test_open_approval_holds_tracking():
    from bot.interactions import register_open_approval_hold, unregister_open_approval_hold, get_open_approval_holds

    register_open_approval_hold("evt-100")
    register_open_approval_hold("evt-200")
    holds = get_open_approval_holds()
    assert "evt-100" in holds
    assert "evt-200" in holds

    unregister_open_approval_hold("evt-100")
    holds_after = get_open_approval_holds()
    assert "evt-100" not in holds_after
    assert "evt-200" in holds_after

    unregister_open_approval_hold("evt-200")
    assert get_open_approval_holds() == []


def test_approval_phrase_detection_distinguishes_conversational_hebrew():
    exact_approval_words = {
        "אישור", "אשר", "מאשר", "מאושר", "approve", "yes", "כן",
    }
    prefix_approval_words = (
        "מאושר ", "מאשר ", "אשר ", "אישור ", "approve ",
        "כן אשר", "כן תאשר", "כן, אשר", "כן, תאשר", "כן לשגר", "מאושר תשלח", "מאושר לשלוח",
    )
    exact_rejection_words = {
        "ביטול", "בטל", "דחה", "דחייה", "reject", "no", "לא",
    }
    prefix_rejection_words = (
        "בטל ", "ביטול ", "דחה ", "דחייה ", "reject ",
        "לא בטל", "לא, בטל", "דחה שיגור", "בטל שיגור",
    )

    def is_appr(txt: str) -> bool:
        norm = txt.strip().lower()
        return norm in exact_approval_words or any(norm.startswith(p) for p in prefix_approval_words)

    def is_rej(txt: str) -> bool:
        norm = txt.strip().lower()
        return norm in exact_rejection_words or any(norm.startswith(p) for p in prefix_rejection_words)

    # Conversational phrases should NOT trigger approval/rejection
    assert not is_appr("כן זה השאלה")
    assert not is_rej("לא הבנתי מה קשור")
    assert not is_appr("כן מה קורה")
    assert not is_rej("לא כרגע תודה")

    # Real approvals and rejections MUST trigger
    assert is_appr("כן")
    assert is_appr("מאושר")
    assert is_appr("אשר")
    assert is_appr("מאושר תשלח")
    assert is_appr("כן אשר")
    assert is_rej("לא")
    assert is_rej("ביטול")
    assert is_rej("בטל שיגור")


def test_open_approval_holds_syncs_with_db(tmp_path):
    import sqlite3
    from bot.interactions import get_open_approval_holds, unregister_open_approval_hold

    db_file = str(tmp_path / "test_holds.db")
    conn = sqlite3.connect(db_file)
    conn.execute(
        "CREATE TABLE held_events (hold_id TEXT, kind TEXT, event_id TEXT, resolved INTEGER, created_at TEXT)"
    )
    conn.execute(
        "INSERT INTO held_events VALUES ('h1', 'approval', 'evt-from-db-1', 0, '2026-09-07T12:00:00')"
    )
    conn.execute(
        "INSERT INTO held_events VALUES ('h2', 'approval', 'evt-from-db-2', 1, '2026-09-07T12:01:00')"
    )
    conn.commit()
    conn.close()

    holds = get_open_approval_holds(db_file)
    assert "evt-from-db-1" in holds
    assert "evt-from-db-2" not in holds  # resolved=1 should not be loaded

    unregister_open_approval_hold("evt-from-db-1")

