"""Response-team resource lifecycle: drone missions that end, reuse of covering missions,
event/incident links, releases, area normalization, camera seeds, and the binders that tie
corrections and closures to the resources they release. No billed model calls."""

import sqlite3
from contextvars import copy_context
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import profiles.response_team as rt
from messages import get_catalog, set_current_catalog
from persistence import (
    NeighboringForceStoreError,
    SurveillancePersistenceError,
    open_neighboring_force_store,
    open_response_team_roster_store,
    open_response_team_surveillance_store,
)
from persistence.sqlite_store import SQLitePersistence
from profiles.response_team_protocols import (
    PROTOCOLS,
    _bind_armed_threat,
    _bind_close_security_incident,
    _bind_correct_false_security_report,
    _bind_dispatch_drone,
)
from profiles.simulation import SimulationPersona, SimulationRoster, simulation_user_telegram_id
from profiles.simulation_provisioning import _withdraw_undeclared_simulation_members
from tools import event_id_context

_T0 = datetime(2026, 10, 12, 8, 0, tzinfo=timezone.utc)
_WAREHOUSE_TO_WEST_GATE = rt.eta_seconds(rt.DRONES_WAREHOUSE, "west_gate")


class _Clock:
    """Deterministic clock injected into the surveillance store."""

    def __init__(self, start: datetime):
        """Start the clock at `start`."""
        self.now = start

    def __call__(self) -> str:
        """Return the current fake time as ISO text."""
        return self.now.isoformat()

    def advance(self, seconds: int) -> None:
        """Move the fake time forward."""
        self.now += timedelta(seconds=seconds)


def _store(tmp_path, clock=None, loiter=rt.DRONE_LOITER_SECONDS):
    """A seeded surveillance store with the profile's two drones."""
    store = open_response_team_surveillance_store(
        str(tmp_path / "surveillance.db"), eta_fn=rt.eta_seconds, home_area=rt.DRONES_WAREHOUSE,
        mission_loiter_seconds=loiter, clock=clock,
    )
    for drone in rt.DRONES:
        store.ensure_drone(**drone)
    for camera in rt.CAMERAS:
        store.ensure_camera(**camera)
    return store


def _mission_row(store, mission_id):
    """Read one mission row directly, including finished ones."""
    conn = sqlite3.connect(store.db_path)
    conn.row_factory = sqlite3.Row
    try:
        return dict(conn.execute("SELECT * FROM drone_missions WHERE mission_id = ?", (mission_id,)).fetchone())
    finally:
        conn.close()


def _in_language(language, fn, *args, **kwargs):
    """Run fn with the given catalog as the request's current catalog."""
    def _run():
        set_current_catalog(get_catalog(language))
        return fn(*args, **kwargs)
    return copy_context().run(_run)


# -- Drone mission lifecycle ------------------------------------------------------------------


def test_mission_goes_on_station_at_eta_and_completes_after_the_loiter_window(tmp_path):
    """Dispatched -> on_station at the ETA -> completed after loitering, with the drone home and ready."""
    clock = _Clock(_T0)
    store = _store(tmp_path, clock)
    mission = store.dispatch_drone(target_area="west_gate", incident_description="shots heard")
    assert mission["status"] == "dispatched" and mission["reused"] is False

    clock.advance(_WAREHOUSE_TO_WEST_GATE - 1)
    assert store.get_active_missions()[0]["status"] == "dispatched"

    clock.advance(1)
    (on_station,) = store.get_active_missions()
    assert on_station["status"] == "on_station"
    assert on_station["updated_at"] == (_T0 + timedelta(seconds=_WAREHOUSE_TO_WEST_GATE)).isoformat()

    clock.advance(rt.DRONE_LOITER_SECONDS - 1)
    assert store.get_active_missions()[0]["status"] == "on_station"

    clock.advance(1)
    assert store.get_active_missions() == []
    completed_at = (_T0 + timedelta(seconds=_WAREHOUSE_TO_WEST_GATE + rt.DRONE_LOITER_SECONDS)).isoformat()
    assert _mission_row(store, mission["mission_id"])["status"] == "completed"
    assert _mission_row(store, mission["mission_id"])["updated_at"] == completed_at
    drone = store.get_drone(mission["drone_id"])
    assert drone["status"] == "ready"
    assert drone["current_area"] == rt.DRONES_WAREHOUSE
    assert drone["assigned_mission_id"] is None
    assert drone["last_updated"] == completed_at


