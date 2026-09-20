"""Task 62 — bounded scoped operational facts in the deterministic fallback.

Task 54 forbade the fallback from stating committed reports at all, because the
upstream read then behaved like a global recent-event dump. That contract has
changed: `recent_reports` is scoped to the current scenario run, admits only
succeeded `*_report` events, and is capped before it reaches the snapshot. The
fallback may therefore state a small number of those facts — never a dump, never
a duplicate of a structured section, and never a recommendation.
"""

import pytest

from messages import get_catalog, get_current_catalog, set_current_catalog
from orchestrator.situational_picture import (
    FALLBACK_OPERATIONAL_FACT_LIMIT,
    CameraSnapshot,
    RecentOperationalReport,
    SituationalSnapshot,
    SnapshotProvenance,
    TeamSnapshot,
    _recent_committed_reports,
    _render_reasoning_fallback,
    operational_facts_for_render,
)


FIRE_RUN = ("FIRE_002_PHASE_1", "run-task62-fire")
SEC_RUN = ("SEC_001_PHASE_1", "run-task62-sec")


@pytest.fixture(autouse=True)
def english_catalog():
    original = get_current_catalog()
    set_current_catalog(get_catalog("en"))
    try:
        yield
    finally:
        set_current_catalog(original)


def _provenance():
    return SnapshotProvenance(source="store", as_of="2026-09-09T11:30:00+00:00")


def _report(text, domain):
    return RecentOperationalReport(
        text=text, source_ref=f"event:{domain}", received_at="2026-09-09T11:00:00+00:00", domain=domain
    )


def _snapshot(*, reports=(), cameras=True, team=True):
    camera_section = (
        CameraSnapshot(
            total=6, active=5, inactive=1, degraded=0, offline=1, unknown=0,
            status="ok", provenance=_provenance(),
        )
        if cameras
        else None
    )
    team_section = (
        TeamSnapshot(
            total=3, available=2, unavailable=1, not_reported=0, pending_identity=0,
            status="ok", provenance=_provenance(),
            operational_manpower=6, effective_manpower=5, operational_resources=("ASHED-3", "CARMEL-1"),
        )
        if team
        else None
    )
    return SituationalSnapshot(
        cameras=camera_section,
        drones=None,
        team=team_section,
        recent_count=len(reports),
        relevant_recent_events=tuple(report.text for report in reports),
        inconsistencies=(),
        generated_at="2026-09-09T11:30:00+00:00",
        recent_reports=tuple(reports),
    )


class _History:
    """Stands in for the scoped history read, recording what it was asked for."""

    def __init__(self, events):
        self.events = events
        self.calls = []

    def recent_committed_events(self, **kwargs):
        self.calls.append(kwargs)
        scenario_id = kwargs.get("scenario_id")
        run_id = kwargs.get("scenario_run_id")
        return tuple(
            event for event in self.events
            if event.get("scenario_id") == scenario_id and event.get("scenario_run_id") == run_id
        )


def _event(description, classification, run, *, outcome="succeeded", event_id="e1"):
    return {
        "event_id": event_id,
        "classification": classification,
        "description": description,
        "received_at": "2026-09-09T11:00:00+00:00",
        "outcome": outcome,
        "scenario_id": run[0],
        "scenario_run_id": run[1],
    }


def _scoped_reports(events, run):
    from datetime import datetime, timezone

    return _recent_committed_reports(
        _History(events),
        now=datetime(2026, 9, 9, 11, 30, tzinfo=timezone.utc),
        sender_identity_filter=None,
        scenario_id=run[0],
        scenario_run_id=run[1],
    )


# --- a scoped incident fact and a scoped advisory can appear -----------------


def test_a_scoped_roadside_fire_fact_can_appear():
    reports = _scoped_reports(
        [_event("Small brush fire beside route 444, police patrol on scene.", "friendly_forces_report", FIRE_RUN)],
        FIRE_RUN,
    )

    text = _render_reasoning_fallback(_snapshot(reports=reports))

    assert "Recent committed reports:" in text
    assert "Small brush fire beside route 444" in text


