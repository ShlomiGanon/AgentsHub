"""Protocol loading and profile-source editing."""

import importlib
import sys
import uuid

import pytest

from agents.reference import ReferenceAgent
from protocols.editor import ProtocolEditError, add_protocol, read_protocols, remove_protocol, replace_protocol
from protocols.loader import ProtocolSet
from protocols.model import CriticalityLevel, Protocol

_PROFILE_TEMPLATE = """
from protocols.model import Protocol, CriticalityLevel

PROFILE_NAME = "For Tests"
AGENTS = []
PROTOCOLS = [
    Protocol(
        name="existing",
        description="applies to X",
        participating_agents=(),
        approved_tools=(),
        expected_success_output="y",
        criticality=CriticalityLevel.LOW,
        approval_flag=False,
    ),
]
EVENT_TYPES = ["fire"]
AREAS = ["north"]
DB_PATH = {db_path!r}
API_PORT = 9999
RETRY_COUNT = 1
RISK_THRESHOLD = 0.5
LOOKBACK_WINDOW_DAYS = 10
BOT_TOKEN_ENV = "TEST_TOKEN"
MODEL_CREDENTIAL_ENVS = []
"""


@pytest.fixture
def profile_module(tmp_path, monkeypatch):
    module_name = f"editor_test_profile_{uuid.uuid4().hex}"
    content = _PROFILE_TEMPLATE.format(db_path=str(tmp_path / "test.db"))
    (tmp_path / f"{module_name}.py").write_text(content, encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    return module_name


def _reimport(module_name):
    sys.modules.pop(module_name, None)
    return importlib.import_module(module_name)


def _protocol(**overrides):
    fields = dict(
        name="new_one",
        description="applies to Y",
        participating_agents=(),
        approved_tools=(),
        expected_success_output="z",
        criticality=CriticalityLevel.HIGH,
        approval_flag=True,
    )
    fields.update(overrides)
    return Protocol(**fields)


def test_read_protocols_is_a_pass_through():
    protocol = _protocol()
    protocol_set = ProtocolSet(protocols=(protocol,))

    assert read_protocols(protocol_set) == (protocol,)


def test_add_protocol_reports_the_running_system_is_unchanged(profile_module):
    module = importlib.import_module(profile_module)

    result = add_protocol(profile_module, tuple(module.PROTOCOLS), {}, _protocol())

    assert "unchanged" in result
    assert "next start" in result


def test_add_protocol_does_not_touch_the_already_loaded_set(profile_module):
    module = importlib.import_module(profile_module)

    add_protocol(profile_module, tuple(module.PROTOCOLS), {}, _protocol())

    assert {p.name for p in module.PROTOCOLS} == {"existing"}


def test_add_protocol_is_visible_only_after_reimport(profile_module):
    module = importlib.import_module(profile_module)

    add_protocol(profile_module, tuple(module.PROTOCOLS), {}, _protocol())

    reloaded = _reimport(profile_module)
    assert {p.name for p in reloaded.PROTOCOLS} == {"existing", "new_one"}


def test_add_protocol_rejects_a_name_that_already_exists(profile_module):
    module = importlib.import_module(profile_module)
    dup = _protocol(name="existing")

    with pytest.raises(ProtocolEditError, match="already exists"):
        add_protocol(profile_module, tuple(module.PROTOCOLS), {}, dup)


def test_add_protocol_rejects_an_unresolvable_agent_reference(profile_module):
    module = importlib.import_module(profile_module)
    bad = _protocol(participating_agents=("ghost",))

    with pytest.raises(ProtocolEditError, match="ghost"):
        add_protocol(profile_module, tuple(module.PROTOCOLS), {}, bad)


def test_add_protocol_rejects_a_tool_the_named_agent_does_not_expose(profile_module):
    module = importlib.import_module(profile_module)
    agent = ReferenceAgent(model="m")
    bad = _protocol(participating_agents=("reference_agent",), approved_tools=("not_real",))

    with pytest.raises(ProtocolEditError, match="not_real"):
        add_protocol(profile_module, tuple(module.PROTOCOLS), {"reference_agent": agent}, bad)


def test_replace_protocol_updates_the_named_entry(profile_module):
    module = importlib.import_module(profile_module)
    updated = _protocol(name="existing", description="applies to Z now", criticality=CriticalityLevel.MEDIUM)

    replace_protocol(profile_module, tuple(module.PROTOCOLS), {}, updated)

    reloaded = _reimport(profile_module)
    assert len(reloaded.PROTOCOLS) == 1
    assert reloaded.PROTOCOLS[0].description == "applies to Z now"
    assert reloaded.PROTOCOLS[0].criticality == CriticalityLevel.MEDIUM


def test_replace_protocol_rejects_an_unknown_name(profile_module):
    module = importlib.import_module(profile_module)
    ghost = _protocol(name="ghost")

    with pytest.raises(ProtocolEditError, match="use add"):
        replace_protocol(profile_module, tuple(module.PROTOCOLS), {}, ghost)


def test_remove_protocol_deletes_the_named_entry(profile_module):
    module = importlib.import_module(profile_module)

    remove_protocol(profile_module, tuple(module.PROTOCOLS), "existing")

    reloaded = _reimport(profile_module)
    assert reloaded.PROTOCOLS == []


def test_remove_protocol_rejects_an_unknown_name(profile_module):
    module = importlib.import_module(profile_module)

    with pytest.raises(ProtocolEditError):
        remove_protocol(profile_module, tuple(module.PROTOCOLS), "does_not_exist")


def test_file_remains_importable_after_a_write(profile_module):
    module = importlib.import_module(profile_module)

    add_protocol(profile_module, tuple(module.PROTOCOLS), {}, _protocol())

    reloaded = _reimport(profile_module)
    assert {p.name for p in reloaded.PROTOCOLS} == {"existing", "new_one"}


def test_write_leaves_no_tmp_file_behind(profile_module, tmp_path):
    module = importlib.import_module(profile_module)

    add_protocol(profile_module, tuple(module.PROTOCOLS), {}, _protocol())

    assert list(tmp_path.glob("*.tmp")) == []


def test_a_write_touches_only_the_protocols_assignment(profile_module):
    module = importlib.import_module(profile_module)

    add_protocol(profile_module, tuple(module.PROTOCOLS), {}, _protocol())

    reloaded = _reimport(profile_module)
    # everything else in the file survived untouched
    assert reloaded.EVENT_TYPES == ["fire"]
    assert reloaded.AREAS == ["north"]
    assert reloaded.API_PORT == 9999

from agents.errors import AgentModelError
from agents.results import AgentResult
from agents.runtime import ToolInfo
from protocols.executor import execute_steps
from protocols.model import Step

READ_ONLY_TOOL = (ToolInfo(name="check_status", description="d", side_effecting=False, idempotent=None),)


class _ScriptedAgent:
    def __init__(self, name, responses):
        self.name = name
        self._responses = list(responses)
        self.calls = []

    def exposed_tools(self):
        return READ_ONLY_TOOL

    def process(self, text, allowed_tools):
        self.calls.append((text, tuple(allowed_tools)))
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class _FakeSettings:
    def get_retry_count(self):
        return 3


def _no_sleep(seconds):
    pass


def _step(agent_name, task_text, allowed_tools=("check_status",)):
    return Step(agent_name=agent_name, task_text=task_text, allowed_tools=allowed_tools)


def test_a_single_successful_step():
    agent = _ScriptedAgent("a1", [AgentResult(status="success", text="ok")])
    steps = [_step("a1", "check gate 3")]

    result = execute_steps(steps, {"a1": agent}, _FakeSettings(), sleep_fn=_no_sleep)

    assert result.completed
    assert len(result.step_outcomes) == 1
    assert result.step_outcomes[0].result_text == "ok"


def test_task_text_reaches_the_agent_unmodified():
    agent = _ScriptedAgent("a1", [AgentResult(status="success", text="ok")])
    steps = [_step("a1", "exactly this text, nothing added")]

    execute_steps(steps, {"a1": agent}, _FakeSettings(), sleep_fn=_no_sleep)

    assert agent.calls[0][0] == "exactly this text, nothing added"


def test_approved_tools_are_passed_through_exactly():
    agent = _ScriptedAgent("a1", [AgentResult(status="success", text="ok")])
    steps = [_step("a1", "x", allowed_tools=("check_status", "some_other_tool"))]

    execute_steps(steps, {"a1": agent}, _FakeSettings(), sleep_fn=_no_sleep)

    assert agent.calls[0][1] == ("check_status", "some_other_tool")


def test_multi_step_run_executes_every_step_in_order_when_all_succeed():
    agent_a = _ScriptedAgent("a1", [AgentResult(status="success", text="first")])
    agent_b = _ScriptedAgent("a2", [AgentResult(status="success", text="second")])
    steps = [_step("a1", "do first"), _step("a2", "do second")]

    result = execute_steps(steps, {"a1": agent_a, "a2": agent_b}, _FakeSettings(), sleep_fn=_no_sleep)

    assert result.completed
    assert [o.result_text for o in result.step_outcomes] == ["first", "second"]


def test_run_stops_at_the_first_permanent_failure_and_keeps_prior_results():
    agent_a = _ScriptedAgent("a1", [AgentResult(status="success", text="first")])
    agent_b = _ScriptedAgent("a2", [AgentModelError("a2", "boom")] * 3)  # exhausts the limit of 3
    agent_c = _ScriptedAgent("a3", [AgentResult(status="success", text="never reached")])
    steps = [_step("a1", "first"), _step("a2", "second"), _step("a3", "third")]

    result = execute_steps(steps, {"a1": agent_a, "a2": agent_b, "a3": agent_c}, _FakeSettings(), sleep_fn=_no_sleep)

    assert not result.completed
    assert result.failed_step_index == 1
    assert result.failed_step_agent == "a2"
    assert result.failure_cause is not None
    # step one's result is preserved even though the run failed overall
    assert len(result.step_outcomes) == 2
    assert result.step_outcomes[0].result_text == "first"
    assert result.step_outcomes[0].succeeded is True
    assert result.step_outcomes[1].succeeded is False
    # the third step was never attempted
    assert agent_c.calls == []


def test_steps_are_independent_one_failing_does_not_touch_anothers_call_log():
    agent_a = _ScriptedAgent("a1", [AgentModelError("a1", "boom")] * 3)
    agent_b = _ScriptedAgent("a2", [AgentResult(status="success", text="second")])
    steps = [_step("a1", "first"), _step("a2", "second")]

    execute_steps(steps, {"a1": agent_a, "a2": agent_b}, _FakeSettings(), sleep_fn=_no_sleep)

    assert agent_b.calls == []  # never reached, and nothing about it was touched


from types import SimpleNamespace

from protocols.loader import load_protocols
from protocols.model import CriticalityLevel, Protocol


def _loaded_protocol(name):
    return Protocol(
        name=name,
        description="d",
        participating_agents=(),
        approved_tools=(),
        expected_success_output="x",
        criticality=CriticalityLevel.LOW,
        approval_flag=False,
    )


def test_load_protocols_wraps_the_loaded_profiles_protocols():
    loaded_profile = SimpleNamespace(protocols=(_loaded_protocol("a"), _loaded_protocol("b")))

    protocol_set = load_protocols(loaded_profile)

    assert {p.name for p in protocol_set.all()} == {"a", "b"}


def test_get_by_name():
    loaded_profile = SimpleNamespace(protocols=(_loaded_protocol("a"),))
    protocol_set = load_protocols(loaded_profile)

    assert protocol_set.get("a").name == "a"
    assert protocol_set.get("does_not_exist") is None


def test_protocol_set_holds_nothing_beyond_what_it_was_given():
    loaded_profile = SimpleNamespace(protocols=())
    protocol_set = load_protocols(loaded_profile)

    assert protocol_set.all() == ()

from protocols.model import CriticalityLevel, Protocol, Step


def test_protocol_holds_all_declared_fields():
    protocol = Protocol(
        name="p1",
        description="applies when X, not when Y",
        participating_agents=("reference_agent",),
        approved_tools=("check_status",),
        expected_success_output="a status report",
        criticality=CriticalityLevel.HIGH,
        approval_flag=True,
    )

    assert protocol.name == "p1"
    assert protocol.criticality == CriticalityLevel.HIGH
    assert protocol.approval_flag is True


def test_criticality_is_ordered_for_tie_breaking():
    assert CriticalityLevel.HIGH > CriticalityLevel.MEDIUM > CriticalityLevel.LOW
    assert max(CriticalityLevel.LOW, CriticalityLevel.HIGH, CriticalityLevel.MEDIUM) == CriticalityLevel.HIGH


def test_step_has_exactly_three_fields():
    step = Step(agent_name="reference_agent", task_text="check gate 3", allowed_tools=("check_status",))

    assert step.agent_name == "reference_agent"
    assert step.task_text == "check gate 3"
    assert step.allowed_tools == ("check_status",)
    assert {f for f in step.__dataclass_fields__} == {
        "agent_name", "task_text", "allowed_tools", "step_id", "depends_on"
    }
    assert step.step_id == ""
    assert step.depends_on == ()
