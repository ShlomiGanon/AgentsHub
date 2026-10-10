"""Main orchestrator ingest, execution, and hold-sweep flows."""

import json
import types
from dataclasses import replace
from types import SimpleNamespace

import pytest

from agents import adapter
from agents import AgentRegistry, AgentResult
from agents.history import HistoryAgent
from agents.reference import ReferenceAgent
from agents.runtime import build_agent_registry
from agents.surveillance_agent import SurveillanceAgent
from auth.permissions import PermissionLevel
from config.base import BaseConfig, TierModel
from history import StepExecutionEnvelope, record_step_executions
from history.query import HistoryQueryService
from messages import get_catalog
import orchestrator.flows as flows_module
from orchestrator.flows import (
    FlowDeps,
    apply_drone_selection_reply,
    apply_event_data_reply,
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
    resume_after_event_data,
    run_report_extraction,
)
from orchestrator.holds import create_clarification_hold, create_event_data_hold
from orchestrator.insights import InsightsAgent
from orchestrator.main_agent import MainAgent
from persistence.sqlite_store import SQLitePersistence
from protocols.loader import ProtocolSet
from protocols.model import CriticalityLevel, Protocol, Step
from profiles import AreaRegistry
from profiles import EventTypeRegistry
from tests.crewai_fakes import install_crewai_stub


@pytest.fixture(autouse=True)
def _mock_crewai(monkeypatch):
    """Mock crewai."""
    install_crewai_stub(monkeypatch)


# -- assemble_core_agents (from Part A) --------------------------------


def test_assemble_core_agents_merges_profile_main_and_insights_agents():
    """Assemble core agents merges profile main and insights agents."""
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
    """Assemble core agents does not mutate the profiles dict."""
    loaded_profile = SimpleNamespace(core_agents={"history_agent": SimpleNamespace(name="history_agent")})
    base_config = BaseConfig(core_model=TierModel(model="m", api_key="k"))

    assemble_core_agents(loaded_profile, base_config)

    assert set(loaded_profile.core_agents) == {"history_agent"}  # unchanged


# -- The new-event flow, end to end -----------------------------------


class _FakeResult:
    """FakeResult."""
    def __init__(self, status, text):
        """Initialize this test helper."""
        self.status = status
        self.text = text


class _ScriptedAgent:
    """A duck-typed Main/Insights Agent stand-in: dispatches by sniffing
    the prompt for a keyword unique to each decision, since one object
    plays every role a real MainAgent plays across one flow run."""

    def __init__(self, dispatch: dict[str, str], default_status="success"):
        """Initialize this test helper."""
        self._dispatch = dispatch
        self._default_status = default_status
        self.calls = []

    def process(self, text, allowed_tools, invocation_policy=None):
        """Process."""
        self.calls.append(text)
        for keyword, response_text in self._dispatch.items():
            if keyword in text:
                return _FakeResult(self._default_status, response_text)
        raise AssertionError(f"no scripted response for prompt starting: {text[:150]!r}")


class _FakeSettings:
    """FakeSettings."""
    def __init__(self, risk_threshold=0.5, retry_count=3, lookback_window_days=30, rich_reports_enabled=False):
        """Initialize this test helper."""
        self.risk_threshold = risk_threshold
        self.retry_count = retry_count
        self.lookback_window_days = lookback_window_days
        self.rich_reports_enabled = rich_reports_enabled

    def get_risk_threshold(self):
        """Get risk threshold."""
        return self.risk_threshold

    def get_retry_count(self):
        """Get retry count."""
        return self.retry_count

    def get_lookback_window_days(self):
        """Get lookback window days."""
        return self.lookback_window_days

    def get_rich_reports_enabled(self):
        """Get rich reports enabled."""
        return self.rich_reports_enabled


def _protocols():
    """Protocols."""
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
    """Deps."""
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
    """Extraction response."""
    import json

    return json.dumps(
        {"classification": classification, "area": area, "entities": ["gate-3"], "description": description, "severity": severity, "occurred_at": occurred_at}
    )


def _happy_path_agent(risk_score="0.2", selected="status_check", verdict="success", agent_task="check gate 3", extraction=None):
    """Happy path agent."""
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
    """Part 1 (item #6): an unresolved
    classification now resolves to the built-in UNCLASSIFIED_TYPE, which
    requires `area` — asked (via the event-data hold) before the clarification
    hold that lets a commander pick a real classification exists at all."""
    agent = _ScriptedAgent(
        {
            "Extract this operational event": '{"classification": null, "area": null, "entities": [], "description": null, "severity": null, "occurred_at": null}',
            "Write one concise question": "Which area is this in?",
        }
    )
    insights_agent = _ScriptedAgent({})

    result = process_report(deps, agent, insights_agent, "something happened, unclear what", "telegram", "2026-08-20T10:00:00", "viewer-1")

    assert result.outcome == "waiting_for_event_data"
    assert deps.persistence.fetch_event(result.event_id)["classification"] == "unclassified"
    [hold] = deps.persistence.list_held_events("event_data")
    assert hold["missing_fields"] == ["area"]
    assert deps.persistence.list_held_events("clarification") == []

    deps.persistence.update_event(result.event_id, {"area": "north_sector"})
    resumed = resume_after_event_data(deps, result.event_id, agent, insights_agent)

    assert resumed.outcome == "held_for_clarification"
    [held] = deps.persistence.list_held_events("clarification")
    assert held["event_id"] == result.event_id


def test_merged_no_match_finishes_before_classification_or_required_field_holds(deps):
    """An unsupported primary update is not converted into an unrelated clarification."""

    merged_deps = replace(
        deps,
        optimization_policy=replace(
            deps.optimization_policy,
            operational_decision_mode="merged",
        ),
    )
    agent = _ScriptedAgent(
        {
            "Extract this operational event": json.dumps(
                {
                    "classification": None,
                    "area": None,
                    "entities": [],
                    "description": "primary update outside system capabilities",
                    "severity": None,
                    "occurred_at": None,
                    "availability_start": None,
                    "availability_end": None,
                    "absence_reason": None,
                    "risk_score": 0.1,
                    "risk_reason": "routine update",
                    "protocol_status": "no_match",
                    "protocol_name": None,
                    "candidate_names": [],
                    "protocol_reason": "the primary update matches no declared protocol",
                }
            ),
        }
    )
    insights_agent = _ScriptedAgent({})

    result = process_report(
        merged_deps,
        agent,
        insights_agent,
        "A primary update with an incidental availability remark",
        "telegram",
        "2026-08-20T10:00:00",
        "viewer-1",
    )

    assert result.outcome == "no_match_protocol"
    assert merged_deps.persistence.list_held_events("clarification") == []
    assert merged_deps.persistence.list_held_events("event_data") == []


def test_process_report_holds_for_clarification_logs_the_hold_kind(deps, caplog):
    """Process report holds for clarification logs the hold kind."""
    agent = _ScriptedAgent(
        {
            "Extract this operational event": '{"classification": null, "area": null, "entities": [], "description": null, "severity": null, "occurred_at": null}',
            "Write one concise question": "Which area is this in?",
        }
    )
    insights_agent = _ScriptedAgent({})

    with caplog.at_level("INFO"):
        result = process_report(deps, agent, insights_agent, "something happened, unclear what", "telegram", "2026-08-20T10:00:00", "viewer-1")

    holds = [r for r in caplog.records if getattr(r, "event", None) == "hold_created"]
    assert len(holds) == 1
    assert holds[0].hold_kind == "event_data"
    assert holds[0].event_id == result.event_id

    extraction = [r for r in caplog.records if getattr(r, "event", None) == "extraction_result"]
    assert len(extraction) == 1
    assert extraction[0].classification is None
    assert extraction[0].missing_fields  # unresolved classification is named as missing

    caplog.clear()
    deps.persistence.update_event(result.event_id, {"area": "north_sector"})
    with caplog.at_level("INFO"):
        resume_after_event_data(deps, result.event_id, agent, insights_agent)

    resumed_holds = [r for r in caplog.records if getattr(r, "event", None) == "hold_created"]
    assert len(resumed_holds) == 1
    assert resumed_holds[0].hold_kind == "clarification"


def test_resumed_event_uses_original_sender_permission_snapshot(deps):
    """Resumed event uses original sender permission snapshot."""
    commander_controlled = tuple(
        replace(
            protocol,
            approval_flag=False,
            commander_only=True,
            requires_confirmation=False,
        )
        if protocol.name == "status_check"
        else protocol
        for protocol in deps.protocol_set.all()
    )
    resumed_deps = replace(deps, protocol_set=ProtocolSet(protocols=commander_controlled))
    event_id = begin_report(
        resumed_deps,
        "smoke observed at gate 3",
        "sensor",
        "2026-08-20T10:00:00",
        "sensor-1",
        sender_permission_level="viewer",
    )
    resumed_deps.persistence.update_event(
        event_id,
        {
            "classification": "fire",
            "area": "north_sector",
            "description": "smoke",
            "severity": "moderate",
            "occurred_at": "2026-08-20T10:00:00",
        },
    )

    result = resume_after_event_data(
        resumed_deps,
        event_id,
        _happy_path_agent(selected="status_check"),
        _ScriptedAgent({}),
    )

    assert result.outcome == "held_for_approval"
    assert resumed_deps.persistence.fetch_event(event_id)["sender_permission_level"] == "viewer"
    assert resumed_deps.persistence.fetch_event(event_id)["outcome"] is None


def test_required_fields_gate_asks_only_for_the_field_extraction_could_not_resolve(deps):
    """Part 1 (item #6): a profile-
    defined event type's required fields are checked immediately after
    extraction, before risk assessment or protocol selection ever run —
    asking only for the one extraction couldn't resolve."""
    gated_deps = replace(
        deps,
        event_type_registry=EventTypeRegistry(types=deps.event_type_registry.types, required_fields={"fire": ("area", "severity")}),
    )
    agent = _ScriptedAgent(
        {
            "Extract this operational event": '{"classification": "fire", "area": "north_sector", "entities": [], "description": "smoke", "severity": null, "occurred_at": "2026-08-20T09:00:00"}',
            "Write one concise question": "How severe is it?",
            "RISK_SCORE": "RISK_SCORE: 0.1\nREASON: r",  # must not be reached before the gate resolves
        }
    )
    insights_agent = _ScriptedAgent({})

    result = process_report(gated_deps, agent, insights_agent, "smoke at gate 3", "telegram", "2026-08-20T10:00:00", "viewer-1")

    assert result.outcome == "waiting_for_event_data"
    [hold] = gated_deps.persistence.list_held_events("event_data")
    assert hold["missing_fields"] == ["severity"]  # not "area" — already resolved by extraction
    assert not any("RISK_SCORE" in call for call in agent.calls)  # risk assessment never ran


# -- direct_tool_binder protocols (Phase A): no formulate_tasks, no judge_success ---


