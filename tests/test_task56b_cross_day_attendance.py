"""Task 56B — which operational window owns an attendance report.

A report is judged against the window that owns its operational instant, and it
cannot be late for an operational day that never had an open window: nothing was
asked of that member that day. Cycle creation stays with the scheduler, so the
LIVE daily-check flow is unchanged.
"""

from datetime import timedelta

import pytest

from persistence import (
    OperationalScope,
    open_team_status_persistence,
    operational_scope_context,
    operational_time_context,
    parse_operational_timestamp,
    runtime_now,
)


DAY_ONE = "2026-09-06T07:30:00+00:00"
DAY_TWO = "2026-09-07T06:30:00+00:00"


@pytest.fixture
def store(tmp_path):
    return open_team_status_persistence(str(tmp_path / "team_status.db"))


@pytest.fixture
def simulation_scope():
    return OperationalScope.simulation("SEC_001_PHASE_1", "run-task56b")


def _seed(store, scope):
    store.ensure_scope(
        scope,
        baseline={
            "team": {
                "members": [
                    {"telegram_identity": "1", "full_name": "Eli"},
                    {"telegram_identity": "2", "full_name": "Danny"},
                ],
                "approve": True,
            }
        },
    )


def _open_day_one(store, scope):
    opened = parse_operational_timestamp(DAY_ONE)
    return store.open_cycle(
        "2026-09-06", opened.isoformat(), (opened + timedelta(hours=1)).isoformat(), scope=scope
    )


def _respond(store, scope, identity, reported_at, day, **extra):
    return store.record_response(
        telegram_identity=identity,
        source_message_id=f"msg-{identity}-{reported_at}",
        availability="unavailable",
        original_text="unavailable",
        received_at=runtime_now().isoformat(),
        reason="operational absence",
        reported_at=reported_at,
        operational_day=day,
        scope=scope,
        **extra,
    )


def test_a_report_on_a_day_with_no_open_window_is_not_late(store, simulation_scope):
    _seed(store, simulation_scope)
    _open_day_one(store, simulation_scope)

    same_day = _respond(store, simulation_scope, "1", DAY_ONE, "2026-09-06")
    next_day = _respond(store, simulation_scope, "2", DAY_TWO, "2026-09-07")

    assert same_day["approval_status"] == "accepted"
    assert next_day["approval_status"] == "accepted"


def test_a_late_report_inside_its_own_day_is_still_held_for_review(store, simulation_scope):
    _seed(store, simulation_scope)
    _open_day_one(store, simulation_scope)

    late = _respond(store, simulation_scope, "1", "2026-09-06T10:30:00+00:00", "2026-09-06")

    assert late["approval_status"] == "pending"


def test_no_extra_cycle_is_created_by_a_next_day_report(store, simulation_scope):
    _seed(store, simulation_scope)
    _open_day_one(store, simulation_scope)

    _respond(store, simulation_scope, "2", DAY_TWO, "2026-09-07")

    assert store.latest_cycle(scope=simulation_scope)["cycle_key"] == "2026-09-06"


def test_the_owning_window_is_resolved_by_operational_instant(store, simulation_scope):
    _seed(store, simulation_scope)
    first = _open_day_one(store, simulation_scope)
    later = parse_operational_timestamp(DAY_TWO)
    second = store.open_cycle(
        "2026-09-07", later.isoformat(), (later + timedelta(hours=1)).isoformat(), scope=simulation_scope
    )

    owned_by_first = store.cycle_for_operational_instant(parse_operational_timestamp("2026-09-06T08:00:00+00:00"), scope=simulation_scope)
    owned_by_second = store.cycle_for_operational_instant(parse_operational_timestamp("2026-09-07T07:00:00+00:00"), scope=simulation_scope)

    assert owned_by_first["cycle_id"] == first.cycle_id
    assert owned_by_second["cycle_id"] == second.cycle_id


