"""Firefighting protocol declarations and direct-tool binders."""

from datetime import datetime, timezone
from pathlib import Path

from agents import Agent, InvocationPolicy, NeighboringForcesAgent, SurveillanceAgent, TeamStatusAgent, failed_tool_result, get_authenticated_request_identity, tool
from messages import get_catalog
from persistence import (
    ApparatusStoreError,
    open_apparatus_store,
    open_incident_responder_store,
    open_response_team_surveillance_store,
    open_team_status_persistence,
)
from profiles.admin_tables import AdminColumn, AdminTable
from profiles.contracts import AgentSpec, OptimizationPolicy
from profiles.simulation import SimulationGroup, SimulationPersona, SimulationRoster, SimulationScenario
from protocols import CriticalityLevel, Protocol, Step

import profiles.firefighting as _facade
globals().update({name: getattr(_facade, name) for name in dir(_facade) if not name.startswith("__")})

_FAST_JUDGMENT_POLICY = InvocationPolicy(max_output_tokens=400, reasoning_effort="none")


def _as_aware_iso(value: str) -> str:
    """A persisted event timestamp is stored without an explicit offset but is always UTC
    — the crew-status tools reject a naive string, so make it explicit before a direct bind."""

    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.isoformat()


def _bind_record_crew_availability(event: dict) -> tuple[Step, ...]:
    """Same tool and kwargs as response_team attendance: record_attendance_response from extracted fields."""

    absence_reason = (event.get("absence_reason") or "").strip()
    received_at = event.get("received_at") or ""
    base_kwargs = {
        "source_message_id": event.get("source_message_id") or "",
        "original_text": event.get("raw_text") or "",
        "received_at": _as_aware_iso(received_at) if received_at else "",
    }
    if not absence_reason:
        kwargs = {**base_kwargs, "availability": "available", "reason": "", "unavailable_days": 0}
        required: tuple[str, ...] = ()
    else:
        missing = tuple(name for name in ("availability_start", "availability_end") if not event.get(name))
        kwargs = {**base_kwargs, "availability": "unavailable", "reason": absence_reason}
        if missing:
            required = missing
        else:
            start = datetime.fromisoformat(event["availability_start"])
            end = datetime.fromisoformat(event["availability_end"])
            days = (end - start).total_seconds() / 86400
            kwargs["unavailable_days"] = max(1, int(days + 0.999999))
            required = ()
    return (
        Step(
            agent_name="team_status_agent",
            task_text="Record the reporter's own crew availability response, bound directly from the event's extracted fields.",
            allowed_tools=("record_attendance_response",),
            step_id="1",
            required_event_fields=required,
            kind="direct_tool",
            direct_tool_name="record_attendance_response",
            direct_tool_kwargs=kwargs,
        ),
    )


def _bind_record_crew_shift_status(event: dict) -> tuple[Step, ...]:
    """Commander shift declaration: record_crew_shift_status with member_identities='all', plus one
    apparatus-status step per extracted entity — the same tools a successful CrewAI formulation uses."""

    absence_reason = (event.get("absence_reason") or "").strip()
    received_at = event.get("received_at") or ""
    shift_kwargs = {
        "member_identities": "all",
        "availability": "unavailable" if absence_reason else "available",
        "source_message_id": event.get("source_message_id") or "",
        "original_text": event.get("raw_text") or "",
        "received_at": _as_aware_iso(received_at) if received_at else "",
    }
    steps = [
        Step(
            agent_name="team_status_agent",
            task_text="Record the commander's crew shift availability declaration for the entire approved crew.",
            allowed_tools=("record_crew_shift_status",),
            step_id="1",
            kind="direct_tool",
            direct_tool_name="record_crew_shift_status",
            direct_tool_kwargs=shift_kwargs,
        )
    ]
    entities = event.get("entities") or []
    description = (event.get("description") or "").strip()
    area = (event.get("area") or "").strip()
    for index, identifier in enumerate(entities):
        steps.append(
            Step(
                agent_name="team_status_agent",
                task_text=(
                    f"Apparatus {identifier} was named in the shift declaration"
                    f"{f' in area {area}' if area else ''}. Determine its resulting status "
                    f"(operational, dispatched, unavailable, or maintenance) from the report "
                    f"below, and call update_apparatus_status for {identifier} with that status.\n\n"
                    f"Report: {description}"
                ),
                allowed_tools=("update_apparatus_status",),
                step_id=str(index + 2),
                invocation_policy=_FAST_JUDGMENT_POLICY,
            )
        )
    return tuple(steps)


