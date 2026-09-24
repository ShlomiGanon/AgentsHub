"""Acceptance scenarios for the two operational profiles, run offline and
deterministically through the real API with the model boundary faked
(docs/bar_improves.md Stage 6; the SEC scenarios below were retargeted to
profiles/response_team.py's rebuilt protocol/agent/tool set by
docs/responce_improve.md). These verify pipeline wiring — classification
routing, required-field gating, holds, approval, persisted fields,
notifications — never model quality; nothing here asserts model wording.

The Main/Insights Agents are always scripted stand-ins (`ScriptedAgent`, the
same technique every other API-level test in this suite uses). The
participating specialist agents (`roster_agent`, `surveillance_agent`,
`neighboring_forces_agent`, `dispatch_agent`, `hazmat_agent`) are REAL, real
profile instances, invoked through a small keyword-dispatch fake `crewai.Agent`
local to this file only (not the shared `tests/api_fakes.py` fixture, which
always returns one fixed canned string regardless of task) — this is what
lets a step's persisted `result_text` deterministically reflect the task it
was actually given, so a scenario can assert e.g. "the step result says a
dispatch request was recorded" without depending on a real model's wording.

Every profiles/response_team.py protocol declares `approval_flag=False`
(docs/responce_improve.md), so none of the SEC scenarios below exercise an
approval hold the way the deleted profiles/standby_squad.py's
`external_force_response` protocol once did -- `query_situational_picture`
below instead exercises this profile's one three-agent protocol.
"""

import dataclasses
import types

import pytest

from agents import AgentModelError, AgentTimeoutError
from agents.history import HistoryAgent
from agents.runtime import build_agent_registry
from api.app import build_app
from api.operations import job_status
from config.base import TierModel
from orchestrator.flows import resume_after_event_data
from profiles import build_area_registry, build_event_type_registry
from profiles.loader import load_profile
from protocols.loader import ProtocolSet
from tests.api_fakes import COMMANDER_IDENTITY, VIEWER_IDENTITY, FakeResult, ScriptedAgent, auth_headers, build_context

CORE_MODEL = TierModel(model="openai/test-core-model", api_key="test-key")
SUB_MODEL = TierModel(model="openai/test-sub-model", api_key="test-key")


class _KeywordCrewAgent:
    """Local, file-scoped fake `crewai.Agent` — a real specialist agent's
    `.process()` ultimately calls this via `agents.runtime.invoke`. Returns a
    deterministic 'recorded: <task>' echo of the task text it was given
    (fully under this test file's own control, since the task text is
    itself produced by a scripted Main Agent formulation response below) —
    never a real model call, and no tool is actually invoked (the same
    limitation the shared `tests/api_fakes.py`/other integration tests'
    fixed-string fake crewai already has); good enough to prove a step's
    persisted result reflects what it was actually tasked with doing.
    """

    def __init__(self, **kwargs):
        pass

    def kickoff(self, text):
        return types.SimpleNamespace(raw=f"recorded: {text}")


@pytest.fixture(autouse=True)
def _mock_crewai(monkeypatch):
    from agents import adapter

    fake_module = types.SimpleNamespace(
        Agent=_KeywordCrewAgent, LLM=lambda **kwargs: kwargs["model"], tools=types.SimpleNamespace(BaseTool=object)
    )
    monkeypatch.setattr(adapter, "_get_crewai", lambda: fake_module)


@pytest.fixture(autouse=True)
def _bot_tokens(monkeypatch):
    monkeypatch.setenv("RESPONSE_TEAM_BOT_TOKEN", "response-team-test-token")
    monkeypatch.setenv("FIRE_STATION_BOT_TOKEN", "fire-station-test-token")


@pytest.fixture
def teardown_ctx():
    contexts = []
    yield contexts
    for ctx in contexts:
        ctx.queue.stop()
        ctx.deps.persistence.close()


_USERS = (
    (VIEWER_IDENTITY, "viewer"),
    (COMMANDER_IDENTITY, "commander"),
    ("michael", "viewer"),
    ("gil", "viewer"),
)


