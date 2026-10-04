"""Response Team protocol declarations and direct-tool binders."""

from agents import InvocationPolicy
from protocols import CriticalityLevel, Protocol, Step
from protocols import as_aware_iso as _as_aware_iso
from protocols import bind_record_attendance_response

# Binders skip formulate_tasks by using extracted event fields only.
# Unknown parameters stay in required_event_fields so the usual hold fires.


def _bind_record_attendance(event: dict) -> tuple[Step, ...]:
    """Same shared attendance binder as firefighting, with this profile's roster agent."""

    return bind_record_attendance_response(
        event,
        agent_name="roster_agent",
        task_text="Record the reporter's own attendance/availability response, bound directly from the event's extracted fields.",
    )


def _report_text(event: dict) -> str:
    """Prefer the extracted description, then the original report text."""

    return (event.get("description") or event.get("raw_text") or "").strip()


def _bind_update_camera_status(event: dict) -> tuple[Step, ...]:
    """One camera-status step per named camera, or a missing-fields hold."""

    entities = event.get("entities") or []
    description = _report_text(event)
    if not entities:
        return (
            Step(
                agent_name="surveillance_agent",
                task_text="Record the reported camera(s) status, bound directly from the event's extracted fields.",
                allowed_tools=("update_camera_status",),
                step_id="1",
                required_event_fields=("entities",),
                kind="direct_tool",
                direct_tool_name="update_camera_status",
                direct_tool_kwargs={},
            ),
        )
    return tuple(
        Step(
            agent_name="surveillance_agent",
            task_text=(
                f"You MUST call update_camera_status exactly once for camera {camera_id} and for "
                f"no other camera. Do not skip the tool call. Map the report to status: offline "
                f"(no signal, physically down, cut cable, communications cut), degraded "
                f"(intermittent, glare, smoke-blinded, flaky, heat-alert), or active (back online, "
                f"restored, working again). Observation is a short restatement of what was reported "
                f"for this camera only.\n\nReport: {description}"
            ),
            allowed_tools=("update_camera_status",),
            step_id=str(index + 1),
        )
        for index, camera_id in enumerate(entities)
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
            "incident_description": description or "Reported security incident",
            "mission_type": "recon",
        }
    return (
        Step(
            agent_name="surveillance_agent",
            task_text="Dispatch a recon drone to the reported area, bound directly from the event's extracted fields.",
            allowed_tools=("dispatch_drone_to_area",),
            step_id="1",
            required_event_fields=missing,
            kind="direct_tool",
            direct_tool_name="dispatch_drone_to_area",
            direct_tool_kwargs=kwargs,
        ),
    )


def _notice_step(agent_name: str, notice_key: str, task_text: str) -> Step:
    """One catalog notice, with no camera check and no resource dispatch."""

    return Step(
        agent_name=agent_name,
        task_text=task_text,
        allowed_tools=("post_operational_notice",),
        step_id="1",
        kind="direct_tool",
        direct_tool_name="post_operational_notice",
        direct_tool_kwargs={"notice_key": notice_key},
    )


def _bind_log_security_observation(event: dict) -> tuple[Step, ...]:
    """Record an already-handled observation. Never dispatches a drone."""

    return (
        Step(
            agent_name="surveillance_agent",
            task_text="Log the already-handled observation. Do not dispatch a drone or any other resource.",
            allowed_tools=("log_security_observation",),
            step_id="1",
            kind="direct_tool",
            direct_tool_name="log_security_observation",
            direct_tool_kwargs={"note": _report_text(event)},
        ),
    )


