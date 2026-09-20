"""Task 64 — deterministic correction/retraction intake and ingestion wiring.

Task 63 built the supersession mechanism; this covers the intake that reaches
it without a provider. The rule that matters most here is restraint: an
ordinary negative report is a fact about the present, not a withdrawal of an
earlier report, and must fall through to the normal intake path rather than be
guessed at.
"""

import tempfile
from types import SimpleNamespace

import pytest

from orchestrator.flows import (
    _commit_correction_report,
    _trusted_correction_extraction,
    _trusted_group_extraction,
)
from orchestrator.supersession import CORRECTION, CORRECTION_REPORT_TYPE, RETRACTION
from persistence import OperationalScope
from persistence.sqlite_store import SQLitePersistence
from profiles.contracts import EventTypeRegistry


SEC_SCOPE = OperationalScope.simulation("SEC_001_PHASE_3", "run-task64-sec")
FIRE_SCOPE = OperationalScope.simulation("FIRE_002_PHASE_3", "run-task64-fire")

SEC_GUNFIRE = "רגע! תושבים מדווחים על ירי בלתי פוסק באזור השער המערבי!"
SEC_CASUALTY = "קיבלנו דיווח על פצוע ירי בכניסה לשכונת ההרחבה!"
SEC_RETRACTION = (
    "הבהרה: אין ירי בשער המערבי! "
    "הירי שדווח הוא ירי אזהרה של הניידת שלנו."
)
FIRE_TRAPPED = "יש שני ילדים לכודים על גג המבנה ברחוב אורנים 14!"
FIRE_FLAMES = "חירום! הלהבות בחזית הצפונית הגיעו לגדר של המפעל!"
FIRE_RETRACTION = (
    "בדיקה ברחוב אורנים 14: הבית ריק! "
    "הילדים פונו מוקדם יותר. "
    "הדיווח על לכודים - סרק!"
)

REGISTRY = EventTypeRegistry(types=(CORRECTION_REPORT_TYPE, "friendly_forces_report"))
REGISTRY_WITHOUT_CORRECTIONS = EventTypeRegistry(types=("friendly_forces_report",))


@pytest.fixture
def store():
    built = SQLitePersistence(f"{tempfile.mkdtemp()}/history.db")
    yield built
    built.close()


def _deps(store, registry=REGISTRY, group_owner=None):
    return SimpleNamespace(
        persistence=store,
        event_type_registry=registry,
        group_owner=group_owner,
        registry=None,
        timezone_name="Asia/Jerusalem",
    )


def _commit_report(store, text, scope, scenario_time, sender="reporter"):
    event_id = store.append_event({
        "received_at": "2026-09-20T10:00:00+00:00",
        "source": "telegram",
        "sender_identity": sender,
        "raw_text": text,
        "scenario_id": scope.scenario_id,
        "scenario_run_id": scope.scenario_run_id,
        "scenario_time": scenario_time,
    })
    store.update_event(event_id, {"classification": "friendly_forces_report", "description": text})
    store.finalize_event_if_open(event_id, "succeeded")
    return event_id


def _ingest_correction(store, deps, text, scope, scenario_time, sender="external"):
    extraction = _trusted_correction_extraction(deps, text, "2026-09-20T10:05:00+00:00", scenario_time)
    assert extraction is not None, "the message was not recognised as a correction"
    event_id = store.append_event({
        "received_at": "2026-09-20T10:05:00+00:00",
        "source": "telegram",
        "sender_identity": sender,
        "raw_text": text,
        "scenario_id": scope.scenario_id,
        "scenario_run_id": scope.scenario_run_id,
        "scenario_time": extraction.occurred_at,
    })
    store.update_event(event_id, {
        "classification": extraction.classification,
        "description": extraction.description,
        "business_fields": dict(extraction.business_fields),
    })
    result = _commit_correction_report(deps, store.fetch_event(event_id))
    return event_id, result


# --- recognition -------------------------------------------------------------


def test_an_explicit_correction_phrase_produces_a_typed_extraction(store):
    extraction = _trusted_correction_extraction(_deps(store), SEC_RETRACTION, "2026-09-20T10:05:00+00:00", None)

    assert extraction.classification == CORRECTION_REPORT_TYPE
    assert extraction.business_fields == {"correction_kind": RETRACTION}
    assert extraction.classification_status == "trusted"


def test_an_explicit_cancel_phrase_is_recognised(store):
    text = "מבטלים את הדיווח הקודם על השריפה."

    extraction = _trusted_correction_extraction(_deps(store), text, "2026-09-20T10:05:00+00:00", None)

    assert extraction.business_fields["correction_kind"] == RETRACTION


def test_a_clarification_without_a_negation_is_a_correction(store):
    text = "הבהרה: האירוע הוא במטע המזרחי."

    extraction = _trusted_correction_extraction(_deps(store), text, "2026-09-20T10:05:00+00:00", None)

    assert extraction.business_fields["correction_kind"] == CORRECTION


