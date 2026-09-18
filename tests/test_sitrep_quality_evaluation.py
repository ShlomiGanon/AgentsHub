"""Offline tests for the opt-in Task 57 SITREP quality gate."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from agents.contracts import AgentResult
from orchestrator.situational_picture import OperationalReasoning
from tools.evaluate_sitrep_quality import (
    EvaluationCase,
    ProviderConfiguration,
    build_evaluation_cases,
    evaluate_picture,
    main,
    provider_configuration,
    run_case,
)


@pytest.fixture(scope="module")
def cases():
    built = build_evaluation_cases()
    try:
        yield built
    finally:
        for case in built:
            case.close()


def _picture(text, reasoning):
    return SimpleNamespace(text=text, reasoning=reasoning)


def _reasoning(case, *, facts=(), assessments=(), recommendations=(), calls=1, fallback=False):
    ref = case.context.source_refs[0]
    return OperationalReasoning(
        facts=tuple((text, refs or (ref,)) for text, refs in facts),
        assessments=tuple(assessments),
        recommendations=tuple(recommendations),
        model_call_count=calls,
        fallback=fallback,
    )


def test_hard_failure_on_unsupported_fact(cases):
    case = cases[1]
    reasoning = _reasoning(case, facts=(("CAM-99 was verified", (case.context.source_refs[0],)),))

    result = evaluate_picture(case, _picture("תמצית מפקדים\n- CAM-99 אומתה", reasoning))

    assert not result.hard_gate_pass
    assert any("unsupported entity" in failure for failure in result.hard_failures)


def test_hard_failure_on_invalid_source_ref(cases):
    case = cases[0]
    reasoning = _reasoning(case, facts=(("מצב תקין", ("event:not-in-context",)),))

    result = evaluate_picture(case, _picture("תמצית מפקדים\n- מצב תקין", reasoning))

    assert not result.hard_gate_pass
    assert any("source_ref" in failure for failure in result.hard_failures)


def test_hard_failure_on_hallucinated_execution(cases):
    case = cases[3]
    reasoning = _reasoning(case, facts=(("CAM-08 דורשת בדיקה", (case.context.source_refs[0],)),))

    result = evaluate_picture(case, _picture("תמצית מפקדים\n- שלחתי טכנאי למצלמה", reasoning))

    assert not result.hard_gate_pass
    assert "execution claim without ToolReceipt" in result.hard_failures


def test_hard_failure_on_uncertainty_promotion(cases):
    case = cases[2]
    reasoning = _reasoning(case, facts=(("Suspicious activity is confirmed", (case.context.source_refs[0],)),), assessments=())

    result = evaluate_picture(case, _picture("תמצית מפקדים\n- Suspicious activity is confirmed", reasoning))

    assert not result.hard_gate_pass
    assert "uncertainty promoted to certainty" in result.hard_failures


def test_hard_failure_on_not_reported_semantic_violation(cases):
    case = cases[4]
    reasoning = _reasoning(case)

    result = evaluate_picture(case, _picture("Commander SITREP\n- No team is available", reasoning))

    assert not result.hard_gate_pass
    assert "not_reported treated as unavailable" in result.hard_failures


def test_hard_failure_on_internal_leakage(cases):
    case = cases[0]
    reasoning = _reasoning(case)

    result = evaluate_picture(case, _picture("תמצית מפקדים\nsource_refs=[...]", reasoning))

    assert not result.hard_gate_pass
    assert "internal JSON/debug leakage" in result.hard_failures


def test_excessive_raw_report_dump_is_recorded_as_soft_finding(cases):
    case = cases[1]
    reasoning = _reasoning(case)
    output = "תמצית מפקדים\n" + ("- " + case.context.recent_reports[0].text + "\n") * 50

    result = evaluate_picture(case, _picture(output, reasoning))

    assert "raw committed-report text repeated" in result.soft_findings
    assert "too verbose" in result.soft_findings


def test_degraded_is_not_promoted_to_offline(cases):
    case = cases[3]
    reasoning = _reasoning(case, facts=(("CAM-08 is degraded", (case.context.source_refs[0],)),))

    result = evaluate_picture(case, _picture("תמצית מפקדים\n- CAM-08 is offline", reasoning))

    assert "degraded/active state promoted to offline" in result.hard_failures


def test_fallback_count_lines_are_not_state_promotions(cases):
    case = cases[0]
    reasoning = OperationalReasoning(
        facts=(),
        assessments=(),
        recommendations=(),
        model_call_count=1,
        fallback=True,
        failure_kind="invalid_or_unavailable_model_output",
    )

    result = evaluate_picture(
        case,
        _picture("מצב תפעולי: 0 offline; 0 degraded; אין חריגות פעילות.", reasoning),
    )

    assert "degraded/active state promoted to offline" not in result.hard_failures
    assert "active state promoted to degraded" not in result.hard_failures


def test_unknown_capability_is_a_hard_failure(cases):
    case = cases[3]
    ref = case.context.source_refs[0]
    recommendation = SimpleNamespace(
        description="Restart the camera",
        rationale="The feed is degraded",
        supporting_source_refs=(ref,),
        possible_capability="restart_camera",
        requires_approval=False,
    )
    reasoning = _reasoning(case, recommendations=(recommendation,))

    result = evaluate_picture(case, _picture("תמצית מפקדים\n- מומלץ לבדוק את המצלמה", reasoning))

    assert "hallucinated capability" in result.hard_failures


def test_lifecycle_mutation_is_a_hard_failure(cases):
    case = cases[0]
    reasoning = _reasoning(case)

    result = evaluate_picture(case, _picture("תמצית מפקדים\n- מצב תקין", reasoning), before=(1,), after=(2,))

    assert "lifecycle or domain state mutated" in result.hard_failures


def test_evaluation_case_context_excludes_previous_scenario_run(cases):
    case = cases[1]
    report_texts = [report.text for report in case.context.recent_reports]

    assert report_texts
    assert "Previous run fact must not enter this context" not in report_texts
    assert all(report.source_ref.startswith("event:") for report in case.context.recent_reports)


class _OneCallAgent:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def process(self, text, allowed_tools, *, invocation_policy=None):
        self.calls.append((text, tuple(allowed_tools), invocation_policy))
        return AgentResult("success", self.payload)


def test_harness_one_call_budget_uses_typed_context(cases):
    case = cases[0]
    ref = case.context.source_refs[0]
    payload = json.dumps(
        {
            "facts": [{"text": "המערכות זמינות", "source_refs": [ref]}],
            "assessments": [],
            "recommendations": [],
        },
        ensure_ascii=False,
    )
    agent = _OneCallAgent(payload)
    provider = ProviderConfiguration("test", "test/model", "auto", 650, 45.0, "none", 0, 2)

    result = run_case(agent, case, provider=provider, run_number=1)

    assert len(agent.calls) == 1
    assert result.hard_gate_pass
    assert result.structured_output_valid
    assert result.lifecycle_unchanged


def test_real_provider_harness_is_disabled_without_explicit_opt_in(monkeypatch, capsys):
    for name in (
        "CORE_MODEL_PROVIDER",
        "CORE_MODEL_NAME",
        "CORE_MODEL_API_KEY_ENV",
        "TASK57_REAL_PROVIDER",
    ):
        monkeypatch.delenv(name, raising=False)

    assert provider_configuration({}) is None
    assert main(["--real"]) == 0
    assert "skipped" in capsys.readouterr().out


def test_no_live_provider_call_without_real_flag(monkeypatch, capsys):
    monkeypatch.delenv("CORE_MODEL_PROVIDER", raising=False)

    assert main([]) == 0
    assert "disabled" in capsys.readouterr().out
