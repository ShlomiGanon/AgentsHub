"""Environment-backed configuration behavior."""

from config.base import _parse_console_json_flag, _parse_debug_flag


def test_debug_flag_parsing_is_strict_not_any_non_empty_string():
    # Absent or unset — never an error, never on by default.
    assert _parse_debug_flag(None) is False
    assert _parse_debug_flag("") is False

    # Explicitly falsy values must mean off, not "any non-empty string is
    # truthy" — this is the exact case docs/server_report.md's follow-up
    # asked to be tested directly.
    assert _parse_debug_flag("false") is False
    assert _parse_debug_flag("False") is False
    assert _parse_debug_flag("FALSE") is False
    assert _parse_debug_flag("0") is False
    assert _parse_debug_flag("no") is False
    assert _parse_debug_flag("off") is False
    assert _parse_debug_flag("garbage") is False

    # Only these turn it on, any case, optionally padded with whitespace.
    assert _parse_debug_flag("1") is True
    assert _parse_debug_flag("true") is True
    assert _parse_debug_flag("True") is True
    assert _parse_debug_flag("TRUE") is True
    assert _parse_debug_flag("  true  ") is True


def test_deep_debug_defaults_off_and_is_read_once_at_import(monkeypatch):
    import importlib
    import config.base as base_config

    monkeypatch.setenv("DEEP_DEBUG", "true")
    importlib.reload(base_config)
    try:
        assert base_config.DEEP_DEBUG is True
    finally:
        monkeypatch.delenv("DEEP_DEBUG", raising=False)
        importlib.reload(base_config)

    assert base_config.DEEP_DEBUG is False


def test_debug_flag_is_read_from_the_environment_variable_once_at_import(monkeypatch):
    import importlib

    import config.base as base_config

    monkeypatch.setenv("DEBUG_VERBOSE_LOGGING", "true")
    importlib.reload(base_config)
    try:
        assert base_config.DEBUG_FLAG is True
        core_model = base_config.build_tier_model("openrouter", "m", "k")
        assert base_config.load_base_config(core_model=core_model).DEBUG_FLAG is True
    finally:
        monkeypatch.delenv("DEBUG_VERBOSE_LOGGING", raising=False)
        importlib.reload(base_config)  # restore normal (unset -> False) state for every other test

    assert base_config.DEBUG_FLAG is False


def test_console_json_flag_defaults_on_and_only_an_explicit_falsy_value_turns_it_off():
    # Unset, empty, or garbage — on by default, the exact opposite
    # default from _parse_debug_flag, and deliberately so: this flag
    # exists to opt *out* of behavior every existing caller already
    # depends on, so "unset" must reproduce that existing behavior.
    assert _parse_console_json_flag(None) is True
    assert _parse_console_json_flag("") is True
    assert _parse_console_json_flag("garbage") is True
    assert _parse_console_json_flag("true") is True
    assert _parse_console_json_flag("1") is True

    # Only these turn it off, any case, optionally padded with whitespace.
    assert _parse_console_json_flag("false") is False
    assert _parse_console_json_flag("False") is False
    assert _parse_console_json_flag("FALSE") is False
    assert _parse_console_json_flag("0") is False
    assert _parse_console_json_flag("  false  ") is False


def test_console_json_flag_is_read_from_the_environment_variable_once_at_import(monkeypatch):
    import importlib

    import config.base as base_config

    monkeypatch.setenv("LOG_CONSOLE_JSON", "false")
    importlib.reload(base_config)
    try:
        assert base_config.LOG_CONSOLE_JSON_ENABLED is False
    finally:
        monkeypatch.delenv("LOG_CONSOLE_JSON", raising=False)
        importlib.reload(base_config)  # restore normal (unset -> True) state for every other test

    assert base_config.LOG_CONSOLE_JSON_ENABLED is True

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
