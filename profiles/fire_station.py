"""Fire and Rescue (FIRE) deployment profile (docs/bar_improves.md Stage 4b).

A Fire and Rescue station: structure fires, hazmat fires, rescues (a person trapped or in
danger without fire), mutual-aid dispatch, and attendance. One deployment = this one
profile module = one database file = one API port = one Telegram bot -- the isolation
mechanism this codebase already has, reused as-is.

All profile content is written in English, per the hard constraint that no Hebrew literal
may live outside messages/en.py / messages/he.py. `DEFAULT_LANGUAGE = "he"` only selects
which of those two existing, already-validated catalogs supplies this deployment's fixed
API/Telegram/CLI text -- it does not change the language any of this file's own text is
written in.
"""

from pathlib import Path

from agents import DispatchAgent, HazmatAgent, RosterAgent
from profiles.contracts import AgentSpec, OptimizationPolicy
from protocols import CriticalityLevel, Protocol

PROFILE_NAME = "Fire and Rescue Station"
DEFAULT_LANGUAGE = "he"
MAX_ITER = 6
MODEL_TIMEOUT_SECONDS = 45

_PROFILE_DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "fire_station"
_PROFILE_DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = str(_PROFILE_DATA_DIR / "fire_station_history.db")
RESETTABLE_DATABASES = (DB_PATH,)

API_PORT = 8908

BOT_TOKEN_ENV = "FIRE_STATION_BOT_TOKEN"
MODEL_CREDENTIAL_ENVS: list[str] = []

RETRY_COUNT = 2
RISK_THRESHOLD = 0.6
LOOKBACK_WINDOW_DAYS = 30
TIMEZONE = "Asia/Jerusalem"
CONVERSATION_HISTORY_TURNS = 6
CONVERSATION_HISTORY_TTL_HOURS = 24
OPTIMIZATION_POLICY = OptimizationPolicy()


# Named mutual-aid resources this deployment recognizes. These names are identifiers
# only -- no capability is invented for them; the mutual-aid tool accepts only a name
# from this tuple and returns a refusal text for anything else (docs/bar_improves.md
# Stage 4b). Declared here, at profile level, and bound onto the shared DispatchAgent
# class below via a profile-specific subclass -- the same pattern
# profiles/standby_squad.py already uses to bind e.g. `surveillance_db_path` onto a
# shared agent class, since the reusable agents/fire_station_agents.py module must not
# hardcode any profile-specific resource name itself.
MUTUAL_AID_RESOURCES = ("ASHED", "CARMEL")


class FireStationDispatchAgent(DispatchAgent):
    """Binds this deployment's own recognized mutual-aid resource names."""

    MUTUAL_AID_RESOURCES = MUTUAL_AID_RESOURCES


AGENTS = [
    AgentSpec(cls=FireStationDispatchAgent, tier="sub"),
    AgentSpec(cls=HazmatAgent, tier="sub"),
    AgentSpec(cls=RosterAgent, tier="sub"),
]


PROTOCOLS = [
    Protocol(
        name="structure_fire_response",
        description=(
            "Applies to a fire in a building or structure with no hazardous-materials, gas, "
            "chemical, or industrial-facility involvement -- use hazmat_response instead when "
            "any of those are involved; the response is to dispatch the station's own crew."
        ),
        participating_agents=("dispatch_agent",),
        approved_tools=("dispatch_station_crew",),
        expected_success_output="Confirmation that the station crew dispatch request was recorded for the area.",
        criticality=CriticalityLevel.HIGH,
        approval_flag=False,
    ),
    Protocol(
        name="hazmat_response",
        description=(
            "Applies to a fire involving hazardous materials, gas, chemicals, or an industrial "
            "facility -- does not apply to an ordinary structure fire with none of those "
            "involved (use structure_fire_response for that); the response is to dispatch the "
            "station's own crew and request a hazmat assessment."
        ),
        participating_agents=("dispatch_agent", "hazmat_agent"),
        approved_tools=("dispatch_station_crew", "request_hazmat_assessment"),
        expected_success_output=(
            "Confirmation that the station crew dispatch request and the hazmat assessment "
            "request were both recorded for the area."
        ),
        criticality=CriticalityLevel.HIGH,
        approval_flag=True,
    ),
    Protocol(
        name="mutual_aid_request",
        description=(
            "Applies when a named mutual-aid resource needs to be requested for an ongoing "
            "incident -- does not apply to an initial report with no dispatch decision yet (use "
            "structure_fire_response, hazmat_response, or rescue_response first for that); the "
            "response is to record the mutual-aid request for the named resource."
        ),
        participating_agents=("dispatch_agent",),
        approved_tools=("request_mutual_aid",),
        expected_success_output="Confirmation that the mutual-aid request was recorded for the named resource.",
        criticality=CriticalityLevel.HIGH,
        approval_flag=True,
    ),
    Protocol(
        name="rescue_response",
        description=(
            "Applies to a person trapped or in danger with no fire involved -- does not apply "
            "once fire is also present (use structure_fire_response or hazmat_response for "
            "that, as appropriate); the response is to dispatch the station's own crew."
        ),
        participating_agents=("dispatch_agent",),
        approved_tools=("dispatch_station_crew",),
        expected_success_output="Confirmation that the station crew dispatch request was recorded for the area.",
        criticality=CriticalityLevel.MEDIUM,
        approval_flag=False,
    ),
    Protocol(
        name="attendance_update",
        description=(
            "Applies when a crew member reports being unavailable, with or without a stated "
            "reason or time interval; the response is to record exactly what was reported. "
            "Never guesses a missing interval."
        ),
        participating_agents=("roster_agent",),
        approved_tools=("record_availability",),
        expected_success_output="Confirmation that the member's availability report was recorded.",
        criticality=CriticalityLevel.LOW,
        approval_flag=False,
    ),
]


# Event types. As in profiles/response_team.py, what belongs in each type and what does
# not is declared below in EVENT_TYPE_DESCRIPTIONS, fed into the extraction prompt next
# to each type's own name (docs/bar_improves.md's follow-up to Stage 4).
EVENT_TYPES = [
    "structure_fire",
    "hazmat_fire",
    "rescue",
    "attendance",
]

EVENT_TYPE_DESCRIPTIONS = {
    "structure_fire": (
        "A fire in a building or structure, with no hazardous-materials/gas/chemical/"
        "industrial-facility involvement."
    ),
    "hazmat_fire": "A fire involving hazardous materials, gas, chemicals, or an industrial facility.",
    "rescue": "A person trapped or in danger, without fire.",
    "attendance": (
        "A crew member reporting their own unavailability, with or without a stated reason "
        "or interval."
    ),
}

EVENT_TYPE_REQUIRED_FIELDS = {
    "structure_fire": ("area",),
    "hazmat_fire": ("area",),
    "rescue": ("area",),
    "attendance": ("availability_start", "availability_end"),
}

AREAS = [
    "district_north",
    "district_south",
    "district_center",
    "industrial_zone",
]