def _bind_armed_threat(event: dict) -> tuple[Step, ...]:
    """Drone, police, YASAM, and this site's squad, all to the extracted area."""

    area = (event.get("area") or "").strip()
    description = _report_text(event) or "Armed life-threatening incident"
    missing = tuple(name for name in ("area",) if not area)
    if missing:
        return (
            Step(
                agent_name="surveillance_agent",
                task_text="The armed-threat response needs the reported area.",
                allowed_tools=("dispatch_drone_to_area",),
                step_id="1",
                required_event_fields=missing,
                kind="direct_tool",
                direct_tool_name="dispatch_drone_to_area",
                direct_tool_kwargs={},
            ),
        )
    note = description
    return (
        Step(
            agent_name="surveillance_agent",
            task_text="Dispatch a recon drone to the armed-threat area.",
            allowed_tools=("dispatch_drone_to_area",),
            step_id="1",
            kind="direct_tool",
            direct_tool_name="dispatch_drone_to_area",
            direct_tool_kwargs={
                "target_area": area,
                "incident_description": note,
                "mission_type": "recon",
            },
        ),
        Step(
            agent_name="neighboring_forces_agent",
            task_text="Dispatch police to the armed-threat area.",
            allowed_tools=("dispatch_neighboring_force",),
            step_id="2",
            kind="direct_tool",
            direct_tool_name="dispatch_neighboring_force",
            direct_tool_kwargs={"kind": "police", "target_area": area, "unit_count": 1, "note": note},
        ),
        Step(
            agent_name="neighboring_forces_agent",
            task_text="Dispatch YASAM to the armed-threat area.",
            allowed_tools=("dispatch_neighboring_force",),
            step_id="3",
            kind="direct_tool",
            direct_tool_name="dispatch_neighboring_force",
            direct_tool_kwargs={"kind": "yasam", "target_area": area, "unit_count": 1, "note": note},
        ),
        Step(
            agent_name="neighboring_forces_agent",
            task_text="Dispatch this site's own squad to the armed-threat area.",
            allowed_tools=("dispatch_squad",),
            step_id="4",
            kind="direct_tool",
            direct_tool_name="dispatch_squad",
            direct_tool_kwargs={"target_area": area, "unit_count": 1, "note": note},
        ),
    )


def _bind_correct_false_security_report(event: dict) -> tuple[Step, ...]:
    """Retract a false report. Do not ask which camera to check."""

    return (
        _notice_step(
            "roster_agent",
            "response_team.notice.false_gunfire",
            "Record that the earlier security report was false and notify the operational update.",
        ),
    )


def _bind_close_security_incident(event: dict) -> tuple[Step, ...]:
    """Record that the incident is under control. Do not ask which camera to check."""

    return (
        _notice_step(
            "roster_agent",
            "response_team.notice.incident_closed",
            "Record that the incident is under control and notify the operational update.",
        ),
    )


def _bind_query_situational_picture(event: dict) -> tuple[Step, ...]:
    """Read roster, cameras, and neighboring-force dispatches. No specialist model call."""

    return (
        Step(
            agent_name="roster_agent",
            task_text="Read the current team availability.",
            allowed_tools=("report_team_availability",),
            step_id="1",
            kind="direct_tool",
            direct_tool_name="report_team_availability",
            direct_tool_kwargs={},
        ),
        Step(
            agent_name="surveillance_agent",
            task_text="Read the current surveillance overview.",
            allowed_tools=("get_surveillance_overview",),
            step_id="2",
            kind="direct_tool",
            direct_tool_name="get_surveillance_overview",
            direct_tool_kwargs={},
        ),
        Step(
            agent_name="neighboring_forces_agent",
            task_text="Read the current neighboring-force dispatches.",
            allowed_tools=("list_neighboring_force_dispatches",),
            step_id="3",
            kind="direct_tool",
            direct_tool_name="list_neighboring_force_dispatches",
            direct_tool_kwargs={},
        ),
    )


def _bind_dispatch_own_squad(event: dict) -> tuple[Step, ...]:
    """Direct-tool squad dispatch when area is known; otherwise a missing-fields hold."""

    area = (event.get("area") or "").strip()
    description = _report_text(event)
    missing = tuple(name for name in ("area",) if not area)
    kwargs: dict = {}
    if not missing:
        kwargs = {
            "target_area": area,
            "unit_count": 1,
            "note": description,
        }
    return (
        Step(
            agent_name="neighboring_forces_agent",
            task_text="Dispatch this site's own response-team roster to the reported area, bound directly from the event's extracted fields.",
            allowed_tools=("dispatch_squad",),
            step_id="1",
            required_event_fields=missing,
            kind="direct_tool",
            direct_tool_name="dispatch_squad",
            direct_tool_kwargs=kwargs,
        ),
    )


