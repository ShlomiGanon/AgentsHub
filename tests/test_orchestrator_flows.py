import json
import types
from types import SimpleNamespace

import pytest

from agents import adapter
from agents.history import HistoryAgent
from agents.reference import ReferenceAgent
from agents.runtime import build_agent_registry
from auth.permissions import PermissionLevel
from config.base import BaseConfig, TierModel
from history.query import HistoryQueryService
from orchestrator.flows import (
    FlowDeps,
    assemble_core_agents,
    begin_report,
    begin_request,
    continue_after_approval,
    continue_after_clarification,
    process_message,
    process_report,
    process_request,
    resolve_approval,
    resolve_clarification,
    resume_after_approval,
    resume_after_clarification,
    run_report_extraction,
)
from orchestrator.insights import InsightsAgent
from orchestrator.main_agent import MainAgent
from persistence.sqlite_store import SQLitePersistence
from protocols.loader import ProtocolSet
from protocols.model import CriticalityLevel, Protocol
from profiles import AreaRegistry
from profiles import EventTypeRegistry


@pytest.fixture(autouse=True)
def _mock_crewai(monkeypatch):
    """Autouse for this whole file: whenever a *real* Agent (ReferenceAgent,
    HistoryAgent — never the scripted main/insights-agent stand-ins below,
    which implement .process() directly and never touch the adapter) is
    actually invoked, it goes through this fake instead of needing crewai
    installed.
    """

    class _FakeOutput:
        def __init__(self, raw):
            self.raw = raw

    class _FakeCrewAgent:
        def __init__(self, **kwargs):
            pass

        def kickoff(self, text):
            return _FakeOutput("status nominal, no anomalies")

    fake_module = types.SimpleNamespace(Agent=_FakeCrewAgent, tools=types.SimpleNamespace(BaseTool=object))
    monkeypatch.setattr(adapter, "_get_crewai", lambda: fake_module)


# -- assemble_core_agents (from Part A) --------------------------------


def test_assemble_core_agents_merges_profile_main_and_insights_agents():
    history_agent = SimpleNamespace(name="history_agent")
    loaded_profile = SimpleNamespace(core_agents={"history_agent": history_agent})
    base_config = BaseConfig(core_model=TierModel(model="main-model", api_key="core-key"))

    core_agents = assemble_core_agents(loaded_profile, base_config)

    assert set(core_agents) == {"history_agent", "main_agent", "insights_agent"}
    assert core_agents["history_agent"] is history_agent
    assert isinstance(core_agents["main_agent"], MainAgent)
    assert core_agents["main_agent"].model == "main-model"
    assert core_agents["main_agent"].descriptor.api_key == "core-key"
    assert isinstance(core_agents["insights_agent"], InsightsAgent)
    assert core_agents["insights_agent"].model == "main-model"
    assert core_agents["insights_agent"].descriptor.api_key == "core-key"


def test_assemble_core_agents_does_not_mutate_the_profiles_dict():
    loaded_profile = SimpleNamespace(core_agents={"history_agent": SimpleNamespace(name="history_agent")})
    base_config = BaseConfig(core_model=TierModel(model="m", api_key="k"))

    assemble_core_agents(loaded_profile, base_config)

    assert set(loaded_profile.core_agents) == {"history_agent"}  # unchanged


# -- The new-event flow, end to end -----------------------------------


class _FakeResult:
    def __init__(self, status, text):
        self.status = status
        self.text = text


class _ScriptedAgent:
    """A duck-typed Main/Insights Agent stand-in: dispatches by sniffing
    the prompt for a keyword unique to each decision, since one object
    plays every role a real MainAgent plays across one flow run."""

    def __init__(self, dispatch: dict[str, str], default_status="success"):
        self._dispatch = dispatch
        self._default_status = default_status
        self.calls = []

    def process(self, text, allowed_tools):
        self.calls.append(text)
        for keyword, response_text in self._dispatch.items():
            if keyword in text:
                return _FakeResult(self._default_status, response_text)
        raise AssertionError(f"no scripted response for prompt starting: {text[:150]!r}")


class _FakeSettings:
    def __init__(self, risk_threshold=0.5, retry_count=3, lookback_window_days=30):
        self.risk_threshold = risk_threshold
        self.retry_count = retry_count
        self.lookback_window_days = lookback_window_days

    def get_risk_threshold(self):
        return self.risk_threshold

    def get_retry_count(self):
        return self.retry_count

    def get_lookback_window_days(self):
        return self.lookback_window_days


