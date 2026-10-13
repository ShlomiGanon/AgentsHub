"""The faster message paths keep the same answers with fewer model round trips: a read-only
question or picture reads each specialist's live state directly, one planner call classifies a
message and routes it, and a report's fast-lane decision rides on that same call."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from agents import Agent, get_authenticated_request_identity, tool
from agents.contracts import AgentResult, ToolResult
from agents.runtime import build_agent_registry
from history import HistoryAnswer
from history.query import HistoryQueryError
from messages import get_catalog, set_current_catalog
from orchestrator import flows_direct_lane
from orchestrator.flows_direct_lane import (
    attempt_direct_lane,
    direct_lane_plan_section,
    direct_lane_result_from_payload,
)
from orchestrator.reasoning import AgentSelectionResult, answer_question, answer_question_from_plan, plan_message
from orchestrator.reasoning_report import _build_intent_prompt, _parse_structured_intent_response
from orchestrator.situational_picture import RECENT_EVENTS_DOMAIN, build_situational_picture, direct_read_tools
from protocols import CriticalityLevel, Protocol

NOW = datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)
AREA_LABELS = {"east_fence": "the east fence", "west_gate": "the west gate"}


@pytest.fixture(autouse=True)
def _english_catalog():
    """English catalog."""
    set_current_catalog(get_catalog("en"))


class _BoardAgent(Agent):
    """A specialist whose whole live state is one argument-free read."""

    name = "board_agent"
    role = "Keeps the live board of cameras and drones."
    system_prompt = "fake"
    snapshot_tools = ("get_board",)

    def __init__(self, board: str = "Cameras: CAM-01 east_fence ACTIVE. Drones: DRONE-01 READY at west_gate."):
        """Initialize with the board text the read returns."""
        super().__init__("test-model")
        self.board = board
        self.process_calls: list[str] = []
        self.read_identities: list[str | None] = []

    @tool("get_board", "Returns the whole live board.", side_effecting=False)
    def get_board(self, area: str = "") -> str:
        """Return the board."""
        self.read_identities.append(get_authenticated_request_identity())
        return self.board

    @tool("move_drone", "Moves a drone to an area.", side_effecting=True, idempotent=False)
    def move_drone(self, area: str) -> str:
        """Move a drone."""
        return f"moved to {area}"

    def process(self, text, allowed_tools, *, invocation_policy=None):
        """Record a model call; the fast paths must not make one."""
        self.process_calls.append(text)
        return AgentResult("success", "model answer")


class _RosterAgent(_BoardAgent):
    """A second specialist with its own argument-free read."""

    name = "roster_agent"
    role = "Keeps the roster."
    snapshot_tools = ("get_roster",)

    @tool("get_roster", "Returns who is available.", side_effecting=False)
    def get_roster(self) -> str:
        """Return the roster."""
        return "Roster: Dan available, Noa unavailable (sick)."


class _NoSnapshotAgent(_BoardAgent):
    """A specialist that declares no snapshot, so it is still asked through its model."""

    name = "plain_agent"
    snapshot_tools = ()


class _ComposingMainAgent:
    """Main Agent that answers every prompt in order from a script and records the prompts."""

    def __init__(self, *responses: str):
        """Initialize with the scripted responses."""
        self.responses = list(responses)
        self.prompts: list[str] = []
        self.policies: list[object] = []

    def process(self, text, allowed_tools, *, invocation_policy=None):
        """Return the next scripted response."""
        self.prompts.append(text)
        self.policies.append(invocation_policy)
        if not self.responses:
            raise AssertionError(f"unexpected Main Agent call: {text[:120]!r}")
        return AgentResult("success", self.responses.pop(0))


class _History:
    """History service with area names and recent reports."""

    def __init__(self, recent=None, spec_error: HistoryQueryError | None = None):
        """Initialize."""
        self.area_labels = dict(AREA_LABELS)
        self.recent = list(recent or [])
        self.spec_error = spec_error
        self.spec_calls = []

    def planning_context(self):
        """Planning context."""
        return {"current_time_local": "2026-09-10T15:00:00+03:00", "timezone": "Asia/Jerusalem", "areas": sorted(AREA_LABELS)}

    def operational_context(self, **kwargs):
        """Recent reports."""
        return list(self.recent)

    def query_spec(self, question, spec, *, sender_identity_filter=None):
        """Stored-history answer."""
        self.spec_calls.append((question, spec))
        if self.spec_error is not None:
            raise self.spec_error
        return HistoryAnswer("The last report was smoke at the west gate.", (), spec.time_start, spec.time_end, 1)


# --- snapshot declaration ------------------------------------------------------------------------

def test_a_snapshot_must_name_read_only_tools():
    """Declaring a state-changing tool as a snapshot read is refused at construction."""

    class _Bad(_BoardAgent):
        name = "bad_agent"
        snapshot_tools = ("move_drone",)

    with pytest.raises(TypeError, match="snapshot_tools must name read-only tools"):
        _Bad()


def test_read_snapshot_joins_every_snapshot_tool_and_reports_failure():
    """The snapshot is the text of every declared read; a failed read marks it unusable."""

    agent = _BoardAgent()
    snapshot = agent.read_snapshot()
    assert snapshot.ok and "CAM-01 east_fence ACTIVE" in snapshot.text

    class _Failing(_BoardAgent):
        name = "failing_agent"

        @tool("get_board", "Returns the whole live board.", side_effecting=False)
        def get_board(self, area: str = "") -> str | ToolResult:
            return ToolResult("store unavailable", ok=False)

    assert _Failing().read_snapshot().ok is False
    assert _NoSnapshotAgent().read_snapshot() is None


# --- questions -----------------------------------------------------------------------------------

def test_a_routed_question_reads_the_snapshot_and_composes_without_a_specialist_model_call():
    """The specialist's live state goes to the composer as raw data, with the area names."""

    board = _BoardAgent()
    registry = build_agent_registry({}, [board])
    main_agent = _ComposingMainAgent(
        "ROUTE: normal",
        json.dumps({"route": "agents", "tasks": [{"agent_name": "board_agent", "task": "check cameras"}], "reason": "live"}),
        "Camera CAM-01 at the east fence is active.",
    )

    answer = answer_question(main_agent, "is the east fence covered?", registry, _History())

    assert answer == "Camera CAM-01 at the east fence is active."
    assert board.process_calls == []
    compose_prompt = main_agent.prompts[-1]
    assert "CAM-01 east_fence ACTIVE" in compose_prompt
    assert '"east_fence": "the east fence"' in compose_prompt
    assert "never by its internal id" in compose_prompt


