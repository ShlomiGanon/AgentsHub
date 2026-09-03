"""The Friendly Forces profile: dispatch-coordination logging for ambulance, police,
firefighter, and military response requests."""

from pathlib import Path

from agents.friendly_forces_agent import FriendlyForcesAgent
from profiles.contracts import AgentSpec, OptimizationPolicy
from protocols import CriticalityLevel, Protocol

PROFILE_NAME = "Friendly Forces"
DEFAULT_LANGUAGE = "en"
MAX_ITER = 8
MODEL_TIMEOUT_SECONDS = 30

AGENTS = [
    AgentSpec(cls=FriendlyForcesAgent, tier="sub"),
]

PROTOCOLS = [
    Protocol(
        name="dispatch_ambulance",
        description="Applies when a report requires recording a request to send ambulance/medical "
        "response units to a location; does not apply to a routine status check with no action "
        "required, and does not apply to any other response type (police, firefighter, or military) "
        "— 'dispatching an ambulance' means recording a medical response request only.",
        participating_agents=("friendly_forces_agent",),
        approved_tools=("dispatch_ambulance",),
        expected_success_output="Confirmation that an ambulance dispatch request was recorded for the location.",
        criticality=CriticalityLevel.HIGH,
        approval_flag=True,
    ),
    Protocol(
        name="dispatch_police",
        description="Applies when a report requires recording a request to send police response "
        "units to a location; does not apply to a routine status check with no action required, and "
        "does not apply to any other response type (ambulance, firefighter, or military) — "
        "'dispatching police' means recording a police response request only.",
        participating_agents=("friendly_forces_agent",),
        approved_tools=("dispatch_police",),
        expected_success_output="Confirmation that a police dispatch request was recorded for the location.",
        criticality=CriticalityLevel.HIGH,
        approval_flag=True,
    ),
    Protocol(
        name="dispatch_firefighters",
        description="Applies when a report requires recording a request to send firefighter response "
        "units to a location; does not apply to a routine status check with no action required, and "
        "does not apply to any other response type (ambulance, police, or military) — 'dispatching "
        "firefighters' means recording a firefighter response request only.",
        participating_agents=("friendly_forces_agent",),
        approved_tools=("dispatch_firefighters",),
        expected_success_output="Confirmation that a firefighter dispatch request was recorded for the location.",
        criticality=CriticalityLevel.HIGH,
        approval_flag=True,
    ),
    Protocol(
        name="dispatch_military",
        description="Applies when a report requires recording a request to send general military "
        "response units to a location; does not apply to a routine status check with no action "
        "required, and does not apply to any other response type (ambulance, police, or firefighter) "
        "— 'dispatching military forces' means recording a military response request only.",
        participating_agents=("friendly_forces_agent",),
        approved_tools=("dispatch_military",),
        expected_success_output="Confirmation that a military dispatch request was recorded for the location.",
        criticality=CriticalityLevel.HIGH,
        approval_flag=True,
    ),
]

EVENT_TYPES = ["fire", "medical", "crime", "military_threat"]
AREAS = ["north_sector", "south_sector"]

_FRIENDLY_FORCES_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_FRIENDLY_FORCES_DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = str(_FRIENDLY_FORCES_DATA_DIR / "friendly_forces_profile.db")

API_PORT = 8903
RETRY_COUNT = 3
RISK_THRESHOLD = 0.6
LOOKBACK_WINDOW_DAYS = 30
TIMEZONE = "UTC"
CONVERSATION_HISTORY_TURNS = 6
CONVERSATION_HISTORY_TTL_HOURS = 24
OPTIMIZATION_POLICY = OptimizationPolicy()

BOT_TOKEN_ENV = "BOT_TOKEN"
MODEL_CREDENTIAL_ENVS = []
