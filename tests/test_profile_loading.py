import textwrap

import pytest

from profiles.loader import ProfileLoadError, ProfileValidationError, hash_profile_file, load_profile
from tests.helpers import write_profile_module

BOT_TOKEN_ENV = "TEST_LOADER_BOT_TOKEN"
MODEL_CRED_ENV = "TEST_LOADER_MODEL_KEY"


def _write(tmp_path, monkeypatch, module_name, **kwargs):
    write_profile_module(tmp_path, monkeypatch, module_name, bot_token_env=BOT_TOKEN_ENV, model_cred_env=MODEL_CRED_ENV, **kwargs)


def test_missing_profile_argument_fails_immediately(test_core_model, test_sub_model):
    with pytest.raises(ProfileLoadError):
        load_profile("", core_model=test_core_model, sub_model=test_sub_model)


def test_nonexistent_module_fails_naming_it(test_core_model, test_sub_model):
    with pytest.raises(ProfileLoadError, match="does_not_exist_xyz"):
        load_profile("does_not_exist_xyz", core_model=test_core_model, sub_model=test_sub_model)


def test_missing_required_attribute_fails_naming_it(tmp_path, monkeypatch, test_core_model, test_sub_model):
    _write(tmp_path, monkeypatch, "broken_profile_missing_port", omit=("API_PORT",))

    with pytest.raises(ProfileLoadError, match="API_PORT"):
        load_profile("broken_profile_missing_port", core_model=test_core_model, sub_model=test_sub_model)


def test_missing_bot_token_env_fails_naming_it(tmp_path, monkeypatch, test_core_model, test_sub_model):
    _write(tmp_path, monkeypatch, "profile_missing_bot_env")
    monkeypatch.delenv(BOT_TOKEN_ENV, raising=False)
    monkeypatch.delenv(MODEL_CRED_ENV, raising=False)

    with pytest.raises(ProfileLoadError, match=BOT_TOKEN_ENV):
        load_profile("profile_missing_bot_env", core_model=test_core_model, sub_model=test_sub_model)


def test_missing_model_credential_env_fails_naming_it(tmp_path, monkeypatch, test_core_model, test_sub_model):
    _write(tmp_path, monkeypatch, "profile_missing_model_env")
    monkeypatch.setenv(BOT_TOKEN_ENV, "token")
    monkeypatch.delenv(MODEL_CRED_ENV, raising=False)

    with pytest.raises(ProfileLoadError, match=MODEL_CRED_ENV):
        load_profile("profile_missing_model_env", core_model=test_core_model, sub_model=test_sub_model)


def test_valid_minimal_fixture_profile_loads_and_freezes(monkeypatch, test_core_model, test_sub_model):
    monkeypatch.setenv("AGENTSHUB_FIXTURE_BOT_TOKEN", "token-value")
    monkeypatch.setenv("AGENTSHUB_FIXTURE_MODEL_KEY", "key-value")

    loaded = load_profile("fixtures.profiles.minimal_profile", core_model=test_core_model, sub_model=test_sub_model)

    assert "human_activation" in loaded.event_types
    assert "fire" in loaded.event_types
    assert loaded.areas == ("north_sector", "south_sector")
    assert loaded.resolved_secrets["AGENTSHUB_FIXTURE_BOT_TOKEN"] == "token-value"
    assert tuple(loaded.core_agents) == ("history_agent",)


def test_loaded_profile_is_frozen(monkeypatch, test_core_model, test_sub_model):
    monkeypatch.setenv("AGENTSHUB_FIXTURE_BOT_TOKEN", "token-value")
    monkeypatch.setenv("AGENTSHUB_FIXTURE_MODEL_KEY", "key-value")
    loaded = load_profile("fixtures.profiles.minimal_profile", core_model=test_core_model, sub_model=test_sub_model)

    with pytest.raises(Exception):
        loaded.db_path = "/somewhere/else.db"


def test_profile_file_hash_matches_a_direct_hash_of_the_same_file(monkeypatch, test_core_model, test_sub_model):
    monkeypatch.setenv("AGENTSHUB_FIXTURE_BOT_TOKEN", "token-value")
    monkeypatch.setenv("AGENTSHUB_FIXTURE_MODEL_KEY", "key-value")

    loaded = load_profile("fixtures.profiles.minimal_profile", core_model=test_core_model, sub_model=test_sub_model)

    assert loaded.profile_file_hash == hash_profile_file("fixtures.profiles.minimal_profile")


def test_profile_file_hash_changes_when_the_file_on_disk_changes(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, "hash_check_profile")

    before = hash_profile_file("hash_check_profile")

    import importlib

    spec = importlib.util.find_spec("hash_check_profile")
    path = spec.origin
    with open(path, "a", encoding="utf-8") as f:
        f.write("\n# a pending, not-yet-restarted edit\n")

    after = hash_profile_file("hash_check_profile")

    assert before != after


def test_profile_agent_with_an_invalid_tier_fails_to_load_naming_it(tmp_path, monkeypatch, test_core_model, test_sub_model):
    # AGENTS declares a tier — "core"/"sub" are the only two that mean
    # anything; anything else must fail loudly, naming what's wrong,
    # rather than a raw KeyError deep inside the loader.
    prelude = textwrap.dedent(
        """
        from agents.reference import ReferenceAgent
        from profiles.spec import AgentSpec

        AGENTS = [AgentSpec(cls=ReferenceAgent, tier="bogus")]
        """
    )
    _write(tmp_path, monkeypatch, "profile_bad_tier_name", omit=("AGENTS",), extra_prelude=prelude)
    monkeypatch.setenv(BOT_TOKEN_ENV, "token")
    monkeypatch.setenv(MODEL_CRED_ENV, "key")

    with pytest.raises(ProfileLoadError, match="bogus"):
        load_profile("profile_bad_tier_name", core_model=test_core_model, sub_model=test_sub_model)


