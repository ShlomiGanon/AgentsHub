import importlib
import sys
import types
import uuid

import pytest

from agents import adapter
from api.app import build_app
from tests.api_fakes import COMMANDER_IDENTITY, VIEWER_IDENTITY, auth_headers, build_context

_PROFILE_TEMPLATE = """
from protocols.model import Protocol, CriticalityLevel

PROFILE_NAME = "For Tests"
AGENTS = []
PROTOCOLS = [
    Protocol(
        name="status_check",
        description="applies to a routine status check",
        participating_agents=("reference_agent",),
        approved_tools=("check_status",),
        expected_success_output="a status report",
        criticality=CriticalityLevel.LOW,
        approval_flag=False,
    ),
]
EVENT_TYPES = ["fire"]
AREAS = ["north"]
DB_PATH = {db_path!r}
API_PORT = 9999
RETRY_COUNT = 1
RISK_THRESHOLD = 0.5
LOOKBACK_WINDOW_DAYS = 10
BOT_TOKEN_ENV = "TEST_TOKEN"
MODEL_CREDENTIAL_ENVS = []
"""


@pytest.fixture(autouse=True)
def _mock_crewai(monkeypatch):
    class _FakeOutput:
        def __init__(self, raw):
            self.raw = raw

    class _FakeCrewAgent:
        def __init__(self, **kwargs):
            pass

        def kickoff(self, text):
            return _FakeOutput("status nominal, no anomalies")

    fake_module = types.SimpleNamespace(Agent=_FakeCrewAgent, tools=types.SimpleNamespace(BaseTool=object))
    monkeypatch.setattr(adapter, "_get_crewai", lambda: fake_module)