def _operational_ctx(tmp_path, module_path, main_agent):
    """A real `api.app.ApiContext` wired to one of this task's real
    operational profiles' real protocols/agents/registries — everything
    `tests/api_fakes.build_context` already builds (real SQLitePersistence,
    a real queue, a real settings store), with only the protocol/agent/
    registry slice swapped for the real profile's own declared content, the
    same `dataclasses.replace` technique `tests/test_api_messages.py`'s own
    `_ctx_with_protocol_flags` already uses to customize a context without
    touching the shared fixture."""

    loaded = load_profile(module_path, CORE_MODEL, SUB_MODEL)
    ctx = build_context(tmp_path, main_agent=main_agent, users=_USERS)
    history_agent = ctx.deps.registry.get("history_agent")
    registry = build_agent_registry({"history_agent": history_agent}, list(loaded.agents))
    new_deps = dataclasses.replace(
        ctx.deps,
        registry=registry,
        protocol_set=ProtocolSet(protocols=loaded.protocols),
        event_type_registry=build_event_type_registry(loaded),
        area_registry=build_area_registry(loaded),
    )
    return dataclasses.replace(ctx, deps=new_deps)


def _sec_ctx(tmp_path, teardown_ctx, main_agent):
    ctx = _operational_ctx(tmp_path, "profiles.response_team", main_agent)
    teardown_ctx.append(ctx)
    return ctx


def _fire_ctx(tmp_path, teardown_ctx, main_agent):
    ctx = _operational_ctx(tmp_path, "profiles.fire_station", main_agent)
    teardown_ctx.append(ctx)
    return ctx


def _extraction(**overrides) -> str:
    import json

    payload = {
        "classification": None, "area": None, "entities": [], "description": None, "severity": None,
        "occurred_at": "2026-09-10T10:00:00", "availability_start": None, "availability_end": None,
        "absence_reason": None,
    }
    payload.update(overrides)
    return json.dumps(payload)


def _submit(client, identity, text):
    resp = client.post("/Event", headers=auth_headers(identity), json={"text": text, "sender_identity": identity})
    assert resp.status_code == 202
    return resp.get_json()["event_id"]


def _single_agent_formulation(agent_name: str, task: str) -> str:
    return f"AGENT: {agent_name}\nTASK: {task}"


def _two_agent_formulation(first_agent: str, first_task: str, second_agent: str, second_task: str) -> str:
    import json

    return json.dumps(
        {
            "steps": [
                {"step_id": "s1", "agent_name": first_agent, "task": first_task, "depends_on": [], "required_event_fields": []},
                {"step_id": "s2", "agent_name": second_agent, "task": second_task, "depends_on": [], "required_event_fields": []},
            ]
        }
    )


def _three_agent_formulation(
    first_agent: str, first_task: str, second_agent: str, second_task: str, third_agent: str, third_task: str
) -> str:
    import json

    return json.dumps(
        {
            "steps": [
                {"step_id": "s1", "agent_name": first_agent, "task": first_task, "depends_on": [], "required_event_fields": []},
                {"step_id": "s2", "agent_name": second_agent, "task": second_task, "depends_on": [], "required_event_fields": []},
                {"step_id": "s3", "agent_name": third_agent, "task": third_task, "depends_on": [], "required_event_fields": []},
            ]
        }
    )


_VERDICT_SUCCESS = "VERDICT: success\nREASONING: matches expected output"


# == SEC scenarios (profiles.response_team) =================================