def test_a_third_launch_succeeds_once_earlier_missions_have_ended(tmp_path):
    """Two launches no longer exhaust the fleet for the rest of the run."""
    clock = _Clock(_T0)
    store = _store(tmp_path, clock)
    store.dispatch_drone(target_area="west_gate", incident_description="a")
    store.dispatch_drone(target_area="east_gate", incident_description="b")
    with pytest.raises(SurveillancePersistenceError):
        store.dispatch_drone(target_area="south_corner", incident_description="c")

    clock.advance(_WAREHOUSE_TO_WEST_GATE + rt.DRONE_LOITER_SECONDS + 60)
    third = store.dispatch_drone(target_area="south_corner", incident_description="c")

    assert third["status"] == "dispatched"
    assert third["reused"] is False


def test_without_a_loiter_window_missions_stay_open_as_before(tmp_path):
    """A profile that declares no loiter window keeps the older open-ended missions."""
    clock = _Clock(_T0)
    store = _store(tmp_path, clock, loiter=None)
    store.dispatch_drone(target_area="west_gate", incident_description="a")

    clock.advance(24 * 60 * 60)

    (mission,) = store.get_active_missions()
    assert mission["status"] == "dispatched"
    second = store.dispatch_drone(target_area="west_gate", incident_description="b")
    assert second["reused"] is False  # no area reuse without a lifecycle


# -- Reuse of a covering mission --------------------------------------------------------------


def test_second_request_for_a_covered_area_reuses_the_mission(tmp_path):
    """A drone already over the area is reported instead of launching the second drone."""
    store = _store(tmp_path, _Clock(_T0))
    first = store.dispatch_drone(target_area="west_gate", incident_description="first", event_id="evt-1")

    again = store.dispatch_drone(target_area="West_Gate ", incident_description="second", event_id="evt-2")

    assert again["reused"] is True
    assert again["mission_id"] == first["mission_id"]
    assert again["event_id"] == "evt-1"  # an already linked mission keeps its own event
    assert len(store.list_drones(status="ready")) == 1


def test_reuse_adopts_the_first_event_link_for_an_unlinked_mission(tmp_path):
    """An unlinked covering mission is linked to the first event that relies on it."""
    store = _store(tmp_path, _Clock(_T0))
    store.dispatch_drone(target_area="west_gate", incident_description="first")

    again = store.dispatch_drone(
        target_area="west_gate", incident_description="second", event_id="evt-2", incident_id="inc-2"
    )

    assert (again["event_id"], again["incident_id"]) == ("evt-2", "inc-2")


def test_an_explicitly_named_second_drone_is_still_launched(tmp_path):
    """Naming a different drone is a request for that drone, not for coverage."""
    store = _store(tmp_path, _Clock(_T0))
    first = store.dispatch_drone(target_area="west_gate", incident_description="first")
    other = next(d["drone_id"] for d in rt.DRONES if d["drone_id"] != first["drone_id"])

    second = store.dispatch_drone(target_area="west_gate", incident_description="second", specific_drone_id=other)

    assert second["reused"] is False
    assert second["drone_id"] == other


