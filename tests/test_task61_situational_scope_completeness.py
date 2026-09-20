"""Task 61 — an explicit situational-picture request returns the whole picture.

Naming domains inside a request for a situational picture is emphasis, not a
restriction. The classifier treated it as a restriction, so a commander asking
for a picture while mentioning manpower was answered without the surveillance
section — and a degraded camera the system had already committed stayed
invisible.
"""

import pytest

from orchestrator.situational_picture import (
    SituationalQueryScope,
    classify_situational_query,
)


# The two commander questions from the official fixtures that exposed this.
FIRE_PHASE_1_STEP_7 = (
    "תפיק לי תמונת מצב לתחילת הצהריים: "
    "מה זמינות הכוחות והרכבים שלנו "
    "תחת תנאי השרב?"
)
FIRE_PHASE_2_STEP_7 = (
    "תציג לי תמונת מצב דחופה: "
    "מה מיקום השריפה המדויק, "
    "מה הסטטוס של הצוותים בשטח?"
)


@pytest.mark.parametrize("query", (FIRE_PHASE_1_STEP_7, FIRE_PHASE_2_STEP_7))
def test_an_explicit_picture_request_is_never_narrowed(query):
    scope = classify_situational_query(query)

    assert scope == SituationalQueryScope.overall_scope()
    assert scope.surveillance is True
    assert scope.team is True


def test_the_surveillance_section_is_reachable_for_a_manpower_worded_picture():
    """The exact regression: a degraded camera must not be hidden by wording."""

    scope = classify_situational_query(FIRE_PHASE_1_STEP_7)

    assert scope.overall or scope.surveillance, "camera state would not be built"


@pytest.mark.parametrize(
    "query,expected",
    (
        # A bare domain question stays bounded to that domain.
        ("מה הסטטוס של מצלמות הגדר?", SituationalQueryScope(surveillance=True)),
        ("מי חסר בסד\"כ ללילה?", SituationalQueryScope(team=True)),
        ("מה מצב הכוח והמצלמות?", SituationalQueryScope(team=True, surveillance=True)),
    ),
)
def test_a_bare_domain_question_stays_bounded(query, expected):
    assert classify_situational_query(query) == expected


def test_the_generic_picture_wording_stays_bounded():
    """Only the explicit situational-picture phrase widens the scope."""

    scope = classify_situational_query(
        "תציף לי תמונה מהירה: "
        "יש משהו חשוד בגזרה המזרחית?"
    )

    assert scope == SituationalQueryScope(surveillance=True, external_reports=True)
    assert scope.overall is False


def test_a_daily_summary_keeps_its_own_bounded_scope():
    scope = classify_situational_query(
        "ערב טוב, תפיק לי סיכום יומי: "
        "מי חסר בסד\"כ ללילה "
        "ומה הסטטוס של מצלמות הגדר?"
    )

    assert scope == SituationalQueryScope(team=True, surveillance=True)


@pytest.mark.parametrize(
    "query",
    (
        "מה מצב הפעולה שביקשתי?",
        "מה מצב האישור?",
        "מה מצב מזג האוויר?",
    ),
)
def test_non_operational_questions_are_still_refused(query):
    assert classify_situational_query(query) is None


def test_a_team_worded_question_is_recognised():
    """`צוות` is ordinary team vocabulary and was missing from the domain terms."""

    scope = classify_situational_query("מה מצב הצוותים בשטח?")

    assert scope == SituationalQueryScope(team=True)


def test_english_team_wording_is_recognised():
    assert classify_situational_query("what is the status of the team?") == SituationalQueryScope(team=True)


def test_an_overall_request_requires_bounded_reasoning():
    scope = classify_situational_query(FIRE_PHASE_1_STEP_7)

    assert scope.requires_bounded_reasoning is True


def test_every_section_is_built_for_an_explicit_picture(tmp_path):
    """End to end on authoritative state: the camera section is present."""

    from datetime import datetime, timedelta, timezone

    from orchestrator.situational_picture import build_typed_snapshot
    from persistence import OperationalScope, open_surveillance_persistence, open_team_status_persistence

    scope = OperationalScope.simulation("FIRE_002_PHASE_1", "run-task61")
    surveillance = open_surveillance_persistence(
        str(tmp_path / "surveillance.db"), seed_demo_data=True, seed_profile="profiles.unified_test"
    )
    surveillance.ensure_scope(scope)
    team = open_team_status_persistence(str(tmp_path / "team.db"))
    team.ensure_scope(
        scope,
        baseline={"team": {"members": [{"telegram_identity": "1", "full_name": "Omri"}], "approve": True}},
    )

    moment = datetime(2026, 9, 9, 11, 30, tzinfo=timezone.utc)
    surveillance.update_camera_feed("CAM-02", "planned cleaning", status="offline", updated_at=moment.isoformat(), scope=scope)
    team.record_operational_state(
        manpower_count=6,
        resources=[{"name": "ASHED", "count": 3, "status": "operational"}],
        source_event_id="e1",
        received_at=moment.isoformat(),
        scope=scope,
    )

    class _Registry:
        def __init__(self):
            self._agents = {
                "surveillance_agent": type("A", (), {"surveillance_store": surveillance})(),
                "team_status_agent": type("A", (), {"status_store": team})(),
            }

        def get(self, name):
            return self._agents[name]

    query_scope = classify_situational_query(FIRE_PHASE_1_STEP_7)
    snapshot = build_typed_snapshot(
        _Registry(), now=moment, scope=query_scope, operational_scope=scope
    )

    assert snapshot is not None
    assert snapshot.cameras is not None, "the camera section was omitted"
    assert snapshot.cameras.offline == 1
    assert snapshot.team is not None
    assert snapshot.team.operational_manpower == 6
