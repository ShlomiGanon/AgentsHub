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
        """Minimal CrewAI-shaped kickoff result with a raw text field."""

        def __init__(self, raw):
            """Initialize this test helper."""
            self.raw = raw

    class _FakeCrewAgent:
        """In-process CrewAI Agent stand-in that returns a canned kickoff result."""

        def __init__(self, **kwargs):
            """Initialize this test helper."""
            pass

        def kickoff(self, text):
            """Return the scripted kickoff text without calling a model."""
            return _FakeOutput(kickoff_text)

    fake_module = types.SimpleNamespace(
        Agent=_FakeCrewAgent,
        LLM=lambda **kwargs: kwargs["model"],
        tools=types.SimpleNamespace(BaseTool=object),
    )
    monkeypatch.setattr(adapter, "_get_crewai", lambda: fake_module)
    adapter._clear_agent_cache()
    adapter._clear_llm_cache()
