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
PROTOCOLS = []
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
def hashable_profile_module(tmp_path, monkeypatch):
    module_name = f"api_system_test_profile_{uuid.uuid4().hex}"
    content = _PROFILE_TEMPLATE.format(db_path=str(tmp_path / "test.db"))
    path = tmp_path / f"{module_name}.py"
    path.write_text(content, encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    yield module_name, path
    sys.modules.pop(module_name, None)


@pytest.fixture
def teardown_ctx():
    contexts = []
    yield contexts
    for ctx in contexts:
        ctx.queue.stop()
        ctx.deps.persistence.close()


def test_get_system_reports_profile_agents_protocols_types_areas(tmp_path, teardown_ctx):
    ctx = build_context(tmp_path)
    teardown_ctx.append(ctx)
    client = build_app(ctx).test_client()

    resp = client.get("/SYSTEM", headers=auth_headers(VIEWER_IDENTITY))

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["profile"] == ctx.loaded_profile.module_path
    assert set(body["agents"]) == {"reference_agent", "history_agent"}
    assert {p["name"] for p in body["protocols"]} == {"status_check", "dispatch_response"}
    assert body["event_types"] == ["fire", "medical", "human_activation"]
    assert body["areas"] == ["north_sector", "south_sector"]


def test_get_system_protocol_summary_matches_get_protocols_full_shape(tmp_path, teardown_ctx):
    # §7.12: ProfileView/ProtocolView (bot/api_client.py) need description
    # and criticality, not just name/approval_flag — confirm GET /SYSTEM's
    # protocol entries carry the same fields GET /Protocol's do, so a
    # caller never needs to compose both endpoints for one protocol view.
    ctx = build_context(tmp_path)
    teardown_ctx.append(ctx)
    client = build_app(ctx).test_client()

    system_resp = client.get("/SYSTEM", headers=auth_headers(VIEWER_IDENTITY))
    protocol_resp = client.get("/Protocol", headers=auth_headers(VIEWER_IDENTITY))

    system_by_name = {p["name"]: p for p in system_resp.get_json()["protocols"]}
    protocol_by_name = {p["name"]: p for p in protocol_resp.get_json()["protocols"]}

    assert system_by_name == protocol_by_name
    dispatch_response = system_by_name["dispatch_response"]
    assert dispatch_response["description"] == "applies when a response must be dispatched"
    assert dispatch_response["criticality"] == "high"
    assert dispatch_response["participating_agents"] == ["reference_agent"]


def test_get_system_reports_queued_and_held_counts(tmp_path, teardown_ctx):
    ctx = build_context(tmp_path)
    teardown_ctx.append(ctx)
    client = build_app(ctx).test_client()
    ctx.queue.stop()  # nothing draining, so a submission stays counted
    ctx.queue.submit(("evt-x", lambda: None))

    resp = client.get("/SYSTEM", headers=auth_headers(VIEWER_IDENTITY))

    body = resp.get_json()
    assert body["queued_events"] == 1
    assert body["held_events"] == {"clarification": 0, "approval": 0}


def test_get_system_reports_the_scheduler_status(tmp_path, teardown_ctx):
    ctx = build_context(tmp_path)
    teardown_ctx.append(ctx)
    client = build_app(ctx).test_client()

    resp = client.get("/SYSTEM", headers=auth_headers(VIEWER_IDENTITY))

    assert resp.get_json()["scheduler"] == {"last_run_at": None, "last_run_ok": None, "last_run_error": None}


def test_get_system_reports_current_settings(tmp_path, teardown_ctx):
    ctx = build_context(tmp_path)
    teardown_ctx.append(ctx)
    client = build_app(ctx).test_client()

    resp = client.get("/SYSTEM", headers=auth_headers(VIEWER_IDENTITY))

    assert resp.get_json()["settings"] == {"retry_count": 3, "risk_threshold": 0.5, "lookback_window_days": 30}


def test_get_system_reports_no_pending_profile_change_when_the_file_is_untouched(tmp_path, teardown_ctx, hashable_profile_module):
    module_name, _path = hashable_profile_module
    ctx = build_context(tmp_path, module_path=module_name)
    teardown_ctx.append(ctx)
    client = build_app(ctx).test_client()

    resp = client.get("/SYSTEM", headers=auth_headers(VIEWER_IDENTITY))

    assert resp.get_json()["profile_file_changed"] is False


def test_get_system_reports_a_pending_profile_change_after_the_file_is_edited(tmp_path, teardown_ctx, hashable_profile_module):
    module_name, path = hashable_profile_module
    ctx = build_context(tmp_path, module_path=module_name)
    teardown_ctx.append(ctx)
    client = build_app(ctx).test_client()

    with open(path, "a", encoding="utf-8") as f:
        f.write("\n# a pending, not-yet-restarted edit\n")

    resp = client.get("/SYSTEM", headers=auth_headers(VIEWER_IDENTITY))

    assert resp.get_json()["profile_file_changed"] is True


def test_get_system_requires_authentication(tmp_path, teardown_ctx):
    ctx = build_context(tmp_path)
    teardown_ctx.append(ctx)
    client = build_app(ctx).test_client()

    resp = client.get("/SYSTEM")

    assert resp.status_code == 401


def test_put_system_accepts_a_partial_body_and_writes_before_responding(tmp_path, teardown_ctx):
    ctx = build_context(tmp_path)
    teardown_ctx.append(ctx)
    client = build_app(ctx).test_client()

    resp = client.put("/SYSTEM", headers=auth_headers(COMMANDER_IDENTITY), json={"risk_threshold": 0.75})

    assert resp.status_code == 200
    assert resp.get_json()["risk_threshold"] == 0.75
    assert ctx.deps.settings_store.get_risk_threshold() == 0.75
    # untouched fields keep their starting values
    assert ctx.deps.settings_store.get_retry_count() == 3


def test_put_system_rejects_a_profile_owned_field_by_name(tmp_path, teardown_ctx):
    ctx = build_context(tmp_path)
    teardown_ctx.append(ctx)
    client = build_app(ctx).test_client()

    resp = client.put("/SYSTEM", headers=auth_headers(COMMANDER_IDENTITY), json={"api_port": 9999})

    assert resp.status_code == 400
    assert resp.get_json()["field"] == "api_port"
    assert ctx.deps.settings_store.get_retry_count() == 3  # nothing was silently applied


@pytest.mark.parametrize("field,value", [("retry_count", -1), ("risk_threshold", 1.5), ("risk_threshold", -0.1), ("lookback_window_days", 0)])
def test_put_system_rejects_invalid_values(tmp_path, teardown_ctx, field, value):
    ctx = build_context(tmp_path)
    teardown_ctx.append(ctx)
    client = build_app(ctx).test_client()

    resp = client.put("/SYSTEM", headers=auth_headers(COMMANDER_IDENTITY), json={field: value})

    assert resp.status_code == 400
    assert resp.get_json()["field"] == field


def test_put_system_accepts_a_zero_retry_count(tmp_path, teardown_ctx):
    # Only negative is invalid per work_plan.md §7.8's own wording; zero
    # retries ("try once, never retry") is a legitimate operator choice.
    ctx = build_context(tmp_path)
    teardown_ctx.append(ctx)
    client = build_app(ctx).test_client()

    resp = client.put("/SYSTEM", headers=auth_headers(COMMANDER_IDENTITY), json={"retry_count": 0})

    assert resp.status_code == 200
    assert ctx.deps.settings_store.get_retry_count() == 0


def test_put_system_requires_commander_level(tmp_path, teardown_ctx):
    ctx = build_context(tmp_path)
    teardown_ctx.append(ctx)
    client = build_app(ctx).test_client()

    resp = client.put("/SYSTEM", headers=auth_headers(VIEWER_IDENTITY), json={"retry_count": 5})

    assert resp.status_code == 403
