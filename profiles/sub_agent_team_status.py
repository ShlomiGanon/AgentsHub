"""Deployment profile for readiness-team attendance and availability."""

import tempfile
from pathlib import Path

from agents import TeamStatusAgent
from profiles.contracts import AgentSpec, OptimizationPolicy
from protocols import CriticalityLevel, Protocol

PROFILE_NAME = "sub agent team status"
DEFAULT_LANGUAGE = "he"
MAX_ITER = 8
MODEL_TIMEOUT_SECONDS = 30

_PROFILE_DATA_DIR = Path(tempfile.gettempdir()) / "agentshub_sub_agent_team_status"
_PROFILE_DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = str(_PROFILE_DATA_DIR / "operational_history.db")
TEAM_STATUS_DB_PATH = str(_PROFILE_DATA_DIR / "team_status.db")

BOT_TOKEN_ENV = "TEAM_STATUS_BOT_TOKEN"
TEAM_STATUS_CHAT_ID_ENV = "TEAM_STATUS_CHAT_ID"


class SubAgentTeamStatusAgent(TeamStatusAgent):
    """Binds the reusable specialist to this profile's isolated status DB."""

    status_db_path = TEAM_STATUS_DB_PATH
    timezone_name = "Asia/Jerusalem"
    attendance_check_hour = 8
    response_window_hours = 1


AGENTS = [
    AgentSpec(cls=SubAgentTeamStatusAgent, tier="sub"),
]

PROTOCOLS = [
    Protocol(
        name="report_team_availability",
        description=(
            "Applies when a commander asks for the current availability picture of the approved "
            "readiness team, including who is available, unavailable, or has not reported; does "
            "not apply to general personnel, operational dispatch, or historical incident questions."
        ),
        participating_agents=("team_status_agent",),
        approved_tools=("report_team_availability",),
        expected_success_output=(
            "A complete name-by-name readiness-team report with availability counts, unavailable "
            "reasons and return times, original responses, and members who have not reported."
        ),
        criticality=CriticalityLevel.LOW,
        approval_flag=False,
    ),
]

EVENT_TYPES = ["team_availability"]
AREAS = ["readiness_team"]

API_PORT = 8903
RETRY_COUNT = 3
RISK_THRESHOLD = 0.6
LOOKBACK_WINDOW_DAYS = 30
TIMEZONE = "Asia/Jerusalem"
CONVERSATION_HISTORY_TURNS = 6
CONVERSATION_HISTORY_TTL_HOURS = 24
OPTIMIZATION_POLICY = OptimizationPolicy()

MODEL_CREDENTIAL_ENVS = []
