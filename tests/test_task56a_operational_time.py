"""Task 56A — operational (business) time vs runtime lifecycle time.

The two clocks must never decide one another's questions: a historical
scenario timestamp may not make a runtime deadline born expired, and a real
runtime receipt may not make an in-window operational report look late.
"""

from datetime import datetime, timedelta, timezone

import pytest

from persistence import (
    OperationalScope,
    current_operational_time,
    open_team_status_persistence,
    operational_now,
    operational_scope_context,
    operational_time_context,
    operational_time_of_event,
    parse_operational_timestamp,
    runtime_now,
)


SCENARIO_TIME = "2026-09-09T07:45:00+00:00"
WINDOW_START = "2026-09-09T12:00:00+00:00"
WINDOW_END = "2026-09-09T15:00:00+00:00"


@pytest.fixture
def store(tmp_path):
    return open_team_status_persistence(str(tmp_path / "team_status.db"))


@pytest.fixture
def simulation_scope():
    return OperationalScope.simulation("FIRE_002_PHASE_1", "run-task56a")


def _seed(store, scope, identity="9001", name="Omri"):
    store.ensure_scope(
        scope,
        baseline={"team": {"members": [{"telegram_identity": identity, "full_name": name}], "approve": True}},
    )


def _open_operational_cycle(store, scope, opened_at=SCENARIO_TIME, window_hours=1):
    opened = parse_operational_timestamp(opened_at)
    return store.open_cycle(
        opened.isoformat()[:10],
        opened.isoformat(),
        (opened + timedelta(hours=window_hours)).isoformat(),
        scope=scope,
    )


def test_runtime_clock_is_real_time():
    assert abs((runtime_now() - datetime.now(timezone.utc)).total_seconds()) < 5


def test_live_scope_business_time_is_the_receipt_time():
    received = "2026-01-02T03:04:05+00:00"

    resolved = operational_now(
        scenario_time="2020-01-01T00:00:00+00:00",
        received_at=received,
        scope=OperationalScope.live(),
    )

    assert resolved == parse_operational_timestamp(received)


def test_simulation_scope_business_time_is_the_declared_scenario_time():
    resolved = operational_now(
        scenario_time=SCENARIO_TIME,
        received_at=runtime_now().isoformat(),
        scope=OperationalScope.simulation("S", "r1"),
    )

    assert resolved == parse_operational_timestamp(SCENARIO_TIME)


def test_ambient_operational_time_applies_only_inside_a_simulation_scope():
    sim = OperationalScope.simulation("S", "r1")

    with operational_scope_context(sim), operational_time_context(SCENARIO_TIME):
        assert operational_now() == parse_operational_timestamp(SCENARIO_TIME)
        assert current_operational_time() == parse_operational_timestamp(SCENARIO_TIME)

    with operational_scope_context(OperationalScope.live()), operational_time_context(SCENARIO_TIME):
        assert operational_now().date() == datetime.now(timezone.utc).date()

    assert current_operational_time() is None


def test_simulation_without_a_declared_time_falls_back_to_the_receipt():
    received = "2026-05-05T05:05:05+00:00"

    resolved = operational_now(received_at=received, scope=OperationalScope.simulation("S", "r1"))

    assert resolved == parse_operational_timestamp(received)


def test_operational_time_of_event_prefers_scenario_time_in_simulation():
    event = {"scenario_time": SCENARIO_TIME, "received_at": runtime_now().isoformat()}

    resolved = operational_time_of_event(event, scope=OperationalScope.simulation("S", "r1"))

    assert resolved == parse_operational_timestamp(SCENARIO_TIME)


def test_in_window_report_is_accepted_despite_a_much_later_runtime_receipt(store, simulation_scope):
    _seed(store, simulation_scope)
    _open_operational_cycle(store, simulation_scope)

    response = store.record_response(
        telegram_identity="9001",
        source_message_id="msg-1",
        availability="unavailable",
        original_text="leaving at 12:00, back at 15:00",
        received_at=runtime_now().isoformat(),
        reason="periodic medical check",
        availability_start=WINDOW_START,
        availability_end=WINDOW_END,
        reported_at=SCENARIO_TIME,
        scope=simulation_scope,
    )

    assert response["approval_status"] == "accepted"
    assert parse_operational_timestamp(response["reported_at"]) == parse_operational_timestamp(SCENARIO_TIME)


def test_a_genuinely_late_operational_report_is_still_held_for_review(store, simulation_scope):
    _seed(store, simulation_scope)
    _open_operational_cycle(store, simulation_scope)

    response = store.record_response(
        telegram_identity="9001",
        source_message_id="msg-late",
        availability="available",
        original_text="reporting in",
        received_at=runtime_now().isoformat(),
        reported_at="2026-09-09T10:30:00+00:00",
        scope=simulation_scope,
    )

    assert response["approval_status"] == "pending"


def test_omitting_the_operational_time_preserves_live_receipt_semantics(store):
    scope = OperationalScope.live()
    store.register_member("7001", "Live Member")
    store.approve_roster("commander_user")
    opened = runtime_now()
    store.open_cycle(
        opened.isoformat()[:10],
        opened.isoformat(),
        (opened + timedelta(hours=1)).isoformat(),
        scope=scope,
    )

    response = store.record_response(
        telegram_identity="7001",
        source_message_id="msg-live",
        availability="available",
        original_text="available",
        received_at=opened.isoformat(),
        scope=scope,
    )

    assert response["approval_status"] == "accepted"
    assert parse_operational_timestamp(response["reported_at"]) == parse_operational_timestamp(opened)