def _direct_tool_protocol(name="log_status"):
    """Direct tool protocol."""
    def binder(event):
        return (
            Step(
                agent_name="reference_agent", task_text="record it directly", allowed_tools=("record_action",),
                step_id="1", kind="direct_tool", direct_tool_name="record_action",
                direct_tool_kwargs={"location": "gate-3", "note": "reported via direct lane"},
            ),
        )

    return Protocol(
        name=name,
        description="applies to a direct-tool logging report",
        participating_agents=("reference_agent",),
        approved_tools=("record_action",),
        expected_success_output="confirmation the action was recorded",
        criticality=CriticalityLevel.LOW,
        approval_flag=False,
        needs_insight=False,
        direct_tool_binder=binder,
    )


def test_direct_tool_protocol_never_calls_task_formulation_or_judge_success(deps):
    # _happy_path_agent's dispatch deliberately has no entry for "participating in the"
    # (task_formulation) or "VERDICT:" (judge_success) — _ScriptedAgent.process raises
    # AssertionError on an unscripted prompt, so this fails loudly if either is ever reached.
    """Direct tool protocol never calls task formulation or judge success."""
    direct_deps = replace(deps, protocol_set=ProtocolSet(protocols=(*deps.protocol_set.all(), _direct_tool_protocol())))
    agent = _happy_path_agent(risk_score="0.1", selected="log_status")
    insights_agent = _ScriptedAgent({})  # never called either -- needs_insight=False

    result = process_report(direct_deps, agent, insights_agent, "log this please", "telegram", "2026-08-20T10:00:00", "viewer-1")

    assert result.outcome == "succeeded"
    reference_agent = direct_deps.registry.get("reference_agent")
    assert reference_agent.actions_taken == ["gate-3: reported via direct lane"]


def test_direct_tool_protocol_fails_deterministically_without_a_model_call(deps):
    """Direct tool protocol fails deterministically without a model call."""
    def failing_binder(event):
        return (
            Step(
                agent_name="reference_agent", task_text="x", allowed_tools=("record_action",),
                step_id="1", kind="direct_tool", direct_tool_name="record_action",
                direct_tool_kwargs={},  # record_action requires `location` -- TypeError -> failed
            ),
        )

    failing_protocol = replace(_direct_tool_protocol("log_status_bad"), direct_tool_binder=failing_binder)
    direct_deps = replace(deps, protocol_set=ProtocolSet(protocols=(*deps.protocol_set.all(), failing_protocol)))
    agent = _happy_path_agent(risk_score="0.1", selected="log_status_bad")
    insights_agent = _ScriptedAgent({})

    result = process_report(direct_deps, agent, insights_agent, "log this please", "telegram", "2026-08-20T10:00:00", "viewer-1")

    assert result.outcome == "failed"


# -- resource-unavailable mechanism: deterministic, never model-judged as failure ------------


def _resource_unavailable_protocol(name="dispatch_something"):
    """Resource unavailable protocol."""
    def _try_dispatch(self, area=""):
        self.signal_resource_unavailable("drone", area, "no ready drones available")
        return f"Drone dispatch failed: no ready drones available for {area}."

    def binder(event):
        return (
            Step(
                agent_name="reference_agent", task_text="try dispatch", allowed_tools=(),
                step_id="1", kind="direct_tool", direct_tool_name="try_dispatch",
                direct_tool_kwargs={"area": "east_gate"},
            ),
        )

    protocol = Protocol(
        name=name,
        description="applies when a resource dispatch is attempted",
        participating_agents=("reference_agent",),
        approved_tools=(),
        expected_success_output="dispatch confirmation",
        criticality=CriticalityLevel.HIGH,
        approval_flag=False,
        direct_tool_binder=binder,
    )
    return protocol, _try_dispatch


def test_resource_unavailable_is_handled_not_failed_and_skips_judgment_entirely(deps):
    """Resource unavailable is handled not failed and skips judgment entirely."""
    protocol, try_dispatch = _resource_unavailable_protocol()
    resource_deps = replace(deps, protocol_set=ProtocolSet(protocols=(*deps.protocol_set.all(), protocol)))
    reference_agent = resource_deps.registry.get("reference_agent")
    reference_agent.try_dispatch = types.MethodType(try_dispatch, reference_agent)

    agent = _happy_path_agent(risk_score="0.9", selected="dispatch_something")
    # needs_insight defaults to True on this protocol -- an empty dispatch here proves
    # build_insight/judge_success were never reached (_ScriptedAgent.process raises on any
    # unscripted prompt), i.e. the resource-unavailable short-circuit fires ahead of them.
    insights_agent = _ScriptedAgent({})

    result = process_report(resource_deps, agent, insights_agent, "suspicious activity", "telegram", "2026-08-20T10:00:00", "viewer-1")

    assert result.outcome == "handled_resource_unavailable"
    event = resource_deps.persistence.fetch_event(result.event_id)
    # No profile hook configured on this fixture -- core's own generic (English) fallback fact.
    assert event["report_text"] is not None
    assert "drone" in event["report_text"]
    assert "east_gate" in event["report_text"]
    # The commander alert (and any alternatives) must never appear in the reporter's own text.
    assert "Commander alert" not in event["report_text"]
    assert "Alternatives" not in event["report_text"]
    assert event["commander_alert_text"] is not None
    assert "Commander alert" in event["commander_alert_text"]
    assert "no alternatives could be determined" in event["commander_alert_text"]  # no hook configured


def test_resource_unavailable_uses_the_profiles_description_hook_when_supplied(deps):
    """Resource unavailable uses the profiles description hook when supplied."""
    protocol, try_dispatch = _resource_unavailable_protocol()
    calls = []

    def description_hook(resource_kind, area, reason, registry):
        calls.append((resource_kind, area, reason))
        return "no drone could be sent to the east sector", "CAM-01 covers the area; 1 ready drone remains"

    resource_deps = replace(
        deps,
        protocol_set=ProtocolSet(protocols=(*deps.protocol_set.all(), protocol)),
        resource_unavailable_description=description_hook,
    )
    reference_agent = resource_deps.registry.get("reference_agent")
    reference_agent.try_dispatch = types.MethodType(try_dispatch, reference_agent)

    agent = _happy_path_agent(risk_score="0.9", selected="dispatch_something")
    insights_agent = _ScriptedAgent({})

    result = process_report(resource_deps, agent, insights_agent, "suspicious activity", "telegram", "2026-08-20T10:00:00", "viewer-1")

    assert result.outcome == "handled_resource_unavailable"
    event = resource_deps.persistence.fetch_event(result.event_id)
    # The reporter sees the fact sentence, never the alternatives.
    assert "no drone could be sent to the east sector" in event["report_text"]
    assert "CAM-01" not in event["report_text"]
    # The commander alert carries both, on its own separate column.
    assert "no drone could be sent to the east sector" in event["commander_alert_text"]
    assert "CAM-01 covers the area; 1 ready drone remains" in event["commander_alert_text"]
    assert calls == [("drone", "east_gate", "no ready drones available")]


def test_resource_unavailable_survives_the_description_hook_raising(deps):
    """Resource unavailable survives the description hook raising."""
    protocol, try_dispatch = _resource_unavailable_protocol()

    def broken_hook(resource_kind, area, reason, registry):
        raise RuntimeError("boom")

    resource_deps = replace(
        deps,
        protocol_set=ProtocolSet(protocols=(*deps.protocol_set.all(), protocol)),
        resource_unavailable_description=broken_hook,
    )
    reference_agent = resource_deps.registry.get("reference_agent")
    reference_agent.try_dispatch = types.MethodType(try_dispatch, reference_agent)

    agent = _happy_path_agent(risk_score="0.9", selected="dispatch_something")
    insights_agent = _ScriptedAgent({})

    result = process_report(resource_deps, agent, insights_agent, "suspicious activity", "telegram", "2026-08-20T10:00:00", "viewer-1")

    assert result.outcome == "handled_resource_unavailable"  # never surfaces as a hard failure
    event = resource_deps.persistence.fetch_event(result.event_id)
    # Falls all the way back to core's own generic fact and "no alternatives" text.
    assert "drone" in event["report_text"]
    assert "no alternatives could be determined" in event["commander_alert_text"]


# Availability fields for absence reports (Stage 3, )


def _attendance_gated_deps(deps):
    """Attendance gated deps."""
    return replace(
        deps,
        event_type_registry=EventTypeRegistry(
            types=(*deps.event_type_registry.types, "attendance"),
            required_fields={"attendance": ("availability_start", "availability_end")},
        ),
    )


def test_absence_with_interval_proceeds_without_a_hold(deps):
    """The reporter stated both ends of the interval up front — the
    required-fields gate has nothing to ask for, and the event runs
    normally."""
    gated_deps = _attendance_gated_deps(deps)
    agent = _happy_path_agent(risk_score="0.1", selected="status_check")
    agent._dispatch["Extract this operational event"] = (
        '{"classification": "attendance", "area": null, "entities": [], "description": "unavailable", '
        '"severity": null, "occurred_at": "2026-08-20T09:00:00", '
        '"availability_start": "2026-08-25T00:00:00Z", "availability_end": "2026-08-27T00:00:00Z", '
        '"absence_reason": "family matter"}'
    )
    insights_agent = type("I", (), {"process": lambda self, text, tools: _FakeResult("success", "insight")})()

    result = process_report(
        gated_deps, agent, insights_agent, "Michael: I won't be available June 25-27, family matter",
        "telegram", "2026-08-20T10:00:00", "michael",
    )

    assert result.outcome == "succeeded"
    event = gated_deps.persistence.fetch_event(result.event_id)
    assert event["availability_start"] == "2026-08-25T00:00:00"
    assert event["availability_end"] == "2026-08-27T00:00:00"
    assert event["absence_reason"] == "family matter"
    assert gated_deps.persistence.list_held_events("event_data") == []


def test_available_status_does_not_ask_for_an_absence_interval(deps):
    """Availability bounds are conditional on an actual absence declaration."""

    gated_deps = _attendance_gated_deps(deps)
    agent = _happy_path_agent(risk_score="0.1", selected="status_check")
    agent._dispatch["Extract this operational event"] = (
        '{"classification": "attendance", "area": null, "entities": [], '
        '"description": "available tonight", "severity": null, '
        '"occurred_at": "2026-08-20T09:00:00", "availability_start": null, '
        '"availability_end": null, "absence_reason": null}'
    )
    insights_agent = type("I", (), {"process": lambda self, text, tools: _FakeResult("success", "insight")})()

    result = process_report(
        gated_deps, agent, insights_agent, "I am available tonight",
        "telegram", "2026-08-20T10:00:00", "michael",
    )

    assert result.outcome == "succeeded"
    assert gated_deps.persistence.list_held_events("event_data") == []