def test_dispatch_tool_reports_reuse_as_success_without_a_resource_signal(tmp_path, monkeypatch):
    """The agent tool says the area is already covered and never turns that into resource-unavailable."""
    agent = _surveillance_agent(tmp_path, monkeypatch)
    agent.dispatch_drone_to_area(target_area="west_gate", incident_description="first")
    agent.take_resource_unavailable_signal()

    result = agent.dispatch_drone_to_area(target_area="west_gate", incident_description="second")

    assert "already covering the area" in str(result)
    assert agent.take_resource_unavailable_signal() is None


# -- Event links and releases -----------------------------------------------------------------


def test_missions_record_event_and_incident_and_end_by_either(tmp_path):
    """end_missions matches event_id or incident_id, never everything."""
    store = _store(tmp_path, _Clock(_T0))
    west = store.dispatch_drone(target_area="west_gate", incident_description="a", event_id="evt-1", incident_id="inc-1")
    east = store.dispatch_drone(target_area="east_gate", incident_description="b", event_id="evt-2", incident_id="inc-2")
    assert (west["event_id"], west["incident_id"]) == ("evt-1", "inc-1")

    with pytest.raises(SurveillancePersistenceError):
        store.end_missions()
    ended = store.end_missions(incident_id="inc-1", outcome="aborted", reason="report corrected")

    assert [m["mission_id"] for m in ended] == [west["mission_id"]]
    assert _mission_row(store, west["mission_id"])["status"] == "aborted"
    assert [m["mission_id"] for m in store.get_active_missions()] == [east["mission_id"]]
    assert store.get_drone(west["drone_id"])["status"] == "ready"
    assert store.end_missions(event_id="evt-1") == []  # idempotent


