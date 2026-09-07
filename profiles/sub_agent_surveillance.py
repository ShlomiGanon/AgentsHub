"""Deployment profile for visual surveillance, camera feeds, and aerial drone operations."""

import tempfile
from pathlib import Path

from agents import SurveillanceAgent
from profiles.contracts import AgentSpec, OptimizationPolicy
from protocols import CriticalityLevel, Protocol

PROFILE_NAME = "sub agent surveillance"
DEFAULT_LANGUAGE = "he"
MAX_ITER = 2
MODEL_TIMEOUT_SECONDS = 30

_PROFILE_DATA_DIR = Path(tempfile.gettempdir()) / "agentshub_sub_agent_surveillance"
_PROFILE_DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = str(_PROFILE_DATA_DIR / "operational_history.db")
SURVEILLANCE_DB_PATH = str(_PROFILE_DATA_DIR / "surveillance.db")

BOT_TOKEN_ENV = "SURVEILLANCE_BOT_TOKEN"
SURVEILLANCE_CHAT_ID_ENV = "SURVEILLANCE_CHAT_ID"


class SubAgentSurveillanceAgent(SurveillanceAgent):
    """Binds the reusable specialist to this profile's isolated surveillance DB."""

    surveillance_db_path = SURVEILLANCE_DB_PATH


AGENTS = [
    AgentSpec(cls=SubAgentSurveillanceAgent, tier="sub"),
]

PROTOCOLS = [
    Protocol(
        name="query_camera_status",
        description=(
            "Applies only when a commander or operator asks what security cameras see or for camera status in a sector."
        ),
        participating_agents=("surveillance_agent",),
        approved_tools=("get_camera_feeds",),
        expected_success_output=(
            "A concise camera report restricted to the requested camera or sector."
        ),
        criticality=CriticalityLevel.LOW,
        approval_flag=False,
    ),
    Protocol(
        name="query_drone_fleet_status",
        description="Applies only when current drone availability, readiness, battery, or fleet status is requested.",
        participating_agents=("surveillance_agent",),
        approved_tools=("get_drone_fleet_status",),
        expected_success_output="A concise current drone fleet status report.",
        criticality=CriticalityLevel.LOW,
        approval_flag=False,
    ),
    Protocol(
        name="query_active_drone_missions",
        description="Applies only when current active drone missions or airborne assignments are requested.",
        participating_agents=("surveillance_agent",),
        approved_tools=("get_active_missions",),
        expected_success_output="A concise list of current active drone missions.",
        criticality=CriticalityLevel.LOW,
        approval_flag=False,
    ),
    Protocol(
        name="query_surveillance_overview",
        description="Applies when one combined overview of cameras, drone readiness, and active missions is explicitly requested.",
        participating_agents=("surveillance_agent",),
        approved_tools=("get_surveillance_overview",),
        expected_success_output="One combined tactical overview restricted to the requested sector.",
        criticality=CriticalityLevel.LOW,
        approval_flag=False,
    ),
    Protocol(
        name="dispatch_drone_to_incident",
        description=(
            "Applies when a commander requests tactical drone dispatch or aerial recon to a specific incident area or target location."
        ),
        participating_agents=("surveillance_agent",),
        approved_tools=("dispatch_drone_to_area",),
        expected_success_output=(
            "Confirmation of drone dispatch with assigned drone callsign, target area, estimated arrival time (ETA), and mission ID."
        ),
        criticality=CriticalityLevel.MEDIUM,
        approval_flag=True,
    ),
    Protocol(
        name="return_drone_to_base",
        description=(
            "Applies only when a commander asks to recall, return, bring back, or cancel an active drone mission. "
            "If multiple drones are active and none is identified, it lists them and asks which one to return without changing state."
        ),
        participating_agents=("surveillance_agent",),
        approved_tools=("return_drone_to_base",),
        expected_success_output=(
            "Confirmation of the single recalled drone, or an exact list of active drones requiring the commander to choose one."
        ),
        criticality=CriticalityLevel.MEDIUM,
        approval_flag=True,
    ),
    Protocol(
        name="surveillance_area_scan",
        description=(
            "Applies when a read-only comprehensive visual scan of a sector is requested. It never dispatches or repositions a drone."
        ),
        participating_agents=("surveillance_agent",),
        approved_tools=("get_surveillance_overview",),
        expected_success_output=(
            "A full tactical visual overview of the sector including camera feeds, drone positioning, and recon status."
        ),
        criticality=CriticalityLevel.MEDIUM,
        approval_flag=False,
    ),
    Protocol(
        name="update_camera_observation",
        description="Applies only when an operator explicitly requests saving a new observation for a specific camera.",
        participating_agents=("surveillance_agent",),
        approved_tools=("update_camera_observation",),
        expected_success_output="Confirmation of the exact camera observation update.",
        criticality=CriticalityLevel.MEDIUM,
        approval_flag=True,
    ),
]

EVENT_TYPES = ["surveillance_report", "drone_dispatch", "drone_recall"]
AREAS = ["north_gate", "south_sector", "east_fence", "west_hill", "central_hub"]

API_PORT = 8904
RETRY_COUNT = 1
RISK_THRESHOLD = 0.6
LOOKBACK_WINDOW_DAYS = 30
TIMEZONE = "Asia/Jerusalem"
CONVERSATION_HISTORY_TURNS = 6
CONVERSATION_HISTORY_TTL_HOURS = 24
OPTIMIZATION_POLICY = OptimizationPolicy()

MODEL_CREDENTIAL_ENVS = []
