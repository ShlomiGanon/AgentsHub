"""Task 59 — committed manpower and resources reach the rendered operational picture.

Roster attendance and reported manpower are different facts. The typed renderer
carried only the attendance counts, so a commander asking about forces *and
vehicles* was answered with neither the committed headcount nor the vehicles,
even though both were already on the snapshot and in the compact context.
"""

import pytest

from messages import get_catalog, get_current_catalog, set_current_catalog
from orchestrator.situational_picture import (
    SnapshotProvenance,
    SituationalSnapshot,
    TeamSnapshot,
    render_typed_snapshot,
)


@pytest.fixture(autouse=True)
def english_catalog():
    """Another suite may leave the process bound to another language."""

    original = get_current_catalog()
    set_current_catalog(get_catalog("en"))
    try:
        yield
    finally:
        set_current_catalog(original)


def _provenance():
    return SnapshotProvenance(
        source="team_status_store.availability_snapshot",
        as_of="2026-09-09T11:30:00+00:00",
        scope="global",
        operational_scope="SIMULATION_RUN:FIRE_002_PHASE_1:run-task59",
    )


def _snapshot(**overrides):
    team = TeamSnapshot(
        total=3,
        available=2,
        unavailable=1,
        not_reported=0,
        pending_identity=0,
        status="ok",
        provenance=_provenance(),
        **overrides,
    )
    return _wrap(team)


def _wrap(team):
    return SituationalSnapshot(
        cameras=None,
        drones=None,
        team=team,
        recent_count=0,
        relevant_recent_events=(),
        inconsistencies=(),
        generated_at="2026-09-09T11:30:00+00:00",
    )


def test_committed_manpower_is_rendered():
    text = render_typed_snapshot(
        _snapshot(operational_manpower=6, effective_manpower=5),
        include_findings=False,
        include_recent_reports=False,
    )

    assert "Manpower: 5 of 6 reported personnel currently available." in text


def test_committed_resources_are_rendered():
    text = render_typed_snapshot(
        _snapshot(operational_manpower=6, effective_manpower=5, operational_resources=("ASHED-3", "CARMEL-1")),
        include_findings=False,
        include_recent_reports=False,
    )

    assert "Resources: ASHED-3, CARMEL-1." in text


def test_nothing_is_rendered_when_no_manpower_is_committed():
    text = render_typed_snapshot(
        _snapshot(),
        include_findings=False,
        include_recent_reports=False,
    )

    assert "Manpower" not in text
    assert "Resources" not in text
    assert "Readiness team:" in text


def test_resources_render_even_without_a_headcount():
    text = render_typed_snapshot(
        _snapshot(operational_resources=("ASHED-3",)),
        include_findings=False,
        include_recent_reports=False,
    )

    assert "Resources: ASHED-3." in text
    assert "Manpower" not in text


def test_the_attendance_line_is_unchanged():
    text = render_typed_snapshot(
        _snapshot(operational_manpower=6, effective_manpower=5),
        include_findings=False,
        include_recent_reports=False,
    )

    assert "Readiness team: 2 available; 1 unavailable; 0 not reported; 0 pending identity; 3 total." in text


@pytest.mark.parametrize("language", ("en", "he"))
def test_both_catalogs_carry_the_new_keys(language):
    catalog = get_catalog(language)

    manpower = catalog.text("orchestrator.picture.typed.manpower", effective=5, reported=6)
    resources = catalog.text("orchestrator.picture.typed.resources", resources="ASHED-3")

    assert "5" in manpower and "6" in manpower
    assert "ASHED-3" in resources


def test_effective_manpower_reflects_the_scoped_unavailable_count(tmp_path):
    """The rendered headcount is derived from authoritative state, not from prose."""

    from datetime import datetime, timedelta, timezone

    from orchestrator.situational_picture import _build_team_snapshot
    from persistence import OperationalScope, open_team_status_persistence

    store = open_team_status_persistence(str(tmp_path / "team.db"))
    scope = OperationalScope.simulation("FIRE_002_PHASE_1", "run-task59-state")
    store.ensure_scope(
        scope,
        baseline={
            "team": {
                "members": [
                    {"telegram_identity": "1", "full_name": "Lahav"},
                    {"telegram_identity": "2", "full_name": "Omri"},
                    {"telegram_identity": "3", "full_name": "Yuval"},
                ],
                "approve": True,
            }
        },
    )
    opened = datetime(2026, 9, 9, 7, 45, tzinfo=timezone.utc)
    as_of = opened + timedelta(hours=1)
    store.open_cycle(opened.isoformat()[:10], opened.isoformat(), (opened + timedelta(hours=1)).isoformat(), scope=scope)
    store.record_response(
        telegram_identity="2",
        source_message_id="m1",
        availability="unavailable",
        original_text="out",
        received_at=opened.isoformat(),
        reason="medical checkup",
        availability_start=opened.isoformat(),
        availability_end=(opened + timedelta(hours=3)).isoformat(),
        reported_at=opened.isoformat(),
        operational_day=opened.isoformat()[:10],
        scope=scope,
    )
    store.record_operational_state(
        manpower_count=6,
        resources=[{"name": "ASHED", "count": 3, "status": "operational"}],
        source_event_id="e1",
        received_at=opened.isoformat(),
        scope=scope,
    )

    snapshot = _build_team_snapshot(store, now=as_of, operational_scope=scope)

    assert snapshot.operational_manpower == 6
    assert snapshot.unavailable == 1
    assert snapshot.effective_manpower == 5
    assert snapshot.operational_resources == ("ASHED-3",)

    text = render_typed_snapshot(
        _wrap(snapshot),
        include_findings=False,
        include_recent_reports=False,
    )
    assert "Manpower: 5 of 6 reported personnel currently available." in text
    assert "Resources: ASHED-3." in text