def test_a_scoped_advisory_or_restriction_can_appear():
    reports = _scoped_reports(
        [_event("Fire lighting prohibited in all area forests due to the heatwave.", "friendly_forces_report", FIRE_RUN)],
        FIRE_RUN,
    )

    text = _render_reasoning_fallback(_snapshot(reports=reports))

    assert "Fire lighting prohibited in all area forests" in text


def test_a_significant_condition_fact_can_appear():
    reports = _scoped_reports(
        [_event("Low heat alert from the temperature sensor.", "operational_condition_report", FIRE_RUN)],
        FIRE_RUN,
    )

    text = _render_reasoning_fallback(_snapshot(reports=reports))

    assert "Low heat alert" in text


# --- isolation ---------------------------------------------------------------


def test_facts_from_another_run_cannot_appear():
    events = [
        _event("FIRE run advisory.", "friendly_forces_report", FIRE_RUN, event_id="fire-1"),
        _event("SEC run stolen vehicle report.", "friendly_forces_report", SEC_RUN, event_id="sec-1"),
    ]

    text = _render_reasoning_fallback(_snapshot(reports=_scoped_reports(events, FIRE_RUN)))

    assert "FIRE run advisory." in text
    assert "SEC run stolen vehicle report." not in text


def test_live_facts_cannot_leak_into_a_simulation_picture():
    events = [
        _event("FIRE run advisory.", "friendly_forces_report", FIRE_RUN, event_id="fire-1"),
        {
            "event_id": "live-1",
            "classification": "friendly_forces_report",
            "description": "LIVE production advisory.",
            "received_at": "2026-09-09T11:00:00+00:00",
            "outcome": "succeeded",
            "scenario_id": None,
            "scenario_run_id": None,
        },
    ]

    reports = _scoped_reports(events, FIRE_RUN)
    text = _render_reasoning_fallback(_snapshot(reports=reports))

    assert "LIVE production advisory." not in text
    assert "FIRE run advisory." in text


def test_the_scoped_read_is_asked_for_the_run_and_never_globally():
    history = _History([_event("FIRE run advisory.", "friendly_forces_report", FIRE_RUN)])
    from datetime import datetime, timezone

    _recent_committed_reports(
        history,
        now=datetime(2026, 9, 9, 11, 30, tzinfo=timezone.utc),
        sender_identity_filter=None,
        scenario_id=FIRE_RUN[0],
        scenario_run_id=FIRE_RUN[1],
    )

    assert history.calls[0]["scenario_id"] == FIRE_RUN[0]
    assert history.calls[0]["scenario_run_id"] == FIRE_RUN[1]


def test_the_renderer_performs_no_history_lookup_of_its_own():
    """The renderer reads the snapshot it was handed, nothing else."""

    reports = (_report("Advisory already selected upstream.", "friendly_forces"),)

    assert operational_facts_for_render(_snapshot(reports=reports)) == reports


# --- only succeeded committed reports ---------------------------------------


def test_a_failed_report_cannot_appear():
    events = [
        _event("Committed advisory.", "friendly_forces_report", FIRE_RUN, event_id="ok-1"),
        _event("Failed advisory.", "friendly_forces_report", FIRE_RUN, outcome="failed", event_id="bad-1"),
    ]

    text = _render_reasoning_fallback(_snapshot(reports=_scoped_reports(events, FIRE_RUN)))

    assert "Committed advisory." in text
    assert "Failed advisory." not in text


def test_a_non_terminal_report_cannot_appear():
    events = [
        _event("Committed advisory.", "friendly_forces_report", FIRE_RUN, event_id="ok-1"),
        _event("Still processing.", "friendly_forces_report", FIRE_RUN, outcome=None, event_id="pending-1"),
    ]

    text = _render_reasoning_fallback(_snapshot(reports=_scoped_reports(events, FIRE_RUN)))

    assert "Still processing." not in text