def test_older_databases_gain_the_new_columns(tmp_path):
    """Opening a database created before the link and kind columns adds them in place."""
    db_path = str(tmp_path / "old.db")
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE cameras (camera_id TEXT PRIMARY KEY, name TEXT NOT NULL, area TEXT NOT NULL,
            status TEXT NOT NULL, azimuth_degrees INTEGER NOT NULL DEFAULT 0, feed_summary TEXT NOT NULL,
            last_updated TEXT NOT NULL);
        CREATE TABLE drone_missions (mission_id TEXT PRIMARY KEY, drone_id TEXT NOT NULL, target_area TEXT NOT NULL,
            mission_type TEXT NOT NULL, incident_description TEXT NOT NULL, status TEXT NOT NULL,
            dispatched_by TEXT NOT NULL, dispatched_at TEXT NOT NULL, eta_seconds INTEGER NOT NULL,
            notes TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL);
        CREATE TABLE neighboring_force_dispatches (request_id TEXT PRIMARY KEY, force_kind TEXT NOT NULL,
            unit_count INTEGER NOT NULL, origin_area TEXT NOT NULL, target_area TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('en_route', 'arrived')), dispatched_at TEXT NOT NULL,
            eta_seconds INTEGER NOT NULL, arrived_at TEXT, note TEXT NOT NULL DEFAULT '');
        """
    )
    conn.close()

    open_response_team_surveillance_store(db_path)
    open_neighboring_force_store(db_path)

    conn = sqlite3.connect(db_path)
    try:
        def columns(table):
            return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        assert {"event_id", "incident_id"} <= columns("drone_missions")
        assert "kind" in columns("cameras")
        assert {"event_id", "incident_id", "released_at", "release_reason"} <= columns("neighboring_force_dispatches")
    finally:
        conn.close()


def test_force_release_by_event_or_incident_and_listing(tmp_path):
    """Released dispatches keep their row, leave the en_route filter, and appear as released."""
    store = open_neighboring_force_store(str(tmp_path / "forces.db"))
    police = store.dispatch(force_kind="police", origin_area="a", target_area="west_gate", unit_count=1,
                            eta_seconds=60, event_id="evt-1", incident_id="inc-1")
    store.dispatch(force_kind="yasam", origin_area="a", target_area="west_gate", unit_count=1,
                   eta_seconds=60, event_id="evt-2", incident_id="inc-2")

    with pytest.raises(NeighboringForceStoreError):
        store.release_dispatches()
    released = store.release_dispatches(incident_id="inc-1", reason="incident closed")

    assert [row["request_id"] for row in released] == [police["request_id"]]
    assert released[0]["released_at"] and released[0]["release_reason"] == "incident closed"
    assert [row["force_kind"] for row in store.list_dispatches(status="en_route")] == ["yasam"]
    assert [row["force_kind"] for row in store.list_dispatches(status="released")] == ["police"]
    assert len(store.list_dispatches()) == 2
    assert store.release_dispatches(event_id="evt-1") == []  # idempotent


def test_capacity_ignores_released_dispatches(tmp_path, monkeypatch):
    """A released unit is free again for the pool check."""
    agent = _neighboring_forces_agent(tmp_path, monkeypatch)
    agent.dispatch_neighboring_force(kind="police", target_area="east_gate", unit_count=2, event_id="evt-1")
    assert "dispatch failed" in str(agent.dispatch_neighboring_force(kind="police", target_area="west_gate"))
    agent.take_resource_unavailable_signal()

    release_text = agent.release_force_dispatches(event_id="evt-1", reason="report corrected")

    assert f"{rt._RESOURCE_KIND_LABELS['police']} x2" in release_text
    assert rt._AREA_LABELS["east_gate"] in release_text
    assert "dispatch recorded" in agent.dispatch_neighboring_force(kind="police", target_area="west_gate")
    listing = agent.list_neighboring_force_dispatches()
    assert "RELEASED" in listing


def test_dispatch_tools_link_to_the_running_event_when_not_told(tmp_path, monkeypatch):
    """A model-formulated dispatch inside a protocol run still records its event."""
    agent = _neighboring_forces_agent(tmp_path, monkeypatch)

    with event_id_context("evt-ctx"):
        agent.dispatch_neighboring_force(kind="ambulance", target_area="east_gate")

    (row,) = agent.dispatch_store.list_dispatches()
    assert (row["event_id"], row["incident_id"]) == ("evt-ctx", "evt-ctx")


def test_release_tools_confirm_in_catalog_text_and_refuse_without_a_reference(tmp_path, monkeypatch):
    """Release tools answer from the catalog and never release without an id."""
    forces = _neighboring_forces_agent(tmp_path, monkeypatch)
    surveillance = _surveillance_agent(tmp_path, monkeypatch)
    surveillance.dispatch_drone_to_area(target_area="west_gate", incident_description="a", event_id="evt-1")

    hebrew = get_catalog("he")
    assert forces.release_force_dispatches().ok is False
    assert surveillance.end_drone_missions().ok is False
    assert forces.release_force_dispatches(event_id="evt-none") == hebrew.text("response_team.release.no_dispatches")
    ended = surveillance.end_drone_missions(event_id="evt-1", outcome="aborted")
    assert rt._AREA_LABELS["west_gate"] in ended
    assert rt._AREA_LABELS[rt.DRONES_WAREHOUSE] in ended
    assert surveillance.end_drone_missions(event_id="evt-1") == hebrew.text("response_team.release.no_missions")


# -- Area normalization and surveillance reads ------------------------------------------------


def test_camera_feeds_accept_ids_labels_and_whole_site_phrases(tmp_path, monkeypatch):
    """An id, a Hebrew label, or a whole-site phrase all resolve; nothing silently returns empty."""
    agent = _surveillance_agent(tmp_path, monkeypatch)

    by_id = agent.get_camera_feeds(area="east_fence")
    by_label = agent.get_camera_feeds(area=get_catalog("he").text("response_team.area.east_fence"))
    whole_site = agent.get_camera_feeds(area="entire perimeter")

    assert "Camera feeds (2 cameras)" in by_id
    assert by_label == by_id
    assert "Camera feeds (5 cameras)" in whole_site


def test_unknown_or_ambiguous_area_is_an_error_listing_valid_areas(tmp_path, monkeypatch):
    """An unrecognized area never looks like "no cameras registered"."""
    agent = _surveillance_agent(tmp_path, monkeypatch)

    unknown = agent.get_camera_feeds(area="north pole")
    ambiguous = agent.get_surveillance_overview(area="gate")

    for result in (unknown, ambiguous):
        assert result.ok is False
        assert "Valid areas:" in result.text
        assert "west_gate" in result.text and "old_public_building" in result.text
    assert agent.take_resource_unavailable_signal() is None


def test_overview_filters_drones_with_the_area_and_shows_mission_ids(tmp_path, monkeypatch):
    """Cameras, drones, and missions share one area filter; a flying drone shows its mission."""
    agent = _surveillance_agent(tmp_path, monkeypatch)
    dispatch = agent.dispatch_drone_to_area(target_area="west_gate", incident_description="check")
    mission_id = next(line.split(": ")[1] for line in str(dispatch).splitlines() if "Mission ID" in line)

    overview = agent.get_surveillance_overview(area="west_gate")
    whole = agent.get_surveillance_overview()

    assert f"Mission: {mission_id}" in overview
    assert overview.count("[DRONE-") == 1  # only the drone over the west gate
    assert "Fleet-wide ready for dispatch: 1" in overview
    assert whole.count("[DRONE-") == 2


def test_mast_and_drone_cameras_are_seeded_offline_with_their_kind(tmp_path, monkeypatch):
    """Temporary cameras never count as live coverage from the seed alone."""
    agent = _surveillance_agent(tmp_path, monkeypatch)

    overview = agent.get_surveillance_overview(area="old_public_building")

    assert "Cameras (0/2 Active)" in overview
    assert "mast camera" in overview and "drone camera" in overview
    assert {c["camera_id"]: c["status"] for c in agent.surveillance_store.list_cameras(area="old_public_building")} == {
        "CAM-DRONE": "offline", "CAM-MAST": "offline",
    }


# -- Binders ----------------------------------------------------------------------------------


def test_dispatching_binders_pass_event_and_incident_ids():
    """Every dispatching step carries the event id and the incident id (or the event id as fallback)."""
    grouped = {"event_id": "evt-1", "incident_id": "inc-1", "area": "west_gate", "description": "shots"}
    single = {"event_id": "evt-2", "area": "west_gate", "description": "shots"}

    for step in (*_bind_armed_threat(grouped), *_bind_dispatch_drone(grouped)):
        assert (step.direct_tool_kwargs["event_id"], step.direct_tool_kwargs["incident_id"]) == ("evt-1", "inc-1")
    (drone_step,) = _bind_dispatch_drone(single)
    assert drone_step.direct_tool_kwargs["incident_id"] == "evt-2"


def test_correction_binder_releases_the_corrected_event_then_notifies():
    """A correction ends the corrected report's missions, releases its forces, then posts a notice."""
    steps = _bind_correct_false_security_report({"event_id": "evt-9", "corrects_event_id": "evt-1", "area": "west_gate"})

    assert [step.direct_tool_name for step in steps] == [
        "end_drone_missions", "release_force_dispatches", "post_operational_notice",
    ]
    assert steps[0].direct_tool_kwargs["event_id"] == "evt-1"
    assert steps[0].direct_tool_kwargs["outcome"] == "aborted"
    assert steps[1].direct_tool_kwargs["event_id"] == "evt-1"
    assert steps[2].direct_tool_kwargs["about_event_id"] == "evt-1"
    assert steps[2].direct_tool_kwargs["notice_key"] == "response_team.notice.report_corrected"
    assert len({step.step_id for step in steps}) == 3