def test_a_report_predating_every_window_belongs_to_the_first(store, simulation_scope):
    _seed(store, simulation_scope)
    first = _open_day_one(store, simulation_scope)

    owned = store.cycle_for_operational_instant(parse_operational_timestamp("2026-09-05T23:00:00+00:00"), scope=simulation_scope)

    assert owned["cycle_id"] == first.cycle_id


def test_omitting_the_operational_day_preserves_the_previous_window_judgement(store, simulation_scope):
    _seed(store, simulation_scope)
    _open_day_one(store, simulation_scope)

    without_day = store.record_response(
        telegram_identity="2",
        source_message_id="msg-no-day",
        availability="unavailable",
        original_text="unavailable",
        received_at=runtime_now().isoformat(),
        reason="operational absence",
        reported_at=DAY_TWO,
        scope=simulation_scope,
    )

    assert without_day["approval_status"] == "pending"


def test_ingestion_across_two_operational_days_reaches_the_right_night_picture(tmp_path):
    from agents.team_status_agent import TeamStatusAgent

    class _Agent(TeamStatusAgent):
        status_db_path = str(tmp_path / "ingest.db")

        def __init__(self):
            self.status_store = open_team_status_persistence(self.status_db_path)

    agent = _Agent()
    scope = OperationalScope.simulation("SEC_001_PHASE_1", "run-task56b-ingest")
    _seed(agent.status_store, scope)

    def ingest(identity, scenario_time, start, end, reason, message_id):
        return agent.ingest_report(
            {
                "event_id": message_id,
                "source_message_id": message_id,
                "classification": "team_attendance_report",
                "business_fields": {"availability": "unavailable", "reason": reason},
                "availability_start": start,
                "availability_end": end,
                "sender_identity": identity,
                "raw_text": reason,
                "received_at": runtime_now().isoformat(),
                "scenario_time": scenario_time,
                "scenario_id": scope.scenario_id,
                "scenario_run_id": scope.scenario_run_id,
            },
            scope=scope,
        )

    assert ingest("1", DAY_ONE, DAY_ONE, "2026-09-08T18:00:00Z", "reserve duty", "m1").status == "committed"
    assert ingest("2", DAY_TWO, DAY_TWO, "2026-09-07T21:00:00Z", "high fever", "m6").status == "committed"

    night = {entry["full_name"]: entry["availability"] for entry in agent.status_store.availability_snapshot("2026-09-07T19:00:00+00:00", scope=scope)}

    assert night == {"Eli": "unavailable", "Danny": "unavailable"}


def test_the_implicit_cycle_key_is_the_profile_local_day(tmp_path):
    from agents.team_status_agent import TeamStatusAgent

    class _Agent(TeamStatusAgent):
        status_db_path = str(tmp_path / "local_day.db")

        def __init__(self):
            self.status_store = open_team_status_persistence(self.status_db_path)

    agent = _Agent()
    scope = OperationalScope.simulation("SEC_001_PHASE_1", "run-task56b-localday")
    _seed(agent.status_store, scope)

    # 22:00Z is already the next calendar day in Asia/Jerusalem.
    agent.ingest_report(
        {
            "event_id": "m-late",
            "source_message_id": "m-late",
            "classification": "team_attendance_report",
            "business_fields": {"availability": "unavailable", "reason": "night absence"},
            "availability_start": "2026-09-06T22:00:00+00:00",
            "availability_end": "2026-09-07T04:00:00+00:00",
            "sender_identity": "1",
            "raw_text": "night absence",
            "received_at": runtime_now().isoformat(),
            "scenario_time": "2026-09-06T22:00:00+00:00",
            "scenario_id": scope.scenario_id,
            "scenario_run_id": scope.scenario_run_id,
        },
        scope=scope,
    )

    assert agent.status_store.latest_cycle(scope=scope)["cycle_key"] == "2026-09-07"


