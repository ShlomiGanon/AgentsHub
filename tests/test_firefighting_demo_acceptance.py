from agents.history import HistoryAgent
from agents.runtime import build_agent_registry
from history.query import HistoryQueryService
from orchestrator.flows import FlowDeps, begin_report, run_report_extraction
from persistence import open_team_status_persistence
from persistence.sqlite_store import SQLitePersistence
from profiles import AreaRegistry, EventTypeRegistry
from profiles import firefighting as fire
from profiles.simulation import simulation_user_telegram_id
from protocols.loader import ProtocolSet
from tests.api_fakes import FakeSettings


def _temporary_fire_paths(tmp_path, monkeypatch):
    history = str(tmp_path / "history.db")
    surveillance = str(tmp_path / "surveillance.db")
    crew = str(tmp_path / "crew.db")
    operations = str(tmp_path / "operations.db")
    monkeypatch.setattr(fire, "DB_PATH", history)
    monkeypatch.setattr(fire, "FIREFIGHTING_SURVEILLANCE_DB_PATH", surveillance)
    monkeypatch.setattr(fire, "FIREFIGHTING_CREW_STATUS_DB_PATH", crew)
    monkeypatch.setattr(fire, "FIREFIGHTING_OPERATIONS_DB_PATH", operations)
    monkeypatch.setattr(fire.FirefightingSurveillanceAgent, "surveillance_db_path", surveillance)
    monkeypatch.setattr(fire.FirefightingCrewStatusAgent, "status_db_path", crew)
    monkeypatch.setattr(
        fire,
        "SIMULATION_ROSTERS",
        (fire.SimulationRoster(key="team_status", open=open_team_status_persistence, db_path=crew),),
    )
    fire.ensure_seed_data()
    return history, surveillance, crew, operations


def build_fire_deps(tmp_path, monkeypatch):
    history_path, _, _, _ = _temporary_fire_paths(tmp_path, monkeypatch)
    persistence = SQLitePersistence(history_path)
    for persona in fire.SIMULATION_USERS:
        persistence.write_user(simulation_user_telegram_id(persona.offset), persona.permission_level, persona.full_name)
    history_agent = HistoryAgent(model="local-test")
    registry = build_agent_registry({"history_agent": history_agent}, [
        fire.FirefightingSurveillanceAgent(model="local-test"),
        fire.FirefightingCrewStatusAgent(model="local-test"),
        fire.FirefightingExternalForcesAgent(model="local-test"),
    ])
    settings = FakeSettings()
    return FlowDeps(
        persistence=persistence, settings_store=settings, registry=registry,
        protocol_set=ProtocolSet(protocols=fire.PROTOCOLS),
        event_type_registry=EventTypeRegistry(types=tuple(fire.EVENT_TYPES) + ("human_activation",)),
        area_registry=AreaRegistry(areas=tuple(fire.AREAS)),
        history_query_service=HistoryQueryService(persistence, history_agent, settings),
        optimization_policy=fire.OPTIMIZATION_POLICY,
    )


def seed_fire_run(deps, source="opening", received="2026-09-27T09:00:00"):
    from agents import authenticated_request_identity
    instant = "2026-09-09T07:00:00+00:00"
    commander = simulation_user_telegram_id(0)
    surveillance = deps.registry.get("surveillance_agent")
    surveillance.operations_store.reset_current_state(now=instant, run_started_at=received)
    team = deps.registry.get("team_status_agent")
    with authenticated_request_identity(commander):
        assert "6 approved" in team.record_crew_shift_status("all", "available", source, "פתיחת משמרת: כולם זמינים", instant)
    event_id = begin_report(
        deps, "פתיחת משמרת: שישה כבאים זמינים", "telegram", received, commander,
        source_message_id=source, occurred_at=instant,
        sender_permission_level="commander", simulation_context="FIRE_SIMULATION",
    )
    return event_id


