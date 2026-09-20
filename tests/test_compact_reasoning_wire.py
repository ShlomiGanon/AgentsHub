"""Task 62 compact provider-wire contract and size tests."""

from __future__ import annotations

import json

import pytest

from orchestrator.situational_picture import (
    REASONING_CONCLUSION_TEXT_MAX,
    REASONING_FACT_TEXT_MAX,
    REASONING_QUALIFICATION_TEXT_MAX,
    REASONING_RATIONALE_TEXT_MAX,
    REASONING_RECOMMENDATION_TEXT_MAX,
    _REASONING_SCHEMA,
    _expand_reasoning_wire_payload,
    _reasoning_schema_issue,
    _validate_reasoning_payload,
    build_operational_context,
)
from tools.evaluate_sitrep_quality import build_evaluation_cases


def _max_valid_wire_payload() -> dict:
    return {
        "facts": [
            {"text": "א" * REASONING_FACT_TEXT_MAX, "source_aliases": ["S1", "S2"]}
            for _ in range(2)
        ],
        "assessments": [
            {
                "conclusion": "ב" * REASONING_CONCLUSION_TEXT_MAX,
                "source_aliases": ["S1"],
                "confidence": "high",
                "qualification": "ג" * REASONING_QUALIFICATION_TEXT_MAX,
                "affected_domains": ["team", "surveillance"],
                "priority": "high",
            }
            for _ in range(1)
        ],
        "recommendations": [
            {
                "description": "ד" * REASONING_RECOMMENDATION_TEXT_MAX,
                "rationale": "ה" * REASONING_RATIONALE_TEXT_MAX,
                "source_aliases": ["S1"],
                "priority": "medium",
                "possible_capability": "ask_current_state",
                "requires_approval": True,
            }
            for _ in range(1)
        ],
    }


def _case_context():
    cases = build_evaluation_cases()
    case = next(case for case in cases if case.case_id == "B-sec-phase1-supported")
    return cases, case.context


def test_provider_context_is_compact_and_hides_canonical_source_ids():
    cases, context = _case_context()
    try:
        payload = context.provider_prompt_payload()
        serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

        assert set(payload) == {"t", "q", "o", "v", "s", "r", "f", "u"}
        assert payload["q"] == context.query_scope.as_dict()
        assert payload["o"] == context.operational_scope
        assert payload["v"] == [f"S{index}" for index in range(1, len(context.source_refs) + 1)]
        assert all(source_ref not in serialized for source_ref in context.source_refs)
        assert "authoritative_facts" not in serialized
        assert "current_run_operational_reports" not in serialized
        assert len(serialized) < len(json.dumps(context.prompt_payload(), ensure_ascii=False, separators=(",", ":")))
    finally:
        for case in cases:
            case.close()


def test_maximum_valid_wire_payload_is_closed_and_bounded():
    payload = _max_valid_wire_payload()
    serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

    assert _reasoning_schema_issue(payload, _REASONING_SCHEMA) is None
    assert len(serialized) <= 900
    assert len(serialized.encode("utf-8")) > len(serialized)


@pytest.mark.parametrize(
    "payload",
    (
        {"facts": [{"text": "x" * (REASONING_FACT_TEXT_MAX + 1), "source_aliases": ["S1"]}], "assessments": [], "recommendations": []},
        {"facts": [], "assessments": [{"conclusion": "x", "source_aliases": ["S1"], "confidence": "high", "qualification": "x" * (REASONING_QUALIFICATION_TEXT_MAX + 1), "affected_domains": ["cross_domain"], "priority": "high"}], "recommendations": []},
        {"facts": [], "assessments": [], "recommendations": [{"description": "x", "rationale": "x" * (REASONING_RATIONALE_TEXT_MAX + 1), "source_aliases": ["S1"], "priority": "medium", "possible_capability": None, "requires_approval": False}]},
    ),
)
def test_wire_string_bounds_are_rejected(payload):
    assert _reasoning_schema_issue(payload, _REASONING_SCHEMA) is not None


def test_wire_cardinality_and_arbitrary_fields_are_rejected():
    payload = _max_valid_wire_payload()
    payload["assessments"].append(payload["assessments"][0])
    assert _reasoning_schema_issue(payload, _REASONING_SCHEMA)[0] == "array_too_long"

    payload = _max_valid_wire_payload()
    payload["recommendations"][0]["unexpected"] = True
    assert _reasoning_schema_issue(payload, _REASONING_SCHEMA)[0] == "additional_property"


def test_alias_conversion_restores_canonical_refs_and_rejects_unknown_or_duplicate_aliases():
    cases, context = _case_context()
    try:
        payload = {"facts": [{"text": "fact", "source_aliases": ["S1"]}], "assessments": [], "recommendations": []}
        expanded = _expand_reasoning_wire_payload(payload, context)
        assert expanded["facts"][0]["source_refs"] == [context.source_refs[0]]

        duplicate = {"facts": [{"text": "fact", "source_aliases": ["S1", "S1"]}], "assessments": [], "recommendations": []}
        with pytest.raises(ValueError, match="duplicate"):
            _expand_reasoning_wire_payload(duplicate, context)

        unknown = {"facts": [{"text": "fact", "source_aliases": ["S99"]}], "assessments": [], "recommendations": []}
        unknown_expanded = _expand_reasoning_wire_payload(unknown, context)
        assert unknown_expanded["facts"][0]["source_refs"] == ["S99"]
        with pytest.raises(ValueError, match="unknown source_ref"):
            _validate_reasoning_payload(unknown_expanded, context, wire_schema_validated=True)
    finally:
        for case in cases:
            case.close()
