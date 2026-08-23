import textwrap

import pytest

from profiles.loader import ProfileLoadError, ProfileValidationError, load_profile
from tests.helpers import write_profile_module

BOT_TOKEN_ENV = "TEST_LOADER_BOT_TOKEN"
MODEL_CRED_ENV = "TEST_LOADER_MODEL_KEY"


def _write(tmp_path, monkeypatch, module_name, **kwargs):
    write_profile_module(tmp_path, monkeypatch, module_name, bot_token_env=BOT_TOKEN_ENV, model_cred_env=MODEL_CRED_ENV, **kwargs)


def test_missing_profile_argument_fails_immediately():
    with pytest.raises(ProfileLoadError):
        load_profile("")


def test_nonexistent_module_fails_naming_it():
    with pytest.raises(ProfileLoadError, match="does_not_exist_xyz"):
        load_profile("does_not_exist_xyz")


def test_missing_required_attribute_fails_naming_it(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, "broken_profile_missing_port", omit=("API_PORT",))

    with pytest.raises(ProfileLoadError, match="API_PORT"):
        load_profile("broken_profile_missing_port")


def test_missing_bot_token_env_fails_naming_it(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, "profile_missing_bot_env")
    monkeypatch.delenv(BOT_TOKEN_ENV, raising=False)
    monkeypatch.delenv(MODEL_CRED_ENV, raising=False)

    with pytest.raises(ProfileLoadError, match=BOT_TOKEN_ENV):
        load_profile("profile_missing_bot_env")


def test_missing_model_credential_env_fails_naming_it(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, "profile_missing_model_env")
    monkeypatch.setenv(BOT_TOKEN_ENV, "token")
    monkeypatch.delenv(MODEL_CRED_ENV, raising=False)

    with pytest.raises(ProfileLoadError, match=MODEL_CRED_ENV):
        load_profile("profile_missing_model_env")


def test_valid_minimal_fixture_profile_loads_and_freezes(monkeypatch):
    monkeypatch.setenv("AGENTSHUB_FIXTURE_BOT_TOKEN", "token-value")
    monkeypatch.setenv("AGENTSHUB_FIXTURE_MODEL_KEY", "key-value")

    loaded = load_profile("fixtures.profiles.minimal_profile")

    assert "human_activation" in loaded.event_types
    assert "fire" in loaded.event_types
    assert loaded.areas == ("north_sector", "south_sector")
    assert loaded.resolved_secrets["AGENTSHUB_FIXTURE_BOT_TOKEN"] == "token-value"
    assert tuple(loaded.core_agents) == ("history_agent",)


def test_loaded_profile_is_frozen(monkeypatch):
    monkeypatch.setenv("AGENTSHUB_FIXTURE_BOT_TOKEN", "token-value")
    monkeypatch.setenv("AGENTSHUB_FIXTURE_MODEL_KEY", "key-value")
    loaded = load_profile("fixtures.profiles.minimal_profile")

    with pytest.raises(Exception):
        loaded.db_path = "/somewhere/else.db"


def test_profile_with_unresolvable_protocol_agent_fails_validation(tmp_path, monkeypatch):
    prelude = textwrap.dedent(
        """
        from tests.helpers import FakeAgent, FakeProtocol

        AGENTS = [FakeAgent(name="a1", tools=())]
        PROTOCOLS = [FakeProtocol(name="bad", participating_agents=("ghost",))]
        """
    )
    _write(tmp_path, monkeypatch, "profile_bad_agent_ref", omit=("AGENTS", "PROTOCOLS"), extra_prelude=prelude)
    monkeypatch.setenv(BOT_TOKEN_ENV, "token")
    monkeypatch.setenv(MODEL_CRED_ENV, "key")

    with pytest.raises(ProfileValidationError) as exc_info:
        load_profile("profile_bad_agent_ref")

    assert any("ghost" in failure for failure in exc_info.value.failures)