def _protocols():
    return (
        Protocol(
            name="status_check",
            description="applies to a routine status check",
            participating_agents=("reference_agent",),
            approved_tools=("check_status",),
            expected_success_output="a status report",
            criticality=CriticalityLevel.LOW,
            approval_flag=False,
        ),
        Protocol(
            name="dispatch_response",
            description="applies when a response must be dispatched",
            participating_agents=("reference_agent",),
            approved_tools=("check_status", "record_action"),
            expected_success_output="confirmation a response was dispatched",
            criticality=CriticalityLevel.HIGH,
            approval_flag=True,
        ),
    )


@pytest.fixture
def deps(tmp_path):
    persistence = SQLitePersistence(str(tmp_path / "flows.db"))
    reference_agent = ReferenceAgent(model="m")
    history_agent = HistoryAgent(model="m")
    registry = build_agent_registry({}, [reference_agent, history_agent])
    settings = _FakeSettings()
    history_query_service = HistoryQueryService(persistence, history_agent, settings)

    yield FlowDeps(
        persistence=persistence,
        settings_store=settings,
        registry=registry,
        protocol_set=ProtocolSet(protocols=_protocols()),
        event_type_registry=EventTypeRegistry(types=("fire", "medical", "human_activation")),
        area_registry=AreaRegistry(areas=("north_sector", "south_sector")),
        history_query_service=history_query_service,
    )

    persistence.close()


def _extraction_response(classification="fire", area="north_sector", description="smoke at gate 3", severity="moderate", occurred_at="2026-08-20T09:00:00"):
    import json

    return json.dumps(
        {"classification": classification, "area": area, "entities": ["gate-3"], "description": description, "severity": severity, "occurred_at": occurred_at}
    )


def _happy_path_agent(risk_score="0.2", selected="status_check", verdict="success", agent_task="check gate 3", extraction=None):
    return _ScriptedAgent(
        {
            "Extract this operational event": extraction or _extraction_response(),
            "RISK_SCORE": f"RISK_SCORE: {risk_score}\nREASON: assessed",
            "Choose the protocol": f"SELECTED: {selected}\nREASON: fits",
            "participating in the": f"AGENT: reference_agent\nTASK: {agent_task}",
            "VERDICT:": f"VERDICT: {verdict}\nREASONING: matches expected output",
        }
    )


def test_process_report_holds_for_clarification_when_classification_is_unresolved(deps):
    agent = _ScriptedAgent({"Extract this operational event": '{"classification": null, "area": null, "entities": [], "description": null, "severity": null, "occurred_at": null}'})
    insights_agent = _ScriptedAgent({})

    result = process_report(deps, agent, insights_agent, "something happened, unclear what", "telegram", "2026-08-20T10:00:00", "viewer-1")

    assert result.outcome == "held_for_clarification"
    [held] = deps.persistence.list_held_events("clarification")
    assert held["event_id"] == result.event_id


def test_process_report_holds_for_clarification_logs_the_hold_kind(deps, caplog):
    agent = _ScriptedAgent({"Extract this operational event": '{"classification": null, "area": null, "entities": [], "description": null, "severity": null, "occurred_at": null}'})
    insights_agent = _ScriptedAgent({})

    with caplog.at_level("INFO"):
        result = process_report(deps, agent, insights_agent, "something happened, unclear what", "telegram", "2026-08-20T10:00:00", "viewer-1")

    holds = [r for r in caplog.records if getattr(r, "event", None) == "hold_created"]
    assert len(holds) == 1
    assert holds[0].hold_kind == "clarification"
    assert holds[0].event_id == result.event_id

    extraction = [r for r in caplog.records if getattr(r, "event", None) == "extraction_result"]
    assert len(extraction) == 1
    assert extraction[0].classification is None
    assert extraction[0].missing_fields  # unresolved classification is named as missing


def test_process_report_low_risk_unflagged_protocol_runs_to_success(deps, caplog):
    agent = _happy_path_agent(risk_score="0.1", selected="status_check", verdict="success")
    insights_agent = type("I", (), {"process": lambda self, text, tools: _FakeResult("success", "no notable precedent")})()

    with caplog.at_level("INFO"):
        result = process_report(deps, agent, insights_agent, "smoke at gate 3", "telegram", "2026-08-20T10:00:00", "viewer-1")

    assert result.outcome == "succeeded"
    event = deps.persistence.fetch_event(result.event_id)
    assert event["outcome"] == "succeeded"
    assert event["selected_protocol"] == "status_check"
    assert event["steps"][0]["agent_name"] == "reference_agent"

    risk_records = [r for r in caplog.records if getattr(r, "event", None) == "risk_assessed"]
    assert risk_records and risk_records[0].risk_level == "low"

    selection_records = [r for r in caplog.records if getattr(r, "event", None) == "protocol_selection"]
    assert selection_records and selection_records[0].protocol_name == "status_check"

    verdict_records = [r for r in caplog.records if getattr(r, "event", None) == "final_verdict"]
    assert verdict_records and verdict_records[0].verdict == "success"

    outcome_records = [r for r in caplog.records if getattr(r, "event", None) == "event_outcome" and r.event_id == result.event_id]
    assert outcome_records[-1].outcome == "succeeded"


