"""Task 55 coverage for trusted scenario-run identity and current-run history."""

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

from agents import project_report_facts
from api.app import build_app
from bot.contracts import MessageSubmissionResult
from bot.simulator_app import SimulatorRuntime
from bot.transports import current_simulation_context
from history.query import HistoryQueryService
from messages import get_catalog
from profiles import SimulationPersona, SimulationScenario, simulation_user_telegram_id
from tests.api_fakes import VIEWER_IDENTITY, auth_headers, build_context, happy_path_agent
from tests.bot_fakes import FakeBotApiClient


NOW = datetime(2026, 9, 18, 12, 0, tzinfo=timezone.utc)
SCENARIO_ID = "SEC_001_PHASE_1"


def _message_scenario() -> SimulationScenario:
    return SimulationScenario(
        key="sec001",
        title="SEC-001",
        raw={
            "scenario": {"id": SCENARIO_ID},
            "chats": [
                {"key": "viewer_dm", "kind": "message", "telegram_chat_type": "private"},
            ],
            "steps": [
                {
                    "step": 1,
                    "chat": "viewer_dm",
                    "sender_identity": "viewer",
                    "text": "status",
                    "timestamp": "2026-09-18T10:00:00Z",
                },
            ],
        },
    )


def _event_scenario() -> SimulationScenario:
    return SimulationScenario(
        key="sensor",
        title="Sensor",
        raw={
            "scenario": {"id": SCENARIO_ID},
            "chats": [{"key": "sensor", "kind": "event"}],
            "steps": [
                {
                    "step": 1,
                    "chat": "sensor",
                    "sender_identity": "sensor-1",
                    "text": "regional report",
                    "timestamp": "2026-09-18T10:00:00Z",
                },
            ],
        },
    )


def _event(persistence, *, description, run_id=None, step=1, outcome="succeeded", source_message_id=None):
    event_id = persistence.append_event(
        {
            "source": "telegram",
            "raw_text": description,
            "sender_identity": "9000000000000001",
            "received_at": "2026-09-18T10:00:00+00:00",
            "source_message_id": source_message_id,
            "scenario_id": SCENARIO_ID if run_id is not None else None,
            "scenario_run_id": run_id,
            "scenario_step": step,
            "scenario_time": f"2026-09-18T10:{step:02d}:00Z",
            "classification": "friendly_forces_report",
            "description": description,
            "outcome": outcome,
        }
    )
    return event_id


def test_current_run_history_excludes_previous_run_null_run_and_failed_events(tmp_path):
    from persistence import open_persistence

    persistence = open_persistence(str(tmp_path / "history.db"))
    try:
        _event(persistence, description="Run A Eli report", run_id="run-a", step=1)
        _event(persistence, description="Run A regional report", run_id="run-a", step=2)
        _event(persistence, description="Run B Eli report", run_id="run-b", step=1)
        _event(persistence, description="Run B ATV report", run_id="run-b", step=2)
        _event(persistence, description="Legacy NULL-run report", run_id=None, step=3)
        _event(persistence, description="Run B failed report", run_id="run-b", step=3, outcome="failed")

        service = HistoryQueryService(persistence, None)
        current = service.recent_committed_events(
            now=NOW,
            scenario_id=SCENARIO_ID,
            scenario_run_id="run-b",
            limit=10,
        )
        assert [event["description"] for event in current] == ["Run B Eli report", "Run B ATV report"]

        production_history = service.recent_committed_events(now=NOW, limit=20)
        descriptions = {event["description"] for event in production_history}
        assert "Run A Eli report" in descriptions
        assert "Legacy NULL-run report" in descriptions
    finally:
        persistence.close()


