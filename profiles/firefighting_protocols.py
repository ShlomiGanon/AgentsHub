"""Firefighting protocol declarations and direct-tool binders."""

import re
from dataclasses import replace

from agents import InvocationPolicy
from messages import SUPPORTED_LANGUAGES, get_catalog
from messages.camera_names import resolve_camera_id_from_records
from messages.apparatus_names import resolve_apparatus_ids_from_records
from profiles.firefighting import APPARATUS, CAMERAS, FORCE_BASES
from protocols import CriticalityLevel, Protocol, Step
from protocols import as_aware_iso as _as_aware_iso
from protocols import bind_record_attendance_response

_FAST_JUDGMENT_POLICY = InvocationPolicy(max_output_tokens=400, reasoning_effort="none")


def _report_text(event: dict) -> str:
    """Prefer the extracted description, then the original report text."""

    return (event.get("description") or event.get("raw_text") or "").strip()


def _camera_status_markers(kind: str) -> tuple[str, ...]:
    """Collect status vocabulary from every supported message catalog."""

    key = f"firefighting.camera.status_markers.{kind}"
    return tuple(
        marker.strip()
        for language in SUPPORTED_LANGUAGES
        for marker in get_catalog(language).text(key).split("|")
        if marker.strip()
    )


_CAMERA_OFFLINE_MARKERS = _camera_status_markers("offline")
_CAMERA_DEGRADED_MARKERS = _camera_status_markers("degraded")
_CAMERA_ACTIVE_MARKERS = _camera_status_markers("active")


def _camera_status_from_report(report: str) -> str:
    """Map explicit operating-condition language to the store's stable status values."""

    folded = report.casefold()
    if any(marker in folded for marker in _CAMERA_OFFLINE_MARKERS):
        return "offline"
    if any(marker in folded for marker in _CAMERA_DEGRADED_MARKERS):
        return "degraded"
    if any(marker in folded for marker in _CAMERA_ACTIVE_MARKERS):
        return "active"
    return ""


def _camera_ids_for_event(event: dict) -> tuple[str, ...]:
    """Canonicalize extracted camera references and deduplicate one physical asset."""

    raw_references = event.get("entities") or ()
    if isinstance(raw_references, str):
        raw_references = (raw_references,)
    references = tuple(str(value).strip() for value in raw_references if str(value).strip())
    if not references:
        report = _report_text(event)
        report_camera_id = resolve_camera_id_from_records(CAMERAS, report, "firefighting")
        if report_camera_id in {camera["camera_id"] for camera in CAMERAS}:
            references = (report,)
    area = str(event.get("area") or "").strip()
    area_cameras = tuple(
        camera["camera_id"]
        for camera in CAMERAS
        if str(camera.get("area") or "").casefold() == area.casefold()
    )
    resolved: list[str] = []
    for reference in references:
        camera_id = resolve_camera_id_from_records(CAMERAS, reference, "firefighting")
        explicit_camera_id = re.fullmatch(r"CAM-[A-Z0-9_-]+", reference, flags=re.IGNORECASE)
        if camera_id == reference and not explicit_camera_id and len(area_cameras) == 1:
            camera_id = area_cameras[0]
        if camera_id not in resolved:
            resolved.append(camera_id)
    if not resolved and len(area_cameras) == 1:
        resolved.append(area_cameras[0])
    return tuple(resolved)


def _bind_record_crew_availability(event: dict) -> tuple[Step, ...]:
    """Same tool and kwargs as response_team attendance: record_attendance_response from extracted fields."""

    return bind_record_attendance_response(
        event,
        agent_name="team_status_agent",
        task_text="Record the reporter's own crew availability response, bound directly from the event's extracted fields.",
    )