def test_process_report_flagged_protocol_holds_for_approval_then_resumes_approved(deps, caplog):
    agent = _happy_path_agent(risk_score="0.9", selected="dispatch_response", verdict="success", agent_task="dispatch to gate 3")
    insights_agent = type("I", (), {"process": lambda self, text, tools: _FakeResult("success", "insight")})()

    with caplog.at_level("INFO"):
        held = process_report(deps, agent, insights_agent, "fire at gate 3", "telegram", "2026-08-20T10:00:00", "viewer-1")
    assert held.outcome == "held_for_approval"

    holds = [r for r in caplog.records if getattr(r, "event", None) == "hold_created"]
    assert len(holds) == 1
    assert holds[0].hold_kind == "approval"
    assert holds[0].reason == "flagged_protocol"
    assert holds[0].event_id == held.event_id

    [hold] = deps.persistence.list_held_events("approval")
    resumed = resume_after_approval(deps, agent, insights_agent, hold["hold_id"], "commander-1", PermissionLevel.COMMANDER, "approved")

    assert resumed.outcome == "succeeded"
    event = deps.persistence.fetch_event(held.event_id)
    assert event["approval_held"] is True
    assert event["approval_answered_by"] == "commander-1"
    assert event["outcome"] == "succeeded"


def test_resume_after_approval_rejection_declines_and_records_outcome(deps):
    agent = _happy_path_agent(risk_score="0.9", selected="dispatch_response")
    insights_agent = type("I", (), {"process": lambda self, text, tools: _FakeResult("success", "insight")})()

    held = process_report(deps, agent, insights_agent, "fire at gate 3", "telegram", "2026-08-20T10:00:00", "viewer-1")
    [hold] = deps.persistence.list_held_events("approval")

    resumed = resume_after_approval(deps, agent, insights_agent, hold["hold_id"], "commander-1", PermissionLevel.COMMANDER, "rejected")

    assert resumed.outcome == "declined"
    assert deps.persistence.fetch_event(held.event_id)["outcome"] == "declined"


def test_resume_after_clarification_continues_at_risk_assessment_not_extraction(deps):
    agent = _ScriptedAgent(
        {
            "Extract this operational event": '{"classification": null, "area": "north_sector", "entities": [], "description": "d", "severity": "s", "occurred_at": "2026-08-20T09:00:00"}',
            "RISK_SCORE": "RISK_SCORE: 0.1\nREASON: r",
            "Choose the protocol": "SELECTED: status_check\nREASON: fits",
            "participating in the": "AGENT: reference_agent\nTASK: check gate 3",
            "VERDICT:": "VERDICT: success\nREASONING: r",
        }
    )
    insights_agent = type("I", (), {"process": lambda self, text, tools: _FakeResult("success", "insight")})()

    held = process_report(deps, agent, insights_agent, "something unclear at gate 3", "telegram", "2026-08-20T10:00:00", "viewer-1")
    assert held.outcome == "held_for_clarification"

    extraction_calls_before = sum("Extract this operational event" in c for c in agent.calls)

    [hold] = deps.persistence.list_held_events("clarification")
    resumed = resume_after_clarification(deps, agent, insights_agent, hold["hold_id"], "commander-1", PermissionLevel.COMMANDER, "fire")

    extraction_calls_after = sum("Extract this operational event" in c for c in agent.calls)
    assert extraction_calls_after == extraction_calls_before  # never ran extraction again

    assert resumed.outcome == "succeeded"
    event = deps.persistence.fetch_event(held.event_id)
    assert event["classification"] == "fire"
    assert event["area"] == "north_sector"  # preserved from the original extraction, not discarded


def test_resume_after_clarification_rejects_free_text(deps):
    agent = _ScriptedAgent({"Extract this operational event": '{"classification": null, "area": null, "entities": [], "description": null, "severity": null, "occurred_at": null}'})
    insights_agent = type("I", (), {"process": lambda self, text, tools: _FakeResult("success", "insight")})()

    process_report(deps, agent, insights_agent, "unclear text", "telegram", "2026-08-20T10:00:00", "viewer-1")
    [hold] = deps.persistence.list_held_events("clarification")

    answer = resume_after_clarification(deps, agent, insights_agent, hold["hold_id"], "commander-1", PermissionLevel.COMMANDER, "not_a_real_type")

    assert answer.status == "invalid_classification"


