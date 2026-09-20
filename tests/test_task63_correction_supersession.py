"""Task 63 — a correction retracts the report it names, or retracts nothing.

Derived from the two retractions in the official fixtures: SEC_001_PHASE_3
step 5 retracts step 2's gunfire report, and FIRE_002_PHASE_3 step 5 retracts
step 3's trapped-children report. Both corrections come from a different group
than the report they retract, both name their target by subject rather than by
identifier, and both fixtures end with a commander asking which reports turned
out to be false — so the retracted report has to survive.
"""

from datetime import datetime, timezone

import pytest

from orchestrator.supersession import (
    CORRECTION,
    RETRACTION,
    classify_correction,
    resolve_superseded_event,
)
from persistence import NotFoundError, OperationalScope, PersistenceError
from persistence.sqlite_store import SQLitePersistence


SEC_SCOPE = OperationalScope.simulation("SEC_001_PHASE_3", "run-task63-sec")
FIRE_SCOPE = OperationalScope.simulation("FIRE_002_PHASE_3", "run-task63-fire")

SEC_GUNFIRE = (
    "רגע! תושבים מדווחים עכשיו "
    "על ירי בלתי פוסק באזור "
    "השער המערבי! אני רץ לשם!"
)
SEC_RETRACTION = (
    "הבהרה: אין ירי בשער המערבי! "
    "הירי שדווח הוא ירי אזהרה "
    "של הניידת שלנו באזור "
    "המטעים המזרחיים!"
)
SEC_CASUALTY = (
    "קיבלנו דיווח על פצוע "
    "בכניסה לשכונת ההרחבה! "
    "אמבולנס בדרך."
)
FIRE_TRAPPED = (
    "יש שני ילדים לכודים על גג "
    "המבנה ברחוב אורנים 14! "
    "סורגים סגורים."
)
FIRE_RETRACTION = (
    "בדיקה ברחוב אורנים 14: "
    "הבית ריק! הילדים פונו "
    "מוקדם יותר על ידי ההורים. "
    "הדיווח על לכודים - סרק!"
)


def _report(event_id, text, scenario_time, scope, **overrides):
    payload = {
        "event_id": event_id,
        "outcome": "succeeded",
        "classification": "friendly_forces_report",
        "raw_text": text,
        "description": text,
        "scenario_time": scenario_time,
        "received_at": "2026-09-20T10:00:00+00:00",
        "scenario_id": scope.scenario_id,
        "scenario_run_id": scope.scenario_run_id,
    }
    payload.update(overrides)
    return payload


# --- classification ----------------------------------------------------------


def test_the_fixture_retractions_are_recognised():
    assert classify_correction(SEC_RETRACTION) == RETRACTION
    assert classify_correction(FIRE_RETRACTION) == RETRACTION


def test_an_ordinary_report_is_not_a_correction():
    for text in (SEC_GUNFIRE, FIRE_TRAPPED, SEC_CASUALTY, ""):
        assert classify_correction(text) is None


def test_a_clarification_without_a_negation_is_a_correction_not_a_retraction():
    clarification = "הבהרה: האירוע הוא במטע המזרחי."

    assert classify_correction(clarification) == CORRECTION


# --- resolution against the real fixture pairs -------------------------------


def test_the_sec_retraction_resolves_to_the_gunfire_report():
    correction = _report("sec-5", SEC_RETRACTION, "2026-09-08T09:00:00Z", SEC_SCOPE)
    candidates = [
        _report("sec-2", SEC_GUNFIRE, "2026-09-08T08:52:00Z", SEC_SCOPE),
        _report("sec-3", SEC_CASUALTY, "2026-09-08T08:53:00Z", SEC_SCOPE),
    ]

    resolution = resolve_superseded_event(correction, candidates, scope=SEC_SCOPE)

    assert resolution.kind == RETRACTION
    assert resolution.status == "resolved"
    assert resolution.target_event_id == "sec-2"


def test_the_fire_retraction_resolves_to_the_trapped_children_report():
    correction = _report("fire-5", FIRE_RETRACTION, "2026-09-09T13:14:00Z", FIRE_SCOPE)
    candidates = [_report("fire-3", FIRE_TRAPPED, "2026-09-09T13:07:00Z", FIRE_SCOPE)]

    resolution = resolve_superseded_event(correction, candidates, scope=FIRE_SCOPE)

    assert resolution.status == "resolved"
    assert resolution.target_event_id == "fire-3"


