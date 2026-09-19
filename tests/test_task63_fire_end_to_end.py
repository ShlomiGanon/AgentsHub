"""Task 63: trusted FIRE_002 phase-one intake through terminal projections."""

from types import SimpleNamespace

from agents import AgentRegistry, FriendlyForcesAgent, SurveillanceAgent
from agents.team_status_agent import TeamStatusAgent
from history import HistoryQueryService
from orchestrator.flows import (
    FlowDeps,
    begin_report,
    continue_fast_path_report,
    prepare_fast_path_report,
)
from orchestrator.situational_picture import (
    SituationalQueryScope,
    build_typed_snapshot,
    build_operational_context,
)
from persistence import OperationalScope, open_persistence
from profiles import AreaRegistry, EventTypeRegistry, OptimizationPolicy, initialize_operational_scope, unified_test
from protocols import ProtocolSet


class _TempSurveillance(SurveillanceAgent):
    surveillance_seed_enabled = True
    surveillance_seed_profile = "profiles.unified_test"


def _deps(tmp_path, monkeypatch):
    team_path = str(tmp_path / "team.db")
    surveillance_path = str(tmp_path / "surveillance.db")
    monkeypatch.setattr(TeamStatusAgent, "status_db_path", str(tmp_path / "unused.db"))
    _TempSurveillance.surveillance_db_path = surveillance_path
    team = TeamStatusAgent("mock")
    surveillance = _TempSurveillance("mock")
    friendly = FriendlyForcesAgent("mock")
    team.register_member("omri_firefighter", "Omri Firefighter")
    team.approve_roster("station_commander", "2026-09-09T06:00:00+00:00")
    team.status_store.open_cycle(
        "2026-09-09", "2026-09-09T07:00:00+00:00", "2026-09-09T08:00:00+00:00"
    )
    persistence = open_persistence(str(tmp_path / "events.db"))
    registry = AgentRegistry({
        "team_status_agent": team,
        "surveillance_agent": surveillance,
        "friendly_forces_agent": friendly,
    })
    deps = FlowDeps(
        persistence=persistence,
        settings_store=None,
        registry=registry,
        protocol_set=ProtocolSet(tuple(unified_test.PROTOCOLS)),
        event_type_registry=EventTypeRegistry(tuple(unified_test.EVENT_TYPES)),
        area_registry=AreaRegistry(tuple(unified_test.AREAS)),
        history_query_service=HistoryQueryService(persistence, None),
        optimization_policy=OptimizationPolicy(operational_intake_mode="single", deterministic_execution_mode="direct"),
        timezone_name="Asia/Jerusalem",
        event_type_business_fields=unified_test.EVENT_TYPE_BUSINESS_FIELDS,
        group_owner=None,
    )
    return persistence, deps, team, surveillance, friendly


def _run_report(deps, text, *, owner, sender, step, time, run_id="fire-run-1"):
    deps = deps.__class__(**{**deps.__dict__, "group_owner": owner})
    scope = OperationalScope.simulation("FIRE_002_PHASE_1", run_id)
    baseline = {"team": {"members": [
        {"telegram_identity": "lahav_avi_shift_commander", "full_name": "Lahav"},
        {"telegram_identity": "omri_firefighter", "full_name": "Omri"},
        {"telegram_identity": "yuval_ashed3_commander", "full_name": "Yuval"},
    ]}}
    for agent in deps.registry.all():
        initializer = getattr(agent, "ensure_operational_scope", None)
        if callable(initializer):
            initializer(scope, baseline=baseline)
    plan = prepare_fast_path_report(
        deps, object(), text, time, False, time, scope,
    )
    assert plan is not None and plan.domain_only is True
    event_id = begin_report(
        deps,
        text,
        "telegram",
        time,
        sender,
        source_message_id=f"fire-{step}",
        simulation_context=SimpleNamespace(
            scenario_id="FIRE_002_PHASE_1",
            scenario_run_id=run_id,
            scenario_step=step,
            scenario_time=time,
        ),
    )
    result = continue_fast_path_report(deps, event_id, object(), object(), plan)
    assert result.outcome == "succeeded"
    return deps.persistence.fetch_event(event_id)