def test_absence_without_interval_holds_for_exactly_the_two_missing_fields(deps):
    """"Michael: I won't be available, family matter" — the reason is stated,
    the interval isn't. Stored as reported; the event_data hold asks for
    exactly the two missing fields, never a guessed range."""
    gated_deps = _attendance_gated_deps(deps)
    agent = _ScriptedAgent(
        {
            "Extract this operational event": (
                '{"classification": "attendance", "area": null, "entities": [], "description": "unavailable", '
                '"severity": null, "occurred_at": "2026-08-20T09:00:00", '
                '"availability_start": null, "availability_end": null, "absence_reason": "family matter"}'
            ),
            "Write one concise question": "When does this start and end?",
        }
    )
    insights_agent = _ScriptedAgent({})

    result = process_report(
        gated_deps, agent, insights_agent, "Michael: I won't be available, family matter",
        "telegram", "2026-08-20T10:00:00", "michael",
    )

    assert result.outcome == "waiting_for_event_data"
    event = gated_deps.persistence.fetch_event(result.event_id)
    assert event["classification"] == "attendance"
    assert event["absence_reason"] == "family matter"
    assert event["availability_start"] is None
    assert event["availability_end"] is None
    [hold] = gated_deps.persistence.list_held_events("event_data")
    assert set(hold["missing_fields"]) == {"availability_start", "availability_end"}


def test_absence_reply_fills_interval_and_resumes(deps):
    """Absence reply fills interval and resumes."""
    gated_deps = _attendance_gated_deps(deps)
    agent = _happy_path_agent(risk_score="0.1", selected="status_check")
    agent._dispatch["Extract this operational event"] = (
        '{"classification": "attendance", "area": null, "entities": [], "description": "unavailable", '
        '"severity": null, "occurred_at": "2026-08-20T09:00:00", '
        '"availability_start": null, "availability_end": null, "absence_reason": "family matter"}'
    )
    agent._dispatch["Write one concise question"] = "When does this start and end?"
    insights_agent = type("I", (), {"process": lambda self, text, tools: _FakeResult("success", "insight")})()

    event_id = begin_report(
        gated_deps, "Michael: I won't be available, family matter", "telegram", "2026-08-20T10:00:00", "michael",
        conversation_id="c-michael",
    )
    held = run_report_extraction(gated_deps, event_id, agent, insights_agent)
    assert held.outcome == "waiting_for_event_data"

    reply_agent = _ScriptedAgent(
        {
            "pending request for missing event details": (
                '{"addresses_request": true, "updates": {"availability_start": "2026-08-25T00:00:00Z", '
                '"availability_end": "2026-08-27T00:00:00Z"}, "reply_text": "Recorded, thank you."}'
            )
        }
    )
    reply = apply_event_data_reply(gated_deps, reply_agent, "from the 25th to the 27th", "michael", "c-michael")

    assert reply is not None
    assert reply.updates["availability_start"] == "2026-08-25T00:00:00"
    assert reply.updates["availability_end"] == "2026-08-27T00:00:00"
    assert gated_deps.persistence.list_held_events("event_data") == []

    resumed = resume_after_event_data(gated_deps, held.event_id, agent, insights_agent)

    assert resumed.outcome == "succeeded"
    event = gated_deps.persistence.fetch_event(held.event_id)
    assert event["availability_start"] == "2026-08-25T00:00:00"
    assert event["availability_end"] == "2026-08-27T00:00:00"


def test_absence_reply_with_end_before_start_is_rejected(deps):
    """Absence reply with end before start is rejected."""
    from orchestrator.main_agent import OrchestrationParseError

    gated_deps = _attendance_gated_deps(deps)
    event_id = begin_report(
        gated_deps, "Michael: I won't be available, family matter", "telegram", "2026-08-20T10:00:00", "michael",
        conversation_id="c-michael",
    )
    gated_deps.persistence.update_event(event_id, {"classification": "attendance"})
    create_event_data_hold(
        gated_deps.persistence, event_id, ("availability_start", "availability_end"),
        "When does this start and end?", (),
    )

    reply_agent = _ScriptedAgent(
        {
            "pending request for missing event details": (
                '{"addresses_request": true, "updates": {"availability_start": "2026-08-27T00:00:00Z", '
                '"availability_end": "2026-08-25T00:00:00Z"}, "reply_text": "Recorded."}'
            )
        }
    )

    with pytest.raises(OrchestrationParseError):
        apply_event_data_reply(gated_deps, reply_agent, "from the 27th to the 25th", "michael", "c-michael")


def test_required_fields_gate_resumes_into_risk_assessment_once_resolved(deps):
    """Once the gate's missing field is resolved, the event proceeds through
    risk assessment and protocol selection exactly as it would have if the
    field had been present from the start."""
    gated_deps = replace(
        deps,
        event_type_registry=EventTypeRegistry(types=deps.event_type_registry.types, required_fields={"fire": ("area", "severity")}),
    )
    agent = _happy_path_agent(risk_score="0.1", selected="status_check")
    agent._dispatch["Extract this operational event"] = (
        '{"classification": "fire", "area": "north_sector", "entities": [], "description": "smoke", '
        '"severity": null, "occurred_at": "2026-08-20T09:00:00"}'
    )
    agent._dispatch["Write one concise question"] = "How severe is it?"
    insights_agent = type("I", (), {"process": lambda self, text, tools: _FakeResult("success", "insight")})()

    result = process_report(gated_deps, agent, insights_agent, "smoke at gate 3", "telegram", "2026-08-20T10:00:00", "viewer-1")
    assert result.outcome == "waiting_for_event_data"

    gated_deps.persistence.update_event(result.event_id, {"severity": "moderate"})
    resumed = resume_after_event_data(gated_deps, result.event_id, agent, insights_agent)

    assert resumed.outcome == "succeeded"
    assert gated_deps.persistence.fetch_event(result.event_id)["selected_protocol"] == "status_check"


def test_per_step_required_fields_still_work_alongside_the_new_event_type_gate(deps):
    """The new, early, event-type-level gate (item #6) is additional to, not
    a replacement for, the existing per-protocol-step required-field
    mechanism (protocols/executor.py) — proves both fire correctly, end to
    end, without interfering with each other."""
    agent = _ScriptedAgent(
        {
            # classification="fire" has no event-type-level required fields
            # declared in this fixture's plain registry — the new gate is a
            # no-op here; the per-step mechanism below is what fires.
            "Extract this operational event": (
                '{"classification": "fire", "area": "north_sector", "entities": [], "description": "smoke", '
                '"severity": "moderate", "occurred_at": "2026-08-20T09:00:00"}'
            ),
            "RISK_SCORE": "RISK_SCORE: 0.1\nREASON: r",
            "Choose the protocol": "SELECTED: status_check\nREASON: fits",
            "participating in the": json.dumps(
                {
                    "steps": [
                        {
                            "step_id": "s1",
                            "agent_name": "reference_agent",
                            "task": "check status",
                            "depends_on": [],
                            "required_event_fields": ["entities"],
                        }
                    ]
                }
            ),
            "Write one concise question": "What entities are involved?",
            "VERDICT:": "VERDICT: success\nREASONING: r",
        }
    )
    insights_agent = type("I", (), {"process": lambda self, text, tools: _FakeResult("success", "insight")})()

    result = process_report(deps, agent, insights_agent, "smoke at gate 3", "telegram", "2026-08-20T10:00:00", "viewer-1")

    assert result.outcome == "waiting_for_event_data"
    [hold] = deps.persistence.list_held_events("event_data")
    assert hold["missing_fields"] == ["entities"]
    assert hold["waiting_step_ids"] == ["s1"]  # non-empty — this is a per-step hold, not the new gate's

    deps.persistence.update_event(result.event_id, {"entities": ["gate-3"]})
    resumed = resume_after_event_data(deps, result.event_id, agent, insights_agent)

    assert resumed.outcome == "succeeded"


def test_required_fields_floor_is_enforced_end_to_end_for_the_reported_fire_bug(deps):
    """The original reported fire-without-area bug,
    closed end to end: a "fire" report with no area (i) is held by the
    pre-formulation gate before protocol selection ever runs, and (ii) once
    area is supplied, every persisted step's required_event_fields includes
    "area" even though the scripted formulation response below doesn't
    declare it — the static floor, not the model, is what guarantees it."""
    gated_deps = replace(
        deps,
        event_type_registry=EventTypeRegistry(types=deps.event_type_registry.types, required_fields={"fire": ("area",)}),
    )
    agent = _ScriptedAgent(
        {
            "Extract this operational event": (
                '{"classification": "fire", "area": null, "entities": [], "description": "smoke", '
                '"severity": "moderate", "occurred_at": "2026-08-20T09:00:00"}'
            ),
            "Write one concise question": "Which area is this in?",
            "RISK_SCORE": "RISK_SCORE: 0.1\nREASON: never reached before area is supplied",
            "Choose the protocol": "SELECTED: status_check\nREASON: fits",
            "participating in the": json.dumps(
                {
                    "steps": [
                        {
                            "step_id": "s1",
                            "agent_name": "reference_agent",
                            "task": "check status",
                            "depends_on": [],
                            "required_event_fields": [],  # model doesn't declare it — the floor must
                        }
                    ]
                }
            ),
            "VERDICT:": "VERDICT: success\nREASONING: r",
        }
    )
    insights_agent = type("I", (), {"process": lambda self, text, tools: _FakeResult("success", "insight")})()

    result = process_report(gated_deps, agent, insights_agent, "fire, no location given", "telegram", "2026-08-20T10:00:00", "viewer-1")

    assert result.outcome == "waiting_for_event_data"
    [hold] = gated_deps.persistence.list_held_events("event_data")
    assert hold["missing_fields"] == ["area"]
    assert hold["waiting_step_ids"] == []  # the early gate, not a per-step hold
    assert not any("RISK_SCORE" in call for call in agent.calls)  # protocol selection never ran

    gated_deps.persistence.update_event(result.event_id, {"area": "north_sector"})
    resumed = resume_after_event_data(gated_deps, result.event_id, agent, insights_agent)

    assert resumed.outcome == "succeeded"
    persisted_steps = gated_deps.persistence.fetch_event(result.event_id)["steps"]
    assert persisted_steps and all("area" in step["required_event_fields"] for step in persisted_steps)