# --- refusing to guess -------------------------------------------------------


def test_nothing_is_retracted_when_no_report_matches():
    correction = _report("fire-5", FIRE_RETRACTION, "2026-09-09T13:14:00Z", FIRE_SCOPE)
    candidates = [_report("fire-1", SEC_CASUALTY, "2026-09-09T13:00:00Z", FIRE_SCOPE)]

    resolution = resolve_superseded_event(correction, candidates, scope=FIRE_SCOPE)

    assert resolution.status == "no_match"
    assert resolution.target_event_id is None


def test_nothing_is_retracted_when_two_reports_match_equally():
    correction = _report("fire-5", FIRE_RETRACTION, "2026-09-09T13:14:00Z", FIRE_SCOPE)
    candidates = [
        _report("fire-3", FIRE_TRAPPED, "2026-09-09T13:07:00Z", FIRE_SCOPE),
        _report("fire-4", FIRE_TRAPPED, "2026-09-09T13:08:00Z", FIRE_SCOPE),
    ]

    resolution = resolve_superseded_event(correction, candidates, scope=FIRE_SCOPE)

    assert resolution.status == "ambiguous"
    assert resolution.target_event_id is None
    assert resolution.candidate_event_ids == ("fire-3", "fire-4")


def test_a_report_later_than_the_correction_is_never_retracted():
    """Ordering is on the operational clock, not the runtime receipt."""

    correction = _report("fire-5", FIRE_RETRACTION, "2026-09-09T13:14:00Z", FIRE_SCOPE)
    candidates = [_report("fire-9", FIRE_TRAPPED, "2026-09-09T14:00:00Z", FIRE_SCOPE)]

    assert resolve_superseded_event(correction, candidates, scope=FIRE_SCOPE).status == "no_match"


@pytest.mark.parametrize(
    "overrides",
    (
        {"superseded_by_event_id": "fire-7"},
        {"outcome": "failed"},
        {"outcome": None},
        {"classification": "human_activation"},
    ),
)
def test_ineligible_reports_are_never_retracted(overrides):
    correction = _report("fire-5", FIRE_RETRACTION, "2026-09-09T13:14:00Z", FIRE_SCOPE)
    candidates = [_report("fire-3", FIRE_TRAPPED, "2026-09-09T13:07:00Z", FIRE_SCOPE, **overrides)]

    assert resolve_superseded_event(correction, candidates, scope=FIRE_SCOPE).target_event_id is None


def test_a_message_that_retracts_nothing_resolves_nothing():
    plain = _report("fire-1", FIRE_TRAPPED, "2026-09-09T13:07:00Z", FIRE_SCOPE)

    resolution = resolve_superseded_event(plain, [], scope=FIRE_SCOPE)

    assert resolution.status == "not_a_correction"
    assert resolution.target_event_id is None


# --- persistence -------------------------------------------------------------


@pytest.fixture
def store(tmp_path):
    built = SQLitePersistence(str(tmp_path / "history.db"))
    yield built
    built.close()


def _append(store, text, scope, **overrides):
    payload = {
        "received_at": "2026-09-20T10:00:00+00:00",
        "source": "telegram",
        "sender_identity": "u1",
        "raw_text": text,
        "scenario_id": scope.scenario_id,
        "scenario_run_id": scope.scenario_run_id,
    }
    payload.update(overrides)
    return store.append_event(payload)


def test_a_supersession_links_both_events_and_preserves_the_original(store):
    original = _append(store, SEC_GUNFIRE, SEC_SCOPE)
    correction = _append(store, SEC_RETRACTION, SEC_SCOPE)

    assert store.record_supersession(
        superseded_event_id=original, superseding_event_id=correction, kind=RETRACTION
    ) is True

    retracted = store.fetch_event(original)
    correcting = store.fetch_event(correction)

    assert retracted["superseded_by_event_id"] == correction
    assert retracted["supersession_kind"] == RETRACTION
    assert correcting["supersedes_event_id"] == original
    # The retracted report survives intact — a commander can still be told which
    # reports turned out to be false.
    assert retracted["raw_text"] == SEC_GUNFIRE


def test_recording_the_same_supersession_twice_is_idempotent(store):
    original = _append(store, SEC_GUNFIRE, SEC_SCOPE)
    correction = _append(store, SEC_RETRACTION, SEC_SCOPE)

    first = store.record_supersession(superseded_event_id=original, superseding_event_id=correction, kind=RETRACTION)
    second = store.record_supersession(superseded_event_id=original, superseding_event_id=correction, kind=RETRACTION)

    assert (first, second) == (True, False)
    assert store.fetch_event(original)["superseded_by_event_id"] == correction