def test_same_scenario_run_id_is_exactly_deduplicated_by_source_message_id(tmp_path):
    from persistence import open_persistence

    persistence = open_persistence(str(tmp_path / "history.db"))
    try:
        first = _event(
            persistence,
            description="same source message",
            run_id="run-b",
            source_message_id="source-1",
        )
        duplicate = persistence.append_event(
            {
                "event_id": "forced-duplicate",
                "source": "telegram",
                "raw_text": "same source message",
                "sender_identity": "9000000000000001",
                "received_at": "2026-09-18T10:01:00+00:00",
                "source_message_id": "source-1",
                "scenario_id": SCENARIO_ID,
                "scenario_run_id": "run-b",
                "scenario_step": 2,
                "scenario_time": "2026-09-18T10:01:00Z",
                "classification": "friendly_forces_report",
                "description": "duplicate must not persist",
                "outcome": "succeeded",
            }
        )
        assert duplicate == first
        service = HistoryQueryService(persistence, None)
        events = service.recent_committed_events(
            now=NOW,
            scenario_id=SCENARIO_ID,
            scenario_run_id="run-b",
            limit=10,
        )
        assert [event["event_id"] for event in events] == [first]
    finally:
        persistence.close()


def test_domain_projection_retains_scenario_run_provenance():
    projection = project_report_facts(
        {
            "event_id": "event-1",
            "source": "telegram",
            "source_message_id": "source-1",
            "scenario_id": SCENARIO_ID,
            "scenario_run_id": "run-b",
            "scenario_step": 2,
            "scenario_time": "2026-09-18T10:01:00Z",
            "description": "ATV report",
        },
        domain="friendly_forces",
        projection_kind="operational_fact",
    )
    assert projection.scenario_id == SCENARIO_ID
    assert projection.scenario_run_id == "run-b"


def test_simulator_generates_one_run_and_replay_gets_a_new_one(tmp_path):
    persona = SimulationPersona(key="viewer", offset=2, permission_level="viewer", full_name="V")
    loaded = SimpleNamespace(
        module_path="profiles.test_sim",
        profile_name="Test Sim",
        db_path=str(tmp_path / "sim.db"),
        message_catalog=get_catalog("en"),
        api_port=0,
        simulator_port=8999,
        simulation_users=(persona,),
        simulation_groups=(),
        simulations=(_message_scenario(),),
    )
    runtime = SimulatorRuntime(loaded, asyncio.new_event_loop(), api_client=FakeBotApiClient())
    first = asyncio.run(runtime.start_scenario_run(SCENARIO_ID))
    resumed = asyncio.run(runtime.start_scenario_run(SCENARIO_ID, first["scenario_run_id"]))
    replay = asyncio.run(runtime.start_scenario_run(SCENARIO_ID))
    assert first["scenario_run_id"] == resumed["scenario_run_id"]
    assert first["scenario_run_id"] != replay["scenario_run_id"]
    runtime.loop.close()


def test_simulator_propagates_trusted_run_id_to_message_context(tmp_path):
    persona = SimulationPersona(key="viewer", offset=2, permission_level="viewer", full_name="V")
    loaded = SimpleNamespace(
        module_path="profiles.test_sim",
        profile_name="Test Sim",
        db_path=str(tmp_path / "sim.db"),
        message_catalog=get_catalog("en"),
        api_port=0,
        simulator_port=8999,
        simulation_users=(persona,),
        simulation_groups=(),
        simulations=(_message_scenario(),),
    )
    api_client = FakeBotApiClient(
        users={simulation_user_telegram_id(persona.offset): "viewer"},
        message_submission_result=MessageSubmissionResult(kind="conversational", answer_text="ok"),
    )
    observed = []
    original = api_client.submit_message

    async def capture(*args, **kwargs):
        observed.append(current_simulation_context())
        return await original(*args, **kwargs)

    api_client.submit_message = capture
    runtime = SimulatorRuntime(loaded, asyncio.new_event_loop(), api_client=api_client)

    async def scenario():
        await runtime.startup()
        run = await runtime.start_scenario_run(SCENARIO_ID)
        try:
            await runtime.handle_message(
                {
                    "sender_identity": simulation_user_telegram_id(persona.offset),
                    "chat_id": simulation_user_telegram_id(persona.offset),
                    "chat_type": "private",
                    "text": "status",
                    "source_message_id": "source-1",
                    "scenario_id": SCENARIO_ID,
                    "scenario_step": 1,
                    "scenario_time": "2026-09-18T10:00:00Z",
                    "scenario_run_id": run["scenario_run_id"],
                }
            )
        finally:
            await runtime.shutdown()

    try:
        asyncio.run(scenario())
    finally:
        runtime.loop.close()
    assert len(observed) == 1
    assert observed[0].scenario_id == SCENARIO_ID
    assert observed[0].scenario_run_id


