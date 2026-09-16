"""bot/simulator_app.py: identity gating, dispatch through the real bot handlers
(`bot/app.py`, unmodified), reply capture, and the `/Simulator-msg` HTTP surface
itself, including its thread/asyncio-loop bridge (docs/bot_simulation_mode_design.md
§4.2/§4.3/§8, §10's "Flask-in-a-thread bridging correctness" risk).
"""

import asyncio
import json
import threading
import types
from types import SimpleNamespace

from agents import adapter
from profiles import SimulationGroup, SimulationPersona, simulation_group_chat_id, simulation_user_telegram_id

from bot.contracts import BOT_SERVICE_IDENTITY, BotDeps, MessageSubmissionResult
from bot.simulator_app import SimulatorRequestRefused, SimulatorRuntime, build_flask_app
from bot.transports import HttpApiClient
from messages import get_catalog
from tests.api_fakes import RunningApiServer, build_context, happy_path_agent
from tests.bot_fakes import FakeBotApiClient


def _run(coro):
    return asyncio.run(coro)


def _fake_loaded_profile(tmp_path, simulation_users=(), simulation_groups=(), api_port=0):
    return SimpleNamespace(
        module_path="profiles.test_sim",
        profile_name="Test Sim",
        db_path=str(tmp_path / "sim.db"),
        message_catalog=get_catalog("en"),
        api_port=api_port,
        simulator_port=8999,
        simulation_users=simulation_users,
        simulation_groups=simulation_groups,
    )


_PERSONA = SimulationPersona(key="viewer", offset=2, permission_level="viewer", full_name="V")
_GROUP = SimulationGroup(key="team", offset=1, agent_name="reference_agent", label="Team")
_PERSONA_ID = simulation_user_telegram_id(_PERSONA.offset)
_GROUP_ID = simulation_group_chat_id(_GROUP.offset)


# -- SimulatorRuntime.handle_message: identity gating ----------------------------------


def test_handle_message_refuses_an_identity_the_profile_never_declared(tmp_path):
    loaded = _fake_loaded_profile(tmp_path, simulation_users=(_PERSONA,))
    api_client = FakeBotApiClient(users={_PERSONA_ID: "viewer"})

    async def scenario():
        loop = asyncio.get_event_loop()
        runtime = SimulatorRuntime(loaded, loop, api_client=api_client)
        await runtime.startup()
        try:
            await runtime.handle_message(
                {"sender_identity": "123", "chat_id": "123", "chat_type": "private", "text": "hi", "source_message_id": "s1"}
            )
            assert False, "expected SimulatorRequestRefused"
        except SimulatorRequestRefused:
            pass
        finally:
            await runtime.shutdown()

    _run(scenario())


def test_handle_message_refuses_a_private_chat_id_that_does_not_match_sender(tmp_path):
    loaded = _fake_loaded_profile(tmp_path, simulation_users=(_PERSONA,))
    api_client = FakeBotApiClient(users={_PERSONA_ID: "viewer"})

    async def scenario():
        loop = asyncio.get_event_loop()
        runtime = SimulatorRuntime(loaded, loop, api_client=api_client)
        await runtime.startup()
        try:
            await runtime.handle_message(
                {"sender_identity": _PERSONA_ID, "chat_id": "999999999999999", "chat_type": "private",
                 "text": "hi", "source_message_id": "s1"}
            )
            assert False, "expected SimulatorRequestRefused"
        except SimulatorRequestRefused:
            pass
        finally:
            await runtime.shutdown()

    _run(scenario())


def test_handle_message_refuses_a_group_chat_id_the_profile_never_declared(tmp_path):
    loaded = _fake_loaded_profile(tmp_path, simulation_users=(_PERSONA,), simulation_groups=(_GROUP,))
    api_client = FakeBotApiClient(users={_PERSONA_ID: "viewer"})

    async def scenario():
        loop = asyncio.get_event_loop()
        runtime = SimulatorRuntime(loaded, loop, api_client=api_client)
        await runtime.startup()
        try:
            await runtime.handle_message(
                {"sender_identity": _PERSONA_ID, "chat_id": "-1", "chat_type": "supergroup",
                 "text": "hi", "source_message_id": "s1"}
            )
            assert False, "expected SimulatorRequestRefused"
        except SimulatorRequestRefused:
            pass
        finally:
            await runtime.shutdown()

    _run(scenario())