def test_process_request_bypasses_extraction_entirely(deps):
    agent = _ScriptedAgent(
        {
            "RISK_SCORE": "RISK_SCORE: 0.9\nREASON: commander request",
            "Choose the protocol": "SELECTED: dispatch_response\nREASON: fits",
            "participating in the": "AGENT: reference_agent\nTASK: dispatch",
            "VERDICT:": "VERDICT: success\nREASONING: r",
        }
    )
    insights_agent = type("I", (), {"process": lambda self, text, tools: _FakeResult("success", "insight")})()

    result = process_request(deps, agent, insights_agent, "please dispatch someone to gate 3", "2026-08-20T10:00:00", "commander-1", originated_from_commander=True)

    assert not any("Extract this operational event" in c for c in agent.calls)
    event = deps.persistence.fetch_event(result.event_id)
    assert event["classification"] == "human_activation"
    # commander's own request bypasses the approval flag even for a flagged protocol
    assert result.outcome == "succeeded"
    assert event["approval_held"] is False


def test_process_request_from_a_viewer_still_holds_for_a_flagged_protocol(deps):
    agent = _ScriptedAgent(
        {
            "RISK_SCORE": "RISK_SCORE: 0.9\nREASON: r",
            "Choose the protocol": "SELECTED: dispatch_response\nREASON: fits",
        }
    )
    insights_agent = type("I", (), {"process": lambda self, text, tools: _FakeResult("success", "insight")})()

    result = process_request(deps, agent, insights_agent, "please dispatch someone", "2026-08-20T10:00:00", "viewer-1", originated_from_commander=False)

    assert result.outcome == "held_for_approval"


def test_process_message_routes_question_without_writing_an_event(deps):
    agent = _ScriptedAgent(
        {
            "kind of message": "INTENT: question\nREASON: asks about status",
            "Decide whether this question can be answered by directly looking up": "ROUTE: normal",
            "Decide which of the following agents": "AGENT: reference_agent\nTASK: what's the status?",
        }
    )
    insights_agent = type("I", (), {"process": lambda self, text, tools: _FakeResult("success", "insight")})()

    kind, result = process_message(deps, agent, insights_agent, "what's the status at gate 3?", "viewer-1", "2026-08-20T10:00:00", is_commander=False)

    assert kind == "question"
    assert isinstance(result, str)
    assert deps.persistence.fetch_events_range("2000-01-01", "2100-01-01") == []


def test_process_message_routes_conversational_directly_with_no_agent_routing(deps):
    # No "Decide whether this question can be answered by directly looking
    # up" or "Decide which of the following agents" dispatch entries are
    # given here at all — if the conversational branch ever fell through
    # into question_flow.py's machinery, _ScriptedAgent.process would
    # raise on the unmatched prompt, failing this test loudly.
    agent = _ScriptedAgent(
        {
            "kind of message": "INTENT: conversational\nREASON: purely social, nothing to look up or act on",
            "Reply naturally and directly": "Doing well, thanks for asking!",
        }
    )
    insights_agent = type("I", (), {"process": lambda self, text, tools: _FakeResult("success", "insight")})()

    kind, result = process_message(deps, agent, insights_agent, "hey, how are you?", "viewer-1", "2026-08-20T10:00:00", is_commander=False)

    assert kind == "conversational"
    assert result == "Doing well, thanks for asking!"
    assert deps.persistence.fetch_events_range("2000-01-01", "2100-01-01") == []
    # Exactly two model calls: intent classification, then the direct reply.
    assert len(agent.calls) == 2


def test_process_message_still_declines_a_genuine_no_agent_fit_question(deps):
    # Regression check for this session's earlier NONE fix: a real
    # question with no agent whose role fits it must still classify as
    # "question" (not "conversational") and go through question_flow.py's
    # own NONE decline — completely unaffected by the new conversational
    # branch.
    agent = _ScriptedAgent(
        {
            "kind of message": "INTENT: question\nREASON: asks the system to check something real",
            "Decide whether this question can be answered by directly looking up": "ROUTE: normal",
            "Decide which of the following agents": "NONE: no loaded agent tracks personal tasks",
        }
    )
    insights_agent = type("I", (), {"process": lambda self, text, tools: _FakeResult("success", "insight")})()

    kind, result = process_message(deps, agent, insights_agent, "do I have any tasks?", "viewer-1", "2026-08-20T10:00:00", is_commander=False)

    assert kind == "question"
    assert result == "I don't have a way to answer that. no loaded agent tracks personal tasks"


def test_process_message_routes_report_into_the_new_event_flow(deps):
    agent = _happy_path_agent(risk_score="0.1", selected="status_check")
    agent._dispatch["kind of message"] = "INTENT: report\nREASON: describes something that happened"
    insights_agent = type("I", (), {"process": lambda self, text, tools: _FakeResult("success", "insight")})()

    kind, result = process_message(deps, agent, insights_agent, "smoke seen at gate 3", "viewer-1", "2026-08-20T10:00:00", is_commander=False)

    assert kind == "report"
    assert result.outcome == "succeeded"


