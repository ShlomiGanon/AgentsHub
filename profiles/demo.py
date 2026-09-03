"""The demonstration profile (work_plan.md §4.7)."""

from pathlib import Path

from agents import ReferenceAgent
from profiles.contracts import AgentSpec, OptimizationPolicy
from protocols import CriticalityLevel, Protocol

PROFILE_NAME = "For Tests"
DEFAULT_LANGUAGE = "en"
MAX_ITER = 8
MODEL_TIMEOUT_SECONDS = 30

AGENTS = [
    AgentSpec(cls=ReferenceAgent, tier="sub"),
]

PROTOCOLS = [
    Protocol(
        name="status_check",
        description="Applies when a commander or sensor needs to confirm current conditions at a "
        "location, with no action beyond checking; does not apply when an action must be taken, and "
        "does not apply to relaying a message from one person to another — there is no messenger "
        "capability here, only a status check.",
        participating_agents=("reference_agent",),
        approved_tools=("check_status",),
        expected_success_output="A status report describing current conditions at the requested location.",
        criticality=CriticalityLevel.LOW,
        approval_flag=False,
    ),
    Protocol(
        name="dispatch_response",
        description="Applies when a report requires dispatching a physical response to a location and "
        "recording that action was taken there; does not apply to a routine status check with no action "
        "required, and does not apply to relaying a message from one person to another — 'dispatching a "
        "response' means sending a response to a location, never passing along words between people.",
        participating_agents=("reference_agent",),
        approved_tools=("check_status", "record_action"),
        expected_success_output="Confirmation that a response was dispatched and recorded at the location.",
        criticality=CriticalityLevel.HIGH,
        approval_flag=True,
    ),
    Protocol(
        name="minor_incident_review",
        description="Applies to a minor incident report needing a quick status confirmation at the "
        "scene; does not apply when the report describes an ongoing or escalating situation, and does "
        "not apply to relaying a message between people — there is no messenger capability here.",
        participating_agents=("reference_agent",),
        approved_tools=("check_status",),
        expected_success_output="A status confirmation for the minor incident location.",
        criticality=CriticalityLevel.MEDIUM,
        approval_flag=False,
    ),
    Protocol(
        name="routine_check",
        description="Applies to a minor incident report needing a quick status check at the scene; "
        "does not apply when the report describes an ongoing or escalating situation, and does not "
        "apply to relaying a message between people — there is no messenger capability here.",
        participating_agents=("reference_agent",),
        approved_tools=("check_status",),
        expected_success_output="A status confirmation for the minor incident location.",
        criticality=CriticalityLevel.LOW,
        approval_flag=True,
    ),
]

EVENT_TYPES = ["fire", "medical"]
AREAS = ["north_sector", "south_sector"]

_DEMO_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_DEMO_DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = str(_DEMO_DATA_DIR / "demo_profile.db")

API_PORT = 8902
RETRY_COUNT = 3
RISK_THRESHOLD = 0.6
LOOKBACK_WINDOW_DAYS = 30
TIMEZONE = "UTC"
CONVERSATION_HISTORY_TURNS = 6
CONVERSATION_HISTORY_TTL_HOURS = 24
OPTIMIZATION_POLICY = OptimizationPolicy()

BOT_TOKEN_ENV = "BOT_TOKEN"
MODEL_CREDENTIAL_ENVS = []