def test_shift_tools_ignore_scheduler_cycle_and_do_not_confirm_planned_return(tmp_path, monkeypatch):
    import json
    from agents import authenticated_request_identity
    deps = build_fire_deps(tmp_path, monkeypatch)
    seed_fire_run(deps)
    team = deps.registry.get("team_status_agent")
    team.status_store.open_cycle("daily-2026-09-27", "2026-09-27T07:00:00+00:00", "2026-09-27T08:00:00+00:00")
    before = json.loads(team.report_team_availability("2026-09-09T11:30:00+00:00"))
    assert before["confirmed_opening"] == 6
    assert sum(row["availability"] == "available" for row in before["crew"]) == 6
    pre_open = json.loads(team.report_team_availability("2026-09-09T09:00:00+03:00"))
    assert all(row["status"] == "unknown" and row["current_location"] == "unknown" for row in pre_open["vehicles"])
    with authenticated_request_identity(simulation_user_telegram_id(1)):
        result = team.record_attendance_response(
            source_message_id="omri", availability="unavailable",
            original_text="נעדר לבדיקה מ-12 עד 15", absence_reason="בדיקה",
            event_id="attendance-event-omri",
            received_at="2026-09-27T10:00:00+00:00",
            occurred_at="2026-09-09T09:00:00+00:00",
            availability_start="2026-09-09T09:00:00+00:00",
            availability_end="2026-09-09T12:00:00+00:00",
        )
    assert result == "The attendance response was stored."
    after = json.loads(team.report_team_availability("2026-09-09T11:30:00+00:00"))
    assert after["confirmed_opening"] == 6
    assert sum(row["availability"] == "available" for row in after["crew"]) == 5
    omri = next(row for row in after["crew"] if row["full_name"].startswith("רס\"ל עמרי"))
    assert omri["availability"] == "unavailable"
    assert omri["unavailable_until"] == "2026-09-09T12:00:00+00:00"
    stored_omri = next(row for row in team.status_store.list_responses() if row["source_message_id"].endswith(":omri"))
    assert stored_omri["received_at"] == "2026-09-27T10:00:00+00:00"
    assert stored_omri["occurred_at"] == "2026-09-09T09:00:00+00:00"
    later = json.loads(team.report_team_availability("2026-09-09T12:50:00+00:00"))
    assert sum(row["availability"] == "planned_return" for row in later["crew"]) == 1
    cycle = team.status_store.find_cycle("shift-2026-09-09")
    team.status_store.record_response(
        telegram_identity=simulation_user_telegram_id(1), source_message_id="confirmed-return",
        availability="available", original_text="חזרתי לתחנה", received_at="2026-09-27T10:10:00+00:00",
        occurred_at="2026-09-09T13:00:00+00:00", cycle_id=cycle["cycle_id"],
    )
    confirmed = json.loads(team.report_team_availability("2026-09-09T13:01:00+00:00"))
    returned_omri = next(row for row in confirmed["crew"] if row["telegram_identity"] == simulation_user_telegram_id(1))
    assert returned_omri["availability"] == "available"
    assert returned_omri["occurred_at"] == "2026-09-09T13:00:00+00:00"
    assert json.loads(team.report_team_availability("2026-09-08T12:00:00+00:00"))["crew"] is None
    deps.persistence.close()


