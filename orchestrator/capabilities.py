"""Role-aware capability context for the Main Agent (docs/Next_Plan.md §4.3, §4.4, §8).

The application controls which capabilities and runtime metadata may enter a
prompt; the model only ever phrases the natural-language answer from what it
is given here. See docs/Next_Plan.md §2.5.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from auth.permissions import PermissionLevel, RequestedOperation, is_permitted
from protocols import Protocol

if TYPE_CHECKING:
    from agents.runtime import AgentRegistry


@dataclass(frozen=True)
class CapabilityDescriptor:
    """English technical facts about one system capability — never a final answer sentence.

    `operation` is the `RequestedOperation` that authorizes disclosing and
    using this capability. A caller sees only the descriptors whose
    operation they are authorized for (`auth.permissions.is_permitted`) —
    this is the only capability policy; there is no separate disclosure list.
    """

    name: str
    description: str
    operation: RequestedOperation
    uses_event_history: bool = False
    uses_current_state: bool = False
    uses_protocols: bool = False
    has_side_effects: bool = False
    requires_human_review: bool = False


CAPABILITY_DESCRIPTORS: tuple[CapabilityDescriptor, ...] = (
    CapabilityDescriptor(
        name="report_event",
        description="Receive and classify an operational event report.",
        operation=RequestedOperation.REPORT_EVENT,
        uses_protocols=True,
    ),
    CapabilityDescriptor(
        name="request_action",
        description="Accept an action request and run a matching approved protocol.",
        operation=RequestedOperation.REQUEST_ACTION,
        uses_protocols=True,
        has_side_effects=True,
    ),
    CapabilityDescriptor(
        name="ask_current_state",
        description="Answer current-state questions through suitable read-only specialist tools.",
        operation=RequestedOperation.ASK_QUESTION,
        uses_current_state=True,
    ),
    CapabilityDescriptor(
        name="ask_event_history",
        description="Search and explain persisted event history without treating conversation as operational fact.",
        operation=RequestedOperation.ASK_QUESTION,
        uses_event_history=True,
    ),
    CapabilityDescriptor(
        name="handle_human_review",
        description="Request clarification or commander approval when safe execution of a report or request requires it.",
        operation=RequestedOperation.REQUEST_ACTION,
        requires_human_review=True,
    ),
    CapabilityDescriptor(
        name="view_protocols",
        description="List the loaded handling protocols and what each one is for.",
        operation=RequestedOperation.LIST_PROTOCOLS,
        uses_protocols=True,
    ),
    CapabilityDescriptor(
        name="manage_protocols",
        description="Add, edit, or remove handling protocols (takes effect on the next restart).",
        operation=RequestedOperation.CREATE_PROTOCOL,
        uses_protocols=True,
        has_side_effects=True,
    ),
    CapabilityDescriptor(
        name="manage_settings",
        description="View and change live operational settings: retry count, risk threshold, lookback window.",
        operation=RequestedOperation.CHANGE_SETTINGS,
        has_side_effects=True,
    ),
    CapabilityDescriptor(
        name="resolve_clarifications",
        description="Resolve a held event's clarification by choosing its correct classification.",
        operation=RequestedOperation.RESOLVE_CLARIFICATION,
        requires_human_review=True,
        has_side_effects=True,
    ),
    CapabilityDescriptor(
        name="approve_runs",
        description="Approve or reject a flagged protocol run, or pick among ambiguous candidates.",
        operation=RequestedOperation.APPROVE_RUN,
        requires_human_review=True,
        has_side_effects=True,
    ),
    CapabilityDescriptor(
        name="view_commander_roster",
        description="List every registered commander identity.",
        operation=RequestedOperation.VIEW_COMMANDER_ROSTER,
    ),
    CapabilityDescriptor(
        name="poll_notifications",
        description="Receive asynchronous job, clarification, and approval notifications.",
        operation=RequestedOperation.POLL_NOTIFICATIONS,
    ),
    CapabilityDescriptor(
        name="describe_runtime",
        description="Describe the registered sub-agents and tools available at runtime.",
        operation=RequestedOperation.VIEW_SYSTEM_INTERNALS,
    ),
)


def _validate_capability_descriptors() -> None:
    """Every descriptor's operation must be a real `RequestedOperation` — a startup-time
    configuration error, never a silently ungrounded capability claim."""

    for descriptor in CAPABILITY_DESCRIPTORS:
        if not isinstance(descriptor.operation, RequestedOperation):
            raise ValueError(f"CapabilityDescriptor {descriptor.name!r} has no valid RequestedOperation")


_validate_capability_descriptors()


def visible_capabilities(level: PermissionLevel) -> tuple[CapabilityDescriptor, ...]:
    """Every descriptor `level` is authorized for — the sole source for a "what can you do
    for me?" answer. Removing a `RequestedOperation` from `ViewerAllowedAction` automatically
    removes every descriptor built on it from a viewer's result, with no separate edit here."""

    return tuple(descriptor for descriptor in CAPABILITY_DESCRIPTORS if is_permitted(level, descriptor.operation))


def build_role_aware_system_context(
    level: PermissionLevel,
    profile_name: str,
    protocols: tuple[Protocol, ...],
    registry: "AgentRegistry",
    event_types: tuple[str, ...],
    areas: tuple[str, ...],
) -> dict:
    """Build the Main Agent's system context, filtered for `level`.

    Protected arrays (`protocols`, `sub_agents`) are absent entirely for a
    caller not authorized for `VIEW_SYSTEM_INTERNALS` — never present as an
    empty hint (docs/Next_Plan.md §4.5). `capabilities` is always the
    caller's own `visible_capabilities`, never the full set with some
    filtered out after the fact.
    """

    context: dict = {
        "identity": {
            "profile_name": profile_name,
            "role": "main agent and orchestrator of the event-management service",
        },
        "capabilities": [
            {"name": descriptor.name, "description": descriptor.description}
            for descriptor in visible_capabilities(level)
        ],
        "event_types": list(event_types),
        "areas": list(areas),
    }

    if is_permitted(level, RequestedOperation.VIEW_SYSTEM_INTERNALS):
        context["protocols"] = [
            {
                "name": protocol.name,
                "description": protocol.description,
                "participating_agents": list(protocol.participating_agents),
                "approved_tools": list(protocol.approved_tools),
                "requires_approval": protocol.approval_flag,
            }
            for protocol in protocols
        ]
        context["sub_agents"] = [
            {
                "name": agent.descriptor.name,
                "role": agent.descriptor.role,
                "tools": [
                    {"name": tool.name, "description": tool.description, "side_effecting": tool.side_effecting}
                    for tool in agent.descriptor.tools
                ],
            }
            for agent in registry.all()
            if agent.descriptor.name != "main_agent"
        ]

    return context
