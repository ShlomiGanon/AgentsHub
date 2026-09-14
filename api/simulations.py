"""Server-side JSON adapter: profile-declared simulations -> the existing
admin-simulator scenario JSON contract (docs/profile_simulations_design.md).

Pure logic, no Flask — the conversion is kept separate from the Flask routes
that serve it. `api/routes.py`'s simulations blueprint (`GET /Simulations`,
`GET /Simulations/<key>`) is the only caller.
"""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING

from profiles import (
    SimulationGroup,
    SimulationPersona,
    SimulationScenario,
    simulation_group_chat_id,
    simulation_user_telegram_id,
)

if TYPE_CHECKING:
    from profiles.contracts import LoadedProfile


def simulation_catalog_payload(loaded_profile: "LoadedProfile") -> list[dict]:
    """One `{key, title, description, tags}` entry per declared `SimulationScenario` —
    metadata only, cheap enough to compute from the already-loaded profile on every call."""

    return [
        {
            "key": scenario.key,
            "title": scenario.title,
            "description": scenario.description,
            "tags": list(scenario.tags),
        }
        for scenario in loaded_profile.simulations
    ]


def find_simulation_scenario(loaded_profile: "LoadedProfile", key: str) -> SimulationScenario | None:
    """The declared scenario named `key`, or None."""

    for scenario in loaded_profile.simulations:
        if scenario.key == key:
            return scenario
    return None


def materialize_simulation(
    scenario: SimulationScenario,
    simulation_users: tuple[SimulationPersona, ...],
    simulation_groups: tuple[SimulationGroup, ...],
) -> dict:
    """The scenario's canonical `{scenario, chats, steps}` JSON, with every declared
    persona/group *key* it references replaced by its deterministic reserved
    Telegram ID. Every other field (labels, text, timestamps, protocol hints,
    non-persona sender identities such as a sensor's) passes through unchanged —
    this never restructures the existing contract, only substitutes the two ID
    fields. `profiles.loader.validate_profile` already guarantees, at profile
    load time, that every placeholder a declared scenario references resolves to
    a declared persona/group key, so an unresolved key is not expected here.
    """

    user_ids_by_key = {persona.key: simulation_user_telegram_id(persona.offset) for persona in simulation_users}
    group_ids_by_key = {group.key: simulation_group_chat_id(group.offset) for group in simulation_groups}

    materialized = copy.deepcopy(dict(scenario.raw))

    for chat in materialized.get("chats", []):
        chat_id_key = chat.get("telegram_chat_id")
        if chat_id_key is not None and chat_id_key in group_ids_by_key:
            chat["telegram_chat_id"] = group_ids_by_key[chat_id_key]

    for step in materialized.get("steps", []):
        sender_key = step.get("sender_identity")
        if sender_key in user_ids_by_key:
            step["sender_identity"] = user_ids_by_key[sender_key]

    return materialized