def test_commander_roster_label_resolves_seeded_simulation_members_and_stays_out_of_daily_survey(tmp_path, monkeypatch):
    import json
    from agents import authenticated_request_identity

    deps = build_fire_deps(tmp_path, monkeypatch)
    team = deps.registry.get("team_status_agent")
    commander = simulation_user_telegram_id(0)
    named_members = ",".join(simulation_user_telegram_id(offset) for offset in (0, 1, 6))
    with authenticated_request_identity(commander):
        result = team.record_crew_shift_status(
            named_members, "available", "opening-all-six",
            "אבי אישר שכל ששת חברי צוות א זמינים: אבי, עמרי, יובל ושלושת אנשי צוות הסימולציה",
            "2026-09-27T09:00:00+00:00", "2026-09-09T07:00:00+00:00",
        )
    assert "6 approved member(s)" in result
    shift = team.status_store.find_cycle("shift-2026-09-09")
    stored = team.status_store.list_responses(cycle_id=shift["cycle_id"])
    assert len(stored) == 6
    assert {row["full_name"] for row in stored if "סימולציה" in row["full_name"]} == {
        persona.full_name for persona in fire.SIMULATION_USERS
        if persona.key in {"firefighter_team_a_4", "firefighter_team_a_5", "firefighter_team_a_6"}
    }

    with authenticated_request_identity(simulation_user_telegram_id(1)):
        assert team.record_attendance_response(
            source_message_id="omri-absence", availability="unavailable", original_text="בדיקה",
            absence_reason="בדיקה", event_id="omri-absence-event", received_at="2026-09-27T09:01:00+00:00",
            occurred_at="2026-09-09T09:00:00+00:00", availability_start="2026-09-09T09:00:00+00:00",
            availability_end="2026-09-09T12:00:00+00:00",
        ) == "The attendance response was stored."
    picture = json.loads(team.report_team_availability("2026-09-09T11:30:00+00:00"))
    assert picture["confirmed_opening"] == 6
    assert sum(row["availability"] == "available" for row in picture["crew"]) == 5
    omri = next(row for row in picture["crew"] if row["telegram_identity"] == simulation_user_telegram_id(1))
    assert omri["availability"] == "unavailable"
    assert omri["unavailable_until"] == "2026-09-09T12:00:00+00:00"

    # The shared bot polls this hook for scheduled roll calls; FIRE must not create one.
    assert team.open_scheduled_cycle("2026-09-27T18:00:00+00:00", force=True) is None
    assert team.status_store.find_cycle("2026-09-27") is None
    deps.persistence.close()


def test_fire_drone_dispatch_is_readable_at_its_scenario_time(tmp_path, monkeypatch):
    from agents import authenticated_request_identity

    deps = build_fire_deps(tmp_path, monkeypatch)
    seed_fire_run(deps)
    surveillance = deps.registry.get("surveillance_agent")
    with authenticated_request_identity(simulation_user_telegram_id(5)):
        result = surveillance.dispatch_drone_to_area(
            target_area="pine_ridge", incident_description="reported smoke", occurred_at="2026-09-09T14:30:00+03:00",
        )
    assert "Simulated drone dispatch recorded" in result
    overview = surveillance.get_surveillance_overview(as_of_iso="2026-09-09T14:31:00+03:00")
    assert "DRONE-01" in overview and "IN_FLIGHT" in overview
    assert "pine_ridge" in overview
    deps.persistence.close()


def test_current_run_history_contains_all_report_types_not_other_runs(tmp_path, monkeypatch):
    from orchestrator.firefighting_picture import fire_run_context
    deps = build_fire_deps(tmp_path, monkeypatch)
    old = seed_fire_run(deps, "old", "2026-09-26T09:00:00")
    current = seed_fire_run(deps, "current", "2026-09-27T09:00:00")
    report = begin_report(
        deps, "עשן ועומסי תנועה בכביש 444", "telegram", "2026-09-27T09:01:00",
        simulation_user_telegram_id(4), source_message_id="police-new",
        occurred_at="2026-09-09T12:22:00", simulation_context="FIRE_SIMULATION",
    )
    run = fire_run_context(deps.registry, deps.persistence)
    assert {row["event_id"] for row in run["events"]} == {current, report}
    assert old not in {row["event_id"] for row in run["events"]}
    assert run["events"][-1]["raw_text"] == "עשן ועומסי תנועה בכביש 444"
    assert "שריפת קוצים" not in run["events"][-1]["raw_text"]
    assert run["clock"] == "2026-09-09T12:22:00+03:00"
    deps.persistence.close()


def test_history_fallback_labels_field_reports_and_user_questions_separately():
    from orchestrator.firefighting_picture import format_fire_run_history

    history = format_fire_run_history((
        {"occurred_at": "2026-09-09T11:00:00+03:00", "sender_name": "אבי",
         "classification": "fire_incident", "raw_text": "עשן ליד הכביש"},
        {"occurred_at": "2026-09-09T11:05:00+03:00", "sender_name": "מפקד",
         "classification": "human_activation", "raw_text": "מה השתנה?"},
    ))

    assert "אבי (דיווח שטח): עשן ליד הכביש" in history
    assert "מפקד (שאלת משתמש): מה השתנה?" in history


