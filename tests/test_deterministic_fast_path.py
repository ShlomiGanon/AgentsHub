import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from types import MappingProxyType, SimpleNamespace

import pytest

from agents import Agent, AgentInvocationError, AgentResult, authenticated_request_identity, build_agent_registry, tool
from agents import trusted_event_metadata
from orchestrator.flows import prepare_fast_path_report
from orchestrator.reasoning import formulate_tasks
from profiles import AreaRegistry, EventTypeRegistry, OptimizationPolicy
from profiles import unified_test
from protocols import CriticalityLevel, DirectToolExecution, Protocol, ProtocolSet, execute_steps


MESSAGE = "אני במילואים מראשון עד שלישי בערב, לא זמין ביישוב"
RECEIVED_AT = "2026-09-16T12:46:21+00:00"


class _IntakeAgent:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def process(self, prompt, allowed_tools, *, invocation_policy=None):
        self.calls.append((prompt, tuple(allowed_tools), invocation_policy))
        return AgentResult("success", json.dumps(self.payload, ensure_ascii=False))


class _DirectAgent(Agent):
    name = "direct_agent"
    role = "executes a deterministic test action"
    system_prompt = "Use only the allowed tool."

    def __init__(self, model="mock"):
        self.invocations = []
        super().__init__(model)

    @tool("record_value", "Record a validated value.", side_effecting=True, idempotent=True)
    def record_value(self, value: str = "") -> str:
        self.invocations.append(value)
        return f"stored:{value}"


class _AttendanceShapeAgent(Agent):
    name = "team_status_agent"
    role = "records attendance"
    system_prompt = "Use only the attendance tool."

    @tool("record_attendance_response", "Record attendance.", side_effecting=True, idempotent=True)
    def record_attendance_response(self, availability: str = "available", reason: str = "") -> str:
        return f"stored:{availability}:{reason}"


class _Settings:
    def get_risk_threshold(self):
        return 0.6

    def get_retry_count(self):
        return 1


def _attendance_protocol():
    return next(protocol for protocol in unified_test.PROTOCOLS if protocol.name == "record_attendance_response")


def _payload(**changes):
    payload = {
        "intent": {
            "value": "report",
            "confident": True,
            "asks_for_information": False,
            "reports_occurrence": True,
            "requests_action": False,
            "social_only": False,
            "is_quoted": False,
            "is_hypothetical": False,
            "evidence": MESSAGE,
        },
        "classification": {
            "name": "team_attendance_report",
            "confident": True,
            "area": "readiness_team",
            "entities": [],
            "description": "The reporting member is unavailable due to reserve duty.",
            "severity": "low",
            "occurred_at": None,
        },
        "business_fields": {"availability": "unavailable", "reason": "reserve duty"},
        "risk": {"risk_score": 0.1, "risk_reason": "Routine attendance update."},
        "protocol": {
            "status": "selected",
            "name": "record_attendance_response",
            "candidate_names": [],
            "reason": "The report records member attendance.",
        },
        "temporal": {"expression": None, "availability_start": None, "availability_end": None},
    }
    for key, value in changes.items():
        if "." in key:
            section, field = key.split(".", 1)
            payload[section][field] = value
        else:
            payload[key] = value
    return payload


def _deps(agent, policy=None, protocols=None):
    return SimpleNamespace(
        optimization_policy=policy or OptimizationPolicy(
            operational_intake_mode="single",
            deterministic_execution_mode="direct",
        ),
        event_type_registry=EventTypeRegistry(
            ("team_attendance_report",),
            MappingProxyType({}),
        ),
        area_registry=AreaRegistry(("readiness_team",)),
        protocol_set=ProtocolSet(tuple(protocols or (_attendance_protocol(),))),
        settings_store=_Settings(),
        registry=build_agent_registry({}, [agent]),
        timezone_name="Asia/Jerusalem",
    )


