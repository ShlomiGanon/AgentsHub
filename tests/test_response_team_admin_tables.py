"""`profiles/response_team.py`'s real `ADMIN_TABLES` wiring -- drones, standby-squad attendance, and friendly forces, end to end through the Flask
admin routes against the profile's own real stores. The generic mechanism itself (rendering,
form validation, CSRF) is already covered by tests/test_admin_tables.py against a fake table;
this file only proves the real wrappers/cascades for this profile's three tables."""

import dataclasses
import re
from datetime import datetime, timedelta, timezone

import pytest

from agents.runtime import build_agent_registry
from api.app import build_app
from config.base import TierModel
from profiles import build_area_registry, build_event_type_registry
from profiles.loader import load_profile
from protocols.loader import ProtocolSet
from tests.api_fakes import COMMANDER_IDENTITY, build_context, teardown_ctx

CORE_MODEL = TierModel(model="openai/test-core-model", api_key="test-key")
SUB_MODEL = TierModel(model="openai/test-sub-model", api_key="test-key")

ADMIN_USERNAME = "test-admin"
ADMIN_PASSWORD = "test-admin-password"



@pytest.fixture
def _admin_env(monkeypatch):
    """Admin env."""
    monkeypatch.setenv("ADMIN_USERNAME", ADMIN_USERNAME)
    monkeypatch.setenv("ADMIN_PASSWORD", ADMIN_PASSWORD)
    monkeypatch.setenv("ADMIN_SESSION_SECRET", "test-admin-session-secret")
    monkeypatch.setenv("BOT_TOKEN", "test-admin-tables-token")


def _rt_ctx(tmp_path, teardown_ctx):
    """A real ApiContext wired to profiles.response_team's own real registry/stores (same
    technique tests/test_operational_scenarios.py's own _operational_ctx uses), with
    ctx.loaded_profile.admin_tables set to the profile's real ADMIN_TABLES declaration so the
    admin routes actually find them."""

    loaded = load_profile("profiles.response_team", CORE_MODEL, SUB_MODEL)
    ctx = build_context(tmp_path, admin_tables=loaded.admin_tables)
    history_agent = ctx.deps.registry.get("history_agent")
    registry = build_agent_registry({"history_agent": history_agent}, list(loaded.agents))
    new_deps = dataclasses.replace(
        ctx.deps,
        registry=registry,
        protocol_set=ProtocolSet(protocols=loaded.protocols),
        event_type_registry=build_event_type_registry(loaded),
        area_registry=build_area_registry(loaded),
    )
    ctx = dataclasses.replace(ctx, deps=new_deps)
    teardown_ctx.append(ctx)
    return ctx


def _login(client):
    """Login."""
    client.post("/admin/login", data={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD}, follow_redirects=False)
    setup = client.get("/admin/acting-identity")
    match = re.search(r'name="csrf_token" value="([^"]+)"', setup.get_data(as_text=True))
    client.post(
        "/admin/acting-identity",
        data={"csrf_token": match.group(1), "api_identity": COMMANDER_IDENTITY},
        follow_redirects=False,
    )


def _csrf_token(client):
    """Csrf token."""
    resp = client.get("/admin/")
    match = re.search(r'name="csrf_token" value="([^"]+)"', resp.get_data(as_text=True))
    return match.group(1)


# -- Drones ------------------------------------------------------------------------------


import sqlite3
import uuid


def _insert_test_drone(store, *, status="ready"):
    """A self-contained, uniquely-IDed drone row -- these admin-table tests run against
    profiles/response_team.py's own real, shared DB_PATH (same convention
    tests/test_operational_scenarios.py already uses), so each test creates and only ever
    touches its own row rather than reading/mutating whatever real fleet data already exists
    there."""
    drone_id = f"TEST-DRONE-{uuid.uuid4().hex[:8]}"
    conn = sqlite3.connect(store.db_path)
    conn.execute(
        "INSERT INTO drones (drone_id, callsign, model, status, battery_percent, current_area, assigned_mission_id, last_updated) "
        "VALUES (?, ?, ?, ?, ?, ?, NULL, ?)",
        (drone_id, f"TestCallsign-{drone_id[-8:]}", "Test Model", status, 80, "central_hub", "2026-09-29T00:00:00+00:00"),
    )
    conn.commit()
    conn.close()
    return drone_id


