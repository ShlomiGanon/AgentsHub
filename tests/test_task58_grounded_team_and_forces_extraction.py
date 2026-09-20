"""Task 58 — the team-status and friendly-forces extractors state only what the message grounds.

Both paths previously matched a loose keyword and then emitted constants taken
from one fixture's wording, which committed operational facts nobody reported:
a headcount of zero for a message that states no headcount, a medical reason
for any message mentioning a shift, and a cigarette-caused brush fire for any
message mentioning a road number.
"""

import pytest

from persistence import OperationalScope, open_team_status_persistence


OCCURRED = "2026-09-09T07:00:00+00:00"
SCOPE = OperationalScope.simulation("FIRE_002_PHASE_1", "run-task58")


@pytest.fixture
def team(tmp_path):
    from agents.team_status_agent import TeamStatusAgent

    class _Agent(TeamStatusAgent):
        status_db_path = str(tmp_path / "team.db")

        def __init__(self):
            self.status_store = open_team_status_persistence(self.status_db_path)

    built = _Agent()
    built.status_store.ensure_scope(
        SCOPE,
        baseline={"team": {"members": [{"telegram_identity": "1", "full_name": "Omri"}], "approve": True}},
    )
    return built


@pytest.fixture
def forces():
    from agents.friendly_forces_agent import FriendlyForcesAgent

    class _Agent(FriendlyForcesAgent):
        def __init__(self):
            self.dispatches_recorded = []

    return _Agent()


def _extract(agent, text):
    return agent.extract_report(text, received_at=OCCURRED, scenario_time=OCCURRED)


# --- team status ------------------------------------------------------------


def test_a_stated_headcount_and_vehicle_counts_are_extracted(team):
    result = _extract(team, "מעדכן סד\"כ פותח: 6 כבאים, רכב אשד 3 וכרמל 1 במבצעיות.")

    assert result.classification == "team_resource_report"
    assert result.business_fields["manpower_count"] == 6
    assert result.business_fields["resources_count"] == 2
    assert set(result.entities) == {"ASHED", "CARMEL"}


def test_a_message_with_no_headcount_never_reports_zero(team):
    """The old path substituted 0 whenever its count pattern missed."""

    result = _extract(team, "רכב אשד 3 יצא לנקודה. סד\"כ בתחנה צמצום.")

    assert result.classification == "team_resource_report"
    assert "manpower_count" not in result.business_fields
    assert result.business_fields["resources_count"] == 1


def test_a_readiness_word_alone_is_not_a_resource_report(team):
    assert _extract(team, "מה מצב הסד\"כ שלנו?") is None


def test_the_absence_reason_is_read_from_the_message(team):
    cases = {
        "אני במילואים מראשון עד שלישי בערב.": "reserve duty",
        "קמתי עם חום גבוה, לא אוכל להשתתף בסיור.": "illness",
        "צריך לצאת ב-12:00 לבדיקה רפואית תקופתית.": "medical checkup",
        "אני בחופשה השבוע.": "leave",
    }
    for text, expected in cases.items():
        result = _extract(team, text)
        assert result is not None, text
        assert result.business_fields["reason"] == expected, text


def test_no_reason_is_invented_when_the_message_gives_none(team):
    """The old path returned a fixed medical reason for any timed shift message."""

    assert _extract(team, "אני חוזר למשמרת ב-15:00.") is None


def test_a_vehicle_only_report_carries_the_committed_headcount_forward(team):
    opening = _extract(team, "6 כבאים, אשד 3 וכרמל 1.")
    team.ingest_report(
        {
            "event_id": "e1", "classification": opening.classification,
            "business_fields": dict(opening.business_fields), "description": opening.description,
            "received_at": OCCURRED, "scenario_id": SCOPE.scenario_id, "scenario_run_id": SCOPE.scenario_run_id,
        },
        scope=SCOPE,
    )

    follow_up = _extract(team, "רכב אשד 3 יצא לנקודה.")
    result = team.ingest_report(
        {
            "event_id": "e2", "classification": follow_up.classification,
            "business_fields": dict(follow_up.business_fields), "description": follow_up.description,
            "received_at": OCCURRED, "scenario_id": SCOPE.scenario_id, "scenario_run_id": SCOPE.scenario_run_id,
        },
        scope=SCOPE,
    )

    assert result.status == "committed"
    assert team.status_store.operational_state(scope=SCOPE)["manpower_count"] == 6


def test_a_vehicle_only_report_with_nothing_committed_is_rejected_not_guessed(team):
    follow_up = _extract(team, "רכב אשד 3 יצא לנקודה.")
    result = team.ingest_report(
        {
            "event_id": "e3", "classification": follow_up.classification,
            "business_fields": dict(follow_up.business_fields), "description": follow_up.description,
            "received_at": OCCURRED, "scenario_id": SCOPE.scenario_id, "scenario_run_id": SCOPE.scenario_run_id,
        },
        scope=SCOPE,
    )

    assert result.status == "rejected"
    assert team.status_store.operational_state(scope=SCOPE) is None


# --- friendly forces --------------------------------------------------------


def test_an_advisory_states_only_the_qualifiers_present(forces):
    full = _extract(forces, "עקב השרב, הוצאנו הנחיה לאיסור הדלקת אש בכל היערות באזור. יערנים בסריקות.")
    bare = _extract(forces, "הוצא איסור הדלקת אש במרחב.")

    assert full.business_fields == {
        "advisory_kind": "fire_lighting_prohibition", "status": "active",
        "applies_to": "forests in area", "patrols": "rangers", "active_due_to": "heatwave",
    }
    assert bare.business_fields == {"advisory_kind": "fire_lighting_prohibition", "status": "active"}


def test_a_road_number_alone_is_no_longer_a_brush_fire(forces):
    """The old path turned any message naming route 444 into a cigarette-caused fire."""

    assert _extract(forces, "מתקבלים דיווחים מאזרחים על עשן סמיך שנראה מכביש 444. עומסי תנועה מתפתחים.") is None


def test_an_incident_states_only_the_facts_present(forces):
    result = _extract(forces, "דיווח על שריפת קוצים קטנה בצד כביש 444, כנראה מסיגריה. ניידת במקום, אין סיכון למבנים.")

    assert result.business_fields == {
        "incident_kind": "brush_fire", "size": "small", "location": "Route 444",
        "possible_cause": "cigarette remains", "cause_status": "unverified",
        "responding_unit": "police patrol", "building_risk": "none",
    }


def test_a_fire_without_a_stated_cause_or_road_omits_those_fields(forces):
    result = _extract(forces, "שריפה קטנה בשטח פתוח ליד כביש הגישה האזורי. כיבוי מטפלים, אין סיכון לשטחים חקלאיים.")

    assert result.business_fields == {
        "incident_kind": "fire", "size": "small", "responding_unit": "firefighters",
    }
    assert "location" not in result.business_fields
    assert "possible_cause" not in result.business_fields
    # "no risk to agricultural areas" is not a statement about buildings.
    assert "building_risk" not in result.business_fields


def test_a_cause_stated_without_hedging_is_not_marked_unverified(forces):
    hedged = _extract(forces, "שריפה בשולי הדרך, כנראה מסיגריה.")
    stated = _extract(forces, "שריפה בשולי הדרך מסיגריה שהושלכה.")

    assert hedged.business_fields["cause_status"] == "unverified"
    assert "cause_status" not in stated.business_fields


def test_a_message_with_no_advisory_or_incident_is_declined(forces):
    assert _extract(forces, "עדכון גזרתי: הלילה נגנב טרקטרון מאצלינו.") is None