def _bind_apparatus_movement(event: dict) -> tuple[Step, ...]:
    """Mirrors response_team.py's own _bind_report_team_movement: a direct_tool_binder skips
    formulate_tasks entirely (no separate task-formulation model call), while the step(s) it
    returns still run through the normal agent turn -- status and incident-linking are genuine
    judgment calls from free text, the same class of decision _bind_update_camera_status makes
    for a camera's resulting status, so a keyword heuristic can never pre-decide them."""

    entities = event.get("entities") or []
    area = (event.get("area") or "").strip()
    description = (event.get("description") or "").strip()
    missing = tuple(name for name in ("entities", "description") if not event.get(name))
    if missing:
        return (
            Step(
                agent_name="team_status_agent",
                task_text="Record the reported apparatus status, bound directly from the event's extracted fields.",
                allowed_tools=("update_apparatus_status",),
                step_id="1",
                required_event_fields=missing,
                kind="direct_tool",
                direct_tool_name="update_apparatus_status",
                direct_tool_kwargs={},
            ),
        )
    return tuple(
        Step(
            agent_name="team_status_agent",
            task_text=(
                f"Apparatus {identifier} was reported on{f' in area {area}' if area else ''}. Determine its "
                f"resulting status (operational, dispatched, unavailable, or maintenance) from the report "
                f"below, and call update_apparatus_status for {identifier} with that status. Then decide: "
                f"does the report clearly say {identifier} is dispatched to a specific incident there (not "
                f"merely relocated)? If so, also call join_incident_response for the same area. If the "
                f"report also asks who/what else is responding, also call list_incident_responders for the "
                f"same area and include its answer in your reply.\n\nReport: {description}"
            ),
            allowed_tools=("update_apparatus_status", "join_incident_response", "list_incident_responders"),
            step_id=str(index + 1),
            invocation_policy=_FAST_JUDGMENT_POLICY,
        )
        for index, identifier in enumerate(entities)
    )


