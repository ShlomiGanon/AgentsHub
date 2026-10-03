"""FirefightingCrewStatusAgent fires-registry tools -- COP write/read and two-day expiry."""

from datetime import datetime, timedelta, timezone

import profiles.firefighting as ff
from persistence.fire_store import FIRE_ACTIVE_TTL


def _agent(tmp_path, monkeypatch):
    """Agent."""
    monkeypatch.setattr(ff.FirefightingCrewStatusAgent, "status_db_path", str(tmp_path / "crew.db"))
    monkeypatch.setattr(ff, "FIREFIGHTING_APPARATUS_DB_PATH", str(tmp_path / "apparatus.db"))
    monkeypatch.setattr(ff, "FIREFIGHTING_FIRES_DB_PATH", str(tmp_path / "fires.db"))
    monkeypatch.setattr(ff, "DB_PATH", str(tmp_path / "firefighting_history.db"))
    return ff.FirefightingCrewStatusAgent(model="test-model")


def test_record_burning_then_list_active_fires(tmp_path, monkeypatch):
    """Record burning then list active fires."""
    agent = _agent(tmp_path, monkeypatch)

    recorded = agent.record_fire_status(area="pine_ridge", status="burning")
    listing = agent.list_active_fires()

    assert "BURNING" in recorded
    assert "pine_ridge" in listing
    assert "No burning fires" not in listing


def test_extinguished_report_drops_the_fire_from_active_list(tmp_path, monkeypatch):
    """Extinguished report drops the fire from active list."""
    agent = _agent(tmp_path, monkeypatch)
    agent.record_fire_status(area="route_444", status="burning")

    result = agent.record_fire_status(area="route_444", status="extinguished")
    listing = agent.list_active_fires()

    assert "EXTINGUISHED" in result
    assert listing == "No burning fires are currently on record."


def test_stale_fire_is_not_listed_as_burning(tmp_path, monkeypatch):
    """Stale fire is not listed as burning."""
    agent = _agent(tmp_path, monkeypatch)
    started = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)
    created = agent.fire_store.upsert_burning(area="quarry_junction", now=started.isoformat())
    later = started + FIRE_ACTIVE_TTL + timedelta(hours=1)
    agent.fire_store.list_active(now=later.isoformat())

    listing = agent.list_active_fires()
    expired = agent.fire_store.get_fire(created["fire_id"], now=later.isoformat())

    assert listing == "No burning fires are currently on record."
    assert expired["status"] == "extinguished"
    assert expired["expiry_reason"] == "stale"


def test_touch_refreshes_a_burning_fire(tmp_path, monkeypatch):
    """Touch refreshes a burning fire."""
    agent = _agent(tmp_path, monkeypatch)
    agent.record_fire_status(area="industrial_park", status="burning")

    result = agent.touch_active_fire(area="industrial_park")

    assert "still BURNING" in result
    assert "industrial_park" in result


def test_touch_without_a_burning_fire_is_harmless(tmp_path, monkeypatch):
    """Touch without a burning fire is harmless."""
    agent = _agent(tmp_path, monkeypatch)

    result = agent.touch_active_fire(area="chemical_plant")

    assert "No burning fire is currently on record" in result


def test_list_active_fires_can_filter_by_area(tmp_path, monkeypatch):
    """List active fires can filter by area."""
    agent = _agent(tmp_path, monkeypatch)
    agent.record_fire_status(area="pine_ridge", status="burning")
    agent.record_fire_status(area="route_444", status="burning")

    listing = agent.list_active_fires(area="pine_ridge")

    assert "pine_ridge" in listing
    assert "route_444" not in listing
