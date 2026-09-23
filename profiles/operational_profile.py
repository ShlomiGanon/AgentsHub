"""Canonical operational-organization type, separate from operational scope.

`OperationalScope` answers *which operational instance am I in* — LIVE, or one
simulation run. `OperationalProfile` answers a different question: *what type of
operational organization is this* — a response team, or a fire station. One
deployment of the shared GTCA core hosts both, and a single scope always has
exactly one profile.

The profile is trusted configuration. It is never chosen by a model and never
inferred from the words in a message: a report mentioning fire does not make the
reporting organization a fire station. In production it comes from the
deployment's declared LIVE profile; in simulation it comes from the fixture's
own canonical metadata.

A profile carries semantics, not state. It says what an organization can
contain and how it speaks; the scoped stores remain the only source of what is
currently true.
"""

from __future__ import annotations

import re
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Mapping

from messages import get_catalog


RESPONSE_TEAM = "response_team"
FIRE_STATION = "fire_station"


# Operational domains a profile may enable. These name existing subsystems of
# the shared core; a profile selects among them, it does not invent new ones.
DOMAIN_TEAM = "team"
DOMAIN_SURVEILLANCE = "surveillance"
DOMAIN_DRONES = "drones"
DOMAIN_EXTERNAL_FORCES = "external_forces"
DOMAIN_OPERATIONAL_FACTS = "operational_facts"
DOMAIN_HISTORY = "history"


class OperationalProfileError(ValueError):
    """The requested operational profile is not declared."""


@dataclass(frozen=True)
class ResourceDefinition:
    """One resource kind an organization of this type can operate.

    A definition is not an inventory. Declaring that a fire station understands
    water tenders does not give any particular station one; a run materializes
    the resources its own fixture reports.
    """

    resource_id: str
    pattern: str
    label_key: str

    def matches(self, text: str) -> "re.Match | None":
        return re.search(self.pattern, str(text or ""), re.IGNORECASE)


@dataclass(frozen=True)
class OperationalProfile:
    """What an operational organization of this type contains and understands."""

    profile_id: str
    name_key: str
    roster_label_key: str
    member_label_key: str
    domains: frozenset[str]
    agents: frozenset[str]
    protocols: frozenset[str]
    resources: tuple[ResourceDefinition, ...] = ()

    def allows_domain(self, domain: str) -> bool:
        return domain in self.domains

    def allows_agent(self, agent_name: str) -> bool:
        return agent_name in self.agents

    def allows_protocol(self, protocol_name: str) -> bool:
        return protocol_name in self.protocols

    def protocol_catalogue(self, declared: tuple | list) -> tuple:
        """The declared protocols this organization type actually exposes.

        Filtering never invents a protocol: a name the deployment does not
        declare cannot become executable by appearing in a profile.
        """

        return tuple(
            protocol for protocol in declared
            if self.allows_protocol(getattr(protocol, "name", ""))
        )

    def resolve_resource(self, text: str) -> tuple[str, int] | None:
        """The one resource and count this text states, or None.

        A profile that declares no resource catalogue resolves nothing, which is
        how a response team stays unable to report a fire engine it does not
        operate.
        """

        for definition in self.resources:
            match = definition.matches(text)
            if match is None:
                continue
            try:
                return definition.resource_id, int(match.group(1))
            except (IndexError, ValueError):
                continue
        return None

    def resolve_resources(self, text: str) -> tuple[dict, ...]:
        """Every declared resource this text states, in catalogue order."""

        found = []
        for definition in self.resources:
            match = definition.matches(text)
            if match is None:
                continue
            try:
                count = int(match.group(1))
            except (IndexError, ValueError):
                continue
            found.append({"name": definition.resource_id, "count": count, "status": "operational"})
        return tuple(found)


def _resource(resource_id: str, label_key: str) -> ResourceDefinition:
    """Build a catalogue entry whose match vocabulary lives in the catalog.

    The pattern is bilingual operator vocabulary, so it belongs beside every
    other piece of user-facing language rather than inside production logic.
    """

    return ResourceDefinition(
        resource_id=resource_id,
        pattern=get_catalog("en").text(f"{label_key}.pattern"),
        label_key=label_key,
    )


# --- the two declared organization types ------------------------------------

_RESPONSE_TEAM_PROFILE = OperationalProfile(
    profile_id=RESPONSE_TEAM,
    name_key="profile.response_team.name",
    roster_label_key="profile.response_team.roster",
    member_label_key="profile.response_team.member",
    domains=frozenset({
        DOMAIN_TEAM, DOMAIN_SURVEILLANCE, DOMAIN_DRONES,
        DOMAIN_EXTERNAL_FORCES, DOMAIN_OPERATIONAL_FACTS, DOMAIN_HISTORY,
    }),
    agents=frozenset({"team_status_agent", "surveillance_agent", "friendly_forces_agent"}),
    # Every name here is a protocol the deployment actually declares. A name that
    # drifts from the declared set is not a harmless typo: `protocol_catalogue`
    # intersects the two, so a misspelled entry silently removes a real capability.
    protocols=frozenset({
        "report_team_availability",
        "record_attendance_response",
        "query_camera_status",
        "query_drone_fleet_status",
        "query_active_drone_missions",
        "query_surveillance_overview",
        "query_historical_incidents",
        "overall_situational_picture",
        "dispatch_drone_to_incident",
        "recall_drone_to_base",
        "dispatch_emergency_forces",
    }),
    # A response team operates no canonical vehicle catalogue of its own in the
    # current architecture, so it resolves no resource names.
    resources=(),
)