def _apparatus_ids_for_event(event: dict) -> tuple[str, ...]:
    """Keep only profile-owned apparatus, never people or external resources."""

    entities = event.get("entities") or ()
    if isinstance(entities, str):
        entities = (entities,)
    return resolve_apparatus_ids_from_records(
        APPARATUS,
        (*entities, _report_text(event)),
        stem="firefighting",
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
    description = _report_text(event)
    area = (event.get("area") or "").strip()
    for index, identifier in enumerate(_apparatus_ids_for_event(event)):
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
    """Mirrors response_team's movement binder: a direct_tool_binder skips formulate_tasks,
    while the step(s) it returns still run through the normal agent turn -- status and
    incident-linking are genuine judgment calls from free text."""

    apparatus_ids = _apparatus_ids_for_event(event)
    area = (event.get("area") or "").strip()
    description = _report_text(event)
    missing = tuple(name for name in ("entities",) if not apparatus_ids)
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
                f"You MUST call update_apparatus_status exactly once for apparatus {identifier}. "
                f"Map the report to status: dispatched (left station / en route to a call), "
                f"operational (available at station), unavailable, or maintenance"
                f"{f' in area {area}' if area else ''}. Then decide: does the report clearly say "
                f"{identifier} is dispatched to a specific incident there (not merely relocated)? "
                f"If so, also call join_incident_response for the same area. If the report also "
                f"asks who/what else is responding, also call list_incident_responders for the "
                f"same area and include its answer in your reply.\n\nReport: {description}"
            ),
            allowed_tools=("update_apparatus_status", "join_incident_response", "list_incident_responders"),
            step_id=str(index + 1),
            invocation_policy=_FAST_JUDGMENT_POLICY,
        )
        for index, identifier in enumerate(apparatus_ids)
    )


def _bind_update_camera_observation(event: dict) -> tuple[Step, ...]:
    """Update each named camera directly with canonical id and deterministic status."""

    camera_ids = _camera_ids_for_event(event)
    description = _report_text(event)
    if not camera_ids:
        return (
            Step(
                agent_name="surveillance_agent",
                task_text="Record the reported fire-camera observation, bound directly from the event's extracted fields.",
                allowed_tools=("update_camera_observation",),
                step_id="1",
                required_event_fields=("entities",),
                kind="direct_tool",
                direct_tool_name="update_camera_observation",
                direct_tool_kwargs={},
            ),
        )
    return tuple(
        Step(
            agent_name="surveillance_agent",
            task_text=f"Record the reported operating-condition observation for camera {camera_id}.",
            allowed_tools=("update_camera_observation",),
            step_id=str(index + 1),
            kind="direct_tool",
            direct_tool_name="update_camera_observation",
            direct_tool_kwargs={
                "camera_id": camera_id,
                "new_observation": description,
                "status": _camera_status_from_report(description),
            },
        )
        for index, camera_id in enumerate(camera_ids)
    )


def _bind_dispatch_drone(event: dict) -> tuple[Step, ...]:
    """Direct-tool recon dispatch when area is known; otherwise a missing-fields hold."""

    area = (event.get("area") or "").strip()
    description = _report_text(event)
    missing = tuple(name for name in ("area",) if not area)
    kwargs: dict = {}
    if not missing:
        kwargs = {
            "target_area": area,
            "incident_description": description or "Reported fire incident",
            "mission_type": "recon",
        }
    return (
        Step(
            agent_name="surveillance_agent",
            task_text="Dispatch a recon drone to the reported fire area, bound directly from the event's extracted fields.",
            allowed_tools=("dispatch_drone_to_area",),
            step_id="1",
            required_event_fields=missing,
            kind="direct_tool",
            direct_tool_name="dispatch_drone_to_area",
            direct_tool_kwargs=kwargs,
        ),
    )