def test_process_message_routes_request_into_the_new_event_flow(deps):
    # Unaffected by the new conversational branch — "request" is checked
    # after both "conversational" and "question" and reaches process_request
    # exactly as before.
    agent = _happy_path_agent(risk_score="0.9", selected="dispatch_response", agent_task="dispatch to gate 3")
    agent._dispatch["kind of message"] = "INTENT: request\nREASON: asks for a response to be dispatched"
    insights_agent = type("I", (), {"process": lambda self, text, tools: _FakeResult("success", "insight")})()

    kind, result = process_message(deps, agent, insights_agent, "please dispatch someone to gate 3", "commander-1", "2026-08-20T10:00:00", is_commander=True)

    assert kind == "request"
    assert result.outcome == "succeeded"


def test_a_held_event_resumes_correctly_after_a_simulated_restart(deps, tmp_path):
    agent = _happy_path_agent(risk_score="0.9", selected="dispatch_response")
    insights_agent = type("I", (), {"process": lambda self, text, tools: _FakeResult("success", "insight")})()

    held = process_report(deps, agent, insights_agent, "fire at gate 3", "telegram", "2026-08-20T10:00:00", "viewer-1")
    [hold] = deps.persistence.list_held_events("approval")

    # Simulate a restart: a fresh SQLitePersistence instance against the
    # same file, no in-memory state carried over from `deps`.
    restarted_persistence = SQLitePersistence(deps.persistence.db_path)
    restarted_deps = FlowDeps(
        persistence=restarted_persistence,
        settings_store=deps.settings_store,
        registry=deps.registry,
        protocol_set=deps.protocol_set,
        event_type_registry=deps.event_type_registry,
        area_registry=deps.area_registry,
        history_query_service=HistoryQueryService(restarted_persistence, deps.registry.get("history_agent"), deps.settings_store),
    )

    resumed = resume_after_approval(restarted_deps, agent, insights_agent, hold["hold_id"], "commander-1", PermissionLevel.COMMANDER, "approved")

    assert resumed.outcome == "succeeded"
    restarted_persistence.close()


# -- The synchronous-prefix / queued-continuation split (§7.2, §7.11) -----
# process_report/process_request/resume_after_clarification/resume_after_
# approval are exercised above as one call each; these tests exercise the
# split pieces §7.2/§7.11 will call separately — one inline, one queued.


def test_begin_report_returns_immediately_with_no_model_call(deps):
    agent = _ScriptedAgent({})  # would raise on any .process() call

    event_id = begin_report(deps, "smoke at gate 3", "telegram", "2026-08-20T10:00:00", "viewer-1")

    assert agent.calls == []
    event = deps.persistence.fetch_event(event_id)
    assert event["raw_text"] == "smoke at gate 3"
    assert event["classification"] is None  # extraction hasn't run yet


def test_run_report_extraction_continues_from_a_begin_report_event_id(deps):
    agent = _happy_path_agent(risk_score="0.1", selected="status_check")
    insights_agent = type("I", (), {"process": lambda self, text, tools: _FakeResult("success", "insight")})()

    event_id = begin_report(deps, "smoke at gate 3", "telegram", "2026-08-20T10:00:00", "viewer-1")
    result = run_report_extraction(deps, event_id, agent, insights_agent)

    assert result.event_id == event_id
    assert result.outcome == "succeeded"


def test_begin_request_returns_immediately_with_no_model_call(deps):
    agent = _ScriptedAgent({})  # would raise on any .process() call

    event_id = begin_request(deps, "please dispatch someone", "2026-08-20T10:00:00", "commander-1")

    assert agent.calls == []
    event = deps.persistence.fetch_event(event_id)
    assert event["classification"] == "human_activation"
    assert event["risk_level"] is None  # risk assessment hasn't run yet


def test_resolve_clarification_writes_the_answer_without_resuming(deps):
    agent = _ScriptedAgent({"Extract this operational event": '{"classification": null, "area": null, "entities": [], "description": null, "severity": null, "occurred_at": null}'})
    insights_agent = _ScriptedAgent({})

    held = process_report(deps, agent, insights_agent, "unclear text", "telegram", "2026-08-20T10:00:00", "viewer-1")
    [hold] = deps.persistence.list_held_events("clarification")

    answer = resolve_clarification(deps, hold["hold_id"], "commander-1", PermissionLevel.COMMANDER, "fire")

    assert answer.status == "resolved"
    event = deps.persistence.fetch_event(held.event_id)
    assert event["classification"] == "fire"
    assert event["risk_level"] is None  # continuation hasn't run yet


