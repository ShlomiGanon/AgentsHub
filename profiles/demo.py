"""The demonstration profile (work_plan.md §4.7).

Constructs the reference agent exactly as a real profile would construct
a real one, and declares four protocols covering every property §4.7
requires in one set:

- `status_check` — read-only tool only, unflagged.
- `dispatch_response` — the side-effecting tool, flagged (needs approval).
- `minor_incident_review` / `routine_check` — a deliberate tie: their
  descriptions cover the same ground, so a selector choosing by
  description alone can't cleanly discriminate between them. Distinct
  criticality (`MEDIUM` vs `LOW`) makes "most critical" unambiguous;
  one is flagged and the other isn't, so both approval branches are
  reachable through the tie pair too.

Event types and areas reuse Mission 2's seed-dataset domain
("fire"/"medical", "north_sector"/"south_sector") rather than inventing a
new fictional vocabulary.
"""

import tempfile
from pathlib import Path

from agents.reference import ReferenceAgent
from profiles.spec import AgentSpec
from protocols.model import CriticalityLevel, Protocol

# Each agent picks a tier, not a model — see docs/profile_spec.md's "Model
# tiers" section. AgentSpec only *declares* which class and which tier;
# profiles.loader.load_profile is the one place that actually resolves a
# tier and constructs the agent, at load time — never here, at import
# time, and never against os.environ directly (a profile module has no
# way to receive parameters from whoever imports it, so it must never be
# the one making that decision). This profile's one specialist agent uses
# the cheaper "sub" tier; the three core agents (Main, History, Insights)
# always use "core", entirely separately from this list.
AGENTS = [
    AgentSpec(cls=ReferenceAgent, tier="sub"),
]

PROTOCOLS = [
    Protocol(
        name="status_check",
        description="Applies when a commander or sensor needs to confirm current conditions at a "
        "location, with no action beyond checking; does not apply when an action must be taken.",
        participating_agents=("reference_agent",),
        approved_tools=("check_status",),
        expected_success_output="A status report describing current conditions at the requested location.",
        criticality=CriticalityLevel.LOW,
        approval_flag=False,
    ),
    Protocol(
        name="dispatch_response",
        description="Applies when a report requires dispatching a response and recording that action "
        "was taken at the location; does not apply to a routine status check with no action required.",
        participating_agents=("reference_agent",),
        approved_tools=("check_status", "record_action"),
        expected_success_output="Confirmation that a response was dispatched and recorded at the location.",
        criticality=CriticalityLevel.HIGH,
        approval_flag=True,
    ),
    Protocol(
        name="minor_incident_review",
        description="Applies to a minor incident report needing a quick status confirmation at the "
        "scene; does not apply when the report describes an ongoing or escalating situation.",
        participating_agents=("reference_agent",),
        approved_tools=("check_status",),
        expected_success_output="A status confirmation for the minor incident location.",
        criticality=CriticalityLevel.MEDIUM,
        approval_flag=False,
    ),
    Protocol(
        name="routine_check",
        description="Applies to a minor incident report needing a quick status check at the scene; "
        "does not apply when the report describes an ongoing or escalating situation.",
        participating_agents=("reference_agent",),
        approved_tools=("check_status",),
        expected_success_output="A status confirmation for the minor incident location.",
        criticality=CriticalityLevel.LOW,
        approval_flag=True,
    ),
]

EVENT_TYPES = ["fire", "medical"]
AREAS = ["north_sector", "south_sector"]

# Under the OS temp directory, not the repo — same pattern as
# fixtures/profiles/minimal_profile.py, so running tests never leaves a
# database file behind in version control.
_DEMO_DATA_DIR = Path(tempfile.gettempdir()) / "agentshub_fixtures"
_DEMO_DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = str(_DEMO_DATA_DIR / "demo_profile.db")

API_PORT = 8902
RETRY_COUNT = 3
RISK_THRESHOLD = 0.6
LOOKBACK_WINDOW_DAYS = 30

BOT_TOKEN_ENV = "BOT_TOKEN"
# Still a required profile attribute (docs/profile_spec.md), and the
# mechanism itself is unchanged — but empty here, deliberately: every
# agent this profile constructs (the one ReferenceAgent above, plus the
# three core agents every deployment gets) now resolves its API key
# through the "core"/"sub" tier system's own *_MODEL_API_KEY_ENV
# indirection instead, so there is no separate model credential left for
# this list to name. Kept for a profile that adds an agent constructed
# the old way (a bare model string, no tier, no explicit key) — that
# agent would need its provider's credential var listed here instead.
MODEL_CREDENTIAL_ENVS = []