PROTOCOLS = [
    Protocol(
        name="record_crew_availability_response",
        description=(
            "Applies when a firefighting crew member reports their own availability for a "
            "shift -- e.g. leaving for a medical checkup, returning from one, or any other "
            "reason they will or will not be on duty; does not apply to a commander asking "
            "about the crew's overall roster (use report_crew_status for that)."
        ),
        participating_agents=("team_status_agent",),
        approved_tools=("record_attendance_response",),
        expected_success_output="Confirmation that the crew member's availability response was recorded.",
        criticality=CriticalityLevel.LOW,
        approval_flag=False,
        requires_confirmation=False,
        commander_only=False,
        needs_insight=False,
        direct_tool_binder=_bind_record_crew_availability,
        direct_lane_eligible=True,
    ),
    Protocol(
        name="record_crew_shift_status",
        description=(
            "Applies when a commander explicitly declares the availability of multiple approved "
            "firefighting crew members for a shift -- for example, that the entire crew is available "
            "at opening; use record_crew_shift_status to persist that declaration. The same report "
            "commonly also states each engine/vehicle's own operating status (e.g. 'Ashed 3 and "
            "Carmel 1 are operational') -- call update_apparatus_status for each one named. Does "
            "not apply to a request for a read-only roster picture (use report_crew_status for that)."
        ),
        participating_agents=("team_status_agent",),
        approved_tools=("record_crew_shift_status", "update_apparatus_status"),
        expected_success_output="Confirmation that the declared crew shift availability was recorded.",
        criticality=CriticalityLevel.LOW,
        approval_flag=False,
        requires_confirmation=False,
        commander_only=True,
        needs_insight=False,
        direct_tool_binder=_bind_record_crew_shift_status,
    ),
    Protocol(
        name="report_apparatus_movement",
        description=(
            "Applies when any crew member (not only a commander) reports one named apparatus's "
            "own dispatch or movement -- e.g. an engine left the station en route to a call and "
            "is no longer available there -- use update_apparatus_status for that engine, and, "
            "if the report clearly states it is dispatched to a specific incident there (not "
            "merely relocated), also call join_incident_response for the same area. If the "
            "report also asks who/what else is responding, also call list_incident_responders. "
            "Does not apply to a commander's blanket declaration of multiple members' shift "
            "availability (use record_crew_shift_status for that), and does not apply to a "
            "read-only status question (use report_crew_status for that)."
        ),
        participating_agents=("team_status_agent",),
        approved_tools=("update_apparatus_status", "join_incident_response", "list_incident_responders"),
        expected_success_output=(
            "Confirmation that the named apparatus's status/area was recorded; if it was "
            "dispatched to a specific incident, confirmation it was linked to it and, if asked, "
            "who/what else is currently linked to that same incident."
        ),
        criticality=CriticalityLevel.LOW,
        approval_flag=False,
        requires_confirmation=False,
        commander_only=False,
        needs_insight=False,
        direct_tool_binder=_bind_apparatus_movement,
        direct_lane_eligible=True,
    ),
    Protocol(
        name="report_crew_status",
        description=(
            "Applies when someone asks for a read-only picture of the firefighting crew's shift "
            "roster or engine/vehicle availability -- e.g. who is currently on duty, or whether "
            "the station's apparatus is available; does not apply to a commander declaring "
            "multiple members' availability (use "
            "record_crew_shift_status for that), and does not apply to a single member's own "
            "availability report (use "
            "record_crew_availability_response for that)."
        ),
        participating_agents=("team_status_agent",),
        approved_tools=("report_team_availability", "get_apparatus_status"),
        expected_success_output="A read-only roster report covering crew headcount and availability.",
        criticality=CriticalityLevel.LOW,
        approval_flag=False,
        requires_confirmation=False,
        commander_only=False,
        direct_lane_eligible=True,
    ),
    Protocol(
        name="update_camera_observation",
        description=(
            "Applies when an operator reports a fire camera's or thermal sensor's own operating "
            "condition -- a heat-alert reading, a lens paused for cleaning, a feed blinded by "
            "smoke/glare, or a similar equipment-status observation; does not apply to what a "
            "camera *shows* about an actual fire (use report_fire_incident for that)."
        ),
        participating_agents=("surveillance_agent",),
        approved_tools=("update_camera_observation",),
        expected_success_output="Confirmation that the camera's observation/status was recorded.",
        criticality=CriticalityLevel.MEDIUM,
        approval_flag=True,
        requires_confirmation=False,
        commander_only=False,
    ),
    Protocol(
        name="dispatch_drone_to_incident",
        description=(
            "Applies when aerial drone recon is needed to confirm or monitor a reported fire or "
            "threat from the air -- e.g. confirming a smoke sighting, or checking fire proximity "
            "to a hazardous structure; does not apply to a routine camera/sensor status update "
            "with no active fire (use update_camera_observation for that)."
        ),
        participating_agents=("surveillance_agent",),
        approved_tools=("dispatch_drone_to_area",),
        expected_success_output=(
            "Confirmation of drone dispatch to the reported location, with callsign, ETA, and mission ID."
        ),
        criticality=CriticalityLevel.MEDIUM,
        approval_flag=True,
        requires_confirmation=False,
        commander_only=False,
    ),
    Protocol(
        name="report_fire_incident",
        description=(
            "Applies to a report of an active or escalating fire -- smoke or flame first "
            "detected, spread into new terrain (a tree line, a structure, a hazardous-materials "
            "site), or a reported casualty/trapped person; does not apply to a routine, "
            "already-resolved, no-risk report (e.g. a small roadside fire already extinguished "
            "with no risk to structures -- use log_fire_observation for that, never dispatch a "
            "drone for an already-handled report), and does not apply to a resource-dispatch "
            "decision itself (use dispatch_mutual_aid for that)."
        ),
        participating_agents=("surveillance_agent",),
        approved_tools=("dispatch_drone_to_area",),
        expected_success_output="Confirmation of drone dispatch to confirm/monitor the reported fire.",
        criticality=CriticalityLevel.HIGH,
        approval_flag=True,
        requires_confirmation=True,
        commander_only=False,
        # A field/citizen fire report can arrive in any group, not only the
        # camera-ops channel this protocol's own agent (surveillance_agent) is bound
        # to -- keep it selectable everywhere (orchestrator/group_routing.py).
        safety_critical=True,
    ),
    Protocol(
        # Split from report_fire_incident (over-dispatch fix, parity with response_team's
        # report_security_incident split): an already-resolved report has no dispatch tool
        # available at all here, structurally, not merely a prompt instruction the agent could
        # still disregard.
        name="log_fire_observation",
        description=(
            "Applies when a report describes a fire-related observation that is explicitly "
            "already resolved, extinguished, or presents no further risk -- e.g. a small "
            "roadside fire already extinguished with no risk to structures. Purely informational: "
            "logs the observation; never dispatches a drone or any other resource. Does not apply "
            "to anything still active, escalating, or unconfirmed (use report_fire_incident for "
            "that)."
        ),
        participating_agents=("surveillance_agent",),
        approved_tools=(),
        expected_success_output="A plain acknowledgement that the observation was logged.",
        criticality=CriticalityLevel.LOW,
        approval_flag=False,
        requires_confirmation=False,
        commander_only=False,
        needs_insight=False,
        safety_critical=True,
    ),
    Protocol(
        name="dispatch_mutual_aid",
        description=(
            "Applies when a report requires dispatching a real external firefighting resource -- "
            "water-tanker trucks, firefighting aircraft, bulldozers/engines, or a police cordon "
            "for evacuation -- to a location; does not apply to a mere report or recon request "
            "with no dispatch decision yet (use report_fire_incident or "
            "overall_situational_picture first for those)."
        ),
        participating_agents=("neighboring_forces_agent",),
        approved_tools=("dispatch_neighboring_force",),
        expected_success_output="Confirmation that the requested mutual-aid resource(s) were dispatched.",
        criticality=CriticalityLevel.HIGH,
        approval_flag=True,
        requires_confirmation=True,
        commander_only=True,
    ),
    Protocol(
        name="overall_situational_picture",
        description=(
            "Applies when a commander asks for a combined snapshot spanning both the crew's "
            "roster/vehicle availability and the camera/surveillance picture in one request -- "
            "e.g. 'what's our force and vehicle availability' or 'urgent picture: exact fire "
            "location and crew status'. Also applies when a commander describes multiple "
            "critical hot spots or reports at once and asks to prioritize response or allocate "
            "crews/water -- pull the actual current records rather than trusting the "
            "commander's own recap of what was reported earlier. Does not apply when only one "
            "of the two domains is asked about."
        ),
        participating_agents=("surveillance_agent", "team_status_agent"),
        approved_tools=("get_surveillance_overview", "report_team_availability"),
        expected_success_output="One combined report covering both current camera status and crew availability.",
        criticality=CriticalityLevel.LOW,
        approval_flag=False,
        requires_confirmation=False,
        commander_only=False,
    ),
    Protocol(
        name="query_historical_incidents",
        description=(
            "Applies when a commander asks for a retrospective, end-to-end debrief of an "
            "incident already underway or closed -- a timeline, resource management, which "
            "reports turned out to be false alarms, or guidance for residents returning home; "
            "does not apply to a question about the current, live state of the crew or cameras."
        ),
        participating_agents=("history_agent",),
        approved_tools=(),
        expected_success_output="A faithful, chronological debrief drawn only from recorded events.",
        criticality=CriticalityLevel.LOW,
        approval_flag=False,
        requires_confirmation=False,
        commander_only=False,
    ),
]
