"""Reference profile — a fill-in-the-blank TEMPLATE, not a real deployment.

Not loaded by anything in this codebase — no default-profile mechanism
exists (every profile is named explicitly, e.g. `python -m bot.app
<profile_module>`), so this file only ever loads if someone explicitly
points at `profiles.reference`. Copy this file, rename it, and replace
every placeholder value before using it for a real deployment.

Full field-by-field explanations: `docs/profile_spec.md` (what the loader
requires) and `docs/agent_authoring.md` (how to write a real agent).
`profiles/demo.py` is the other example to read alongside this one — a
complete, working profile, not a template.
"""

import tempfile
from pathlib import Path

from agents.base import Agent
from agents.reference import ReferenceAgent
from profiles.spec import AgentSpec
from protocols.model import CriticalityLevel, Protocol


class _ExampleCoreTierSpecialist(Agent):
    """Stand-in for a second, real agent you'd write yourself. A real one
    belongs in its own file under `agents/` (docs/agent_authoring.md) —
    inlined here only so this one file can show a "core"-tier agent
    without a name collision against ReferenceAgent below (every agent
    needs its own unique `name`).
    """

    name = "example_core_tier_specialist"  # unique registry key
    role = "One or two sentences: what this agent is for and good at — written for the Main Agent to read, not a human."
    system_prompt = "This agent's instructions — what it should do, and how it should report back."


# Every agent picks a tier ("core" or "sub"), not a model — see
# docs/profile_spec.md's "Model tiers" section. AgentSpec only *declares*
# which class and which tier; profiles.loader.load_profile resolves the
# tier and constructs the agent at load time, never here (a profile
# module can't receive parameters from whoever imports it, so it must
# never read os.environ or resolve a tier itself).
AGENTS = [
    # Typical case: a specialist agent on the cheaper "sub" tier.
    AgentSpec(cls=ReferenceAgent, tier="sub"),
    # A specialist that genuinely needs the stronger tier uses "core"
    # instead — same pattern, different tier.
    AgentSpec(cls=_ExampleCoreTierSpecialist, tier="core"),
]

PROTOCOLS = [
    Protocol(
        name="example_unflagged_protocol",
        # Written for the Main Agent to select by, not a human — describe
        # when this applies and, ideally, when it doesn't.
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

# What this deployment classifies events into, and where. "human_activation"
# is added automatically — don't declare it yourself.
EVENT_TYPES = ["example_type_a", "example_type_b"]
AREAS = ["example_area_a", "example_area_b"]

# No default for either — two profiles sharing one would collide the
# moment both ran at once. Pick a real path and a free port for a real
# deployment; these are placeholders.
DB_PATH = str(Path(tempfile.gettempdir()) / "agentshub_reference_template.db")
API_PORT = 9999

# Starting values only — the settings store owns them after first run
# (changeable live via `/settings set`, no restart needed).
RETRY_COUNT = 3
RISK_THRESHOLD = 0.6
LOOKBACK_WINDOW_DAYS = 30

# Name of the env var holding the real Telegram token — never the token
# itself (docs/operator_guide.md Step 2).
BOT_TOKEN_ENV = "YOUR_BOT_TOKEN_ENV_VAR_NAME"

# Names of env vars holding model credentials for any agent NOT
# constructed through the tier system above (the AgentSpec entries in
# AGENTS already cover both agents here, so this is empty — see
# docs/profile_spec.md's "Model tiers" section for when you'd need to
# list something here).
MODEL_CREDENTIAL_ENVS = []
