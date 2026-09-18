"""Opt-in, non-secret diagnostics for one structured provider invocation."""

from __future__ import annotations

import hashlib
import re
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Iterator, Mapping


DIAGNOSTIC_STAGES = (
    "REQUEST_BUILD",
    "PROVIDER_REQUEST",
    "PROVIDER_RESPONSE",
    "PROVIDER_NORMALIZATION",
    "CONTENT_EXTRACTION",
    "JSON_PARSE",
    "SCHEMA_VALIDATION",
    "CANONICAL_MODEL_VALIDATION",
    "PROVENANCE_VALIDATION",
    "RENDER",
    "FALLBACK",
)

_active_trace: ContextVar["ProviderDiagnosticTrace | None"] = ContextVar(
    "active_provider_diagnostic_trace",
    default=None,
)


def _type_name(value: object) -> str:
    value_type = type(value)
    return f"{value_type.__module__}.{value_type.__qualname__}"


def _safe_keys(value: object) -> tuple[str, ...]:
    if isinstance(value, Mapping):
        return tuple(sorted(str(key) for key in value.keys())[:64])
    try:
        keys = vars(value).keys()
    except TypeError:
        return ()
    return tuple(sorted(str(key) for key in keys if not str(key).startswith("_"))[:64])


def _shape(value: object) -> dict[str, object]:
    result: dict[str, object] = {"python_type": _type_name(value)}
    keys = _safe_keys(value)
    if keys:
        result["keys"] = list(keys)
    if isinstance(value, (list, tuple)):
        result["length"] = len(value)
        result["item_types"] = sorted({_type_name(item) for item in value[:16]})
    return result


def _safe_string(value: object, *, limit: int = 96) -> str | None:
    if value is None:
        return None
    text = str(value)
    text = re.sub(r"(?i)(authorization|api[_-]?key|token|secret|cookie)\s*[:=]\s*[^,;\s]+", r"\1=[redacted]", text)
    return text[:limit]


def _safe_usage(value: object) -> dict[str, int | float] | None:
    if not isinstance(value, Mapping):
        return None
    allowed = (
        "prompt_tokens",
        "input_tokens",
        "completion_tokens",
        "output_tokens",
        "total_tokens",
        "cached_tokens",
        "cache_read_tokens",
    )
    result: dict[str, int | float] = {}
    for key in allowed:
        item = value.get(key)
        if isinstance(item, (int, float)) and not isinstance(item, bool):
            result[key] = item
    return result or None