def _bind_report_fire_incident(event: dict) -> tuple[Step, ...]:
    """Record the fire as burning in the COP registry, then dispatch recon -- the
    registry write is a direct_tool so a first report is never camera-only."""

    area = (event.get("area") or "").strip()
    missing = tuple(name for name in ("area",) if not area)
    fire_kwargs: dict = {}
    if not missing:
        fire_kwargs = {
            "area": area,
            "status": "burning",
            "source_event_id": (event.get("event_id") or "").strip(),
        }
    fire_step = Step(
        agent_name="team_status_agent",
        task_text="Record the reported fire as currently burning in the fires registry.",
        allowed_tools=("record_fire_status",),
        step_id="1",
        required_event_fields=missing,
        kind="direct_tool",
        direct_tool_name="record_fire_status",
        direct_tool_kwargs=fire_kwargs,
    )
    if missing:
        return (fire_step,)
    drone_step = replace(_bind_dispatch_drone(event)[0], step_id="2")
    return (fire_step, drone_step)


def _bind_dispatch_drone_to_incident(event: dict) -> tuple[Step, ...]:
    """Follow-up recon: refresh the burning fire's last_updated (TTL clock), then dispatch."""

    area = (event.get("area") or "").strip()
    missing = tuple(name for name in ("area",) if not area)
    touch_kwargs: dict = {}
    if not missing:
        touch_kwargs = {"area": area}
    touch_step = Step(
        agent_name="team_status_agent",
        task_text="Refresh the burning fire currently on record for this area, if any.",
        allowed_tools=("touch_active_fire",),
        step_id="1",
        required_event_fields=missing,
        kind="direct_tool",
        direct_tool_name="touch_active_fire",
        direct_tool_kwargs=touch_kwargs,
    )
    if missing:
        return (touch_step,)
    drone_step = replace(_bind_dispatch_drone(event)[0], step_id="2")
    return (touch_step, drone_step)


def _bind_log_fire_observation(event: dict) -> tuple[Step, ...]:
    """Record an already-resolved fire as extinguished; never dispatch."""

    area = (event.get("area") or "").strip()
    missing = tuple(name for name in ("area",) if not area)
    kwargs: dict = {}
    if not missing:
        kwargs = {"area": area, "status": "extinguished"}
    return (
        Step(
            agent_name="team_status_agent",
            task_text="Record the reported fire as extinguished in the fires registry.",
            allowed_tools=("record_fire_status",),
            step_id="1",
            required_event_fields=missing,
            kind="direct_tool",
            direct_tool_name="record_fire_status",
            direct_tool_kwargs=kwargs,
        ),
    )


def _fire_notice_step(notice_key: str, task_text: str, step_id: str) -> Step:
    """Catalog update for the station commander. No camera check."""

    return Step(
        agent_name="team_status_agent",
        task_text=task_text,
        allowed_tools=("post_operational_notice",),
        step_id=step_id,
        kind="direct_tool",
        direct_tool_name="post_operational_notice",
        direct_tool_kwargs={"notice_key": notice_key},
    )


def _bind_close_contained_fire(event: dict) -> tuple[Step, ...]:
    """Mark the fire extinguished when the area is known, then notify containment."""

    area = (event.get("area") or "").strip()
    steps: list[Step] = []
    if area:
        steps.append(
            Step(
                agent_name="team_status_agent",
                task_text="Record the fire as extinguished because the incident is contained.",
                allowed_tools=("record_fire_status",),
                step_id="1",
                kind="direct_tool",
                direct_tool_name="record_fire_status",
                direct_tool_kwargs={"area": area, "status": "extinguished"},
            )
        )
    steps.append(
        _fire_notice_step(
            "firefighting.notice.incident_contained",
            "Notify that the fire incident is contained.",
            str(len(steps) + 1),
        )
    )
    return tuple(steps)


def _bind_correct_false_fire_report(event: dict) -> tuple[Step, ...]:
    """Record a false alarm as extinguished when the area is known, then notify."""

    area = (event.get("area") or "").strip()
    steps: list[Step] = []
    if area:
        steps.append(
            Step(
                agent_name="team_status_agent",
                task_text="Record the reported fire as extinguished because the report was a false alarm.",
                allowed_tools=("record_fire_status",),
                step_id="1",
                kind="direct_tool",
                direct_tool_name="record_fire_status",
                direct_tool_kwargs={"area": area, "status": "extinguished"},
            )
        )
    steps.append(
        _fire_notice_step(
            "firefighting.notice.false_alarm",
            "Notify that the fire report was a false alarm.",
            str(len(steps) + 1),
        )
    )
    return tuple(steps)


