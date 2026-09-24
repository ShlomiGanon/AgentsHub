"""Response Team (SEC) specialist agents (docs/bar_improves.md Stage 4): security
observation logging, team notification and friendly-force dispatch coordination, drone
recall, and camera-fault handling.

No external system integration in this task: every side-effecting tool here records what
it did and returns a precise text result stating only the tool's own recorded effect —
never an unobserved real-world outcome ("friendly-force dispatch request recorded for
west_gate", never "force dispatched" or "force arrived"). That result is persisted by the
existing step-execution recording, which is what makes the request durable and readable
later."""

from agents.runtime import Agent, tool


class SecurityOpsAgent(Agent):
    name = "security_ops_agent"
    role = (
        "The response team's operations specialist: logs perimeter and external-force "
        "observations, records that a team notification was issued, and records requests to "
        "dispatch a friendly force or recall the drone. Every tool here only records what this "
        "system was told or asked to do — it never contacts a real team member, vehicle, or "
        "drone, and never confirms that a dispatched force or a recalled drone actually "
        "arrived."
    )
    system_prompt = (
        "You are the security operations agent. You have four tools: log_observation records an "
        "observation note for a named area; notify_team records that a team notification was "
        "issued about a named area; request_friendly_force_dispatch records a request to "
        "dispatch a friendly force to a named area; recall_drone records a request to recall "
        "the drone to base. None of these tools contacts a real person, vehicle, or drone — "
        "each only logs that the action was requested, for this system's own record-keeping. "
        "Use only the tool the task actually asks for, and report back plainly, in one sentence "
        "describing only what was recorded — never a claim about what happened in the field."
    )

    def __init__(self, model: str, api_key: str | None = None):
        self.observations_logged: list[str] = []
        self.notifications_issued: list[str] = []
        self.dispatch_requests: list[str] = []
        self.drone_recalls: list[str] = []
        super().__init__(model, api_key)

    @tool(
        "log_observation",
        "Records an observation note about something seen at or near a named area. "
        "Side-effecting and idempotent — recording the identical note for the same area twice "
        "leaves one record.",
        side_effecting=True,
        idempotent=True,
    )
    def log_observation(self, area: str, note: str) -> str:
        entry = f"{area}: {note}"
        if entry not in self.observations_logged:
            self.observations_logged.append(entry)
        return f"observation note recorded for '{area}'"

    @tool(
        "notify_team",
        "Records that a notification was issued to the response team about a named area. "
        "Side-effecting and not idempotent — running it twice records two notifications, not "
        "one.",
        side_effecting=True,
        idempotent=False,
    )
    def notify_team(self, area: str, message: str = "") -> str:
        self.notifications_issued.append(f"{area}: {message}" if message else area)
        return f"team notification recorded for '{area}'"

    @tool(
        "request_friendly_force_dispatch",
        "Records a request to dispatch a friendly force to a named area. Side-effecting and not "
        "idempotent — running it twice records two dispatch requests, not one.",
        side_effecting=True,
        idempotent=False,
    )
    def request_friendly_force_dispatch(self, area: str, note: str = "") -> str:
        record = f"{area}: {note}" if note else area
        self.dispatch_requests.append(record)
        return f"friendly-force dispatch request recorded for '{area}'"

    @tool(
        "recall_drone",
        "Records a request to recall the drone to base. Side-effecting and not idempotent — "
        "running it twice records two recall requests, not one.",
        side_effecting=True,
        idempotent=False,
    )
    def recall_drone(self, note: str = "") -> str:
        self.drone_recalls.append(note or "recall requested")
        return "drone recall request recorded"


class SurveillanceFaultAgent(Agent):
    name = "surveillance_agent"
    role = (
        "The response team's surveillance-fault specialist: records a fault for each reported "
        "camera identifier and can request a technician. Every tool here only records what was "
        "reported — it never inspects or repairs a real camera, and never confirms a technician "
        "actually attended."
    )
    system_prompt = (
        "You are the surveillance fault agent. You have two tools: log_camera_fault records a "
        "fault for one camera identifier at a time — call it once per camera identifier "
        "reported (e.g. call it once for CAM-03 and once again for CAM-04 if a single report "
        "names both); request_technician records a request for a technician to attend. Record "
        "the physical observation reported (what was seen — e.g. 'cable physically cut, camera "
        "offline'), and keep any stated cause or suspicion as the reporter's own claim, never as "
        "confirmed fact. Neither tool inspects or repairs a real camera — each only logs that "
        "the fault/request was recorded. Report back plainly what was recorded, never a claim "
        "that the camera was fixed or inspected."
    )

    def __init__(self, model: str, api_key: str | None = None):
        self.faults_logged: list[str] = []
        self.technician_requests: list[str] = []
        super().__init__(model, api_key)

    @tool(
        "log_camera_fault",
        "Records a fault for one named camera identifier (e.g. 'CAM-03'). Side-effecting and "
        "idempotent — recording the identical camera's fault twice leaves one record. Call it "
        "once per camera identifier when a report names more than one.",
        side_effecting=True,
        idempotent=True,
    )
    def log_camera_fault(self, camera_id: str, note: str = "") -> str:
        entry = f"{camera_id}: {note}" if note else camera_id
        if entry not in self.faults_logged:
            self.faults_logged.append(entry)
        return f"camera fault recorded for '{camera_id}'"

    @tool(
        "request_technician",
        "Records a request for a technician to attend a named camera fault. Side-effecting and "
        "not idempotent — running it twice records two requests, not one.",
        side_effecting=True,
        idempotent=False,
    )
    def request_technician(self, camera_id: str = "", note: str = "") -> str:
        if camera_id and note:
            record = f"{camera_id}: {note}"
        else:
            record = camera_id or note or "technician requested"
        self.technician_requests.append(record)
        suffix = f" for '{camera_id}'" if camera_id else ""
        return f"technician request recorded{suffix}"
