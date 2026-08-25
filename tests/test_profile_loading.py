import textwrap

import pytest

from profiles.loader import ProfileLoadError, ProfileValidationError, hash_profile_file, load_profile
from tests.helpers import write_profile_module

BOT_TOKEN_ENV = "TEST_LOADER_BOT_TOKEN"
MODEL_CRED_ENV = "TEST_LOADER_MODEL_KEY"


def _write(tmp_path, monkeypatch, module_name, **kwargs):
    write_profile_module(tmp_path, monkeypatch, module_name, bot_token_env=BOT_TOKEN_ENV, model_cred_env=MODEL_CRED_ENV, **kwargs)


def test_missing_profile_argument_fails_immediately(test_core_model, test_sub_model):
    with pytest.raises(ProfileLoadError):
        load_profile("", core_model=test_core_model, sub_model=test_sub_model)


def test_nonexistent_module_fails_naming_it(test_core_model, test_sub_model):
    with pytest.raises(ProfileLoadError, match="does_not_exist_xyz"):
        load_profile("does_not_exist_xyz", core_model=test_core_model, sub_model=test_sub_model)


def test_missing_required_attribute_fails_naming_it(tmp_path, monkeypatch, test_core_model, test_sub_model):
    _write(tmp_path, monkeypatch, "broken_profile_missing_port", omit=("API_PORT",))

    with pytest.raises(ProfileLoadError, match="API_PORT"):
        load_profile("broken_profile_missing_port", core_model=test_core_model, sub_model=test_sub_model)


def test_missing_bot_token_env_fails_naming_it(tmp_path, monkeypatch, test_core_model, test_sub_model):
    _write(tmp_path, monkeypatch, "profile_missing_bot_env")
    monkeypatch.delenv(BOT_TOKEN_ENV, raising=False)
    monkeypatch.delenv(MODEL_CRED_ENV, raising=False)

    with pytest.raises(ProfileLoadError, match=BOT_TOKEN_ENV):
        load_profile("profile_missing_bot_env", core_model=test_core_model, sub_model=test_sub_model)


def test_missing_model_credential_env_fails_naming_it(tmp_path, monkeypatch, test_core_model, test_sub_model):
    _write(tmp_path, monkeypatch, "profile_missing_model_env")
    monkeypatch.setenv(BOT_TOKEN_ENV, "token")
    monkeypatch.delenv(MODEL_CRED_ENV, raising=False)

    with pytest.raises(ProfileLoadError, match=MODEL_CRED_ENV):
        load_profile("profile_missing_model_env", core_model=test_core_model, sub_model=test_sub_model)


def test_valid_minimal_fixture_profile_loads_and_freezes(monkeypatch, test_core_model, test_sub_model):
    monkeypatch.setenv("AGENTSHUB_FIXTURE_BOT_TOKEN", "token-value")
    monkeypatch.setenv("AGENTSHUB_FIXTURE_MODEL_KEY", "key-value")

    loaded = load_profile("fixtures.profiles.minimal_profile", core_model=test_core_model, sub_model=test_sub_model)

    assert "human_activation" in loaded.event_types
    assert "fire" in loaded.event_types
    assert loaded.areas == ("north_sector", "south_sector")
    assert loaded.resolved_secrets["AGENTSHUB_FIXTURE_BOT_TOKEN"] == "token-value"
    assert tuple(loaded.core_agents) == ("history_agent",)


def test_loaded_profile_is_frozen(monkeypatch, test_core_model, test_sub_model):
    monkeypatch.setenv("AGENTSHUB_FIXTURE_BOT_TOKEN", "token-value")
    monkeypatch.setenv("AGENTSHUB_FIXTURE_MODEL_KEY", "key-value")
    loaded = load_profile("fixtures.profiles.minimal_profile", core_model=test_core_model, sub_model=test_sub_model)

    with pytest.raises(Exception):
        loaded.db_path = "/somewhere/else.db"


def test_profile_file_hash_matches_a_direct_hash_of_the_same_file(monkeypatch, test_core_model, test_sub_model):
    monkeypatch.setenv("AGENTSHUB_FIXTURE_BOT_TOKEN", "token-value")
    monkeypatch.setenv("AGENTSHUB_FIXTURE_MODEL_KEY", "key-value")

    loaded = load_profile("fixtures.profiles.minimal_profile", core_model=test_core_model, sub_model=test_sub_model)

    assert loaded.profile_file_hash == hash_profile_file("fixtures.profiles.minimal_profile")


def test_profile_file_hash_changes_when_the_file_on_disk_changes(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, "hash_check_profile")

    before = hash_profile_file("hash_check_profile")

    import importlib

    spec = importlib.util.find_spec("hash_check_profile")
    path = spec.origin
    with open(path, "a", encoding="utf-8") as f:
        f.write("\n# a pending, not-yet-restarted edit\n")

    after = hash_profile_file("hash_check_profile")

    assert before != after


def test_profile_agent_with_an_invalid_tier_fails_to_load_naming_it(tmp_path, monkeypatch, test_core_model, test_sub_model):
    # AGENTS declares a tier — "core"/"sub" are the only two that mean
    # anything; anything else must fail loudly, naming what's wrong,
    # rather than a raw KeyError deep inside the loader.
    prelude = textwrap.dedent(
        """
        from agents.reference import ReferenceAgent
        from profiles.spec import AgentSpec

        AGENTS = [AgentSpec(cls=ReferenceAgent, tier="bogus")]
        """
    )
    _write(tmp_path, monkeypatch, "profile_bad_tier_name", omit=("AGENTS",), extra_prelude=prelude)
    monkeypatch.setenv(BOT_TOKEN_ENV, "token")
    monkeypatch.setenv(MODEL_CRED_ENV, "key")

    with pytest.raises(ProfileLoadError, match="bogus"):
        load_profile("profile_bad_tier_name", core_model=test_core_model, sub_model=test_sub_model)


