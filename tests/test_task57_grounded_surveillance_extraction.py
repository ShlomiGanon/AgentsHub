"""Task 57 — the trusted surveillance extractor states only what the message grounds.

Every field this path returns becomes authoritative state, so the tests below
fix two properties: a camera or condition that the message does not support is
never asserted, and a subject the message does name is resolved generically
against the scoped inventory rather than against a fixture's camera number.
"""

import pytest

from persistence import OperationalScope, open_surveillance_persistence


OCCURRED = "2026-09-06T08:15:00+00:00"

SCOPE = OperationalScope.simulation("SEC_001_PHASE_1", "run-task57")


@pytest.fixture
def agent(tmp_path):
    from agents.surveillance_agent import SurveillanceAgent

    class _Agent(SurveillanceAgent):
        surveillance_db_path = str(tmp_path / "surveillance.db")
        surveillance_seed_enabled = True
        surveillance_seed_profile = "profiles.unified_test"

        def __init__(self):
            self.surveillance_store = open_surveillance_persistence(
                self.surveillance_db_path,
                seed_demo_data=self.surveillance_seed_enabled,
                seed_profile=self.surveillance_seed_profile,
            )

    built = _Agent()
    built.surveillance_store.ensure_scope(SCOPE)
    return built


def _extract(agent, text):
    return agent.extract_report(text, received_at=OCCURRED, scenario_time=OCCURRED, scope=SCOPE)


def test_any_camera_in_the_inventory_is_resolved_not_only_one(agent):
    """The old path recognised a single hard-coded camera number."""

    resolved = {}
    for number in ("01", "03", "05", "08"):
        result = _extract(agent, f"מצלמה {number} הופסקה.")
        resolved[number] = None if result is None else result.business_fields["camera_id"]

    assert resolved == {"01": "CAM-01", "03": "CAM-03", "05": "CAM-05", "08": "CAM-08"}


def test_english_and_hebrew_camera_references_resolve_alike(agent):
    for text in ("camera 03 went dark", "CAM-03 is offline", "מצלמה 03 נותקה"):
        result = _extract(agent, text)
        assert result is not None, text
        assert result.business_fields["camera_id"] == "CAM-03"


def test_a_camera_outside_the_inventory_is_declined(agent):
    assert _extract(agent, "מצלמה 77 הופסקה.") is None


def test_status_is_read_from_the_message(agent):
    degraded = _extract(agent, "מצלמה 08 מציגה הפרעות קליטה לפרקים.")
    offline = _extract(agent, "מצלמה 08 הופסקה.")
    restored = _extract(agent, "מצלמה 08 חזרה לפעול.")

    assert degraded.business_fields["camera_status"] == "degraded"
    assert offline.business_fields["camera_status"] == "offline"
    assert restored.business_fields["camera_status"] == "active"


def test_a_camera_mentioned_without_a_state_is_declined(agent):
    """Naming a camera is not reporting a state change."""

    assert _extract(agent, "זיהוי עשן ראשוני במצלמה 05 (רכס אורנים)!") is None


def test_a_report_naming_two_cameras_is_declined(agent):
    text = "מצלמה 03 עדיין לא חזרה, וגם מצלמה 04 תקועה."

    assert _extract(agent, text) is None


def test_planned_maintenance_and_duration_are_grounded(agent):
    planned = _extract(
        agent,
        "מצלמה 03 הורדה יזומית לשעתיים לצורך עדכון גרסה תקופתי.",
    )
    unplanned = _extract(agent, "מצלמה 03 נותקה.")

    assert planned.business_fields["shutdown_type"] == "planned_maintenance"
    assert planned.business_fields["downtime_duration_hours"] == 2.0
    assert "shutdown_type" not in unplanned.business_fields
    assert "downtime_duration_hours" not in unplanned.business_fields


def test_no_field_is_invented_for_a_camera_report(agent):
    """The old path asserted a fixed status, shutdown type and reason."""

    result = _extract(agent, "מצלמה 08 מציגה הפרעות קליטה לפרקים.")

    assert set(result.business_fields) == {"camera_id", "camera_status", "sector"}
    assert result.business_fields["sector"] == "south_sector"
    assert result.entities == ("CAM-08",)


def test_the_sector_comes_from_the_scoped_inventory(agent):
    assert _extract(agent, "מצלמה 03 נותקה.").business_fields["sector"] == "east_fence"
    assert _extract(agent, "מצלמה 05 נותקה.").business_fields["sector"] == "west_hill"


def test_a_heat_alert_is_extracted_with_the_stated_severity_and_sources(agent):
    result = _extract(
        agent,
        "חיישן טמפרטורה ומצלמה תרמית מציגים התראת חום נמוכה.",
    )

    assert result.classification == "operational_condition_report"
    assert result.business_fields["condition_type"] == "heat_alert"
    assert result.business_fields["severity_label"] == "low"
    assert result.business_fields["observation_source"] == "temperature sensor, thermal camera"


def test_no_location_or_qualification_is_invented_for_a_heat_alert(agent):
    """The old path asserted a fixed location, wind and heatwave qualification."""

    result = _extract(agent, "מצלמה תרמית מציגה התראת חום נמוכה.")

    assert "location" not in result.business_fields
    assert "qualification" not in result.business_fields
    assert result.entities == ()


def test_a_heat_alert_without_a_stated_severity_is_declined(agent):
    assert _extract(agent, "מצלמה תרמית מציגה התראת חום.") is None


def test_a_place_name_alone_no_longer_produces_a_heat_alert(agent):
    """`Oranim` used to trigger a fabricated heat alert on any message."""

    assert _extract(agent, "מדווח מרכס אורנים: הכוחות בעמדות.") is None


def test_a_grounded_report_commits_the_read_state(agent):
    result = _extract(agent, "מצלמה 08 מציגה הפרעות קליטה לפרקים.")
    ingestion = agent.ingest_report(
        {
            "event_id": "event-57",
            "classification": result.classification,
            "business_fields": dict(result.business_fields),
            "entities": list(result.entities),
            "description": result.description,
            "received_at": OCCURRED,
            "scenario_id": SCOPE.scenario_id,
            "scenario_run_id": SCOPE.scenario_run_id,
        },
        scope=SCOPE,
    )

    assert ingestion.status == "committed"
    camera = next(c for c in agent.surveillance_store.list_cameras(scope=SCOPE) if c["camera_id"] == "CAM-08")
    assert camera["status"] == "degraded"


def test_a_high_severity_heat_alert_is_accepted_by_ingestion(agent):
    result = _extract(agent, "חיישן טמפרטורה מציג התראת חום גבוהה.")
    assert result.business_fields["severity_label"] == "high"

    ingestion = agent.ingest_report(
        {
            "event_id": "event-57-high",
            "classification": result.classification,
            "business_fields": dict(result.business_fields),
            "description": result.description,
            "received_at": OCCURRED,
            "scenario_id": SCOPE.scenario_id,
            "scenario_run_id": SCOPE.scenario_run_id,
        },
        scope=SCOPE,
    )

    assert ingestion.status == "committed"


def test_an_ungrounded_severity_is_still_rejected_by_ingestion(agent):
    ingestion = agent.ingest_report(
        {
            "event_id": "event-57-bad",
            "classification": "operational_condition_report",
            "business_fields": {"condition_type": "heat_alert", "severity_label": "catastrophic"},
            "description": "x",
            "received_at": OCCURRED,
            "scenario_id": SCOPE.scenario_id,
            "scenario_run_id": SCOPE.scenario_run_id,
        },
        scope=SCOPE,
    )

    assert ingestion.status == "rejected"
