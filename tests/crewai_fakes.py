"""Shared CrewAI adapter stub for tests that never talk to a real model.

`tests/test_agent_runtime.py` keeps its own richer fake (captured kwargs,
custom kickoff behavior). One-off orchestrator tests that return a specific
decision string also keep a local stub.
"""

from __future__ import annotations

import types

from agents import adapter

DEFAULT_KICKOFF_TEXT = "status nominal, no anomalies"


def install_crewai_stub(monkeypatch, kickoff_text: str = DEFAULT_KICKOFF_TEXT) -> None:
    """Route `agents.adapter._get_crewai` to an in-process stand-in."""

    class _FakeOutput:
        def __init__(self, raw):
            self.raw = raw

    class _FakeCrewAgent:
        def __init__(self, **kwargs):
            pass

        def kickoff(self, text):
            return _FakeOutput(kickoff_text)

    fake_module = types.SimpleNamespace(
        Agent=_FakeCrewAgent,
        LLM=lambda **kwargs: kwargs["model"],
        tools=types.SimpleNamespace(BaseTool=object),
    )
    monkeypatch.setattr(adapter, "_get_crewai", lambda: fake_module)
    adapter._clear_agent_cache()
    adapter._clear_llm_cache()