@pytest.mark.parametrize(
    "text",
    (
        "אין נפגעים באירוע.",
        "אין סיכון למבנים באזור.",
        "אין דליפת חומ\"ס.",
        SEC_GUNFIRE,
        FIRE_TRAPPED,
        "There are no casualties at the scene.",
    ),
)
def test_an_ordinary_negative_report_is_not_a_correction(store, text):
    assert _trusted_correction_extraction(_deps(store), text, "2026-09-20T10:05:00+00:00", None) is None


def test_the_path_is_inert_when_the_profile_does_not_declare_the_type(store):
    deps = _deps(store, registry=REGISTRY_WITHOUT_CORRECTIONS)

    assert _trusted_correction_extraction(deps, SEC_RETRACTION, "2026-09-20T10:05:00+00:00", None) is None


def test_a_correction_is_recognised_before_any_domain_extractor(store):
    """It keeps its own type instead of being normalized into a domain."""

    class _Owner:
        owned_report_types = ("friendly_forces_report",)

        def extract_report(self, *args, **kwargs):  # pragma: no cover - must not run
            raise AssertionError("the domain extractor must not see a correction")

    class _Registry:
        def get(self, name):
            return _Owner()

    deps = _deps(store, group_owner="friendly_forces_agent")
    deps.registry = _Registry()

    result = _trusted_group_extraction(deps, SEC_RETRACTION, "2026-09-20T10:05:00+00:00", None)

    assert result.classification == CORRECTION_REPORT_TYPE


# --- the fixture pairs -------------------------------------------------------


def test_the_sec_correction_retracts_the_gunfire_report_not_the_casualty_report(store):
    deps = _deps(store)
    gunfire = _commit_report(store, SEC_GUNFIRE, SEC_SCOPE, "2026-09-08T08:52:00Z")
    casualty = _commit_report(store, SEC_CASUALTY, SEC_SCOPE, "2026-09-08T08:53:00Z")

    correction_id, result = _ingest_correction(store, deps, SEC_RETRACTION, SEC_SCOPE, "2026-09-08T09:00:00Z")

    assert result.status == "committed"
    assert result.projection.facts["target_status"] == "resolved"
    assert store.fetch_event(gunfire)["superseded_by_event_id"] == correction_id
    assert store.fetch_event(casualty)["superseded_by_event_id"] is None


def test_the_fire_correction_retracts_the_trapped_children_report(store):
    deps = _deps(store)
    flames = _commit_report(store, FIRE_FLAMES, FIRE_SCOPE, "2026-09-09T13:00:00Z")
    trapped = _commit_report(store, FIRE_TRAPPED, FIRE_SCOPE, "2026-09-09T13:07:00Z")

    correction_id, result = _ingest_correction(store, deps, FIRE_RETRACTION, FIRE_SCOPE, "2026-09-09T13:14:00Z")

    assert result.projection.facts["superseded_event_id"] == trapped
    assert store.fetch_event(trapped)["superseded_by_event_id"] == correction_id
    assert store.fetch_event(flames)["superseded_by_event_id"] is None


def test_a_correction_from_a_different_group_still_retracts(store):
    """Supersession is scope-gated, never group-gated."""

    deps = _deps(store)
    trapped = _commit_report(store, FIRE_TRAPPED, FIRE_SCOPE, "2026-09-09T13:07:00Z", sender="response_team")

    correction_id, _ = _ingest_correction(
        store, deps, FIRE_RETRACTION, FIRE_SCOPE, "2026-09-09T13:14:00Z", sender="external_forces"
    )

    assert store.fetch_event(trapped)["superseded_by_event_id"] == correction_id


# --- refusing to guess -------------------------------------------------------


def test_no_matching_report_retracts_nothing(store):
    deps = _deps(store)
    flames = _commit_report(store, FIRE_FLAMES, FIRE_SCOPE, "2026-09-09T13:00:00Z")

    _, result = _ingest_correction(store, deps, FIRE_RETRACTION, FIRE_SCOPE, "2026-09-09T13:14:00Z")

    assert result.status == "committed"
    assert result.projection.facts["target_status"] == "no_match"
    assert store.fetch_event(flames)["superseded_by_event_id"] is None


def test_an_ambiguous_subject_retracts_nothing(store):
    deps = _deps(store)
    first = _commit_report(store, FIRE_TRAPPED, FIRE_SCOPE, "2026-09-09T13:07:00Z")
    second = _commit_report(store, FIRE_TRAPPED, FIRE_SCOPE, "2026-09-09T13:08:00Z")

    _, result = _ingest_correction(store, deps, FIRE_RETRACTION, FIRE_SCOPE, "2026-09-09T13:14:00Z")

    assert result.projection.facts["target_status"] == "ambiguous"
    assert result.projection.facts["ambiguous_candidate_count"] == 2
    assert store.fetch_event(first)["superseded_by_event_id"] is None
    assert store.fetch_event(second)["superseded_by_event_id"] is None


