"""api/app.py's own wiring (work_plan.md §7, `build_context`/`create_app`).

Every other `test_api_*.py` file builds its `ApiContext` through
`tests/api_fakes.py`'s hand-rolled `build_context` — a parallel
implementation that mimics `api.app.build_context`'s wiring but never
calls it. This file is the one place the *real* `api.app.build_context`/
`create_app` are exercised, against a real profile
(`fixtures.profiles.minimal_profile`) — the exact gap the Mission 8
coverage audit found: nothing had ever called these functions for real,
and the first real call crashed (`api/management.py::protocol_to_dict`
assumed `criticality` was always a real `CriticalityLevel` enum; the
fixture profile it was tried against used a plain string). Fixed at the
validation boundary (§1.6) rather than at the consumer — see
`profiles/loader.py`'s and `fixtures/profiles/minimal_profile.py`'s own
notes on that decision.
"""

import os
from pathlib import Path
import subprocess
import sys
import types

import pytest

from agents import adapter
from api import app as api_app
from api.app import build_app, build_context

BOT_TOKEN_ENV = "AGENTSHUB_FIXTURE_BOT_TOKEN"
MODEL_CRED_ENV = "AGENTSHUB_FIXTURE_MODEL_KEY"


def test_module_entry_point_does_not_preimport_api_app():
    result = subprocess.run(
        [sys.executable, "-W", "error::RuntimeWarning", "-m", "api.app", "--help"],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "RuntimeWarning" not in result.stderr


@pytest.fixture(autouse=True)
def _mock_crewai(monkeypatch):
    # Not strictly required for this file's own assertions (no test here
    # calls an agent's .process(), and CrewAI construction is lazy — see
    # agents/base.py) — kept anyway for consistency with every other api/
    # test file, and so this file stays safe if a future test here does
    # end up invoking an agent.
    class _FakeOutput:
        def __init__(self, raw):
            self.raw = raw

    class _FakeCrewAgent:
        def __init__(self, **kwargs):
            pass

        def kickoff(self, text):
            return _FakeOutput("status nominal, no anomalies")

    fake_module = types.SimpleNamespace(Agent=_FakeCrewAgent, LLM=lambda **kwargs: kwargs["model"], tools=types.SimpleNamespace(BaseTool=object))
    monkeypatch.setattr(adapter, "_get_crewai", lambda: fake_module)
    monkeypatch.setattr(api_app, "initialize_agent_runtime", lambda agents: tuple(agent.model for agent in agents))


@pytest.fixture(autouse=True)
def _fixture_profile_env(monkeypatch):
    monkeypatch.setenv(BOT_TOKEN_ENV, "token")
    monkeypatch.setenv(MODEL_CRED_ENV, "key")


def test_build_context_succeeds_against_a_real_profile(test_core_model, test_sub_model):
    # This is the exact call that crashed during the Mission 8 coverage
    # audit, before the criticality fix — confirming it no longer does.
    ctx = build_context("fixtures.profiles.minimal_profile", core_model=test_core_model, sub_model=test_sub_model)
    try:
        assert ctx.loaded_profile.module_path == "fixtures.profiles.minimal_profile"
        assert ctx.main_agent is not None
        assert ctx.insights_agent is not None
        assert "history_agent" in [a.name for a in ctx.deps.registry.all()]
    finally:
        ctx.queue.stop()
        ctx.deps.persistence.close()


def test_model_warmup_finishes_before_queue_and_scheduler_start(monkeypatch, test_core_model, test_sub_model):
    order = []
    monkeypatch.setattr(api_app, "initialize_agent_runtime", lambda agents: order.append("warmup"))

    original_queue_start = api_app.SerialEventQueue.start
    original_scheduler_start = api_app.SummaryScheduler.start
    monkeypatch.setattr(api_app.SerialEventQueue, "start", lambda self: (order.append("queue"), original_queue_start(self))[1])
    monkeypatch.setattr(
        api_app.SummaryScheduler,
        "start",
        lambda self: (order.append("scheduler"), original_scheduler_start(self))[1],
    )

    ctx = build_context("fixtures.profiles.minimal_profile", core_model=test_core_model, sub_model=test_sub_model)
    try:
        assert order[:3] == ["warmup", "queue", "scheduler"]
    finally:
        ctx.scheduler.stop()
        ctx.queue.stop()
        ctx.deps.persistence.close()


def test_model_warmup_failure_prevents_queue_start(monkeypatch, test_core_model, test_sub_model):
    monkeypatch.setattr(api_app, "initialize_agent_runtime", lambda agents: (_ for _ in ()).throw(RuntimeError("bad model")))
    queue_started = []
    monkeypatch.setattr(api_app.SerialEventQueue, "start", lambda self: queue_started.append(True))

    with pytest.raises(RuntimeError, match="bad model"):
        build_context("fixtures.profiles.minimal_profile", core_model=test_core_model, sub_model=test_sub_model)

    assert queue_started == []


def test_get_system_succeeds_against_the_real_wiring(test_core_model, test_sub_model):
    # protocols are commander-only (view_system_internals) — "u1" must be a
    # commander to see them in the GET /SYSTEM payload.
    ctx = build_context("fixtures.profiles.minimal_profile", core_model=test_core_model, sub_model=test_sub_model)
    try:
        ctx.deps.persistence.write_user("u1", "commander")
        client = build_app(ctx).test_client()

        resp = client.get("/SYSTEM", headers={"X-Identity": "u1"})

        assert resp.status_code == 200
        body = resp.get_json()
        assert body["profile"] == "fixtures.profiles.minimal_profile"
        [protocol] = body["protocols"]
        assert protocol["name"] == "basic_response"
        assert protocol["criticality"] == "low"
    finally:
        ctx.queue.stop()
        ctx.deps.persistence.close()


def test_get_protocol_succeeds_against_the_real_wiring(test_core_model, test_sub_model):
    # list_protocols is commander-only — "u1" must be a commander.
    ctx = build_context("fixtures.profiles.minimal_profile", core_model=test_core_model, sub_model=test_sub_model)
    try:
        ctx.deps.persistence.write_user("u1", "commander")
        client = build_app(ctx).test_client()

        resp = client.get("/Protocol", headers={"X-Identity": "u1"})

        assert resp.status_code == 200
        [protocol] = resp.get_json()["protocols"]
        assert protocol["name"] == "basic_response"
        assert protocol["criticality"] == "low"
        assert protocol["approval_flag"] is False
    finally:
        ctx.queue.stop()
        ctx.deps.persistence.close()


def test_get_system_requires_authentication_against_the_real_wiring(test_core_model, test_sub_model):
    ctx = build_context("fixtures.profiles.minimal_profile", core_model=test_core_model, sub_model=test_sub_model)
    try:
        client = build_app(ctx).test_client()

        resp = client.get("/SYSTEM")  # no identity header — must be refused, not crash

        assert resp.status_code == 401
    finally:
        ctx.queue.stop()
        ctx.deps.persistence.close()


# -- api.app.main() — the real root that reads os.environ for model-tier config --


def test_main_fails_loudly_naming_the_missing_tier_env_var(monkeypatch):
    for name in ("CORE_MODEL_PROVIDER", "CORE_MODEL_NAME", "CORE_MODEL_API_KEY_ENV"):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(SystemExit, match="CORE_MODEL_PROVIDER"):
        api_app.main(["fixtures.profiles.minimal_profile"])


def test_main_translates_runtime_warmup_failure_before_starting_http(monkeypatch):
    from agents import AgentWarmupError
    from config import TierModel

    monkeypatch.setattr(api_app, "_tier_model_from_environ", lambda prefix: TierModel("openai/test", "secret"))
    monkeypatch.setattr(api_app, "configure_telemetry", lambda: None)
    monkeypatch.setattr(
        api_app,
        "build_context",
        lambda *args, **kwargs: (_ for _ in ()).throw(AgentWarmupError("runtime", "model unavailable")),
    )

    with pytest.raises(SystemExit, match="failed to start API.*model unavailable"):
        api_app.main(["fixtures.profiles.minimal_profile"])

import pytest

from api.auth import authenticate, require
from api.errors import AuthenticationError, AuthorizationError
from auth.permissions import PermissionLevel, RequestedOperation
from persistence.sqlite_store import SQLitePersistence


@pytest.fixture
def store(tmp_path):
    backend = SQLitePersistence(str(tmp_path / "auth_test.db"))
    backend.write_user("viewer-1", "viewer")
    backend.write_user("commander-1", "commander")
    yield backend
    backend.close()


def test_authenticate_returns_the_registered_level(store):
    assert authenticate(store, "viewer-1") == PermissionLevel.VIEWER
    assert authenticate(store, "commander-1") == PermissionLevel.COMMANDER


def test_authenticate_rejects_an_unregistered_identity(store):
    with pytest.raises(AuthenticationError):
        authenticate(store, "nobody")


def test_authenticate_rejects_a_missing_identity(store):
    with pytest.raises(AuthenticationError):
        authenticate(store, None)

    with pytest.raises(AuthenticationError):
        authenticate(store, "")


def test_require_permits_an_operation_the_caller_is_authorized_for():
    require(PermissionLevel.COMMANDER, RequestedOperation.APPROVE_RUN)  # does not raise
    require(PermissionLevel.VIEWER, RequestedOperation.SUBMIT_MESSAGE)  # does not raise


def test_require_rejects_an_operation_the_caller_is_not_authorized_for():
    with pytest.raises(AuthorizationError):
        require(PermissionLevel.VIEWER, RequestedOperation.APPROVE_RUN)


def test_require_permits_every_requested_operation_for_a_commander():
    for operation in RequestedOperation:
        require(PermissionLevel.COMMANDER, operation)  # does not raise


def test_commander_never_denied_for_any_defined_operation_via_the_api_boundary():
    # docs/Next_Plan.md §6 success criteria: exhaustive proof a commander is
    # allowed for every RequestedOperation, exercised through require() itself
    # rather than is_permitted() directly.
    for operation in RequestedOperation:
        try:
            require(PermissionLevel.COMMANDER, operation)
        except AuthorizationError:
            pytest.fail(f"commander was denied {operation.value!r}, which docs/Next_Plan.md §2.1 forbids")


def test_require_rejects_a_legacy_action_string():
    # docs/Next_Plan.md Stage 2: every api/routes.py call site now passes
    # RequestedOperation; the transitional string path from Stage 1 is gone.
    with pytest.raises(TypeError):
        require(PermissionLevel.COMMANDER, "approve_run")
