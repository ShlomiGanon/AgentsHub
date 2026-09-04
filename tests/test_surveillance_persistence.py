import pytest

from persistence import (
    SurveillancePersistenceError,
    open_surveillance_persistence,
)


def test_surveillance_persistence_initialization_and_seeding(tmp_path):
    db_file = str(tmp_path / "surveillance.db")
    store = open_surveillance_persistence(db_file)

    cameras = store.list_cameras()
    assert len(cameras) >= 5
    for camera in cameras:
        assert camera["camera_id"].startswith("CAM-")
        assert camera["status"] == "active"
        assert camera["feed_summary"]
        assert camera["last_updated"]

    drones = store.list_drones()
    assert len(drones) >= 3
    ready_drones = [d for d in drones if d["status"] == "ready"]
    assert len(ready_drones) >= 2
    for drone in drones:
        assert drone["drone_id"].startswith("DRONE-")
        assert 0 <= drone["battery_percent"] <= 100
        assert drone["last_updated"]


def test_camera_listing_and_feed_update(tmp_path):
    db_file = str(tmp_path / "surveillance.db")
    store = open_surveillance_persistence(db_file)

    north_cams = store.list_cameras(area="north_gate")
    assert len(north_cams) >= 1
    cam = north_cams[0]

    updated = store.update_camera_feed(
        cam["camera_id"],
        "Suspicious white pickup truck stationary near gate barrier.",
        status="active",
    )
    assert "Suspicious white pickup truck" in updated["feed_summary"]

    retrieved = store.get_camera(cam["camera_id"])
    assert retrieved is not None
    assert "Suspicious white pickup truck" in retrieved["feed_summary"]


def test_drone_dispatch_lifecycle_and_state_transitions(tmp_path):
    db_file = str(tmp_path / "surveillance.db")
    store = open_surveillance_persistence(db_file)

    initial_ready_count = len(store.list_drones(status="ready"))
    assert initial_ready_count >= 2

    # Dispatch drone
    mission = store.dispatch_drone(
        target_area="north_gate",
        incident_description="Investigate suspicious vehicle at North Gate",
        mission_type="intercept",
        dispatched_by="commander_1",
    )

    mission_id = mission["mission_id"]
    assert mission_id.startswith("MSN-")
    assert mission["status"] == "dispatched"
    assert mission["target_area"] == "north_gate"
    assert mission["eta_seconds"] > 0

    drone = mission["drone"]
    # Verify drone state changed from ready to in_flight!
    assert drone["status"] == "in_flight"
    assert drone["assigned_mission_id"] == mission_id
    assert drone["current_area"] == "north_gate"

    # Verify ready count decreased
    after_dispatch_ready = len(store.list_drones(status="ready"))
    assert after_dispatch_ready == initial_ready_count - 1

    # Verify active missions includes this mission
    active = store.get_active_missions()
    assert any(m["mission_id"] == mission_id for m in active)

    # Transition mission to on_station
    updated_mission = store.update_mission_status(mission_id, "on_station", notes="Arrived on scene. Drone hovering.")
    assert updated_mission["status"] == "on_station"

    # Complete mission and verify drone returns to ready
    completed_mission = store.update_mission_status(mission_id, "completed", notes="Target vehicle cleared.")
    assert completed_mission["status"] == "completed"

    drone_after = store.get_drone(drone["drone_id"])
    assert drone_after is not None
    assert drone_after["status"] == "ready"
    assert drone_after["assigned_mission_id"] is None


def test_drone_dispatch_exhaustion_raises_error(tmp_path):
    db_file = str(tmp_path / "surveillance.db")
    store = open_surveillance_persistence(db_file)

    ready_drones = store.list_drones(status="ready")
    # Dispatch all ready drones
    for i, d in enumerate(ready_drones):
        store.dispatch_drone(
            target_area="south_sector",
            incident_description=f"Patrol event {i}",
            specific_drone_id=d["drone_id"],
        )

    # Now attempting to dispatch another drone must raise SurveillancePersistenceError
    with pytest.raises(SurveillancePersistenceError) as exc_info:
        store.dispatch_drone(
            target_area="east_fence",
            incident_description="Should fail, no drones left",
        )
    assert "No ready drones available" in str(exc_info.value)


def test_surveillance_overview(tmp_path):
    db_file = str(tmp_path / "surveillance.db")
    store = open_surveillance_persistence(db_file)

    overview = store.surveillance_overview()
    assert overview["active_camera_count"] >= 5
    assert overview["ready_drone_count"] >= 2
    assert overview["in_flight_drone_count"] == 0
    assert overview["as_of"]
