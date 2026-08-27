"""9.5 — Test user administration (work_plan.md §9.5).

Most of this subtask's bullets are already covered at the unit level
(`tests/test_user_admin.py`'s own CLI tests, `tests/test_bot_users.py`'s
refusal-message tests). This file adds what isn't covered anywhere else:
a real end-to-end proof that a commander added via `cli.user_admin` can
actually approve a real held run through the real API, and a structural
check — over the real registered Flask routes, not by inspection — that
no `api/*` route creates, changes, or removes a user, including the two
read-only lookups added after this subtask was first drafted
(`GET /Commanders`, `GET /User/<identity>`, §8.13/§8.14).
"""

import types
import uuid

import pytest

from agents import adapter
from api.app import build_app
from cli.user_admin import main as user_admin_main
from orchestrator.holds import create_approval_hold
from orchestrator.main_agent import RiskAssessment
from orchestrator.main_agent import ProtocolSelectionResult
from tests.api_fakes import auth_headers, build_context
from tests.helpers import write_profile_module

BOT_TOKEN_ENV = "TEST_INT_USER_ADMIN_TOKEN"
MODEL_CRED_ENV = "TEST_INT_USER_ADMIN_MODEL_KEY"


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

    fake_module = types.SimpleNamespace(Agent=_FakeCrewAgent, tools=types.SimpleNamespace(BaseTool=object))
    monkeypatch.setattr(adapter, "_get_crewai", lambda: fake_module)


def test_a_commander_added_via_the_admin_command_can_approve_a_real_held_run(tmp_path, monkeypatch, real_tier_env):
    # cli.user_admin.main() reloads the profile independently and opens its
    # own persistence connection against *that module's own* DB_PATH — for
    # this to be a genuine end-to-end proof (not two different databases
    # that happen not to conflict), the disposable profile module's
    # DB_PATH must be the exact file build_context's own ApiContext reads
    # from, matching tests/test_api_protocols.py's writable_profile_module
    # pattern.
    db_path = tmp_path / "api_test.db"
    module_name = f"user_admin_integration_profile_{uuid.uuid4().hex}"
    monkeypatch.setenv(BOT_TOKEN_ENV, "token")
    monkeypatch.setenv(MODEL_CRED_ENV, "key")
    write_profile_module(
        tmp_path, monkeypatch, module_name, bot_token_env=BOT_TOKEN_ENV, model_cred_env=MODEL_CRED_ENV,
        overrides={"DB_PATH": f"DB_PATH = {str(db_path)!r}"},
    )

    ctx = build_context(tmp_path, module_path=module_name, users=())  # empty database, same as a fresh deployment
    try:
        exit_code = user_admin_main(["--profile", module_name, "add", "--telegram-id", "fresh-commander", "--level", "commander"])
        assert exit_code == 0

        assert ctx.deps.persistence.read_user("fresh-commander") is not None

        event_id = ctx.deps.persistence.append_event({
            "received_at": "2026-08-24T10:00:00", "source": "sensor", "sender_identity": "sensor-1",
            "occurred_at": "2026-08-24T10:00:00", "raw_text": "fire needing dispatch",
        })
        selection = ProtocolSelectionResult(status="selected", protocol_name="dispatch_response", reason="matched")
        risk = RiskAssessment(level="high", score=0.9, reason="side-effecting")
        create_approval_hold(ctx.deps.persistence, event_id, "flagged_protocol", selection, risk)

        client = build_app(ctx).test_client()
        resp = client.post(f"/Approve/{event_id}", headers=auth_headers("fresh-commander"), json={"decision": "approved"})

        assert resp.status_code == 202
        assert resp.get_json()["status"] == "queued"
        ctx.queue.wait_until_idle()

        hold = ctx.deps.persistence.fetch_held_event("approval", event_id)
        assert hold["resolved"] is True
        assert hold["resolved_by"] == "fresh-commander"
    finally:
        ctx.queue.stop()
        ctx.deps.persistence.close()


def test_no_registered_api_route_creates_changes_or_removes_a_user(tmp_path):
    ctx = build_context(tmp_path)
    try:
        app = build_app(ctx)

        user_touching_routes = [
            (rule.rule, sorted(rule.methods - {"HEAD", "OPTIONS"}))
            for rule in app.url_map.iter_rules()
            if "user" in rule.rule.lower() or "commander" in rule.rule.lower()
        ]

        assert user_touching_routes, "expected at least GET /User/<identity> and GET /Commanders to exist"

        for path, methods in user_touching_routes:
            assert methods == ["GET"], f"{path} exposes {methods} — only reads are allowed on user-shaped routes (§8.2/§9.5)"
    finally:
        ctx.queue.stop()
        ctx.deps.persistence.close()