def test_picture_excludes_same_run_reports_and_state_updates_after_requested_time(tmp_path, monkeypatch):
    import json
    from agents.contracts import AgentResult
    from orchestrator.firefighting_picture import build_fire_situational_picture
    from orchestrator.situational_picture import RECENT_EVENTS_DOMAIN
    from tests.test_situational_picture import FakeMainAgent

    deps = build_fire_deps(tmp_path, monkeypatch)
    seed_fire_run(deps, received="2026-09-27T09:00:00")
    deps.registry.get("surveillance_agent").operations_store.record_incident_update(
        source_message_id="stale-force-projection", event_id="previous-run-force",
        update_kind="external_force", summary="force from previous run",
        occurred_at="2026-09-09T13:00:00+03:00", received_at="2026-09-27T08:59:00+00:00",
        external_force={"force_id": "previous-run-force", "force_kind": "police", "count": 1,
                        "status": "reported", "location": "old area", "notes": "stale projection"},
    )
    future_event = begin_report(
        deps, "כוח נוסף יצא בשעה 15:00", "telegram", "2026-09-27T09:10:00",
        simulation_user_telegram_id(4), source_message_id="future-force-event",
        occurred_at="2026-09-09T15:00:00", simulation_context="FIRE_SIMULATION",
    )
    deps.registry.get("surveillance_agent").operations_store.record_incident_update(
        source_message_id="same-chat-message-id", event_id=future_event,
        update_kind="external_force", summary="כוח נוסף יצא בשעה 15:00",
        occurred_at="2026-09-09T15:00:00", received_at="2026-09-27T09:10:00",
        external_force={"force_id": "future-kkl-force", "force_kind": "kkl", "count": 1,
                        "status": "en_route", "location": "north", "notes": "future"},
    )
    for name in ("surveillance_agent", "team_status_agent", "friendly_forces_agent"):
        agent = deps.registry.get(name)
        monkeypatch.setattr(agent, "process", lambda *_args, **_kwargs: AgentResult("success", "נתונים נקראו"))
    picture = build_fire_situational_picture(
        deps.registry, "תמונת מצב ל־14:30", as_of_iso="2026-09-09T14:30:00",
        main_agent=FakeMainAgent(plan_text=json.dumps({"domains": [], "recent_events_hours": 12})),
        history_query_service=deps.history_query_service,
        protocol=deps.protocol_set.get("overall_situational_picture"),
        caller_identity=simulation_user_telegram_id(5), persistence=deps.persistence,
    )
    recent = next(report for report in picture.reports if report.domain == RECENT_EVENTS_DOMAIN)
    data = json.loads(recent.text)
    assert not any(row["event_id"] == future_event for row in data["events"])
    assert not any(row["force_id"] == "future-kkl-force" for row in data["external_forces"])
    assert not any(row["force_id"] == "previous-run-force" for row in data["external_forces"])
    deps.persistence.close()


