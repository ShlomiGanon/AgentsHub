"""Agent runtime and CrewAI integration behavior."""

import sys
import types

import pytest

from agents import adapter
from agents.runtime import AgentDescriptor
from agents.errors import (
    AgentFrameworkNotReadyError,
    AgentModelError,
    AgentOutputParseError,
    AgentTimeoutError,
    AgentToolConstructionError,
)
from agents.runtime import ToolInfo


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

"""Tool, Agent, and LLM construction against the REAL crewai library, not
the mock.

Every test in `tests/test_agent_adapter.py` monkeypatches `_get_crewai()`
with a fake module whose `tools.BaseTool` is a plain class (or bare
`object`) — none of Pydantic v2's field-override rules apply to that fake,
so that file could never catch a defect specific to `crewai.tools.BaseTool`
actually being a Pydantic v2 model. It was exactly that gap that let
`_build_crewai_tools` reach production still building a dynamic subclass
with a bare, unannotated `{"name": ..., "description": ...}` class dict —
which `crewai.tools.BaseTool` rejects at class-creation time with
`PydanticUserError`, for every tool on every agent, universally (found via
a real end-to-end manual run, not by the automated suite — see
docs/server_report.md's account of that failure).

This file closes that gap: it imports the real, installed `crewai` package
(no monkeypatching of `_get_crewai()` at all) and drives
`agents.adapter._build_crewai_tools` against the real `crewai.tools.BaseTool`
for `ReferenceAgent`'s real tools. It costs nothing and needs no API key or
network access — building a `BaseTool` subclass is pure local construction,
never a model call — so, unlike `tests/sanity_check_real_model_call.py`,
this belongs in the normal automated suite and must run in CI, specifically
to catch this class of defect (a real-library incompatibility a full mock
can't surface) before it reaches a real run again.

The same gap exists one level up: `crewai.Agent` and `crewai.LLM` — the two
other real crewai classes `agents/adapter.py::invoke()` constructs — are
*also* Pydantic v2 models, and `tests/test_agent_adapter.py`'s `_FakeAgent`/
`_FakeLLM` accept arbitrary `**kwargs` with no validation at all. A
kwarg-shape regression there (a renamed or removed parameter, a wrong type)
would pass that mocked suite silently and only ever surface against the
real library — the same class of risk as the `BaseTool` bug, just not yet
triggered. The tests below close that gap too: real `crewai.Agent(...)`/
`crewai.LLM(...)` construction, with the exact kwargs `invoke()` uses,
construction only — never `.kickoff()` — so still no network call and no
real API key needed.
"""

import pytest

pytest.importorskip("crewai")

from agents import adapter
from agents.reference import ReferenceAgent


def _real_crewai_module():
    # The one call in this whole file that must not be monkeypatched —
    # the real, installed crewai package, exactly as agents/adapter.py
    # imports it in production.
    return adapter._get_crewai()


def test_build_crewai_tools_against_real_base_tool_succeeds_for_every_reference_agent_tool():
    crewai_module = _real_crewai_module()
    agent = ReferenceAgent(model="openrouter/test-provider/test-model")

    # descriptor.tools holds every tool the agent exposes (agents/base.py) —
    # both of ReferenceAgent's, unfiltered by any per-call allowed_tools,
    # exactly what a real invoke() call builds every time (agents/adapter.py
    # ::invoke). Building both is the point: the original defect broke the
    # very first tool touched (alphabetically first, "check_status"), which
    # made it look tool-specific when it was universal — asserting both
    # succeed is what proves it's fixed for every tool, not just the one
    # that happened to surface the bug.
    built = adapter._build_crewai_tools(crewai_module, agent.name, agent._wrapped_tools, agent.descriptor.tools)

    assert {tool.name for tool in built} == {"check_status", "record_action"}

    base_tool_class = crewai_module.tools.BaseTool
    for tool in built:
        assert isinstance(tool, base_tool_class)

    by_name = {tool.name: tool for tool in built}
    assert by_name["check_status"].description == next(
        info.description for info in agent.descriptor.tools if info.name == "check_status"
    )
    assert by_name["record_action"].description == next(
        info.description for info in agent.descriptor.tools if info.name == "record_action"
    )


