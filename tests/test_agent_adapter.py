import types

import pytest

from agents import adapter
from agents.descriptor import AgentDescriptor
from agents.errors import AgentFrameworkNotReadyError, AgentModelError, AgentOutputParseError, AgentTimeoutError
from agents.tooling import ToolInfo


class _FakeOutput:
    def __init__(self, raw):
        self.raw = raw


class _FakeBaseTool:
    pass


def _make_fake_crewai(kickoff_behavior):
    """`kickoff_behavior(text)` returns the fake `.kickoff()` result, or raises."""

    captured = {}

    class _FakeAgent:
        def __init__(self, **kwargs):
            captured["agent_kwargs"] = kwargs

        def kickoff(self, text):
            return kickoff_behavior(text)

    fake_module = types.SimpleNamespace(Agent=_FakeAgent, tools=types.SimpleNamespace(BaseTool=_FakeBaseTool))
    return fake_module, captured


def _descriptor(**overrides):
    fields = {"name": "a1", "role": "role", "system_prompt": "prompt", "tools": (), "model": "some-model"}
    fields.update(overrides)
    return AgentDescriptor(**fields)


# -- crewai not installed (the real state of this environment) --------------


def test_crewai_not_installed_raises_clearly():
    with pytest.raises(AgentFrameworkNotReadyError):
        adapter._get_crewai()


def test_invoke_without_crewai_installed_raises_the_same_error():
    with pytest.raises(AgentFrameworkNotReadyError):
        adapter.invoke(_descriptor(), {}, "do something", 30)


# -- invoke() against a fake crewai ------------------------------------------


def test_invoke_returns_raw_text_on_success(monkeypatch):
    fake_module, captured = _make_fake_crewai(lambda text: _FakeOutput(f"handled: {text}"))
    monkeypatch.setattr(adapter, "_get_crewai", lambda: fake_module)

    result = adapter.invoke(_descriptor(model="model-x"), {}, "do the thing", 30)

    assert result == "handled: do the thing"
    assert captured["agent_kwargs"]["llm"] == "model-x"
    assert captured["agent_kwargs"]["max_execution_time"] == 30


def test_invoke_routes_to_the_descriptors_own_model(monkeypatch):
    fake_module, captured = _make_fake_crewai(lambda text: _FakeOutput("ok"))
    monkeypatch.setattr(adapter, "_get_crewai", lambda: fake_module)

    adapter.invoke(_descriptor(model="agent-one-model"), {}, "x", 10)
    first_model = captured["agent_kwargs"]["llm"]

    adapter.invoke(_descriptor(model="agent-two-model"), {}, "x", 10)
    second_model = captured["agent_kwargs"]["llm"]

    assert first_model == "agent-one-model"
    assert second_model == "agent-two-model"


def test_invoke_translates_timeout(monkeypatch):
    def _raise(text):
        raise TimeoutError("too slow")

    fake_module, _ = _make_fake_crewai(_raise)
    monkeypatch.setattr(adapter, "_get_crewai", lambda: fake_module)

    with pytest.raises(AgentTimeoutError):
        adapter.invoke(_descriptor(), {}, "x", 5)


def test_invoke_translates_a_generic_model_failure(monkeypatch):
    def _raise(text):
        raise RuntimeError("api unreachable")

    fake_module, _ = _make_fake_crewai(_raise)
    monkeypatch.setattr(adapter, "_get_crewai", lambda: fake_module)

    with pytest.raises(AgentModelError):
        adapter.invoke(_descriptor(), {}, "x", 5)


def test_invoke_raises_on_output_with_no_raw_text(monkeypatch):
    fake_module, _ = _make_fake_crewai(lambda text: "a plain string with no .raw attribute")
    monkeypatch.setattr(adapter, "_get_crewai", lambda: fake_module)

    with pytest.raises(AgentOutputParseError):
        adapter.invoke(_descriptor(), {}, "x", 5)


def test_a_successful_call_is_never_retried_by_this_layer(monkeypatch):
    # invoke() itself does not retry — that's the retry policy's job (§4.5,
    # later); a call here either returns text once or raises once.
    calls = []

    def _behavior(text):
        calls.append(text)
        return _FakeOutput("ok")

    fake_module, _ = _make_fake_crewai(_behavior)
    monkeypatch.setattr(adapter, "_get_crewai", lambda: fake_module)

    adapter.invoke(_descriptor(), {}, "x", 5)
    assert len(calls) == 1


# -- Tool wiring --------------------------------------------------------------


def test_build_crewai_tools_wires_name_description_and_delegates_to_the_wrapper():
    fake_module, _ = _make_fake_crewai(lambda text: _FakeOutput("ok"))
    seen = []

    def wrapped_check_status(location):
        seen.append(location)
        return "status ok"

    tool_infos = (ToolInfo(name="check_status", description="Checks status.", side_effecting=False, idempotent=None),)
    built = adapter._build_crewai_tools(fake_module, {"check_status": wrapped_check_status}, tool_infos)

    assert len(built) == 1
    assert built[0].name == "check_status"
    assert built[0].description == "Checks status."

    result = built[0]._run(location="gate-3")
    assert result == "status ok"
    assert seen == ["gate-3"]