def test_handle_message_refuses_an_unknown_chat_type(tmp_path):
    loaded = _fake_loaded_profile(tmp_path, simulation_users=(_PERSONA,))
    api_client = FakeBotApiClient(users={_PERSONA_ID: "viewer"})

    async def scenario():
        loop = asyncio.get_event_loop()
        runtime = SimulatorRuntime(loaded, loop, api_client=api_client)
        await runtime.startup()
        try:
            await runtime.handle_message(
                {"sender_identity": _PERSONA_ID, "chat_id": _PERSONA_ID, "chat_type": "channel",
                 "text": "hi", "source_message_id": "s1"}
            )
            assert False, "expected SimulatorRequestRefused"
        except SimulatorRequestRefused:
            pass
        finally:
            await runtime.shutdown()

    _run(scenario())


def test_handle_message_refuses_missing_text_or_source_message_id(tmp_path):
    loaded = _fake_loaded_profile(tmp_path, simulation_users=(_PERSONA,))
    api_client = FakeBotApiClient(users={_PERSONA_ID: "viewer"})

    async def scenario():
        loop = asyncio.get_event_loop()
        runtime = SimulatorRuntime(loaded, loop, api_client=api_client)
        await runtime.startup()
        try:
            for bad in (
                {"sender_identity": _PERSONA_ID, "chat_id": _PERSONA_ID, "chat_type": "private", "source_message_id": "s1"},
                {"sender_identity": _PERSONA_ID, "chat_id": _PERSONA_ID, "chat_type": "private", "text": "hi"},
            ):
                try:
                    await runtime.handle_message(bad)
                    assert False, "expected SimulatorRequestRefused"
                except SimulatorRequestRefused:
                    pass
        finally:
            await runtime.shutdown()

    _run(scenario())


# -- SimulatorRuntime.handle_message: dispatch through the real handlers -------------------


def test_handle_message_dispatches_through_the_real_handler_and_captures_the_reply(tmp_path):
    """The core end-to-end proof: a declared persona's message, sent through
    PTB's real Application.process_update() and bot/app.py's real
    _on_text_message (unmodified), produces the same reply the real bot's
    submit_message()/present_incoming_message() flow would."""

    loaded = _fake_loaded_profile(tmp_path, simulation_users=(_PERSONA,))
    api_client = FakeBotApiClient(
        users={_PERSONA_ID: "viewer"},
        message_submission_result=MessageSubmissionResult(kind="question", answer_text="42 events"),
    )

    async def scenario():
        loop = asyncio.get_event_loop()
        runtime = SimulatorRuntime(loaded, loop, api_client=api_client)
        await runtime.startup()
        try:
            result = await runtime.handle_message(
                {"sender_identity": _PERSONA_ID, "chat_id": _PERSONA_ID, "chat_type": "private",
                 "text": "how many events?", "source_message_id": "sim-step-1"}
            )
        finally:
            await runtime.shutdown()
        return result

    result = _run(scenario())
    assert result["reply_text"] == "42 events"
    assert result["watermark"] == {"status_len": 2, "sent_len": 0}  # send_status + edit_status, no plain sends


def test_handle_message_reuses_the_same_message_id_for_a_repeated_source_message_id(tmp_path):
    """Proves the /Msg dedup-preservation property end-to-end: re-sending the
    same source_message_id must be seen by the real handler as the same
    message (docs/bot_simulation_mode_design.md §10)."""

    loaded = _fake_loaded_profile(tmp_path, simulation_users=(_PERSONA,))
    seen_message_ids = []
    api_client = FakeBotApiClient(
        users={_PERSONA_ID: "viewer"},
        message_submission_result=MessageSubmissionResult(kind="conversational", answer_text="ok"),
    )
    original_submit = api_client.submit_message

    async def capturing_submit(text, sender_identity, source_message_id, *args, **kwargs):
        seen_message_ids.append(source_message_id)
        return await original_submit(text, sender_identity, source_message_id, *args, **kwargs)

    api_client.submit_message = capturing_submit

    async def scenario():
        loop = asyncio.get_event_loop()
        runtime = SimulatorRuntime(loaded, loop, api_client=api_client)
        await runtime.startup()
        try:
            for _ in range(2):
                await runtime.handle_message(
                    {"sender_identity": _PERSONA_ID, "chat_id": _PERSONA_ID, "chat_type": "private",
                     "text": "hi", "source_message_id": "sim-step-1"}
                )
        finally:
            await runtime.shutdown()

    _run(scenario())
    assert len(seen_message_ids) == 2
    assert seen_message_ids[0] == seen_message_ids[1]


