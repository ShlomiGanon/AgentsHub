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


EVENT_TYPES = ("team_attendance_report", "team_availability")
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
            "description": "The member is unavailable due to reserve duty.",
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
        "intent", "classification", "business_fields", "risk", "protocol", "temporal"
    }
    assert set(schema["properties"]) == set(schema["required"])
    assert schema["additionalProperties"] is False
    assert set(schema["properties"]["intent"]["required"]) == {
        "value", "confident", "asks_for_information", "reports_occurrence", "requests_action",
        "social_only", "is_quoted", "is_hypothetical", "evidence",
    }
    assert set(schema["properties"]["classification"]["required"]) == {"name", "confident", "entities", "description"}


def test_full_valid_intake_passes_in_one_model_call():
    intake, agent = _make(_payload())

    assert intake.confident
    assert intake.extraction.classification == "team_attendance_report"
    assert intake.decision.selection.protocol_name == "record_attendance_response"
    assert len(agent.calls) == 1


def test_nested_attendance_payload_from_task40_shape_is_canonical():
    payload = _payload(**{"classification.name": "team_availability"})
    intake, agent = _make(payload)

    assert intake.confident
    assert intake.intent.intent == "report"
    assert intake.extraction.classification == "team_availability"
    assert intake.extraction.business_fields == {"availability": "unavailable", "reason": "reserve duty"}
    assert intake.decision.selection.protocol_name == "record_attendance_response"
    assert len(agent.calls) == 1


def test_prompt_and_provider_schema_use_the_same_canonical_shape():
    _intake, agent = _make(_payload())
    prompt, _tools, policy = agent.calls[0]
    schema = policy.response_schema["schema"]

    assert set(schema["properties"]) == {
        "intent", "classification", "business_fields", "risk", "protocol", "temporal"
    }
    assert "exactly these nested sections: intent, classification, business_fields, risk, protocol, temporal" in prompt
    assert "temporal.availability_start and temporal.availability_end must be null" in prompt
    assert policy.response_schema["name"] == "operational_intake"


def test_optional_nullable_fields_may_be_omitted():
    payload = _payload()
    for field_name in ("area", "severity", "occurred_at"):
        del payload["classification"][field_name]
    for field_name in ("expression", "availability_start", "availability_end"):
        del payload["temporal"][field_name]

    intake, agent = _make(payload)

    assert intake.confident
    assert intake.extraction.area is None
    assert intake.extraction.occurred_at is None
    assert len(agent.calls) == 1


@pytest.mark.parametrize(
    ("change", "category"),
    [
        (lambda payload: payload["classification"].pop("description"), "object_keys:classification"),
        (lambda payload: payload.update(unexpected="rejected"), "object_keys"),
        (lambda payload: payload["protocol"].update(status="invalid"), "enum:protocol.status"),
        (lambda payload: payload["risk"].update(risk_score="high"), "type:risk.risk_score"),
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
    payload = _payload(**{"classification.area": 17})

    with pytest.raises(OrchestrationParseError):
        _make(payload)


def test_malformed_json_remains_a_parse_failure():
    agent = _MalformedIntakeAgent(None)

    with pytest.raises(OrchestrationParseError, match="could not parse operational intake JSON"):
        make_operational_intake(agent, MESSAGE, RECEIVED_AT, EVENT_TYPES, AREAS, PROTOCOLS, 0.6)

    assert len(agent.calls) == 1
