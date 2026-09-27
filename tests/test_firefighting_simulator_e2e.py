import asyncio
import os
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
from profiles.simulation import simulation_user_telegram_id
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
            opening = fire.SIMULATIONS[0].raw["steps"][0]
            commander_id = simulation_user_telegram_id(0)
            first = await runtime.handle_message({
                "sender_identity": commander_id,
                "chat_id": commander_id,
                "chat_type": "private",
                "text": opening["text"],
                "event_time": opening["timestamp"],
                "protocol_hint": opening["protocol_hint"],
                "source_message_id": "e2e-opening",
            })
            assert "FIRE picture collected" not in first["reply_text"]
            await asyncio.sleep(0.25)
            picture_step = fire.SIMULATIONS[0].raw["steps"][6]
            second = await runtime.handle_message({
                "sender_identity": simulation_user_telegram_id(5),
                "chat_id": simulation_user_telegram_id(5),
                "chat_type": "private",
                "text": picture_step["text"],
                "event_time": picture_step["timestamp"],
                "protocol_hint": picture_step["protocol_hint"],
                "source_message_id": "e2e-picture",
            })
            assert "FIRE picture collected" not in second["reply_text"]
            watermark = (second["watermark"]["status_len"], second["watermark"]["sent_len"])
            delivered = ""
            for _ in range(30):
                await asyncio.sleep(0.2)
                delivered = runtime.poll_chat(simulation_user_telegram_id(5), watermark)["reply_text"]
                if delivered:
                    break
            assert "תמונת מצב מבצעית" in delivered
            assert "סיכונים" in delivered and "פערי מידע" in delivered
            assert "מספר רשומות" not in delivered
            assert "steps_completed" not in delivered
        finally:
            await runtime.shutdown()

    with RunningApiServer(ctx) as server:
        asyncio.run(scenario(server.base_url))
