from agents import adapter
from agents.history import HistoryAgent


def test_history_agent_has_no_tools_and_uses_standard_process(monkeypatch):
    captured = {}

    def fake_invoke(descriptor, wrapped_tools, text, timeout_seconds):
        captured["descriptor"] = descriptor
        captured["tools"] = wrapped_tools
        return "faithful summary"

    monkeypatch.setattr(adapter, "invoke", fake_invoke)
    agent = HistoryAgent("test-model")
    result = agent.process("summarize supplied records", allowed_tools=[])

    assert agent.exposed_tools() == ()
    assert result.text == "faithful summary"
    assert "contradictory" in agent.system_prompt
    assert "outside knowledge" in agent.system_prompt