def test_drone_battery_and_area_edit_is_immediately_visible_to_the_real_tool(tmp_path, teardown_ctx, _admin_env):
    """Drone battery and area edit is immediately visible to the real tool."""
    ctx = _rt_ctx(tmp_path, teardown_ctx)
    surveillance = ctx.deps.registry.get("surveillance_agent")
    drone_id = _insert_test_drone(surveillance.surveillance_store)
    drone = surveillance.surveillance_store.get_drone(drone_id)
    client = build_app(ctx).test_client()
    _login(client)
    token = _csrf_token(client)

    client.post(
        f"/admin/tables/drones/edit/{drone_id}",
        data={"csrf_token": token, "callsign": drone["callsign"], "model": drone["model"],
              "status": "maintenance", "battery_percent": "42", "current_area": drone["current_area"]},
        follow_redirects=False,
    )

    updated = surveillance.surveillance_store.get_drone(drone_id)
    assert updated["status"] == "maintenance"
    assert updated["battery_percent"] == 42
    # A real tool call reflects the edit immediately, no restart.
    report = surveillance.get_drone_fleet_status(status_filter="maintenance")
    assert drone_id in report or drone["callsign"] in report


def test_drone_status_edit_away_from_in_flight_aborts_the_linked_mission(tmp_path, teardown_ctx, _admin_env):
    """Drone status edit away from in flight aborts the linked mission."""
    ctx = _rt_ctx(tmp_path, teardown_ctx)
    surveillance = ctx.deps.registry.get("surveillance_agent")
    store = surveillance.surveillance_store
    drone_id = _insert_test_drone(store, status="ready")
    surveillance.dispatch_drone_to_area("east_gate", "admin-table test dispatch", specific_drone_id=drone_id)
    ready = store.get_drone(drone_id)
    mission_id = next(m["mission_id"] for m in store.get_active_missions() if m["drone_id"] == drone_id)
    client = build_app(ctx).test_client()
    _login(client)
    token = _csrf_token(client)

    client.post(
        f"/admin/tables/drones/edit/{drone_id}",
        data={"csrf_token": token, "callsign": ready["callsign"], "model": ready["model"],
              "status": "ready", "battery_percent": "80", "current_area": "central_hub"},
        follow_redirects=False,
    )

    drone_after = store.get_drone(drone_id)
    assert drone_after["status"] == "ready"
    assert drone_after["assigned_mission_id"] is None
    conn = sqlite3.connect(store.db_path)
    mission_status = conn.execute(
        "SELECT status FROM drone_missions WHERE mission_id = ?", (mission_id,)
    ).fetchone()[0]
    conn.close()
    assert mission_status == "aborted"


def test_drone_edit_rejects_in_flight_status_with_a_nonexistent_mission_id(tmp_path, teardown_ctx, _admin_env):
    """Drone edit rejects in flight status with a nonexistent mission id."""
    ctx = _rt_ctx(tmp_path, teardown_ctx)
    surveillance = ctx.deps.registry.get("surveillance_agent")
    store = surveillance.surveillance_store
    drone_id = _insert_test_drone(store, status="ready")
    ready = store.get_drone(drone_id)
    client = build_app(ctx).test_client()
    _login(client)
    token = _csrf_token(client)

    client.post(
        f"/admin/tables/drones/edit/{ready['drone_id']}",
        data={"csrf_token": token, "callsign": ready["callsign"], "model": ready["model"],
              "status": "in_flight", "battery_percent": "80", "current_area": "east_gate",
              "assigned_mission_id": "does-not-exist"},
        follow_redirects=False,
    )

    unchanged = store.get_drone(ready["drone_id"])
    assert unchanged["status"] == "ready"  # the write was rejected, nothing committed


# -- Standby-squad attendance --------------------------------------------------------------


def _open_roster_and_cycle(store, *, pending: bool):
    """Anchored on the real current time, not a fixed date: these tests run against
    profiles/response_team.py's own real, shared DB_PATH (see _insert_test_drone), and
    `record_response` always looks up `latest_cycle()` (the cycle with the max `opened_at`) --
    a fixed-date `opened_at` could tie with, or lose to, a leftover cycle from an earlier test
    run. Anchoring on `datetime.now()` guarantees this call's own cycle is always the newest.
    Returns (member_identity, received_at) -- `received_at` is placed on whichever side of
    `deadline_at` is still opened so leftover cycles stay older than this one."""

    now = datetime.now(timezone.utc)
    opened_at = now.isoformat()
    identity = f"member-{uuid.uuid4().hex[:8]}"
    store.register_member(identity, "Test Member")
    store.approve_roster(approved_by="test-commander")
    if pending:
        deadline_at = (now + timedelta(seconds=1)).isoformat()
        received_at = (now + timedelta(hours=1)).isoformat()  # after the deadline -> "pending"
    else:
        deadline_at = (now + timedelta(hours=1)).isoformat()
        received_at = (now + timedelta(seconds=1)).isoformat()  # before the deadline -> "accepted"
    store.open_cycle(f"test-cycle-{uuid.uuid4().hex[:8]}", opened_at=opened_at, deadline_at=deadline_at)
    return identity, received_at