# -- the /Simulator-msg HTTP surface, including its thread/loop bridge ---------------------


class _RunningSimulator:
    """Runs a real `SimulatorRuntime` on a background thread's asyncio loop and
    exposes a Flask test client for `/Simulator-msg` — the same thread/loop
    split `bot/simulator_app.py`'s `run_simulator()` uses in production (just
    with a Flask test client standing in for a real HTTP server), so this
    exercises the actual `asyncio.run_coroutine_threadsafe` bridge, not a
    simplification of it."""

    def __init__(self, tmp_path, bot_service_key="test-key", simulation_users=(_PERSONA,), simulation_groups=()):
        self.loaded = _fake_loaded_profile(tmp_path, simulation_users=simulation_users, simulation_groups=simulation_groups)
        self.api_client = FakeBotApiClient(
            users={simulation_user_telegram_id(p.offset): "viewer" for p in simulation_users},
            message_submission_result=MessageSubmissionResult(kind="question", answer_text="42 events"),
        )
        self.bot_service_key = bot_service_key
        self.loop = asyncio.new_event_loop()
        self.runtime = SimulatorRuntime(self.loaded, self.loop, api_client=self.api_client)
        self._thread = threading.Thread(target=self.loop.run_forever, daemon=True)

    def __enter__(self):
        self._thread.start()
        asyncio.run_coroutine_threadsafe(self.runtime.startup(), self.loop).result(timeout=10)
        self.flask_app = build_flask_app(self.runtime, self.bot_service_key)
        self.client = self.flask_app.test_client()
        return self

    def __exit__(self, *exc_info):
        asyncio.run_coroutine_threadsafe(self.runtime.shutdown(), self.loop).result(timeout=10)
        self.loop.call_soon_threadsafe(self.loop.stop)
        self._thread.join(timeout=10)
        self.loop.close()


def test_simulator_msg_refuses_a_missing_service_key(tmp_path):
    with _RunningSimulator(tmp_path) as sim:
        response = sim.client.post("/Simulator-msg", json={})
        assert response.status_code == 403


def test_simulator_msg_refuses_a_wrong_service_key(tmp_path):
    with _RunningSimulator(tmp_path) as sim:
        response = sim.client.post("/Simulator-msg", json={}, headers={"X-Service-Key": "wrong"})
        assert response.status_code == 403


def test_simulator_msg_refuses_a_non_json_body(tmp_path):
    with _RunningSimulator(tmp_path) as sim:
        response = sim.client.post(
            "/Simulator-msg", data=b"not json", headers={"X-Service-Key": "test-key", "Content-Type": "application/json"}
        )
        assert response.status_code == 400


def test_simulator_msg_refuses_an_undeclared_identity(tmp_path):
    with _RunningSimulator(tmp_path) as sim:
        response = sim.client.post(
            "/Simulator-msg",
            json={"sender_identity": "1", "chat_id": "1", "chat_type": "private", "text": "hi", "source_message_id": "s1"},
            headers={"X-Service-Key": "test-key"},
        )
        assert response.status_code == 403
        assert "not a currently-declared" in response.get_json()["error"]["message"]


def test_simulator_msg_dispatches_and_returns_the_real_reply(tmp_path):
    with _RunningSimulator(tmp_path) as sim:
        response = sim.client.post(
            "/Simulator-msg",
            json={
                "sender_identity": _PERSONA_ID, "chat_id": _PERSONA_ID, "chat_type": "private",
                "text": "how many events?", "source_message_id": "sim-step-1",
            },
            headers={"X-Service-Key": "test-key"},
        )
        assert response.status_code == 200
        body = response.get_json()
        assert body["reply_text"] == "42 events"
        assert set(body["watermark"]) == {"status_len", "sent_len"}


