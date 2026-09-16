"""Equivalence coverage for the separate and merged operational decisions."""

import json

import pytest

from orchestrator.holds import determine_approval_hold
from orchestrator.main_agent import assess_risk, make_operational_decision, select_protocol
from profiles import unified_test
from profiles.contracts import OptimizationPolicy


class _Result:
    status = "success"

    def __init__(self, text):
        self.text = text


class _DecisionAgent:
    """Return the equivalent separate or merged answer for one test case."""

    def __init__(self, risk_text: str, selection_text: str, merged_payload: dict):
        self.risk_text = risk_text
        self.selection_text = selection_text
        self.merged_text = json.dumps(merged_payload)
        self.calls: list[tuple[str, object]] = []

    def process(self, prompt, _allowed_tools, *, invocation_policy=None):
        self.calls.append((prompt, invocation_policy))
        if invocation_policy is not None:
            return _Result(self.merged_text)
        if "Assess the risk" in prompt:
            return _Result(self.risk_text)
        if "Choose the protocol" in prompt:
            return _Result(self.selection_text)
        raise AssertionError(f"unexpected operational prompt: {prompt[:100]!r}")


def _business_decision(risk, selection):
    protocols_by_name = {protocol.name: protocol for protocol in unified_test.PROTOCOLS}
    approval = determine_approval_hold(selection, protocols_by_name, originated_from_commander=False)
    return risk.level, risk.score, selection.status, selection.protocol_name, selection.candidate_names, approval


_CASES = (
    pytest.param(
        "team attendance report",
        "team_attendance_report", "readiness_team", "member availability report", "low",
        "0.2", "record_attendance_response", "recorded attendance response",
        {"risk_score": 0.2, "risk_reason": "routine attendance", "protocol_status": "selected", "protocol_name": "record_attendance_response", "candidate_names": [], "protocol_reason": "attendance response fits"},
        id="team-attendance-report",
    ),
    pytest.param(
        "surveillance report",
        "surveillance_report", "north_gate", "camera reports smoke near gate", "moderate",
        "0.4", "query_surveillance_overview", "surveillance overview fits",
        {"risk_score": 0.4, "risk_reason": "limited observation", "protocol_status": "selected", "protocol_name": "query_surveillance_overview", "candidate_names": [], "protocol_reason": "surveillance overview fits"},
        id="surveillance-report",
    ),
    pytest.param(
        "no matching protocol",
        "surveillance_report", "central_hub", "a social message with no operational request", "none",
        "0.1", None, None,
        {"risk_score": 0.1, "risk_reason": "no operational hazard", "protocol_status": "no_match", "protocol_name": None, "candidate_names": [], "protocol_reason": "no listed protocol applies"},
        id="no-match",
    ),
    pytest.param(
        "high-risk protocol requiring approval",
        "emergency_dispatch", "north_gate", "dispatch emergency forces to the incident", "high",
        "0.9", "query_surveillance_overview,dispatch_emergency_forces", "both fit; high risk chooses the emergency protocol",
        {"risk_score": 0.9, "risk_reason": "active emergency", "protocol_status": "ambiguous", "protocol_name": None, "candidate_names": ["query_surveillance_overview", "dispatch_emergency_forces"], "protocol_reason": "high risk requires the most critical candidate"},
        id="high-risk-approval",
    ),
    pytest.param(
        "ambiguous input",
        "surveillance_report", "central_hub", "camera and drone status are both requested", "moderate",
        "0.3", "query_surveillance_overview,query_drone_fleet_status", "both protocols fit equally",
        {"risk_score": 0.3, "risk_reason": "informational request", "protocol_status": "ambiguous", "protocol_name": None, "candidate_names": ["query_surveillance_overview", "query_drone_fleet_status"], "protocol_reason": "both protocols fit equally"},
        id="ambiguous-input",
    ),
)


def test_merged_mode_is_enabled_only_for_unified_test():
    assert unified_test.OPTIMIZATION_POLICY.operational_decision_mode == "merged"
    assert OptimizationPolicy().operational_decision_mode == "separate"


@pytest.mark.parametrize(
    "case_name,classification,area,description,severity,risk_score,selection,candidate_or_reason,merged_payload",
    _CASES,
)
def test_merged_and_separate_have_equivalent_business_decisions(
    case_name, classification, area, description, severity, risk_score, selection, candidate_or_reason, merged_payload
):
    protocols = unified_test.PROTOCOLS
    threshold = unified_test.RISK_THRESHOLD
    if selection is None:
        separate_selection_text = "NO_MATCH: no listed protocol applies"
    elif "," in selection:
        separate_selection_text = f"AMBIGUOUS: {selection}\nREASON: {candidate_or_reason}"
    else:
        separate_selection_text = f"SELECTED: {selection}\nREASON: {candidate_or_reason}"
    separate_agent = _DecisionAgent(
        f"RISK_SCORE: {risk_score}\nREASON: separate {case_name}", separate_selection_text, merged_payload
    )
    separate_risk = assess_risk(separate_agent, classification, area, description, severity, threshold)
    separate_selection_result = select_protocol(
        separate_agent, description, classification, area, description, protocols, separate_risk.level
    )

    merged_agent = _DecisionAgent(
        f"RISK_SCORE: {risk_score}\nREASON: separate {case_name}", separate_selection_text, merged_payload
    )
    merged = make_operational_decision(
        merged_agent, description, classification, area, description, severity, protocols, threshold
    )

    assert _business_decision(merged.risk, merged.selection) == _business_decision(
        separate_risk, separate_selection_result
    )
    assert len(separate_agent.calls) == 2
    assert len(merged_agent.calls) == 1
