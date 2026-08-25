"""A minimal valid profile module, used by tests for §1.5/§1.6.

Satisfies docs/profile_spec.md in full. `AGENTS` declares one real agent
(`agents.reference.ReferenceAgent`, on the "sub" tier) via
`profiles.spec.AgentSpec` — every `AGENTS` entry must be a real,
constructible agent class now (profiles.loader.load_profile builds it at
load time), not a duck-typed stand-in; `ReferenceAgent` already exposes
the `check_status` tool this fixture's one protocol needs, so it doubles
as the simplest real agent to use here. `_FixtureProtocol` stays a
minimal, duck-typed stand-in for `PROTOCOLS` — protocols/spec's
structural contract hasn't changed and doesn't need a real
`protocols.model.Protocol`. `criticality` is the one field this contract
requires to be a real `CriticalityLevel` enum member specifically (§1.6,
tightened after the Mission 8 coverage audit found two consumers crash
and one silently miscompares on a plain string) — every other field on
`_FixtureProtocol` stays a plain, minimal stand-in.

The two named environment variables (BOT_TOKEN_ENV, MODEL_CREDENTIAL_ENVS)
must be set before this module is loaded through profiles.loader — tests
set them (e.g. via monkeypatch.setenv) since a profile module never holds
secret values itself.
"""

import tempfile
from dataclasses import dataclass
from pathlib import Path

from agents.reference import ReferenceAgent
from profiles.spec import AgentSpec
from protocols.model import CriticalityLevel


@dataclass(frozen=True)
class _FixtureProtocol:
    name: str
    description: str
    participating_agents: tuple[str, ...]
    approved_tools: tuple[str, ...]
    expected_success_output: str
    criticality: CriticalityLevel
    approval_flag: bool


AGENTS = [
    AgentSpec(cls=ReferenceAgent, tier="sub"),
]

PROTOCOLS = [
    _FixtureProtocol(
        name="basic_response",
        description="Applies to any minor, low-risk event needing only a status check; "
        "does not apply to anything requiring a side-effecting action.",
        participating_agents=("reference_agent",),
        approved_tools=("check_status",),
        expected_success_output="A status report for the requested location.",
        criticality=CriticalityLevel.LOW,
        approval_flag=False,
    ),
]

EVENT_TYPES = ["fire", "medical"]
AREAS = ["north_sector", "south_sector"]

# Under the OS temp directory rather than the repo, so running the test
# suite never leaves a database file behind in version control.
_FIXTURE_DATA_DIR = Path(tempfile.gettempdir()) / "agentshub_fixtures"
_FIXTURE_DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = str(_FIXTURE_DATA_DIR / "minimal_profile.db")

API_PORT = 8901
RETRY_COUNT = 3
RISK_THRESHOLD = 0.5
LOOKBACK_WINDOW_DAYS = 30

BOT_TOKEN_ENV = "AGENTSHUB_FIXTURE_BOT_TOKEN"
MODEL_CREDENTIAL_ENVS = ["AGENTSHUB_FIXTURE_MODEL_KEY"]