def test_simulator_msg_wraps_a_none_runtime_result_in_a_success_object(tmp_path):
    """A completed dispatch must never be serialized as bare JSON ``null``."""

    with _RunningSimulator(tmp_path) as sim:
        async def _no_result(_payload):
            return None

        sim.runtime.handle_message = _no_result
        response = sim.client.post(
            "/Simulator-msg",
            json={
                "sender_identity": _PERSONA_ID, "chat_id": _PERSONA_ID, "chat_type": "private",
                "text": "no reply", "source_message_id": "sim-none-result",
            },
            headers={"X-Service-Key": "test-key"},
        )

        assert response.status_code == 200
        assert response.get_json() == {
            "reply_text": None,
            "watermark": {"status_len": 0, "sent_len": 0},
        }
        assert response.get_json() is not None


def test_simulator_msg_returns_500_on_an_unexpected_handler_failure(tmp_path):
    with _RunningSimulator(tmp_path) as sim:
        async def _boom(payload):
            raise RuntimeError("boom")
        sim.runtime.handle_message = _boom

        response = sim.client.post(
            "/Simulator-msg",
            json={
                "sender_identity": _PERSONA_ID, "chat_id": _PERSONA_ID, "chat_type": "private",
                "text": "hi", "source_message_id": "s1",
            },
            headers={"X-Service-Key": "test-key"},
        )
        assert response.status_code == 500


# -- true end-to-end: the real HttpApiClient against a real, live api.app server -----------


def test_handle_message_persists_real_state_through_a_real_running_api_server(tmp_path, monkeypatch):
    """The point of this whole design (docs/bot_simulation_mode_design.md §1.1/§4.2):
    `SimulatorRuntime`'s `api_client` is the real `HttpApiClient`, so a simulated
    persona's message really reaches a real `api.app` Flask server's `/Msg` and
    produces a real, persisted reply — not a fake standing in for that chain, and
    not `Application.process_update()` in isolation. Mirrors
    `tests/test_bot_transports.py`'s own `RunningApiServer` + real `HttpApiClient`
    pattern, one layer further up the stack (through the real bot handler too,
    which is why — unlike that file's own tests — this needs a real service key:
    `_guarded()`'s admission check, upstream of the handler, calls
    `admit_telegram_update` as bot-service before anything else runs)."""

    monkeypatch.setenv("BOT_SERVICE_KEY", "test-service-key")
    agent = happy_path_agent(intent="conversational")
    agent._dispatch["Reply naturally and directly"] = "Hello from the real bot handler!"
    ctx = build_context(tmp_path, main_agent=agent, users=((_PERSONA_ID, "viewer"), (BOT_SERVICE_IDENTITY, "commander")))
    # A real simulation persona is provisioned with a full_name already set
    # (profiles.simulation.SimulationPersona.full_name via ensure_simulation_entities) —
    # without one, _gate_on_full_name() (bot/app.py, real, unmodified) correctly
    # intercepts every message asking for a name first, exactly as it would for a
    # real, freshly-registered Telegram user.
    ctx.deps.persistence.write_user(_PERSONA_ID, "viewer", "Persona V")

    with RunningApiServer(ctx) as running:
        loaded = _fake_loaded_profile(tmp_path, simulation_users=(_PERSONA,), api_port=running.port)
        real_api_client = HttpApiClient(running.base_url, bot_service_key="test-service-key")

        async def scenario():
            loop = asyncio.get_event_loop()
            runtime = SimulatorRuntime(loaded, loop, api_client=real_api_client)
            await runtime.startup()
            try:
                return await runtime.handle_message(
                    {"sender_identity": _PERSONA_ID, "chat_id": _PERSONA_ID, "chat_type": "private",
                     "text": "hello", "source_message_id": "sim-step-1"}
                )
            finally:
                await runtime.shutdown()

        result = _run(scenario())

    assert result["reply_text"] == "Hello from the real bot handler!"