def test_simulator_propagates_trusted_run_id_to_sensor_event_context(tmp_path):
    loaded = SimpleNamespace(
        module_path="profiles.test_sim",
        profile_name="Test Sim",
        db_path=str(tmp_path / "sim.db"),
        message_catalog=get_catalog("en"),
        api_port=0,
        simulator_port=8999,
        simulation_users=(),
        simulation_groups=(),
        simulations=(_event_scenario(),),
    )
    api_client = FakeBotApiClient()
    observed = []
    original = api_client.submit_event

    async def capture(*args, **kwargs):
        observed.append(current_simulation_context())
        return await original(*args, **kwargs)

    api_client.submit_event = capture
    runtime = SimulatorRuntime(loaded, asyncio.new_event_loop(), api_client=api_client)
    run = asyncio.run(runtime.start_scenario_run(SCENARIO_ID))
    try:
        asyncio.run(
            runtime.handle_event(
                {
                    "sender_identity": "sensor-1",
                    "text": "regional report",
                    "source_message_id": "sensor-source-1",
                    "scenario_id": SCENARIO_ID,
                    "scenario_step": 1,
                    "scenario_time": "2026-09-18T10:00:00Z",
                    "scenario_run_id": run["scenario_run_id"],
                }
            )
        )
    finally:
        runtime.loop.close()
    assert len(observed) == 1
    assert observed[0].scenario_id == SCENARIO_ID
    assert observed[0].scenario_run_id == run["scenario_run_id"]


def test_untrusted_production_message_cannot_inject_simulation_run_context(tmp_path):
    agent = happy_path_agent(intent="conversational")
    agent._dispatch["Reply naturally and directly"] = "ok"
    ctx = build_context(tmp_path, main_agent=agent)
    try:
        client = build_app(ctx).test_client()
        response = client.post(
            "/Msg",
            headers=auth_headers(VIEWER_IDENTITY),
            json={
                "text": "hello",
                "sender_identity": VIEWER_IDENTITY,
                "scenario_id": SCENARIO_ID,
                "scenario_run_id": "spoofed",
            },
        )
        assert response.status_code == 200
        assert ctx.deps.persistence.fetch_events_range("2000-01-01", "2100-01-01") == []
    finally:
        ctx.queue.stop()
        ctx.deps.persistence.close()


def test_simulation_context_requires_service_proof_for_run_headers(tmp_path):
    ctx = build_context(tmp_path, main_agent=happy_path_agent(intent="conversational"))
    try:
        client = build_app(ctx).test_client()
        headers = auth_headers(VIEWER_IDENTITY)
        headers.update(
            {
                "X-Simulation-Mode": "true",
                "X-Simulation-ID": SCENARIO_ID,
                "X-Simulation-Step": "1",
                "X-Simulation-Time": "2026-09-18T10:00:00Z",
                "X-Simulation-Run-ID": "spoofed",
            }
        )
        response = client.post("/Msg", headers=headers, json={"text": "hello", "sender_identity": VIEWER_IDENTITY})
        assert response.status_code == 403
    finally:
        ctx.queue.stop()
        ctx.deps.persistence.close()