def test_clarification_resolution_re_gates_for_the_newly_chosen_classifications_required_fields(deps):
    """The clarification-resolution path (`continue_after_clarification`) now
    re-runs the required-fields gate for the classification the commander
    just chose. Reaching a clarification hold only ever validated
    UNCLASSIFIED_TYPE's fixed `("area",)` floor — resolving into a real
    type with its own required fields still missing must pause immediately,
    the same way the fresh-extraction path would have, rather than letting
    the event proceed into risk assessment with a required field unresolved."""
    gated_deps = replace(
        deps,
        event_type_registry=EventTypeRegistry(types=deps.event_type_registry.types, required_fields={"fire": ("area",)}),
    )
    agent = _ScriptedAgent(
        {
            "Write one concise question": "Which area is this in?",
            "RISK_SCORE": "RISK_SCORE: 0.1\nREASON: never reached — the re-gate must block first",
        }
    )
    insights_agent = type("I", (), {"process": lambda self, text, tools: _FakeResult("success", "insight")})()

    event_id = begin_report(gated_deps, "something happened, unclear what", "telegram", "2026-08-20T10:00:00", "viewer-1")
    hold_id = create_clarification_hold(gated_deps.persistence, event_id, "something happened, unclear what")

    resumed = resume_after_clarification(gated_deps, agent, insights_agent, hold_id, "commander-1", PermissionLevel.COMMANDER, "fire")

    assert resumed.outcome == "waiting_for_event_data"
    assert gated_deps.persistence.fetch_event(event_id)["classification"] == "fire"
    [hold] = gated_deps.persistence.list_held_events("event_data")
    assert hold["missing_fields"] == ["area"]
    assert hold["waiting_step_ids"] == []  # the early gate, not a per-step hold
    assert not any("RISK_SCORE" in call for call in agent.calls)  # risk assessment never ran


def test_clarification_resolution_does_not_re_gate_when_the_registry_declares_no_required_fields(deps):
    """Regression: a profile that declares no EVENT_TYPE_REQUIRED_FIELDS (the
    plain `deps` fixture) sees no behavior change on the clarification path
    — this mirrors `test_resume_after_clarification_continues_at_risk_
    assessment_not_extraction` and must keep proceeding straight to risk
    assessment exactly as before."""
    agent = _ScriptedAgent(
        {
            "RISK_SCORE": "RISK_SCORE: 0.1\nREASON: r",
            "Choose the protocol": "SELECTED: status_check\nREASON: fits",
            "participating in the": "AGENT: reference_agent\nTASK: check gate 3",
            "VERDICT:": "VERDICT: success\nREASONING: r",
        }
    )
    insights_agent = type("I", (), {"process": lambda self, text, tools: _FakeResult("success", "insight")})()

    event_id = begin_report(deps, "something happened, unclear what", "telegram", "2026-08-20T10:00:00", "viewer-1")
    deps.persistence.update_event(event_id, {"area": "north_sector"})
    hold_id = create_clarification_hold(deps.persistence, event_id, "something happened, unclear what")

    resumed = resume_after_clarification(deps, agent, insights_agent, hold_id, "commander-1", PermissionLevel.COMMANDER, "fire")

    assert resumed.outcome == "succeeded"
    assert deps.persistence.list_held_events("event_data") == []


def test_persist_step_outcomes_matches_a_step_id_less_step_whose_task_text_was_mutated(deps):
    """Regression for the bug found while scoping the required-fields floor
    merge: a step with step_id == "" (every step from the legacy AGENT:/
    TASK: formulation fallback) and a non-empty required_event_fields gets
    its task_text rewritten by _execute_protocol_plan's event-data
    injection before it runs — the outcome that comes back therefore
    carries a step that differs *by value* from the original. Matching
    must land on the right position regardless (see _persist_step_outcomes'
    own docstring for why position, not value equality, is now used)."""
    event_id = begin_report(deps, "raw", "telegram", "2026-08-20T10:00:00", "viewer-1")
    original_step = Step(
        agent_name="reference_agent", task_text="check status", allowed_tools=("check_status",),
        required_event_fields=("area",),
    )
    mutated_step = replace(original_step, task_text="check status\n\nCurrent validated event data JSON: {}")
    outcome = StepOutcome(step=mutated_step, result_text="done", attempt_count=1, succeeded=True, status="succeeded")

    flows_module._persist_step_outcomes(deps, event_id, (original_step,), (outcome,))

    [persisted] = deps.persistence.fetch_event(event_id)["steps"]
    assert persisted["step_index"] == 0
    assert persisted["agent_name"] == "reference_agent"
    assert persisted["task_text"] == "check status"  # the original step's, not the mutated copy
    assert persisted["required_event_fields"] == ["area"]
    assert persisted["result_text"] == "done"
    assert persisted["status"] == "succeeded"


def test_execution_injects_complete_event_provenance_into_every_step(deps, monkeypatch):
    """Write-capable agents must receive the immutable sender/provenance envelope,
    even when their formulated step declares no event-data fields.  Attendance
    writes use these values to avoid an unnecessary UNCLEAR_TASK refusal."""
    event_id = begin_report(
        deps,
        "member is unavailable for reserve duty",
        "telegram",
        "2026-08-20T10:00:00",
        "9000000000000000",
        source_message_id="telegram-msg-42",
    )
    deps.persistence.update_event(
        event_id,
        {
            "availability_start": "2026-08-20T12:00:00+00:00",
            "availability_end": "2026-08-22T18:00:00+00:00",
            "absence_reason": "reserve duty",
        },
    )
    captured = {}

    def _capture_execute(steps, agents_by_name, settings, **kwargs):
        captured["task"] = steps[0].task_text
        outcome = StepOutcome(step=steps[0], result_text="stored", attempt_count=1, succeeded=True)
        return ProtocolRunResult((outcome,), completed=True)

    monkeypatch.setattr(flows_module, "execute_steps", _capture_execute)
    monkeypatch.setattr(
        flows_module,
        "_finish_protocol_assessment",
        lambda *args, **kwargs: flows_module.FlowResult(event_id, "succeeded", "ok"),
    )
    protocol = Protocol(
        name="record_attendance_response",
        description="record attendance",
        participating_agents=("reference_agent",),
        approved_tools=("record_attendance_response",),
        expected_success_output="stored",
        criticality=CriticalityLevel.LOW,
        approval_flag=False,
    )
    step = Step(agent_name="reference_agent", task_text="store the report", allowed_tools=())

    result = flows_module._execute_protocol_plan(deps, event_id, object(), object(), protocol, (step,), ())

    assert result.outcome == "succeeded"
    assert '"sender_identity": "9000000000000000"' in captured["task"]
    assert '"source_message_id": "telegram-msg-42"' in captured["task"]
    assert '"received_at": "2026-08-20T10:00:00"' in captured["task"]
    assert '"raw_text": "member is unavailable for reserve duty"' in captured["task"]


def test_approved_async_event_is_not_expired_by_its_original_queue_deadline(deps):
    """Approved async event is not expired by its original queue deadline."""
    event_id = begin_report(
        deps,
        "camera update awaiting commander approval",
        "telegram",
        "2026-08-20T10:00:00",
        "viewer-1",
        deadline_at="2000-01-01T00:00:00+00:00",
    )
    deps.persistence.update_event(
        event_id,
        {"approval_answered_at": "2026-08-20T10:30:00+00:00"},
    )

    assert flows_module._deadline_failure(deps, event_id, "formulation") is None


def test_persist_step_outcomes_matches_multiple_step_id_less_steps_by_position(deps):
    """The same guarantee across more than one step_id == "" step in the same
    plan — proves the position-based match isn't a single-step coincidence
    (every legacy-formatted plan has every step id-less, not just one)."""
    event_id = begin_report(deps, "raw", "telegram", "2026-08-20T10:00:00", "viewer-1")
    step_a = Step(agent_name="reference_agent", task_text="check gate 3", allowed_tools=(), required_event_fields=("area",))
    step_b = Step(agent_name="history_agent", task_text="check history", allowed_tools=(), required_event_fields=("entities",))
    outcome_a = StepOutcome(
        step=replace(step_a, task_text="check gate 3\n\nCurrent validated event data JSON: {}"),
        result_text="a done", attempt_count=1, succeeded=True, status="succeeded",
    )
    outcome_b = StepOutcome(
        step=replace(step_b, task_text="check history\n\nCurrent validated event data JSON: {}"),
        result_text="b done", attempt_count=1, succeeded=True, status="succeeded",
    )

    flows_module._persist_step_outcomes(deps, event_id, (step_a, step_b), (outcome_a, outcome_b))

    persisted = sorted(deps.persistence.fetch_event(event_id)["steps"], key=lambda row: row["step_index"])
    assert [row["agent_name"] for row in persisted] == ["reference_agent", "history_agent"]
    assert [row["result_text"] for row in persisted] == ["a done", "b done"]


def test_drone_selection_result_creates_a_resumable_hold(deps, monkeypatch):
    """Drone selection result creates a resumable hold."""
    event_id = begin_report(deps, "return the drone", "telegram", "2026-08-20T10:00:00", "commander-1")
    step = Step(
        agent_name="reference_agent", task_text="recall", allowed_tools=("check_status",), step_id="recall-1"
    )
    outcome = StepOutcome(
        step=step,
        result_text="- Eagle-1\n- Falcon-2",
        attempt_count=1,
        succeeded=True,
        selection_required=True,
    )
    monkeypatch.setattr(
        flows_module,
        "execute_steps",
        lambda *args, **kwargs: ProtocolRunResult((outcome,), completed=True),
    )
    protocol = Protocol(
        name="return_drone_to_base",
        description="recall",
        participating_agents=("reference_agent",),
        approved_tools=("check_status",),
        expected_success_output="selection or recall",
        criticality=CriticalityLevel.MEDIUM,
        approval_flag=True,
    )

    result = flows_module._execute_protocol_plan(deps, event_id, object(), object(), protocol, (step,), ())

    assert result.outcome == "waiting_for_drone_selection"
    [hold] = deps.persistence.list_held_events("event_data")
    assert hold["missing_fields"] == ["drone_selection"]
    assert hold["waiting_step_ids"] == ["recall-1"]
    assert deps.persistence.fetch_event(event_id)["outcome"] is None


class _TestSurveillanceAgent(SurveillanceAgent):
    """TestSurveillanceAgent."""
    surveillance_db_path = ""


class _DecidingSurveillance:
    """DecidingSurveillance."""
    name = "surveillance_agent"

    def __init__(self, inner, decide):
        """Initialize this test helper."""
        self._inner = inner
        self._decide = decide
        self.calls = []
        self.surveillance_store = inner.surveillance_store

    def exposed_tools(self):
        """Exposed tools."""
        return self._inner.exposed_tools()

    def process(self, text, allowed_tools, invocation_policy=None):
        """Process."""
        self.calls.append((text, tuple(allowed_tools)))
        return self._decide(self._inner, text, allowed_tools)


def _live_surveillance(tmp_path):
    """Live surveillance."""
    _TestSurveillanceAgent.surveillance_db_path = str(tmp_path / "drone-selection.db")
    agent = _TestSurveillanceAgent(model="m")
    agent.dispatch_drone_to_area("north_gate", "first")
    agent.dispatch_drone_to_area("south_sector", "second")
    return agent


def _drone_selection_hold(deps, question):
    """Drone selection hold."""
    event_id = begin_report(deps, "return the drone", "telegram", "2026-08-20T10:00:00", "commander-1")
    create_event_data_hold(deps.persistence, event_id, ("drone_selection",), question, ("recall-1",))
    [hold] = deps.persistence.list_held_events("event_data")
    return hold


