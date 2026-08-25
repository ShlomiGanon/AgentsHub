"""config.base.build_tier_model / load_base_config (docs/profile_spec.md
"Model tiers").

Both are pure now — no environment access, no `Mapping`, no knowledge
that their inputs might have come from an environment variable at all.
The env-reading/`*_MODEL_API_KEY_ENV`-indirection logic that used to live
here moved to each of the four roots' own private helper (`api.app.main`,
`bot.app.main`, `cli.user_admin.main`, `conftest.py`'s `test_core_model`/
`test_sub_model` fixtures) — covered where it now actually lives, not
here.

Uses `import config.base as base_config` and `base_config.X` throughout,
not `from config.base import X` — `tests/test_base_config.py`'s own
`importlib.reload(base_config)` runs earlier in the suite (alphabetical
collection order) and replaces `TierModel`/`BaseConfig` with new class
objects in place; a name bound once at collection time via `from ... import
...` would keep pointing at the pre-reload class, while `build_tier_model`
(looked up dynamically through the module) would construct instances of
the post-reload class — an equality/isinstance mismatch with nothing
actually wrong. Reading everything off the module at call time keeps both
sides on whatever generation is currently live.
"""

import config.base as base_config


def test_build_tier_model_joins_provider_and_model_name():
    result = base_config.build_tier_model("openrouter", "anthropic/claude-3.5-sonnet", "sk-or-v1-secret")

    assert result == base_config.TierModel(model="openrouter/anthropic/claude-3.5-sonnet", api_key="sk-or-v1-secret")


def test_build_tier_model_never_touches_the_real_environment(monkeypatch):
    # A conflicting real env var must have zero effect — the function
    # takes plain strings and only ever uses exactly what it's given.
    monkeypatch.setenv("CORE_MODEL_PROVIDER", "SHOULD_NEVER_BE_USED")

    result = base_config.build_tier_model("openrouter", "some-model", "secret")

    assert "SHOULD_NEVER_BE_USED" not in result.model
    assert "SHOULD_NEVER_BE_USED" not in result.api_key


def test_two_calls_with_different_arguments_are_genuinely_independent():
    # No caching/memoization — same provider, two different models/keys.
    first = base_config.build_tier_model("openrouter", "model-a", "key-one")
    second = base_config.build_tier_model("openrouter", "model-b", "key-two")

    assert first.model != second.model
    assert first.api_key != second.api_key


def test_two_agents_built_from_different_tier_models_get_different_configs():
    from agents.reference import ReferenceAgent

    core_tier = base_config.build_tier_model("anthropic", "claude-3-5-sonnet", "sk-ant-core")
    sub_tier = base_config.build_tier_model("openrouter", "meta-llama/llama-3.1-8b-instruct:free", "sk-or-v1-sub")

    core_agent = ReferenceAgent(model=core_tier.model, api_key=core_tier.api_key)
    sub_agent = ReferenceAgent(model=sub_tier.model, api_key=sub_tier.api_key)

    assert core_agent.descriptor.model == "anthropic/claude-3-5-sonnet"
    assert sub_agent.descriptor.model == "openrouter/meta-llama/llama-3.1-8b-instruct:free"
    assert core_agent.descriptor.api_key != sub_agent.descriptor.api_key


def test_load_base_config_wraps_the_given_core_model():
    core_model = base_config.build_tier_model("openrouter", "anthropic/claude-3.5-sonnet", "sk-or-v1-secret")

    config = base_config.load_base_config(core_model=core_model)

    assert isinstance(config, base_config.BaseConfig)
    assert config.core_model is core_model
    assert config.DEBUG_FLAG is False


def test_base_config_is_frozen():
    core_model = base_config.build_tier_model("openrouter", "m", "k")
    config = base_config.load_base_config(core_model=core_model)

    try:
        config.core_model = base_config.build_tier_model("openrouter", "something-else", "k2")
        assert False, "BaseConfig should be immutable"
    except AttributeError:
        pass