def _bind_overall_situational_picture(event: dict) -> tuple[Step, ...]:
    """Read cameras, crew availability, and burning fires. No specialist model call."""

    return (
        Step(
            agent_name="surveillance_agent",
            task_text="Read the current surveillance overview.",
            allowed_tools=("get_surveillance_overview",),
            step_id="1",
            kind="direct_tool",
            direct_tool_name="get_surveillance_overview",
            direct_tool_kwargs={},
        ),
        Step(
            agent_name="team_status_agent",
            task_text="Read the current crew availability.",
            allowed_tools=("report_team_availability",),
            step_id="2",
            kind="direct_tool",
            direct_tool_name="report_team_availability",
            direct_tool_kwargs={},
        ),
        Step(
            agent_name="team_status_agent",
            task_text="Read the currently burning fires.",
            allowed_tools=("list_active_fires",),
            step_id="3",
            kind="direct_tool",
            direct_tool_name="list_active_fires",
            direct_tool_kwargs={},
        ),
    )


def _bind_report_active_fires(event: dict) -> tuple[Step, ...]:
    """Read-only list of burning fires, optionally filtered to one area."""

    area = (event.get("area") or "").strip()
    kwargs = {"area": area} if area else {}
    return (
        Step(
            agent_name="team_status_agent",
            task_text="List currently burning fires from the fires registry after two-day stale-expiry.",
            allowed_tools=("list_active_fires",),
            step_id="1",
            kind="direct_tool",
            direct_tool_name="list_active_fires",
            direct_tool_kwargs=kwargs,
        ),
    )


def _bind_dispatch_mutual_aid(event: dict) -> tuple[Step, ...]:
    """Ask the mutual-aid specialist to pick a force kind, or hold if area is missing."""

    area = (event.get("area") or "").strip()
    description = _report_text(event)
    missing = tuple(name for name in ("area",) if not area)
    kinds = ", ".join(sorted(FORCE_BASES))
    if missing:
        return (
            Step(
                agent_name="neighboring_forces_agent",
                task_text="Dispatch the requested mutual-aid force, bound directly from the event's extracted fields.",
                allowed_tools=("dispatch_neighboring_force",),
                step_id="1",
                required_event_fields=missing,
                kind="direct_tool",
                direct_tool_name="dispatch_neighboring_force",
                direct_tool_kwargs={},
            ),
        )
    return (
        Step(
            agent_name="neighboring_forces_agent",
            task_text=(
                f"Dispatch the requested mutual-aid force to '{area}'. You MUST call "
                f"dispatch_neighboring_force exactly once. kind must be one of: {kinds}. "
                f"unit_count defaults to 1 unless the report states a number. This is never "
                f"Ashed 3, Carmel 1, or any station apparatus (those use report_apparatus_movement).\n\n"
                f"Report: {description}"
            ),
            allowed_tools=("dispatch_neighboring_force",),
            step_id="1",
            invocation_policy=_FAST_JUDGMENT_POLICY,
        ),
    )