def test_unicode_text_survives_simulator_bot_api_and_event_persistence(tmp_path, monkeypatch):
    """The complete simulation transport must preserve Unicode byte-for-byte.

    This deliberately crosses the real ``/Simulator-msg`` Flask route, the
    synthetic Telegram Update and real bot handler, a real HTTP ``/Msg`` call,
    and the API's SQLite event persistence.  The scripted Main Agent keeps the
    flow deterministic; the CrewAI shim only prevents a real provider call if
    the valid extraction proceeds all the way to the reference specialist.
    """

    monkeypatch.setenv("BOT_SERVICE_KEY", "test-service-key")

    class _FakeOutput:
        def __init__(self, raw):
            self.raw = raw

    class _FakeCrewAgent:
        def __init__(self, **kwargs):
            pass

        def kickoff(self, text):
            return _FakeOutput("status nominal, no anomalies")

    fake_crewai = types.SimpleNamespace(
        Agent=_FakeCrewAgent,
        LLM=lambda **kwargs: kwargs["model"],
        tools=types.SimpleNamespace(BaseTool=object),
    )
    monkeypatch.setattr(adapter, "_get_crewai", lambda: fake_crewai)

    message = "אני במילואים מראשון עד שלישי בערב, לא זמין ביישוב — 12/14! 🚑 ✅"
    extraction = json.dumps(
        {
            "classification": "fire",
            "area": "north_sector",
            "entities": ["gate-3"],
            "description": "reserve-duty status near gate 3",
            "severity": "low",
            "occurred_at": "2026-09-16T10:00:00+00:00",
        },
        ensure_ascii=False,
    )
    agent = happy_path_agent(intent="report", extraction=extraction)
    ctx = build_context(
        tmp_path,
        main_agent=agent,
        users=((_PERSONA_ID, "viewer"), (BOT_SERVICE_IDENTITY, "commander")),
    )
    ctx.deps.persistence.write_user(_PERSONA_ID, "viewer", "Persona V")

    loop = asyncio.new_event_loop()
    runtime = SimulatorRuntime(
        _fake_loaded_profile(tmp_path, simulation_users=(_PERSONA,)),
        loop,
        api_client=HttpApiClient("http://127.0.0.1:0", bot_service_key="test-service-key"),
    )
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    runtime_started = False

    try:
        with RunningApiServer(ctx) as running:
            try:
                runtime.api_client._base_url = running.base_url
                thread.start()
                asyncio.run_coroutine_threadsafe(runtime.startup(), loop).result(timeout=10)
                runtime_started = True
                simulator = build_flask_app(runtime, "test-service-key")

                body = {
                    "sender_identity": _PERSONA_ID,
                    "chat_id": _PERSONA_ID,
                    "chat_type": "private",
                    "text": message,
                    "source_message_id": "unicode-e2e-1",
                }
                response = simulator.test_client().post(
                    "/Simulator-msg",
                    data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                    headers={
                        "X-Service-Key": "test-service-key",
                        "Content-Type": "application/json; charset=utf-8",
                    },
                )
                assert response.status_code == 200

                ctx.queue.wait_until_idle()
                events = ctx.deps.persistence.fetch_events_range("2000-01-01", "2100-01-01")
                assert len(events) == 1
                assert events[0]["raw_text"] == message
            finally:
                if runtime_started:
                    asyncio.run_coroutine_threadsafe(runtime.shutdown(), loop).result(timeout=10)
    finally:
        if thread.is_alive():
            loop.call_soon_threadsafe(loop.stop)
            thread.join(timeout=10)
        loop.close()


# -- SimulatorRuntime.poll_chat / GET /Simulator-msg/poll: Priority 3 -----------------------
# (docs/work_process.md §16 — surfacing a real run_notification_poll_loop delivery that
# arrives after the original request already returned, without blocking anything.)


def test_poll_chat_returns_nothing_when_nothing_happened_since_the_watermark(tmp_path):
    loaded = _fake_loaded_profile(tmp_path, simulation_users=(_PERSONA,))
    api_client = FakeBotApiClient(users={_PERSONA_ID: "viewer"})

    async def scenario():
        loop = asyncio.get_event_loop()
        runtime = SimulatorRuntime(loaded, loop, api_client=api_client)
        await runtime.startup()
        try:
            mark = runtime.telegram_client.mark()
            return runtime.poll_chat(_PERSONA_ID, mark)
        finally:
            await runtime.shutdown()

    result = _run(scenario())
    assert result["reply_text"] is None


