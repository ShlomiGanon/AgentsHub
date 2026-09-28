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
from persistence import EventSearchCriteria
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


def test_fire_simulator_attendance_poll_does_not_open_or_deliver_sec_roll_call(tmp_path, monkeypatch):
    from bot.background_services import run_attendance_check_once

    deps = build_fire_deps(tmp_path, monkeypatch)
    ctx, queue = _fire_http_context(tmp_path, deps, monkeypatch)
    ctx.group_routing.upsert("fire_response_team", "team_status_agent", "FIRE response team")

    class Telegram:
        sent = []

        async def send_with_buttons(self, chat_id, text, buttons):
            self.sent.append((chat_id, text, buttons))

    async def poll_and_deliver(url):
        api = HttpApiClient(url, bot_service_key="e2e-key")
        telegram = Telegram()
        service = SimpleNamespace(
            api_client=api, telegram_client=telegram,
            loaded_profile=ctx.loaded_profile,
        )
        try:
            prompted = await run_attendance_check_once(service)
            return prompted, telegram.sent
        finally:
            await api.close()

    try:
        with RunningApiServer(ctx) as server:
            prompted, sent = asyncio.run(poll_and_deliver(server.base_url))
        team = deps.registry.get("team_status_agent")
        assert prompted == 0
        assert sent == []
        assert team.status_store.list_cycles() == []
    finally:
        queue.stop()
        deps.persistence.close()


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
                    # UTC event timestamps must be converted to the FIRE scenario's local clock.
                    "text": text, "event_time": "2026-09-09T11:30:00Z",
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


def test_drone_dispatch_through_fire_simulator_uses_the_simulated_tool_result(tmp_path, monkeypatch):
    from agents import base, authenticated_request_identity
    from history.contracts import ExtractionResult
    from orchestrator.reasoning import FormulationResult
    from protocols import Step

    deps = build_fire_deps(tmp_path, monkeypatch)
    seed_fire_run(deps)
    sender = simulation_user_telegram_id(5)
    source_id = "sim-drone-protocol-hint"
    main = FakeMainAgent(compose_text="משימת רחפן סימולטיבית נרשמה לאזור אורנים.")
    ctx, queue = _fire_http_context(tmp_path, deps, monkeypatch, main=main)
    monkeypatch.setattr(
        "orchestrator.flows.extract_event",
        lambda *_args, **_kwargs: ExtractionResult(
            "drone_dispatch", "resolved", "pine_ridge", (), "reported smoke", "moderate",
            "2026-09-09T14:30:00+03:00", False, (),
        ),
    )
    monkeypatch.setattr(
        "orchestrator.flows.formulate_tasks",
        lambda *_args, **_kwargs: FormulationResult(steps=(Step(
            "surveillance_agent", "בדוק זמינות ובצע שיגור סימולטיבי", ("dispatch_drone_to_area",),
        ),)),
    )
    surveillance = deps.registry.get("surveillance_agent")
    tool_results = []

    def local_tool_caller(_task, allowed_tools):
        event = deps.persistence.fetch_event_by_source_message("telegram", sender, source_id)
        token = base._current_allowed_tools.set(frozenset(allowed_tools))
        try:
            with authenticated_request_identity(sender):
                result = surveillance._wrapped_tools["dispatch_drone_to_area"](
                    target_area="pine_ridge", incident_description="reported smoke",
                    occurred_at=event["occurred_at"],
                )
            tool_results.append(result)
            return AgentResult("success", result, (("dispatch_drone_to_area", result),))
        finally:
            base._current_allowed_tools.reset(token)

    monkeypatch.setattr(surveillance, "process", local_tool_caller)
    try:
        response = build_app(ctx).test_client().post("/Msg", headers={"X-Identity": sender}, json={
            "text": "שלח רחפן לאזור השריפה", "sender_identity": sender,
            "source_message_id": source_id, "protocol_hint": "dispatch_drone_to_incident",
            "simulation_context": "FIRE_SIMULATION", "event_time": "2026-09-09T14:30:00",
        })
        assert response.status_code == 200
        assert response.json["answer"] == "משימת רחפן סימולטיבית נרשמה לאזור אורנים."
        assert len(tool_results) == 1 and "mission MSN-" in tool_results[0]
        mission = surveillance.surveillance_store.get_active_missions()[0]
        assert mission["target_area"] == "pine_ridge"
        assert mission["status"] == "dispatched"
        dispatched_drone = next(
            drone for drone in surveillance.surveillance_store.list_drones()
            if drone["drone_id"] == mission["drone_id"]
        )
        assert dispatched_drone["status"] == "in_flight"
        event = deps.persistence.fetch_event_by_source_message("telegram", sender, source_id)
        assert event["outcome"] == "succeeded"
        assert event["user_response"] == response.json["answer"]
    finally:
        queue.stop()
        deps.persistence.close()