# A narrow, low-stakes judgment call (decide whether a movement report also indicates incident
# response, then call up to three known tools) never needs the agent's default reasoning budget --
# same mechanism SurveillanceAgent.process already uses for its own tool-turn-plus-summary calls.
_FAST_JUDGMENT_POLICY = InvocationPolicy(max_output_tokens=400, reasoning_effort="none")


def _bind_report_team_movement(event: dict) -> tuple[Step, ...]:
    """Record the reporter's area, then let the specialist judge incident linkage."""

    area = (event.get("area") or "").strip()
    description = _report_text(event)
    missing = tuple(name for name in ("area",) if not area)
    if missing:
        return (
            Step(
                agent_name="roster_agent",
                task_text="Record the reporter's own current area, bound directly from the event's extracted fields.",
                allowed_tools=("report_team_movement",),
                step_id="1",
                required_event_fields=missing,
                kind="direct_tool",
                direct_tool_name="report_team_movement",
                direct_tool_kwargs={},
            ),
        )
    # Whether this movement is a response to a specific incident is a genuine judgment call
    # from the free-text report, the same class of decision `_bind_update_camera_status` makes
    # for a camera's resulting status -- so the specialist agent decides and calls the relevant
    # tool(s) itself rather than a keyword heuristic pre-deciding it.
    return (
        Step(
            agent_name="roster_agent",
            task_text=(
                f"The reporter's own current area was reported as '{area}'. Call report_team_movement "
                f"with that area. Then, from the report below, decide: does it clearly say the reporter "
                f"is responding to, heading to, or dispatched to a specific incident at that area (not "
                f"merely stationed or passing through)? If so, also call join_incident_response for the "
                f"same area. If the report also asks who else is with/responding, also call "
                f"list_incident_responders for the same area and include its answer in your reply.\n\n"
                f"Report: {description}"
            ),
            allowed_tools=("report_team_movement", "join_incident_response", "list_incident_responders"),
            step_id="1",
            invocation_policy=_FAST_JUDGMENT_POLICY,
        ),
    )


# -- Protocols ----------------------------------------------------------------
# Each description names what it covers and which sibling to use instead.

