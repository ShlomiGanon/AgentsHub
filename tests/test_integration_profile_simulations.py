"""End-to-end proof of docs/profile_simulations_design.md: a profile's declared
simulation users/groups are provisioned exactly like `api.app.build_context`
provisions them, the resulting reserved Telegram IDs are never created by the
test itself, and a materialized simulation (`api.simulations.materialize_simulation`)
runs successfully through the real `/Msg`/`/Event` endpoints — the same code
path `bot/transports.py` uses for a real Telegram message, with no shortcut.
"""

import types

import pytest

from agents import adapter
from api.app import build_app
from api.simulations import materialize_simulation
from profiles.simulation import SimulationGroup, SimulationPersona, SimulationScenario
from profiles.simulation_provisioning import ensure_simulation_entities
from tests.api_fakes import auth_headers, build_context, happy_path_agent


@pytest.fixture(autouse=True)
def _mock_crewai(monkeypatch):
    """`reference_agent` is a real agent built by build_context (unlike the
    scripted main/insights agents) — a protocol that actually dispatches to it
    invokes real CrewAI construction, faked here exactly like every other
    api/ test file fakes it (tests/test_api_groups.py's own fixture)."""

    class _FakeOutput:
        def __init__(self, raw):
            self.raw = raw

    class _FakeCrewAgent:
        def __init__(self, **kwargs):
            pass

        def kickoff(self, text):
            return _FakeOutput("status nominal, no anomalies")

    fake_module = types.SimpleNamespace(
        Agent=_FakeCrewAgent, LLM=lambda **kwargs: kwargs["model"], tools=types.SimpleNamespace(BaseTool=object)
    )
    monkeypatch.setattr(adapter, "_get_crewai", lambda: fake_module)


@pytest.fixture
def teardown_ctx():
    contexts = []
    yield contexts
    for ctx in contexts:
        ctx.queue.stop()
        ctx.deps.persistence.close()


def _build_request(chat: dict, step: dict) -> tuple[str, dict]:
    """Mirrors api/admin_simulator.py's own client-side buildRequest() exactly —
    this test drives the server the same way the admin simulator's browser
    script does, not a shortcut of its own invention."""

    if chat["kind"] == "event":
        return "/Event", {"text": step["text"], "sender_identity": step["sender_identity"]}

    chat_id = chat.get("telegram_chat_id") or step["sender_identity"]
    body = {
        "text": step["text"],
        "sender_identity": step["sender_identity"],
        "source_message_id": step.get("source_message_id") or f"sim-test-{step['step']}",
        "conversation_id": f"telegram:{chat_id}:main",
    }
    if chat.get("telegram_chat_type"):
        body["telegram_chat_type"] = chat["telegram_chat_type"]
        if chat.get("telegram_chat_id"):
            body["telegram_chat_id"] = chat["telegram_chat_id"]
    if step.get("protocol_hint"):
        body["protocol_hint"] = step["protocol_hint"]
    return "/Msg", body


_PRIVATE_SCENARIO_RAW = {
    "scenario": {"id": "status_query", "title": "Status query"},
    "chats": [{"key": "dm", "kind": "message", "label": "Viewer DM", "telegram_chat_type": "private"}],
    "steps": [
        {"step": 1, "chat": "dm", "sender_identity": "viewer", "text": "status?", "protocol_hint": "status_check"},
    ],
}

_GROUP_SCENARIO_RAW = {
    "scenario": {"id": "group_status_query", "title": "Group status query"},
    "chats": [
        {
            "key": "team", "kind": "message", "label": "Response team",
            "telegram_chat_type": "supergroup", "telegram_chat_id": "team",
        }
    ],
    "steps": [
        {"step": 1, "chat": "team", "sender_identity": "commander", "text": "status?", "protocol_hint": "status_check"},
    ],
}

_EVENT_SCENARIO_RAW = {
    "scenario": {"id": "sensor_report", "title": "Sensor report"},
    "chats": [{"key": "sensors", "kind": "event", "label": "Fence sensors"}],
    "steps": [{"step": 1, "chat": "sensors", "sender_identity": "sensor-north-1", "text": "smoke at gate 3"}],
}


def test_provisioning_is_the_only_source_of_a_simulation_users_registration(tmp_path, teardown_ctx):
    """No test in this file ever calls persistence.write_user for a simulation
    persona — ensure_simulation_entities (the exact call api.app.build_context
    makes on every profile load) is the only thing that ever registers one."""

    persona = SimulationPersona(key="viewer", offset=0, permission_level="viewer", full_name="Sim Viewer")
    ctx = build_context(tmp_path, simulation_users=(persona,))
    teardown_ctx.append(ctx)
    reserved_id = "9000000000000000"

    assert ctx.deps.persistence.read_user(reserved_id) is None

    result = ensure_simulation_entities(ctx.deps.persistence, ctx.loaded_profile)

    assert result.created_users == (reserved_id,)
    stored = ctx.deps.persistence.read_user(reserved_id)
    assert stored == {
        "telegram_identity": reserved_id, "permission_level": "viewer",
        "full_name": "Sim Viewer", "auto_register": False,
    }


