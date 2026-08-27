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
