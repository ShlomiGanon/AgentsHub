"""Fire and Rescue (FIRE) simulation deployment (docs/bar_improves.md Stage 5).

Same rationale as profiles/response_team_sim.py: a simulation is simply another
deployment of profiles/fire_station.py's content, with its own DB_PATH, API port, and
BOT_TOKEN_ENV (a separate Telegram bot). No new infrastructure.
"""

from pathlib import Path

from profiles.fire_station import (
    AGENTS,
    AREAS,
    CONVERSATION_HISTORY_TTL_HOURS,
    CONVERSATION_HISTORY_TURNS,
    EVENT_TYPE_DESCRIPTIONS,
    EVENT_TYPE_REQUIRED_FIELDS,
    EVENT_TYPES,
    LOOKBACK_WINDOW_DAYS,
    MAX_ITER,
    MODEL_CREDENTIAL_ENVS,
    MODEL_TIMEOUT_SECONDS,
    MUTUAL_AID_RESOURCES,
    OPTIMIZATION_POLICY,
    PROTOCOLS,
    RETRY_COUNT,
    RISK_THRESHOLD,
    TIMEZONE,
)

PROFILE_NAME = "Fire and Rescue Station (Simulation)"
DEFAULT_LANGUAGE = "he"

_PROFILE_DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "fire_station_sim"
_PROFILE_DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = str(_PROFILE_DATA_DIR / "fire_station_sim_history.db")
RESETTABLE_DATABASES = (DB_PATH,)

API_PORT = 8918

BOT_TOKEN_ENV = "FIRE_STATION_SIM_BOT_TOKEN"
