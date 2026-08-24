from types import SimpleNamespace

from agents.reference import ReferenceAgent
from profiles.validate import validate_profile
from protocols.model import CriticalityLevel, Protocol
from tests.helpers import FakeAgent, FakeProtocol, ShapelessProtocol


def _loaded(agents=(), protocols=(), areas=("x",)):
    return SimpleNamespace(agents=agents, protocols=protocols, areas=areas)


def test_reports_protocol_naming_an_unconstructed_agent():
    protocol = FakeProtocol(participating_agents=("nobody",))
    failures = validate_profile(_loaded(protocols=(protocol,)), declared_event_types=["fire"])

    assert any("nobody" in f and protocol.name in f for f in failures)


def test_reports_protocol_approving_a_tool_no_agent_exposes():
    agent = FakeAgent(name="a1", tools=())
    protocol = FakeProtocol(participating_agents=("a1",), approved_tools=("phantom_tool",))
    failures = validate_profile(_loaded(agents=(agent,), protocols=(protocol,)), declared_event_types=["fire"])

    assert any("phantom_tool" in f for f in failures)


def test_protocol_may_approve_fewer_tools_than_its_agents_own():
    agent = FakeAgent(name="a1", tools=("t1", "t2"))
    protocol = FakeProtocol(participating_agents=("a1",), approved_tools=("t1",))
    failures = validate_profile(_loaded(agents=(agent,), protocols=(protocol,)), declared_event_types=["fire"])

    assert failures == []


def test_reports_missing_description():
    protocol = FakeProtocol(description="")
    failures = validate_profile(_loaded(protocols=(protocol,)), declared_event_types=["fire"])

    assert any("no description" in f for f in failures)


def test_reports_missing_criticality():
    protocol = FakeProtocol(criticality=None)
    failures = validate_profile(_loaded(protocols=(protocol,)), declared_event_types=["fire"])

    assert any("criticality" in f for f in failures)


def test_absent_approval_flag_is_a_failure_not_a_default():
    protocol = FakeProtocol(approval_flag=None)
    failures = validate_profile(_loaded(protocols=(protocol,)), declared_event_types=["fire"])

    assert any("approval flag" in f for f in failures)


def test_explicit_false_approval_flag_is_valid():
    protocol = FakeProtocol(approval_flag=False)
    failures = validate_profile(_loaded(protocols=(protocol,)), declared_event_types=["fire"])

    assert not any("approval flag" in f for f in failures)


def test_protocol_missing_required_attrs_entirely_is_reported():
    failures = validate_profile(_loaded(protocols=(ShapelessProtocol(),)), declared_event_types=["fire"])

    assert any("missing required attribute" in f for f in failures)


def test_no_event_types_is_a_failure():
    failures = validate_profile(_loaded(), declared_event_types=[])

    assert any("no event types" in f for f in failures)


def test_declaring_human_activation_is_a_duplicate_failure():
    failures = validate_profile(_loaded(), declared_event_types=["human_activation"])

    assert any("human_activation" in f for f in failures)


def test_no_areas_is_a_failure():
    failures = validate_profile(_loaded(areas=()), declared_event_types=["fire"])

    assert any("no areas" in f for f in failures)


def test_a_valid_profile_reports_no_failures():
    agent = FakeAgent(name="a1", tools=("t1",))
    protocol = FakeProtocol(participating_agents=("a1",), approved_tools=("t1",))
    failures = validate_profile(_loaded(agents=(agent,), protocols=(protocol,), areas=("x",)), declared_event_types=["fire"])

    assert failures == []


def test_every_failure_is_collected_not_only_the_first():
    protocol = FakeProtocol(description="", criticality=None, approval_flag=None)
    failures = validate_profile(_loaded(protocols=(protocol,), areas=()), declared_event_types=[])

    # description, criticality, approval flag, no event types, no areas
    assert len(failures) == 5


# -- Regression: real Agent.exposed_tools() returns ToolInfo objects, not --
# -- plain strings — a mismatch the duck-typed FakeAgent above never      --
# -- surfaced, since its exposed_tools() already returned plain strings.  --


def test_real_agent_and_real_protocol_validate_cleanly():
    agent = ReferenceAgent(model="m")
    protocol = Protocol(
        name="status_check",
        description="applies when a location's status needs confirming",
        participating_agents=("reference_agent",),
        approved_tools=("check_status",),
        expected_success_output="a status report",
        criticality=CriticalityLevel.LOW,
        approval_flag=False,
    )

    failures = validate_profile(_loaded(agents=(agent,), protocols=(protocol,)), declared_event_types=["fire"])

    assert failures == []


def test_a_plain_string_criticality_is_rejected():
    # §1.6, tightened after the Mission 8 coverage audit: criticality must
    # be a real CriticalityLevel enum member — a string that merely looks
    # like one ("low") is not accepted, since api/protocols.py,
    # protocols/editor.py, and orchestrator/selection.py all either crash
    # or silently miscompare on anything else.
    protocol = FakeProtocol(criticality="low")
    failures = validate_profile(_loaded(protocols=(protocol,)), declared_event_types=["fire"])

    assert any("criticality" in f and "low" in f for f in failures)


def test_a_real_criticalitylevel_member_passes():
    protocol = FakeProtocol(criticality=CriticalityLevel.HIGH)
    failures = validate_profile(_loaded(protocols=(protocol,)), declared_event_types=["fire"])

    assert not any("criticality" in f for f in failures)


def test_real_agent_still_rejects_a_genuinely_unapproved_tool():
    agent = ReferenceAgent(model="m")
    protocol = Protocol(
        name="bad",
        description="d",
        participating_agents=("reference_agent",),
        approved_tools=("not_a_real_tool",),
        expected_success_output="x",
        criticality=CriticalityLevel.LOW,
        approval_flag=False,
    )

    failures = validate_profile(_loaded(agents=(agent,), protocols=(protocol,)), declared_event_types=["fire"])

    assert any("not_a_real_tool" in f for f in failures)