PROTOCOLS = [
    Protocol(
        name="record_attendance",
        description=(
            "Applies when a response-team member reports their own attendance/availability "
            "status for the current or an upcoming period -- available, or unavailable with a "
            "reason and, once known, a day count. Does not apply to a member reporting their "
            "current location while still on duty (use report_team_movement for that), and does "
            "not apply to a commander asking about the team's overall roster (use "
            "query_situational_picture for that)."
        ),
        participating_agents=("roster_agent",),
        approved_tools=("record_attendance_response",),
        expected_success_output="Confirmation that the member's attendance response was recorded.",
        criticality=CriticalityLevel.LOW,
        approval_flag=False,
        requires_confirmation=False,
        commander_only=False,
        # Phase A: parameters bound straight from extracted fields, no model call for the
        # step itself. Recording a report exactly as given is correct behavior, not something
        # that needs a model's insight/judgment.
        needs_insight=False,
        direct_tool_binder=_bind_record_attendance,
        direct_lane_eligible=True,
    ),
    Protocol(
        name="update_camera_status",
        description=(
            "Applies when a technician or operator reports one or more named cameras' own "
            "operating condition -- offline, degraded, back online, a physically cut "
            "communications cable, frozen frame, or intermittent reception -- including when "
            "two cameras (for example CAM-01 and CAM-02) are reported down in the same message. "
            "Camera identifiers belong in entities; call update_camera_status once per named "
            "camera. Does not apply to what a camera shows about a hostile or suspicious event "
            "(use report_security_incident for that). Record the physical observation reported "
            "(what was seen); any stated cause or suspicion from the reporter is the reporter's "
            "own claim, never recorded as fact."
        ),
        participating_agents=("surveillance_agent",),
        approved_tools=("update_camera_status",),
        expected_success_output="Confirmation that each reported camera's status/observation was recorded.",
        criticality=CriticalityLevel.MEDIUM,
        approval_flag=False,
        requires_confirmation=False,
        commander_only=False,
        needs_insight=False,
        direct_tool_binder=_bind_update_camera_status,
        direct_lane_eligible=True,
    ),
    Protocol(
        name="report_security_incident",
        description=(
            "Applies to a report of an unconfirmed hostile, suspicious, or still-relevant "
            "security event -- a suspicious vehicle or person, gunfire, a sighted armed suspect, "
            "an intrusion, or a breach in the perimeter fence -- confirmed or monitored by "
            "tasking a drone to the reported area for recon. Does not apply when the report "
            "itself says the situation is already handled, resolved, or presents no further risk "
            "(use log_security_observation for that -- never dispatch a drone for an "
            "already-handled report). Does not apply to a plain camera/sensor equipment-status "
            "observation with no security implication, including dual-camera outages or a cut "
            "cable (use update_camera_status for that). Does not apply to sending this site's "
            "own roster/squad (use dispatch_own_squad for that). Does not apply to a request to "
            "send an external neighboring force (use dispatch_neighboring_force for that). "
            "Does not apply to an armed suspect, active life-threatening gunfire, or a person "
            "on a roof with a weapon or a dark object (use respond_armed_threat for that). "
            "Does not apply to a correction that an earlier gunfire report was false (use "
            "correct_false_security_report for that). Does not apply to a report that the "
            "incident is under control or closed (use close_security_incident for that)."
        ),
        participating_agents=("surveillance_agent",),
        approved_tools=("dispatch_drone_to_area",),
        expected_success_output="Confirmation of drone dispatch to the reported area (callsign, ETA, mission ID).",
        criticality=CriticalityLevel.HIGH,
        approval_flag=False,
        requires_confirmation=False,
        commander_only=False,
        needs_insight=False,
        direct_tool_binder=_bind_dispatch_drone,
        direct_lane_eligible=True,
        # A field report can arrive in any group, not only the camera-ops channel.
        safety_critical=True,
    ),
    Protocol(
        # Already-handled reports have no dispatch tool at all, so the agent cannot over-send.
        name="log_security_observation",
        description=(
            "Applies when a report describes a security-relevant observation that is explicitly "
            "already handled, resolved, or presents no further risk -- e.g. a small fire that is "
            "already out, with no firefighter kind involved, or a suspicious situation that has "
            "already been resolved or cleared. Purely informational: logs the observation: never "
            "dispatches a drone or any other resource. Does not apply to anything still active, "
            "ongoing, or unconfirmed (use report_security_incident for that)."
        ),
        participating_agents=("surveillance_agent",),
        approved_tools=("log_security_observation",),
        expected_success_output="A plain acknowledgement that the observation was logged.",
        criticality=CriticalityLevel.LOW,
        approval_flag=False,
        requires_confirmation=False,
        commander_only=False,
        needs_insight=False,
        direct_tool_binder=_bind_log_security_observation,
        viewer_reply_key="response_team.reply.observation_logged",
        safety_critical=True,
    ),
    Protocol(
        name="respond_armed_threat",
        description=(
            "Applies to an armed, life-threatening security event: an armed suspect, active "
            "gunfire that endangers people, or a person on a roof of the old public building "
            "holding a weapon or a dark object. Sends a drone, police, YASAM, and this site's "
            "own squad to the reported area in one response. Does not apply to an already-handled "
            "observation (use log_security_observation). Does not apply to unconfirmed recon that "
            "needs only a drone (use report_security_incident). Does not apply to a false-report "
            "correction (use correct_false_security_report) or to an incident already under "
            "control (use close_security_incident)."
        ),
        participating_agents=("surveillance_agent", "neighboring_forces_agent"),
        approved_tools=(
            "dispatch_drone_to_area",
            "dispatch_neighboring_force",
            "dispatch_squad",
        ),
        expected_success_output="Police, YASAM, a drone, and the site squad were sent to the area.",
        criticality=CriticalityLevel.HIGH,
        approval_flag=False,
        requires_confirmation=False,
        commander_only=False,
        needs_insight=False,
        direct_tool_binder=_bind_armed_threat,
        viewer_reply_key="response_team.reply.armed_threat",
        safety_critical=True,
    ),
    Protocol(
        name="correct_false_security_report",
        description=(
            "Applies when a report retracts an earlier security report as false -- for example "
            "gunfire at the west gate that was not gunfire, a false alarm, or a clarification "
            "that the earlier report is no longer relevant. Records the correction and sends a "
            "short operational update. Does not check cameras and does not ask which camera to "
            "review. Does not apply to a still-active threat (use respond_armed_threat)."
        ),
        participating_agents=("roster_agent",),
        approved_tools=("post_operational_notice",),
        expected_success_output="The false report was corrected and the operational update was sent.",
        criticality=CriticalityLevel.MEDIUM,
        approval_flag=False,
        requires_confirmation=False,
        commander_only=False,
        needs_insight=False,
        direct_tool_binder=_bind_correct_false_security_report,
        viewer_reply_key="response_team.reply.false_report",
        operational_notice_key="response_team.notice.false_gunfire",
        safety_critical=True,
    ),
    Protocol(
        name="close_security_incident",
        description=(
            "Applies when a report says the incident is under control, contained, or closed -- "
            "for example 'the event is under control'. Records the closure and alerts the site "
            "security officer and the response-team group. Does not ask which camera to check "
            "and does not dispatch a drone. Does not apply to a new or still-active threat "
            "(use respond_armed_threat or report_security_incident)."
        ),
        participating_agents=("roster_agent",),
        approved_tools=("post_operational_notice",),
        expected_success_output="The incident was recorded as under control and the operational update was sent.",
        criticality=CriticalityLevel.MEDIUM,
        approval_flag=False,
        requires_confirmation=False,
        commander_only=False,
        needs_insight=False,
        direct_tool_binder=_bind_close_security_incident,
        viewer_reply_key="response_team.reply.incident_closed",
        operational_notice_key="response_team.notice.incident_closed",
        safety_critical=True,
    ),
    Protocol(
        name="dispatch_neighboring_force",
        description=(
            "Applies when a report requires dispatching a real neighboring/external force -- "
            "ambulance, police, K9, or YASAM (YAMAG folds into YASAM) -- to an area, most "
            "commonly a casualty needing medical response, a confirmed threat needing a "
            "police/YASAM response, or a search needing a K9 unit. Does not apply to sending "
            "this site's own response-team roster, squad, or members (use dispatch_own_squad "
            "for that -- never kind=squad here). Does not apply to a mere report or recon "
            "request with no dispatch decision yet (use report_security_incident first for that)."
        ),
        participating_agents=("neighboring_forces_agent",),
        approved_tools=("dispatch_neighboring_force",),
        expected_success_output="Confirmation that the requested neighboring force was dispatched, en route, with its ETA.",
        criticality=CriticalityLevel.HIGH,
        approval_flag=False,
        requires_confirmation=False,
        commander_only=False,
    ),
    Protocol(
        name="dispatch_own_squad",
        description=(
            "Applies when a report requires sending this site's own response-team roster, "
            "squad, or members to an area -- our people, not police, ambulance, K9, or YASAM. "
            "Does not apply to a neighboring/external force (use dispatch_neighboring_force "
            "for that). Does not apply to drone recon of an unconfirmed hostile event (use "
            "report_security_incident for that)."
        ),
        participating_agents=("neighboring_forces_agent",),
        approved_tools=("dispatch_squad",),
        expected_success_output="Confirmation that the site's own squad was dispatched, en route, with its ETA.",
        criticality=CriticalityLevel.HIGH,
        approval_flag=False,
        requires_confirmation=False,
        commander_only=False,
        needs_insight=False,
        direct_tool_binder=_bind_dispatch_own_squad,
        direct_lane_eligible=True,
        safety_critical=True,
    ),
    Protocol(
        name="report_team_movement",
        description=(
            "Applies when a team member reports their own movement or current position -- e.g. "
            "travelling to or arriving at an area -- while still on duty. Does not apply to a "
            "member reporting they will be unavailable (use record_attendance for that)."
        ),
        participating_agents=("roster_agent",),
        approved_tools=("report_team_movement", "join_incident_response", "list_incident_responders"),
        expected_success_output=(
            "Confirmation that the team member's current area was recorded; if the report also "
            "indicated they are responding to a specific incident, confirmation they were linked "
            "to it and, if asked, who else is currently linked to that same incident."
        ),
        criticality=CriticalityLevel.LOW,
        approval_flag=False,
        requires_confirmation=False,
        commander_only=False,
        needs_insight=False,
        direct_tool_binder=_bind_report_team_movement,
        direct_lane_eligible=True,
    ),
    Protocol(
        name="report_team_availability",
        description=(
            "Applies when someone asks only for the current readiness-team attendance list -- "
            "who is available, unavailable, or still awaiting a report. Does not apply to a "
            "combined picture that also asks about cameras or neighboring forces (use "
            "query_situational_picture for that)."
        ),
        participating_agents=("roster_agent",),
        approved_tools=("report_team_availability",),
        expected_success_output="The current name-by-name attendance list from the roster store.",
        criticality=CriticalityLevel.LOW,
        approval_flag=False,
        requires_confirmation=False,
        commander_only=False,
        needs_insight=False,
        direct_lane_eligible=True,
    ),
    Protocol(
        name="query_situational_picture",
        description=(
            "Applies when someone asks for a combined, current snapshot spanning any of the "
            "team's roster/attendance, the camera picture, or neighboring-force dispatch status "
            "-- e.g. 'who's missing tonight and what's the camera status', or 'anything moving, "
            "and where are the responding forces'. Also applies when a confused or urgent "
            "message recaps several recent reports (possibly from different chats) and asks to "
            "make sense of them and/or decide where to send the available force -- pull the "
            "actual records rather than trusting the requester's own recap. Does not apply to a "
            "retrospective, end-to-end summary of a closed or ongoing incident (use "
            "query_incident_summary for that)."
        ),
        participating_agents=("roster_agent", "surveillance_agent", "neighboring_forces_agent"),
        approved_tools=("report_team_availability", "get_surveillance_overview", "list_neighboring_force_dispatches"),
        expected_success_output="One combined report covering whichever of roster, camera, and dispatch status was asked about.",
        criticality=CriticalityLevel.LOW,
        approval_flag=False,
        requires_confirmation=False,
        commander_only=False,
        needs_insight=False,
        direct_tool_binder=_bind_query_situational_picture,
    ),
    Protocol(
        name="query_incident_summary",
        description=(
            "Applies when a commander asks for a retrospective, end-to-end summary of an "
            "incident already underway or closed -- a timeline, which reports turned out to be "
            "false alarms, casualty/roster status, or a message to relay to residents. Does not "
            "apply to a question about the current, live state of the team, cameras, or "
            "dispatches (use query_situational_picture for that)."
        ),
        participating_agents=("history_agent",),
        approved_tools=(),
        expected_success_output="A faithful, chronological summary of the incident drawn only from recorded events.",
        criticality=CriticalityLevel.LOW,
        approval_flag=False,
        requires_confirmation=False,
        commander_only=False,
    ),
]

__all__ = [
    "PROTOCOLS",
    "_as_aware_iso",
    "_bind_dispatch_drone",
    "_bind_dispatch_own_squad",
    "_bind_record_attendance",
    "_bind_report_team_movement",
    "_bind_update_camera_status",
]
