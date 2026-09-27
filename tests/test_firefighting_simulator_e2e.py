"""Local HTTP/SimulatorRuntime contracts; model replies are explicitly scripted."""

import asyncio
import dataclasses
import json
import time
from types import SimpleNamespace

from agents.contracts import AgentResult
from api.app import ApiContext, build_app, build_group_routing
from bot.simulator_app import SimulatorRuntime
from bot.transports import HttpApiClient
from history.interface import SummaryScheduler
from messages import get_catalog
from orchestrator.flows import SerialEventQueue, begin_report
from orchestrator.reasoning import IntentResult, MessagePlan
from profiles import firefighting as fire
from profiles.simulation import simulation_user_telegram_id
from tests.api_fakes import RunningApiServer
from tests.test_firefighting_demo_acceptance import build_fire_deps, seed_fire_run
from tests.test_situational_picture import FakeMainAgent


def _fire_http_context(tmp_path, deps, monkeypatch, main=None, history_service=None):
    deps.persistence.write_user("bot-service", "commander")
    monkeypatch.setenv("BOT_SERVICE_KEY", "e2e-key")
    main = main or FakeMainAgent(
        plan_text=json.dumps({"domains": [], "recent_events_hours": 12}),
        compose_text="תמונת מצב מקומית",
    )
    queue = SerialEventQueue(lambda item: item[1]())
    queue.start()
    loaded = SimpleNamespace(
        module_path="profiles.firefighting", profile_name="Firefighting",
        db_path=str(tmp_path / "bot.db"), default_language="he",
        message_catalog=get_catalog("he"), api_port=0, simulator_port=0,
        simulation_users=tuple(fire.SIMULATION_USERS), simulation_groups=tuple(fire.SIMULATION_GROUPS),
        conversation_history_turns=6, conversation_history_ttl_hours=24,
    )
    api_deps = dataclasses.replace(deps, history_query_service=history_service) if history_service else deps
    ctx = ApiContext(
        deps=api_deps, main_agent=main, insights_agent=main, loaded_profile=loaded, queue=queue,
        scheduler=SummaryScheduler(deps.persistence, deps.registry.get("history_agent")),
        group_routing=build_group_routing(deps.persistence, deps.registry),
    )
    return ctx, queue