def _deps_with_surveillance(deps, agent):
    """Deps with surveillance."""
    existing = {item.name: item for item in deps.registry.all()}
    existing[agent.name] = agent
    return replace(deps, registry=AgentRegistry(existing))


def _result_from_tool(raw):
    """Result from tool."""
    return AgentResult(
        status="success",
        text=str(raw),
        selection_required=getattr(raw, "selection_required", False),
    )


def test_drone_selection_reply_is_forwarded_to_the_surveillance_agent(deps, tmp_path):
    """Drone selection reply is forwarded to the surveillance agent."""
    inner = _live_surveillance(tmp_path)
    deciding = _DecidingSurveillance(inner, lambda real, text, allowed: _result_from_tool(real.return_all_drones_to_base()))
    scoped = _deps_with_surveillance(deps, deciding)
    hold = _drone_selection_hold(scoped, "- Eagle-1\n- Falcon-2")

    apply_drone_selection_reply(scoped, "operator reply", hold, resolved_by="commander-1")

    prompt, allowed = deciding.calls[0]
    assert prompt == scoped.message_catalog.text(
        "orchestrator.drone_selection.task",
        choices="- Eagle-1\n- Falcon-2",
        reply="operator reply",
    )
    assert allowed == ("return_drone_to_base", "return_all_drones_to_base")


def test_drone_selection_reply_task_uses_the_active_message_catalog(deps, tmp_path):
    """Drone selection reply task uses the active message catalog."""
    inner = _live_surveillance(tmp_path)
    deciding = _DecidingSurveillance(inner, lambda real, text, allowed: _result_from_tool(real.return_all_drones_to_base()))
    hebrew = get_catalog("he")
    scoped = replace(_deps_with_surveillance(deps, deciding), message_catalog=hebrew)
    hold = _drone_selection_hold(scoped, "- Eagle-1\n- Falcon-2")

    apply_drone_selection_reply(scoped, "operator reply", hold, resolved_by="commander-1")

    prompt, _allowed = deciding.calls[0]
    assert prompt == hebrew.text(
        "orchestrator.drone_selection.task",
        choices="- Eagle-1\n- Falcon-2",
        reply="operator reply",
    )
    assert prompt != get_catalog("en").text(
        "orchestrator.drone_selection.task",
        choices="- Eagle-1\n- Falcon-2",
        reply="operator reply",
    )


def test_drone_selection_reply_recalls_every_drone_when_the_agent_calls_return_all(deps, tmp_path):
    """Drone selection reply recalls every drone when the agent calls return all."""
    inner = _live_surveillance(tmp_path)
    deciding = _DecidingSurveillance(inner, lambda real, text, allowed: _result_from_tool(real.return_all_drones_to_base()))
    scoped = _deps_with_surveillance(deps, deciding)
    hold = _drone_selection_hold(scoped, "- Eagle-1\n- Falcon-2")

    result = apply_drone_selection_reply(scoped, "operator reply", hold, resolved_by="commander-1")

    assert result.status == "succeeded"
    assert inner.surveillance_store.get_active_missions() == []
    assert scoped.persistence.list_held_events("event_data") == []
    assert scoped.persistence.fetch_event(result.event_id)["outcome"] == "succeeded"


def test_drone_selection_reply_recalls_one_drone_when_the_agent_passes_an_identifier(deps, tmp_path):
    """Drone selection reply recalls one drone when the agent passes an identifier."""
    inner = _live_surveillance(tmp_path)
    selected = inner.surveillance_store.get_active_missions()[0]["callsign"]

    def _one(real, text, allowed):
        return _result_from_tool(real.return_drone_to_base(selected))

    deciding = _DecidingSurveillance(inner, _one)
    scoped = _deps_with_surveillance(deps, deciding)
    hold = _drone_selection_hold(scoped, "- Eagle-1\n- Falcon-2")

    result = apply_drone_selection_reply(scoped, "operator reply", hold, resolved_by="commander-1")

    assert result.status == "succeeded"
    remaining = inner.surveillance_store.get_active_missions()
    assert len(remaining) == 1
    assert remaining[0]["callsign"] != selected


def test_drone_selection_reply_keeps_the_hold_when_selection_is_still_required(deps, tmp_path):
    """Drone selection reply keeps the hold when selection is still required."""
    inner = _live_surveillance(tmp_path)
    deciding = _DecidingSurveillance(inner, lambda real, text, allowed: _result_from_tool(real.return_drone_to_base("")))
    scoped = _deps_with_surveillance(deps, deciding)
    hold = _drone_selection_hold(scoped, "- Eagle-1\n- Falcon-2")

    result = apply_drone_selection_reply(scoped, "operator reply", hold, resolved_by="commander-1")

    assert result.status == "waiting_for_drone_selection"
    assert len(inner.surveillance_store.get_active_missions()) == 2
    assert scoped.persistence.list_held_events("event_data")[0]["hold_id"] == hold["hold_id"]
    assert scoped.persistence.fetch_event(result.event_id)["outcome"] is None


def test_precedent_lookup_still_runs_when_the_target_events_occurred_at_is_unresolved(deps):
    """DIAGNOSTIC_FINDINGS.MD A.2's related risk, fixed alongside the recency
    bug: `_look_up_precedent_if_possible` used to skip precedent lookup
    entirely whenever the *target* event's own occurred_at was unresolved
    (`if event["occurred_at"] is None: return ()`), rather than falling back
    to received_at as the lookback anchor. A real prior event is planted so
    that a genuinely-skipped lookup (returns `()` unconditionally) is
    distinguishable from one that ran and legitimately found nothing."""
    prior_id = begin_report(deps, "fire near the north gate last week", "sensor", "2026-08-15T10:00:00", "sensor-1")
    deps.persistence.update_event(
        prior_id, {"classification": "fire", "area": "north_sector", "occurred_at": "2026-08-15T10:00:00", "outcome": "succeeded"}
    )

    event_id = begin_report(deps, "smoke near the north gate", "telegram", "2026-08-20T10:00:00", "viewer-1")
    deps.persistence.update_event(
        event_id, {"classification": "fire", "area": "north_sector", "occurred_at": None}
    )
    event = deps.persistence.fetch_event(event_id)

    matches = flows_module._look_up_precedent_if_possible(deps, event_id, event)

    assert {match.event_id for match in matches} == {prior_id}


def test_process_report_low_risk_unflagged_protocol_runs_to_success(deps, caplog):
    """Process report low risk unflagged protocol runs to success."""
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


class _ScriptedComposerAgent:
    """A duck-typed ReportComposerAgent stand-in — no crewai/model involved."""

    def __init__(self, response_text):
        """Initialize this test helper."""
        self._response_text = response_text
        self.calls = []

    def process(self, text, allowed_tools, *, invocation_policy=None):
        """Process."""
        self.calls.append(text)
        return _FakeResult("success", self._response_text)


def test_report_text_is_composed_and_persisted_when_rich_reports_enabled(deps):
    """Report text is composed and persisted when rich reports enabled."""
    deps.settings_store.rich_reports_enabled = True
    composer = _ScriptedComposerAgent("Understood: smoke at gate 3. Handled successfully.")
    deps_with_composer = replace(deps, report_composer_agent=composer)
    agent = _happy_path_agent(risk_score="0.1", selected="status_check", verdict="success")
    insights_agent = type("I", (), {"process": lambda self, text, tools: _FakeResult("success", "no notable precedent")})()

    result = process_report(deps_with_composer, agent, insights_agent, "smoke at gate 3", "telegram", "2026-08-20T10:00:00", "viewer-1")

    event = deps.persistence.fetch_event(result.event_id)
    assert event["report_text"] == "Understood: smoke at gate 3. Handled successfully."
    assert composer.calls  # the composer was actually invoked, once


def test_situational_picture_report_returns_the_picture_without_generic_rich_report(deps):
    """Situational picture report returns the picture without generic rich report."""
    deps.settings_store.rich_reports_enabled = True
    composer = _ScriptedComposerAgent("this generic report must not be used")
    deps_with_composer = replace(deps, report_composer_agent=composer)
    event_id = begin_report(
        deps_with_composer, "show the situational picture", "telegram", "2026-08-20T10:00:00",
        "commander-1", sender_permission_level="commander", telegram_chat_type="private",
    )
    flows_module.record_event_state(
        deps.persistence, event_id, {"selected_protocol": "query_situational_picture"}
    )
    record_step_executions(
        deps.persistence,
        event_id,
        (
            StepExecutionEnvelope(
                step_index=0,
                agent_name="surveillance_agent",
                task_text="read raw surveillance output",
                allowed_tools=["get_surveillance_overview"],
                result_text="=== RAW SPECIALIST HEADING ===\nCAM-01: ACTIVE",
                attempt_count=1,
                status="succeeded",
            ),
        ),
    )

    flows_module._record_outcome_with_report(
        deps_with_composer, event_id, "succeeded", insight_text="סד״כ: 2 זמינים\nמצלמות: CAM-01 כבויה"
    )

    event = deps.persistence.fetch_event(event_id)
    assert event["report_text"] == "סד״כ: 2 זמינים\nמצלמות: CAM-01 כבויה"
    assert composer.calls == []


def test_report_text_falls_back_to_render_summary_when_no_composer_agent_is_available(deps):
    """Report text falls back to render summary when no composer agent is available."""
    deps.settings_store.rich_reports_enabled = True  # deps.report_composer_agent stays None
    agent = _happy_path_agent(risk_score="0.1", selected="status_check", verdict="success")
    insights_agent = type("I", (), {"process": lambda self, text, tools: _FakeResult("success", "no notable precedent")})()

    result = process_report(deps, agent, insights_agent, "smoke at gate 3", "telegram", "2026-08-20T10:00:00", "viewer-1")

    event = deps.persistence.fetch_event(result.event_id)
    assert event["report_text"]  # a non-empty deterministic fallback, never a model call


def test_report_text_is_absent_when_rich_reports_disabled(deps):
    # deps.settings_store.rich_reports_enabled defaults to False (_FakeSettings)
    """Report text is absent when rich reports disabled."""
    agent = _happy_path_agent(risk_score="0.1", selected="status_check", verdict="success")
    insights_agent = type("I", (), {"process": lambda self, text, tools: _FakeResult("success", "no notable precedent")})()

    result = process_report(deps, agent, insights_agent, "smoke at gate 3", "telegram", "2026-08-20T10:00:00", "viewer-1")

    event = deps.persistence.fetch_event(result.event_id)
    assert event.get("report_text") is None