_FIRE_STATION_PROFILE = OperationalProfile(
    profile_id=FIRE_STATION,
    name_key="profile.fire_station.name",
    roster_label_key="profile.fire_station.roster",
    member_label_key="profile.fire_station.member",
    domains=frozenset({
        DOMAIN_TEAM, DOMAIN_SURVEILLANCE, DOMAIN_DRONES,
        DOMAIN_EXTERNAL_FORCES, DOMAIN_OPERATIONAL_FACTS, DOMAIN_HISTORY,
    }),
    agents=frozenset({"team_status_agent", "surveillance_agent", "friendly_forces_agent"}),
    protocols=frozenset({
        "report_team_availability",
        "record_attendance_response",
        "query_camera_status",
        "query_drone_fleet_status",
        "query_active_drone_missions",
        "query_surveillance_overview",
        "query_historical_incidents",
        "overall_situational_picture",
        "dispatch_drone_to_incident",
        "recall_drone_to_base",
        "dispatch_emergency_forces",
        # The one capability a response team does not have. Fire-service mutual
        # aid needs a real action path, and now has one: `dispatch_mutual_aid`
        # approves `dispatch_water_tankers`/`dispatch_aircraft`.
        "dispatch_mutual_aid",
    }),
    resources=(
        _resource("ASHED", "profile.fire_station.resource.ashed"),
        _resource("CARMEL", "profile.fire_station.resource.carmel"),
    ),
)

_DECLARED_PROFILES: Mapping[str, OperationalProfile] = {
    RESPONSE_TEAM: _RESPONSE_TEAM_PROFILE,
    FIRE_STATION: _FIRE_STATION_PROFILE,
}

# Canonical fixture domain -> organization type. The mapping is declared here,
# never derived at runtime from a scenario identifier, so a fixture renamed or
# a scenario added cannot silently change which organization it belongs to.
FIXTURE_DOMAIN_PROFILES: Mapping[str, str] = {
    "FIRST_RESPONDERS_TEAM": RESPONSE_TEAM,
    "FIRE_AND_RESCUE": FIRE_STATION,
}


def operational_profile(profile_id: str) -> OperationalProfile:
    """The declared organization type, or an error — never a guess."""

    profile = _DECLARED_PROFILES.get(str(profile_id or ""))
    if profile is None:
        raise OperationalProfileError(f"undeclared operational profile: {profile_id!r}")
    return profile


def declared_operational_profiles() -> tuple[str, ...]:
    return tuple(sorted(_DECLARED_PROFILES))


def profile_id_for_scenario(scenario, default_profile_id: str) -> str:
    """Resolve one fixture's organization type from its canonical metadata.

    Order: an explicit `operational_profile` declared by the fixture, then the
    declared mapping for its canonical `domain`, then the deployment default.
    A fixture that declares neither — the support/overall fixture — belongs to
    the deployment's own organization rather than to a guessed one.
    """

    metadata = getattr(scenario, "official_metadata", None) or {}
    declared = metadata.get("operational_profile")
    if declared:
        return str(declared)

    domain = getattr(scenario, "domain", None)
    if domain and str(domain) in FIXTURE_DOMAIN_PROFILES:
        return FIXTURE_DOMAIN_PROFILES[str(domain)]

    return default_profile_id


def profile_for_scope(loaded_profile, scope) -> OperationalProfile:
    """The organization type that owns one operational scope.

    LIVE uses the deployment's own declared type. A simulation run uses the
    type its fixture declares, resolved through canonical metadata rather than
    through the run identifier, so the association is configuration and cannot
    drift with a rename.

    Nothing about this needs persisting: `scenario_id` is already stored on
    every event, and the fixture catalogue is deployment configuration, so a
    legacy run resolves to exactly the same type it always had.
    """

    default_id = str(getattr(loaded_profile, "live_operational_profile", RESPONSE_TEAM))
    if scope is None or not getattr(scope, "is_simulation", False):
        return operational_profile(default_id)

    scenario_id = str(getattr(scope, "scenario_id", "") or "")
    scenario = next(
        (item for item in getattr(loaded_profile, "simulations", ()) if item.scenario_id == scenario_id),
        None,
    )
    if scenario is None:
        return operational_profile(default_id)
    return operational_profile(profile_id_for_scenario(scenario, default_id))


# --- ambient binding ---------------------------------------------------------

_CURRENT_PROFILE: ContextVar[OperationalProfile | None] = ContextVar(
    "current_operational_profile", default=None
)


@contextmanager
def operational_profile_context(profile: OperationalProfile | None):
    """Bind the active organization type for everything inside this scope.

    Bound the same way the operational scope and clock are, at the boundary
    that already resolved trusted request metadata. Nothing is process-global:
    two runs of different types execute concurrently without seeing each other.
    """

    token = _CURRENT_PROFILE.set(profile)
    try:
        yield
    finally:
        _CURRENT_PROFILE.reset(token)


def current_operational_profile() -> OperationalProfile | None:
    """The active organization type, when a boundary declared one."""

    return _CURRENT_PROFILE.get()