def test_closure_binder_releases_the_incident_only_when_known():
    """A closure releases its incident's resources; without an incident id it only notifies."""
    with_incident = _bind_close_security_incident({"event_id": "evt-5", "incident_id": "inc-1", "area": "west_gate"})
    without = _bind_close_security_incident({"event_id": "evt-5"})

    assert [step.direct_tool_name for step in with_incident] == [
        "end_drone_missions", "release_force_dispatches", "post_operational_notice",
    ]
    assert with_incident[0].direct_tool_kwargs["incident_id"] == "inc-1"
    assert "event_id" not in with_incident[1].direct_tool_kwargs
    assert [step.direct_tool_name for step in without] == ["post_operational_notice"]


def test_correction_and_closure_protocols_approve_the_release_tools():
    """Every tool a binder can emit is approved and owned by a participating agent."""
    for name in ("correct_false_security_report", "close_security_incident"):
        protocol = next(p for p in PROTOCOLS if p.name == name)
        assert {"end_drone_missions", "release_force_dispatches", "post_operational_notice"} <= set(protocol.approved_tools)
        assert {"roster_agent", "surveillance_agent", "neighboring_forces_agent"} <= set(protocol.participating_agents)
        assert "west gate" not in protocol.description.lower()


# -- Notices and roster report ----------------------------------------------------------------