def test_force_report_and_current_state_commit_atomically_per_event(tmp_path, monkeypatch):
    import json
    import pytest

    deps = build_fire_deps(tmp_path, monkeypatch)
    store = deps.registry.get("surveillance_agent").operations_store
    force_agent = deps.registry.get("friendly_forces_agent")
    assert force_agent.record_external_force_update(
        force_id="police-unit-1", force_kind="police", count=1, status="arrived_reported",
        location="Route 444", notes="reported", source_message_id="42", event_id="event-run-a",
        summary="Police unit reported on scene", occurred_at="2026-09-09T11:00:00+03:00",
    )
    assert force_agent.record_external_force_update(
        force_id="police-unit-1", force_kind="police", count=None, status="reported",
        location="unknown", notes="new report", source_message_id="42", event_id="event-run-b",
        summary="New report without count", occurred_at="2026-09-09T11:05:00+03:00",
    )
    assert len(store.list_updates()) == 2
    facts = json.loads(store.list_updates()[-1]["facts_json"])
    assert facts["external_force"]["count"] is None
    assert store.list_external_forces()[0]["count"] is None

    duplicate = force_agent.record_external_force_update(
        force_id="police-unit-1", force_kind="police", count=9, status="arrived",
        location="made-up", notes="must not overwrite", source_message_id="42", event_id="event-run-b",
        summary="duplicate with conflicting data", occurred_at="2026-09-09T11:06:00+03:00",
    )
    assert "already recorded" in duplicate
    assert store.list_external_forces()[0]["count"] is None
    assert store.list_external_forces()[0]["location"] == "unknown"

    stale = force_agent.record_external_force_update(
        force_id="police-unit-1", force_kind="police", count=9, status="en_route",
        location="old location", notes="older arrival time", source_message_id="43", event_id="event-run-c",
        summary="out-of-order report", occurred_at="2026-09-09T11:03:00+03:00",
    )
    assert "current state unchanged" in stale
    assert len(store.list_updates()) == 3
    assert store.list_external_forces()[0]["status"] == "reported"
    assert store.list_external_forces()[0]["location"] == "unknown"

    incident_id = "incident-order-check"
    store.record_incident_update(
        source_message_id="newer-fire", event_id="event-fire-newer", incident_id=incident_id,
        update_kind="fire_incident", summary="newer fire update", occurred_at="2026-09-09T12:00:00+03:00",
        received_at="2026-09-27T10:20:00+00:00", status="open", area="north_sector",
    )
    store.record_incident_update(
        source_message_id="older-fire", event_id="event-fire-older", incident_id=incident_id,
        update_kind="fire_incident", summary="older fire update", occurred_at="2026-09-09T11:00:00+03:00",
        received_at="2026-09-27T10:21:00+00:00", status="contained", area="south_sector",
    )
    state = store.get_incident(incident_id)
    assert state["status"] == "open" and state["area"] == "north_sector"

    with pytest.raises(ValueError, match="non-negative integer"):
        store.record_incident_update(
            source_message_id="failed", event_id="event-run-invalid", update_kind="external_force",
            summary="invalid count", received_at="2026-09-09T11:07:00+03:00",
            external_force={"force_id": "bad-force", "force_kind": "police", "count": "many",
                            "status": "reported", "location": "unknown", "notes": "invalid"},
        )
    assert "bad-force" not in {row["force_id"] for row in store.list_external_forces()}
    assert not any(row["event_id"] == "event-run-invalid" for row in store.list_updates())
    deps.persistence.close()


def test_tool_business_failure_does_not_become_successful_fire_event(tmp_path, monkeypatch):
    from orchestrator.flows import _finish_protocol_assessment
    from protocols import Step, StepOutcome
    from tests.test_situational_picture import FakeMainAgent

    deps = build_fire_deps(tmp_path, monkeypatch)
    seed_fire_run(deps)
    event_id = begin_report(
        deps, "תשובת נוכחות", "telegram", "2026-09-27T09:02:00",
        simulation_user_telegram_id(1), source_message_id="tool-failure",
        occurred_at="2026-09-09T12:02:00", sender_permission_level="viewer",
        simulation_context="FIRE_SIMULATION",
    )
    protocol = deps.protocol_set.get("record_crew_availability_response")
    step = Step("team_status_agent", "עדכן נוכחות", ("record_attendance_response",), step_id="attendance")
    result = _finish_protocol_assessment(
        deps, event_id, FakeMainAgent(), FakeMainAgent(), protocol,
        (StepOutcome(step, "The attendance response was not stored: no matching shift.", 1, True),),
        (), enforce_deadline=False,
    )
    assert result.outcome == "failed"
    assert deps.persistence.fetch_event(event_id)["outcome"] == "failed"
    deps.persistence.close()


def test_fire_executor_requires_invocation_and_uses_the_exact_tool_result():
    from agents.contracts import AgentResult, ToolInfo
    from protocols import Step
    from protocols.executor import execute_step_with_retry

    tool_info = ToolInfo("record_attendance_response", "records availability", True, True)
    step = Step("team_status_agent", "רשום היעדרות", (tool_info.name,))

    class ScriptedAgent:
        name = "team_status_agent"

        def __init__(self, result):
            self.result = result

        def exposed_tools(self):
            return (tool_info,)

        def process(self, *_args, **_kwargs):
            return self.result

    no_call = execute_step_with_retry(
        ScriptedAgent(AgentResult("success", "ההיעדרות נשמרה.")), step, FakeSettings(), fire_simulation=True,
    )
    assert not no_call.succeeded and no_call.status == "failed"

    rejected = execute_step_with_retry(
        ScriptedAgent(AgentResult(
            "success", "ההיעדרות נשמרה.",
            ((tool_info.name, "The attendance response was not stored: no matching FIRE shift."),),
        )), step, FakeSettings(), fire_simulation=True,
    )
    assert not rejected.succeeded and rejected.status == "failed"
    assert "not stored" in rejected.result_text

    stored = execute_step_with_retry(
        ScriptedAgent(AgentResult(
            "success", "החבר חזר למשמרת.", ((tool_info.name, "The attendance response was stored."),),
        )), step, FakeSettings(), fire_simulation=True,
    )
    assert stored.succeeded
    assert stored.result_text == "The attendance response was stored."