def test_attendance_reason_edit_writes_through(tmp_path, teardown_ctx, _admin_env):
    """Attendance reason edit writes through."""
    ctx = _rt_ctx(tmp_path, teardown_ctx)
    roster = ctx.deps.registry.get("roster_agent")
    identity, received_at = _open_roster_and_cycle(roster.status_store, pending=False)
    response = roster.status_store.record_response(
        telegram_identity=identity, source_message_id=f"msg-{uuid.uuid4().hex[:8]}", availability="unavailable",
        reason="original reason", original_text="x", received_at=received_at,
    )
    client = build_app(ctx).test_client()
    _login(client)
    listed = client.get("/admin/tables/attendance")
    assert listed.status_code == 200
    assert response["response_id"] in listed.get_data(as_text=True)
    token = _csrf_token(client)

    client.post(
        f"/admin/tables/attendance/edit/{response['response_id']}",
        data={"csrf_token": token, "availability": "unavailable", "reason": "corrected reason"},
        follow_redirects=False,
    )

    updated = roster.status_store.get_response(response["response_id"])
    assert updated["reason"] == "corrected reason"


def test_attendance_approval_status_edit_is_immediately_visible_to_report_team_availability(tmp_path, teardown_ctx, _admin_env):
    """Attendance approval status edit is immediately visible to report team availability."""
    ctx = _rt_ctx(tmp_path, teardown_ctx)
    roster = ctx.deps.registry.get("roster_agent")
    identity, received_at = _open_roster_and_cycle(roster.status_store, pending=False)
    response = roster.status_store.record_response(
        telegram_identity=identity, source_message_id=f"msg-{uuid.uuid4().hex[:8]}", availability="unavailable",
        reason="late", original_text="x", received_at=received_at,
        unavailable_until=(datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
    )
    assert response["approval_status"] == "accepted"
    client = build_app(ctx).test_client()
    _login(client)
    token = _csrf_token(client)

    client.post(
        f"/admin/tables/attendance/edit/{response['response_id']}",
        data={"csrf_token": token, "availability": "unavailable", "approval_status": "rejected"},
        follow_redirects=False,
    )

    updated = roster.status_store.get_response(response["response_id"])
    assert updated["approval_status"] == "rejected"
    assert updated["reviewed_by"]
    assert updated["reviewed_at"]
    snapshot_text = roster.report_team_availability()
    assert "Test Member: awaiting response" in snapshot_text


# -- Friendly forces -------------------------------------------------------------------------


def test_force_dispatch_status_edit_is_immediately_visible_to_the_list_tool(tmp_path, teardown_ctx, _admin_env):
    """Force dispatch status edit is immediately visible to the list tool."""
    ctx = _rt_ctx(tmp_path, teardown_ctx)
    forces = ctx.deps.registry.get("neighboring_forces_agent")
    # Inserted directly via the store (bypassing dispatch_neighboring_force's own busy-window
    # capacity check) -- this test only needs an existing row to edit, not to exercise capacity,
    # and FORCE_POOL_SIZE=2 within a 2-hour busy window is easily exhausted by this same test's
    # own repeated runs against the shared, persistent DB_PATH (see _insert_test_drone).
    dispatch = forces.dispatch_store.dispatch(
        force_kind="police", origin_area="east_orchards", target_area="east_orchards",
        unit_count=1, eta_seconds=180, note=f"admin-table-test-{uuid.uuid4().hex[:8]}",
    )
    client = build_app(ctx).test_client()
    _login(client)
    token = _csrf_token(client)

    client.post(
        f"/admin/tables/forces/edit/{dispatch['request_id']}",
        data={"csrf_token": token, "force_kind": dispatch["force_kind"], "unit_count": "1",
              "status": "arrived", "note": "admin-corrected"},
        follow_redirects=False,
    )

    updated = forces.dispatch_store.get_dispatch(dispatch["request_id"])
    assert updated["status"] == "arrived"
    listing = forces.list_neighboring_force_dispatches(status="arrived")
    assert dispatch["request_id"] in listing
