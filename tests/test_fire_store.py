"""persistence/fire_store.py -- burning/extinguished COP registry and two-day stale-expiry."""

from datetime import datetime, timedelta, timezone

import pytest

from persistence.fire_store import FIRE_ACTIVE_TTL, FireStoreError, open_fire_store


def _store(tmp_path):
    """Store."""
    return open_fire_store(str(tmp_path / "fires.db"))


def test_upsert_burning_creates_then_refreshes_same_area(tmp_path):
    """Upsert burning creates then refreshes same area."""
    store = _store(tmp_path)
    first = datetime(2026, 10, 1, 10, 0, tzinfo=timezone.utc)
    later = first + timedelta(hours=3)

    created = store.upsert_burning(area="pine_ridge", source_event_id="EVT-1", now=first.isoformat())
    updated = store.upsert_burning(area="pine_ridge", source_event_id="EVT-2", now=later.isoformat())

    assert created["status"] == "burning"
    assert updated["fire_id"] == created["fire_id"]
    assert updated["source_event_id"] == "EVT-2"
    assert updated["last_updated"] == later.isoformat()
    assert len(store.list_fires()) == 1


def test_new_burning_fire_after_extinguish_gets_a_new_id(tmp_path):
    """New burning fire after extinguish gets a new id."""
    store = _store(tmp_path)
    first = store.upsert_burning(area="route_444")
    store.extinguish("route_444")
    second = store.upsert_burning(area="route_444")

    assert second["fire_id"] != first["fire_id"]
    assert second["status"] == "burning"
    assert store.get_fire(first["fire_id"])["status"] == "extinguished"


def test_stale_burning_fire_expires_on_read(tmp_path):
    """Stale burning fire expires on read."""
    store = _store(tmp_path)
    started = datetime(2026, 10, 1, 8, 0, tzinfo=timezone.utc)
    created = store.upsert_burning(area="pine_ridge", now=started.isoformat())
    later = started + FIRE_ACTIVE_TTL + timedelta(minutes=1)

    active = store.list_active(now=later.isoformat())
    expired = store.get_fire(created["fire_id"], now=later.isoformat())

    assert active == []
    assert expired["status"] == "extinguished"
    assert expired["expiry_reason"] == "stale"
    assert expired["extinguished_at"] == later.isoformat()


def test_touch_within_ttl_keeps_the_fire_burning(tmp_path):
    """Touch within ttl keeps the fire burning."""
    store = _store(tmp_path)
    started = datetime(2026, 10, 1, 8, 0, tzinfo=timezone.utc)
    created = store.upsert_burning(area="quarry_junction", now=started.isoformat())
    touched_at = started + FIRE_ACTIVE_TTL - timedelta(hours=1)
    store.touch("quarry_junction", now=touched_at.isoformat())
    later = touched_at + FIRE_ACTIVE_TTL - timedelta(minutes=1)

    active = store.list_active(now=later.isoformat())

    assert [row["fire_id"] for row in active] == [created["fire_id"]]


def test_touch_with_no_burning_fire_returns_none(tmp_path):
    """Touch with no burning fire returns none."""
    store = _store(tmp_path)

    assert store.touch("industrial_park") is None


def test_extinguish_without_existing_row_still_records_extinguished(tmp_path):
    """Extinguish without existing row still records extinguished."""
    store = _store(tmp_path)

    row = store.extinguish("chemical_plant", reason="reported")

    assert row["status"] == "extinguished"
    assert row["expiry_reason"] == "reported"
    assert store.list_active() == []


def test_admin_status_edit_is_visible_to_list_active(tmp_path):
    """Admin status edit is visible to list active."""
    store = _store(tmp_path)
    created = store.upsert_burning(area="ornim_street")

    updated = store.admin_update_fire(created["fire_id"], status="extinguished")

    assert updated["status"] == "extinguished"
    assert updated["expiry_reason"] == "admin"
    assert store.list_active() == []


def test_admin_can_reopen_an_extinguished_fire(tmp_path):
    """Admin can reopen an extinguished fire."""
    store = _store(tmp_path)
    created = store.upsert_burning(area="fire_station")
    store.admin_update_fire(created["fire_id"], status="extinguished")

    reopened = store.admin_update_fire(created["fire_id"], status="burning")

    assert reopened["status"] == "burning"
    assert reopened["extinguished_at"] is None
    assert reopened["expiry_reason"] is None
    assert store.list_active()[0]["fire_id"] == created["fire_id"]


def test_admin_cannot_set_a_second_burning_fire_in_the_same_area(tmp_path):
    """Admin cannot set a second burning fire in the same area."""
    store = _store(tmp_path)
    store.upsert_burning(area="pine_ridge")
    other = store.upsert_burning(area="route_444")
    store.extinguish("route_444")

    with pytest.raises(FireStoreError, match="already on record"):
        store.admin_update_fire(other["fire_id"], area="pine_ridge", status="burning")