def test_correction_notice_is_built_from_the_corrected_event(tmp_path, monkeypatch):
    """The notice names the corrected report's area and description, not a fixed scenario."""
    roster = _roster_agent(tmp_path, monkeypatch)
    corrected_id = _append_event(roster, area="east_gate", description="shots near the gate")

    text = _in_language(
        "he", roster.post_operational_notice,
        notice_key="response_team.notice.report_corrected", about_event_id=corrected_id,
    )
    fallback = _in_language("he", roster.post_operational_notice, notice_key="response_team.notice.report_corrected")

    hebrew = get_catalog("he")
    assert text == hebrew.text(
        "response_team.notice.report_corrected.area_description",
        area=hebrew.text("response_team.area.east_gate"), description="shots near the gate",
    )
    assert fallback == hebrew.text("response_team.notice.report_corrected")


def test_hebrew_roster_report_formats_dates_and_shows_members_in_the_field(tmp_path, monkeypatch):
    """Hebrew dates never pass non-ASCII to strftime, and a member in the field is not "awaiting"."""
    roster = _roster_agent(tmp_path, monkeypatch)
    now = datetime.now(timezone.utc)
    roster.status_store.open_cycle(now.date().isoformat(), now.isoformat(), (now + timedelta(hours=1)).isoformat())
    roster.status_store.record_response(
        telegram_identity="dan", source_message_id="m-1", availability="unavailable", reason="sick",
        original_text="sick", received_at=now.isoformat(), unavailable_until=(now + timedelta(days=2)).isoformat(),
    )
    roster.status_store.set_current_area("gil", "east_gate")

    report = _in_language("he", roster.report_team_availability)

    hebrew = get_catalog("he")
    in_field = hebrew.text("attendance.report.status.in_field", area=hebrew.text("response_team.area.east_gate"))
    assert f"Gil: {in_field}" in report
    assert hebrew.text("attendance.report.count_in_field", count=1) in report
    assert "Dan: " + hebrew.text("attendance.report.status.unavailable") in report
    assert "Yuval: " + hebrew.text("attendance.report.status.awaiting_response") in report


def test_provisioning_withdraws_a_persona_no_longer_declared_for_the_roster(tmp_path):
    """A simulation persona dropped from a roster loses approval; real members are never touched."""
    db_path = str(tmp_path / "roster.db")
    store = open_response_team_roster_store(db_path)
    member_id, external_id = simulation_user_telegram_id(0), simulation_user_telegram_id(14)
    for identity in (member_id, external_id, "12345"):
        store.register_member(identity, f"Member {identity}")
    store.approve_roster("commander")
    loaded = SimpleNamespace(
        simulation_users=(
            SimulationPersona(key="member", offset=0, pre_approved_rosters=("team_status",)),
            SimulationPersona(key="external", offset=14),
        ),
        simulation_rosters=(SimulationRoster(key="team_status", open=open_response_team_roster_store, db_path=db_path),),
    )

    withdrawn = _withdraw_undeclared_simulation_members(loaded)

    assert withdrawn == (("team_status", external_id),)
    assert {m["telegram_identity"] for m in store.list_members(approved_only=True)} == {member_id, "12345"}
    assert _withdraw_undeclared_simulation_members(loaded) == ()  # idempotent