def test_fire_execution_envelope_preserves_event_time_and_receipt_time(tmp_path, monkeypatch):
    from orchestrator.flows import _execute_protocol_plan
    from protocols import ProtocolRunResult, Step, StepOutcome
    from tests.test_situational_picture import FakeMainAgent

    deps = build_fire_deps(tmp_path, monkeypatch)
    seed_fire_run(deps)
    event_id = begin_report(
        deps, "דיווח כוח חוץ", "telegram", "2026-09-27T10:00:00+00:00",
        simulation_user_telegram_id(4), source_message_id="two-clocks",
        occurred_at="2026-09-09T14:30:00+03:00", sender_permission_level="viewer",
        simulation_context="FIRE_SIMULATION",
    )
    step = Step("friendly_forces_agent", "עדכן כוח", ("record_external_force_update",), step_id="force")
    captured = {}
    def execute(steps, *_args, **_kwargs):
        captured["task"] = steps[0].task_text
        return ProtocolRunResult((StepOutcome(step, "External-force status recorded.", 1, True),), completed=True)
    monkeypatch.setattr("orchestrator.flows.execute_steps", execute)
    main = FakeMainAgent(compose_text="דיווח כוח החוץ נשמר.")
    _execute_protocol_plan(
        deps, event_id, main, main, deps.protocol_set.get("record_incident_update"), (step,), (),
    )
    assert '"received_at": "2026-09-27T10:00:00+00:00"' in captured["task"]
    assert '"scenario_time": "2026-09-09T14:30:00+03:00"' in captured["task"]
    assert '"event_id":' in captured["task"]
    deps.persistence.close()


def test_existing_force_database_migrates_without_losing_known_counts(tmp_path):
    import sqlite3
    from persistence.firefighting_operations import FirefightingOperationsStore

    path = tmp_path / "legacy-force.db"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE external_force_state (force_id TEXT PRIMARY KEY, force_kind TEXT NOT NULL, "
            "count INTEGER NOT NULL, status TEXT NOT NULL, location TEXT NOT NULL, notes TEXT NOT NULL, last_updated TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO external_force_state VALUES ('kkl-1', 'kkl', 2, 'en_route', 'north', 'reported', '2026-09-09T10:00:00+03:00')"
        )
    store = FirefightingOperationsStore(str(path))
    assert store.list_external_forces()[0]["count"] == 2
    with sqlite3.connect(path) as connection:
        count_column = next(row for row in connection.execute("PRAGMA table_info(external_force_state)") if row[1] == "count")
    assert count_column[3] == 0


def test_fire_picture_composition_keeps_long_operational_answer_intact():
    from agents.contracts import AgentResult
    from orchestrator.situational_picture import RECENT_EVENTS_DOMAIN, DomainReport, compose_situational_picture

    expected = "\n".join(f"ממצא מבצעי {index}: נבדק ומסומן." for index in range(1, 21))
    class Composer:
        def process(self, prompt, tools, *, invocation_policy=None):
            assert "in at most 30 short lines" in prompt
            assert invocation_policy.max_output_tokens == 1100
            return AgentResult("success", expected)

    actual = compose_situational_picture(
        Composer(), (DomainReport(RECENT_EVENTS_DOMAIN, "מצב", "דיווחי הריצה", True),),
        "תמונת מצב", current_time="14:30", recent_events_hours=12, fallback_text="כשל FIRE",
    )
    assert actual == expected