def test_the_original_cycle_and_its_provenance_are_preserved(store, simulation_scope):
    """A next-day report joins the existing window; it never rewrites it."""

    _seed(store, simulation_scope)
    first = _open_day_one(store, simulation_scope)
    before = store.latest_cycle(scope=simulation_scope)

    response = _respond(store, simulation_scope, "2", DAY_TWO, "2026-09-07")
    after = store.latest_cycle(scope=simulation_scope)

    assert response["cycle_id"] == first.cycle_id
    assert after == before
    assert parse_operational_timestamp(response["reported_at"]) == parse_operational_timestamp(DAY_TWO)
    assert parse_operational_timestamp(response["received_at"]) != parse_operational_timestamp(DAY_TWO)
    assert response["original_text"] == "unavailable"
    assert response["reason"] == "operational absence"


def test_live_daily_check_scheduling_is_unchanged(tmp_path):
    """The scheduler still owns cycle creation and still prompts exactly once a day."""

    from agents.team_status_agent import TeamStatusAgent

    class _Agent(TeamStatusAgent):
        status_db_path = str(tmp_path / "live_schedule.db")

        def __init__(self):
            self.status_store = open_team_status_persistence(self.status_db_path)

    agent = _Agent()
    agent.status_store.register_member("7001", "Live Member")
    agent.status_store.approve_roster("commander_user")

    morning = "2026-09-06T06:00:00+00:00"  # 09:00 Asia/Jerusalem, after the 08:00 check hour
    assert agent.attendance_check_due(morning) is True

    opened = agent.open_scheduled_cycle(morning)
    assert opened is not None
    assert opened["cycle_key"] == "2026-09-06"
    assert opened["members_required"] == ["Live Member"]

    assert agent.attendance_check_due(morning) is False
    assert agent.open_scheduled_cycle(morning) is None

    scope = OperationalScope.live()
    agent.ingest_report(
        {
            "event_id": "live-1",
            "source_message_id": "live-1",
            "classification": "team_attendance_report",
            "business_fields": {"availability": "unavailable", "reason": "medical"},
            "availability_start": "2026-09-06T06:30:00+00:00",
            "availability_end": "2026-09-06T09:00:00+00:00",
            "sender_identity": "7001",
            "raw_text": "unavailable",
            "received_at": "2026-09-06T06:30:00+00:00",
        },
        scope=scope,
    )

    cycles = agent.status_store.latest_cycle(scope=scope)
    assert cycles["cycle_key"] == "2026-09-06"


def test_runtime_holds_and_deadlines_still_use_the_runtime_clock(tmp_path):
    """Task 49's expiry path must never read the operational clock."""

    from types import SimpleNamespace

    from orchestrator.flows import finalize_expired_event
    from persistence.sqlite_store import SQLitePersistence

    store = SQLitePersistence(str(tmp_path / "expiry.db"))
    try:
        deps = SimpleNamespace(persistence=store)

        def _event(deadline_at, **extra):
            payload = {
                "received_at": runtime_now().isoformat(),
                "source": "telegram",
                "sender_identity": "viewer-1",
                "raw_text": "test event",
                "deadline_at": deadline_at,
                # A historical simulation instant that must not reach the expiry path.
                "scenario_id": "SEC_001_PHASE_1",
                "scenario_run_id": "run-expiry",
                "scenario_time": DAY_ONE,
            }
            payload.update(extra)
            return store.append_event(payload)

        live_id = _event((runtime_now() + timedelta(minutes=5)).isoformat())
        stale_id = _event((runtime_now() - timedelta(minutes=5)).isoformat())

        simulation = OperationalScope.simulation("SEC_001_PHASE_1", "run-expiry")
        with operational_scope_context(simulation), operational_time_context(DAY_ONE):
            not_yet = finalize_expired_event(deps, live_id)
            already = finalize_expired_event(deps, stale_id)

        assert not_yet.status == "skipped"
        assert not_yet.skip_reason == "deadline_not_expired"
        assert store.fetch_event(live_id)["outcome"] is None

        assert already.status == "finalized"
        assert already.finalization_reason == "deadline_expired"
        assert store.fetch_event(stale_id)["outcome"] == "failed"
    finally:
        store.close()