# -- Helpers ----------------------------------------------------------------------------------


def _surveillance_agent(tmp_path, monkeypatch):
    """The profile's surveillance agent on an isolated, seeded database."""
    db_path = str(tmp_path / "rt.db")
    monkeypatch.setattr(rt.ResponseTeamSurveillanceAgent, "surveillance_db_path", db_path)
    agent = rt.ResponseTeamSurveillanceAgent(model="test-model")
    for drone in rt.DRONES:
        agent.surveillance_store.ensure_drone(**drone)
    for camera in rt.CAMERAS:
        agent.surveillance_store.ensure_camera(**camera)
    return agent


def _neighboring_forces_agent(tmp_path, monkeypatch):
    """The profile's dispatch agent on an isolated database."""
    db_path = str(tmp_path / "rt.db")
    monkeypatch.setattr(rt, "DB_PATH", db_path)
    monkeypatch.setattr(rt.NeighboringForcesAgent, "dispatch_db_path", db_path)
    return rt.NeighboringForcesAgent(model="test-model")


def _roster_agent(tmp_path, monkeypatch):
    """The profile's roster agent with three approved members on an isolated database."""
    db_path = str(tmp_path / "rt.db")
    monkeypatch.setattr(rt.ResponseTeamRosterAgent, "status_db_path", db_path)
    agent = rt.ResponseTeamRosterAgent(model="test-model")
    for identity, name in (("gil", "Gil"), ("dan", "Dan"), ("yuval", "Yuval")):
        agent.status_store.register_member(identity, name)
    agent.status_store.approve_roster("commander-1")
    return agent


def _append_event(agent, *, area: str, description: str) -> str:
    """Store one core events row in the agent's database and return its id."""
    occurred_at = datetime.now(timezone.utc).isoformat()
    return SQLitePersistence(agent.status_db_path).append_event({
        "classification": "security_incident", "area": area, "description": description,
        "occurred_at": occurred_at, "received_at": occurred_at, "source": "test",
        "sender_identity": "test-sender", "raw_text": description,
    })


def test_a_repeat_request_for_the_same_force_and_area_reuses_the_active_dispatch(tmp_path, monkeypatch):
    """A second report about the same place does not send another unit of a kind already there."""
    agent = _neighboring_forces_agent(tmp_path, monkeypatch)

    first = agent.dispatch_neighboring_force(kind="police", target_area="old_public_building")
    second = agent.dispatch_neighboring_force(kind="police", target_area="old_public_building")
    elsewhere = agent.dispatch_neighboring_force(kind="police", target_area="west_gate")

    rows = agent.dispatch_store.list_dispatches()
    assert "dispatch recorded" in first and "dispatch recorded" in elsewhere
    assert "already dispatched" in second and "no additional unit" in second
    assert len([row for row in rows if row["force_kind"] == "police"]) == 2


def test_a_released_dispatch_is_not_reused(tmp_path, monkeypatch):
    """Once a dispatch is released, a new request for that place sends a fresh unit."""
    agent = _neighboring_forces_agent(tmp_path, monkeypatch)
    agent.dispatch_neighboring_force(kind="yasam", target_area="west_gate", event_id="evt-1")
    agent.release_force_dispatches(event_id="evt-1", reason="report corrected as false")

    again = agent.dispatch_neighboring_force(kind="yasam", target_area="west_gate")

    assert "dispatch recorded" in again
