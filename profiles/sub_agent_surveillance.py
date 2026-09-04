"""Deployment profile for visual surveillance, camera feeds, and aerial drone operations."""

import tempfile
from pathlib import Path

from agents import SurveillanceAgent
from profiles.contracts import AgentSpec, OptimizationPolicy
from protocols import CriticalityLevel, Protocol

PROFILE_NAME = "sub agent surveillance"
DEFAULT_LANGUAGE = "he"
MAX_ITER = 8
MODEL_TIMEOUT_SECONDS = 30

_PROFILE_DATA_DIR = Path(tempfile.gettempdir()) / "agentshub_sub_agent_surveillance"
_PROFILE_DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = str(_PROFILE_DATA_DIR / "operational_history.db")
SURVEILLANCE_DB_PATH = str(_PROFILE_DATA_DIR / "surveillance.db")

import os

BOT_TOKEN_ENV = "SURVEILLANCE_BOT_TOKEN" if os.environ.get("SURVEILLANCE_BOT_TOKEN") else "BOT_TOKEN"
SURVEILLANCE_CHAT_ID_ENV = "SURVEILLANCE_CHAT_ID" if os.environ.get("SURVEILLANCE_CHAT_ID") else "TEAM_STATUS_CHAT_ID"


class SubAgentSurveillanceAgent(SurveillanceAgent):
    """Binds the reusable specialist to this profile's isolated surveillance DB."""

    surveillance_db_path = SURVEILLANCE_DB_PATH


AGENTS = [
    AgentSpec(cls=SubAgentSurveillanceAgent, tier="sub"),
]

PROTOCOLS = [
    Protocol(
        name="query_surveillance_status",
        description=(
            "Applies when a commander or operator asks for visual surveillance intelligence, what security cameras see, "
            "drone fleet availability, or active drone mission status in any sector."
        ),
        participating_agents=("surveillance_agent",),
        approved_tools=("get_camera_feeds", "get_drone_fleet_status", "get_active_missions", "get_surveillance_overview"),
        expected_success_output=(
            "A clear visual situation report detailing camera feed observations, drone readiness, or active airborne missions."
        ),
        criticality=CriticalityLevel.LOW,
        approval_flag=False,
    ),
    Protocol(
        name="dispatch_drone_to_incident",
        description=(
            "Applies when a commander requests tactical drone dispatch or aerial recon to a specific incident area or target location."
        ),
        participating_agents=("surveillance_agent",),
        approved_tools=("dispatch_drone_to_area", "get_drone_fleet_status", "get_active_missions"),
        expected_success_output=(
            "Confirmation of drone dispatch with assigned drone callsign, target area, estimated arrival time (ETA), and mission ID."
        ),
        criticality=CriticalityLevel.MEDIUM,
        approval_flag=False,
    ),
    Protocol(
        name="surveillance_area_scan",
        description=(
            "Applies when a comprehensive sector scan is requested combining camera feeds and dispatching or positioning a drone."
        ),
        participating_agents=("surveillance_agent",),
        approved_tools=("get_surveillance_overview", "dispatch_drone_to_area"),
        expected_success_output=(
            "A full tactical visual overview of the sector including camera feeds, drone positioning, and recon status."
        ),
        criticality=CriticalityLevel.MEDIUM,
        approval_flag=False,
    ),
]

EVENT_TYPES = ["surveillance_report", "drone_dispatch"]
AREAS = ["north_gate", "south_sector", "east_fence", "west_hill", "central_hub"]

API_PORT = 8904
RETRY_COUNT = 3
RISK_THRESHOLD = 0.6
LOOKBACK_WINDOW_DAYS = 30
TIMEZONE = "Asia/Jerusalem"
CONVERSATION_HISTORY_TURNS = 6
CONVERSATION_HISTORY_TTL_HOURS = 24
OPTIMIZATION_POLICY = OptimizationPolicy()

MODEL_CREDENTIAL_ENVS = []
