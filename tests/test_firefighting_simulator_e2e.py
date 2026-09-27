import asyncio
import os
import time
import zlib
from types import SimpleNamespace

from api.app import ApiContext, build_app, build_group_routing
from bot.simulator_app import SimulatorRuntime
from bot.transports import HttpApiClient
from history.interface import SummaryScheduler
from history.query import HistoryQueryService
from messages import get_catalog
from orchestrator.flows import FlowDeps, SerialEventQueue
from persistence.sqlite_store import SQLitePersistence
from profiles import AreaRegistry, EventTypeRegistry
from profiles import firefighting as fire
from profiles.simulation import simulation_group_chat_id, simulation_user_telegram_id
from protocols.loader import ProtocolSet
from tests.api_fakes import FakeSettings, RunningApiServer
from tests.test_firefighting_demo_acceptance import _temporary_fire_paths
from agents.history import HistoryAgent
from agents.runtime import build_agent_registry


def test_fire_picture_through_real_simulator_http_and_job(tmp_path, monkeypatch):
    history_path, surveillance_path, crew_path, operations_path = _temporary_fire_paths(tmp_path, monkeypatch)
    persistence = SQLitePersistence(history_path)
    for persona in fire.SIMULATION_USERS:
        persistence.write_user(
            simulation_user_telegram_id(persona.offset), persona.permission_level, persona.full_name
        )
    persistence.write_user("bot-service", "commander")
    monkeypatch.setenv("BOT_SERVICE_KEY", "e2e-key")

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
    queue = SerialEventQueue(lambda item: item[1]())
    queue.start()
    loaded = SimpleNamespace(
        module_path="profiles.firefighting",
        profile_name="Firefighting",
        db_path=str(tmp_path / "bot.db"),
        default_language="he",
        message_catalog=get_catalog("he"),
        api_port=0,
        simulation_users=tuple(fire.SIMULATION_USERS),
        simulation_groups=tuple(fire.SIMULATION_GROUPS),
        simulator_port=0,
    )
    ctx = ApiContext(
        deps=deps,
        main_agent=SimpleNamespace(),
        insights_agent=SimpleNamespace(),
        loaded_profile=loaded,
        queue=queue,
        scheduler=SummaryScheduler(persistence, history_agent),
        group_routing=build_group_routing(persistence, registry),
    )

    async def scenario(server_url):
        loop = asyncio.get_running_loop()
        runtime = SimulatorRuntime(
            loaded,
            loop,
            api_client=HttpApiClient(server_url, bot_service_key="e2e-key"),
        )
        await runtime.startup()
        try:
            async def send_step(scenario_key, step):
                sender = next(persona for persona in fire.SIMULATION_USERS if persona.key == step["sender_identity"])
                sender_id = simulation_user_telegram_id(sender.offset)
                group = next((item for item in fire.SIMULATION_GROUPS if item.key == step["chat"]), None)
                chat_id = simulation_group_chat_id(group.offset) if group else sender_id
                chat_type = "supergroup" if group else "private"
                source = f"e2e-{scenario_key}-{step['step']}"
                started = time.perf_counter()
                initial = await runtime.handle_message({
                    "sender_identity": sender_id,
                    "chat_id": chat_id,
                    "chat_type": chat_type,
                    "text": step["text"],
                    "event_time": step["timestamp"],
                    "protocol_hint": step["protocol_hint"],
                    "source_message_id": source,
                })
                assert "FIRE picture collected" not in (initial["reply_text"] or "")
                hashed = str(zlib.crc32(source.encode("utf-8")) & 0x7FFFFFFF)
                event = None
                for _ in range(40):
                    event = persistence.fetch_event_by_source_message("telegram", sender_id, hashed)
                    if event and event.get("outcome") in {"succeeded", "failed"}:
                        break
                    await asyncio.sleep(0.1)
                assert event is not None and event["outcome"] == "succeeded", event
                job = await runtime.api_client.get_job_result(event["event_id"], sender_id)
                assert job is not None and job.user_response
                elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
                print(f"CHAT {scenario_key}/{step['step']} ({elapsed_ms} ms): {job.user_response}")
                return event, job, elapsed_ms

            outputs = {}
            for scenario in fire.SIMULATIONS[:2]:
                for step in scenario.raw["steps"]:
                    event, job, elapsed = await send_step(scenario.key, step)
                    outputs[(scenario.key, step["step"])] = (event, job, elapsed)
                    if scenario.key == "fire002_phase1" and step["step"] == 1:
                        cycle = registry.get("team_status_agent").status_store.find_cycle("shift-2026-09-09")
                        before_omri = registry.get("team_status_agent").status_store.availability_snapshot(
                            "2026-09-09T07:45:00Z", cycle_id=cycle["cycle_id"]
                        )
                        print(f"CREW BEFORE OMRI: {before_omri}")
                    if scenario.key == "fire002_phase1" and step["step"] == 2:
                        cycle = registry.get("team_status_agent").status_store.find_cycle("shift-2026-09-09")
                        after_omri = registry.get("team_status_agent").status_store.availability_snapshot(
                            "2026-09-09T11:30:00Z", cycle_id=cycle["cycle_id"]
                        )
                        print(f"CREW AFTER OMRI: {after_omri}")

            for key in (("fire002_phase1", 7), ("fire002_phase2", 8)):
                picture = outputs[key][1].user_response
                assert "תמונת מצב מבצעית" in picture
                assert "סיכונים" in picture and "פערי מידע" in picture
                assert "steps_completed" not in picture
                assert "FIRE simulation action applied" not in picture
            varied = dict(fire.SIMULATIONS[0].raw["steps"][6])
            varied["text"] = "מפקד, תן עכשיו סטטוס מבצעי עדכני של הכוח והנכסים, ומה עדיין לא אומת."
            varied["step"] = 99
            varied_event, varied_job, _ = await send_step("varied-picture", varied)
            assert "תמונת מצב מבצעית" in varied_job.user_response
            assert "לא ניתן" not in varied_job.user_response
        finally:
            await runtime.shutdown()

    with RunningApiServer(ctx) as server:
        asyncio.run(scenario(server.base_url))
