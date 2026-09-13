"""GET /Simulations, GET /Simulations/<key> (docs/profile_simulations_design.md)."""

import pytest

from api.app import build_app
from profiles.simulation import SimulationGroup, SimulationPersona, SimulationScenario
from tests.api_fakes import COMMANDER_IDENTITY, VIEWER_IDENTITY, auth_headers, build_context

_SIMULATION_USERS = (
    SimulationPersona(key="commander", offset=0, permission_level="commander", full_name="Sim Commander"),
    SimulationPersona(key="viewer", offset=1, permission_level="viewer", full_name="Sim Viewer"),
)
_SIMULATION_GROUPS = (
    SimulationGroup(key="response_team", offset=0, agent_name="reference_agent", label="Sim group"),
)
_SCENARIO_RAW = {
    "scenario": {"id": "demo", "title": "Demo", "description": "", "tags": []},
    "chats": [
        {"key": "viewer_dm", "kind": "message", "label": "Viewer DM", "telegram_chat_type": "private"},
        {
            "key": "response_team", "kind": "message", "label": "Response team",
            "telegram_chat_type": "supergroup", "telegram_chat_id": "response_team",
        },
    ],
    "steps": [
        {"step": 1, "chat": "viewer_dm", "sender_identity": "viewer", "sender_name": "Sim Viewer", "text": "hello"},
        {"step": 2, "chat": "response_team", "sender_identity": "commander", "sender_name": "Sim Commander", "text": "status?"},
    ],
}
_SIMULATIONS = (
    SimulationScenario(key="demo_scenario", title="Demo scenario", description="a demo", tags=("demo",), raw=_SCENARIO_RAW),
)


@pytest.fixture
def teardown_ctx():
    contexts = []
    yield contexts
    for ctx in contexts:
        ctx.queue.stop()
        ctx.deps.persistence.close()


def _ctx(tmp_path, teardown_ctx):
    ctx = build_context(
        tmp_path,
        simulation_users=_SIMULATION_USERS,
        simulation_groups=_SIMULATION_GROUPS,
        simulations=_SIMULATIONS,
    )
    teardown_ctx.append(ctx)
    return ctx


def test_list_simulations_is_commander_only(tmp_path, teardown_ctx):
    ctx = _ctx(tmp_path, teardown_ctx)
    client = build_app(ctx).test_client()

    assert client.get("/Simulations", headers=auth_headers(VIEWER_IDENTITY)).status_code == 403

    resp = client.get("/Simulations", headers=auth_headers(COMMANDER_IDENTITY))
    assert resp.status_code == 200
    assert resp.get_json()["simulations"] == [
        {"key": "demo_scenario", "title": "Demo scenario", "description": "a demo", "tags": ["demo"]}
    ]


def test_list_simulations_empty_by_default(tmp_path, teardown_ctx):
    """A profile declaring none (every profile before this feature) is unaffected."""

    ctx = build_context(tmp_path)
    teardown_ctx.append(ctx)
    client = build_app(ctx).test_client()

    resp = client.get("/Simulations", headers=auth_headers(COMMANDER_IDENTITY))
    assert resp.status_code == 200
    assert resp.get_json()["simulations"] == []


def test_get_simulation_is_commander_only(tmp_path, teardown_ctx):
    ctx = _ctx(tmp_path, teardown_ctx)
    client = build_app(ctx).test_client()

    assert client.get("/Simulations/demo_scenario", headers=auth_headers(VIEWER_IDENTITY)).status_code == 403


def test_get_simulation_materializes_reserved_ids_and_keeps_the_shape(tmp_path, teardown_ctx):
    ctx = _ctx(tmp_path, teardown_ctx)
    client = build_app(ctx).test_client()

    resp = client.get("/Simulations/demo_scenario", headers=auth_headers(COMMANDER_IDENTITY))
    assert resp.status_code == 200
    body = resp.get_json()

    # Same shape in and out — only the two ID-bearing fields are substituted.
    assert body["scenario"] == _SCENARIO_RAW["scenario"]
    assert body["chats"][0] == _SCENARIO_RAW["chats"][0]  # private chat: no chat_id to substitute
    assert body["chats"][1]["telegram_chat_id"] == "-9000000000000000"  # response_team, offset=0
    assert body["steps"][0]["sender_identity"] == "9000000000000001"  # viewer, offset=1
    assert body["steps"][1]["sender_identity"] == "9000000000000000"  # commander, offset=0
    # Every other field passes through untouched.
    assert body["steps"][0]["text"] == "hello"
    assert body["steps"][1]["text"] == "status?"


def test_get_unknown_simulation_is_404(tmp_path, teardown_ctx):
    ctx = _ctx(tmp_path, teardown_ctx)
    client = build_app(ctx).test_client()

    resp = client.get("/Simulations/does-not-exist", headers=auth_headers(COMMANDER_IDENTITY))
    assert resp.status_code == 404