def test_scenario_1_absence_without_interval_then_reply_fills_it_and_resumes(tmp_path, teardown_ctx):
    agent = ScriptedAgent(
        {
            "Extract this operational event": _extraction(
                classification="attendance", absence_reason="family matter",
            ),
            "Write one concise question": "When does this start and end?",
        }
    )
    ctx = _sec_ctx(tmp_path, teardown_ctx, agent)
    client = build_app(ctx).test_client()

    event_id = _submit(client, "michael", "Michael: I won't be available, family matter")
    ctx.queue.wait_until_idle()

    status = job_status(ctx, event_id)
    assert status["status"] == "waiting_for_event_data"
    event = ctx.deps.persistence.fetch_event(event_id)
    assert event["classification"] == "attendance"
    assert event["absence_reason"] == "family matter"
    assert event["availability_start"] is None
    assert event["availability_end"] is None
    [hold] = ctx.deps.persistence.list_held_events("event_data")
    assert set(hold["missing_fields"]) == {"availability_start", "availability_end"}

    notifications = build_app(ctx).test_client().get("/Notifications", headers=auth_headers(COMMANDER_IDENTITY)).get_json()
    [notification] = notifications["notifications"]
    assert notification["kind"] == "event_data_hold"
    assert notification["target_chat_ids"] == ["michael"]

    # Michael replies with an interval — resolved the same way
    # tests/test_orchestrator_flows.py's Stage 3 tests already exercise
    # (POST /Event carries no conversation_id, so there is no HTTP-level
    # conversational reply path for an /Event-originated hold — the same
    # limitation tests/test_api_holds.py's own `_make_clarification_hold`
    # documents and works around identically).
    ctx.deps.persistence.update_event(
        event_id, {"availability_start": "2026-09-15T00:00:00", "availability_end": "2026-09-17T00:00:00"}
    )
    agent._dispatch.update(
        {
            "RISK_SCORE": "RISK_SCORE: 0.1\nREASON: routine",
            "Choose the protocol": "SELECTED: record_attendance\nREASON: fits",
            "participating in the": _single_agent_formulation("roster_agent", "record michael's reported absence"),
            "VERDICT:": _VERDICT_SUCCESS,
        }
    )
    resumed = resume_after_event_data(ctx.deps, event_id, ctx.main_agent, ctx.insights_agent)

    assert resumed.outcome == "succeeded"
    event = ctx.deps.persistence.fetch_event(event_id)
    assert event["selected_protocol"] == "record_attendance"
    assert event["availability_start"] == "2026-09-15T00:00:00"
    assert event["availability_end"] == "2026-09-17T00:00:00"


def test_scenario_2_camera_interference_near_the_east_fence(tmp_path, teardown_ctx):
    agent = ScriptedAgent(
        {
            "Extract this operational event": _extraction(
                classification="camera_status", area="east_fence", entities=["CAM-01"],
                description="CAM-01 shows intermittent reception interference",
            ),
            "RISK_SCORE": "RISK_SCORE: 0.2\nREASON: routine equipment observation",
            "Choose the protocol": "SELECTED: update_camera_status\nREASON: camera operating-condition report",
            "participating in the": _single_agent_formulation(
                "surveillance_agent", "record CAM-01's status/observation at east_fence"
            ),
            "VERDICT:": _VERDICT_SUCCESS,
        }
    )
    ctx = _sec_ctx(tmp_path, teardown_ctx, agent)
    client = build_app(ctx).test_client()

    event_id = _submit(client, VIEWER_IDENTITY, "CAM-01 shows intermittent reception interference")
    ctx.queue.wait_until_idle()

    event = ctx.deps.persistence.fetch_event(event_id)
    assert event["classification"] == "camera_status"
    assert event["area"] == "east_fence"
    assert event["selected_protocol"] == "update_camera_status"
    assert job_status(ctx, event_id)["status"] == "succeeded"

    # Visible to a commander's history question — the history agent answers
    # only from what is actually stored, never invented.
    stored = ctx.deps.persistence.fetch_events_range("2000-01-01T00:00:00", "2100-01-01T00:00:00")
    assert event_id in {row["event_id"] for row in stored}


def test_scenario_3_team_member_in_transit(tmp_path, teardown_ctx):
    agent = ScriptedAgent(
        {
            "Extract this operational event": _extraction(
                classification="team_movement", area="east_orchards", description="Gil is travelling to east_orchards",
            ),
            "RISK_SCORE": "RISK_SCORE: 0.1\nREASON: routine",
            "Choose the protocol": "SELECTED: report_team_movement\nREASON: own movement report",
            "participating in the": _single_agent_formulation("roster_agent", "record Gil's movement to east_orchards"),
            "VERDICT:": _VERDICT_SUCCESS,
        }
    )
    ctx = _sec_ctx(tmp_path, teardown_ctx, agent)
    client = build_app(ctx).test_client()

    event_id = _submit(client, "gil", "Gil: on my way to the eastern orchards")
    ctx.queue.wait_until_idle()

    event = ctx.deps.persistence.fetch_event(event_id)
    assert event["classification"] == "team_movement"
    assert event["area"] == "east_orchards"
    assert event["selected_protocol"] == "report_team_movement"
    assert job_status(ctx, event_id)["status"] == "succeeded"


