"""profiles/response_team.py's resource-unavailable wiring: dispatch_neighboring_force's
per-force-kind capacity (2 units, mirroring the drone fleet's own size, busy for
FORCE_BUSY_SECONDS after dispatch regardless of en_route/arrived status) and its "squad" kind
(checked against the roster's own live available-member count, not a fixed pool), plus
_find_resource_alternatives and _describe_resource_unavailable (the profile's resource-
unavailable hook, registered as RESOURCE_UNAVAILABLE_DESCRIPTION, consumed by
orchestrator/flows.py's shared mechanism) -- all user-facing text here is Hebrew, never a raw
internal identifier like "drone"/"east_gate"."""

from datetime import datetime, timedelta, timezone

import profiles.response_team as rt


def _neighboring_forces_agent(tmp_path, monkeypatch):
    """Neighboring forces agent."""
    db_path = str(tmp_path / "response_team.db")
    monkeypatch.setattr(rt, "DB_PATH", db_path)
    # `dispatch_db_path` is a class attribute bound at class-definition time (the same pattern
    # every other DB-backed agent in this codebase uses, e.g. FirefightingCrewStatusAgent's own
    # `status_db_path`) -- monkeypatching the module-level DB_PATH alone doesn't reach it, so it
    # needs its own override too, kept consistent with the same tmp_path.
    monkeypatch.setattr(rt.NeighboringForcesAgent, "dispatch_db_path", db_path)
    return rt.NeighboringForcesAgent(model="test-model")


def _seed_available_member(agent, identity, name):
    """Registers, approves, and records one 'available' attendance response for a roster
    member -- the full sequence availability_snapshot needs to report them as available."""

    now = datetime.now(timezone.utc)
    agent.roster_store.register_member(identity, name, registered_at=now.isoformat())
    agent.roster_store.approve_roster(approved_by="commander-1", approved_at=now.isoformat())
    if agent.roster_store.latest_cycle() is None:
        agent.roster_store.open_cycle(
            now.date().isoformat(), now.isoformat(), (now + timedelta(hours=1)).isoformat()
        )
    agent.roster_store.record_response(
        telegram_identity=identity, source_message_id=f"msg-{identity}", availability="available",
        original_text="available", received_at=now.isoformat(),
    )


# -- dispatch_neighboring_force: per-kind capacity (external forces) -------------------------


def test_dispatch_neighboring_force_succeeds_within_the_pool(tmp_path, monkeypatch):
    """Dispatch neighboring force succeeds within the pool."""
    agent = _neighboring_forces_agent(tmp_path, monkeypatch)

    result = agent.dispatch_neighboring_force(kind="police", target_area="east_gate", unit_count=2)

    assert "dispatch recorded, en route" in result
    assert agent.take_resource_unavailable_signal() is None


def test_dispatch_neighboring_force_signals_resource_unavailable_once_the_pool_is_exhausted(tmp_path, monkeypatch):
    """Dispatch neighboring force signals resource unavailable once the pool is exhausted."""
    agent = _neighboring_forces_agent(tmp_path, monkeypatch)
    agent.dispatch_neighboring_force(kind="police", target_area="east_gate", unit_count=2)

    result = agent.dispatch_neighboring_force(kind="police", target_area="west_gate", unit_count=1)

    assert "dispatch failed" in result
    signal = agent.take_resource_unavailable_signal()
    assert signal is not None
    resource_kind, area, reason = signal
    assert resource_kind == "police"
    assert area == "west_gate"
    assert "רק 0 מתוך 2" in reason  # Hebrew reason -- never a raw English phrase


def test_dispatch_neighboring_force_pool_is_tracked_per_kind_independently(tmp_path, monkeypatch):
    """Dispatch neighboring force pool is tracked per kind independently."""
    agent = _neighboring_forces_agent(tmp_path, monkeypatch)
    agent.dispatch_neighboring_force(kind="police", target_area="east_gate", unit_count=2)

    # A different kind's own pool is untouched by police's exhaustion.
    result = agent.dispatch_neighboring_force(kind="ambulance", target_area="east_gate", unit_count=2)

    assert "dispatch recorded, en route" in result
    assert agent.take_resource_unavailable_signal() is None


