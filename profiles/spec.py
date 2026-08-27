"""Profile module specification (work_plan.md §1.4).

Declares the exact names a profile module must expose and the structural
(duck-typed) contract its `AGENTS` and `PROTOCOLS` entries must satisfy.
See docs/profile_spec.md for the human-readable version of this contract.

Kept separate from `profiles.loader` so both the loader and
`profiles.loader` depend on one shared definition rather than two that
can drift.

`AgentSpec` (below) is what a profile's `AGENTS` list actually holds: a
declaration, not a built agent. A profile module's own top-level code runs
the moment it's imported, with no way for `profiles.loader.load_profile`
to pass anything into it — so an `AGENTS` entry can never construct
itself against a specific model/API key without reaching into `os.environ`
directly (exactly the exception this design avoids). Declaring `cls`/
`tier` instead defers construction to `load_profile`, which already has
the resolved `core`/`sub` `TierModel` values as explicit parameters — the
only place any agent from a profile's `AGENTS` list actually gets built.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from agents.base import Agent


@dataclass(frozen=True)
class AgentSpec:
    """One `AGENTS` entry: `cls`, a class taking `(model: str, api_key:
    str | None = None)` — normally a concrete `agents.base.Agent`
    subclass (docs/agent_authoring.md) — and `tier`, which of the two
    resolved `TierModel`s (`config.base.TierModel`) `load_profile` builds
    it with.
    """

    cls: "type[Agent]"
    tier: Literal["core", "sub"]

# Every name a profile module must define at module level. No defaults —
# a profile missing one of these fails to load, naming the missing name.
REQUIRED_PROFILE_ATTRS = (
    "AGENTS",
    "PROTOCOLS",
    "EVENT_TYPES",
    "AREAS",
    "DB_PATH",
    "API_PORT",
    "RETRY_COUNT",
    "RISK_THRESHOLD",
    "LOOKBACK_WINDOW_DAYS",
    "BOT_TOKEN_ENV",
    "MODEL_CREDENTIAL_ENVS",
)

# The one event type every deployment gets whether its profile likes it or
# not (work_plan.md §1.2, §2.1). A profile declaring this itself is a
# duplicate and a validation failure.
HUMAN_ACTIVATION_TYPE = "human_activation"

# Attributes a PROTOCOLS entry must expose. Sections 3/4 haven't landed
# real Agent/Protocol classes yet, so this is checked by attribute
# presence (getattr), not isinstance.
PROTOCOL_REQUIRED_ATTRS = (
    "name",
    "description",
    "participating_agents",
    "approved_tools",
    "expected_success_output",
    "criticality",
    "approval_flag",
)


def agent_has_required_shape(agent: Any) -> bool:
    """True if `agent` looks enough like an Agent (§3.1) to validate."""

    exposed_tools = getattr(agent, "exposed_tools", None)
    return bool(getattr(agent, "name", None)) and callable(exposed_tools)


def protocol_missing_attrs(protocol: Any) -> list[str]:
    """Names from PROTOCOL_REQUIRED_ATTRS that `protocol` does not expose.

    Empty list means the protocol has the right shape — it says nothing
    about whether the *values* (e.g. a non-empty description) are valid;
    that judgment belongs to profiles.loader.
    """

    missing = []
    for attr_name in PROTOCOL_REQUIRED_ATTRS:
        if not hasattr(protocol, attr_name):
            missing.append(attr_name)

    return missing