def test_continue_after_clarification_finishes_the_run(deps):
    agent = _happy_path_agent(risk_score="0.1", selected="status_check")
    insights_agent = type("I", (), {"process": lambda self, text, tools: _FakeResult("success", "insight")})()
    agent._dispatch["Extract this operational event"] = '{"classification": null, "area": "north_sector", "entities": [], "description": "d", "severity": "s", "occurred_at": "2026-08-20T09:00:00"}'

    held = process_report(deps, agent, insights_agent, "something unclear at gate 3", "telegram", "2026-08-20T10:00:00", "viewer-1")
    [hold] = deps.persistence.list_held_events("clarification")
    answer = resolve_clarification(deps, hold["hold_id"], "commander-1", PermissionLevel.COMMANDER, "fire")

    result = continue_after_clarification(deps, answer.hold["event_id"], agent, insights_agent)

    assert result.outcome == "succeeded"


def test_resolve_approval_denial_is_synchronous_with_no_continuation_needed(deps):
    agent = _happy_path_agent(risk_score="0.9", selected="dispatch_response")
    insights_agent = type("I", (), {"process": lambda self, text, tools: _FakeResult("success", "insight")})()

    held = process_report(deps, agent, insights_agent, "fire at gate 3", "telegram", "2026-08-20T10:00:00", "viewer-1")
    [hold] = deps.persistence.list_held_events("approval")

    answer = resolve_approval(deps, hold["hold_id"], "commander-1", PermissionLevel.COMMANDER, "rejected")

    assert answer.status == "rejected"
    # resolve_approval only resolves — it never records the declined
    # outcome itself; resume_after_approval (or the API's own deny path)
    # does that next, synchronously, with no continuation to queue.
    event = deps.persistence.fetch_event(held.event_id)
    assert event["outcome"] is None


def test_resolve_approval_then_continue_after_approval_composes_to_success(deps):
    agent = _happy_path_agent(risk_score="0.9", selected="dispatch_response", agent_task="dispatch to gate 3")
    insights_agent = type("I", (), {"process": lambda self, text, tools: _FakeResult("success", "insight")})()

    held = process_report(deps, agent, insights_agent, "fire at gate 3", "telegram", "2026-08-20T10:00:00", "viewer-1")
    [hold] = deps.persistence.list_held_events("approval")

    answer = resolve_approval(deps, hold["hold_id"], "commander-1", PermissionLevel.COMMANDER, "approved")
    assert answer.status == "approved"

    result = continue_after_approval(deps, answer.hold["event_id"], agent, insights_agent, answer.hold["selected_protocol_name"])

    assert result.outcome == "succeeded"


def test_process_report_no_match_selection_writes_a_terminal_outcome_not_a_hold(deps, caplog):
    # NO_MATCH has no candidate to approve/reject/select, so there is
    # nothing a hold could ever resolve — it must behave like
    # uncertain/closed_on_precedent: a real terminal outcome plus a
    # one-way notification, never a held_events row.
    agent = _ScriptedAgent(
        {
            "Extract this operational event": _extraction_response(),
            "RISK_SCORE": "RISK_SCORE: 0.2\nREASON: assessed",
            "Choose the protocol": "NO_MATCH: no loaded protocol handles this kind of request",
        }
    )
    insights_agent = type("I", (), {"process": lambda self, text, tools: _FakeResult("success", "insight")})()

    with caplog.at_level("INFO"):
        result = process_report(deps, agent, insights_agent, "an unroutable report", "telegram", "2026-08-20T10:00:00", "viewer-1")

    assert result.outcome == "no_match_protocol"
    assert result.detail == "no loaded protocol handles this kind of request"

    # No approval hold was created for it.
    assert deps.persistence.list_held_events("approval") == []

    event = deps.persistence.fetch_event(result.event_id)
    assert event["outcome"] == "no_match_protocol"
    assert event["outcome_failure_reason"] == "no loaded protocol handles this kind of request"
    assert event["approval_held"] is False  # never went through the hold path at all

    outcome_logs = [r for r in caplog.records if getattr(r, "event", None) == "event_outcome"]
    assert any(r.outcome == "no_match_protocol" for r in outcome_logs)