def test_an_agent_without_a_snapshot_is_still_asked_through_its_model():
    """No snapshot declared means the old specialist path, unchanged."""

    plain = _NoSnapshotAgent()
    registry = build_agent_registry({}, [plain])
    selection = AgentSelectionResult(status="selected", chosen_tasks={"plain_agent": "check cameras"}, reason="live")
    main_agent = _ComposingMainAgent("composed")

    answer = answer_question_from_plan(main_agent, "cameras?", selection, registry, _History())

    assert answer.text == "composed"
    assert len(plain.process_calls) == 1


def test_a_planned_question_with_no_fitting_agent_is_answered_from_recent_reports():
    """A 'none' route composes from what people reported instead of showing the router's reason."""

    registry = build_agent_registry({}, [_BoardAgent()])
    selection = AgentSelectionResult(status="none", reason="no agent knows about planned works (internal)")
    recent = [{"time": "14:00", "message": "road works at the west gate tomorrow", "area": "the west gate"}]
    main_agent = _ComposingMainAgent("Road works were reported at the west gate for tomorrow.")

    answer = answer_question_from_plan(main_agent, "is anything planned at the gate?", selection, registry, _History(recent))

    assert answer.text == "Road works were reported at the west gate for tomorrow."
    assert "internal" not in answer.text
    assert "road works at the west gate tomorrow" in main_agent.prompts[0]


def test_a_planned_history_question_with_no_stored_match_falls_back_to_recent_reports():
    """An empty stored history is not the end of the answer when recent reports exist."""

    registry = build_agent_registry({}, [_BoardAgent()])
    spec_payload = {
        "route": "history", "reason": "past",
        "history_query": {
            "operation": "latest", "time_start": None, "time_end": None, "time_basis": "received_at",
            "classifications": [], "areas": [], "outcomes": [], "protocol_names": [], "event_ids": [],
            "risk_levels": [], "order": "newest", "group_by": "none", "limit": 1,
        },
    }
    from orchestrator.reasoning_qa import _parse_agent_selection_response

    selection = _parse_agent_selection_response(json.dumps(spec_payload))
    history = _History(
        recent=[{"time": "14:00", "message": "smoke at the west gate"}],
        spec_error=HistoryQueryError("no stored events match", empty=True),
    )
    main_agent = _ComposingMainAgent("Smoke was reported at the west gate at 14:00.")

    answer = answer_question_from_plan(main_agent, "what just happened?", selection, registry, history)

    assert answer.text == "Smoke was reported at the west gate at 14:00."