def test_scenario_4_two_cameras_in_one_message(tmp_path, teardown_ctx):
    agent = ScriptedAgent(
        {
            "Extract this operational event": _extraction(
                classification="camera_status", area="east_fence", entities=["CAM-01", "CAM-02"],
                description="CAM-01 and CAM-02 are offline",
            ),
            "RISK_SCORE": "RISK_SCORE: 0.2\nREASON: equipment fault",
            "Choose the protocol": "SELECTED: update_camera_status\nREASON: two cameras reported offline",
            # This architecture formulates exactly one step per PARTICIPATING
            # agent (orchestrator.reasoning.formulate_tasks rejects more than
            # one step for the same agent) — a real model handling this task
            # would call update_camera_status twice within its one step, once
            # per camera identifier, exactly as
            # profiles.response_team.ResponseTeamSurveillanceAgent's
            # update_camera_status tool description instructs. What this test
            # can actually observe offline is the guarantee upstream of that:
            # extraction captured both identifiers, and the one formulated
            # task names both.
            "participating in the": _single_agent_formulation(
                "surveillance_agent", "record the status update for CAM-01 and the status update for CAM-02"
            ),
            "VERDICT:": _VERDICT_SUCCESS,
        }
    )
    ctx = _sec_ctx(tmp_path, teardown_ctx, agent)
    client = build_app(ctx).test_client()

    event_id = _submit(client, VIEWER_IDENTITY, "CAM-01 and CAM-02 are offline")
    ctx.queue.wait_until_idle()

    event = ctx.deps.persistence.fetch_event(event_id)
    assert event["classification"] == "camera_status"
    assert set(event["entities"]) == {"CAM-01", "CAM-02"}
    assert event["selected_protocol"] == "update_camera_status"
    [step] = event["steps"]
    assert step["agent_name"] == "surveillance_agent"
    assert "CAM-01" in step["task_text"] and "CAM-02" in step["task_text"]
    assert job_status(ctx, event_id)["status"] == "succeeded"


def test_scenario_5_cut_communications_cable(tmp_path, teardown_ctx):
    agent = ScriptedAgent(
        {
            "Extract this operational event": _extraction(
                classification="camera_status", area="east_fence", entities=["CAM-01"],
                description="cable physically cut, camera offline",
            ),
            "RISK_SCORE": "RISK_SCORE: 0.2\nREASON: equipment fault",
            "Choose the protocol": "SELECTED: update_camera_status\nREASON: physically cut camera cable",
            "participating in the": _single_agent_formulation("surveillance_agent", "record the status update for CAM-01"),
            "VERDICT:": _VERDICT_SUCCESS,
        }
    )
    ctx = _sec_ctx(tmp_path, teardown_ctx, agent)
    client = build_app(ctx).test_client()

    event_id = _submit(client, VIEWER_IDENTITY, "CAM-01's cable looks physically cut, they think someone did it")
    ctx.queue.wait_until_idle()

    event = ctx.deps.persistence.fetch_event(event_id)
    assert event["classification"] == "camera_status"
    assert "CAM-01" in event["entities"]
    # The physical observation, not the reporter's suspicion, is what is recorded as description.
    assert event["description"] == "cable physically cut, camera offline"
    assert job_status(ctx, event_id)["status"] == "succeeded"


def test_scenario_6_ambulance_dispatched_for_a_casualty(tmp_path, teardown_ctx):
    """docs/responce_improve.md: every profiles/response_team.py protocol declares
    approval_flag=False -- unlike the deleted profiles/standby_squad.py's
    external_force_response, a viewer's own force-dispatch report runs immediately,
    with no commander approval hold."""

    agent = ScriptedAgent(
        {
            "Extract this operational event": _extraction(
                classification="force_dispatch", area="expansion_neighborhood",
                description="casualty reported near the expansion neighborhood, ambulance needed",
            ),
            "RISK_SCORE": "RISK_SCORE: 0.8\nREASON: casualty reported",
            "Choose the protocol": "SELECTED: dispatch_neighboring_force\nREASON: casualty needs an ambulance",
            "participating in the": _single_agent_formulation(
                "neighboring_forces_agent", "dispatch an ambulance to expansion_neighborhood"
            ),
            "VERDICT:": _VERDICT_SUCCESS,
        }
    )
    ctx = _sec_ctx(tmp_path, teardown_ctx, agent)
    client = build_app(ctx).test_client()

    event_id = _submit(client, VIEWER_IDENTITY, "casualty near the expansion neighborhood, we need an ambulance")
    ctx.queue.wait_until_idle()

    final_status = job_status(ctx, event_id)
    assert final_status["status"] == "succeeded"  # never held — force_dispatch's approval_flag is False
    event = ctx.deps.persistence.fetch_event(event_id)
    assert event["classification"] == "force_dispatch"
    assert event["area"] == "expansion_neighborhood"
    assert event["selected_protocol"] == "dispatch_neighboring_force"
    [step] = event["steps"]
    assert "recorded" in step["result_text"]


