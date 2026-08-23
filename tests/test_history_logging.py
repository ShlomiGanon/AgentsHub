import config.base as base_config
from tools.logging_config import log_ai_interaction


def test_raw_ai_exchange_prints_only_when_debug_flag_is_enabled(monkeypatch, capsys):
    monkeypatch.setattr(base_config, "DEBUG_FLAG", False)
    log_ai_interaction("history", "secret prompt", "secret response")
    assert capsys.readouterr().out == ""

    monkeypatch.setattr(base_config, "DEBUG_FLAG", True)
    log_ai_interaction("history", "secret prompt", "secret response", trace_id="trace")
    output = capsys.readouterr().out
    assert "secret prompt" in output
    assert "secret response" in output
