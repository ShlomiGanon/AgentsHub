"""Base configuration (work_plan.md §1.3).

Holds only values true of every deployment. Today that is the model each
of the three core agents runs on. Deployment-specific values — ports,
database paths, agent rosters, protocols — belong to a profile, never here.

Loaded before any profile, since the three core agents are constructed
regardless of which profile was named.
"""

from dataclasses import dataclass

DEBUG_FLAG = False


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