def test_fire_simulator_shift_and_absence_flow_through_tools_and_persisted_roster(tmp_path, monkeypatch):
    from agents import base
    from history.contracts import ExtractionResult
    from orchestrator.reasoning import FormulationResult
    from protocols import Step

    deps = build_fire_deps(tmp_path, monkeypatch)
    main = FakeMainAgent(compose_text="העדכון נרשם לפי הדיווח.")
    ctx, queue = _fire_http_context(tmp_path, deps, monkeypatch, main=main)
    identities = {offset: simulation_user_telegram_id(offset) for offset in (0, 1, 6)}
    source_ids = {"opening": identities[0], "absence": identities[1]}

    def extract(raw_text, _source, _received, *_args, **_kwargs):
        is_absence = "בדיקה" in raw_text
        return ExtractionResult(
            "crew_availability", "resolved", None, (), raw_text, "low", None, False, (),
            "2026-09-09T09:00:00" if is_absence else None,
            "2026-09-09T12:00:00" if is_absence else None,
            "בדיקה רפואית תקופתית" if is_absence else None,
        )

    monkeypatch.setattr("orchestrator.flows.extract_event", extract)

    def formulate(_main, protocol, _registry, *_args, **_kwargs):
        if protocol.name == "record_crew_shift_status":
            step = Step("team_status_agent", "רשום אישור פתיחת משמרת", ("record_crew_shift_status",))
        else:
            step = Step("team_status_agent", "רשום את היעדרות המדווח", ("record_attendance_response",))
        return FormulationResult(steps=(step,))

    monkeypatch.setattr("orchestrator.flows.formulate_tasks", formulate)

    def local_model(_descriptor, tools, _task, _timeout, *_policy):
        event = base.get_authenticated_request_event_context()
        validated = event["validated_event_fields"]
        if "record_crew_shift_status" in tools:
            # The report names three people and the canonical roster label for the other three.
            tools["record_crew_shift_status"](
                member_identities=",".join(identities.values()), availability="available",
            )
            return "All six crew members are available."
        tools["record_attendance_response"](
            source_message_id="model-selected-wrong-id", event_id="model-selected-wrong-event",
            availability="available", absence_reason="מודל המציא סיבה",
            availability_start="2030-01-01T00:00:00Z", availability_end="2030-01-02T00:00:00Z",
            cycle_id="model-selected-wrong-cycle",
        )
        assert validated["availability_start"] == "2026-09-09T09:00:00"
        return "The member is available and will return at 15:00."

    monkeypatch.setattr("agents.runtime.invoke", local_model)
    opening_text = "אבי מאשר שצוות א׳ זמין: אבי, עמרי, יובל ושלושת אנשי צוות הסימולציה."
    absence_text = "אני יוצא לבדיקה רפואית תקופתית מ-12:00 עד 15:00."

    async def scenario(url):
        runtime = SimulatorRuntime(
            ctx.loaded_profile, asyncio.get_running_loop(),
            api_client=HttpApiClient(url, bot_service_key="e2e-key"),
        )
        await runtime.startup()
        try:
            opening = await runtime.handle_message({
                "sender_identity": source_ids["opening"], "chat_id": source_ids["opening"],
                "chat_type": "private", "text": opening_text,
                "event_time": "2026-09-09T07:00:00+03:00", "source_message_id": "crew-opening-e2e",
                "protocol_hint": "record_crew_shift_status",
            })
            absence = await runtime.handle_message({
                "sender_identity": source_ids["absence"], "chat_id": source_ids["absence"],
                "chat_type": "private", "text": absence_text,
                "event_time": "2026-09-09T12:00:00+03:00", "source_message_id": "crew-absence-e2e",
                "protocol_hint": "record_crew_availability_response",
            })
            assert opening["reply_text"] == "העדכון נרשם לפי הדיווח."
            assert absence["reply_text"] == "העדכון נרשם לפי הדיווח."
        finally:
            await runtime.shutdown()

    try:
        with RunningApiServer(ctx) as server:
            asyncio.run(scenario(server.base_url))
        team = deps.registry.get("team_status_agent")
        opened = json.loads(team.report_team_availability("2026-09-09T11:59:00+03:00"))
        current = json.loads(team.report_team_availability("2026-09-09T14:30:00+03:00"))
        assert sum(row["availability"] == "available" for row in opened["crew"]) == 6
        assert sum(row["availability"] == "available" for row in current["crew"]) == 5
        omri = next(row for row in current["crew"] if row["telegram_identity"] == identities[1])
        assert omri["availability"] == "unavailable"
        assert omri["reason"] == "בדיקה רפואית תקופתית"
        assert omri["unavailable_from"] == "2026-09-09T09:00:00+00:00"
        assert omri["unavailable_until"] == "2026-09-09T12:00:00+00:00"
        for sender, raw_text in (
            (identities[0], opening_text),
            (identities[1], absence_text),
        ):
            event = next(
                row for row in deps.persistence.search_events(EventSearchCriteria(sender_identity=sender, limit=10))
                if row["raw_text"] == raw_text
            )
            assert event["outcome"] == "succeeded"
            assert "approved member(s)" in event["steps"][0]["result_text"] or "attendance response was stored" in event["steps"][0]["result_text"]
        stored = team.status_store.list_responses(cycle_id=team.status_store.find_cycle("shift-2026-09-09")["cycle_id"])
        assert len(stored) == 7
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
    history_prompts = []
    history_agent = deps.registry.get("history_agent")
    monkeypatch.setattr(
        history_agent, "process",
        lambda prompt, tools: (history_prompts.append(prompt) or AgentResult("success", "הדיווח הנוכחי מתועד בריצה.")),
    )

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
        assert history.json["answer"] == "הדיווח הנוכחי מתועד בריצה."
        assert len(history_prompts) == 1
        assert "פתיחת משמרת: שישה כבאים זמינים" in history_prompts[0]
        assert "דיווח ישן שאינו שייך לריצה הנוכחית" not in history_prompts[0]
        assert run_event not in history_prompts[0]

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