def test_poll_chat_surfaces_a_background_delivery_that_arrives_after_the_watermark(tmp_path):
    """Simulates exactly what `run_notification_poll_loop` does on its own timer —
    calls `deps.telegram_client.send_reply(...)` independently of any request/response
    cycle — and proves `poll_chat` picks it up."""

    loaded = _fake_loaded_profile(tmp_path, simulation_users=(_PERSONA,))
    api_client = FakeBotApiClient(users={_PERSONA_ID: "viewer"})

    async def scenario():
        loop = asyncio.get_event_loop()
        runtime = SimulatorRuntime(loaded, loop, api_client=api_client)
        await runtime.startup()
        try:
            mark = runtime.telegram_client.mark()
            await runtime.telegram_client.send_reply(_PERSONA_ID, "your report is done", None)
            return runtime.poll_chat(_PERSONA_ID, mark)
        finally:
            await runtime.shutdown()

    result = _run(scenario())
    assert result["reply_text"] == "your report is done"


def test_poll_chat_refuses_an_undeclared_chat_id(tmp_path):
    loaded = _fake_loaded_profile(tmp_path, simulation_users=(_PERSONA,))
    api_client = FakeBotApiClient(users={_PERSONA_ID: "viewer"})

    async def scenario():
        loop = asyncio.get_event_loop()
        runtime = SimulatorRuntime(loaded, loop, api_client=api_client)
        await runtime.startup()
        try:
            mark = runtime.telegram_client.mark()
            try:
                runtime.poll_chat("999999999999999", mark)
                assert False, "expected SimulatorRequestRefused"
            except SimulatorRequestRefused:
                pass
        finally:
            await runtime.shutdown()

    _run(scenario())


def test_simulator_msg_poll_requires_the_service_key(tmp_path):
    with _RunningSimulator(tmp_path) as sim:
        response = sim.client.get(f"/Simulator-msg/poll?chat_id={_PERSONA_ID}&status_len=0&sent_len=0")
        assert response.status_code == 403


def test_simulator_msg_poll_refuses_an_undeclared_chat_id(tmp_path):
    with _RunningSimulator(tmp_path) as sim:
        response = sim.client.get(
            "/Simulator-msg/poll?chat_id=999999999999999&status_len=0&sent_len=0",
            headers={"X-Service-Key": "test-key"},
        )
        assert response.status_code == 403


def test_simulator_msg_poll_finds_a_reply_delivered_after_the_original_watermark(tmp_path):
    with _RunningSimulator(tmp_path) as sim:
        first = sim.client.post(
            "/Simulator-msg",
            json={
                "sender_identity": _PERSONA_ID, "chat_id": _PERSONA_ID, "chat_type": "private",
                "text": "how many events?", "source_message_id": "sim-step-1",
            },
            headers={"X-Service-Key": "test-key"},
        )
        watermark = first.get_json()["watermark"]

        # Simulate run_notification_poll_loop's own later, independent delivery.
        asyncio.run_coroutine_threadsafe(
            sim.runtime.telegram_client.send_reply(_PERSONA_ID, "job finished", None), sim.loop
        ).result(timeout=5)

        poll = sim.client.get(
            f"/Simulator-msg/poll?chat_id={_PERSONA_ID}&status_len={watermark['status_len']}&sent_len={watermark['sent_len']}",
            headers={"X-Service-Key": "test-key"},
        )
        assert poll.status_code == 200
        assert poll.get_json()["reply_text"] == "job finished"

        # A second poll from the *new* watermark finds nothing further.
        second_watermark = poll.get_json()["watermark"]
        again = sim.client.get(
            f"/Simulator-msg/poll?chat_id={_PERSONA_ID}&status_len={second_watermark['status_len']}&sent_len={second_watermark['sent_len']}",
            headers={"X-Service-Key": "test-key"},
        )
        assert again.get_json()["reply_text"] is None