def test_fire_phase_one_steps_one_to_six_commit_expected_state_without_actions(tmp_path, monkeypatch):
    persistence, deps, team, surveillance, friendly = _deps(tmp_path, monkeypatch)
    try:
        _run_report(deps, "בוקר טוב. מעדכן סד\"כ פותח: 6 כבאים בצוות א', רכב אשד 3 וכרמל 1 במבצעיות מלאה.", owner="team_status_agent", sender="lahav_avi_shift_commander", step=1, time="2026-09-09T07:00:00+00:00")
        step2 = _run_report(deps, "מעדכן שאני צריך לצאת ב-12:00 לבדיקה רפואית תקופתית, חוזר למשמרת ב-15:00.", owner="team_status_agent", sender="omri_firefighter", step=2, time="2026-09-09T07:45:00+00:00")
        assert step2["availability_start"].startswith("2026-09-09T09:00")
        assert step2["availability_end"].startswith("2026-09-09T12:00")
        scope = OperationalScope.simulation("FIRE_002_PHASE_1", "fire-run-1")
        assert team.status_store.operational_state(scope=scope)["manpower_count"] == 6
        assert any(row["availability"] == "unavailable" for row in team.status_store.availability_snapshot("2026-09-09T10:00:00+00:00", scope=scope))

        condition = _run_report(deps, "חיישן טמפרטורה ומצלמה תרמית במגדל תצפית אורנים מציגים התראת חום נמוכה עקב שרב כבד ורוחות מזרחיות.", owner="surveillance_agent", sender="roni_surveillance_operator", step=3, time="2026-09-09T08:30:00+00:00")
        assert condition["classification"] == "operational_condition_report"
        assert condition["business_fields"]["severity_label"] == "low"
        assert "temperature_value" not in condition["business_fields"]
        assert "fire" not in condition["business_fields"]

        advisory = _run_report(deps, "לכל הגורמים: עקב השרב, הוצאנו הנחיה לאיסור הדלקת אש בכל היערות באזור. יערנים בסריקות.", owner="friendly_forces_agent", sender="kkl_mountains_sector", step=4, time="2026-09-09T09:15:00+00:00")
        assert advisory["business_fields"]["status"] == "active"

        maintenance = _run_report(deps, "מצלמה 02 (צומת המחצבה) הופסקה יזומית לטובת ניקוי עדשה עקב אבק כבד.", owner="surveillance_agent", sender="roni_surveillance_operator", step=5, time="2026-09-09T10:00:00+00:00")
        assert maintenance["business_fields"]["camera_id"] == "CAM-02"
        assert surveillance.surveillance_store.get_camera("CAM-02", scope=scope)["status"] == "offline"
        assert maintenance["business_fields"]["downtime_duration_hours"] is None

        incident = _run_report(deps, "דיווח על שריפת קוצים קטנה בצד כביש 444, כנראה מסיגריה. ניידת במקום, אין סיכון למבנים.", owner="friendly_forces_agent", sender="police_hub_agam", step=6, time="2026-09-09T11:00:00+00:00")
        assert incident["business_fields"]["cause_status"] == "unverified"
        assert incident["business_fields"]["building_risk"] == "none"
        assert all(persistence.fetch_event(event_id)["action_state"] is None for event_id in (step2["event_id"], condition["event_id"], advisory["event_id"], maintenance["event_id"], incident["event_id"]))
    finally:
        persistence.close()


def test_fire_run_reports_are_scoped_for_step_seven_typed_context(tmp_path, monkeypatch):
    persistence, deps, team, surveillance, friendly = _deps(tmp_path, monkeypatch)
    try:
        messages = (
            ("team_status_agent", "בוקר טוב. מעדכן סד\"כ פותח: 6 כבאים בצוות א', רכב אשד 3 וכרמל 1 במבצעיות מלאה.", "lahav_avi_shift_commander", 1, "2026-09-09T07:00:00+00:00"),
            ("team_status_agent", "מעדכן שאני צריך לצאת ב-12:00 לבדיקה רפואית תקופתית, חוזר למשמרת ב-15:00.", "omri_firefighter", 2, "2026-09-09T07:45:00+00:00"),
            ("surveillance_agent", "מצלמה 02 (צומת המחצבה) הופסקה יזומית לטובת ניקוי עדשה עקב אבק כבד.", "roni_surveillance_operator", 5, "2026-09-09T10:00:00+00:00"),
            ("friendly_forces_agent", "דיווח על שריפת קוצים קטנה בצד כביש 444, כנראה מסיגריה. ניידת במקום, אין סיכון למבנים.", "police_hub_agam", 6, "2026-09-09T11:00:00+00:00"),
        )
        for owner, text, sender, step, time in messages:
            _run_report(deps, text, owner=owner, sender=sender, step=step, time=time)
        snapshot = build_typed_snapshot(
            deps.registry,
            history_query_service=deps.history_query_service,
            now=None,
            scenario_time="2026-09-09T11:30:00+00:00",
            scenario_id="FIRE_002_PHASE_1",
            scenario_run_id="fire-run-1",
            scope=SituationalQueryScope.overall_scope(),
            operational_scope=OperationalScope.simulation("FIRE_002_PHASE_1", "fire-run-1"),
        )
        assert snapshot is not None
        assert snapshot.team is not None
        assert snapshot.team.operational_manpower == 6
        assert snapshot.team.effective_manpower == 5
        assert any("מצלמה 02" in report.text for report in snapshot.recent_reports)
        assert all("old-run" not in report.text for report in snapshot.recent_reports)
        context = build_operational_context(snapshot, query_scope=SituationalQueryScope.overall_scope(), current_time=snapshot.generated_at)
        assert any("כביש 444" in report.text for report in context.recent_reports)
    finally:
        persistence.close()
