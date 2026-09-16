"""Output-contract tests for the merged operational decision call."""

import json

import pytest

from orchestrator.main_agent import OrchestrationParseError, make_operational_decision
from profiles import unified_test


class _Result:
    status = "success"

    def __init__(self, text: str):
        self.text = text


class _SequenceAgent:
    def __init__(self, *responses: str):
        self.responses = list(responses)
        self.calls = []

    def process(self, prompt, _allowed_tools, *, invocation_policy=None):
        self.calls.append((prompt, invocation_policy))
        return _Result(self.responses.pop(0))


def _payload(**overrides) -> dict:
    value = {
        "risk_score": 0.2,
        "risk_reason": "routine attendance",
        "protocol_status": "selected",
        "protocol_name": "record_attendance_response",
        "candidate_names": [],
        "protocol_reason": "attendance response fits",
    }
    value.update(overrides)
    return value


def _run(agent):
    return make_operational_decision(
        agent,
        "member is unavailable",
        "team_attendance_report",
        "readiness_team",
        "member availability report",
        "low",
        unified_test.PROTOCOLS,
        unified_test.RISK_THRESHOLD,
    )


def test_valid_merged_json_is_one_call_and_uses_compact_output_policy():
    agent = _SequenceAgent(json.dumps(_payload()))

    decision = _run(agent)

    assert decision.selection.protocol_name == "record_attendance_response"
    assert len(agent.calls) == 1
    assert "protocol_status must be exactly one of: selected, ambiguous, no_match" in agent.calls[0][0]
    policy = agent.calls[0][1]
    assert policy.max_output_tokens == 450
    assert policy.reasoning_effort == "none"
    assert policy.response_schema["name"] == "operational_decision"
    assert set(policy.response_schema["schema"]["required"]) == {
        "risk_score", "risk_reason", "protocol_status", "protocol_name", "candidate_names", "protocol_reason"
    }


@pytest.mark.parametrize("language", ["json", ""])
def test_fenced_merged_json_is_accepted_without_repair(language):
    body = json.dumps(_payload())
    opening = f"```{language}\n"
    agent = _SequenceAgent(f"  \n{opening}{body}\n```  \n")

    _run(agent)

    assert len(agent.calls) == 1


def test_truncated_merged_json_is_rejected_after_one_repair():
    truncated = '{"risk_score": 0.2, "risk_reason": "routine'
    agent = _SequenceAgent(truncated, truncated)

    with pytest.raises(OrchestrationParseError, match="could not parse operational decision JSON"):
        _run(agent)

    assert len(agent.calls) == 2
    repair_prompt = agent.calls[1][0]
    assert "Repair only the JSON shape" in repair_prompt
    assert "Do not add prose, markdown, analysis, or reasoning" in repair_prompt
    assert "do not reconsider the business decision" in repair_prompt


def test_schema_invalid_merged_json_is_rejected_and_not_partially_used():
    invalid = _payload(unexpected="not allowed")
    agent = _SequenceAgent(json.dumps(invalid), json.dumps(invalid))

    with pytest.raises(OrchestrationParseError, match="schema invalid"):
        _run(agent)

    assert len(agent.calls) == 1


@pytest.mark.parametrize(
    "payload,expected_status,expected_protocol",
    [
        (_payload(), "selected", "record_attendance_response"),
        (_payload(protocol_status="no_match", protocol_name=None, protocol_reason="no listed protocol applies"), "no_match", None),
        (_payload(protocol_status="ambiguous", protocol_name=None, candidate_names=["record_attendance_response", "query_surveillance_overview"], protocol_reason="both may apply"), "ambiguous", None),
    ],
)
def test_selected_no_match_and_ambiguous_decisions_keep_existing_semantics(payload, expected_status, expected_protocol):
    agent = _SequenceAgent(json.dumps(payload))

    decision = _run(agent)

    assert decision.selection.status == expected_status
    assert decision.selection.protocol_name == expected_protocol
    assert len(agent.calls) == 1


def test_operational_output_contract_rejects_missing_required_field():
    invalid = _payload()
    del invalid["protocol_reason"]
    agent = _SequenceAgent(json.dumps(invalid), json.dumps(invalid))

    with pytest.raises(OrchestrationParseError, match="schema invalid"):
        _run(agent)


@pytest.mark.parametrize("alias", ["match", "matched"])
def test_protocol_status_aliases_normalize_to_selected(alias):
    agent = _SequenceAgent(json.dumps(_payload(protocol_status=alias)))

    decision = _run(agent)

    assert decision.selection.status == "selected"
    assert decision.selection.protocol_name == "record_attendance_response"
    assert len(agent.calls) == 1


def test_unknown_protocol_status_is_still_rejected():
    agent = _SequenceAgent(json.dumps(_payload(protocol_status="match_or_not")))

    with pytest.raises(OrchestrationParseError, match="invalid operational protocol_status"):
        _run(agent)


def test_match_without_protocol_name_is_rejected_after_normalization():
    agent = _SequenceAgent(json.dumps(_payload(protocol_status="match", protocol_name=None)))

    with pytest.raises(OrchestrationParseError, match="selected unknown protocol"):
        _run(agent)