def test_fire_followup_through_simulator_http_has_one_reply_and_fresh_run_context(tmp_path, monkeypatch):
    deps = build_fire_deps(tmp_path, monkeypatch)
    seed_fire_run(deps)
    deps.persistence.write_user("bot-service", "commander")
    monkeypatch.setenv("BOT_SERVICE_KEY", "e2e-key")
    final_text = "לפי דיווחי הריצה, חזרת עמרי טרם אומתה. מומלץ לאמת את זמינות הכוח לפני הקצאה נוספת."
    main = FakeMainAgent(
        plan_text=json.dumps({"domains": [], "recent_events_hours": 12}),
        compose_text=final_text,
    )
    specialist_calls = []
    for name in ("surveillance_agent", "team_status_agent", "friendly_forces_agent"):
        agent = deps.registry.get(name)
        def read(prompt, tools, *, invocation_policy=None, agent=agent):
            specialist_calls.append((agent.name, prompt, tools))
            if agent.name == "team_status_agent":
                return AgentResult("success", agent.report_team_availability("2026-09-09T12:50:00+00:00"))
            if agent.name == "surveillance_agent":
                return AgentResult("success", agent.get_surveillance_overview())
            return AgentResult("success", "טרם אושרה הגעת סיוע")
        monkeypatch.setattr(agent, "process", read)
    plans = []
    def plan(_main, protocols, text, registry, history, previous, context):
        plans.append((text, previous))
        return MessagePlan(IntentResult("question", "local scripted follow-up classification"))
    monkeypatch.setattr("api.routes.plan_message", plan)
    queue = SerialEventQueue(lambda item: item[1]())
    queue.start()
    loaded = SimpleNamespace(
        module_path="profiles.firefighting", profile_name="Firefighting",
        db_path=str(tmp_path / "bot.db"), default_language="he",
        message_catalog=get_catalog("he"), api_port=0, simulator_port=0,
        simulation_users=tuple(fire.SIMULATION_USERS), simulation_groups=tuple(fire.SIMULATION_GROUPS),
        conversation_history_turns=6, conversation_history_ttl_hours=24,
    )
    ctx = ApiContext(
        deps=deps, main_agent=main, insights_agent=main, loaded_profile=loaded, queue=queue,
        scheduler=SummaryScheduler(deps.persistence, deps.registry.get("history_agent")),
        group_routing=build_group_routing(deps.persistence, deps.registry),
    )

    async def scenario(url):
        runtime = SimulatorRuntime(loaded, asyncio.get_running_loop(),
                                   api_client=HttpApiClient(url, bot_service_key="e2e-key"))
        await runtime.startup()
        sender = simulation_user_telegram_id(5)
        try:
            for index, text in enumerate(("תמונת מצב", "תבדוק לעומק עם כל הסוכנים ותן תמונה עדכנית")):
                started = time.perf_counter()
                result = await runtime.handle_message({
                    "sender_identity": sender, "chat_id": sender, "chat_type": "private",
                    "text": text, "event_time": "2026-09-09T14:30:00",
                    "source_message_id": f"local-contract-{index}",
                })
                assert time.perf_counter() - started < 10  # local transport, not a model latency claim
                assert result["reply_text"] == final_text
                mark = result["watermark"]
                extra = runtime.poll_chat(sender, (mark["status_len"], mark["sent_len"]))
                assert not extra["reply_text"]
            assert plans and any(row["content"] == final_text for row in plans[-1][1])
            assert plans[-1][0] == "תבדוק לעומק עם כל הסוכנים ותן תמונה עדכנית"
            assert len(specialist_calls) == 6
            assert all("2026-09-09" in item[1] for item in specialist_calls)
            assert all("2026-09-09T14:30:00+03:00" in item[1] for item in specialist_calls), [
                item[1][item[1].find("FIRE simulation scenario clock"):][:100] for item in specialist_calls
            ]
        finally:
            await runtime.shutdown()

    try:
        with RunningApiServer(ctx) as server:
            asyncio.run(scenario(server.base_url))
    finally:
        queue.stop()
        deps.persistence.close()


def test_fire_first_message_links_one_active_run_and_history_uses_that_same_run(tmp_path, monkeypatch):
    deps = build_fire_deps(tmp_path, monkeypatch)
    old_run_event = begin_report(
        deps, "דיווח ישן שאינו שייך לריצה הנוכחית", "telegram", "2026-09-27T08:00:00",
        simulation_user_telegram_id(5), source_message_id="old-run-report",
        occurred_at="2026-09-09T07:30:00", simulation_context="FIRE_SIMULATION",
    )
    run_event = seed_fire_run(deps)
    sender = simulation_user_telegram_id(5)
    conversation = f"telegram:{sender}:main"
    ctx, queue = _fire_http_context(tmp_path, deps, monkeypatch)

    try:
        client = build_app(ctx).test_client()
        headers = {"X-Identity": sender}
        linked = client.post("/Msg", headers=headers, json={
            "text": "למה?", "sender_identity": sender, "source_message_id": "link-1",
            "conversation_id": conversation,
        })
        assert "חיברתי את השיחה" in linked.json["answer"]
        rows = deps.persistence.fetch_conversation_messages(conversation, 10)
        assert any(row["event_id"] == run_event for row in rows)
        why_again = client.post("/Msg", headers=headers, json={
            "text": "למה?", "sender_identity": sender, "source_message_id": "why-again",
            "conversation_id": conversation,
        })
        assert "מבודד את ההיסטוריה" in why_again.json["answer"]

        history = client.post("/Msg", headers=headers, json={
            "text": "תן היסטוריה של דיווחים", "sender_identity": sender,
            "source_message_id": "history-1", "conversation_id": conversation,
            "protocol_hint": "query_historical_incidents",
        })
        assert "פתיחת משמרת: שישה כבאים זמינים" in history.json["answer"]
        assert "דיווח ישן שאינו שייך לריצה הנוכחית" not in history.json["answer"]

        first_request_conversation = f"telegram:{sender}:first-request"
        team_agent = deps.registry.get("team_status_agent")
        monkeypatch.setattr(
            team_agent, "process",
            lambda *_args, **_kwargs: AgentResult("success", "סד״כ הפתיחה המאומת הוא שישה כבאים."),
        )
        first_request = client.post("/Msg", headers=headers, json={
            "text": "מה מצב הצוות?", "sender_identity": sender, "source_message_id": "first-fire-request",
            "conversation_id": first_request_conversation, "protocol_hint": "report_crew_status",
        })
        assert first_request.status_code == 200
        linked_first_request = deps.persistence.fetch_conversation_messages(first_request_conversation, 10)
        assert any(row["event_id"] == run_event for row in linked_first_request)
        assert old_run_event not in {row["event_id"] for row in deps.persistence.fetch_conversation_messages(conversation, 10)}
    finally:
        queue.stop()
        deps.persistence.close()