def _attendance_agent(tmp_path, monkeypatch):
    database = tmp_path / "attendance-fast-path.db"
    monkeypatch.setattr(unified_test.UnifiedTeamStatusAgent, "status_db_path", str(database))
    agent = unified_test.UnifiedTeamStatusAgent(model="mock")
    opened = datetime(2026, 9, 16, 5, 0, tzinfo=timezone.utc)
    agent.status_store.register_member("member-1", "Test Member", opened.isoformat())
    agent.status_store.approve_roster("commander-1", opened.isoformat())
    agent.status_store.open_cycle(
        opened.date().isoformat(),
        opened.isoformat(),
        (opened + timedelta(hours=1)).isoformat(),
    )
    return agent, database


def test_clear_attendance_prepares_one_call_direct_plan_and_writes_database(tmp_path, monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    specialist, database = _attendance_agent(tmp_path, monkeypatch)
    intake_agent = _IntakeAgent(_payload())
    deps = _deps(specialist)

    plan = prepare_fast_path_report(deps, intake_agent, MESSAGE, RECEIVED_AT, False)

    assert plan is not None
    assert len(intake_agent.calls) == 1
    assert plan.extraction.availability_start == "2026-09-19T21:00:00+00:00"
    assert plan.extraction.availability_end == "2026-09-22T17:00:00+00:00"
    formulation = formulate_tasks(
        intake_agent,
        plan.protocol,
        deps.registry,
        MESSAGE,
        plan.extraction.classification,
        plan.extraction.area,
        plan.extraction.description,
        event_data=plan.extraction.__dict__,
        allow_direct_execution=True,
    )
    step = formulation.steps[0]
    assert len(intake_agent.calls) == 1
    assert step.direct_tool_name == "record_attendance_response"
    assert step.direct_tool_arguments == {"availability": "unavailable", "reason": "reserve duty"}

    with authenticated_request_identity("member-1"), trusted_event_metadata({
        "source_message_id": "fast-path-message-1",
        "original_text": MESSAGE,
        "received_at": RECEIVED_AT,
        "availability_start": plan.extraction.availability_start,
        "availability_end": plan.extraction.availability_end,
    }):
        result = execute_steps([step], {specialist.name: specialist}, _Settings(), event_data=plan.extraction.__dict__)

    assert result.completed
    assert len(intake_agent.calls) == 1
    assert any(getattr(record, "event", None) == "tool_call" for record in caplog.records)
    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT availability, reason, availability_start, availability_end, source_message_id, "
            "telegram_identity, original_text, received_at "
            "FROM attendance_responses"
        ).fetchone()
    assert row == (
        "unavailable",
        "reserve duty",
        "2026-09-19T21:00:00+00:00",
        "2026-09-22T17:00:00+00:00",
        "fast-path-message-1",
        "member-1",
        MESSAGE,
        RECEIVED_AT,
    )


def test_available_attendance_uses_same_direct_mechanism(tmp_path, monkeypatch):
    specialist, database = _attendance_agent(tmp_path, monkeypatch)
    message = "אני זמין היום"
    intake_agent = _IntakeAgent(_payload(
        **{"intent.evidence": message, "classification.description": "The reporting member is available today."},
        business_fields={"availability": "available", "reason": None},
    ))
    deps = _deps(specialist)

    plan = prepare_fast_path_report(deps, intake_agent, message, RECEIVED_AT, False)

    assert plan is not None
    formulation = formulate_tasks(
        intake_agent, plan.protocol, deps.registry, message, plan.extraction.classification,
        plan.extraction.area, plan.extraction.description, event_data=plan.extraction.__dict__,
        allow_direct_execution=True,
    )
    step = formulation.steps[0]
    assert step.direct_tool_arguments == {"availability": "available"}
    with authenticated_request_identity("member-1"), trusted_event_metadata({
        "source_message_id": "fast-path-available",
        "original_text": message,
        "received_at": RECEIVED_AT,
        "availability_start": plan.extraction.availability_start,
        "availability_end": plan.extraction.availability_end,
    }):
        result = execute_steps([step], {specialist.name: specialist}, _Settings(), event_data=plan.extraction.__dict__)
    assert result.completed
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT availability FROM attendance_responses").fetchone()[0] == "available"


