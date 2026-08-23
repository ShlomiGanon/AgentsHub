"""A minimal valid profile module, used by tests for §1.5/§1.6.

Satisfies docs/profile_spec.md in full, including the structural
(duck-typed) agent/protocol contract — the real Agent/Protocol classes
from §3/§4 don't exist yet, so this fixture defines the smallest objects
that pass profiles.spec's shape checks.

The two named environment variables (BOT_TOKEN_ENV, MODEL_CREDENTIAL_ENVS)
must be set before this module is loaded through profiles.loader — tests
set them (e.g. via monkeypatch.setenv) since a profile module never holds
secret values itself.
"""

import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class _FixtureAgent:
    name: str
    tools: tuple[str, ...] = ()

    def exposed_tools(self) -> tuple[str, ...]:
        return self.tools


@dataclass(frozen=True)
class _FixtureProtocol:
    name: str
    description: str
    participating_agents: tuple[str, ...]
    approved_tools: tuple[str, ...]
    criticality: str
    approval_flag: bool


AGENTS = [
    _FixtureAgent(name="reference_agent", tools=("check_status",)),
]

PROTOCOLS = [
    _FixtureProtocol(
        name="basic_response",
        description="Applies to any minor, low-risk event needing only a status check; "
        "does not apply to anything requiring a side-effecting action.",
        participating_agents=("reference_agent",),
        approved_tools=("check_status",),
        criticality="low",
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