def test_fire_unlinked_chat_gets_friendly_no_run_and_unregistered_user_cannot_link(tmp_path, monkeypatch):
    deps = build_fire_deps(tmp_path, monkeypatch)
    ctx, queue = _fire_http_context(tmp_path, deps, monkeypatch)
    sender = simulation_user_telegram_id(5)
    conversation = f"telegram:{sender}:main"

    try:
        client = build_app(ctx).test_client()
        headers = {"X-Identity": sender}
        no_run = client.post("/Msg", headers=headers, json={
            "text": "איך מתחברים?", "sender_identity": sender, "source_message_id": "connect-1",
            "conversation_id": conversation,
        })
        assert "אין כרגע ריצת FIRE פעילה" in no_run.json["answer"]
        denied = client.post("/Msg", headers={"X-Identity": "unregistered-fire-user"}, json={
            "text": "תמונת מצב", "sender_identity": "unregistered-fire-user",
            "source_message_id": "blocked-1", "conversation_id": "telegram:bad:main",
        })
        assert denied.status_code == 401
        assert deps.persistence.fetch_conversation_messages("telegram:bad:main", 10) == []
    finally:
        queue.stop()
        deps.persistence.close()


def test_fire_chat_does_not_switch_to_a_new_run_without_explicit_relink(tmp_path, monkeypatch):
    deps = build_fire_deps(tmp_path, monkeypatch)
    previous_run_event = seed_fire_run(deps, source="run-before")
    sender = simulation_user_telegram_id(5)
    conversation = f"telegram:{sender}:main"
    ctx, queue = _fire_http_context(tmp_path, deps, monkeypatch)

    try:
        client = build_app(ctx).test_client()
        headers = {"X-Identity": sender}
        linked = client.post("/Msg", headers=headers, json={
            "text": "לאיזו ריצה אני מחובר?", "sender_identity": sender,
            "source_message_id": "run-before-link", "conversation_id": conversation,
        })
        assert "חיברתי את השיחה" in linked.json["answer"]
        assert any(row["event_id"] == previous_run_event for row in deps.persistence.fetch_conversation_messages(conversation, 10))

        current_run_event = seed_fire_run(
            deps, source="run-after", received="2026-09-27T10:00:00",
        )
        denied_switch = client.post("/Msg", headers=headers, json={
            "text": "תמונת מצב", "sender_identity": sender,
            "source_message_id": "no-implicit-switch", "conversation_id": conversation,
        })
        assert "לא העברתי אותה אוטומטית" in denied_switch.json["answer"]
        assert not any(row["event_id"] == current_run_event for row in deps.persistence.fetch_conversation_messages(conversation, 10))

        relinked = client.post("/Msg", headers=headers, json={
            "text": "חבר אותי לריצה הפעילה", "sender_identity": sender,
            "source_message_id": "explicit-switch", "conversation_id": conversation,
        })
        assert "חיברתי את השיחה" in relinked.json["answer"]
        assert any(row["event_id"] == current_run_event for row in deps.persistence.fetch_conversation_messages(conversation, 10))
    finally:
        queue.stop()
        deps.persistence.close()
