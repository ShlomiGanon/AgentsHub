"""Tests for Phase 1 Attendance & Team Status Gate Requirements.

Verifies:
1. Pre-approved simulation personas are approved (approved=1) idempotently.
2. Unavailability report with reason ("אני במילואים...") persists as accepted with reason and unavailable_until.
3. Unavailability report with reason ("קמתי עם חום גבוה...") persists as accepted with reason and unavailable_until.
4. Missing reason ("אני לא זמין היום") triggers clarification ("מה הסיבה לאי-הזמינות?") rather than failure.
5. Clarification answer resolves hold and updates DB with reason and unavailable_until.
6. Identity is strictly derived from authenticated context.
7. Real business failures and clarifications are captured as such.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import pytest

from agents import authenticated_request_identity, request_message_context
from messages.catalog import get_catalog
from persistence import open_team_status_persistence
from profiles.unified_test import (
    UnifiedTeamStatusAgent,
    UNIFIED_TEAM_STATUS_DB_PATH,
    SIMULATION_USERS,
)


def _setup_agent(tmp_path: Path):
    db_path = str(tmp_path / "test_team_status.db")
    store = open_team_status_persistence(db_path)
    now = datetime(2026, 9, 15, 8, 0, tzinfo=timezone.utc)

    # Register team members
    store.register_member("9000000000000002", "אלי - כיתת כוננות", now.isoformat())
    store.register_member("9000000000000005", "דני - כיתת כוננות", now.isoformat())
    store.register_member("9000000000000007", "מיכאל - כיתת כוננות", now.isoformat())
    store.approve_roster("commander-1", now.isoformat())

    agent = UnifiedTeamStatusAgent(model="mock-model")
    agent.status_store = store
    return agent, store, now


from types import SimpleNamespace
from profiles.simulation import SimulationPersona, SimulationRoster, simulation_user_telegram_id
from profiles.simulation_provisioning import _ensure_roster_memberships


def test_pre_approved_simulation_personas_idempotent_approval(tmp_path: Path):
    """1.1 Gate: personas declared with pre_approved_rosters=('team_status',) get approved=1."""
    db_path = str(tmp_path / "team_status_roster.db")
    store = open_team_status_persistence(db_path)
    now = datetime(2026, 9, 15, 8, 0, tzinfo=timezone.utc).isoformat()

    # Roster is approved before simulation persona is added
    store.register_member("1001", "Existing Member", now)
    store.approve_roster("commander-1", now)

    personas = [
        SimulationPersona(key="eli", offset=2, permission_level="viewer", full_name="אלי", pre_approved_rosters=("team_status",)),
        SimulationPersona(key="yossi", offset=3, permission_level="viewer", full_name="יוסי"), # not pre-approved
    ]
    fake_profile = SimpleNamespace(
        simulation_users=personas,
        simulation_rosters=[
            SimulationRoster(key="team_status", open=lambda p: store, db_path=db_path, approved_by="commander-1"),
        ],
    )
    _ensure_roster_memberships(fake_profile)

    approved = {m["telegram_identity"]: m["approved"] for m in store.list_members(approved_only=False)}
    eli_id = simulation_user_telegram_id(2)
    assert approved.get(eli_id) == 1, "Pre-approved persona must have approved=1"
    assert "1001" in approved and approved["1001"] == 1

    # Idempotence: run again
    _ensure_roster_memberships(fake_profile)
    approved_again = {m["telegram_identity"]: m["approved"] for m in store.list_members(approved_only=False)}
    assert approved_again.get(eli_id) == 1


def test_miluim_report_persists_as_accepted(tmp_path: Path):
    """1.3 Gate: 'אני במילואים מראשון עד שלישי בערב...' persists as accepted with reason and unavailable_until."""
    agent, store, now = _setup_agent(tmp_path)
    eli_id = "9000000000000002"
    raw_text = "בוקר טוב, מעדכן שאני במילואים מראשון עד שלישי בערב, לא זמין ביישוב."

    with authenticated_request_identity(eli_id), request_message_context(
        source_message_id="msg-eli-1",
        received_at=now.isoformat(),
        raw_text=raw_text,
    ):
        result = agent.record_attendance_response(
            availability="unavailable",
            reason="שירות מילואים מראשון עד שלישי בערב",
            unavailable_days=3,
        )

    assert "✅" not in result
    assert "❌" in result or "נרשם" in result

    # Check persistence
    snapshot = store.availability_snapshot(now.isoformat())
    eli_entry = next((e for e in snapshot if e["telegram_identity"] == eli_id), None)
    assert eli_entry is not None
    assert eli_entry["availability"] == "unavailable"
    assert "מילואים" in (eli_entry["reason"] or "")
    assert eli_entry["unavailable_until"] is not None

    with store._connect() as conn:
        row = conn.execute("SELECT approval_status FROM attendance_responses WHERE telegram_identity = ?", (eli_id,)).fetchone()
        assert row["approval_status"] == "accepted"


def test_high_fever_report_persists_as_accepted(tmp_path: Path):
    """1.3 Gate: 'קמתי עם חום גבוה, לא אוכל להשתתף בסיור הערב' persists as accepted with reason and unavailable_until."""
    agent, store, now = _setup_agent(tmp_path)
    michael_id = "9000000000000007"
    raw_text = "קמתי עם חום גבוה, לא אוכל להשתתף בסיור הערב."

    with authenticated_request_identity(michael_id), request_message_context(
        source_message_id="msg-michael-1",
        received_at=now.isoformat(),
        raw_text=raw_text,
    ):
        result = agent.record_attendance_response(
            availability="unavailable",
            reason="חום גבוה / מחלה",
            unavailable_days=1,
        )

    # Check persistence
    snapshot = store.availability_snapshot(now.isoformat())
    michael_entry = next((e for e in snapshot if e["telegram_identity"] == michael_id), None)
    assert michael_entry is not None
    assert michael_entry["availability"] == "unavailable"
    assert "חום גבוה" in (michael_entry["reason"] or "")
    assert michael_entry["unavailable_until"] is not None
    with store._connect() as conn:
        row = conn.execute("SELECT approval_status FROM attendance_responses WHERE telegram_identity = ?", (michael_id,)).fetchone()
        assert row["approval_status"] == "accepted"


def test_missing_reason_triggers_clarification_not_failure(tmp_path: Path):
    """1.3 Gate: 'אני לא זמין היום' without reason returns clarification question."""
    agent, store, now = _setup_agent(tmp_path)
    michael_id = "9000000000000007"
    raw_text = "אני לא זמין היום"

    with authenticated_request_identity(michael_id), request_message_context(
        source_message_id="msg-michael-2",
        received_at=now.isoformat(),
        raw_text=raw_text,
    ):
        result = agent.record_attendance_response(
            availability="unavailable",
            reason="",
        )

    assert result == "מה הסיבה לאי-הזמינות?"


def test_sender_identity_from_context_not_fuzzy_matching(tmp_path: Path):
    """1.2 & 1.4 Gate: Identity is strictly authenticated context; unauthenticated or unapproved fails."""
    agent, store, now = _setup_agent(tmp_path)

    # 1. No authenticated identity -> returns error message
    result_no_auth = agent.record_attendance_response(
        availability="available",
    )
    catalog = get_catalog("he")
    assert result_no_auth == catalog.text("unified.team_status.identity_unavailable")

    # 2. Authenticated user but not on roster -> returns unapproved failure
    with authenticated_request_identity("9999999999999999"):
        result_unapproved = agent.record_attendance_response(
            availability="available",
        )
    assert result_unapproved == catalog.text("unified.team_status.not_approved")


def test_clarification_answer_updates_db(tmp_path: Path):
    """1.3 Gate: After clarification question, answering with reason updates the DB."""
    agent, store, now = _setup_agent(tmp_path)
    michael_id = "9000000000000007"
    raw_text = "אני לא זמין היום"

    # Step 1: Initial report without reason -> clarification
    with authenticated_request_identity(michael_id), request_message_context(
        source_message_id="msg-michael-3",
        received_at=now.isoformat(),
        raw_text=raw_text,
    ):
        clarification = agent.record_attendance_response(
            availability="unavailable",
            reason="",
        )
    assert clarification == "מה הסיבה לאי-הזמינות?"

    # Step 2: Answering with reason -> DB updates
    clarified_reason = "חום גבוה"
    with authenticated_request_identity(michael_id), request_message_context(
        source_message_id="msg-michael-3-clarified",
        received_at=(now + timedelta(minutes=2)).isoformat(),
        raw_text=clarified_reason,
    ):
        result = agent.record_attendance_response(
            availability="unavailable",
            reason=clarified_reason,
            unavailable_days=1,
        )

    assert "✅" not in result
    snapshot = store.availability_snapshot((now + timedelta(minutes=2)).isoformat())
    michael_entry = next((e for e in snapshot if e["telegram_identity"] == michael_id), None)
    assert michael_entry is not None
    assert michael_entry["availability"] == "unavailable"
    assert michael_entry["reason"] == "חום גבוה"
    assert michael_entry["unavailable_until"] is not None

