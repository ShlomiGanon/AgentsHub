"""Startup profile validation (work_plan.md §1.6).

Runs after loading and before anything else accepts traffic. Collects
every failure found — not only the first — so an author fixing a profile
sees the whole list in one run.

Checks agents and protocols structurally (attribute presence via
profiles.spec), not by type, since the real Agent/Protocol classes land in
§3/§4. See docs/profile_spec.md.
"""

from typing import TYPE_CHECKING

from profiles.spec import HUMAN_ACTIVATION_TYPE, protocol_missing_attrs

if TYPE_CHECKING:
    from profiles.loader import LoadedProfile


def validate_profile(loaded: "LoadedProfile", declared_event_types: list) -> list[str]:
    failures: list[str] = []

    agents_by_name = {agent.name: agent for agent in loaded.agents}

    for protocol in loaded.protocols:
        failures.extend(_validate_protocol(protocol, agents_by_name))

    if not declared_event_types:
        failures.append("profile declares no event types — extraction has nothing to classify into")

    if HUMAN_ACTIVATION_TYPE in declared_event_types:
        failures.append(
            f"profile declares '{HUMAN_ACTIVATION_TYPE}' as an event type — "
            "it is built in and added automatically, declaring it is a duplicate"
        )

    if not loaded.areas:
        failures.append("profile declares no areas — extraction has nothing to resolve a location to")

    return failures


def _validate_protocol(protocol, agents_by_name: dict) -> list[str]:
    failures: list[str] = []

    missing_attrs = protocol_missing_attrs(protocol)
    if missing_attrs:
        failures.append(
            f"protocol object {protocol!r} is missing required attribute(s): "
            f"{', '.join(missing_attrs)}"
        )
        return failures

    exposed_by_participants: set[str] = set()

    for agent_name in protocol.participating_agents:
        agent = agents_by_name.get(agent_name)
        if agent is None:
            failures.append(
                f"protocol '{protocol.name}' names agent '{agent_name}' "
                "which was not constructed by the profile"
            )
            continue

        # exposed_tools() returns tool *names* for the duck-typed test
        # fixtures (plain strings) but ToolInfo objects for a real
        # agents.base.Agent (§3.3) — getattr's default falls through to
        # the value itself when there's no .name, so this handles both
        # shapes without needing to know which one we were given.
        exposed_by_participants.update(getattr(t, "name", t) for t in agent.exposed_tools())

    for tool_name in protocol.approved_tools:
        if tool_name not in exposed_by_participants:
            failures.append(
                f"protocol '{protocol.name}' approves tool '{tool_name}' "
                "which none of its participating agents expose"
            )

    if not protocol.description:
        failures.append(f"protocol '{protocol.name}' has no description")

    if not protocol.expected_success_output:
        failures.append(f"protocol '{protocol.name}' has no expected success output")

    if protocol.criticality is None:
        failures.append(f"protocol '{protocol.name}' has no criticality level")

    if protocol.approval_flag is not True and protocol.approval_flag is not False:
        failures.append(
            f"protocol '{protocol.name}' has no explicitly-set approval flag "
            "(True/False required — an absent flag is not defaulted)"
        )

    return failures