def test_a_real_built_tool_still_delegates_to_the_wrapped_agent_method():
    # Not just "construction didn't raise" — the resulting tool object must
    # still actually call through to the wrapped agent method, the same
    # behavior tests/test_agent_adapter.py's mocked equivalent checks.
    crewai_module = _real_crewai_module()
    agent = ReferenceAgent(model="openrouter/test-provider/test-model")

    built = adapter._build_crewai_tools(crewai_module, agent.name, agent._wrapped_tools, agent.descriptor.tools)
    check_status_tool = next(tool for tool in built if tool.name == "check_status")

    # The permission contextvar is only set inside Agent.process() — calling
    # the built tool directly here, outside that, is expected to hit the
    # "not permitted" branch rather than the real check_status body; the
    # point of this test is only that the call reaches the wrapper at all
    # (no crash, a real string back), not that permission-checking is
    # exercised here (agents/test_agent_permission_enforcement.py's job).
    result = check_status_tool._run(location="gate-3")
    assert isinstance(result, str)


# -- Real parameter exposure (the tool-parameter bug this file was extended
# for) ------------------------------------------------------------------
#
# Building successfully was never the whole story: `_build_crewai_tools`'s
# `_run` closure used to report a signature of `(self, *args, _wrapped=...,
# **kwargs)` to CrewAI's own schema auto-generator — `*args`/`**kwargs` are
# both skipped by that introspection, and the one named parameter it would
# otherwise see, `_wrapped`, is silently dropped by Pydantic for its leading
# underscore. Net result: every generated `args_schema` had zero fields, so
# a tool like check_status(location) looked to the model like it took no
# arguments at all — confirmed live: a real model call reached the tool with
# no arguments and got `check_status() missing 1 required positional
# argument: 'location'`. The tests below assert the schema itself, not just
# that construction didn't raise.


def test_check_status_tool_exposes_its_real_parameter_in_the_generated_schema():
    crewai_module = _real_crewai_module()
    agent = ReferenceAgent(model="openrouter/test-provider/test-model")

    built = adapter._build_crewai_tools(crewai_module, agent.name, agent._wrapped_tools, agent.descriptor.tools)
    check_status_tool = next(tool for tool in built if tool.name == "check_status")

    fields = check_status_tool.args_schema.model_fields
    assert set(fields) == {"location"}
    assert fields["location"].is_required()
    assert fields["location"].annotation is str


def test_record_action_tool_exposes_its_required_and_optional_parameters():
    # A second real tool with a different shape — one required parameter
    # plus one with a default — so the fix is checked against more than a
    # single-parameter coincidence.
    crewai_module = _real_crewai_module()
    agent = ReferenceAgent(model="openrouter/test-provider/test-model")

    built = adapter._build_crewai_tools(crewai_module, agent.name, agent._wrapped_tools, agent.descriptor.tools)
    record_action_tool = next(tool for tool in built if tool.name == "record_action")

    fields = record_action_tool.args_schema.model_fields
    assert set(fields) == {"location", "note"}
    assert fields["location"].is_required()
    assert not fields["note"].is_required()
    assert fields["note"].default == ""


def test_calling_the_real_tool_through_run_with_a_real_argument_reaches_the_wrapped_method():
    # Goes through CrewAI's own validate-then-dispatch path (BaseTool.run,
    # which validates kwargs against args_schema before calling _run) —
    # not `_run` directly — proving the fix works through crewai's real
    # argument-passing mechanism, the same path a live model call uses,
    # not just that a schema object with the right shape now exists.
    from agents.base import _current_allowed_tools

    crewai_module = _real_crewai_module()
    agent = ReferenceAgent(model="openrouter/test-provider/test-model")

    built = adapter._build_crewai_tools(crewai_module, agent.name, agent._wrapped_tools, agent.descriptor.tools)
    check_status_tool = next(tool for tool in built if tool.name == "check_status")

    # Agent.process() sets this contextvar for the duration of a real call
    # (agents/base.py) — set it directly here since we're driving the built
    # CrewAI tool object on its own, outside of process().
    token = _current_allowed_tools.set(frozenset({"check_status"}))
    try:
        result = check_status_tool.run(location="north_sector")
    finally:
        _current_allowed_tools.reset(token)

    assert result == "status for 'north_sector': nominal, no anomalies detected"