def test_scenario_6_variant_combined_situational_picture(tmp_path, teardown_ctx):
    """query_situational_picture is this profile's one three-agent protocol
    (roster + surveillance + neighboring forces) — the SEC equivalent of the
    FIRE scenario below's two-agent hazmat_response, and of the deleted
    profiles/standby_squad.py's overall_situational_picture (two agents only)."""

    agent = ScriptedAgent(
        {
            "Extract this operational event": _extraction(classification="situational_query"),
            "RISK_SCORE": "RISK_SCORE: 0.1\nREASON: routine status request",
            "Choose the protocol": "SELECTED: query_situational_picture\nREASON: combined snapshot requested",
            "participating in the": _three_agent_formulation(
                "roster_agent", "report the current team availability",
                "surveillance_agent", "report the current surveillance overview",
                "neighboring_forces_agent", "list the current neighboring-force dispatches",
            ),
            "VERDICT:": _VERDICT_SUCCESS,
        }
    )
    ctx = _sec_ctx(tmp_path, teardown_ctx, agent)
    client = build_app(ctx).test_client()

    event_id = _submit(client, COMMANDER_IDENTITY, "what's our combined situational picture right now?")
    ctx.queue.wait_until_idle()

    assert job_status(ctx, event_id)["status"] == "succeeded"
    event = ctx.deps.persistence.fetch_event(event_id)
    assert event["selected_protocol"] == "query_situational_picture"
    assert {step["agent_name"] for step in event["steps"]} == {
        "roster_agent", "surveillance_agent", "neighboring_forces_agent",
    }


# == FIRE scenario (profiles.fire_station) ===================================


def test_scenario_7_hazardous_fire_viewer_report_held_for_approval(tmp_path, teardown_ctx):
    agent = ScriptedAgent(
        {
            "Extract this operational event": _extraction(
                classification="hazmat_fire", area="industrial_zone",
                description="fire reported at a chemical facility in the industrial zone",
            ),
            "RISK_SCORE": "RISK_SCORE: 0.9\nREASON: hazardous materials involved",
            "Choose the protocol": "SELECTED: hazmat_response\nREASON: fire at a chemical facility",
        }
    )
    ctx = _fire_ctx(tmp_path, teardown_ctx, agent)
    client = build_app(ctx).test_client()

    event_id = _submit(client, VIEWER_IDENTITY, "fire at the chemical facility in the industrial zone")
    ctx.queue.wait_until_idle()

    status = job_status(ctx, event_id)
    assert status["status"] == "held_for_approval"
    event = ctx.deps.persistence.fetch_event(event_id)
    assert event["classification"] == "hazmat_fire"
    assert event["area"] == "industrial_zone"
    assert event["steps"] in ([], None)  # no tool executed before approval

    agent._dispatch.update(
        {
            "participating in the": _two_agent_formulation(
                "dispatch_agent", "dispatch the station crew to industrial_zone",
                "hazmat_agent", "request a hazmat assessment for industrial_zone",
            ),
            "VERDICT:": _VERDICT_SUCCESS,
        }
    )
    resp = client.post(f"/Approve/{event_id}", headers=auth_headers(COMMANDER_IDENTITY), json={"decision": "approved"})
    assert resp.status_code == 202
    ctx.queue.wait_until_idle()

    assert job_status(ctx, event_id)["status"] == "succeeded"
    event = ctx.deps.persistence.fetch_event(event_id)
    assert {step["agent_name"] for step in event["steps"]} == {"dispatch_agent", "hazmat_agent"}


