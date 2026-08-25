from config.base import _parse_debug_flag


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