def test_free_text_history_plan_uses_fire_run_history_not_picture_routing(tmp_path, monkeypatch):
    from orchestrator.reasoning import AgentSelectionResult

    deps = build_fire_deps(tmp_path, monkeypatch)
    seed_fire_run(deps)
    ctx, queue = _fire_http_context(tmp_path, deps, monkeypatch)
    sender = simulation_user_telegram_id(5)
    captured = []
    history_agent = deps.registry.get("history_agent")
    monkeypatch.setattr(
        history_agent, "process",
        lambda prompt, tools: (captured.append(prompt) or AgentResult("success", "היסטוריית הריצה נבדקה.")),
    )
    monkeypatch.setattr(
        "api.routes.plan_message",
        lambda *_args, **_kwargs: MessagePlan(IntentResult("question", "history selected by local planner"),
                                              AgentSelectionResult(status="history")),
    )
    monkeypatch.setattr(
        "api.routes.build_fire_situational_picture",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("history question routed as a picture")),
    )

    try:
        response = build_app(ctx).test_client().post("/Msg", headers={"X-Identity": sender}, json={
            "text": "מה יש בהיסטוריה של הדיווחים?", "sender_identity": sender,
            "source_message_id": "free-history", "conversation_id": f"telegram:{sender}:free-history",
        })
        assert response.status_code == 200
        assert response.json["protocol"] == "query_historical_incidents"
        assert response.json["answer"] == "היסטוריית הריצה נבדקה."
        assert len(captured) == 1
        assert "פתיחת משמרת: שישה כבאים זמינים" in captured[0]
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