def test_scenario_7_variant_commanders_own_hazmat_report_bypasses_approval(tmp_path, teardown_ctx):
    """The existing commander-bypass rule (a commander's own request skips a
    flagged protocol's approval hold) still applies unchanged."""
    agent = ScriptedAgent(
        {
            "Extract this operational event": _extraction(
                classification="hazmat_fire", area="industrial_zone",
                description="fire reported at a chemical facility in the industrial zone",
            ),
            "RISK_SCORE": "RISK_SCORE: 0.9\nREASON: hazardous materials involved",
            "Choose the protocol": "SELECTED: hazmat_response\nREASON: fire at a chemical facility",
            "participating in the": _two_agent_formulation(
                "dispatch_agent", "dispatch the station crew to industrial_zone",
                "hazmat_agent", "request a hazmat assessment for industrial_zone",
            ),
            "VERDICT:": _VERDICT_SUCCESS,
        }
    )
    ctx = _fire_ctx(tmp_path, teardown_ctx, agent)
    client = build_app(ctx).test_client()

    event_id = _submit(client, COMMANDER_IDENTITY, "fire at the chemical facility in the industrial zone")
    ctx.queue.wait_until_idle()

    status = job_status(ctx, event_id)
    assert status["status"] == "succeeded"  # never held — the commander's own submission bypassed approval
    event = ctx.deps.persistence.fetch_event(event_id)
    assert {step["agent_name"] for step in event["steps"]} == {"dispatch_agent", "hazmat_agent"}


# == Robustness (either profile) — Stages 1 and 2 through the real pipeline =


class _TimeoutOnceThenScriptedAgent(ScriptedAgent):
    """Stage 2: raises `AgentTimeoutError` on the first call whose prompt
    matches the extraction keyword, then answers normally — proves the
    retry (orchestrator.flows._model_invoker_for) through the real pipeline,
    not just orchestrator.flows unit-level."""

    def __init__(self, dispatch):
        super().__init__(dispatch)
        self._extraction_attempts = 0

    def process(self, text, allowed_tools):
        if "Extract this operational event" in text:
            self._extraction_attempts += 1
            if self._extraction_attempts == 1:
                raise AgentTimeoutError("main_agent", "timed out")
        return super().process(text, allowed_tools)


def test_scenario_8_extraction_times_out_once_then_succeeds(tmp_path, teardown_ctx):
    agent = _TimeoutOnceThenScriptedAgent(
        {
            "Extract this operational event": _extraction(
                classification="team_movement", area="access_road", description="Gil is on site",
            ),
            "RISK_SCORE": "RISK_SCORE: 0.1\nREASON: routine",
            "Choose the protocol": "SELECTED: report_team_movement\nREASON: own movement report",
            "participating in the": _single_agent_formulation("roster_agent", "record Gil's presence at access_road"),
            "VERDICT:": _VERDICT_SUCCESS,
        }
    )
    ctx = _sec_ctx(tmp_path, teardown_ctx, agent)
    client = build_app(ctx).test_client()

    event_id = _submit(client, "gil", "Gil: at the access road")
    ctx.queue.wait_until_idle()

    assert agent._extraction_attempts == 2  # exactly one retry
    status = job_status(ctx, event_id)
    assert status["status"] == "succeeded"
    event = ctx.deps.persistence.fetch_event(event_id)
    assert event["classification"] == "team_movement"


def test_scenario_9_malformed_optional_field_proceeds_with_it_null(tmp_path, teardown_ctx):
    import json

    malformed_extraction = json.dumps(
        {
            "classification": "security_incident", "area": "south_corner",
            "entities": [], "description": "unusual activity reported near the south corner",
            # Stage 1: severity is malformed (an object, not a string) — the
            # report must still proceed, with severity dropped to null.
            "severity": {"level": "unclear"},
            "occurred_at": "2026-09-10T10:00:00",
        }
    )
    agent = ScriptedAgent(
        {
            "Extract this operational event": malformed_extraction,
            "RISK_SCORE": "RISK_SCORE: 0.2\nREASON: routine",
            "Choose the protocol": "SELECTED: report_security_incident\nREASON: unconfirmed suspicious activity",
            "participating in the": _single_agent_formulation(
                "surveillance_agent", "log the observation at south_corner; no drone dispatch needed"
            ),
            "VERDICT:": _VERDICT_SUCCESS,
        }
    )
    ctx = _sec_ctx(tmp_path, teardown_ctx, agent)
    client = build_app(ctx).test_client()

    event_id = _submit(client, VIEWER_IDENTITY, "unusual activity reported near the south corner")
    ctx.queue.wait_until_idle()

    status = job_status(ctx, event_id)
    assert status["status"] == "succeeded"  # not failed — the malformed field was dropped, not the report
    event = ctx.deps.persistence.fetch_event(event_id)
    assert event["classification"] == "security_incident"
    assert event["severity"] is None