def test_dispatch_neighboring_force_unit_stays_busy_after_arrival_not_just_en_route(tmp_path, monkeypatch):
    # Fix (d): capacity is checked against dispatched_at + FORCE_BUSY_SECONDS, regardless of
    # the dispatch's own en_route/arrived status -- arriving on scene doesn't free the unit.
    """Dispatch neighboring force unit stays busy after arrival not just en route."""
    agent = _neighboring_forces_agent(tmp_path, monkeypatch)
    long_ago = datetime.now(timezone.utc) - timedelta(seconds=1)
    # A tiny ETA (same-area dispatch) means this record is already "arrived" by the time we
    # check capacity, yet it must still count as busy since it was dispatched moments ago.
    agent.dispatch_store.dispatch(
        force_kind="police", origin_area="east_gate", target_area="east_gate", unit_count=2,
        eta_seconds=1, dispatched_at=long_ago.isoformat(),
    )
    assert agent.dispatch_store.list_dispatches(status="en_route") == []  # already "arrived"

    result = agent.dispatch_neighboring_force(kind="police", target_area="east_gate", unit_count=1)

    assert "dispatch failed" in result
    assert agent.take_resource_unavailable_signal() is not None


def test_dispatch_neighboring_force_unit_frees_up_after_the_busy_window_elapses(tmp_path, monkeypatch):
    """Dispatch neighboring force unit frees up after the busy window elapses."""
    agent = _neighboring_forces_agent(tmp_path, monkeypatch)
    long_ago = datetime.now(timezone.utc) - timedelta(seconds=rt.FORCE_BUSY_SECONDS + 60)
    agent.dispatch_store.dispatch(
        force_kind="police", origin_area="east_gate", target_area="east_gate", unit_count=2,
        eta_seconds=1, dispatched_at=long_ago.isoformat(),
    )

    result = agent.dispatch_neighboring_force(kind="police", target_area="east_gate", unit_count=2)

    assert "dispatch recorded, en route" in result
    assert agent.take_resource_unavailable_signal() is None


# -- dispatch_neighboring_force: "squad" kind (roster availability, not a fixed pool) ---------


def test_dispatch_neighboring_force_squad_succeeds_when_enough_members_are_available(tmp_path, monkeypatch):
    """Dispatch neighboring force squad succeeds when enough members are available."""
    agent = _neighboring_forces_agent(tmp_path, monkeypatch)
    _seed_available_member(agent, "9000000000000001", "Eli")
    _seed_available_member(agent, "9000000000000002", "Danny")

    result = agent.dispatch_neighboring_force(kind="squad", target_area="east_orchards", unit_count=2)

    assert "dispatch recorded, en route" in result
    assert agent.take_resource_unavailable_signal() is None


def test_dispatch_neighboring_force_squad_signals_resource_unavailable_when_too_few_members(tmp_path, monkeypatch):
    """Dispatch neighboring force squad signals resource unavailable when too few members."""
    agent = _neighboring_forces_agent(tmp_path, monkeypatch)
    _seed_available_member(agent, "9000000000000001", "Eli")

    result = agent.dispatch_neighboring_force(kind="squad", target_area="east_orchards", unit_count=2)

    # agents/neighboring_forces_agent.py's shared dispatch_neighboring_force always lowercases
    # Kind_norm for this prefix.
    assert "squad dispatch failed" in result
    signal = agent.take_resource_unavailable_signal()
    assert signal is not None
    resource_kind, area, reason = signal
    assert resource_kind == "squad_member"
    assert area == "east_orchards"
    assert "רק 1 מתוך 2" in reason  # Hebrew reason