# --- situational picture -------------------------------------------------------------------------

def _picture_protocol(*agents: str, approved: tuple[str, ...]) -> Protocol:
    """A picture protocol over the given agents and tools."""
    return Protocol(
        name="query_situational_picture", description="picture", participating_agents=agents,
        approved_tools=approved, expected_success_output="picture", criticality=CriticalityLevel.LOW,
        approval_flag=False,
    )


def test_a_picture_over_argument_free_reads_skips_planning_and_specialist_models():
    """Every domain is read straight from its tools under the caller's identity, then composed once."""

    board, roster = _BoardAgent(), _RosterAgent()
    registry = build_agent_registry({}, [board, roster])
    protocol = _picture_protocol("board_agent", "roster_agent", approved=("get_board", "get_roster"))
    main_agent = _ComposingMainAgent("Picture: CAM-01 active at the east fence; Dan available.")

    picture = build_situational_picture(
        main_agent, protocol, registry, _History(), "picture please",
        caller_identity="commander-1", sender_identity_filter=None, now=NOW,
    )

    assert picture.text == "Picture: CAM-01 active at the east fence; Dan available."
    assert len(main_agent.prompts) == 1  # composition only: no planning call
    assert board.process_calls == [] and roster.process_calls == []
    assert board.read_identities == ["commander-1"]
    compose_prompt = main_agent.prompts[0]
    assert "CAM-01 east_fence ACTIVE" in compose_prompt and "Noa unavailable (sick)" in compose_prompt
    assert '"east_fence": "the east fence"' in compose_prompt
    assert [d["domain"] for d in picture.provenance()["domains"]] == ["board_agent", "roster_agent", RECENT_EVENTS_DOMAIN]
    assert picture.provenance()["planned_by_model"] is False


def test_a_picture_tool_that_needs_an_argument_or_changes_state_keeps_the_planned_path():
    """Only argument-free, read-only approved tools are read directly."""

    registry = build_agent_registry({}, [_BoardAgent(), _RosterAgent()])
    assert direct_read_tools(_picture_protocol("board_agent", approved=("get_board",)), registry) == {"board_agent": ("get_board",)}
    assert direct_read_tools(_picture_protocol("board_agent", approved=("get_board", "move_drone")), registry) is None
    # An agent with none of the approved tools could only be asked through its model.
    assert direct_read_tools(_picture_protocol("board_agent", "roster_agent", approved=("get_roster",)), registry) is None


def test_a_failed_direct_read_is_reported_unavailable_and_the_rest_still_compose():
    """A domain whose read fails is named as unavailable, never filled in."""

    class _Broken(_RosterAgent):
        name = "broken_roster"

        @tool("get_roster", "Returns who is available.", side_effecting=False)
        def get_roster(self) -> str:
            raise RuntimeError("roster store locked")

    registry = build_agent_registry({}, [_BoardAgent(), _Broken()])
    protocol = _picture_protocol("board_agent", "broken_roster", approved=("get_board", "get_roster"))
    main_agent = _ComposingMainAgent("Picture: CAM-01 active.")

    picture = build_situational_picture(
        main_agent, protocol, registry, _History(), "picture", caller_identity="c", sender_identity_filter=None, now=NOW,
    )

    domains = {d["domain"]: d["succeeded"] for d in picture.provenance()["domains"]}
    assert domains["board_agent"] is True and domains["broken_roster"] is False
    assert "broken_roster" in picture.text  # the missing-domain note


# --- intent fields -------------------------------------------------------------------------------

def test_intent_parsing_no_longer_needs_the_unused_quoted_and_hypothetical_flags():
    """The evidence quote and the consistency flags are still checked; the dropped flags are not asked for."""

    payload = {
        "primary_intent": "report", "asks_for_information": False, "reports_occurrence": True,
        "requests_action": False, "social_only": False, "is_followup_without_context": False,
        "evidence": {"report": "smoke at the gate"}, "matched_protocol_names": [], "reason": "a sighting",
        "ambiguity_reason": None, "clarification_question": None,
    }
    result = _parse_structured_intent_response(json.dumps(payload), "I see smoke at the gate", ())
    assert result.intent == "report"

    prompt = _build_intent_prompt("I see smoke at the gate", ())
    assert "is_quoted" not in prompt and "is_hypothetical" not in prompt
    assert '"evidence"' in prompt and "is_followup_without_context" in prompt

    payload["evidence"] = {"report": "a fire at the depot"}
    with pytest.raises(Exception, match="not present in the message"):
        _parse_structured_intent_response(json.dumps(payload), "I see smoke at the gate", ())