def test_a_non_report_event_cannot_appear():
    events = [
        _event("Committed advisory.", "friendly_forces_report", FIRE_RUN, event_id="ok-1"),
        _event("A human activation.", "human_activation", FIRE_RUN, event_id="act-1"),
    ]

    text = _render_reasoning_fallback(_snapshot(reports=_scoped_reports(events, FIRE_RUN)))

    assert "A human activation." not in text


# --- no duplication with structured sections --------------------------------


def test_camera_team_and_resource_facts_are_not_repeated():
    reports = (
        _report("Camera CAM-02 offline for planned maintenance.", "surveillance"),
        _report("Omri unavailable from 12:00 to 15:00.", "team_attendance"),
        _report("Opening manpower 6, ASHED 3, CARMEL 1.", "team_resource"),
        _report("Brush fire beside route 444.", "friendly_forces"),
    )

    text = _render_reasoning_fallback(_snapshot(reports=reports))

    assert "Brush fire beside route 444." in text
    assert "Camera CAM-02 offline" not in text
    assert "Omri unavailable" not in text
    assert "Opening manpower 6" not in text
    # The same facts are still stated once, by their own structured sections.
    assert "Cameras:" in text
    assert "Readiness team:" in text
    assert "Manpower: 5 of 6 reported personnel currently available." in text


def test_those_facts_are_stated_when_no_section_represents_them():
    reports = (
        _report("Camera CAM-02 offline for planned maintenance.", "surveillance"),
        _report("Brush fire beside route 444.", "friendly_forces"),
    )

    text = _render_reasoning_fallback(_snapshot(reports=reports, cameras=False, team=False))

    assert "Camera CAM-02 offline for planned maintenance." in text
    assert "Brush fire beside route 444." in text


def test_a_resource_fact_appears_when_no_manpower_is_committed():
    reports = (_report("Two firefighting tractors dispatched by the district.", "team_resource"),)
    snapshot = _snapshot(reports=reports)
    stripped = SituationalSnapshot(
        cameras=snapshot.cameras,
        drones=None,
        team=TeamSnapshot(
            total=3, available=2, unavailable=1, not_reported=0, pending_identity=0,
            status="ok", provenance=_provenance(),
        ),
        recent_count=1,
        relevant_recent_events=(reports[0].text,),
        inconsistencies=(),
        generated_at=snapshot.generated_at,
        recent_reports=reports,
    )

    text = _render_reasoning_fallback(stripped)

    assert "Two firefighting tractors dispatched by the district." in text


# --- bounds, and nothing advised ---------------------------------------------


def test_the_cap_is_enforced():
    reports = tuple(
        _report(f"External force fact {index}.", "friendly_forces") for index in range(FALLBACK_OPERATIONAL_FACT_LIMIT + 6)
    )

    selected = operational_facts_for_render(_snapshot(reports=reports))
    text = _render_reasoning_fallback(_snapshot(reports=reports))
    rendered = [line for line in text.splitlines() if "External force fact" in line]

    assert len(selected) == FALLBACK_OPERATIONAL_FACT_LIMIT
    assert len(rendered) == FALLBACK_OPERATIONAL_FACT_LIMIT


def test_no_recommendation_or_action_is_fabricated():
    reports = (
        _report("Brush fire beside route 444, police patrol on scene.", "friendly_forces"),
        _report("Fire lighting prohibited in all area forests.", "friendly_forces"),
    )

    text = _render_reasoning_fallback(_snapshot(reports=reports))

    assert "Recommended next actions:" not in text
    assert "dispatch" not in text.casefold()
    assert "approved" not in text.casefold()


def test_no_event_identifier_or_metadata_is_rendered():
    reports = _scoped_reports(
        [_event("Brush fire beside route 444.", "friendly_forces_report", FIRE_RUN, event_id="event-abc123")],
        FIRE_RUN,
    )

    text = _render_reasoning_fallback(_snapshot(reports=reports))

    assert "Brush fire beside route 444." in text
    assert "event-abc123" not in text
    assert "event:" not in text
    assert "2026-09-09T11:00:00" not in text