def test_an_ambiguous_selection_hold_resolves_to_a_real_protocol_and_resumes(deps):
    # §6.4/§6.7's gap, closed additively in orchestrator.holds: before this
    # fix, `selected_protocol_name` stayed None for an ambiguous hold and
    # continue_after_approval had nothing real to run — this asserts a
    # chosen candidate reaches it, not just that the None case is absent.
    agent = _ScriptedAgent(
        {
            "Extract this operational event": _extraction_response(),
            "RISK_SCORE": "RISK_SCORE: 0.1\nREASON: low, but two protocols fit",
            "Choose the protocol": "AMBIGUOUS: status_check,dispatch_response\nREASON: both fit equally well",
            "participating in the": "AGENT: reference_agent\nTASK: check gate 3",
            "VERDICT:": "VERDICT: success\nREASONING: matches expected output",
        }
    )
    insights_agent = type("I", (), {"process": lambda self, text, tools: _FakeResult("success", "insight")})()

    held = process_report(deps, agent, insights_agent, "smoke at gate 3", "telegram", "2026-08-20T10:00:00", "viewer-1")
    assert held.outcome == "held_for_approval"
    [hold] = deps.persistence.list_held_events("approval")
    assert hold["reason"] == "ambiguous_selection"
    assert hold["selected_protocol_name"] is None

    answer = resolve_approval(deps, hold["hold_id"], "commander-1", PermissionLevel.COMMANDER, "status_check")
    assert answer.status == "approved"
    assert answer.hold["selected_protocol_name"] == "status_check"

    event = deps.persistence.fetch_event(held.event_id)
    assert event["selected_protocol"] == "status_check"  # resolve_approval's own write

    result = continue_after_approval(deps, answer.hold["event_id"], agent, insights_agent, answer.hold["selected_protocol_name"])

    assert result.outcome == "succeeded"

import types

import pytest

from agents import adapter
from agents.reference import ReferenceAgent
from agents.runtime import build_agent_registry
from orchestrator.main_agent import OrchestrationParseError, _parse_formulation_response, formulate_tasks, rewrite_task
from protocols.model import CriticalityLevel, Protocol, Step


class _ScriptedMainAgent:
    def __init__(self, response_text, status="success"):
        self._response_text = response_text
        self._status = status
        self.calls = []

    def process(self, text, allowed_tools):
        self.calls.append((text, allowed_tools))

        class _Result:
            status = self._status
            text = self._response_text

        return _Result()


class _SequentialMainAgent:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def process(self, text, allowed_tools):
        self.calls.append((text, allowed_tools))
        response_text, status = self._responses.pop(0)

        class _Result:
            pass

        _Result.status = status
        _Result.text = response_text
        return _Result()


@pytest.fixture
def registry():
    agent = ReferenceAgent(model="m")
    return build_agent_registry({}, [agent])


def _protocol(**overrides):
    fields = dict(
        name="dispatch_response",
        description="d",
        participating_agents=("reference_agent",),
        approved_tools=("check_status", "record_action"),
        expected_success_output="x",
        criticality=CriticalityLevel.LOW,
        approval_flag=False,
    )
    fields.update(overrides)
    return Protocol(**fields)


# -- Parser -------------------------------------------------------------


def test_parse_single_agent_block():
    tasks = _parse_formulation_response("AGENT: reference_agent\nTASK: check gate 3")

    assert tasks == {"reference_agent": "check gate 3"}


def test_parse_multiple_agent_blocks():
    response = "AGENT: a1\nTASK: do the first thing\nAGENT: a2\nTASK: do the second thing"

    tasks = _parse_formulation_response(response)

    assert tasks == {"a1": "do the first thing", "a2": "do the second thing"}


# -- formulate_tasks ------------------------------------------------------


def test_formulate_tasks_produces_a_step_per_participating_agent(registry):
    agent = _ScriptedMainAgent("AGENT: reference_agent\nTASK: check status at gate 3")

    result = formulate_tasks(agent, _protocol(), registry, "raw", "fire", "north", "d")

    assert result.success
    assert len(result.steps) == 1
    assert result.steps[0].agent_name == "reference_agent"
    assert result.steps[0].task_text == "check status at gate 3"


def test_formulation_preserves_valid_required_event_fields(registry):
    agent = _ScriptedMainAgent(json.dumps({
        "steps": [{
            "step_id": "check-location",
            "agent_name": "reference_agent",
            "task": "Check the reported location.",
            "depends_on": [],
            "required_event_fields": ["area"],
        }]
    }))

    result = formulate_tasks(agent, _protocol(), registry, "raw", "fire", None, "d")

    assert result.success
    assert result.steps[0].required_event_fields == ("area",)


def test_allowed_tools_are_filtered_to_what_the_agent_actually_exposes(registry):
    agent = _ScriptedMainAgent("AGENT: reference_agent\nTASK: t")
    protocol = _protocol(approved_tools=("check_status", "record_action", "some_other_tool_no_agent_has"))

    result = formulate_tasks(agent, protocol, registry, "raw", "fire", "north", "d")

    assert set(result.steps[0].allowed_tools) == {"check_status", "record_action"}


def test_missing_an_agents_block_fails_naming_that_agent(registry):
    agent = _ScriptedMainAgent("this response has no AGENT/TASK blocks at all")

    result = formulate_tasks(agent, _protocol(), registry, "raw", "fire", "north", "d")

    assert not result.success
    assert result.failed_agent_name == "reference_agent"