def test_dispatch_neighboring_force_squad_signals_when_no_members_are_registered_at_all(tmp_path, monkeypatch):
    """Dispatch neighboring force squad signals when no members are registered at all."""
    agent = _neighboring_forces_agent(tmp_path, monkeypatch)

    result = agent.dispatch_neighboring_force(kind="squad", target_area="east_orchards", unit_count=1)

    assert "squad dispatch failed" in result
    resource_kind, _area, _reason = agent.take_resource_unavailable_signal()
    assert resource_kind == "squad_member"


def test_dispatch_neighboring_force_still_rejects_an_unrecognized_kind(tmp_path, monkeypatch):
    """Dispatch neighboring force still rejects an unrecognized kind."""
    agent = _neighboring_forces_agent(tmp_path, monkeypatch)

    result = agent.dispatch_neighboring_force(kind="helicopter", target_area="east_gate", unit_count=1)

    assert result.ok is False
    assert "Clarification required" in result.text
    assert "squad" in result.text  # now listed among the valid kinds
    assert agent.take_resource_unavailable_signal() is None


# -- _find_resource_alternatives / _describe_resource_unavailable ----------------------------


class _FakeSurveillanceStore:
    """FakeSurveillanceStore."""
    def __init__(self, cameras, drones):
        """Initialize this test helper."""
        self._cameras = cameras
        self._drones = drones

    def list_cameras(self, area=None):
        """List cameras."""
        return [c for c in self._cameras if area is None or c["area"] == area]

    def list_drones(self, status=None):
        """List drones."""
        return [d for d in self._drones if status is None or d["status"] == status]


class _FakeRosterStore:
    """FakeRosterStore."""
    def __init__(self, snapshot):
        """Initialize this test helper."""
        self._snapshot = snapshot

    def availability_snapshot(self, as_of):
        """Availability snapshot."""
        return self._snapshot


class _FakeDispatchStore:
    """FakeDispatchStore."""
    def __init__(self, dispatches):
        """Initialize this test helper."""
        self._dispatches = dispatches

    def list_dispatches(self, status=None):
        """List dispatches."""
        return [d for d in self._dispatches if status is None or d.get("status") == status]


class _FakeRegistry:
    """FakeRegistry."""
    def __init__(self, agents):
        """Initialize this test helper."""
        self._agents = agents

    def get(self, name):
        """Get."""
        return self._agents[name]


def _fake_registry(cameras=(), drones=(), roster_snapshot=(), dispatches=()):
    """Fake registry."""
    surveillance_agent = type("S", (), {"surveillance_store": _FakeSurveillanceStore(cameras, drones)})()
    roster_agent = type("R", (), {"status_store": _FakeRosterStore(roster_snapshot)})()
    neighboring_forces_agent = type("N", (), {"dispatch_store": _FakeDispatchStore(dispatches)})()
    return _FakeRegistry({
        "surveillance_agent": surveillance_agent,
        "roster_agent": roster_agent,
        "neighboring_forces_agent": neighboring_forces_agent,
    })


_NOW = datetime.now(timezone.utc)
_JUST_DISPATCHED = _NOW.isoformat()


def test_find_resource_alternatives_reports_cameras_covering_the_area():
    """Find resource alternatives reports cameras covering the area."""
    registry = _fake_registry(cameras=[{"camera_id": "CAM-01", "area": "east_gate", "status": "active"}])

    result = rt._find_resource_alternatives("east_gate", registry)

    assert "CAM-01" in result
    assert rt._AREA_LABELS["east_gate"] in result


def test_find_resource_alternatives_reports_no_cameras_when_none_cover_the_area():
    """Find resource alternatives reports no cameras when none cover the area."""
    registry = _fake_registry(cameras=[{"camera_id": "CAM-01", "area": "east_gate", "status": "active"}])

    result = rt._find_resource_alternatives("old_public_building", registry)

    assert rt._AREA_LABELS["old_public_building"] in result
    assert "CAM-01" not in result