def test_declared_absence_is_a_bounded_operational_interval(store, simulation_scope):
    _seed(store, simulation_scope)
    _open_operational_cycle(store, simulation_scope)
    store.record_response(
        telegram_identity="9001",
        source_message_id="msg-window",
        availability="unavailable",
        original_text="leaving at 12:00, back at 15:00",
        received_at=runtime_now().isoformat(),
        reason="periodic medical check",
        availability_start=WINDOW_START,
        availability_end=WINDOW_END,
        reported_at=SCENARIO_TIME,
        scope=simulation_scope,
    )

    def availability_at(instant):
        return store.availability_snapshot(instant, scope=simulation_scope)[0]["availability"]

    assert availability_at(SCENARIO_TIME) == "available"
    assert availability_at("2026-09-09T11:30:00+00:00") == "available"
    assert availability_at(WINDOW_START) == "unavailable"
    assert availability_at("2026-09-09T13:00:00+00:00") == "unavailable"
    assert availability_at(WINDOW_END) == "available"
    assert availability_at("2026-09-09T16:00:00+00:00") == "available"


def test_the_declared_window_stays_visible_while_the_member_is_still_available(store, simulation_scope):
    _seed(store, simulation_scope)
    _open_operational_cycle(store, simulation_scope)
    store.record_response(
        telegram_identity="9001",
        source_message_id="msg-window",
        availability="unavailable",
        original_text="leaving at 12:00, back at 15:00",
        received_at=runtime_now().isoformat(),
        reason="periodic medical check",
        availability_start=WINDOW_START,
        availability_end=WINDOW_END,
        reported_at=SCENARIO_TIME,
        scope=simulation_scope,
    )

    entry = store.availability_snapshot("2026-09-09T11:30:00+00:00", scope=simulation_scope)[0]

    assert entry["availability"] == "available"
    assert entry["availability_start"] == parse_operational_timestamp(WINDOW_START).isoformat()
    assert entry["availability_end"] == parse_operational_timestamp(WINDOW_END).isoformat()
    assert entry["reason"] == "periodic medical check"


def test_migration_backfills_the_operational_time_from_the_runtime_receipt(tmp_path):
    import sqlite3

    db_path = str(tmp_path / "legacy.db")
    store = open_team_status_persistence(db_path)
    scope = OperationalScope.live()
    store.register_member("7002", "Legacy Member")
    store.approve_roster("commander_user")
    opened = runtime_now()
    store.open_cycle(
        opened.isoformat()[:10], opened.isoformat(), (opened + timedelta(hours=1)).isoformat(), scope=scope
    )
    store.record_response(
        telegram_identity="7002",
        source_message_id="msg-legacy",
        availability="available",
        original_text="available",
        received_at=opened.isoformat(),
        scope=scope,
    )

    connection = sqlite3.connect(db_path)
    connection.execute("UPDATE attendance_responses SET reported_at=NULL")
    connection.commit()
    connection.close()

    reopened = open_team_status_persistence(db_path)
    rows = reopened.pending_late_responses(scope=scope)
    assert rows == []

    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    row = connection.execute("SELECT received_at, reported_at FROM attendance_responses").fetchone()
    connection.close()

    assert row["reported_at"] == row["received_at"]


def test_report_ingestion_opens_the_cycle_on_the_operational_clock(tmp_path):
    from agents.team_status_agent import TeamStatusAgent

    class _Agent(TeamStatusAgent):
        status_db_path = str(tmp_path / "ingest.db")

        def __init__(self):
            self.status_store = open_team_status_persistence(self.status_db_path)

    agent = _Agent()
    scope = OperationalScope.simulation("FIRE_002_PHASE_1", "run-ingest")
    _seed(agent.status_store, scope)

    result = agent.ingest_report(
        {
            "event_id": "event-1",
            "source_message_id": "msg-ingest",
            "classification": "team_attendance_report",
            "business_fields": {"availability": "unavailable", "reason": "periodic medical check"},
            "availability_start": WINDOW_START,
            "availability_end": WINDOW_END,
            "sender_identity": "9001",
            "raw_text": "leaving at 12:00, back at 15:00",
            "received_at": runtime_now().isoformat(),
            "scenario_time": SCENARIO_TIME,
            "scenario_id": scope.scenario_id,
            "scenario_run_id": scope.scenario_run_id,
        },
        scope=scope,
    )

    assert result.status == "committed"

    cycle = agent.status_store.latest_cycle(scope=scope)
    assert parse_operational_timestamp(cycle["opened_at"]) == parse_operational_timestamp(SCENARIO_TIME)
    assert parse_operational_timestamp(cycle["deadline_at"]) == parse_operational_timestamp(SCENARIO_TIME) + timedelta(hours=1)

    entry = agent.status_store.availability_snapshot("2026-09-09T13:00:00+00:00", scope=scope)[0]
    assert entry["availability"] == "unavailable"


def test_runtime_deadlines_are_never_taken_from_scenario_time():
    """A historical scenario step must not produce an already-expired runtime deadline."""

    from datetime import timedelta as _timedelta

    with operational_scope_context(OperationalScope.simulation("S", "r1")), operational_time_context(SCENARIO_TIME):
        runtime_deadline = runtime_now() + _timedelta(seconds=120)

    assert runtime_deadline > datetime.now(timezone.utc)
