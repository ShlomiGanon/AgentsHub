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