@pytest.fixture
def writable_profile_module(tmp_path, monkeypatch):
    """A disposable profile module on disk — protocol writes genuinely
    edit the file at `module_path`, so this must never be the shared
    fixtures.profiles.minimal_profile every other test reads.
    """

    module_name = f"api_protocol_test_profile_{uuid.uuid4().hex}"
    content = _PROFILE_TEMPLATE.format(db_path=str(tmp_path / "test.db"))
    (tmp_path / f"{module_name}.py").write_text(content, encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    yield module_name
    sys.modules.pop(module_name, None)


@pytest.fixture
def teardown_ctx():
    contexts = []
    yield contexts
    for ctx in contexts:
        ctx.queue.stop()
        ctx.deps.persistence.close()


def _new_protocol_body(name="dispatch_response"):
    return {
        "name": name,
        "description": "applies when a response must be dispatched",
        "participating_agents": ["reference_agent"],
        "approved_tools": ["check_status", "record_action"],
        "expected_success_output": "confirmation a response was dispatched",
        "criticality": "high",
        "approval_flag": True,
    }


def test_get_protocol_lists_the_loaded_set(tmp_path, teardown_ctx):
    ctx = build_context(tmp_path)
    teardown_ctx.append(ctx)
    client = build_app(ctx).test_client()

    resp = client.get("/Protocol", headers=auth_headers(VIEWER_IDENTITY))

    assert resp.status_code == 200
    names = {p["name"] for p in resp.get_json()["protocols"]}
    assert names == {"status_check", "dispatch_response"}


def test_get_protocol_includes_criticality_and_approval_flag(tmp_path, teardown_ctx):
    ctx = build_context(tmp_path)
    teardown_ctx.append(ctx)
    client = build_app(ctx).test_client()

    resp = client.get("/Protocol", headers=auth_headers(VIEWER_IDENTITY))

    by_name = {p["name"]: p for p in resp.get_json()["protocols"]}
    assert by_name["dispatch_response"]["approval_flag"] is True
    assert by_name["dispatch_response"]["criticality"] == "high"
    assert by_name["status_check"]["approval_flag"] is False


def test_get_protocol_requires_authentication(tmp_path, teardown_ctx):
    ctx = build_context(tmp_path)
    teardown_ctx.append(ctx)
    client = build_app(ctx).test_client()

    resp = client.get("/Protocol")

    assert resp.status_code == 401


def test_post_protocol_writes_the_file_and_returns_the_fixed_message(tmp_path, teardown_ctx, writable_profile_module):
    ctx = build_context(tmp_path, module_path=writable_profile_module)
    teardown_ctx.append(ctx)
    client = build_app(ctx).test_client()

    resp = client.post("/Protocol", headers=auth_headers(COMMANDER_IDENTITY), json=_new_protocol_body("new_protocol"))

    assert resp.status_code == 200
    assert resp.get_json() == {"message": "The running system is unchanged. This edit applies from the next start."}

    reloaded = importlib.import_module(writable_profile_module)
    names = {p.name for p in reloaded.PROTOCOLS}
    assert "new_protocol" in names


def test_post_protocol_does_not_change_the_running_loaded_set(tmp_path, teardown_ctx, writable_profile_module):
    ctx = build_context(tmp_path, module_path=writable_profile_module)
    teardown_ctx.append(ctx)
    client = build_app(ctx).test_client()

    client.post("/Protocol", headers=auth_headers(COMMANDER_IDENTITY), json=_new_protocol_body("new_protocol"))
    resp = client.get("/Protocol", headers=auth_headers(VIEWER_IDENTITY))

    names = {p["name"] for p in resp.get_json()["protocols"]}
    assert names == {"status_check", "dispatch_response"}  # unchanged in this process


def test_post_protocol_requires_commander_level(tmp_path, teardown_ctx, writable_profile_module):
    ctx = build_context(tmp_path, module_path=writable_profile_module)
    teardown_ctx.append(ctx)
    client = build_app(ctx).test_client()

    resp = client.post("/Protocol", headers=auth_headers(VIEWER_IDENTITY), json=_new_protocol_body("new_protocol"))

    assert resp.status_code == 403


def test_post_protocol_rejects_a_duplicate_name(tmp_path, teardown_ctx, writable_profile_module):
    ctx = build_context(tmp_path, module_path=writable_profile_module)
    teardown_ctx.append(ctx)
    client = build_app(ctx).test_client()

    resp = client.post("/Protocol", headers=auth_headers(COMMANDER_IDENTITY), json=_new_protocol_body("status_check"))

    assert resp.status_code == 400


def test_post_protocol_rejects_a_missing_field(tmp_path, teardown_ctx, writable_profile_module):
    ctx = build_context(tmp_path, module_path=writable_profile_module)
    teardown_ctx.append(ctx)
    client = build_app(ctx).test_client()

    body = _new_protocol_body("incomplete")
    del body["approval_flag"]

    resp = client.post("/Protocol", headers=auth_headers(COMMANDER_IDENTITY), json=body)

    assert resp.status_code == 400
    assert resp.get_json()["field"] == "approval_flag"


def test_post_protocol_rejects_an_agent_that_does_not_exist(tmp_path, teardown_ctx, writable_profile_module):
    ctx = build_context(tmp_path, module_path=writable_profile_module)
    teardown_ctx.append(ctx)
    client = build_app(ctx).test_client()

    body = _new_protocol_body("bad_agent")
    body["participating_agents"] = ["no_such_agent"]

    resp = client.post("/Protocol", headers=auth_headers(COMMANDER_IDENTITY), json=body)

    assert resp.status_code == 400


def test_put_protocol_replaces_the_named_one(tmp_path, teardown_ctx, writable_profile_module):
    ctx = build_context(tmp_path, module_path=writable_profile_module)
    teardown_ctx.append(ctx)
    client = build_app(ctx).test_client()

    body = _new_protocol_body("status_check")
    body["description"] = "an updated description"

    resp = client.put("/Protocol/status_check", headers=auth_headers(COMMANDER_IDENTITY), json=body)

    assert resp.status_code == 200
    reloaded = importlib.import_module(writable_profile_module)
    [updated] = [p for p in reloaded.PROTOCOLS if p.name == "status_check"]
    assert updated.description == "an updated description"


def test_put_protocol_on_a_name_that_does_not_exist_is_rejected(tmp_path, teardown_ctx, writable_profile_module):
    ctx = build_context(tmp_path, module_path=writable_profile_module)
    teardown_ctx.append(ctx)
    client = build_app(ctx).test_client()

    resp = client.put("/Protocol/no_such_protocol", headers=auth_headers(COMMANDER_IDENTITY), json=_new_protocol_body("no_such_protocol"))

    assert resp.status_code == 400


def test_delete_protocol_removes_it(tmp_path, teardown_ctx, writable_profile_module):
    ctx = build_context(tmp_path, module_path=writable_profile_module)
    teardown_ctx.append(ctx)
    client = build_app(ctx).test_client()

    resp = client.delete("/Protocol/status_check", headers=auth_headers(COMMANDER_IDENTITY))

    assert resp.status_code == 200
    reloaded = importlib.import_module(writable_profile_module)
    assert "status_check" not in {p.name for p in reloaded.PROTOCOLS}


def test_delete_protocol_requires_commander_level(tmp_path, teardown_ctx, writable_profile_module):
    ctx = build_context(tmp_path, module_path=writable_profile_module)
    teardown_ctx.append(ctx)
    client = build_app(ctx).test_client()

    resp = client.delete("/Protocol/status_check", headers=auth_headers(VIEWER_IDENTITY))

    assert resp.status_code == 403
