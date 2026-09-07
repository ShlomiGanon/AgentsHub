"""9.3 — Test profile loading and validation (work_plan.md §9.3).

Most of this subtask's bullets are already thoroughly covered at the unit
level — `tests/test_profile_loading.py` (missing env vars, hash tracking)
and `tests/test_profile_validation.py` (unconstructed agent, unapproved
tool, unset approval flag, one failure per bad protocol). This file adds
the two bullets that aren't covered anywhere else: an exhaustive "loads
exactly this and nothing else" check, and the two-profiles-different-model
routing check, which is a profile-level integration concern distinct from
`tests/test_agent_adapter.py`'s own adapter-level model-routing test.
"""

import types

from profiles.loader import load_profile
from profiles.spec import HUMAN_ACTIVATION_TYPE
from tests.helpers import write_profile_module


def test_a_valid_profile_loads_exactly_its_agents_protocols_event_types_and_areas(monkeypatch, test_core_model, test_sub_model):
    monkeypatch.setenv("AGENTSHUB_FIXTURE_BOT_TOKEN", "token-value")
    monkeypatch.setenv("AGENTSHUB_FIXTURE_MODEL_KEY", "key-value")

    loaded = load_profile("fixtures.profiles.minimal_profile", core_model=test_core_model, sub_model=test_sub_model)

    assert [a.name for a in loaded.agents] == ["reference_agent"]
    assert [p.name for p in loaded.protocols] == ["basic_response"]
    assert set(loaded.event_types) == {"fire", "medical", HUMAN_ACTIVATION_TYPE}
    assert set(loaded.areas) == {"north_sector", "south_sector"}
    # Nothing extra snuck in alongside the human-activation append.
    assert len(loaded.event_types) == 3
    assert len(loaded.areas) == 2
    # The core agents §1.5 says load on every run regardless of profile.
    assert "history_agent" in loaded.core_agents


def test_two_profiles_differing_only_in_model_route_each_to_its_own(tmp_path, monkeypatch, test_core_model):
    from agents import adapter
    from config.base import build_tier_model

    captured_models = []

    class _FakeOutput:
        def __init__(self, raw):
            self.raw = raw

    class _FakeLLM:
        def __init__(self, **kwargs):
            self.model = kwargs.get("model")

    class _FakeCrewAgent:
        def __init__(self, **kwargs):
            captured_models.append(kwargs.get("llm").model)

        def kickoff(self, text):
            return _FakeOutput("status nominal")

    fake_module = types.SimpleNamespace(Agent=_FakeCrewAgent, LLM=_FakeLLM, tools=types.SimpleNamespace(BaseTool=object))
    monkeypatch.setattr(adapter, "_get_crewai", lambda: fake_module)

    # Both profiles declare the identical spec (AgentSpec(cls=ReferenceAgent,
    # tier="sub")) — the whole point is that routing now happens through
    # which TierModel each *load* is given, not anything hardcoded in the
    # profile's own source, so the two profile files are identical on this
    # axis; only the two load_profile calls below differ.
    prelude = "from agents.reference import ReferenceAgent\nfrom profiles.spec import AgentSpec\n"

    monkeypatch.setenv("SIM_MODEL_TOKEN", "t")
    monkeypatch.setenv("SIM_MODEL_KEY", "k")

    write_profile_module(
        tmp_path, monkeypatch, "profile_model_a", bot_token_env="SIM_MODEL_TOKEN", model_cred_env="SIM_MODEL_KEY",
        extra_prelude=prelude,
        overrides={"AGENTS": 'AGENTS = [AgentSpec(cls=ReferenceAgent, tier="sub")]', "DB_PATH": 'DB_PATH = "profile_a.db"', "API_PORT": "API_PORT = 9101"},
    )
    write_profile_module(
        tmp_path, monkeypatch, "profile_model_b", bot_token_env="SIM_MODEL_TOKEN", model_cred_env="SIM_MODEL_KEY",
        extra_prelude=prelude,
        overrides={"AGENTS": 'AGENTS = [AgentSpec(cls=ReferenceAgent, tier="sub")]', "DB_PATH": 'DB_PATH = "profile_b.db"', "API_PORT": "API_PORT = 9102"},
    )

    sub_model_a = build_tier_model("openrouter", "model-a", "key-a")
    sub_model_b = build_tier_model("openrouter", "model-b", "key-b")

    loaded_a = load_profile("profile_model_a", core_model=test_core_model, sub_model=sub_model_a)
    loaded_b = load_profile("profile_model_b", core_model=test_core_model, sub_model=sub_model_b)

    loaded_a.agents[0].process("check status", [])
    loaded_b.agents[0].process("check status", [])

    assert captured_models == ["openrouter/model-a", "openrouter/model-b"]