def test_a_correction_cannot_retract_a_report_from_another_run(store):
    deps = _deps(store)
    other_run = OperationalScope.simulation("FIRE_002_PHASE_3", "a-different-run")
    elsewhere = _commit_report(store, FIRE_TRAPPED, other_run, "2026-09-09T13:07:00Z")

    _, result = _ingest_correction(store, deps, FIRE_RETRACTION, FIRE_SCOPE, "2026-09-09T13:14:00Z")

    assert result.projection.facts["target_status"] == "no_match"
    assert store.fetch_event(elsewhere)["superseded_by_event_id"] is None


def test_a_live_correction_cannot_retract_a_simulation_report(store):
    deps = _deps(store)
    simulated = _commit_report(store, FIRE_TRAPPED, FIRE_SCOPE, "2026-09-09T13:07:00Z")

    _, result = _ingest_correction(store, deps, FIRE_RETRACTION, OperationalScope.live(), "2026-09-09T13:14:00Z")

    assert result.projection.facts["target_status"] == "no_match"
    assert store.fetch_event(simulated)["superseded_by_event_id"] is None


# --- idempotence, history and the current picture ---------------------------


def test_a_repeated_correction_is_idempotent(store):
    deps = _deps(store)
    trapped = _commit_report(store, FIRE_TRAPPED, FIRE_SCOPE, "2026-09-09T13:07:00Z")
    correction_id, _ = _ingest_correction(store, deps, FIRE_RETRACTION, FIRE_SCOPE, "2026-09-09T13:14:00Z")

    repeat = _commit_correction_report(deps, store.fetch_event(correction_id))

    assert repeat.status == "committed"
    assert repeat.projection.facts["target_status"] == "resolved"
    assert store.fetch_event(trapped)["superseded_by_event_id"] == correction_id


def test_a_second_correction_does_not_relink_an_already_retracted_report(store):
    deps = _deps(store)
    trapped = _commit_report(store, FIRE_TRAPPED, FIRE_SCOPE, "2026-09-09T13:07:00Z")
    first_id, _ = _ingest_correction(store, deps, FIRE_RETRACTION, FIRE_SCOPE, "2026-09-09T13:14:00Z")

    _, second = _ingest_correction(store, deps, FIRE_RETRACTION, FIRE_SCOPE, "2026-09-09T13:20:00Z")

    assert second.projection.facts["target_status"] == "no_match"
    assert store.fetch_event(trapped)["superseded_by_event_id"] == first_id


def test_both_records_remain_in_history(store):
    deps = _deps(store)
    trapped = _commit_report(store, FIRE_TRAPPED, FIRE_SCOPE, "2026-09-09T13:07:00Z")
    correction_id, _ = _ingest_correction(store, deps, FIRE_RETRACTION, FIRE_SCOPE, "2026-09-09T13:14:00Z")

    original = store.fetch_event(trapped)
    correction = store.fetch_event(correction_id)

    assert original["raw_text"] == FIRE_TRAPPED
    assert original["outcome"] == "succeeded"
    assert correction["raw_text"] == FIRE_RETRACTION
    assert correction["supersedes_event_id"] == trapped


def test_the_retracted_report_leaves_the_current_operational_facts(store):
    from datetime import datetime, timezone

    from orchestrator.situational_picture import _recent_committed_reports

    deps = _deps(store)
    _commit_report(store, FIRE_TRAPPED, FIRE_SCOPE, "2026-09-09T13:07:00Z")
    _ingest_correction(store, deps, FIRE_RETRACTION, FIRE_SCOPE, "2026-09-09T13:14:00Z")

    class _History:
        def recent_committed_events(self, **kwargs):
            from persistence import EventSearchCriteria

            return store.search_events(EventSearchCriteria(
                outcomes=("succeeded",), time_basis="received_at",
                scenario_id=FIRE_SCOPE.scenario_id, scenario_run_id=FIRE_SCOPE.scenario_run_id, limit=50,
            ))

    texts = [
        report.text
        for report in _recent_committed_reports(
            _History(),
            now=datetime(2026, 9, 9, 13, 30, tzinfo=timezone.utc),
            sender_identity_filter=None,
            scenario_id=FIRE_SCOPE.scenario_id,
            scenario_run_id=FIRE_SCOPE.scenario_run_id,
        )
    ]

    assert FIRE_TRAPPED not in texts


def test_no_fixture_sentence_appears_in_the_intake_source():
    """The vocabulary must be generic, not a list of fixture sentences."""

    from pathlib import Path

    source = Path(__file__).resolve().parent.parent / "orchestrator" / "supersession.py"
    body = source.read_text(encoding="utf-8")

    for fragment in (FIRE_TRAPPED, SEC_GUNFIRE, SEC_RETRACTION, FIRE_RETRACTION):
        assert fragment not in body
    assert "אורנים" not in body
    assert "השער המערבי" not in body