# --- merged planner and fast lane ----------------------------------------------------------------

def _lane_protocol() -> Protocol:
    """A fast-lane protocol for moving a drone."""
    return Protocol(
        name="move_drone_protocol", description="move a drone", participating_agents=("board_agent",),
        approved_tools=("move_drone",), expected_success_output="moved", criticality=CriticalityLevel.LOW,
        approval_flag=False, direct_lane_eligible=True,
    )


def _report_plan(fast_lane) -> dict:
    """A merged plan for a report."""
    return {
        "primary_intent": "report", "asks_for_information": False, "reports_occurrence": True,
        "requests_action": False, "social_only": False, "is_followup_without_context": False,
        "evidence": {"report": "drone to the west gate"}, "matched_protocol_names": [], "reason": "a report",
        "ambiguity_reason": None, "clarification_question": None, "question_plan": None,
        "conversational_reply": None, "fast_lane": fast_lane,
    }


def test_the_merged_plan_carries_the_shared_rules_and_the_fast_lane_decision():
    """One call classifies with the same rules as the stand-alone classifier and decides the fast lane."""

    registry = build_agent_registry({}, [_BoardAgent()])
    section = direct_lane_plan_section((_lane_protocol(),), registry)
    fast_lane = {"eligible": True, "actions": [{"tool_name": "move_drone", "parameters": {"area": "west_gate"}}]}
    main_agent = _ComposingMainAgent(json.dumps(_report_plan(fast_lane)))
    recent = [{"time": "14:00", "message": "motorbike at the east fence"}]

    plan = plan_message(
        main_agent, (_lane_protocol(),), "send the drone to the west gate", registry, _History(),
        operational_context=recent, fast_lane_section=section,
    )

    assert plan.intent.intent == "report"
    assert plan.fast_lane == fast_lane
    prompt = main_agent.prompts[0]
    assert "NEEDS_CLARIFICATION applies only when" in prompt  # the shared intent rules
    assert "Cover every independently requested fact" in prompt  # the shared routing rules
    assert "move_drone: Moves a drone to an area." in prompt and "When in doubt, return not eligible" in prompt
    assert "motorbike at the east fence" in prompt
    assert main_agent.policies[0].response_schema is None  # free-form tool parameters


def test_a_planned_fast_lane_decision_is_validated_and_saves_the_triage_call(monkeypatch):
    """The planner's decision runs through the same validation; no separate triage call is made."""

    registry = build_agent_registry({}, [_BoardAgent()])
    deps = SimpleNamespace(protocol_set=SimpleNamespace(all=lambda: (_lane_protocol(),)), registry=registry)
    ran = []
    monkeypatch.setattr(flows_direct_lane, "run_direct_lane", lambda deps, event_id, sender, result: ran.append(result) or "ran")

    class _NoCalls:
        def process(self, *args, **kwargs):
            raise AssertionError("the fast lane was already decided by the planner")

    accepted = attempt_direct_lane(
        deps, _NoCalls(), "evt-1", "user-1", "send the drone to the west gate",
        planned={"eligible": True, "actions": [{"tool_name": "move_drone", "parameters": {"area": "west_gate"}}]},
    )
    assert accepted == "ran"
    assert ran[0].actions[0].tool_name == "move_drone"

    invented = attempt_direct_lane(
        deps, _NoCalls(), "evt-2", "user-1", "x",
        planned={"eligible": True, "actions": [{"tool_name": "launch_everything", "parameters": {}}]},
    )
    assert invented is None
    declined = attempt_direct_lane(deps, _NoCalls(), "evt-3", "user-1", "x", planned={"eligible": False, "reason": "threat"})
    assert declined is None
    assert len(ran) == 1


def test_a_fast_lane_parameter_the_tool_does_not_accept_declines_the_lane():
    """A parameter the tool does not take sends the report to the full pipeline."""

    registry = build_agent_registry({}, [_BoardAgent()])
    _lines, owner = flows_direct_lane._direct_lane_tools((_lane_protocol(),), registry)
    result = direct_lane_result_from_payload(
        {"eligible": True, "actions": [{"tool_name": "move_drone", "parameters": {"altitude": 30}}]}, owner, registry,
    )
    assert result.eligible is False