def test_agents_entries_that_are_not_agentspecs_fail_loudly_naming_the_index(tmp_path, monkeypatch, test_core_model, test_sub_model):
    # Full replacement, not "old already-built-instance shape still works
    # alongside the new one" — an AGENTS entry that isn't an AgentSpec
    # (e.g. an already-constructed agent, the pre-refactor shape) must
    # fail loudly, not be silently accepted or crash unhelpfully.
    prelude = textwrap.dedent(
        """
        from agents.reference import ReferenceAgent

        AGENTS = [ReferenceAgent(model="m")]
        """
    )
    _write(tmp_path, monkeypatch, "profile_agents_not_specs", omit=("AGENTS",), extra_prelude=prelude)
    monkeypatch.setenv(BOT_TOKEN_ENV, "token")
    monkeypatch.setenv(MODEL_CRED_ENV, "key")

    with pytest.raises(ProfileLoadError, match=r"AGENTS\[0\]"):
        load_profile("profile_agents_not_specs", core_model=test_core_model, sub_model=test_sub_model)


def test_profile_agents_on_different_tiers_resolve_to_the_matching_tier_model(tmp_path, monkeypatch, test_core_model, test_sub_model):
    from config.base import build_tier_model

    core_model = build_tier_model("anthropic", "claude-3-5-sonnet", "core-secret")
    sub_model = build_tier_model("openrouter", "meta-llama/llama-3.1-8b-instruct:free", "sub-secret")

    prelude = textwrap.dedent(
        """
        from agents.base import Agent
        from agents.reference import ReferenceAgent
        from profiles.spec import AgentSpec


        class _CoreTierAgent(Agent):
            name = "core_tier_agent"
            role = "role"
            system_prompt = "prompt"


        AGENTS = [
            AgentSpec(cls=ReferenceAgent, tier="sub"),
            AgentSpec(cls=_CoreTierAgent, tier="core"),
        ]
        """
    )
    _write(tmp_path, monkeypatch, "profile_tiered_agents", omit=("AGENTS",), extra_prelude=prelude)
    monkeypatch.setenv(BOT_TOKEN_ENV, "token")
    monkeypatch.setenv(MODEL_CRED_ENV, "key")

    loaded = load_profile("profile_tiered_agents", core_model=core_model, sub_model=sub_model)

    by_name = {agent.name: agent for agent in loaded.agents}
    assert by_name["reference_agent"].descriptor.model == sub_model.model
    assert by_name["reference_agent"].descriptor.api_key == sub_model.api_key
    assert by_name["core_tier_agent"].descriptor.model == core_model.model
    assert by_name["core_tier_agent"].descriptor.api_key == core_model.api_key
    assert by_name["reference_agent"].descriptor.model != by_name["core_tier_agent"].descriptor.model


def test_profile_import_needs_zero_environment_variables_set(tmp_path, monkeypatch, test_core_model, test_sub_model):
    # The whole point of AgentSpec: a profile module never touches
    # os.environ at all — importing it (and loading it, given already-
    # resolved TierModel values) must work with nothing set.
    for name in (
        "CORE_MODEL_PROVIDER", "CORE_MODEL_NAME", "CORE_MODEL_API_KEY_ENV",
        "SUB_MODEL_PROVIDER", "SUB_MODEL_NAME", "SUB_MODEL_API_KEY_ENV",
    ):
        monkeypatch.delenv(name, raising=False)

    prelude = textwrap.dedent(
        """
        from agents.reference import ReferenceAgent
        from profiles.spec import AgentSpec

        AGENTS = [AgentSpec(cls=ReferenceAgent, tier="sub")]
        """
    )
    _write(tmp_path, monkeypatch, "profile_needs_no_env", omit=("AGENTS",), extra_prelude=prelude)
    monkeypatch.setenv(BOT_TOKEN_ENV, "token")
    monkeypatch.setenv(MODEL_CRED_ENV, "key")

    loaded = load_profile("profile_needs_no_env", core_model=test_core_model, sub_model=test_sub_model)

    assert loaded.agents[0].name == "reference_agent"


def test_profile_with_unresolvable_protocol_agent_fails_validation(tmp_path, monkeypatch, test_core_model, test_sub_model):
    prelude = textwrap.dedent(
        """
        from agents.reference import ReferenceAgent
        from profiles.spec import AgentSpec
        from tests.helpers import FakeProtocol

        AGENTS = [AgentSpec(cls=ReferenceAgent, tier="sub")]
        PROTOCOLS = [FakeProtocol(name="bad", participating_agents=("ghost",))]
        """
    )
    _write(tmp_path, monkeypatch, "profile_bad_agent_ref", omit=("AGENTS", "PROTOCOLS"), extra_prelude=prelude)
    monkeypatch.setenv(BOT_TOKEN_ENV, "token")
    monkeypatch.setenv(MODEL_CRED_ENV, "key")

    with pytest.raises(ProfileValidationError) as exc_info:
        load_profile("profile_bad_agent_ref", core_model=test_core_model, sub_model=test_sub_model)

    assert any("ghost" in failure for failure in exc_info.value.failures)
