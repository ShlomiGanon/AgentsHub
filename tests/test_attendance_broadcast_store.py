"""Attendance broadcast persistence."""

from persistence import open_response_team_roster_store, open_team_status_persistence


def _open_cycle(store):
    """Open cycle."""
    store.register_member("101", "Alex Cohen", "2026-09-03T05:00:00+00:00")
    store.approve_roster("commander-1", "2026-09-03T05:00:00+00:00")
    return store.open_cycle("2026-09-03", "2026-09-03T05:00:00+00:00", "2026-09-03T06:00:00+00:00")


def test_team_status_broadcast_claim_is_one_shot(tmp_path):
    """Team status broadcast claim is one shot."""
    store = open_team_status_persistence(str(tmp_path / "crew.db"))
    cycle = _open_cycle(store)
    store.request_broadcast(cycle.cycle_key)
    claimed = store.claim_broadcast()
    assert claimed["cycle_key"] == "2026-09-03"
    assert "Alex Cohen" in claimed["members_required"]
    assert store.claim_broadcast() is None


def test_response_team_broadcast_claim_is_one_shot(tmp_path):
    """Response team broadcast claim is one shot."""
    store = open_response_team_roster_store(str(tmp_path / "roster.db"))
    cycle = _open_cycle(store)
    store.request_broadcast(cycle.cycle_key)
    claimed = store.claim_broadcast()
    assert claimed["cycle_key"] == "2026-09-03"
    assert store.claim_broadcast() is None