def test_find_resource_alternatives_reports_ready_drone_count():
    """Find resource alternatives reports ready drone count."""
    registry = _fake_registry(drones=[{"drone_id": "D1", "status": "ready"}, {"drone_id": "D2", "status": "in_flight"}])

    result = rt._find_resource_alternatives("east_gate", registry)

    assert "1" in result  # one ready drone


def test_find_resource_alternatives_reports_available_roster_members():
    """Find resource alternatives reports available roster members."""
    registry = _fake_registry(roster_snapshot=[
        {"full_name": "Eli", "availability": "available"},
        {"full_name": "Danny", "availability": "unavailable"},
    ])

    result = rt._find_resource_alternatives("east_gate", registry)

    assert "Eli" in result
    assert "Danny" not in result


def test_find_resource_alternatives_reports_force_kinds_with_remaining_capacity():
    """Find resource alternatives reports force kinds with remaining capacity."""
    registry = _fake_registry(dispatches=[
        {"force_kind": "police", "unit_count": 2, "status": "en_route", "dispatched_at": _JUST_DISPATCHED},
        {"force_kind": "ambulance", "unit_count": 1, "status": "en_route", "dispatched_at": _JUST_DISPATCHED},
    ])

    result = rt._find_resource_alternatives("east_gate", registry)

    assert f"{rt._RESOURCE_KIND_LABELS['police']} (0/2)" in result
    assert f"{rt._RESOURCE_KIND_LABELS['ambulance']} (1/2)" in result
    assert f"{rt._RESOURCE_KIND_LABELS['k9']} (2/2)" in result


def test_find_resource_alternatives_ignores_a_dispatch_outside_the_busy_window():
    """Find resource alternatives ignores a dispatch outside the busy window."""
    long_ago = (_NOW - timedelta(seconds=rt.FORCE_BUSY_SECONDS + 60)).isoformat()
    registry = _fake_registry(dispatches=[
        {"force_kind": "police", "unit_count": 2, "status": "arrived", "dispatched_at": long_ago},
    ])

    result = rt._find_resource_alternatives("east_gate", registry)

    assert f"{rt._RESOURCE_KIND_LABELS['police']} (2/2)" in result  # fully free again


def test_describe_resource_unavailable_returns_a_hebrew_fact_and_alternatives():
    """Describe resource unavailable returns a hebrew fact and alternatives."""
    registry = _fake_registry(cameras=[{"camera_id": "CAM-01", "area": "east_gate", "status": "active"}])

    fact, alternatives = rt._describe_resource_unavailable("drone", "east_gate", "no units ready", registry)

    assert rt._RESOURCE_KIND_LABELS["drone"] in fact
    assert rt._AREA_LABELS["east_gate"] in fact
    assert "east_gate" not in fact  # never the raw internal area identifier
    assert "CAM-01" in alternatives


def test_describe_resource_unavailable_replaces_the_english_drone_fleet_reason():
    """Describe resource unavailable replaces the english drone fleet reason."""
    registry = _fake_registry()

    fact, _alternatives = rt._describe_resource_unavailable(
        "drone",
        "east_gate",
        "No ready drones available in fleet for immediate dispatch.",
        registry,
    )

    assert "No ready drones" not in fact
    assert "אין רחפנים מוכנים" in fact or "no ready drones available for immediate dispatch" in fact


def test_dispatch_squad_wraps_kind_squad(tmp_path, monkeypatch):
    """Dispatch squad wraps kind squad."""
    agent = _neighboring_forces_agent(tmp_path, monkeypatch)
    result = agent.dispatch_squad(target_area="east_orchards", unit_count=1)

    assert "squad dispatch failed" in result
    resource_kind, area, _reason = agent.take_resource_unavailable_signal()
    assert resource_kind == "squad_member"
    assert area == "east_orchards"
