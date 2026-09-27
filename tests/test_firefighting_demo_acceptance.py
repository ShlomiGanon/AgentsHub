import sqlite3

from agents.history import HistoryAgent
from agents.runtime import build_agent_registry
from history.query import HistoryQueryService
from orchestrator.flows import FlowDeps, begin_report, run_report_extraction
from persistence import FirefightingOperationsStore, open_surveillance_persistence, open_team_status_persistence
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


def test_fire002_three_phases_share_state_and_a_new_run_is_clean(tmp_path, monkeypatch):
    history_path, surveillance_path, crew_path, operations_path = _temporary_fire_paths(tmp_path, monkeypatch)
    persistence = SQLitePersistence(history_path)
    for persona in fire.SIMULATION_USERS:
        persistence.write_user(
            simulation_user_telegram_id(persona.offset), persona.permission_level
        )

    history_agent = HistoryAgent(model="m")
    profile_agents = [
        fire.FirefightingSurveillanceAgent(model="m"),
        fire.FirefightingCrewStatusAgent(model="m"),
        fire.FirefightingExternalForcesAgent(model="m"),
    ]
    registry = build_agent_registry({"history_agent": history_agent}, profile_agents)
    settings = FakeSettings()
    deps = FlowDeps(
        persistence=persistence,
        settings_store=settings,
        registry=registry,
        protocol_set=ProtocolSet(protocols=fire.PROTOCOLS),
        event_type_registry=EventTypeRegistry(types=tuple(fire.EVENT_TYPES) + ("human_activation",)),
        area_registry=AreaRegistry(areas=tuple(fire.AREAS)),
        history_query_service=HistoryQueryService(persistence, history_agent, settings),
        optimization_policy=fire.OPTIMIZATION_POLICY,
    )

    class _NoModel:
        def process(self, *_args, **_kwargs):
            raise AssertionError("FIRE simulation fast path unexpectedly invoked a model")

    run_results = []
    for scenario in fire.SIMULATIONS:
        for step in scenario.raw["steps"]:
            persona = next(p for p in fire.SIMULATION_USERS if p.key == step["sender_identity"])
            sender = simulation_user_telegram_id(persona.offset)
            source_id = f"FIRE_002_RUN_A:{scenario.key}:{step['step']}"
            event_id = begin_report(
                deps,
                step["text"],
                "telegram",
                step["timestamp"],
                sender,
                source_message_id=source_id,
                occurred_at=step["timestamp"],
                simulation_context="FIRE_SIMULATION",
            )
            result = run_report_extraction(
                deps, event_id, _NoModel(), _NoModel(), selected_protocol_name=step["protocol_hint"]
            )
            assert result.outcome == "succeeded", (scenario.key, step["step"], result)
            if step["protocol_hint"] == "overall_situational_picture":
                event = persistence.fetch_event(event_id)
                picture = event["user_response"]
                assert "תמונת מצב" in picture
                assert "FIRE picture collected" not in picture
                assert "סיכונים" in picture and "פערי מידע" in picture
            run_results.append(result)

    assert len(run_results) == 24
    with sqlite3.connect(history_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 24
    assert {row["camera_id"] for row in open_surveillance_persistence(surveillance_path).list_cameras()} == {"CAM-01", "CAM-02", "CAM-03"}
    assert len(open_surveillance_persistence(surveillance_path).list_drones()) == 2
    assert open_team_status_persistence(crew_path).list_vehicles()[0]["status"] in {"available", "dispatched"}
    force_ids = {row["force_id"] for row in FirefightingOperationsStore(operations_path).list_external_forces()}
    assert {"kkl_tractors", "police", "citizen_trapped_report", "district_support"} <= force_ids
    assert FirefightingOperationsStore(operations_path).list_updates()
    camera_two = open_surveillance_persistence(surveillance_path).get_camera("CAM-02")
    assert camera_two["last_updated"] == "2026-09-09T10:00:00"
    incident_updates = FirefightingOperationsStore(operations_path).list_updates()
    assert not any(update["source_message_id"].endswith(":incident") for update in incident_updates)

    # A second opening step has a different execution/source ID. It resets only
    # current operational state; the first run's event/update history remains.
    first_step = fire.SIMULATIONS[0].raw["steps"][0]
    commander = next(p for p in fire.SIMULATION_USERS if p.key == first_step["sender_identity"])
    event_id = begin_report(
        deps,
        first_step["text"],
        "telegram",
        first_step["timestamp"],
        simulation_user_telegram_id(commander.offset),
        source_message_id="FIRE_002_RUN_B:fire002_phase1:1",
        occurred_at=first_step["timestamp"],
        simulation_context="FIRE_SIMULATION",
    )
    assert run_report_extraction(
        deps, event_id, _NoModel(), _NoModel(), selected_protocol_name=first_step["protocol_hint"]
    ).outcome == "succeeded"
    with sqlite3.connect(history_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 25
    assert open_surveillance_persistence(surveillance_path).get_drone("DRONE-01")["status"] == "ready"
