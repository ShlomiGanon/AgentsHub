"""9.21 — Set up deployment (work_plan.md §9.21).

Uses the *real* `api.app.build_context`/`build_app`/`main` — not
`tests/api_fakes.py`'s test-only fixture — since this subtask is about
proving the actual startup wiring works, not the test double that stands
in for it everywhere else. Scope stays localhost-demo packaging only, per
this subtask's own refined text: production process supervision, TLS,
and everything else `docs/NEXT_STAGE.md` covers is explicitly out of
scope here.
"""

import uuid

import pytest

from api.app import build_app as real_build_app
from api.app import build_context as real_build_context
from cli.user_admin import main as user_admin_main
from tests.api_fakes import auth_headers

_PROFILE_TEMPLATE = """
from agents.reference import ReferenceAgent
from profiles.spec import AgentSpec
from protocols.model import Protocol, CriticalityLevel

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
API_PORT = {api_port}
RETRY_COUNT = 1
RISK_THRESHOLD = 0.5
LOOKBACK_WINDOW_DAYS = 10
BOT_TOKEN_ENV = {bot_token_env!r}
MODEL_CREDENTIAL_ENVS = []
"""


def _write_deployment(tmp_path, monkeypatch, api_port: int):
    module_name = f"integration_deployment_{uuid.uuid4().hex}"
    bot_token_env = f"DEPLOY_TEST_TOKEN_{uuid.uuid4().hex}"
    monkeypatch.setenv(bot_token_env, "token")
    db_path = tmp_path / "deployment.db"
    content = _PROFILE_TEMPLATE.format(db_path=str(db_path), api_port=api_port, bot_token_env=bot_token_env)
    (tmp_path / f"{module_name}.py").write_text(content, encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    return module_name, db_path


def test_the_package_starts_from_nothing_and_serves(tmp_path, monkeypatch, real_tier_env, test_core_model, test_sub_model):
    # A genuinely empty directory — pytest's own tmp_path guarantees this.
    assert list(tmp_path.iterdir()) == []

    module_name, db_path = _write_deployment(tmp_path, monkeypatch, api_port=19001)
    assert not db_path.exists()

    # The administration command is the real first step of a fresh
    # deployment — running it triggers load_profile -> open_persistence,
    # which runs migrations and creates the database from nothing. It is a
    # real root (reads CORE_MODEL_*/SUB_MODEL_* from the real environment
    # itself, via real_tier_env, above), unlike build_context below.
    exit_code = user_admin_main(["--profile", module_name, "add", "--telegram-id", "first-commander", "--level", "commander"])
    assert exit_code == 0
    assert db_path.exists()

    # The system serves — the real build_context/build_app, not the test
    # fixture, wired up exactly as `python -m api.app <profile>` would.
    ctx = real_build_context(module_name, core_model=test_core_model, sub_model=test_sub_model)
    try:
        client = real_build_app(ctx).test_client()
        resp = client.get("/SYSTEM", headers=auth_headers("first-commander"))
        assert resp.status_code == 200
        assert resp.get_json()["profile"] == module_name
    finally:
        ctx.queue.stop()
        ctx.scheduler.stop()
        ctx.deps.persistence.close()


def test_two_deployments_start_side_by_side_from_the_same_build(tmp_path, monkeypatch, real_tier_env, test_core_model, test_sub_model):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()

    module_a, db_a = _write_deployment(tmp_path / "a", monkeypatch, api_port=19011)
    module_b, db_b = _write_deployment(tmp_path / "b", monkeypatch, api_port=19012)

    user_admin_main(["--profile", module_a, "add", "--telegram-id", "commander-a", "--level", "commander"])
    user_admin_main(["--profile", module_b, "add", "--telegram-id", "commander-b", "--level", "commander"])

    ctx_a = real_build_context(module_a, core_model=test_core_model, sub_model=test_sub_model)
    ctx_b = real_build_context(module_b, core_model=test_core_model, sub_model=test_sub_model)
    try:
        assert ctx_a.loaded_profile.api_port != ctx_b.loaded_profile.api_port
        assert ctx_a.deps.persistence.db_path != ctx_b.deps.persistence.db_path

        client_a = real_build_app(ctx_a).test_client()
        client_b = real_build_app(ctx_b).test_client()

        # Each deployment only knows about its own commander.
        resp_a_own = client_a.get("/SYSTEM", headers=auth_headers("commander-a"))
        resp_a_others = client_a.get("/SYSTEM", headers=auth_headers("commander-b"))
        assert resp_a_own.status_code == 200
        assert resp_a_others.status_code == 401

        resp_b_own = client_b.get("/SYSTEM", headers=auth_headers("commander-b"))
        assert resp_b_own.status_code == 200
    finally:
        for ctx in (ctx_a, ctx_b):
            ctx.queue.stop()
            ctx.scheduler.stop()
            ctx.deps.persistence.close()