def test_supersession_cannot_cross_operational_scopes(store):
    live = _append(store, SEC_GUNFIRE, OperationalScope.live())
    simulated = _append(store, SEC_RETRACTION, SEC_SCOPE)

    with pytest.raises(PersistenceError, match="operational scopes"):
        store.record_supersession(superseded_event_id=live, superseding_event_id=simulated, kind=RETRACTION)

    assert store.fetch_event(live)["superseded_by_event_id"] is None


def test_supersession_cannot_cross_simulation_runs(store):
    other_run = OperationalScope.simulation("SEC_001_PHASE_3", "another-run")
    original = _append(store, SEC_GUNFIRE, other_run)
    correction = _append(store, SEC_RETRACTION, SEC_SCOPE)

    with pytest.raises(PersistenceError, match="operational scopes"):
        store.record_supersession(superseded_event_id=original, superseding_event_id=correction, kind=RETRACTION)


def test_a_report_cannot_be_retracted_twice_by_different_corrections(store):
    original = _append(store, SEC_GUNFIRE, SEC_SCOPE)
    first = _append(store, SEC_RETRACTION, SEC_SCOPE)
    second = _append(store, SEC_RETRACTION, SEC_SCOPE)

    store.record_supersession(superseded_event_id=original, superseding_event_id=first, kind=RETRACTION)

    with pytest.raises(PersistenceError, match="already superseded"):
        store.record_supersession(superseded_event_id=original, superseding_event_id=second, kind=RETRACTION)


def test_an_event_cannot_supersede_itself(store):
    only = _append(store, SEC_RETRACTION, SEC_SCOPE)

    with pytest.raises(PersistenceError, match="cannot supersede itself"):
        store.record_supersession(superseded_event_id=only, superseding_event_id=only, kind=RETRACTION)


def test_an_unknown_supersession_kind_is_refused(store):
    original = _append(store, SEC_GUNFIRE, SEC_SCOPE)
    correction = _append(store, SEC_RETRACTION, SEC_SCOPE)

    with pytest.raises(PersistenceError, match="unsupported supersession kind"):
        store.record_supersession(superseded_event_id=original, superseding_event_id=correction, kind="deleted")


def test_a_missing_event_is_reported_not_silently_ignored(store):
    correction = _append(store, SEC_RETRACTION, SEC_SCOPE)

    with pytest.raises(NotFoundError):
        store.record_supersession(superseded_event_id="nope", superseding_event_id=correction, kind=RETRACTION)


# --- effect on the current picture ------------------------------------------


def test_a_retracted_report_is_no_longer_a_current_fact():
    from orchestrator.situational_picture import _recent_committed_reports

    events = (
        {
            "event_id": "sec-2", "classification": "friendly_forces_report", "description": SEC_GUNFIRE,
            "received_at": "2026-09-20T10:00:00+00:00", "outcome": "succeeded",
            "superseded_by_event_id": "sec-5",
        },
        {
            "event_id": "sec-5", "classification": "friendly_forces_report", "description": SEC_RETRACTION,
            "received_at": "2026-09-20T10:01:00+00:00", "outcome": "succeeded",
            "supersedes_event_id": "sec-2",
        },
    )

    class _History:
        def recent_committed_events(self, **kwargs):
            return events

    reports = _recent_committed_reports(
        _History(),
        now=datetime(2026, 9, 8, 9, 30, tzinfo=timezone.utc),
        sender_identity_filter=None,
        scenario_id=SEC_SCOPE.scenario_id,
        scenario_run_id=SEC_SCOPE.scenario_run_id,
    )
    texts = [report.text for report in reports]

    assert SEC_GUNFIRE not in texts
    assert SEC_RETRACTION in texts


def test_the_retracted_report_is_still_readable_from_history(store):
    original = _append(store, SEC_GUNFIRE, SEC_SCOPE)
    correction = _append(store, SEC_RETRACTION, SEC_SCOPE)
    store.record_supersession(superseded_event_id=original, superseding_event_id=correction, kind=RETRACTION)

    retracted = store.fetch_event(original)

    assert retracted["raw_text"] == SEC_GUNFIRE
    assert retracted["superseded_by_event_id"] == correction
