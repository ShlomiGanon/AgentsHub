"""Deterministic coverage for opt-in structured-output diagnostics."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from agents.diagnostics import (
    DIAGNOSTIC_STAGES,
    get_active_provider_diagnostic_trace,
    install_provider_client_diagnostics,
    provider_diagnostic_trace,
)
from orchestrator.situational_picture import (
    OperationalContext,
    SituationalQueryScope,
    _parse_reasoning_json,
    reason_over_operational_context,
)


def _trace():
    return provider_diagnostic_trace(
        provider="openrouter",
        model="anthropic/claude-sonnet-4.6",
        structured_mode="auto",
    )


def test_diagnostic_mode_is_disabled_by_default():
    assert get_active_provider_diagnostic_trace() is None


def test_successful_provider_path_records_stages_without_content():
    with _trace() as trace:
        for stage in DIAGNOSTIC_STAGES[:-1]:
            trace.mark_success(stage)
        trace.record_render()

    artifact = trace.artifact()
    assert artifact["first_failed_stage"] is None
    assert artifact["last_successful_stage"] == "RENDER"
    assert all(status == "success" for status in artifact["stages"].values())
    assert "prompt" not in json.dumps(artifact, sort_keys=True).casefold()


def test_reasoning_boundary_records_json_schema_canonical_and_provenance_success():
    class ReasoningAgent:
        def process(self, *_args, **_kwargs):
            return SimpleNamespace(
                status="success",
                text=json.dumps({"facts": [{"text": "grounded", "source_refs": ["state:cameras"]}], "assessments": [], "recommendations": []}),
            )

    context = OperationalContext(
        query_scope=SituationalQueryScope.overall_scope(),
        current_time="2026-09-18T12:00:00+00:00",
        cameras=None,
        drones=None,
        team=None,
        recent_reports=(),
        findings=(),
        inconsistencies=(),
        source_refs=("state:cameras",),
    )
    with _trace() as trace:
        result = reason_over_operational_context(ReasoningAgent(), context, raw_text="status")

    assert result.fallback is False
    assert trace.json_parse_success is True
    assert trace.schema_validation_success is True
    assert trace.canonical_validation_success is True
    assert trace.provenance_validation_success is True
    assert trace.first_failed_stage is None


def test_provider_exception_maps_to_provider_error_without_secret_capture():
    class ProviderFailure(RuntimeError):
        status_code = 400

    with _trace() as trace:
        trace.record_request_build(
            {
                "additional_params": {
                    "response_format": {
                        "type": "json_schema",
                        "json_schema": {"strict": True},
                    }
                },
                "api_key": "secret-api-key",
            }
        )
        trace.record_provider_error(ProviderFailure("Authorization: secret-api-key response_format rejected"))

    artifact_text = json.dumps(trace.artifact(), sort_keys=True)
    assert trace.first_failed_stage == "PROVIDER_RESPONSE"
    assert trace.provider_error_category == "provider_rejected_schema"
    assert trace.provider_http_status == 400
    assert "secret-api-key" not in artifact_text
    assert "Authorization" not in artifact_text


def test_timeout_maps_to_timeout():
    with _trace() as trace:
        trace.record_provider_error(TimeoutError("secret timeout context"))

    assert trace.provider_error_category == "timeout"
    assert trace.fallback_reason is None
    assert trace.first_failed_stage == "PROVIDER_RESPONSE"


def test_missing_response_and_wrong_shape_are_observable():
    with _trace() as missing:
        missing.record_normalization(SimpleNamespace(status="done"))
        missing.record_content_failure()
    assert missing.first_failed_stage == "CONTENT_EXTRACTION"

    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                finish_reason="stop",
                message=SimpleNamespace(
                    content=[SimpleNamespace(type="text")],
                    parsed=None,
                ),
            )
        ],
        usage={"total_tokens": 12},
    )
    with _trace() as wrong_shape:
        wrong_shape.record_provider_response(response)
    assert wrong_shape.response_received is True
    assert wrong_shape.response_shape["choices_count"] == 1
    assert wrong_shape.response_shape["message_content"]["content_container_type"].endswith("list")
    assert wrong_shape.finish_reason == "stop"
    assert wrong_shape.usage == {"total_tokens": 12}


def test_provider_proxy_captures_request_and_response_shape_without_headers():
    class FakeLLM:
        def __init__(self):
            self._client = SimpleNamespace(
                chat=SimpleNamespace(
                    completions=SimpleNamespace(
                        create=lambda **_: SimpleNamespace(
                            choices=[
                                SimpleNamespace(
                                    finish_reason="stop",
                                    message=SimpleNamespace(content='{"facts": []}', parsed=None),
                                )
                            ],
                            usage={"completion_tokens": 4},
                        )
                    )
                )
            )

        def _get_sync_client(self):
            return self._client

    with _trace() as trace:
        llm = FakeLLM()
        restore = install_provider_client_diagnostics(llm, trace)
        try:
            response = llm._get_sync_client().chat.completions.create(
                headers={"Authorization": "Bearer secret-value"},
                response_format={"type": "json_schema", "json_schema": {"strict": True}},
            )
        finally:
            restore()

    artifact_text = json.dumps(trace.artifact(), sort_keys=True)
    assert response.choices[0].message.content == '{"facts": []}'
    assert trace.stages["PROVIDER_REQUEST"] == "success"
    assert trace.stages["PROVIDER_RESPONSE"] == "success"
    assert "secret-value" not in artifact_text
    assert "headers" not in artifact_text


def test_malformed_and_truncated_json_record_location_and_category():
    with _trace() as malformed:
        with pytest.raises(ValueError):
            _parse_reasoning_json('{"facts": [}')
    assert malformed.first_failed_stage == "JSON_PARSE"
    assert malformed.json_parse_success is False
    assert malformed.json_error_position is not None
    assert malformed.json_error_line == 1
    assert malformed.json_error_column is not None
    assert malformed.json_error_category == "invalid_json"

    with _trace() as truncated:
        with pytest.raises(ValueError):
            _parse_reasoning_json('{"facts": [')
    assert truncated.json_error_category == "truncated"
    assert truncated.truncation_indicator is True


def test_schema_canonical_and_provenance_failures_remain_separate():
    with _trace() as schema:
        schema.record_schema_failure("additional_property", "$.facts[0].unexpected")
        schema.record_fallback("schema_validation_failed")
    assert schema.first_failed_stage == "SCHEMA_VALIDATION"
    assert schema.fallback_reason == "schema_validation_failed"

    with _trace() as canonical:
        canonical.record_schema_success()
        canonical.record_canonical_failure("unsupported_execution_claim")
        canonical.record_fallback("canonical_validation_failed")
    assert canonical.first_failed_stage == "CANONICAL_MODEL_VALIDATION"
    assert canonical.schema_validation_success is True

    with _trace() as provenance:
        provenance.record_schema_success()
        provenance.record_canonical_success()
        provenance.record_provenance_failure("unknown_source_refs", unknown_ref_count=1, claim_type="assessment")
        provenance.record_fallback("provenance_validation_failed")
    assert provenance.first_failed_stage == "PROVENANCE_VALIDATION"
    assert provenance.provenance_unknown_ref_count == 1


def test_fallback_reason_tracks_first_failed_stage_and_no_reasoning_content_is_stored():
    with _trace() as trace:
        trace.record_content_extraction("private hidden reasoning must not be stored")
        trace.record_json_failure(ValueError("not an object"), "private hidden reasoning must not be stored")
        trace.record_fallback("json_parse_failed")

    artifact_text = json.dumps(trace.artifact(), sort_keys=True)
    assert trace.first_failed_stage == "JSON_PARSE"
    assert trace.fallback_reason == "json_parse_failed"
    assert "private hidden reasoning" not in artifact_text
