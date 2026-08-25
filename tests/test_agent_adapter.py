import sys
import types

import pytest

from agents import adapter
from agents.descriptor import AgentDescriptor
from agents.errors import (
    AgentFrameworkNotReadyError,
    AgentModelError,
    AgentOutputParseError,
    AgentTimeoutError,
    AgentToolConstructionError,
)
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


# -- crewai not installed --------------------------------------------------
# crewai is genuinely installed in this environment (1.15.17) — its absence
# is simulated here via sys.modules, the standard technique (an import
# raises ImportError when sys.modules[name] is exactly None), the same way
# every other test in this file mocks `_get_crewai` itself instead of
# relying on the real environment. This is the one function that can't be
# mocked that way, since it's the function under test.


def test_crewai_not_installed_raises_clearly(monkeypatch):
    monkeypatch.setitem(sys.modules, "crewai", None)

    with pytest.raises(AgentFrameworkNotReadyError):
        adapter._get_crewai()


def test_invoke_without_crewai_installed_raises_the_same_error(monkeypatch):
    # invoke() calls _get_crewai() before anything else — this proves that
    # absence propagates through invoke() as the same AgentFrameworkNotReadyError,
    # not translated into AgentModelError or some other outcome along the way.
    monkeypatch.setitem(sys.modules, "crewai", None)

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


# -- Explicit api_key -> crewai.LLM(model=..., api_key=...) -----------------
# (config.base.build_tier_model's normal output; docs/profile_spec.md
# "Model tiers" — this is the mechanism that gives two agents on the same
# provider two genuinely independent API keys.)


def test_invoke_builds_an_explicit_crewai_llm_when_api_key_is_set(monkeypatch):
    captured = {}

    class _FakeLLM:
        def __init__(self, **kwargs):
            captured["llm_kwargs"] = kwargs

    class _FakeAgent:
        def __init__(self, **kwargs):
            captured["agent_kwargs"] = kwargs

        def kickoff(self, text):
            return _FakeOutput("ok")

    fake_module = types.SimpleNamespace(Agent=_FakeAgent, LLM=_FakeLLM, tools=types.SimpleNamespace(BaseTool=_FakeBaseTool))
    monkeypatch.setattr(adapter, "_get_crewai", lambda: fake_module)

    adapter.invoke(_descriptor(model="openrouter/anthropic/claude-3.5-sonnet", api_key="sk-or-secret"), {}, "x", 10)

    assert captured["llm_kwargs"] == {"model": "openrouter/anthropic/claude-3.5-sonnet", "api_key": "sk-or-secret"}
    assert isinstance(captured["agent_kwargs"]["llm"], _FakeLLM)


def test_invoke_falls_back_to_a_bare_model_string_when_there_is_no_api_key(monkeypatch):
    # Legacy construction path (no api_key at all) — unchanged behavior,
    # relies on litellm's own implicit env-var lookup, same as before
    # api_key existed.
    fake_module, captured = _make_fake_crewai(lambda text: _FakeOutput("ok"))
    monkeypatch.setattr(adapter, "_get_crewai", lambda: fake_module)

    adapter.invoke(_descriptor(model="plain-model", api_key=None), {}, "x", 10)

    assert captured["agent_kwargs"]["llm"] == "plain-model"


def test_invoke_gives_two_agents_on_the_same_provider_genuinely_independent_keys(monkeypatch):
    # The exact scenario *_MODEL_API_KEY_ENV's two-level indirection
    # exists for: same provider, two distinct keys — litellm's implicit
    # provider-named env lookup (e.g. OPENROUTER_API_KEY) cannot express
    # this. Nothing here branches on provider name to make it work.
    captured_llms = []

    class _FakeLLM:
        def __init__(self, **kwargs):
            captured_llms.append(kwargs)

    class _FakeAgent:
        def __init__(self, **kwargs):
            pass

        def kickoff(self, text):
            return _FakeOutput("ok")

    fake_module = types.SimpleNamespace(Agent=_FakeAgent, LLM=_FakeLLM, tools=types.SimpleNamespace(BaseTool=_FakeBaseTool))
    monkeypatch.setattr(adapter, "_get_crewai", lambda: fake_module)

    adapter.invoke(_descriptor(model="openrouter/model-a", api_key="key-one"), {}, "x", 10)
    adapter.invoke(_descriptor(model="openrouter/model-b", api_key="key-two"), {}, "x", 10)

    assert captured_llms[0] == {"model": "openrouter/model-a", "api_key": "key-one"}
    assert captured_llms[1] == {"model": "openrouter/model-b", "api_key": "key-two"}
    assert captured_llms[0]["api_key"] != captured_llms[1]["api_key"]


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
    built = adapter._build_crewai_tools(fake_module, "test_agent", {"check_status": wrapped_check_status}, tool_infos)

    assert len(built) == 1
    assert built[0].name == "check_status"
    assert built[0].description == "Checks status."

    result = built[0]._run(location="gate-3")
    assert result == "status ok"
    assert seen == ["gate-3"]


def test_a_tool_that_fails_to_construct_raises_a_typed_error_naming_it(monkeypatch):
    # Simulates pydantic (or anything else) rejecting the dynamic
    # type()-created BaseTool subclass — the specific, currently-
    # unverified-against-real-crewai risk this test closes the gap on.
    class _RejectingBaseTool:
        def __init__(self, *args, **kwargs):
            raise TypeError("simulated: dynamic subclass construction rejected")

    fake_module = types.SimpleNamespace(
        Agent=lambda **kwargs: None,
        tools=types.SimpleNamespace(BaseTool=_RejectingBaseTool),
    )
    monkeypatch.setattr(adapter, "_get_crewai", lambda: fake_module)

    tool_infos = (ToolInfo(name="check_status", description="Checks status.", side_effecting=False, idempotent=None),)

    with pytest.raises(AgentToolConstructionError) as exc_info:
        adapter.invoke(_descriptor(tools=tool_infos), {"check_status": lambda **kw: "ok"}, "do something", 5)

    assert exc_info.value.agent_name == "a1"
    assert "check_status" in str(exc_info.value)
    assert isinstance(exc_info.value.cause, TypeError)


def test_tool_construction_failure_names_the_offending_tool_when_others_are_fine(monkeypatch):
    # Only the tool whose dynamic class name contains "risky_tool" fails
    # to construct — proves the error names the actual offending tool,
    # not just "something failed somewhere in the loop."
    class _SometimesRejectingBaseTool:
        def __init__(self, *args, **kwargs):
            if "risky_tool" in type(self).__name__:
                raise TypeError("simulated: this specific tool's construction failed")

    fake_module = types.SimpleNamespace(
        Agent=lambda **kwargs: None,
        tools=types.SimpleNamespace(BaseTool=_SometimesRejectingBaseTool),
    )
    monkeypatch.setattr(adapter, "_get_crewai", lambda: fake_module)

    tool_infos = (
        ToolInfo(name="fine_tool", description="d", side_effecting=False, idempotent=None),
        ToolInfo(name="risky_tool", description="d", side_effecting=False, idempotent=None),
    )
    wrapped_tools = {"fine_tool": lambda **kw: "ok", "risky_tool": lambda **kw: "ok"}

    with pytest.raises(AgentToolConstructionError) as exc_info:
        adapter.invoke(_descriptor(tools=tool_infos), wrapped_tools, "do something", 5)

    assert "risky_tool" in str(exc_info.value)