def test_calling_without_the_required_argument_is_rejected_by_schema_validation_not_a_bare_typeerror():
    # The exact failure mode the live bug reproduced: before this fix,
    # crewai's own validation had nothing to check against (an empty
    # schema), so a call with no arguments reached the real Python method
    # and blew up with a bare `TypeError: missing 1 required positional
    # argument`. With the schema fixed, crewai's own validation now catches
    # this first, as a clean, named error — the tool method is never even
    # reached with a call it can't satisfy.
    crewai_module = _real_crewai_module()
    agent = ReferenceAgent(model="openrouter/test-provider/test-model")

    built = adapter._build_crewai_tools(crewai_module, agent.name, agent._wrapped_tools, agent.descriptor.tools)
    check_status_tool = next(tool for tool in built if tool.name == "check_status")

    with pytest.raises(ValueError, match="location"):
        check_status_tool.run()


# -- Real Agent/LLM construction (the coverage gap one level up from
# BaseTool) ---------------------------------------------------------------


def test_real_crewai_llm_constructs_with_the_exact_kwargs_invoke_uses():
    # Mirrors agents/adapter.py::invoke()'s explicit-api_key branch exactly:
    # `crewai_module.LLM(model=descriptor.model, api_key=descriptor.api_key)`
    # — config.base.build_tier_model's normal output. Construction only, no
    # network call — crewai/litellm validate the presence of a key at
    # construction time (confirmed directly), not its validity.
    crewai_module = _real_crewai_module()

    llm = crewai_module.LLM(model="openrouter/anthropic/claude-3.5-sonnet", api_key="sk-or-test-key")

    assert isinstance(llm, crewai_module.BaseLLM)


def test_real_crewai_agent_constructs_with_an_explicit_llm_object_and_the_exact_kwargs_invoke_uses():
    # Mirrors invoke()'s crewai_module.Agent(...) call exactly, with a real
    # crewai.LLM object as `llm` (the explicit-api_key branch) and real
    # built tools — everything invoke() actually passes, construction only.
    from agents.results import UNCLEAR_TASK_PROMPT_INSTRUCTION

    crewai_module = _real_crewai_module()
    agent = ReferenceAgent(model="openrouter/test-provider/test-model", api_key="sk-or-test-key")

    crewai_tools = adapter._build_crewai_tools(crewai_module, agent.name, agent._wrapped_tools, agent.descriptor.tools)
    llm = crewai_module.LLM(model=agent.descriptor.model, api_key=agent.descriptor.api_key)
    backstory = f"{agent.descriptor.system_prompt}\n\n{UNCLEAR_TASK_PROMPT_INSTRUCTION}"

    crewai_agent = crewai_module.Agent(
        role=agent.descriptor.role,
        goal="Complete the task given, or state clearly what is missing if it cannot be completed.",
        backstory=backstory,
        llm=llm,
        tools=crewai_tools,
        max_execution_time=60,
        verbose=False,
    )

    assert isinstance(crewai_agent, crewai_module.Agent)
    assert crewai_agent.role == agent.descriptor.role


def test_real_crewai_agent_constructs_with_a_bare_model_string_when_there_is_no_api_key(monkeypatch):
    # The legacy/no-api_key branch invoke() also supports: `llm` is passed
    # as a plain model string, not wrapped in crewai.LLM(...) — relying on
    # litellm's implicit, provider-named env lookup, same as production.
    # crewai's own Agent resolves that string into a real LLM *eagerly*, at
    # construction time (confirmed directly) — not lazily at kickoff() — so
    # the provider's env var must be present (any value; never called over
    # the network here) for construction to succeed at all, exactly as it
    # would need to be in a real legacy-path deployment.
    from agents.results import UNCLEAR_TASK_PROMPT_INSTRUCTION

    monkeypatch.setenv("OPENROUTER_API_KEY", "fake-key-for-construction-only")

    crewai_module = _real_crewai_module()
    agent = ReferenceAgent(model="openrouter/test-provider/test-model")  # no api_key

    crewai_tools = adapter._build_crewai_tools(crewai_module, agent.name, agent._wrapped_tools, agent.descriptor.tools)
    backstory = f"{agent.descriptor.system_prompt}\n\n{UNCLEAR_TASK_PROMPT_INSTRUCTION}"

    crewai_agent = crewai_module.Agent(
        role=agent.descriptor.role,
        goal="Complete the task given, or state clearly what is missing if it cannot be completed.",
        backstory=backstory,
        llm=agent.descriptor.model,
        tools=crewai_tools,
        max_execution_time=60,
        verbose=False,
    )

    assert isinstance(crewai_agent, crewai_module.Agent)