def test_report_composed_for_a_group_message_uses_viewer_audience_even_from_a_commander(deps):
    # A message posted in a group is a shared, visible surface — protocol/agent/risk
    # internals must not leak into it just because the poster happens to be a commander.
    """Report composed for a group message uses viewer audience even from a commander."""
    deps.settings_store.rich_reports_enabled = True
    composer = _ScriptedComposerAgent("Understood: smoke at gate 3. Handled successfully.")
    deps_with_composer = replace(deps, report_composer_agent=composer)
    agent = _happy_path_agent(risk_score="0.1", selected="status_check", verdict="success")
    insights_agent = type("I", (), {"process": lambda self, text, tools: _FakeResult("success", "no notable precedent")})()

    event_id = begin_report(
        deps_with_composer, "smoke at gate 3", "telegram", "2026-08-20T10:00:00", "commander-1",
        sender_permission_level="commander", telegram_chat_type="supergroup",
    )
    run_report_extraction(deps_with_composer, event_id, agent, insights_agent)

    prompt = composer.calls[0]
    assert "status_check" not in prompt  # protocol name — commander-only detail
    assert "reference_agent" not in prompt  # agent name — commander-only detail


def test_attendance_protocol_is_never_closed_on_precedent(deps):
    """Attendance protocol is never closed on precedent."""
    prior_id = begin_report(
        deps, "availability report", "telegram", "2026-08-20T09:00:00", "viewer-1"
    )
    deps.persistence.update_event(
        prior_id,
        {
            "classification": "fire",
            "area": "north_sector",
            "occurred_at": "2026-08-20T09:00:00",
            "outcome": "succeeded",
        },
    )
    event_id = begin_report(
        deps, "I am available", "telegram", "2026-08-20T10:00:00", "viewer-1"
    )
    deps.persistence.update_event(
        event_id,
        {
            "classification": "fire",
            "area": "north_sector",
            "occurred_at": "2026-08-20T10:00:00",
        },
    )
    attendance_deps = replace(
        deps,
        protocol_set=ProtocolSet(protocols=(
            Protocol(
                name="record_attendance_response",
                description="record attendance",
                participating_agents=("reference_agent",),
                approved_tools=("check_status",),
                expected_success_output="attendance stored",
                criticality=CriticalityLevel.LOW,
                approval_flag=False,
            ),
        )),
    )
    agent = _happy_path_agent(
        risk_score="0.1", selected="record_attendance_response", verdict="success",
        agent_task="record this attendance response",
    )
    insights_agent = type(
        "I", (), {"process": lambda self, text, tools: _FakeResult("success", "attendance stored")}
    )()

    result = flows_module.continue_from_risk_assessment(
        attendance_deps, event_id, agent, insights_agent, originated_from_commander=False
    )

    assert result.outcome == "succeeded"
    event = deps.persistence.fetch_event(event_id)
    assert prior_id in event["precedent_matched_event_ids"]
    assert event["precedent_closed_by_event_id"] is None
    assert event["steps"][0]["result_text"] is not None


def test_process_report_flagged_protocol_holds_for_approval_then_resumes_approved(deps, caplog):
    """Process report flagged protocol holds for approval then resumes approved."""
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
    """Resume after approval rejection declines and records outcome."""
    agent = _happy_path_agent(risk_score="0.9", selected="dispatch_response")
    insights_agent = type("I", (), {"process": lambda self, text, tools: _FakeResult("success", "insight")})()

    held = process_report(deps, agent, insights_agent, "fire at gate 3", "telegram", "2026-08-20T10:00:00", "viewer-1")
    [hold] = deps.persistence.list_held_events("approval")

    resumed = resume_after_approval(deps, agent, insights_agent, hold["hold_id"], "commander-1", PermissionLevel.COMMANDER, "rejected")

    assert resumed.outcome == "declined"
    assert deps.persistence.fetch_event(held.event_id)["outcome"] == "declined"


def test_resume_after_clarification_continues_at_risk_assessment_not_extraction(deps):
    """Resume after clarification continues at risk assessment not extraction."""
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
    """Resume after clarification rejects free text."""
    agent = _ScriptedAgent(
        {
            "Extract this operational event": '{"classification": null, "area": null, "entities": [], "description": null, "severity": null, "occurred_at": null}',
            "Write one concise question": "Which area is this in?",
        }
    )
    insights_agent = type("I", (), {"process": lambda self, text, tools: _FakeResult("success", "insight")})()

    result = process_report(deps, agent, insights_agent, "unclear text", "telegram", "2026-08-20T10:00:00", "viewer-1")
    # item #6's early gate asks for `area` (unclassified's required field)
    # before the clarification hold this test actually exercises exists.
    deps.persistence.update_event(result.event_id, {"area": "north_sector"})
    resume_after_event_data(deps, result.event_id, agent, insights_agent)
    [hold] = deps.persistence.list_held_events("clarification")

    answer = resume_after_clarification(deps, agent, insights_agent, hold["hold_id"], "commander-1", PermissionLevel.COMMANDER, "not_a_real_type")

    assert answer.status == "invalid_classification"


def test_process_request_bypasses_extraction_entirely(deps):
    """Process request bypasses extraction entirely."""
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
    """Process request from a viewer still holds for a flagged protocol."""
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
    """Process message routes question without writing an event."""
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
    """Process message routes conversational directly with no agent routing."""
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
    """Process message still declines a genuine no agent fit question."""
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
    """Process message routes report into the new event flow."""
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
    """Process message routes request into the new event flow."""
    agent = _happy_path_agent(risk_score="0.9", selected="dispatch_response", agent_task="dispatch to gate 3")
    agent._dispatch["kind of message"] = "INTENT: request\nREASON: asks for a response to be dispatched"
    insights_agent = type("I", (), {"process": lambda self, text, tools: _FakeResult("success", "insight")})()

    kind, result = process_message(deps, agent, insights_agent, "please dispatch someone to gate 3", "commander-1", "2026-08-20T10:00:00", is_commander=True)

    assert kind == "request"
    assert result.outcome == "succeeded"


def test_a_held_event_resumes_correctly_after_a_simulated_restart(deps, tmp_path):
    """A held event resumes correctly after a simulated restart."""
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
    """Begin report returns immediately with no model call."""
    agent = _ScriptedAgent({})  # would raise on any .process() call

    event_id = begin_report(deps, "smoke at gate 3", "telegram", "2026-08-20T10:00:00", "viewer-1")

    assert agent.calls == []
    event = deps.persistence.fetch_event(event_id)
    assert event["raw_text"] == "smoke at gate 3"
    assert event["classification"] is None  # extraction hasn't run yet


class _TimeoutThenScriptedAgent:
    """Stage 2: duck-typed agent stand-in whose
    extraction call fails a fixed number of times with a model-layer error
    before behaving like `_ScriptedAgent` — used to verify the one-retry
    behavior without needing a real model or crewai."""

    def __init__(self, dispatch: dict[str, str], *, extraction_failures: int, error_cls=None):
        """Initialize this test helper."""
        self._dispatch = dispatch
        self._extraction_failures = extraction_failures
        self._error_cls = error_cls
        self.calls = []
        self._extraction_attempts = 0

    def process(self, text, allowed_tools):
        """Process."""
        self.calls.append(text)
        if "Extract this operational event" in text:
            self._extraction_attempts += 1
            if self._extraction_attempts <= self._extraction_failures:
                raise self._error_cls("main_agent", "the model call failed")
        for keyword, response_text in self._dispatch.items():
            if keyword in text:
                return _FakeResult("success", response_text)
        raise AssertionError(f"no scripted response for prompt starting: {text[:150]!r}")


def test_extraction_retries_once_on_a_model_timeout_then_succeeds(deps, caplog):
    """Extraction retries once on a model timeout then succeeds."""
    from agents.errors import AgentTimeoutError

    agent = _TimeoutThenScriptedAgent(
        {
            "Extract this operational event": _extraction_response(),
            "RISK_SCORE": "RISK_SCORE: 0.1\nREASON: assessed",
            "Choose the protocol": "SELECTED: status_check\nREASON: fits",
            "participating in the": "AGENT: reference_agent\nTASK: check gate 3",
            "VERDICT:": "VERDICT: success\nREASONING: matches expected output",
        },
        extraction_failures=1,
        error_cls=AgentTimeoutError,
    )
    insights_agent = type("I", (), {"process": lambda self, text, tools: _FakeResult("success", "insight")})()

    with caplog.at_level("INFO"):
        result = process_report(deps, agent, insights_agent, "smoke at gate 3", "telegram", "2026-08-20T10:00:00", "viewer-1")

    assert result.outcome == "succeeded"
    assert agent._extraction_attempts == 2  # exactly one retry, never more
    retries = [r for r in caplog.records if getattr(r, "event", None) == "extraction_retry"]
    assert len(retries) == 1
    assert retries[0].cause == "AgentTimeoutError"


def test_extraction_retries_once_on_a_model_error_then_succeeds(deps):
    """Extraction retries once on a model error then succeeds."""
    from agents.errors import AgentModelError

    agent = _TimeoutThenScriptedAgent(
        {
            "Extract this operational event": _extraction_response(),
            "RISK_SCORE": "RISK_SCORE: 0.1\nREASON: assessed",
            "Choose the protocol": "SELECTED: status_check\nREASON: fits",
            "participating in the": "AGENT: reference_agent\nTASK: check gate 3",
            "VERDICT:": "VERDICT: success\nREASONING: matches expected output",
        },
        extraction_failures=1,
        error_cls=AgentModelError,
    )
    insights_agent = type("I", (), {"process": lambda self, text, tools: _FakeResult("success", "insight")})()

    result = process_report(deps, agent, insights_agent, "smoke at gate 3", "telegram", "2026-08-20T10:00:00", "viewer-1")

    assert result.outcome == "succeeded"
    assert agent._extraction_attempts == 2


def test_extraction_fails_after_two_timeouts_never_attempts_a_third_time(deps):
    """Extraction fails after two timeouts never attempts a third time."""
    from agents.errors import AgentTimeoutError

    agent = _TimeoutThenScriptedAgent({}, extraction_failures=2, error_cls=AgentTimeoutError)
    insights_agent = _ScriptedAgent({})

    result = process_report(deps, agent, insights_agent, "smoke at gate 3", "telegram", "2026-08-20T10:00:00", "viewer-1")

    assert result.outcome == "failed"
    assert agent._extraction_attempts == 2  # never more than two attempts
    event = deps.persistence.fetch_event(result.event_id)
    assert event["outcome"] == "failed"


def test_run_report_extraction_continues_from_a_begin_report_event_id(deps):
    """Run report extraction continues from a begin report event id."""
    agent = _happy_path_agent(risk_score="0.1", selected="status_check")
    insights_agent = type("I", (), {"process": lambda self, text, tools: _FakeResult("success", "insight")})()

    event_id = begin_report(deps, "smoke at gate 3", "telegram", "2026-08-20T10:00:00", "viewer-1")
    result = run_report_extraction(deps, event_id, agent, insights_agent)

    assert result.event_id == event_id
    assert result.outcome == "succeeded"


def test_begin_request_returns_immediately_with_no_model_call(deps):
    """Begin request returns immediately with no model call."""
    agent = _ScriptedAgent({})  # would raise on any .process() call

    event_id = begin_request(deps, "please dispatch someone", "2026-08-20T10:00:00", "commander-1")

    assert agent.calls == []
    event = deps.persistence.fetch_event(event_id)
    assert event["classification"] == "human_activation"
    assert event["risk_level"] is None  # risk assessment hasn't run yet


