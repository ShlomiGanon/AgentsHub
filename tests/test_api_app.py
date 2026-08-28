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
import types

import pytest

from agents import adapter
from api import app as api_app
from api.app import build_app, build_context

BOT_TOKEN_ENV = "AGENTSHUB_FIXTURE_BOT_TOKEN"
MODEL_CRED_ENV = "AGENTSHUB_FIXTURE_MODEL_KEY"


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

    fake_module = types.SimpleNamespace(Agent=_FakeCrewAgent, tools=types.SimpleNamespace(BaseTool=object))
    monkeypatch.setattr(adapter, "_get_crewai", lambda: fake_module)


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


def test_get_system_succeeds_against_the_real_wiring(test_core_model, test_sub_model):
    ctx = build_context("fixtures.profiles.minimal_profile", core_model=test_core_model, sub_model=test_sub_model)
    try:
        ctx.deps.persistence.write_user("u1", "viewer")
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
    ctx = build_context("fixtures.profiles.minimal_profile", core_model=test_core_model, sub_model=test_sub_model)
    try:
        ctx.deps.persistence.write_user("u1", "viewer")
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

import pytest

from api.auth import authenticate, require
from api.errors import AuthenticationError, AuthorizationError
from auth.permissions import PermissionLevel
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


def test_require_permits_an_action_at_or_above_its_level():
    require(PermissionLevel.COMMANDER, "approve_run")  # does not raise
    require(PermissionLevel.VIEWER, "send_message")  # does not raise


def test_require_rejects_an_action_below_its_level():
    with pytest.raises(AuthorizationError):
        require(PermissionLevel.VIEWER, "approve_run")
