"""Response Team (SEC) simulation deployment (docs/bar_improves.md Stage 5).

A simulation is simply another deployment of the same profile content, with its own
DB_PATH, API port, and BOT_TOKEN_ENV (a separate Telegram bot) -- no in-process
LIVE/Simulation scope, no runtime context object, no unit/membership/run-ID concept.
Reuses profiles/response_team.py's AGENTS/PROTOCOLS/EVENT_TYPES/AREAS/
EVENT_TYPE_REQUIRED_FIELDS/EVENT_TYPE_DESCRIPTIONS content unchanged and overrides only
deployment values --
confirmed to load and validate cleanly through profiles.loader.load_profile, including
its own, independent profile_file_hash (computed from *this* file's own bytes, per
deployment, never compared across profile modules) -- see
tests/test_operational_profiles.py's simulation tests.

A real person joins this simulation by messaging the simulation bot; their live
registration under profiles/response_team.py is untouched, because this deployment has
its own `users` table in its own database file. Participants are provisioned here with
`cli/user_admin` against *this* profile module -- never against the live one -- per
docs/operator_guide.md's "Running a simulation deployment" section.
"""

from pathlib import Path

from profiles.response_team import (
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
    OPTIMIZATION_POLICY,
    PROTOCOLS,
    RETRY_COUNT,
    RISK_THRESHOLD,
    TIMEZONE,
)

PROFILE_NAME = "Response Team (Simulation)"
DEFAULT_LANGUAGE = "he"

_PROFILE_DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "response_team_sim"
_PROFILE_DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = str(_PROFILE_DATA_DIR / "response_team_sim_history.db")
RESETTABLE_DATABASES = (DB_PATH,)

API_PORT = 8917

BOT_TOKEN_ENV = "RESPONSE_TEAM_SIM_BOT_TOKEN"
