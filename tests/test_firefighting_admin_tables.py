"""`profiles/firefighting.py`'s real `ADMIN_TABLES` wiring (docs/Admin_Tables_Plan.md phases 4,
5, 7, 8) -- drones (via the shared surveillance store, phase 3's code-sharing fix), crew-shift
attendance, and friendly forces (via the newly-extracted shared `NeighboringForcesAgent` base,
phase 7's tool-unification), end to end through the Flask admin routes against firefighting's
own real, separate stores. Mirrors tests/test_response_team_admin_tables.py's structure and its
same-shared-persistent-DB precautions (unique IDs/markers per test, never reading/mutating
whatever real rows already exist)."""

import dataclasses
import re
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from agents.runtime import build_agent_registry
from api.app import build_app
from config.base import TierModel
from profiles import build_area_registry, build_event_type_registry
from profiles.loader import load_profile
from protocols.loader import ProtocolSet
from tests.api_fakes import build_context

CORE_MODEL = TierModel(model="openai/test-core-model", api_key="test-key")
SUB_MODEL = TierModel(model="openai/test-sub-model", api_key="test-key")

ADMIN_USERNAME = "test-admin"
ADMIN_PASSWORD = "test-admin-password"


@pytest.fixture
def teardown_ctx():
    contexts = []
    yield contexts
    for ctx in contexts:
        ctx.queue.stop()
        ctx.deps.persistence.close()


@pytest.fixture
def _admin_env(monkeypatch):
    monkeypatch.setenv("ADMIN_USERNAME", ADMIN_USERNAME)
    monkeypatch.setenv("ADMIN_PASSWORD", ADMIN_PASSWORD)
    monkeypatch.setenv("ADMIN_SESSION_SECRET", "test-admin-session-secret")
    monkeypatch.setenv("BOT_TOKEN", "test-admin-tables-token")


def _fire_ctx(tmp_path, teardown_ctx):
    loaded = load_profile("profiles.firefighting", CORE_MODEL, SUB_MODEL)
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
    client.post("/admin/login", data={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD}, follow_redirects=False)


def _csrf_token(client):
    resp = client.get("/admin/")
    match = re.search(r'name="csrf_token" value="([^"]+)"', resp.get_data(as_text=True))
    return match.group(1)


def _insert_test_drone(store, *, status="ready"):
    drone_id = f"TEST-DRONE-{uuid.uuid4().hex[:8]}"
    conn = sqlite3.connect(store.db_path)
    conn.execute(
        "INSERT INTO drones (drone_id, callsign, model, status, battery_percent, current_area, assigned_mission_id, last_updated) "
        "VALUES (?, ?, ?, ?, ?, ?, NULL, ?)",
        (drone_id, f"TestCallsign-{drone_id[-8:]}", "Test Model", status, 80, "fire_station", "2026-09-29T00:00:00+00:00"),
    )
    conn.commit()
    conn.close()
    return drone_id


# -- Drone code sharing (phase 3) + admin page (phase 4) -----------------------------------


def test_firefighting_drone_edit_writes_through_the_same_shared_store(tmp_path, teardown_ctx, _admin_env):
    ctx = _fire_ctx(tmp_path, teardown_ctx)
    surveillance = ctx.deps.registry.get("surveillance_agent")
    drone_id = _insert_test_drone(surveillance.surveillance_store)
    drone = surveillance.surveillance_store.get_drone(drone_id)
    client = build_app(ctx).test_client()
    _login(client)
    token = _csrf_token(client)

    client.post(
        f"/admin/tables/drones/edit/{drone_id}",
        data={"csrf_token": token, "callsign": drone["callsign"], "model": drone["model"],
              "status": "maintenance", "battery_percent": "55", "current_area": drone["current_area"]},
        follow_redirects=False,
    )

    updated = surveillance.surveillance_store.get_drone(drone_id)
    assert updated["status"] == "maintenance"
    assert updated["battery_percent"] == 55


def test_firefighting_drone_recall_now_works_via_the_shared_base_tool(tmp_path, teardown_ctx, _admin_env):
    """Phase 3's own point: firefighting previously had no way to recall a drone at all, by
    any means -- return_drone_to_base (agents/surveillance_agent.py's shared base tool) now
    works for it because FirefightingSurveillanceAgent opens the same generic,
    home-area-aware store response_team already used."""
    ctx = _fire_ctx(tmp_path, teardown_ctx)
    surveillance = ctx.deps.registry.get("surveillance_agent")
    drone_id = _insert_test_drone(surveillance.surveillance_store, status="ready")
    surveillance.dispatch_drone_to_area("chemical_plant", "test dispatch", specific_drone_id=drone_id)
    dispatched = surveillance.surveillance_store.get_drone(drone_id)
    assert dispatched["status"] == "in_flight"

    result_text = surveillance.return_drone_to_base(drone_id)

    recalled = surveillance.surveillance_store.get_drone(drone_id)
    assert recalled["status"] == "ready"
    assert recalled["assigned_mission_id"] is None
    assert "return" in result_text.lower() or "recall" in result_text.lower() or recalled["current_area"] in result_text


