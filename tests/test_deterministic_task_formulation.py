"""Regression coverage for protocol-declared deterministic task formulation."""

import json
from types import SimpleNamespace

from agents import Agent, build_agent_registry, tool
from orchestrator.reasoning import formulate_tasks
from profiles import unified_test


class _TeamAgent(Agent):
    name = "team_status_agent"
    role = "records team availability"
    system_prompt = "Use the attendance tool."

    @tool("record_attendance_response", "Record attendance.", side_effecting=True, idempotent=True)
    def record_attendance_response(self, availability: str, reason: str = "", unavailable_days: int = 0) -> str:
        return "stored"


class _SurveillanceAgent(Agent):
    name = "surveillance_agent"
    role = "reports surveillance status"
    system_prompt = "Use the surveillance tool."

    @tool("get_surveillance_overview", "Get overview.", side_effecting=False)
    def get_surveillance_overview(self) -> str:
        return "overview"


class _ForcesAgent(Agent):
    name = "friendly_forces_agent"
    role = "dispatches emergency forces"
    system_prompt = "Use an emergency dispatch tool."

    @tool("dispatch_ambulance", "Dispatch ambulance.", side_effecting=True, idempotent=True)
    def dispatch_ambulance(self) -> str:
        return "ok"

    @tool("dispatch_police", "Dispatch police.", side_effecting=True, idempotent=True)
    def dispatch_police(self) -> str:
        return "ok"

    @tool("dispatch_firefighters", "Dispatch firefighters.", side_effecting=True, idempotent=True)
    def dispatch_firefighters(self) -> str:
        return "ok"

    @tool("dispatch_military", "Dispatch military.", side_effecting=True, idempotent=True)
    def dispatch_military(self) -> str:
        return "ok"


class _MainAgentSpy:
    def __init__(self, response: str = ""):
        self.response = response
        self.calls = []

    def process(self, prompt, allowed_tools):
        self.calls.append((prompt, tuple(allowed_tools)))
        return SimpleNamespace(status="success", text=self.response)


def _protocol(name):
    return next(protocol for protocol in unified_test.PROTOCOLS if protocol.name == name)


def _registry(*agents):
    return build_agent_registry({}, [agent(model="mock") for agent in agents])


def test_attendance_protocol_builds_declared_step_without_task_formulation_model_call():
    main = _MainAgentSpy()
    protocol = _protocol("record_attendance_response")
    description = "The reporting member is unavailable due to reserve duty from Sunday through Tuesday evening."
    result = formulate_tasks(
        main,
        protocol,
        _registry(_TeamAgent),
        "raw text must not be used as trusted metadata",
        "team_attendance_report",
        None,
        description,
        event_data={
            "classification": "team_attendance_report",
            "description": description,
            "occurred_at": "2026-09-20T00:00:00+00:00",
            "source_message_id": "must-not-enter-task",
            "received_at": "2026-09-16T12:46:21+00:00",
            "sender_identity": "must-not-enter-task",
            "original_text": "must-not-enter-task",
        },
    )

    assert result.success
    assert main.calls == []
    assert len(result.steps) == 1
    step = result.steps[0]
    assert step.agent_name == "team_status_agent"
    assert step.allowed_tools == ("record_attendance_response",)
    assert step.required_event_fields == ("description", "occurred_at")
    assert step.step_id == "1"
    assert step.depends_on == ()
    assert description in step.task_text
    assert "unavailable" in step.task_text and "reserve duty" in step.task_text
    assert "source_message_id" not in step.task_text
    assert "received_at" not in step.task_text
    assert "sender_identity" not in step.task_text
    assert "original_text" not in step.task_text


def test_available_attendance_report_uses_the_same_deterministic_business_context():
    main = _MainAgentSpy()
    description = "The reporting member is available for readiness-team duty today."
    result = formulate_tasks(
        main,
        _protocol("record_attendance_response"),
        _registry(_TeamAgent),
        "I am available today",
        "team_attendance_report",
        None,
        description,
        event_data={"description": description, "occurred_at": "2026-09-16T06:00:00+00:00"},
    )

    assert result.success
    assert main.calls == []
    assert "available for readiness-team duty" in result.steps[0].task_text


def test_another_declared_single_step_protocol_uses_the_general_mechanism():
    main = _MainAgentSpy()
    protocol = _protocol("query_surveillance_overview")
    result = formulate_tasks(
        main,
        protocol,
        _registry(_SurveillanceAgent),
        "show the surveillance picture",
        "surveillance_report",
        "north_gate",
        "Request for the current surveillance overview.",
        event_data={"classification": "surveillance_report", "area": "north_gate"},
    )

    assert result.success
    assert main.calls == []
    assert result.steps[0].agent_name == "surveillance_agent"
    assert result.steps[0].allowed_tools == ("get_surveillance_overview",)
    assert result.steps[0].required_event_fields == ()


def test_protocol_without_deterministic_declaration_keeps_existing_model_formulation():
    protocol = _protocol("dispatch_emergency_forces")
    response = json.dumps({
        "steps": [{
            "step_id": "dispatch",
            "agent_name": "friendly_forces_agent",
            "task": "Dispatch the appropriate emergency service to the reported area.",
            "depends_on": [],
            "required_event_fields": ["area"],
        }]
    })
    main = _MainAgentSpy(response)
    result = formulate_tasks(
        main,
        protocol,
        _registry(_ForcesAgent),
        "Emergency at the north gate",
        "emergency_dispatch",
        "north_gate",
        "An emergency response is required.",
        event_data={"classification": "emergency_dispatch", "area": "north_gate"},
        required_fields_floor=("area",),
    )

    assert result.success
    assert len(main.calls) == 1
    assert result.steps[0].agent_name == "friendly_forces_agent"
    assert result.steps[0].required_event_fields == ("area",)
