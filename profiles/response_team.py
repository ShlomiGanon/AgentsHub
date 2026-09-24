"""Response Team (SEC) deployment profile (docs/bar_improves.md Stage 4a).

A Security / Standby Response Team: perimeter and external-force observation, camera
surveillance-fault handling, team movement/status reporting, and attendance. One
deployment = this one profile module = one database file = one API port = one Telegram
bot — the isolation mechanism this codebase already has, reused as-is rather than
building any new unit/tenant/scope concept (see docs/bar_improves.md's own framing).

All profile content (agent names/roles/prompts, protocol descriptions, area/event-type
names) is written in English, per the hard constraint that no Hebrew literal may live
outside messages/en.py / messages/he.py. `DEFAULT_LANGUAGE = "he"` only selects which of
those two existing, already-validated catalogs supplies this deployment's fixed
API/Telegram/CLI text (queued/held/approval boilerplate, etc.) — it does not change the
language any of this file's own text is written in.
"""

from pathlib import Path

from agents import RosterAgent, SecurityOpsAgent, SurveillanceFaultAgent
from profiles.contracts import AgentSpec, OptimizationPolicy
from protocols import CriticalityLevel, Protocol

PROFILE_NAME = "Response Team"
DEFAULT_LANGUAGE = "he"
MAX_ITER = 6
MODEL_TIMEOUT_SECONDS = 45

_PROFILE_DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "response_team"
_PROFILE_DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = str(_PROFILE_DATA_DIR / "response_team_history.db")
RESETTABLE_DATABASES = (DB_PATH,)

API_PORT = 8907

BOT_TOKEN_ENV = "RESPONSE_TEAM_BOT_TOKEN"
MODEL_CREDENTIAL_ENVS: list[str] = []

RETRY_COUNT = 2
RISK_THRESHOLD = 0.6
LOOKBACK_WINDOW_DAYS = 30
TIMEZONE = "Asia/Jerusalem"
CONVERSATION_HISTORY_TURNS = 6
CONVERSATION_HISTORY_TTL_HOURS = 24
OPTIMIZATION_POLICY = OptimizationPolicy()


AGENTS = [
    AgentSpec(cls=SecurityOpsAgent, tier="sub"),
    AgentSpec(cls=SurveillanceFaultAgent, tier="sub"),
    AgentSpec(cls=RosterAgent, tier="sub"),
]


