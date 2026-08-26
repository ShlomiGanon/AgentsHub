"""Base configuration (work_plan.md §1.3).

Holds only values true of every deployment. Today that is the model the
three core agents run on. Deployment-specific values — ports, database
paths, agent rosters, protocols — belong to a profile, never here.

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
time (not on every log call). Absent or unset means off; only `"1"`/
`"true"` (any case) turn it on — a bare non-empty string like `"false"`
or `"0"` must not be mistaken for "set". This is a separate, unrelated
concern from everything below — never touched by the model-tier
machinery.

`LOG_CONSOLE_JSON_ENABLED` is the same shape of switch, for a different
concern: `tools.logging_config.configure_logging` normally attaches
*two* console handlers — the original JSON stream (stdout) and the
newer human-readable one (stderr, work_plan.md §1.8 follow-up) — which
interleave in a normal terminal. Read from `LOG_CONSOLE_JSON`, inverted
from `DEBUG_VERBOSE_LOGGING`'s convention on purpose: this one is *on*
by default (today's existing behavior, unchanged, for anything that
doesn't set it — including every existing test), and only an explicit
`"0"`/`"false"` (case-insensitive) turns it off, giving a terminal with
only the human-readable lines. Unset, empty, `"1"`, `"true"`, or any
other text all mean "on" — the default is never one typo away from
silently changing. This flag never reaches the DB-backed log sink
(`_PersistenceLogHandler`) at all, which `configure_logging` attaches
independently of either console handler — full detail keeps landing
there regardless of this flag's value.

`build_tier_model`/`load_base_config` (below) are the model-tier concern:
every agent in the system uses either the "core" tier (the three core
agents — Main, History, Insights — every deployment constructs
unconditionally) or "sub" (a profile's own specialist agents; see
`profiles.spec.AgentSpec`). Neither function has any knowledge of
environment variables, `os.environ`, or any other config source — each
takes the clean, already-resolved values it needs as plain parameters.
Deciding *where* those values come from (reading `CORE_MODEL_PROVIDER`/
`CORE_MODEL_NAME`/`CORE_MODEL_API_KEY_ENV` — and the two-step
`*_MODEL_API_KEY_ENV` indirection, the same convention `BOT_TOKEN_ENV`
uses — from the real process environment) is entirely the job of the
three real entry points (`api.app.main`, `bot.app.main`,
`cli.user_admin.main`) and, for tests, `conftest.py`'s `test_core_model`/
`test_sub_model` fixtures — the only four places in the production
system and automated test suite that reference `os.environ` for
model-tier config. See `docs/profile_spec.md`'s "Model tiers" section for
the full picture, including the handful of standalone, hand-run
diagnostic scripts under `tests/` (never collected by pytest, never
imported by anything) that read model-tier-shaped variables of their own
accord, outside this count.
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


def _parse_console_json_flag(raw: str | None) -> bool:
    """On by default — strict in the opposite direction from
    `_parse_debug_flag`: only these exact (case-insensitive) values turn
    it off. Unset, empty, "true", "1", or any other text all leave it on,
    matching the stream's behavior before this switch existed.
    """

    return (raw or "").strip().lower() not in ("0", "false")


LOG_CONSOLE_JSON_ENABLED = _parse_console_json_flag(os.environ.get("LOG_CONSOLE_JSON"))


class ModelTierError(Exception):
    """A model tier's provider/model/API key could not be resolved from
    the environment. Raised by whichever of the four roots
    (`api.app.main`, `bot.app.main`, `cli.user_admin.main`,
    `conftest.py`'s test fixtures) is doing that resolution — never by
    `build_tier_model` or `load_base_config` themselves, which take
    already-resolved values and can't fail this way.
    """


@dataclass(frozen=True)
class TierModel:
    """One resolved model tier: `.model`, the `provider/model` string
    (`agents/adapter.py` passes this straight through as
    `crewai.LLM(model=...)` / CrewAI's `llm=` argument — the LiteLLM
    `provider/model` prefix convention); `.api_key`, the tier's actual API
    key value. Built by `build_tier_model` from clean, already-resolved
    values — carries no memory of where those values came from.
    """

    model: str
    api_key: str


def build_tier_model(provider: str, model_name: str, api_key: str) -> TierModel:
    """Join a tier's already-resolved provider/model name/API key into a
    `TierModel`. Pure — no environment access, no knowledge that these
    values might have come from environment variables at all. Every
    non-empty combination of clean strings and validating that they're
    fully resolved is the caller's job (one of the four roots — see
    module docstring); this function never fails, it only constructs.
    """

    return TierModel(model=f"{provider}/{model_name}", api_key=api_key)


@dataclass(frozen=True)
class BaseConfig:
    core_model: TierModel
    DEBUG_FLAG: bool = False


def load_base_config(core_model: TierModel) -> BaseConfig:
    """Return the base configuration. `core_model` is required, already
    resolved by the caller (typically `build_tier_model` fed by one of
    the four roots) — the three core agents (Main, History, Insights) all
    share it; there is no per-agent hardcoded placeholder model for any
    of them, and this function itself never reaches into `os.environ`.
    """

    return BaseConfig(core_model=core_model, DEBUG_FLAG=DEBUG_FLAG)
