"""Fill-in-the-blank deployment profile template."""

import tempfile
from pathlib import Path

from agents import Agent, ReferenceAgent
from profiles.contracts import AgentSpec
from protocols import CriticalityLevel, Protocol


class _ExampleCoreTierSpecialist(Agent):
    """Stand-in for a second, real agent you'd write yourself."""

    name = "example_core_tier_specialist"  # unique registry key
    role = "One or two sentences: what this agent is for and good at — written for the Main Agent to read, not a human."
    system_prompt = "This agent's instructions — what it should do, and how it should report back."


AGENTS = [
    AgentSpec(cls=ReferenceAgent, tier="sub"),
    AgentSpec(cls=_ExampleCoreTierSpecialist, tier="core"),
]

PROTOCOLS = [
    Protocol(
        name="example_unflagged_protocol",
        description="Applies when <condition>; does not apply when <condition>.",
        participating_agents=("reference_agent",),
        approved_tools=("check_status",),
        expected_success_output="What a successful run's output looks like, in one sentence.",
        criticality=CriticalityLevel.LOW,
        approval_flag=False,  # runs the moment it's selected, no human in the loop
    ),
    Protocol(
        name="example_flagged_protocol",
        description="Applies when <condition that warrants a human sign-off>.",
        participating_agents=("example_core_tier_specialist",),
        approved_tools=(),
        expected_success_output="What a successful run's output looks like, in one sentence.",
        criticality=CriticalityLevel.HIGH,
        approval_flag=True,  # a commander must approve via /Approve before this runs
    ),
]

EVENT_TYPES = ["example_type_a", "example_type_b"]
AREAS = ["example_area_a", "example_area_b"]

DB_PATH = str(Path(tempfile.gettempdir()) / "agentshub_reference_template.db")
API_PORT = 9999

RETRY_COUNT = 3
RISK_THRESHOLD = 0.6
LOOKBACK_WINDOW_DAYS = 30

BOT_TOKEN_ENV = "YOUR_BOT_TOKEN_ENV_VAR_NAME"

MODEL_CREDENTIAL_ENVS = []