def _content_metadata(content: object) -> dict[str, object]:
    metadata: dict[str, object] = {
        "content_container_type": None if content is None else _type_name(content),
        "content_block_types": [],
        "extracted_content_type": None if content is None else _type_name(content),
        "extracted_content_length": None,
        "content_sha256": None,
        "starts_with_json_object": None,
        "ends_with_json_object": None,
        "markdown_fence_detected": False,
        "truncation_indicator": None,
    }
    if isinstance(content, str):
        stripped = content.strip()
        metadata.update(
            {
                "extracted_content_length": len(content),
                "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                "starts_with_json_object": stripped.startswith("{"),
                "ends_with_json_object": stripped.endswith("}"),
                "markdown_fence_detected": stripped.startswith("```") or "```" in stripped,
            }
        )
    elif isinstance(content, (list, tuple)):
        metadata["extracted_content_length"] = len(content)
        metadata["content_block_types"] = sorted({_type_name(item) for item in content[:32]})
    return metadata


def _response_shape(response: object) -> dict[str, object]:
    shape = _shape(response)
    choices = getattr(response, "choices", None)
    if isinstance(choices, (list, tuple)):
        shape["choices_count"] = len(choices)
        if choices:
            choice = choices[0]
            shape["choice_shape"] = _shape(choice)
            message = getattr(choice, "message", None)
            if message is not None:
                shape["message_shape"] = _shape(message)
                content = getattr(message, "content", None)
                shape["message_content"] = _content_metadata(content)
                parsed = getattr(message, "parsed", None)
                shape["structured_field_present"] = parsed is not None
                if parsed is not None:
                    shape["structured_field_shape"] = _shape(parsed)
    return shape


def _error_category(exc: BaseException) -> str:
    name = type(exc).__name__.casefold()
    text = str(exc).casefold()
    if isinstance(exc, TimeoutError) or "timeout" in name or "timed out" in text:
        return "timeout"
    if "response_format" in text or "json schema" in text or "schema" in text and "invalid" in text:
        return "provider_rejected_schema"
    return "provider_error"


@dataclass
class ProviderDiagnosticTrace:
    provider: str
    model: str
    structured_mode: str
    trace_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    schema_requested: bool = False
    strict_requested: bool = False
    provider_http_status: int | None = None
    provider_error_category: str | None = None
    provider_error_type: str | None = None
    response_received: bool = False
    response_envelope_type: str | None = None
    response_shape: dict[str, object] = field(default_factory=dict)
    finish_reason: str | None = None
    usage: dict[str, int | float] | None = None
    content_container_type: str | None = None
    content_block_types: list[str] = field(default_factory=list)
    extracted_content_type: str | None = None
    extracted_content_length: int | None = None
    content_sha256: str | None = None
    starts_with_json_object: bool | None = None
    ends_with_json_object: bool | None = None
    markdown_fence_detected: bool = False
    truncation_indicator: bool | None = None
    json_parse_success: bool | None = None
    json_error_category: str | None = None
    json_error_position: int | None = None
    json_error_line: int | None = None
    json_error_column: int | None = None
    parsed_top_level_type: str | None = None
    schema_validation_success: bool | None = None
    schema_validation_error_category: str | None = None
    schema_validation_error_path: str | None = None
    canonical_validation_success: bool | None = None
    canonical_validation_error_category: str | None = None
    provenance_validation_success: bool | None = None
    provenance_invalid_ref_count: int = 0
    provenance_missing_ref_count: int = 0
    provenance_unknown_ref_count: int = 0
    provenance_claim_type: str | None = None
    fallback_reason: str | None = None
    first_failed_stage: str | None = None
    last_successful_stage: str | None = None
    stages: dict[str, str] = field(default_factory=dict)
    _failure_recorded: bool = field(default=False, init=False, repr=False)

    def mark_success(self, stage: str) -> None:
        if stage not in DIAGNOSTIC_STAGES:
            return
        self.stages[stage] = "success"
        if not self._failure_recorded:
            self.last_successful_stage = stage

    def mark_failure(self, stage: str, *, category: str | None = None) -> None:
        if stage not in DIAGNOSTIC_STAGES:
            return
        self.stages[stage] = "failed"
        if self.first_failed_stage is None:
            self.first_failed_stage = stage
            self._failure_recorded = True
        if stage == "FALLBACK":
            self.fallback_reason = category or self.fallback_reason or "unknown"

    def record_request_build(self, options: Mapping[str, object]) -> None:
        response_format = options.get("response_format")
        additional = options.get("additional_params")
        if isinstance(additional, Mapping) and response_format is None:
            response_format = additional.get("response_format")
        self.schema_requested = isinstance(response_format, Mapping) and response_format.get("type") == "json_schema"
        schema = response_format.get("json_schema") if isinstance(response_format, Mapping) else None
        self.strict_requested = isinstance(schema, Mapping) and schema.get("strict") is True
        self.mark_success("REQUEST_BUILD")

    def record_provider_request(self, params: Mapping[str, object] | None = None) -> None:
        if isinstance(params, Mapping):
            self.record_request_build(params)
        self.mark_success("PROVIDER_REQUEST")

    def record_provider_response(self, response: object) -> None:
        self.response_received = True
        self.response_envelope_type = _type_name(response)
        self.response_shape = _response_shape(response)
        self.finish_reason = _safe_string(getattr(response.choices[0], "finish_reason", None)) if getattr(response, "choices", None) else None
        self.usage = _safe_usage(getattr(response, "usage", None))
        self.mark_success("PROVIDER_RESPONSE")

    def record_provider_error(self, exc: BaseException) -> None:
        if self.provider_error_category is None:
            self.provider_error_category = _error_category(exc)
            self.provider_error_type = type(exc).__name__
            status = getattr(exc, "status_code", None)
            if isinstance(status, int):
                self.provider_http_status = status
        self.mark_failure("PROVIDER_RESPONSE", category=self.provider_error_category)

    def record_normalization(self, normalized: object) -> None:
        self.response_shape["crewai_normalized_shape"] = _shape(normalized)
        self.mark_success("PROVIDER_NORMALIZATION")

    def record_content_extraction(self, content: object) -> None:
        metadata = _content_metadata(content)
        self.content_container_type = metadata["content_container_type"]
        self.content_block_types = list(metadata["content_block_types"])
        self.extracted_content_type = metadata["extracted_content_type"]
        self.extracted_content_length = metadata["extracted_content_length"]
        self.content_sha256 = metadata["content_sha256"]
        self.starts_with_json_object = metadata["starts_with_json_object"]
        self.ends_with_json_object = metadata["ends_with_json_object"]
        self.markdown_fence_detected = bool(metadata["markdown_fence_detected"])
        self.mark_success("CONTENT_EXTRACTION")

    def record_content_failure(self, category: str = "response_extraction_failed") -> None:
        self.mark_failure("CONTENT_EXTRACTION", category=category)

    def record_json_success(self, payload: object) -> None:
        self.json_parse_success = True
        self.parsed_top_level_type = _type_name(payload)
        self.mark_success("JSON_PARSE")

    def record_json_failure(self, exc: BaseException, raw_text: object) -> None:
        self.json_parse_success = False
        if isinstance(exc, ValueError) and not isinstance(exc, SyntaxError):
            self.json_error_category = "non_object_or_empty"
        if hasattr(exc, "pos"):
            self.json_error_position = getattr(exc, "pos", None)
            self.json_error_line = getattr(exc, "lineno", None)
            self.json_error_column = getattr(exc, "colno", None)
            self.json_error_category = "invalid_json"
        if self.truncation_indicator is None and isinstance(raw_text, str):
            stripped = raw_text.strip()
            self.truncation_indicator = stripped.startswith("{") and not stripped.endswith("}")
        if self.truncation_indicator:
            self.json_error_category = "truncated"
        self.mark_failure("JSON_PARSE", category=self.json_error_category or "json_parse_failed")

    def record_schema_success(self) -> None:
        self.schema_validation_success = True
        self.mark_success("SCHEMA_VALIDATION")

    def record_schema_failure(self, category: str, path: str | None = None) -> None:
        self.schema_validation_success = False
        self.schema_validation_error_category = category
        self.schema_validation_error_path = path
        self.mark_failure("SCHEMA_VALIDATION", category="schema_validation_failed")

    def record_canonical_success(self) -> None:
        self.canonical_validation_success = True
        self.mark_success("CANONICAL_MODEL_VALIDATION")

    def record_canonical_failure(self, category: str) -> None:
        self.canonical_validation_success = False
        self.canonical_validation_error_category = category
        self.mark_failure("CANONICAL_MODEL_VALIDATION", category="canonical_validation_failed")

    def record_provenance_success(self) -> None:
        self.provenance_validation_success = True
        self.mark_success("PROVENANCE_VALIDATION")

    def record_provenance_failure(
        self,
        category: str,
        *,
        invalid_ref_count: int = 0,
        missing_ref_count: int = 0,
        unknown_ref_count: int = 0,
        claim_type: str | None = None,
    ) -> None:
        self.provenance_validation_success = False
        self.provenance_invalid_ref_count = invalid_ref_count
        self.provenance_missing_ref_count = missing_ref_count
        self.provenance_unknown_ref_count = unknown_ref_count
        self.provenance_claim_type = claim_type
        self.mark_failure("PROVENANCE_VALIDATION", category="provenance_validation_failed")

    def record_fallback(self, reason: str | None = None) -> None:
        resolved = reason or self.fallback_reason
        if resolved is None and self.first_failed_stage == "PROVIDER_RESPONSE":
            resolved = self.provider_error_category or "provider_error"
        if resolved is None and self.first_failed_stage == "JSON_PARSE":
            resolved = self.json_error_category or "json_parse_failed"
        if resolved is None:
            resolved = "unknown"
        self.fallback_reason = resolved
        self.mark_success("FALLBACK")

    def record_render(self) -> None:
        self.mark_success("RENDER")

    def artifact(self) -> dict[str, object]:
        return {
            "trace_id": self.trace_id,
            "provider": self.provider,
            "model": self.model,
            "structured_mode": self.structured_mode,
            "schema_requested": self.schema_requested,
            "strict_requested": self.strict_requested,
            "stages": dict(self.stages),
            "last_successful_stage": self.last_successful_stage,
            "first_failed_stage": self.first_failed_stage,
            "provider_http_status": self.provider_http_status,
            "provider_error_category": self.provider_error_category,
            "provider_error_type": self.provider_error_type,
            "response_received": self.response_received,
            "response_envelope_type": self.response_envelope_type,
            "response_shape": self.response_shape,
            "finish_reason": self.finish_reason,
            "usage": self.usage,
            "content_container_type": self.content_container_type,
            "content_block_types": self.content_block_types,
            "extracted_content_type": self.extracted_content_type,
            "extracted_content_length": self.extracted_content_length,
            "content_sha256": self.content_sha256,
            "starts_with_json_object": self.starts_with_json_object,
            "ends_with_json_object": self.ends_with_json_object,
            "markdown_fence_detected": self.markdown_fence_detected,
            "truncation_indicator": self.truncation_indicator,
            "json_parse_success": self.json_parse_success,
            "json_error_category": self.json_error_category,
            "json_error_position": self.json_error_position,
            "json_error_line": self.json_error_line,
            "json_error_column": self.json_error_column,
            "parsed_top_level_type": self.parsed_top_level_type,
            "schema_validation_success": self.schema_validation_success,
            "schema_validation_error_category": self.schema_validation_error_category,
            "schema_validation_error_path": self.schema_validation_error_path,
            "canonical_validation_success": self.canonical_validation_success,
            "canonical_validation_error_category": self.canonical_validation_error_category,
            "provenance_validation_success": self.provenance_validation_success,
            "provenance_invalid_ref_count": self.provenance_invalid_ref_count,
            "provenance_missing_ref_count": self.provenance_missing_ref_count,
            "provenance_unknown_ref_count": self.provenance_unknown_ref_count,
            "provenance_claim_type": self.provenance_claim_type,
            "fallback_reason": self.fallback_reason,
        }


class _DiagnosticResourceProxy:
    def __init__(self, target: object, trace: ProviderDiagnosticTrace, path: str):
        self._target = target
        self._trace = trace
        self._path = path

    def __getattr__(self, name: str) -> object:
        target = getattr(self._target, name)
        path = f"{self._path}.{name}"
        if name in {"create", "parse"} and callable(target):
            return self._wrap_call(target, path)
        if name in {"chat", "completions", "responses", "beta"}:
            return _DiagnosticResourceProxy(target, self._trace, path)
        return target

    def _wrap_call(self, target, path: str):
        def _call(*args, **kwargs):
            params = kwargs if kwargs else (args[0] if args and isinstance(args[0], Mapping) else None)
            self._trace.record_provider_request(params)
            try:
                response = target(*args, **kwargs)
            except Exception as exc:
                self._trace.record_provider_error(exc)
                raise
            self._trace.record_provider_response(response)
            return response

        _call.__name__ = path
        return _call


def install_provider_client_diagnostics(llm: object, trace: ProviderDiagnosticTrace):
    """Temporarily wrap CrewAI's synchronous provider client for one call."""

    getter = getattr(llm, "_get_sync_client", None)
    if not callable(getter):
        return lambda: None
    original = getattr(llm, "_client", None)
    try:
        client = getter()
        setattr(llm, "_client", _DiagnosticResourceProxy(client, trace, "client"))
    except Exception as exc:
        trace.record_provider_error(exc)
        return lambda: None

    def _restore() -> None:
        setattr(llm, "_client", original)

    return _restore


def get_active_provider_diagnostic_trace() -> ProviderDiagnosticTrace | None:
    return _active_trace.get()


@contextmanager
def provider_diagnostic_trace(
    *,
    provider: str,
    model: str,
    structured_mode: str,
) -> Iterator[ProviderDiagnosticTrace]:
    trace = ProviderDiagnosticTrace(provider=provider, model=model, structured_mode=structured_mode)
    token = _active_trace.set(trace)
    try:
        yield trace
    finally:
        _active_trace.reset(token)
