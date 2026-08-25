import pytest

from profiles.loader import load_profile
from protocols.model import CriticalityLevel


@pytest.fixture
def loaded(monkeypatch, test_core_model, test_sub_model):
    monkeypatch.setenv("BOT_TOKEN", "token")
    # MODEL_CREDENTIAL_ENVS is empty in profiles/demo.py — nothing to set
    # there any more (docs/profile_spec.md "Model tiers"). profiles/demo.py
    # only *declares* its one agent (AgentSpec(cls=ReferenceAgent,
    # tier="sub")) — load_profile is what resolves the tier and
    # constructs it, so both tiers just come from the fixtures directly;
    # the profile module itself never touches os.environ at all any more.
    return load_profile("profiles.demo", core_model=test_core_model, sub_model=test_sub_model)


def test_loads_and_validates_successfully(loaded):
    assert loaded.agents
    assert loaded.protocols


def test_constructs_the_reference_agent_with_a_model(loaded):
    assert len(loaded.agents) == 1
    agent = loaded.agents[0]
    assert agent.name == "reference_agent"
    assert agent.model


def test_at_least_one_protocol_approves_only_the_read_only_tool(loaded):
    read_only_only = [p for p in loaded.protocols if set(p.approved_tools) == {"check_status"}]
    assert read_only_only


def test_at_least_one_protocol_approves_the_side_effecting_tool(loaded):
    side_effecting = [p for p in loaded.protocols if "record_action" in p.approved_tools]
    assert side_effecting


def test_at_least_one_flagged_and_one_unflagged_protocol(loaded):
    flags = {p.approval_flag for p in loaded.protocols}
    assert True in flags
    assert False in flags


def test_a_tie_pair_exists_with_distinct_criticality(loaded):
    by_name = {p.name: p for p in loaded.protocols}
    tie_a, tie_b = by_name["minor_incident_review"], by_name["routine_check"]

    # overlapping enough to force a tie: same participating agent, same
    # approved tools, same expected success output
    assert tie_a.approved_tools == tie_b.approved_tools
    assert tie_a.expected_success_output == tie_b.expected_success_output

    assert tie_a.criticality != tie_b.criticality
    assert max(tie_a.criticality, tie_b.criticality) == CriticalityLevel.MEDIUM


def test_event_types_and_areas_are_declared(loaded):
    assert "fire" in loaded.event_types
    assert "medical" in loaded.event_types
    assert "north_sector" in loaded.areas
    assert "south_sector" in loaded.areas


def test_starting_settings_values_are_declared(loaded):
    assert loaded.retry_count == 3
    assert loaded.risk_threshold == 0.6
    assert loaded.lookback_window_days == 30