def test_materialized_private_simulation_runs_through_the_real_msg_endpoint(tmp_path, teardown_ctx):
    persona = SimulationPersona(key="viewer", offset=0, permission_level="viewer", full_name="Sim Viewer")
    scenario = SimulationScenario(key="status_query", title="Status query", raw=_PRIVATE_SCENARIO_RAW)
    ctx = build_context(
        tmp_path, main_agent=happy_path_agent(intent="conversational"), simulation_users=(persona,), simulations=(scenario,)
    )
    teardown_ctx.append(ctx)

    # The one line api.app.build_context adds on every profile load.
    ensure_simulation_entities(ctx.deps.persistence, ctx.loaded_profile)

    materialized = materialize_simulation(scenario, ctx.loaded_profile.simulation_users, ctx.loaded_profile.simulation_groups)
    [chat] = materialized["chats"]
    [step] = materialized["steps"]
    assert step["sender_identity"] == "9000000000000000"  # the reserved ID, not a test-typed one

    client = build_app(ctx).test_client()
    url, body = _build_request(chat, step)
    resp = client.post(url, headers=auth_headers(step["sender_identity"]), json=body)

    assert resp.status_code == 200
    assert resp.get_json()["protocol"] == "status_check"


def test_materialized_group_simulation_is_scoped_through_the_provisioned_group(tmp_path, teardown_ctx):
    persona = SimulationPersona(key="commander", offset=0, permission_level="commander", full_name="Sim Commander")
    group = SimulationGroup(key="team", offset=0, agent_name="reference_agent", label="Sim team")
    scenario = SimulationScenario(key="group_status_query", title="Group status query", raw=_GROUP_SCENARIO_RAW)
    ctx = build_context(
        tmp_path, main_agent=happy_path_agent(intent="conversational"),
        simulation_users=(persona,), simulation_groups=(group,), simulations=(scenario,),
    )
    teardown_ctx.append(ctx)
    reserved_chat_id = "-9000000000000000"

    assert ctx.group_routing.get(reserved_chat_id) is None  # not provisioned yet

    ensure_simulation_entities(ctx.deps.persistence, ctx.loaded_profile)
    # A real restart's group_routing.load() (called once, inside api.app.build_context,
    # right after this same provisioning call) would already see it; the fake ctx here
    # built its routing table before provisioning ran, so refresh it the same way
    # /Telegram/Admission already does whenever a group appears mid-process.
    ctx.group_routing.load()

    assert ctx.group_routing.get(reserved_chat_id).agent_name == "reference_agent"

    materialized = materialize_simulation(scenario, ctx.loaded_profile.simulation_users, ctx.loaded_profile.simulation_groups)
    [chat] = materialized["chats"]
    [step] = materialized["steps"]
    assert chat["telegram_chat_id"] == reserved_chat_id

    client = build_app(ctx).test_client()
    url, body = _build_request(chat, step)
    resp = client.post(url, headers=auth_headers(step["sender_identity"]), json=body)

    assert resp.status_code == 200
    assert resp.get_json()["protocol"] == "status_check"


def test_materialized_event_simulation_runs_through_the_real_event_endpoint(tmp_path, teardown_ctx):
    """An event ("sensor") step's sender_identity is exempt from resolving to a
    declared persona (profiles.loader's own validation rule) — it passes through
    materialize_simulation unchanged and still runs the full extraction pipeline."""

    scenario = SimulationScenario(key="sensor_report", title="Sensor report", raw=_EVENT_SCENARIO_RAW)
    ctx = build_context(
        tmp_path, main_agent=happy_path_agent(), simulations=(scenario,),
        # /Event authenticates its caller exactly like /Msg does — a sensor is
        # registered like any other identity, just outside this feature's own
        # scope (it isn't a Telegram user/group, so it has no reserved ID of
        # its own and profiles.simulation has nothing to say about it).
        users=(("sensor-north-1", "viewer"),),
    )
    teardown_ctx.append(ctx)

    materialized = materialize_simulation(scenario, ctx.loaded_profile.simulation_users, ctx.loaded_profile.simulation_groups)
    [chat] = materialized["chats"]
    [step] = materialized["steps"]
    assert step["sender_identity"] == "sensor-north-1"  # not a persona key: untouched

    client = build_app(ctx).test_client()
    url, body = _build_request(chat, step)
    resp = client.post(url, headers=auth_headers(step["sender_identity"]), json=body)

    assert resp.status_code == 202  # queued, exactly like a real sensor event
    event_id = resp.get_json()["event_id"]
    assert ctx.deps.persistence.fetch_event(event_id)["raw_text"] == "smoke at gate 3"