# -- Crew-shift attendance (phase 5) --------------------------------------------------------


def test_firefighting_attendance_reason_edit_writes_through(tmp_path, teardown_ctx, _admin_env):
    ctx = _fire_ctx(tmp_path, teardown_ctx)
    crew = ctx.deps.registry.get("team_status_agent")
    identity = f"fire-member-{uuid.uuid4().hex[:8]}"
    crew.status_store.register_member(identity, "Test Firefighter")
    crew.status_store.approve_roster(approved_by="test-commander")
    # Anchored on real current time, not a fixed date -- record_response always consults
    # latest_cycle() (max opened_at), and this runs against firefighting's own real, shared
    # DB_PATH (see _insert_test_drone's docstring), so a fixed opened_at could lose a tie to a
    # leftover cycle from an earlier run.
    now = datetime.now(timezone.utc)
    crew.status_store.open_cycle(
        f"fire-test-cycle-{uuid.uuid4().hex[:8]}", opened_at=now.isoformat(),
        deadline_at=(now + timedelta(hours=1)).isoformat(),
    )
    response = crew.status_store.record_response(
        telegram_identity=identity, source_message_id=f"fire-msg-{uuid.uuid4().hex[:8]}",
        availability="unavailable", reason="original reason", original_text="x",
        received_at=(now + timedelta(seconds=1)).isoformat(),
    )
    client = build_app(ctx).test_client()
    _login(client)
    token = _csrf_token(client)

    client.post(
        f"/admin/tables/attendance/edit/{response['response_id']}",
        data={"csrf_token": token, "availability": "unavailable", "reason": "corrected reason"},
        follow_redirects=False,
    )

    updated = crew.status_store.get_response(response["response_id"])
    assert updated["reason"] == "corrected reason"


# -- Friendly forces: single unified tool + admin page (phases 7, 8) -----------------------


def test_firefighting_dispatch_neighboring_force_replaces_the_four_old_named_tools(tmp_path, teardown_ctx, _admin_env):
    """Phase 7: dispatch_police/dispatch_ambulance/dispatch_water_tankers/dispatch_aircraft no
    longer exist as separate tools -- one parameterized dispatch_neighboring_force(kind=...)
    tool covers all four, exactly like response_team."""
    ctx = _fire_ctx(tmp_path, teardown_ctx)
    forces = ctx.deps.registry.get("neighboring_forces_agent")
    assert not hasattr(forces, "dispatch_police")
    assert not hasattr(forces, "dispatch_water_tankers")
    # Clear this kind's busy-window capacity first: this test genuinely needs the real
    # capacity-checked dispatch_neighboring_force call to succeed (that's the point of the
    # test), and FORCE_POOL_SIZE=2 within a 2-hour busy window is easily exhausted by this
    # same test's own repeated runs against the shared, persistent DB_PATH (see
    # test_response_team_admin_tables.py's _insert_test_drone docstring for the same concern).
    conn = sqlite3.connect(forces.dispatch_store.db_path)
    conn.execute("DELETE FROM neighboring_force_dispatches WHERE force_kind = 'water_tankers'")
    conn.commit()
    conn.close()

    marker = f"admin-table-test-{uuid.uuid4().hex[:8]}"
    result_text = forces.dispatch_neighboring_force(kind="water_tankers", target_area="chemical_plant", unit_count=1, note=marker)

    assert "fail" not in result_text.lower()
    dispatch = next(d for d in forces.dispatch_store.list_dispatches() if d["note"] == marker)
    assert dispatch["force_kind"] == "water_tankers"
    assert dispatch["origin_area"] == "chemical_plant"  # profiles/firefighting.py's own FORCE_BASES (section 3.1)


def test_firefighting_force_dispatch_status_edit_is_immediately_visible_to_the_list_tool(tmp_path, teardown_ctx, _admin_env):
    ctx = _fire_ctx(tmp_path, teardown_ctx)
    forces = ctx.deps.registry.get("neighboring_forces_agent")
    # Inserted directly via the store (bypassing dispatch_neighboring_force's own capacity
    # check) -- this test only needs an existing row to edit, not to exercise capacity.
    dispatch = forces.dispatch_store.dispatch(
        force_kind="police", origin_area="ornim_street", target_area="ornim_street",
        unit_count=1, eta_seconds=180, note=f"admin-table-test-{uuid.uuid4().hex[:8]}",
    )
    client = build_app(ctx).test_client()
    _login(client)
    token = _csrf_token(client)

    client.post(
        f"/admin/tables/forces/edit/{dispatch['request_id']}",
        data={"csrf_token": token, "force_kind": dispatch["force_kind"], "unit_count": "1",
              "status": "arrived", "note": dispatch["note"]},
        follow_redirects=False,
    )

    updated = forces.dispatch_store.get_dispatch(dispatch["request_id"])
    assert updated["status"] == "arrived"
    listing = forces.list_neighboring_force_dispatches(status="arrived")
    assert dispatch["request_id"] in listing
