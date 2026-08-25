import config.base as base_config
from tools.logging_config import log_ai_interaction


def test_raw_ai_exchange_logs_only_when_debug_flag_is_enabled(monkeypatch, caplog):
    monkeypatch.setattr(base_config, "DEBUG_FLAG", False)
    with caplog.at_level("DEBUG"):
        log_ai_interaction("history", "secret prompt", "secret response")
    assert caplog.records == []

    monkeypatch.setattr(base_config, "DEBUG_FLAG", True)
    with caplog.at_level("DEBUG"):
        log_ai_interaction("history", "secret prompt", "secret response", stage="summarization", trace_id="trace")

    [record] = [r for r in caplog.records if getattr(r, "event", None) == "model_io"]
    assert record.levelname == "DEBUG"
    assert record.agent == "history"
    assert record.stage == "summarization"
    assert record.prompt == "secret prompt"
    assert record.response == "secret response"
    assert record.trace_id == "trace"


def test_stage_defaults_to_the_current_stage_context_when_not_given_explicitly(monkeypatch, caplog):
    from tools.tracing import stage_context

    monkeypatch.setattr(base_config, "DEBUG_FLAG", True)
    with caplog.at_level("DEBUG"), stage_context("risk_assessment"):
        log_ai_interaction("main_agent", "prompt", "response")

    [record] = [r for r in caplog.records if getattr(r, "event", None) == "model_io"]
    assert record.stage == "risk_assessment"
