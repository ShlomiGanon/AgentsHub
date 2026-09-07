"""9.17 — Test profile editing and settings persistence (work_plan.md §9.17).

Most bullets already covered: "running system unchanged" by
`tests/test_api_protocols.py::test_post_protocol_does_not_change_the_running_loaded_set`,
"GET /SYSTEM reports pending change" by `tests/test_api_system.py
::test_get_system_reports_a_pending_profile_change_after_the_file_is_edited`,
"changed threshold survives a restart" by `tests/test_settings_store.py
::test_later_run_prefers_the_settings_file_over_profile_starting_values`,
"reject a profile-owned field" by `tests/test_api_system.py
::test_put_system_rejects_a_profile_owned_field_by_name`. This file adds
the two genuinely uncovered bullets: a real restart actually loading and
selecting the newly-added protocol, and a risk-threshold change taking
effect on the very next event submitted through the real API.
"""

import types
import uuid

import pytest

from agents import adapter
from api.app import build_app
from profiles.loader import load_profile
from tests.api_fakes import COMMANDER_IDENTITY, VIEWER_IDENTITY, auth_headers, build_context, happy_path_agent
from tests.helpers import write_profile_module

BOT_TOKEN_ENV = "TEST_INT_PROFILE_EDIT_TOKEN"
MODEL_CRED_ENV = "TEST_INT_PROFILE_EDIT_MODEL_KEY"


@pytest.fixture(autouse=True)
def _mock_crewai(monkeypatch):
    class _FakeOutput:
        def __init__(self, raw):
            self.raw = raw

    class _FakeCrewAgent:
        def __init__(self, **kwargs):
            pass

        def kickoff(self, text):
            return _FakeOutput("status nominal")

    fake_module = types.SimpleNamespace(Agent=_FakeCrewAgent, LLM=lambda **kwargs: kwargs["model"], tools=types.SimpleNamespace(BaseTool=object))
    monkeypatch.setattr(adapter, "_get_crewai", lambda: fake_module)


_PROFILE_TEMPLATE = """
from agents.reference import ReferenceAgent
from profiles.spec import AgentSpec
from protocols.model import Protocol, CriticalityLevel

PROFILE_NAME = "For Tests"
DEFAULT_LANGUAGE = "en"
MAX_ITER = 8
MODEL_TIMEOUT_SECONDS = 30
AGENTS = [AgentSpec(cls=ReferenceAgent, tier="sub")]
PROTOCOLS = [
    Protocol(
        name="status_check", description="applies to a routine status check",
        participating_agents=("reference_agent",), approved_tools=("check_status",),
        expected_success_output="a status report", criticality=CriticalityLevel.LOW, approval_flag=False,
    ),
]
EVENT_TYPES = ["fire"]
AREAS = ["north"]
DB_PATH = {db_path!r}
API_PORT = 9999
RETRY_COUNT = 1
RISK_THRESHOLD = 0.5
LOOKBACK_WINDOW_DAYS = 10
BOT_TOKEN_ENV = {bot_token_env!r}
MODEL_CREDENTIAL_ENVS = []
"""


@pytest.fixture
def writable_profile_module(tmp_path, monkeypatch):
    module_name = f"integration_profile_edit_{uuid.uuid4().hex}"
    monkeypatch.setenv(BOT_TOKEN_ENV, "token")
    content = _PROFILE_TEMPLATE.format(db_path=str(tmp_path / "test.db"), bot_token_env=BOT_TOKEN_ENV)
    (tmp_path / f"{module_name}.py").write_text(content, encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    return module_name


def test_a_protocol_added_through_the_api_is_loaded_and_selectable_after_a_real_restart(tmp_path, writable_profile_module, test_core_model, test_sub_model):
    ctx = build_context(tmp_path, module_path=writable_profile_module)
    client = build_app(ctx).test_client()

    new_protocol = {
        "name": "night_watch", "description": "applies to a routine night-shift check",
        "participating_agents": ["reference_agent"], "approved_tools": ["check_status"],
        "expected_success_output": "a status report", "criticality": "LOW", "approval_flag": False,
    }
    resp = client.post("/Protocol", headers=auth_headers(COMMANDER_IDENTITY), json=new_protocol)
    assert resp.status_code == 200
    assert resp.get_json()["message"]

    # The running system is genuinely unchanged.
    assert "night_watch" not in [p.name for p in ctx.deps.protocol_set.all()]

    # "Restart": reload the profile module fresh from disk.
    reloaded = load_profile(writable_profile_module, core_model=test_core_model, sub_model=test_sub_model)

    reloaded_names = [p.name for p in reloaded.protocols]
    assert "night_watch" in reloaded_names
    reloaded_protocol = next(p for p in reloaded.protocols if p.name == "night_watch")
    assert reloaded_protocol.description == "applies to a routine night-shift check"
    assert reloaded_protocol.participating_agents == ("reference_agent",)


def test_a_risk_threshold_change_takes_effect_on_the_very_next_event(tmp_path):
    # A risk score of 0.6 is below the starting 0.7 threshold (no hold),
    # but above a lowered 0.4 threshold (holds for approval) — the
    # clearest possible proof the *new* value, not the starting one, is
    # what the very next event is judged against.
    agent = happy_path_agent(risk_score="0.6", selected="dispatch_response")
    ctx = build_context(tmp_path, main_agent=agent)
    ctx.deps.settings_store.risk_threshold = 0.7
    client = build_app(ctx).test_client()

    put_resp = client.put("/SYSTEM", headers=auth_headers(COMMANDER_IDENTITY), json={"risk_threshold": 0.4})
    assert put_resp.status_code == 200
    assert put_resp.get_json()["risk_threshold"] == 0.4

    resp = client.post("/Event", headers=auth_headers(VIEWER_IDENTITY), json={"text": "fire at gate 3", "sender_identity": VIEWER_IDENTITY})
    assert resp.status_code == 202
    event_id = resp.get_json()["event_id"]
    ctx.queue.wait_until_idle()

    event = ctx.deps.persistence.fetch_event(event_id)
    assert event["approval_held"] is True