def test_apply_event_data_reply_refuses_to_guess_between_two_pending_holds_for_the_same_sender(deps):
    """CRITICAL_FIXES_PLAN item 7: previously this silently picked the single
    most-recently-created matching hold with no ambiguity check — a reply meant
    for an earlier report could get misapplied to a different, newer one with no
    indication it happened. Now it refuses to guess and reports every candidate.
    """
    older_event_id = begin_report(deps, "smoke near the north gate", "telegram", "2026-08-20T10:00:00", "viewer-1", conversation_id="c1")
    newer_event_id = begin_report(deps, "smoke near the south gate", "telegram", "2026-08-20T10:05:00", "viewer-1", conversation_id="c1")
    create_event_data_hold(deps.persistence, older_event_id, ("area",), "Which area?", ())
    create_event_data_hold(deps.persistence, newer_event_id, ("area",), "Which area?", ())

    result = apply_event_data_reply(deps, main_agent=None, reply_text="the north sector", sender_identity="viewer-1", conversation_id="c1")

    assert result is not None
    assert set(result.ambiguous_event_ids) == {older_event_id, newer_event_id}
    assert result.updates == {}
    assert result.event_id == ""


def test_apply_event_data_reply_still_applies_normally_when_only_one_hold_is_pending(deps):
    """Apply event data reply still applies normally when only one hold is pending."""
    event_id = begin_report(deps, "smoke near the north gate", "telegram", "2026-08-20T10:00:00", "viewer-1", conversation_id="c1")
    create_event_data_hold(deps.persistence, event_id, ("area",), "Which area?", ())
    agent = _ScriptedAgent(
        {"pending request for missing event details": '{"addresses_request": true, "updates": {"area": "north_sector"}, "reply_text": "Recorded."}'}
    )

    result = apply_event_data_reply(deps, main_agent=agent, reply_text="the north sector", sender_identity="viewer-1", conversation_id="c1")

    assert result is not None
    assert result.ambiguous_event_ids == ()
    assert result.event_id == event_id


def test_resolve_clarification_writes_the_answer_without_resuming(deps):
    """Resolve clarification writes the answer without resuming."""
    agent = _ScriptedAgent(
        {
            "Extract this operational event": '{"classification": null, "area": null, "entities": [], "description": null, "severity": null, "occurred_at": null}',
            "Write one concise question": "Which area is this in?",
        }
    )
    insights_agent = _ScriptedAgent({})

    held = process_report(deps, agent, insights_agent, "unclear text", "telegram", "2026-08-20T10:00:00", "viewer-1")
    # item #6's early gate asks for `area` (unclassified's required field)
    # before the clarification hold this test actually exercises exists.
    deps.persistence.update_event(held.event_id, {"area": "north_sector"})
    held = resume_after_event_data(deps, held.event_id, agent, insights_agent)
    [hold] = deps.persistence.list_held_events("clarification")

    answer = resolve_clarification(deps, hold["hold_id"], "commander-1", PermissionLevel.COMMANDER, "fire")

    assert answer.status == "resolved"
    event = deps.persistence.fetch_event(held.event_id)
    assert event["classification"] == "fire"
    assert event["risk_level"] is None  # continuation hasn't run yet


def test_continue_after_clarification_finishes_the_run(deps):
    """Continue after clarification finishes the run."""
    agent = _happy_path_agent(risk_score="0.1", selected="status_check")
    insights_agent = type("I", (), {"process": lambda self, text, tools: _FakeResult("success", "insight")})()
    agent._dispatch["Extract this operational event"] = '{"classification": null, "area": "north_sector", "entities": [], "description": "d", "severity": "s", "occurred_at": "2026-08-20T09:00:00"}'

    held = process_report(deps, agent, insights_agent, "something unclear at gate 3", "telegram", "2026-08-20T10:00:00", "viewer-1")
    [hold] = deps.persistence.list_held_events("clarification")
    answer = resolve_clarification(deps, hold["hold_id"], "commander-1", PermissionLevel.COMMANDER, "fire")

    result = continue_after_clarification(deps, answer.hold["event_id"], agent, insights_agent)

    assert result.outcome == "succeeded"


def test_resolve_approval_denial_is_synchronous_with_no_continuation_needed(deps):
    """Resolve approval denial is synchronous with no continuation needed."""
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
    """Resolve approval then continue after approval composes to success."""
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
    """Process report no match selection writes a terminal outcome not a hold."""
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
    """An ambiguous selection hold resolves to a real protocol and resumes."""
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
from history.contracts import PrecedentMatch
from orchestrator.main_agent import OrchestrationParseError, _parse_formulation_response, formulate_tasks, rewrite_task
from protocols.model import CriticalityLevel, Protocol, ProtocolRunResult, Step, StepOutcome


class _ScriptedMainAgent:
    """ScriptedMainAgent."""
    def __init__(self, response_text, status="success"):
        """Initialize this test helper."""
        self._response_text = response_text
        self._status = status
        self.calls = []

    def process(self, text, allowed_tools):
        """Process."""
        self.calls.append((text, allowed_tools))

        class _Result:
            status = self._status
            text = self._response_text

        return _Result()


class _SequentialMainAgent:
    """SequentialMainAgent."""
    def __init__(self, responses):
        """Initialize this test helper."""
        self._responses = list(responses)
        self.calls = []

    def process(self, text, allowed_tools):
        """Process."""
        self.calls.append((text, allowed_tools))
        response_text, status = self._responses.pop(0)

        class _Result:
            pass

        _Result.status = status
        _Result.text = response_text
        return _Result()


@pytest.fixture
def registry():
    """Registry."""
    agent = ReferenceAgent(model="m")
    return build_agent_registry({}, [agent])


def _protocol(**overrides):
    """Protocol."""
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
    """Parse single agent block."""
    tasks = _parse_formulation_response("AGENT: reference_agent\nTASK: check gate 3")

    assert tasks == {"reference_agent": "check gate 3"}


def test_parse_multiple_agent_blocks():
    """Parse multiple agent blocks."""
    response = "AGENT: a1\nTASK: do the first thing\nAGENT: a2\nTASK: do the second thing"

    tasks = _parse_formulation_response(response)

    assert tasks == {"a1": "do the first thing", "a2": "do the second thing"}


# -- formulate_tasks ------------------------------------------------------


def test_formulate_tasks_produces_a_step_per_participating_agent(registry):
    """Formulate tasks produces a step per participating agent."""
    agent = _ScriptedMainAgent("AGENT: reference_agent\nTASK: check status at gate 3")

    result = formulate_tasks(agent, _protocol(), registry, "raw", "fire", "north", "d")

    assert result.success
    assert len(result.steps) == 1
    assert result.steps[0].agent_name == "reference_agent"
    assert result.steps[0].task_text == "check status at gate 3"


def test_legacy_formulated_steps_still_have_no_step_id(registry):
    """Deliberately unchanged: giving legacy-parsed steps a real step_id was
    considered and rejected (see the NOTE at this parse loop) — it would
    silently reroute every legacy-formatted plan into protocols.executor's
    dependency-graph/concurrent scheduler via that function's own dispatch
    condition, a much larger change than fixing _persist_step_outcomes'
    matching bug called for. This pins the current, intentional shape down
    so a future change here has to touch this test, not just the fix."""
    agent = _ScriptedMainAgent("AGENT: reference_agent\nTASK: check status at gate 3")

    result = formulate_tasks(agent, _protocol(), registry, "raw", "fire", "north", "d")

    assert result.steps[0].step_id == ""
    assert result.steps[0].depends_on == ()


def test_formulation_preserves_valid_required_event_fields(registry):
    """Formulation preserves valid required event fields."""
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


def test_formulation_preserves_required_event_fields_when_json_is_markdown_fenced(registry):
    """Formulation preserves required event fields when json is markdown fenced."""
    fenced = "```json\n" + json.dumps({
        "steps": [{
            "step_id": "check-location",
            "agent_name": "reference_agent",
            "task": "Check the reported location.",
            "depends_on": [],
            "required_event_fields": ["area"],
        }]
    }) + "\n```"
    agent = _ScriptedMainAgent(fenced)

    result = formulate_tasks(agent, _protocol(), registry, "raw", "fire", None, "d")

    assert result.success
    assert result.steps[0].required_event_fields == ("area",)


def test_formulation_merges_the_required_fields_floor_when_the_model_omits_it(registry):
    """The event type's static required-fields floor (`EVENT_TYPE_REQUIRED_
    FIELDS`, looked up by the caller and passed as `required_fields_floor`)
    is unioned into the step's `required_event_fields` even when the model's
    own JSON declares none at all — a deterministic guarantee, not left to
    what the model happens to write."""
    agent = _ScriptedMainAgent(json.dumps({
        "steps": [{
            "step_id": "check-location",
            "agent_name": "reference_agent",
            "task": "Check the reported location.",
            "depends_on": [],
            "required_event_fields": [],
        }]
    }))

    result = formulate_tasks(agent, _protocol(), registry, "raw", "fire", None, "d", required_fields_floor=("area",))

    assert result.success
    assert result.steps[0].required_event_fields == ("area",)


def test_formulation_merges_the_floor_alongside_the_models_own_additional_fields(registry):
    """The merge is additive, not a replacement — a step that needs a field
    beyond the static floor keeps it; per-step flexibility for anything
    beyond the floor is preserved."""
    agent = _ScriptedMainAgent(json.dumps({
        "steps": [{
            "step_id": "check-location",
            "agent_name": "reference_agent",
            "task": "Check the reported location and cross-reference entities.",
            "depends_on": [],
            "required_event_fields": ["entities"],
        }]
    }))

    result = formulate_tasks(agent, _protocol(), registry, "raw", "fire", None, "d", required_fields_floor=("area",))

    assert result.success
    assert set(result.steps[0].required_event_fields) == {"area", "entities"}


@pytest.fixture
def two_agent_registry():
    """Two agent registry."""
    return build_agent_registry({}, [ReferenceAgent(model="m"), HistoryAgent(model="m")])


def test_formulation_floor_applies_to_every_step_sequential_plan(two_agent_registry):
    """The floor is unioned in per step, inside the same parse loop
    regardless of topology — a sequential plan (no step_id dependencies)
    gets it on every step, not just the first."""
    protocol = _protocol(participating_agents=("reference_agent", "history_agent"))
    agent = _ScriptedMainAgent(json.dumps({
        "steps": [
            {"step_id": "s1", "agent_name": "reference_agent", "task": "check status", "depends_on": [], "required_event_fields": []},
            {"step_id": "s2", "agent_name": "history_agent", "task": "check history", "depends_on": [], "required_event_fields": []},
        ]
    }))

    result = formulate_tasks(agent, protocol, two_agent_registry, "raw", "fire", None, "d", required_fields_floor=("area",))

    assert result.success
    assert all(step.required_event_fields == ("area",) for step in result.steps)


