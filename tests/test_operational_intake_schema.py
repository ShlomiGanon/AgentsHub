import json
import logging

import pytest

from agents import AgentResult
from orchestrator.reasoning import (
    OrchestrationParseError,
    _operational_intake_schema,
    make_operational_intake,
)
from profiles import unified_test


EVENT_TYPES = ("team_attendance_report",)
AREAS = ("readiness_team",)
PROTOCOLS = tuple(
    protocol for protocol in unified_test.PROTOCOLS if protocol.name == "record_attendance_response"
)
RECEIVED_AT = "2026-09-16T12:46:21+00:00"
MESSAGE = "Member is unavailable due to reserve duty."


class _ScriptedIntakeAgent:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def process(self, prompt, allowed_tools, *, invocation_policy=None):
        self.calls.append((prompt, tuple(allowed_tools), invocation_policy))
        return AgentResult("success", json.dumps(self.payload))


class _MalformedIntakeAgent(_ScriptedIntakeAgent):
    def process(self, prompt, allowed_tools, *, invocation_policy=None):
        self.calls.append((prompt, tuple(allowed_tools), invocation_policy))
        return AgentResult("success", '{"intent":')


def _payload(**changes):
    payload = {
        "intent": "report",
        "intent_confident": True,
        "asks_for_information": False,
        "reports_occurrence": True,
        "requests_action": False,
        "social_only": False,
        "is_quoted": False,
        "is_hypothetical": False,
        "intent_evidence": MESSAGE,
        "classification": "team_attendance_report",
        "classification_confident": True,
        "area": "readiness_team",
        "entities": [],
        "description": "The member is unavailable due to reserve duty.",
        "severity": "low",
        "occurred_at": None,
        "availability_start": None,
        "availability_end": None,
        "business_fields": {"availability": "unavailable", "reason": "reserve duty"},
        "risk_score": 0.1,
        "risk_reason": "Routine attendance update.",
        "protocol_status": "selected",
        "protocol_name": "record_attendance_response",
        "candidate_names": [],
        "protocol_reason": "The report records member attendance.",
    }
    payload.update(changes)
    return payload


def _make(payload):
    agent = _ScriptedIntakeAgent(payload)
    intake = make_operational_intake(
        agent,
        MESSAGE,
        RECEIVED_AT,
        EVENT_TYPES,
        AREAS,
        PROTOCOLS,
        0.6,
    )
    return intake, agent


def test_schema_declares_required_and_optional_fields():
    schema = _operational_intake_schema(EVENT_TYPES, PROTOCOLS)

    assert set(schema["required"]) == {
        "intent",
        "intent_confident",
        "asks_for_information",
        "reports_occurrence",
        "requests_action",
        "social_only",
        "is_quoted",
        "is_hypothetical",
        "intent_evidence",
        "classification",
        "classification_confident",
        "entities",
        "description",
        "business_fields",
        "risk_score",
        "risk_reason",
        "protocol_status",
        "protocol_name",
        "candidate_names",
        "protocol_reason",
    }
    assert set(schema["properties"]) - set(schema["required"]) == {
        "area",
        "severity",
        "occurred_at",
        "availability_start",
        "availability_end",
    }
    assert schema["additionalProperties"] is False


def test_full_valid_intake_passes_in_one_model_call():
    intake, agent = _make(_payload())

    assert intake.confident
    assert intake.extraction.classification == "team_attendance_report"
    assert intake.decision.selection.protocol_name == "record_attendance_response"
    assert len(agent.calls) == 1


def test_optional_nullable_fields_may_be_omitted():
    payload = _payload()
    for field_name in ("area", "severity", "occurred_at", "availability_start", "availability_end"):
        del payload[field_name]

    intake, agent = _make(payload)

    assert intake.confident
    assert intake.extraction.area is None
    assert intake.extraction.occurred_at is None
    assert len(agent.calls) == 1


@pytest.mark.parametrize(
    ("change", "category"),
    [
        (lambda payload: payload.pop("description"), "object_keys"),
        (lambda payload: payload.update(unexpected="rejected"), "object_keys"),
        (lambda payload: payload.update(protocol_status="invalid"), "enum:protocol_status"),
        (lambda payload: payload.update(risk_score="high"), "type:risk_score"),
    ],
)
def test_invalid_schema_payloads_are_rejected_with_safe_validation_telemetry(change, category, caplog):
    caplog.set_level(logging.WARNING)
    payload = _payload()
    change(payload)

    with pytest.raises(OrchestrationParseError):
        _make(payload)

    record = next(record for record in caplog.records if record.event == "structured_schema_validation_failed")
    assert record.schema_validation_category == category
    assert record.expected_keys
    assert record.returned_keys
    assert "reserve duty" not in record.getMessage()
    assert "reserve duty" not in repr(record.returned_keys)


def test_schema_validation_rejects_missing_nested_required_business_field():
    payload = _payload(business_fields={"availability": "unavailable"})

    with pytest.raises(OrchestrationParseError):
        _make(payload)


def test_optional_field_with_invalid_type_is_rejected():
    payload = _payload(area=17)

    with pytest.raises(OrchestrationParseError):
        _make(payload)


def test_malformed_json_remains_a_parse_failure():
    agent = _MalformedIntakeAgent(None)

    with pytest.raises(OrchestrationParseError, match="could not parse operational intake JSON"):
        make_operational_intake(agent, MESSAGE, RECEIVED_AT, EVENT_TYPES, AREAS, PROTOCOLS, 0.6)

    assert len(agent.calls) == 1