def test_missing_reason_and_ambiguous_time_fall_back_without_direct_execution():
    specialist = _AttendanceShapeAgent(model="mock")
    no_reason = _IntakeAgent(_payload(business_fields={"availability": "unavailable", "reason": None}))
    ambiguous_message = "אני לא זמין בקרוב בגלל מילואים"
    ambiguous_time = _IntakeAgent(_payload(**{"intent.evidence": ambiguous_message}))

    assert prepare_fast_path_report(_deps(specialist), no_reason, MESSAGE, RECEIVED_AT, False) is None
    assert prepare_fast_path_report(
        _deps(specialist), ambiguous_time, ambiguous_message, RECEIVED_AT, False
    ) is None


def test_ambiguous_protocol_approval_dynamic_and_disabled_modes_preserve_fallback():
    specialist = _AttendanceShapeAgent(model="mock")
    ambiguous = _IntakeAgent(_payload(
        **{"protocol.status": "ambiguous", "protocol.name": None,
           "protocol.candidate_names": ["record_attendance_response"]},
    ))
    assert prepare_fast_path_report(_deps(specialist), ambiguous, MESSAGE, RECEIVED_AT, False) is None

    approval_protocol = Protocol(
        name="record_attendance_response",
        description="record attendance",
        participating_agents=("team_status_agent",),
        approved_tools=("record_attendance_response",),
        expected_success_output="stored",
        criticality=CriticalityLevel.HIGH,
        approval_flag=True,
        deterministic_required_event_fields=("description", "availability_start", "availability_end"),
        direct_tool_execution=_attendance_protocol().direct_tool_execution,
    )
    assert prepare_fast_path_report(
        _deps(specialist, protocols=(approval_protocol,)), _IntakeAgent(_payload()), MESSAGE, RECEIVED_AT, False
    ) is None

    dynamic = Protocol(
        name="record_attendance_response",
        description="record attendance dynamically",
        participating_agents=("team_status_agent",),
        approved_tools=("record_attendance_response",),
        expected_success_output="stored",
        criticality=CriticalityLevel.LOW,
        approval_flag=False,
    )
    assert prepare_fast_path_report(
        _deps(specialist, protocols=(dynamic,)),
        _IntakeAgent(_payload(business_fields={})),
        MESSAGE,
        RECEIVED_AT,
        False,
    ) is None

    disabled_agent = _IntakeAgent(_payload())
    assert prepare_fast_path_report(
        _deps(specialist, policy=OptimizationPolicy()), disabled_agent, MESSAGE, RECEIVED_AT, False
    ) is None
    assert disabled_agent.calls == []


def test_another_deterministic_protocol_directs_through_runtime_and_enforces_permissions():
    agent = _DirectAgent()
    protocol = Protocol(
        name="record_value_protocol",
        description="record one validated value",
        participating_agents=(agent.name,),
        approved_tools=("record_value",),
        expected_success_output="stored",
        criticality=CriticalityLevel.LOW,
        approval_flag=False,
        deterministic_required_event_fields=(),
        direct_tool_execution=DirectToolExecution(
            tool_name="record_value",
            argument_sources=(("value", "business_fields.value"),),
            required_arguments=("value",),
        ),
    )
    main = _IntakeAgent(_payload())
    formulation = formulate_tasks(
        main,
        protocol,
        build_agent_registry({}, [agent]),
        "record alpha",
        "test_report",
        None,
        "record alpha",
        event_data={"business_fields": {"value": "alpha"}},
        allow_direct_execution=True,
    )
    step = formulation.steps[0]
    assert step.direct_tool_name == "record_value"
    assert execute_steps([step], {agent.name: agent}, _Settings()).completed
    assert agent.invocations == ["alpha"]

    with pytest.raises(AgentInvocationError, match="not permitted"):
        agent.execute_tool("record_value", {"value": "blocked"}, [])
    with pytest.raises(AgentInvocationError, match="schema validation"):
        agent.execute_tool("record_value", {"unknown": "value"}, ["record_value"])