def test_formulation_floor_applies_to_every_step_dependency_graph_plan(two_agent_registry):
    """Same guarantee for a dependency-graph plan (non-empty depends_on,
    the topology protocols.executor._execute_dependency_steps runs) — the
    merge happens in formulate_tasks' parse loop, before any topology
    decision is made, so no special-casing is needed for either shape."""
    protocol = _protocol(participating_agents=("reference_agent", "history_agent"))
    agent = _ScriptedMainAgent(json.dumps({
        "steps": [
            {"step_id": "s1", "agent_name": "reference_agent", "task": "check status", "depends_on": [], "required_event_fields": []},
            {"step_id": "s2", "agent_name": "history_agent", "task": "check history", "depends_on": ["s1"], "required_event_fields": []},
        ]
    }))

    result = formulate_tasks(agent, protocol, two_agent_registry, "raw", "fire", None, "d", required_fields_floor=("area",))

    assert result.success
    assert all(step.required_event_fields == ("area",) for step in result.steps)
    assert result.steps[1].depends_on == ("s1",)  # the dependency-graph shape itself is untouched


def test_formulation_floor_survives_the_markdown_fenced_json_path_when_the_model_omits_it(registry):
    """Mirrors `test_formulation_preserves_required_event_fields_when_json_is_
    markdown_fenced` (which proves the model's own declared fields survive
    fencing) — this proves the floor survives it too, even when the model's
    fenced JSON declares no required fields of its own."""
    fenced = "```json\n" + json.dumps({
        "steps": [{
            "step_id": "check-location",
            "agent_name": "reference_agent",
            "task": "Check the reported location.",
            "depends_on": [],
            "required_event_fields": [],
        }]
    }) + "\n```"
    agent = _ScriptedMainAgent(fenced)

    result = formulate_tasks(agent, _protocol(), registry, "raw", "fire", None, "d", required_fields_floor=("area",))

    assert result.success
    assert result.steps[0].required_event_fields == ("area",)


def test_allowed_tools_are_filtered_to_what_the_agent_actually_exposes(registry):
    """Allowed tools are filtered to what the agent actually exposes."""
    agent = _ScriptedMainAgent("AGENT: reference_agent\nTASK: t")
    protocol = _protocol(approved_tools=("check_status", "record_action", "some_other_tool_no_agent_has"))

    result = formulate_tasks(agent, protocol, registry, "raw", "fire", "north", "d")

    assert set(result.steps[0].allowed_tools) == {"check_status", "record_action"}


def test_missing_an_agents_block_fails_naming_that_agent(registry):
    """Missing an agents block fails naming that agent."""
    agent = _ScriptedMainAgent("this response has no AGENT/TASK blocks at all")

    result = formulate_tasks(agent, _protocol(), registry, "raw", "fire", "north", "d")

    assert not result.success
    assert result.failed_agent_name == "reference_agent"


def test_formulation_repairs_one_invalid_response_with_the_parse_failure(registry):
    """Formulation repairs one invalid response with the parse failure."""
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
    assert "task formulation must contain one step per participating agent" in agent.calls[1][0]
    assert "without Markdown fences" in agent.calls[1][0]


def test_unclear_task_status_fails_formulation(registry):
    """Unclear task status fails formulation."""
    agent = _ScriptedMainAgent("missing context", status="unclear_task")

    result = formulate_tasks(agent, _protocol(), registry, "raw", "fire", "north", "d")

    assert not result.success


def test_precedent_context_defaults_to_empty_and_is_optional(registry):
    """Precedent context defaults to empty and is optional."""
    agent = _ScriptedMainAgent("AGENT: reference_agent\nTASK: t")

    result = formulate_tasks(agent, _protocol(), registry, "raw", "fire", "north", "d")  # no precedent_context passed

    assert result.success


def _precedent(event_id="prior-1", *, resolved: bool, outcome="succeeded"):
    """Precedent."""
    return PrecedentMatch(
        event_id=event_id, classification="fire", area="north", occurred_at="2026-01-01T00:00:00+00:00",
        protocol_name="report_fire_incident", steps_summary=[{"agent_name": "x", "result_text": "prior incident X"}],
        outcome=outcome, resolved=resolved,
    )


def test_precedent_context_appears_in_the_prompt_when_given(registry):
    """Precedent context appears in the prompt when given."""
    agent = _ScriptedMainAgent("AGENT: reference_agent\nTASK: t")

    formulate_tasks(agent, _protocol(), registry, "raw", "fire", "north", "d", precedent_context=(_precedent(resolved=True),))

    assert "prior incident X" in agent.calls[0][0]


def test_unresolved_precedent_is_excluded_from_the_prompt(registry):
    # A failed/unresolved precedent carries no reliable procedure to repeat -- dumping its own
    # failure reasoning into a new event's formulation prompt as unqualified "what was tried
    # before" primes the model to preemptively refuse a fresh attempt (the firefighting
    # crew-shift-status bug this fix addresses).
    """Unresolved precedent is excluded from the prompt."""
    agent = _ScriptedMainAgent("AGENT: reference_agent\nTASK: t")

    formulate_tasks(
        agent, _protocol(), registry, "raw", "fire", "north", "d",
        precedent_context=(_precedent(resolved=False, outcome="failed"),),
    )

    assert "prior incident X" not in agent.calls[0][0]
    assert "Relevant precedent" not in agent.calls[0][0]


def test_a_mix_of_resolved_and_unresolved_precedents_only_shows_the_resolved_one(registry):
    """A mix of resolved and unresolved precedents only shows the resolved one."""
    agent = _ScriptedMainAgent("AGENT: reference_agent\nTASK: t")

    formulate_tasks(
        agent, _protocol(), registry, "raw", "fire", "north", "d",
        precedent_context=(
            _precedent("prior-failed", resolved=False, outcome="failed"),
            _precedent("prior-ok", resolved=True, outcome="succeeded"),
        ),
    )

    prompt = agent.calls[0][0]
    assert "prior-ok" in prompt
    assert "prior-failed" not in prompt


def test_conversation_messages_appear_in_the_prompt_when_given(registry):
    """Conversation messages appear in the prompt when given."""
    agent = _ScriptedMainAgent("AGENT: reference_agent\nTASK: t")

    formulate_tasks(
        agent, _protocol(), registry, "raw", "fire", "north", "d",
        conversation_messages=({"role": "user", "content": "the drone from before"},),
    )

    assert "the drone from before" in agent.calls[0][0]
    assert "Recent conversation in this thread" in agent.calls[0][0]


def test_conversation_messages_absent_by_default(registry):
    """Conversation messages absent by default."""
    agent = _ScriptedMainAgent("AGENT: reference_agent\nTASK: t")

    formulate_tasks(agent, _protocol(), registry, "raw", "fire", "north", "d")

    assert "Recent conversation in this thread" not in agent.calls[0][0]


def test_corrects_event_id_is_parsed_when_the_model_names_a_shown_precedent(registry):
    """Corrects event id is parsed when the model names a shown precedent."""
    agent = _ScriptedMainAgent(json.dumps({
        "steps": [{"step_id": "s1", "agent_name": "reference_agent", "task": "t", "depends_on": [], "required_event_fields": []}],
        "corrects_event_id": "prior-ok",
    }))

    result = formulate_tasks(
        agent, _protocol(), registry, "raw", "fire", "north", "d",
        precedent_context=(_precedent("prior-ok", resolved=True),),
    )

    assert result.success
    assert result.corrects_event_id == "prior-ok"


def test_corrects_event_id_is_rejected_when_not_one_of_the_shown_precedents(registry):
    # Never trust an arbitrary model-supplied event_id -- only a candidate it was actually shown
    # (a resolved precedent) counts as a valid correction/retraction link.
    """Corrects event id is rejected when not one of the shown precedents."""
    agent = _ScriptedMainAgent(json.dumps({
        "steps": [{"step_id": "s1", "agent_name": "reference_agent", "task": "t", "depends_on": [], "required_event_fields": []}],
        "corrects_event_id": "some-other-event-not-shown",
    }))

    result = formulate_tasks(agent, _protocol(), registry, "raw", "fire", "north", "d")

    assert result.success
    assert result.corrects_event_id is None


def test_corrects_event_id_defaults_to_none(registry):
    """Corrects event id defaults to none."""
    agent = _ScriptedMainAgent(json.dumps({
        "steps": [{"step_id": "s1", "agent_name": "reference_agent", "task": "t", "depends_on": [], "required_event_fields": []}],
    }))

    result = formulate_tasks(agent, _protocol(), registry, "raw", "fire", "north", "d")

    assert result.corrects_event_id is None


def test_formulate_tasks_passes_no_tools_to_the_main_agent(registry):
    """Formulate tasks passes no tools to the main agent."""
    agent = _ScriptedMainAgent("AGENT: reference_agent\nTASK: t")

    formulate_tasks(agent, _protocol(), registry, "raw", "fire", "north", "d")

    assert agent.calls[0][1] == []


# -- rewrite_task -----------------------------------------------------------


def test_rewrite_task_returns_the_full_response_as_the_new_task():
    """Rewrite task returns the full response as the new task."""
    agent = _ScriptedMainAgent("check status specifically at gate 3, not the whole perimeter")
    step = Step(agent_name="reference_agent", task_text="check status", allowed_tools=("check_status",))

    rewritten = rewrite_task(agent, step, "which location specifically")

    assert rewritten == "check status specifically at gate 3, not the whole perimeter"


def test_rewrite_task_raises_when_the_agent_reports_unclear_again():
    """Rewrite task raises when the agent reports unclear again."""
    agent = _ScriptedMainAgent("still unclear", status="unclear_task")
    step = Step(agent_name="a", task_text="t", allowed_tools=())

    with pytest.raises(OrchestrationParseError):
        rewrite_task(agent, step, "missing X")


def test_rewrite_task_matches_the_executors_task_rewriter_signature():
    """Rewrite task matches the executors task rewriter signature."""
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
    """End to end through the mocked adapter."""
    from orchestrator.main_agent import MainAgent

    class _FakeOutput:
        def __init__(self, raw):
            self.raw = raw

    class _FakeAgent:
        def __init__(self, **kwargs):
            pass

        def kickoff(self, text):
            return _FakeOutput("AGENT: reference_agent\nTASK: check gate 3 status")

    fake_module = types.SimpleNamespace(Agent=_FakeAgent, LLM=lambda **kwargs: kwargs["model"], tools=types.SimpleNamespace(BaseTool=object))
    monkeypatch.setattr(adapter, "_get_crewai", lambda: fake_module)

    main_agent = MainAgent(model="fake-model")
    result = formulate_tasks(main_agent, _protocol(), registry, "raw", "fire", "north", "d")

    assert result.success
    assert result.steps[0].task_text == "check gate 3 status"