PROTOCOLS = [
    Protocol(
        name="record_crew_availability_response",
        description=(
            "Applies when a firefighting crew member reports their own availability for a "
            "shift -- e.g. leaving for a medical checkup, returning from one, or any other "
            "reason they will or will not be on duty; does not apply to a commander asking "
            "about the crew's overall roster (use report_crew_status for that), and does not "
            "apply to a commander's blanket shift declaration for the whole crew (use "
            "record_crew_shift_status for that)."
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
            "not apply to a request for a read-only roster picture (use report_crew_status for that), "
            "and does not apply to a single member's own availability report (use "
            "record_crew_availability_response for that)."
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
            "Applies when any crew member (not only a commander) reports one named station "
            "apparatus's own dispatch or movement -- Ashed 3 or Carmel 1 leaving the station, "
            "en route to a call, or no longer available there -- use update_apparatus_status "
            "for that engine, and, if the report clearly states it is dispatched to a specific "
            "incident there (not merely relocated), also call join_incident_response for the "
            "same area. If the report also asks who/what else is responding, also call "
            "list_incident_responders. Does not apply to water tankers, firefighting aircraft, "
            "police cordons, or ambulances (use dispatch_mutual_aid for those). Does not apply "
            "to a commander's blanket declaration of multiple members' shift availability (use "
            "record_crew_shift_status for that), and does not apply to a read-only status "
            "question (use report_crew_status for that)."
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
            "record_crew_availability_response for that). Does not apply to a question about "
            "which fires are currently burning (use report_active_fires for that)."
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
            "smoke/glare, thermal confusion, a frozen picture, or a similar equipment-status "
            "observation -- including camera 02 at the quarry junction and camera 03 at pine "
            "ridge. Camera identifiers belong in entities; call update_camera_observation once "
            "per named camera. Does not apply to what a camera *shows* about an actual fire "
            "(use report_fire_incident for that)."
        ),
        participating_agents=("surveillance_agent",),
        approved_tools=("update_camera_observation",),
        expected_success_output="Confirmation that the camera's observation/status was recorded.",
        criticality=CriticalityLevel.MEDIUM,
        # This only records an idempotent equipment observation; it dispatches
        # no resource and must not leave routine group reports awaiting approval.
        approval_flag=False,
        requires_confirmation=False,
        commander_only=False,
        needs_insight=False,
        direct_tool_binder=_bind_update_camera_observation,
    ),
    Protocol(
        name="dispatch_drone_to_incident",
        description=(
            "Applies when someone explicitly asks to send aerial drone recon to a fire that is "
            "already known or being monitored -- confirming a smoke sighting already on the "
            "log, or checking fire proximity to a hazardous structure after the fire itself was "
            "reported. Refreshes that fire's last_updated on the fires registry so the two-day "
            "stale-expiry clock restarts. Does not apply to the first report of a new active "
            "fire (use report_fire_incident for that). Does not apply to a routine camera/sensor "
            "status update with no active fire (use update_camera_observation for that). Does "
            "not apply to an already-extinguished roadside brush fire with no remaining risk "
            "(use log_fire_observation for that)."
        ),
        participating_agents=("team_status_agent", "surveillance_agent"),
        approved_tools=("touch_active_fire", "dispatch_drone_to_area"),
        expected_success_output=(
            "Confirmation of drone dispatch to the reported location, with callsign, ETA, and mission ID."
        ),
        criticality=CriticalityLevel.MEDIUM,
        approval_flag=True,
        requires_confirmation=False,
        commander_only=False,
        needs_insight=False,
        direct_tool_binder=_bind_dispatch_drone_to_incident,
    ),
    Protocol(
        name="report_fire_incident",
        description=(
            "Applies to a first report of an active or escalating fire -- smoke or flame first "
            "detected on pine ridge or Route 444, spread into new terrain (a tree line, a "
            "structure, the industrial park, the chemical plant), or a reported casualty/trapped "
            "person. Records the fire as burning in the fires registry and confirms or monitors "
            "it by tasking a drone to the reported area. Does not apply to a follow-up request "
            "that only asks to send a drone after the fire is already on the log (use "
            "dispatch_drone_to_incident for that). Does not apply to a question about which "
            "fires are currently burning (use report_active_fires for that). Does not apply to a "
            "routine, already-resolved, no-risk report (e.g. a small roadside fire already "
            "extinguished with no risk to structures -- use log_fire_observation for that). "
            "Does not apply to camera equipment status (use update_camera_observation for that). "
            "Does not apply to dispatching water tankers, aircraft, police, or ambulance (use "
            "dispatch_mutual_aid for that), and does not apply to moving Ashed 3 or Carmel 1 "
            "(use report_apparatus_movement for that). Does not apply when the report says the "
            "fire is already contained (use close_contained_fire for that) or that the report "
            "was a false alarm, including a false trapped-persons or Oranim Street alarm (use "
            "correct_false_fire_report for that)."
        ),
        participating_agents=("team_status_agent", "surveillance_agent"),
        approved_tools=("record_fire_status", "dispatch_drone_to_area"),
        expected_success_output="Confirmation that the fire was recorded as burning and a drone was dispatched to confirm/monitor it.",
        criticality=CriticalityLevel.HIGH,
        approval_flag=True,
        requires_confirmation=True,
        commander_only=False,
        needs_insight=False,
        direct_tool_binder=_bind_report_fire_incident,
        # A field report can arrive in any group, not only the camera-ops channel.
        safety_critical=True,
    ),
    Protocol(
        # Already-resolved reports have no dispatch tool at all, so the agent cannot over-send.
        name="log_fire_observation",
        description=(
            "Applies when a report describes a fire-related observation that is explicitly "
            "already resolved, extinguished, or presents no further risk -- e.g. a small "
            "roadside fire already extinguished with no risk to structures, including a "
            "Route 444 brush fire that a patrol already has on scene. Records the fire as "
            "extinguished in the fires registry; never dispatches a drone or any other "
            "resource. Does not apply to anything still active, escalating, or unconfirmed "
            "(use report_fire_incident for that)."
        ),
        participating_agents=("team_status_agent",),
        approved_tools=("record_fire_status",),
        expected_success_output="Confirmation that the fire was recorded as extinguished.",
        criticality=CriticalityLevel.LOW,
        approval_flag=False,
        requires_confirmation=False,
        commander_only=False,
        needs_insight=False,
        direct_tool_binder=_bind_log_fire_observation,
        safety_critical=True,
    ),
    Protocol(
        name="close_contained_fire",
        description=(
            "Applies when a report says the fire is contained, under control, or no longer "
            "spreading -- for example the incident is contained. Records the fire extinguished "
            "when the area is known and notifies the station commander. Does not ask which "
            "camera to check and does not dispatch mutual aid. Does not apply to a new active "
            "fire (use report_fire_incident for that)."
        ),
        participating_agents=("team_status_agent",),
        approved_tools=("record_fire_status", "post_operational_notice"),
        expected_success_output="The fire was recorded as contained and the station commander was notified.",
        criticality=CriticalityLevel.MEDIUM,
        approval_flag=False,
        requires_confirmation=False,
        commander_only=False,
        needs_insight=False,
        direct_tool_binder=_bind_close_contained_fire,
        viewer_reply_key="firefighting.reply.incident_contained",
        operational_notice_key="firefighting.notice.incident_contained",
        safety_critical=True,
    ),
    Protocol(
        name="correct_false_fire_report",
        description=(
            "Applies when a fire report is retracted as a false alarm -- no fire, trapped "
            "persons were not actually trapped, or a mistaken alarm on Oranim Street. Records "
            "the fire extinguished when the area is known and notifies the station commander. "
            "Does not ask which camera to check. Does not apply to a fire that is still burning "
            "(use report_fire_incident for that)."
        ),
        participating_agents=("team_status_agent",),
        approved_tools=("record_fire_status", "post_operational_notice"),
        expected_success_output="The false alarm was recorded and the station commander was notified.",
        criticality=CriticalityLevel.MEDIUM,
        approval_flag=False,
        requires_confirmation=False,
        commander_only=False,
        needs_insight=False,
        direct_tool_binder=_bind_correct_false_fire_report,
        viewer_reply_key="firefighting.reply.false_alarm",
        operational_notice_key="firefighting.notice.false_alarm",
        retracts_precedent=True,
        safety_critical=True,
    ),
    Protocol(
        name="report_active_fires",
        description=(
            "Applies when someone asks which fires are currently burning, where fires are now, "
            "or which fires are still alight -- a read of the fires registry after the two-day "
            "stale-expiry (a fire with no update for two days is treated as extinguished). Does "
            "not apply to the first report of a new fire (use report_fire_incident for that). "
            "Does not apply to camera snapshots or equipment status (use "
            "update_camera_observation or overall_situational_picture for those). Does not "
            "apply to a retrospective debrief of a closed incident (use "
            "query_historical_incidents for that)."
        ),
        participating_agents=("team_status_agent",),
        approved_tools=("list_active_fires",),
        expected_success_output="A read-only list of currently burning fires from the fires registry.",
        criticality=CriticalityLevel.LOW,
        approval_flag=False,
        requires_confirmation=False,
        commander_only=False,
        needs_insight=False,
        direct_tool_binder=_bind_report_active_fires,
        direct_lane_eligible=True,
    ),
    Protocol(
        name="dispatch_mutual_aid",
        description=(
            "Applies when a report requires dispatching a real external firefighting resource -- "
            "water-tanker trucks, firefighting aircraft, a police cordon for evacuation, or an "
            "ambulance -- to a location such as pine ridge, the industrial park, or the chemical "
            "plant. Does not apply to this station's own engines Ashed 3 and Carmel 1 (use "
            "report_apparatus_movement for those). Does not apply to a mere report or recon "
            "request with no dispatch decision yet (use report_fire_incident or "
            "overall_situational_picture first for those)."
        ),
        participating_agents=("neighboring_forces_agent",),
        approved_tools=("dispatch_neighboring_force",),
        expected_success_output="Confirmation that the requested mutual-aid resource(s) were dispatched.",
        criticality=CriticalityLevel.HIGH,
        approval_flag=True,
        requires_confirmation=True,
        commander_only=True,
        needs_insight=False,
        direct_tool_binder=_bind_dispatch_mutual_aid,
    ),
    Protocol(
        name="overall_situational_picture",
        description=(
            "Applies when a commander asks for a combined snapshot spanning the crew's "
            "roster/vehicle availability, currently burning fires, and the camera/surveillance "
            "picture in one request -- e.g. 'what's our force and vehicle availability' or "
            "'urgent picture: exact fire location and crew status'. Also applies when a "
            "commander describes multiple critical hot spots or reports at once and asks to "
            "prioritize response or allocate crews/water -- pull the actual current records "
            "rather than trusting the commander's own recap of what was reported earlier. Does "
            "not apply when only crew/apparatus is asked about (use report_crew_status). Does "
            "not apply when only currently burning fires are asked about (use report_active_fires)."
        ),
        participating_agents=("surveillance_agent", "team_status_agent"),
        approved_tools=("get_surveillance_overview", "report_team_availability", "list_active_fires"),
        expected_success_output="One combined report covering current camera status, crew availability, and burning fires.",
        criticality=CriticalityLevel.LOW,
        approval_flag=False,
        requires_confirmation=False,
        commander_only=False,
        needs_insight=False,
        direct_tool_binder=_bind_overall_situational_picture,
    ),
    Protocol(
        name="query_historical_incidents",
        description=(
            "Applies when a commander asks for a retrospective, end-to-end debrief of an "
            "incident already underway or closed -- a timeline, resource management, which "
            "reports turned out to be false alarms, or guidance for residents returning home; "
            "does not apply to a question about the current, live state of the crew, cameras, "
            "or currently burning fires (use overall_situational_picture, report_crew_status, "
            "or report_active_fires for that)."
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

__all__ = [
    "PROTOCOLS",
    "_as_aware_iso",
    "_bind_apparatus_movement",
    "_bind_dispatch_drone",
    "_bind_dispatch_drone_to_incident",
    "_bind_dispatch_mutual_aid",
    "_bind_log_fire_observation",
    "_bind_record_crew_availability",
    "_bind_record_crew_shift_status",
    "_bind_report_active_fires",
    "_bind_report_fire_incident",
    "_bind_update_camera_observation",
]