def test_protocol_hint_no_longer_bypasses_language_interpretation(tmp_path, monkeypatch):
    from agents.contracts import AgentResult
    deps = build_fire_deps(tmp_path, monkeypatch)
    seed_fire_run(deps)
    camera = deps.registry.get("surveillance_agent").surveillance_store.get_camera("CAM-03")
    prompts = []
    class InvalidModel:
        def process(self, prompt, tools):
            prompts.append(prompt)
            return AgentResult("success", "{}")
    event = begin_report(
        deps, "העין באורנים הפסיקה לזוז", "telegram", "2026-09-27T09:02:00",
        simulation_user_telegram_id(2), source_message_id="alias",
        occurred_at="2026-09-09T12:38:00", simulation_context="FIRE_SIMULATION",
    )
    result = run_report_extraction(deps, event, InvalidModel(), InvalidModel(),
                                   selected_protocol_name="update_camera_observation")
    assert prompts and "CAM-03" in prompts[0]
    assert "2026-09-09" in prompts[0]
    assert result.outcome != "succeeded"
    assert deps.registry.get("surveillance_agent").surveillance_store.get_camera("CAM-03") == camera
    deps.persistence.close()


def test_picture_uses_existing_agents_read_only_and_refreshes_evidence(tmp_path, monkeypatch):
    import json
    from agents.contracts import AgentResult
    from orchestrator.firefighting_picture import build_fire_situational_picture
    from tests.test_situational_picture import FakeMainAgent
    deps = build_fire_deps(tmp_path, monkeypatch)
    seed_fire_run(deps)
    calls = []
    for name in ("surveillance_agent", "team_status_agent", "friendly_forces_agent"):
        agent = deps.registry.get(name)
        def read(prompt, tools, *, invocation_policy=None, agent=agent):
            calls.append((agent.name, prompt, tools))
            assert not any(tool.side_effecting for tool in agent.exposed_tools() if tool.name in tools)
            if agent.name == "team_status_agent":
                return AgentResult("success", agent.report_team_availability("2026-09-09T11:30:00+00:00"))
            if agent.name == "surveillance_agent":
                return AgentResult("success", agent.get_surveillance_overview(as_of_iso="2026-09-09T14:30:00+03:00"))
            return AgentResult("success", "אין הגעה מאומתת")
        monkeypatch.setattr(agent, "process", read)
    main = FakeMainAgent(plan_text=json.dumps({"domains": [], "recent_events_hours": 12}))
    def picture():
        return build_fire_situational_picture(
            deps.registry, "תבדוק לעומק עם כל הסוכנים ותן תמונה עדכנית",
            as_of_iso="2026-09-09T14:30:00",
            main_agent=main, history_query_service=deps.history_query_service,
            protocol=deps.protocol_set.get("overall_situational_picture"),
            caller_identity=simulation_user_telegram_id(5), persistence=deps.persistence,
        )
    first = picture()
    surveillance = deps.registry.get("surveillance_agent")
    surveillance.update_camera_observation("CAM-03", "התמונה אינה חיה", "degraded", "2026-09-09T12:38:00+03:00")
    surveillance.surveillance_store.dispatch_drone(
        target_area="industrial_park", incident_description="future test", specific_drone_id="DRONE-01",
        now_iso="2026-09-09T15:00:00+03:00",
    )
    earlier = surveillance.get_surveillance_overview(as_of_iso="2026-09-09T12:30:00+03:00")
    assert "CAM-03" in earlier and "UNKNOWN" in earlier
    assert "התמונה אינה חיה" not in earlier
    at_1430 = surveillance.get_surveillance_overview(as_of_iso="2026-09-09T14:30:00+03:00")
    assert "[DRONE-01] תצפית-01: UNKNOWN" in at_1430
    assert "industrial_park" not in at_1430
    second = picture()
    assert first.text != second.text
    assert "התמונה אינה חיה" in second.text
    assert {row[0] for row in calls} == {"surveillance_agent", "team_status_agent", "friendly_forces_agent"}
    assert "תבדוק לעומק" in main.prompts[0]
    assert all("2026-09-09" in row[1] for row in calls)
    team_prompt = next(row[1] for row in calls if row[0] == "team_status_agent")
    assert "2026-09-09T14:30:00+03:00" in team_prompt
    deps.persistence.close()