def test_formulation_repairs_one_invalid_response_with_the_parse_failure(registry):
    repaired = json.dumps({
        "steps": [{
            "step_id": "check-south",
            "agent_name": "reference_agent",
            "task": "Check the reported fire in the south sector.",
            "depends_on": [],
        }]
    })
    agent = _SequentialMainAgent([
        ("```json\n{}\n```", "success"),
        (repaired, "success"),
    ])

    result = formulate_tasks(agent, _protocol(), registry, "raw", "fire", "south", "d")

    assert result.success
    assert len(agent.calls) == 2
    assert "model did not produce a task" in agent.calls[1][0]
    assert "without Markdown fences" in agent.calls[1][0]


def test_unclear_task_status_fails_formulation(registry):
    agent = _ScriptedMainAgent("missing context", status="unclear_task")

    result = formulate_tasks(agent, _protocol(), registry, "raw", "fire", "north", "d")

    assert not result.success


def test_precedent_context_defaults_to_empty_and_is_optional(registry):
    agent = _ScriptedMainAgent("AGENT: reference_agent\nTASK: t")

    result = formulate_tasks(agent, _protocol(), registry, "raw", "fire", "north", "d")  # no precedent_context passed

    assert result.success


def test_precedent_context_appears_in_the_prompt_when_given(registry):
    agent = _ScriptedMainAgent("AGENT: reference_agent\nTASK: t")

    formulate_tasks(agent, _protocol(), registry, "raw", "fire", "north", "d", precedent_context=("prior incident X",))

    assert "prior incident X" in agent.calls[0][0]


def test_formulate_tasks_passes_no_tools_to_the_main_agent(registry):
    agent = _ScriptedMainAgent("AGENT: reference_agent\nTASK: t")

    formulate_tasks(agent, _protocol(), registry, "raw", "fire", "north", "d")

    assert agent.calls[0][1] == []


# -- rewrite_task -----------------------------------------------------------


def test_rewrite_task_returns_the_full_response_as_the_new_task():
    agent = _ScriptedMainAgent("check status specifically at gate 3, not the whole perimeter")
    step = Step(agent_name="reference_agent", task_text="check status", allowed_tools=("check_status",))

    rewritten = rewrite_task(agent, step, "which location specifically")

    assert rewritten == "check status specifically at gate 3, not the whole perimeter"


def test_rewrite_task_raises_when_the_agent_reports_unclear_again():
    agent = _ScriptedMainAgent("still unclear", status="unclear_task")
    step = Step(agent_name="a", task_text="t", allowed_tools=())

    with pytest.raises(OrchestrationParseError):
        rewrite_task(agent, step, "missing X")


def test_rewrite_task_matches_the_executors_task_rewriter_signature():
    import functools

    from protocols.executor import execute_steps
    from agents.errors import AgentModelError

    agent = _ScriptedMainAgent("rewritten task text")
    rewriter = functools.partial(rewrite_task, agent)

    class _FailingThenSucceedingAgent:
        name = "reference_agent"
        _calls = 0

        def exposed_tools(self):
            return ()

        def process(self, text, allowed_tools):
            type(self)._calls += 1
            if type(self)._calls == 1:
                from agents.results import AgentResult

                return AgentResult(status="unclear_task", text="missing location")

            from agents.results import AgentResult

            return AgentResult(status="success", text="done")

    class _FakeSettings:
        def get_retry_count(self):
            return 3

    step = Step(agent_name="reference_agent", task_text="check status", allowed_tools=())
    result = execute_steps([step], {"reference_agent": _FailingThenSucceedingAgent()}, _FakeSettings(), task_rewriter=rewriter, sleep_fn=lambda s: None)

    assert result.completed
    assert result.step_outcomes[0].result_text == "done"


# -- End-to-end through the mocked adapter -----------------------------------


def test_end_to_end_through_the_mocked_adapter(monkeypatch, registry):
    from orchestrator.main_agent import MainAgent

    class _FakeOutput:
        def __init__(self, raw):
            self.raw = raw

    class _FakeAgent:
        def __init__(self, **kwargs):
            pass

        def kickoff(self, text):
            return _FakeOutput("AGENT: reference_agent\nTASK: check gate 3 status")

    fake_module = types.SimpleNamespace(Agent=_FakeAgent, tools=types.SimpleNamespace(BaseTool=object))
    monkeypatch.setattr(adapter, "_get_crewai", lambda: fake_module)

    main_agent = MainAgent(model="fake-model")
    result = formulate_tasks(main_agent, _protocol(), registry, "raw", "fire", "north", "d")

    assert result.success
    assert result.steps[0].task_text == "check gate 3 status"