def test_agents_entries_that_are_not_agentspecs_fail_loudly_naming_the_index(tmp_path, monkeypatch, test_core_model, test_sub_model):
    # Full replacement, not "old already-built-instance shape still works
    # alongside the new one" — an AGENTS entry that isn't an AgentSpec
    # (e.g. an already-constructed agent, the pre-refactor shape) must
    # fail loudly, not be silently accepted or crash unhelpfully.
    prelude = textwrap.dedent(
        """
        from agents.reference import ReferenceAgent

        AGENTS = [ReferenceAgent(model="m")]
        """
    )
    _write(tmp_path, monkeypatch, "profile_agents_not_specs", omit=("AGENTS",), extra_prelude=prelude)
    monkeypatch.setenv(BOT_TOKEN_ENV, "token")
    monkeypatch.setenv(MODEL_CRED_ENV, "key")

    with pytest.raises(ProfileLoadError, match=r"AGENTS\[0\]"):
        load_profile("profile_agents_not_specs", core_model=test_core_model, sub_model=test_sub_model)


def test_profile_agents_on_different_tiers_resolve_to_the_matching_tier_model(tmp_path, monkeypatch, test_core_model, test_sub_model):
    from config.base import build_tier_model

    core_model = build_tier_model("anthropic", "claude-3-5-sonnet", "core-secret")
    sub_model = build_tier_model("openrouter", "meta-llama/llama-3.1-8b-instruct:free", "sub-secret")

    prelude = textwrap.dedent(
        """
        from agents.base import Agent
        from agents.reference import ReferenceAgent
        from profiles.spec import AgentSpec


        class _CoreTierAgent(Agent):
            name = "core_tier_agent"
            role = "role"
            system_prompt = "prompt"


        AGENTS = [
            AgentSpec(cls=ReferenceAgent, tier="sub"),
            AgentSpec(cls=_CoreTierAgent, tier="core"),
        ]
        """
    )
    _write(tmp_path, monkeypatch, "profile_tiered_agents", omit=("AGENTS",), extra_prelude=prelude)
    monkeypatch.setenv(BOT_TOKEN_ENV, "token")
    monkeypatch.setenv(MODEL_CRED_ENV, "key")

    loaded = load_profile("profile_tiered_agents", core_model=core_model, sub_model=sub_model)

    by_name = {agent.name: agent for agent in loaded.agents}
    assert by_name["reference_agent"].descriptor.model == sub_model.model
    assert by_name["reference_agent"].descriptor.api_key == sub_model.api_key
    assert by_name["core_tier_agent"].descriptor.model == core_model.model
    assert by_name["core_tier_agent"].descriptor.api_key == core_model.api_key
    assert by_name["reference_agent"].descriptor.model != by_name["core_tier_agent"].descriptor.model


def test_profile_import_needs_zero_environment_variables_set(tmp_path, monkeypatch, test_core_model, test_sub_model):
    # The whole point of AgentSpec: a profile module never touches
    # os.environ at all — importing it (and loading it, given already-
    # resolved TierModel values) must work with nothing set.
    for name in (
        "CORE_MODEL_PROVIDER", "CORE_MODEL_NAME", "CORE_MODEL_API_KEY_ENV",
        "SUB_MODEL_PROVIDER", "SUB_MODEL_NAME", "SUB_MODEL_API_KEY_ENV",
    ):
        monkeypatch.delenv(name, raising=False)

    prelude = textwrap.dedent(
        """
        from agents.reference import ReferenceAgent
        from profiles.spec import AgentSpec

        AGENTS = [AgentSpec(cls=ReferenceAgent, tier="sub")]
        """
    )
    _write(tmp_path, monkeypatch, "profile_needs_no_env", omit=("AGENTS",), extra_prelude=prelude)
    monkeypatch.setenv(BOT_TOKEN_ENV, "token")
    monkeypatch.setenv(MODEL_CRED_ENV, "key")

    loaded = load_profile("profile_needs_no_env", core_model=test_core_model, sub_model=test_sub_model)

    assert loaded.agents[0].name == "reference_agent"


def test_profile_with_unresolvable_protocol_agent_fails_validation(tmp_path, monkeypatch, test_core_model, test_sub_model):
    prelude = textwrap.dedent(
        """
        from agents.reference import ReferenceAgent
        from profiles.spec import AgentSpec
        from tests.helpers import FakeProtocol

        AGENTS = [AgentSpec(cls=ReferenceAgent, tier="sub")]
        PROTOCOLS = [FakeProtocol(name="bad", participating_agents=("ghost",))]
        """
    )
    _write(tmp_path, monkeypatch, "profile_bad_agent_ref", omit=("AGENTS", "PROTOCOLS"), extra_prelude=prelude)
    monkeypatch.setenv(BOT_TOKEN_ENV, "token")
    monkeypatch.setenv(MODEL_CRED_ENV, "key")

    with pytest.raises(ProfileValidationError) as exc_info:
        load_profile("profile_bad_agent_ref", core_model=test_core_model, sub_model=test_sub_model)

    assert any("ghost" in failure for failure in exc_info.value.failures)

from types import SimpleNamespace

from agents.reference import ReferenceAgent
from profiles.loader import validate_profile
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
    # like one ("low") is not accepted, since api/management.py,
    # protocols/editor.py, and orchestrator/main_agent.py all either crash
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
