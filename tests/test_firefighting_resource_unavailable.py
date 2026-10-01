"""profiles/firefighting.py resource-unavailable hook and localized shortage copy."""

from datetime import datetime, timezone

import profiles.firefighting as ff


class _FakeSurveillanceStore:
    def __init__(self, cameras, drones):
        self._cameras = cameras
        self._drones = drones

    def list_cameras(self, area=None):
        return [c for c in self._cameras if area is None or c["area"] == area]

    def list_drones(self, status=None):
        return [d for d in self._drones if status is None or d["status"] == status]


class _FakeCrewStore:
    def __init__(self, snapshot):
        self._snapshot = snapshot

    def availability_snapshot(self, as_of):
        return self._snapshot


class _FakeApparatusStore:
    def __init__(self, rows):
        self._rows = rows

    def list_apparatus(self):
        return self._rows


class _FakeDispatchStore:
    def __init__(self, dispatches):
        self._dispatches = dispatches

    def list_dispatches(self, status=None):
        return [d for d in self._dispatches if status is None or d.get("status") == status]


class _FakeRegistry:
    def __init__(self, agents):
        self._agents = agents

    def get(self, name):
        return self._agents[name]


def _fake_registry(cameras=(), drones=(), roster_snapshot=(), dispatches=(), apparatus=()):
    surveillance_agent = type("S", (), {"surveillance_store": _FakeSurveillanceStore(cameras, drones)})()
    crew_agent = type(
        "C",
        (),
        {
            "status_store": _FakeCrewStore(roster_snapshot),
            "apparatus_store": _FakeApparatusStore(apparatus),
        },
    )()
    neighboring_forces_agent = type("N", (), {"dispatch_store": _FakeDispatchStore(dispatches)})()
    return _FakeRegistry({
        "surveillance_agent": surveillance_agent,
        "team_status_agent": crew_agent,
        "neighboring_forces_agent": neighboring_forces_agent,
    })


def test_describe_resource_unavailable_localizes_english_drone_reason():
    registry = _fake_registry(
        cameras=[{"camera_id": "CAM-03", "area": "pine_ridge", "status": "active"}],
        apparatus=[{"callsign": "Ashed 3", "status": "operational"}],
    )

    fact, alternatives = ff._describe_resource_unavailable(
        "drone",
        "pine_ridge",
        "No ready drones available in fleet for immediate dispatch.",
        registry,
    )

    assert ff._RESOURCE_KIND_LABELS["drone"] in fact
    assert ff._AREA_LABELS["pine_ridge"] in fact
    assert "pine_ridge" not in fact
    assert "No ready drones" not in fact
    assert "CAM-03" in alternatives
    assert "Ashed 3" in alternatives


def test_find_resource_alternatives_reports_mutual_aid_capacity():
    now = datetime.now(timezone.utc).isoformat()
    registry = _fake_registry(
        dispatches=[{"force_kind": "water_tankers", "unit_count": 2, "status": "en_route", "dispatched_at": now}],
    )

    result = ff._find_resource_alternatives("chemical_plant", registry)

    assert f"{ff._RESOURCE_KIND_LABELS['water_tankers']} (0/2)" in result
    assert f"{ff._RESOURCE_KIND_LABELS['police']} (2/2)" in result


def test_force_shortage_text_is_hebrew(tmp_path, monkeypatch):
    monkeypatch.setattr(ff.FirefightingExternalForcesAgent, "dispatch_db_path", str(tmp_path / "forces.db"))
    agent = ff.FirefightingExternalForcesAgent(model="test-model")
    agent.dispatch_neighboring_force(kind="police", target_area="ornim_street", unit_count=2)

    result = agent.dispatch_neighboring_force(kind="police", target_area="pine_ridge", unit_count=1)

    assert "dispatch failed" in result
    signal = agent.take_resource_unavailable_signal()
    assert signal is not None
    resource_kind, area, reason = signal
    assert resource_kind == "police"
    assert area == "pine_ridge"
    assert "רק 0 מתוך 2" in reason