PROTOCOLS = [
    Protocol(
        name="perimeter_check",
        description=(
            "Applies to a routine, unconfirmed observation at or near the perimeter or a gate "
            "(e.g. unusual vehicles, heavy equipment, people, or activity) that only needs to be "
            "logged and the team told about it -- does not apply once a vehicle or force not "
            "belonging to the team has actually been identified (use external_force_response for "
            "that), and does not apply to a camera/sensor equipment fault (use "
            "surveillance_fault_response for that)."
        ),
        participating_agents=("security_ops_agent",),
        approved_tools=("log_observation", "notify_team"),
        expected_success_output="Confirmation that the observation was logged and the team was notified.",
        criticality=CriticalityLevel.MEDIUM,
        approval_flag=False,
    ),
    Protocol(
        name="external_force_response",
        description=(
            "Applies when a vehicle or force not belonging to the team has been observed in or "
            "near the site -- an identified external/unknown force, not merely an ambiguous "
            "observation still being assessed (use perimeter_check for that); the response is to "
            "log it and request a friendly-force dispatch to the area."
        ),
        participating_agents=("security_ops_agent",),
        approved_tools=("log_observation", "request_friendly_force_dispatch"),
        expected_success_output=(
            "Confirmation that the observation was logged and a friendly-force dispatch request "
            "was recorded for the area."
        ),
        criticality=CriticalityLevel.HIGH,
        approval_flag=True,
    ),
    Protocol(
        name="surveillance_fault_response",
        description=(
            "Applies when a camera is reported offline, degraded, or physically damaged -- "
            "including a physically cut camera communications cable -- and does not apply to a "
            "report about an external force or a perimeter observation with no equipment fault "
            "involved; the response is to log the fault for each reported camera identifier and "
            "request a technician."
        ),
        participating_agents=("surveillance_agent",),
        approved_tools=("log_camera_fault", "request_technician"),
        expected_success_output="Confirmation that the camera fault was logged and a technician was requested.",
        criticality=CriticalityLevel.MEDIUM,
        approval_flag=False,
    ),
    Protocol(
        name="team_movement_log",
        description=(
            "Applies when a team member reports their own movement or current position -- e.g. "
            "travelling to or arriving at a sector -- and does not apply to a member reporting "
            "that they will be unavailable (use attendance_update for that)."
        ),
        participating_agents=("security_ops_agent",),
        approved_tools=("log_observation",),
        expected_success_output="Confirmation that the team member's movement/position was logged.",
        criticality=CriticalityLevel.LOW,
        approval_flag=False,
    ),
    Protocol(
        name="attendance_update",
        description=(
            "Applies when a team member reports being unavailable, with or without a stated "
            "reason or time interval -- does not apply to a member reporting their current "
            "location while still on duty (use team_movement_log for that); the response is to "
            "record exactly what was reported. Never guesses a missing interval."
        ),
        participating_agents=("roster_agent",),
        approved_tools=("record_availability",),
        expected_success_output="Confirmation that the member's availability report was recorded.",
        criticality=CriticalityLevel.LOW,
        approval_flag=False,
    ),
    Protocol(
        name="drone_recall",
        description=(
            "Applies when a commander needs the drone recalled to base -- does not apply to a "
            "request to dispatch or task the drone (no dispatch tool exists in this profile)."
        ),
        participating_agents=("security_ops_agent",),
        approved_tools=("recall_drone",),
        expected_success_output="Confirmation that the drone recall request was recorded.",
        criticality=CriticalityLevel.MEDIUM,
        approval_flag=True,
    ),
]


# Event types. What belongs in each type and what does not is declared below in
# EVENT_TYPE_DESCRIPTIONS -- an optional profile attribute (docs/bar_improves.md's
# follow-up to Stage 4) that is fed into the extraction prompt next to each type's own
# name, closing the gap Stage 4 originally found and reported: EVENT_TYPES on its own is
# a plain list[str] (docs/profile_spec.md) with no description channel of its own.
EVENT_TYPES = [
    "perimeter_observation",
    "external_force_observation",
    "surveillance_fault",
    "team_status",
    "attendance",
]

EVENT_TYPE_DESCRIPTIONS = {
    "perimeter_observation": (
        "Unusual vehicles, heavy equipment, people, or activity at or near the perimeter or a "
        "gate (e.g. \"heavy equipment parked near the western gate\"). Not a confirmed external "
        "force (use external_force_observation for that) and not an equipment fault (use "
        "surveillance_fault for that)."
    ),
    "external_force_observation": "A vehicle or force not belonging to the team, observed in or near the site.",
    "surveillance_fault": (
        "A camera offline, degraded, or physically damaged, including a physically cut camera "
        "communications cable. Camera identifiers go into entities. The physical observation "
        "(what was seen, e.g. \"cable physically cut, camera offline\") belongs in description; "
        "any stated cause or suspicion from the reporter is the reporter's own claim, never "
        "recorded as fact."
    ),
    "team_status": "A team member's own movement or position (e.g. \"Gil: on my way to sector B\").",
    "attendance": (
        "A team member reporting their own unavailability, with or without a stated reason or "
        "interval."
    ),
}

EVENT_TYPE_REQUIRED_FIELDS = {
    "perimeter_observation": ("area",),
    "external_force_observation": ("area",),
    # `entities` is part of the fixed event-data field vocabulary
    # (protocols.EVENT_DATA_FIELDS), so it is required here too, per
    # docs/bar_improves.md Stage 4a's own instruction.
    "surveillance_fault": ("area", "entities"),
    "team_status": ("area",),
    "attendance": ("availability_start", "availability_end"),
}

AREAS = [
    "west_gate",
    "east_gate",
    "sector_a",
    "sector_b",
    "sector_c",
    "perimeter_fence",
    "control_room",
]
