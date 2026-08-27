from types import SimpleNamespace

from protocols.loader import load_protocols
from protocols.model import CriticalityLevel, Protocol


def _protocol(name):
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
    loaded_profile = SimpleNamespace(protocols=(_protocol("a"), _protocol("b")))

    protocol_set = load_protocols(loaded_profile)

    assert {p.name for p in protocol_set.all()} == {"a", "b"}


def test_get_by_name():
    loaded_profile = SimpleNamespace(protocols=(_protocol("a"),))
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
    assert {f for f in step.__dataclass_fields__} == {"agent_name", "task_text", "allowed_tools"}
