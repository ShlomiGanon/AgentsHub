"""Base configuration (work_plan.md §1.3).

Holds only values true of every deployment. Today that is the model each
of the three core agents runs on. Deployment-specific values — ports,
database paths, agent rosters, protocols — belong to a profile, never here.

Loaded before any profile, since the three core agents are constructed
regardless of which profile was named.

`DEBUG_FLAG` gates verbose diagnostic logging (docs/server_report.md
Finding 1's follow-up): the full model prompt/response at every agent
call, plus internal detail (successful tool calls, precedent-search
window internals, queue/scheduler state transitions) that's noise during
normal operation but useful when diagnosing a live run — most valuably
the first time this runs against a real model rather than a mock, when a
parse failure or an unexpected response shape is the likeliest problem.
It is read from the `DEBUG_VERBOSE_LOGGING` environment variable — not a
profile-declared value, since it's a diagnostic switch for the process,
not a deployment identity value — parsed once, here, at module import
time (not on every log call), the same "read once at startup" pattern
`profiles.loader` already uses for the environment variables a profile
names (`os.environ.get`, no `.env`-file loader anywhere in this codebase
to match — this follows that exact mechanism rather than introducing a
second one). Absent or unset means off; only `"1"`/`"true"` (any case)
turn it on — a bare non-empty string like `"false"` or `"0"` must not be
mistaken for "set". This is unrelated to §1.6's profile validation, which
only inspects a `LoadedProfile`'s own declared environment variables
(`BOT_TOKEN_ENV`, `MODEL_CREDENTIAL_ENVS`) — `DEBUG_VERBOSE_LOGGING` is
never profile-declared and is never a *required* variable, so a missing
value is never a validation failure, only the normal, expected default.

Its output can include the full original event/message text verbatim —
treat it as sensitive, and never leave it on in normal operation. See
`docs/operator_guide.md`'s "Reading the run logs" section.
"""

import os
from dataclasses import dataclass


def _parse_debug_flag(raw: str | None) -> bool:
    """Strict, not "any non-empty string is truthy": only these exact
    (case-insensitive) values turn the flag on. Everything else — unset,
    empty, "false", "0", "no", or any other text — is off.
    """

    return (raw or "").strip().lower() in ("1", "true")


DEBUG_FLAG = _parse_debug_flag(os.environ.get("DEBUG_VERBOSE_LOGGING"))


@dataclass(frozen=True)
class BaseConfig:
    main_agent_model: str
    history_agent_model: str
    insights_agent_model: str
    DEBUG_FLAG: bool = False


# Placeholder model identifiers. Finalized when model routing (§3.6)
# selects a real model client library; named separately per agent because
# the Main Agent justifies a strong model while the History Agent
# summarizes large volumes of text and can use a cheaper one.
_MAIN_AGENT_MODEL = "gpt-4o"
_HISTORY_AGENT_MODEL = "gpt-4o-mini"
_INSIGHTS_AGENT_MODEL = "gpt-4o-mini"


def load_base_config() -> BaseConfig:
    """Return the base configuration.

    A function rather than a bare module-level constant so later work
    (e.g. reading overrides from the environment) has one place to change.
    """

    return BaseConfig(
        main_agent_model=_MAIN_AGENT_MODEL,
        history_agent_model=_HISTORY_AGENT_MODEL,
        insights_agent_model=_INSIGHTS_AGENT_MODEL,
        DEBUG_FLAG=DEBUG_FLAG,
    )
