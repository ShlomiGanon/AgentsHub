"""Task 50 tests for additive canonical surveillance-seed reconciliation."""

from persistence import open_surveillance_persistence
from persistence.surveillance_store import DEMO_CAMERA_SEED


def _old_camera_seed():
    return DEMO_CAMERA_SEED[:5]


def test_fresh_db_uses_the_current_canonical_camera_seed(tmp_path):
    store = open_surveillance_persistence(str(tmp_path / "surveillance.db"))

    assert tuple(camera["camera_id"] for camera in store.list_cameras()) == tuple(
        record[0] for record in DEMO_CAMERA_SEED
    )
    assert store.get_camera("CAM-08")["name"] == next(
        record[1] for record in DEMO_CAMERA_SEED if record[0] == "CAM-08"
    )


def test_existing_old_inventory_gets_only_missing_canonical_cameras(tmp_path, monkeypatch):
    import persistence.surveillance_store as surveillance_store

    monkeypatch.setattr(surveillance_store, "DEMO_CAMERA_SEED", _old_camera_seed())
    store = open_surveillance_persistence(str(tmp_path / "surveillance.db"))
    monkeypatch.setattr(surveillance_store, "DEMO_CAMERA_SEED", DEMO_CAMERA_SEED)

    result = store.reconcile_camera_seed()

    assert result.examined == len(DEMO_CAMERA_SEED)
    assert result.inserted == 1
    assert result.preserved == 5
    assert result.errors == ()
    assert tuple(camera["camera_id"] for camera in store.list_cameras()) == (
        "CAM-01",
        "CAM-02",
        "CAM-03",
        "CAM-04",
        "CAM-05",
        "CAM-08",
    )


def test_reconciliation_preserves_existing_runtime_state(tmp_path, monkeypatch):
    import persistence.surveillance_store as surveillance_store

    monkeypatch.setattr(surveillance_store, "DEMO_CAMERA_SEED", _old_camera_seed())
    store = open_surveillance_persistence(str(tmp_path / "surveillance.db"))
    store.update_camera_feed("CAM-03", "Authoritative offline observation", status="offline", updated_at="t1")
    store.update_camera_feed("CAM-02", "Authoritative degraded observation", status="degraded", updated_at="t2")
    monkeypatch.setattr(surveillance_store, "DEMO_CAMERA_SEED", DEMO_CAMERA_SEED)

    result = store.reconcile_camera_seed()

    assert result.inserted == 1
    assert store.get_camera("CAM-03")["status"] == "offline"
    assert store.get_camera("CAM-03")["feed_summary"] == "Authoritative offline observation"
    assert store.get_camera("CAM-03")["last_updated"] == "t1"
    assert store.get_camera("CAM-02")["status"] == "degraded"
    assert store.get_camera("CAM-02")["feed_summary"] == "Authoritative degraded observation"
    assert store.get_camera("CAM-02")["last_updated"] == "t2"


def test_reconciliation_is_idempotent(tmp_path, monkeypatch):
    import persistence.surveillance_store as surveillance_store

    monkeypatch.setattr(surveillance_store, "DEMO_CAMERA_SEED", _old_camera_seed())
    store = open_surveillance_persistence(str(tmp_path / "surveillance.db"))
    monkeypatch.setattr(surveillance_store, "DEMO_CAMERA_SEED", DEMO_CAMERA_SEED)

    first = store.reconcile_camera_seed()
    second = store.reconcile_camera_seed()

    assert first.inserted == 1
    assert second.inserted == 0
    assert second.preserved == len(DEMO_CAMERA_SEED)
    assert len(store.list_cameras()) == len(DEMO_CAMERA_SEED)


def test_unknown_persisted_camera_is_preserved(tmp_path, monkeypatch):
    import persistence.surveillance_store as surveillance_store

    unknown = ("CAM-99", "Uncatalogued Camera", "unknown_area", "active", 0, "Runtime-only camera.")
    monkeypatch.setattr(surveillance_store, "DEMO_CAMERA_SEED", DEMO_CAMERA_SEED + (unknown,))
    store = open_surveillance_persistence(str(tmp_path / "surveillance.db"))
    monkeypatch.setattr(surveillance_store, "DEMO_CAMERA_SEED", DEMO_CAMERA_SEED)

    result = store.reconcile_camera_seed()

    assert result.inserted == 0
    assert store.get_camera("CAM-99")["name"] == "Uncatalogued Camera"
    assert len(store.list_cameras()) == len(DEMO_CAMERA_SEED) + 1


def test_demo_seed_can_be_disabled_for_non_demo_configuration(tmp_path):
    store = open_surveillance_persistence(
        str(tmp_path / "production-surveillance.db"),
        seed_demo_data=False,
        seed_profile="production",
    )

    result = store.reconcile_camera_seed()

    assert result.inserted == 0
    assert result.skipped == 1
    assert store.list_cameras() == []
