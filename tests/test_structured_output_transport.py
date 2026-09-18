"""Provider-boundary tests for the Task 56 structured reasoning contract."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from agents import AgentResult, InvocationPolicy, adapter
from orchestrator.situational_picture import _parse_reasoning_json


_MODEL = "openrouter/anthropic/claude-sonnet-4.6"
_SCHEMA = {
    "type": "object",
    "properties": {
        "facts": {"type": "array", "items": {"type": "object"}},
    },
    "required": ["facts"],
    "additionalProperties": False,
}


@pytest.fixture(autouse=True)
def _clear_crewai_import_cache():
    adapter._import_crewai.cache_clear()
    yield
    adapter._import_crewai.cache_clear()


def _policy() -> InvocationPolicy:
    return InvocationPolicy(
        max_output_tokens=650,
        timeout_seconds=45.0,
        reasoning_effort="none",
        response_schema={"name": "operational_sitrep", "schema": _SCHEMA},
    )


def _descriptor():
    return SimpleNamespace(
        name="main_agent",
        role="commander SITREP",
        system_prompt="system",
        tools=(),
        model=_MODEL,
        api_key="construction-only-key",
    )


def test_openrouter_auto_selects_closed_json_schema_on_the_wire():
    adapter.configure_structured_output_mode("auto")
    try:
        options = adapter._llm_options(_descriptor(), timeout_seconds=45.0, invocation_policy=_policy())
        wire_format = options["additional_params"]["response_format"]

        assert "response_format" not in options
        assert wire_format == {
            "type": "json_schema",
            "json_schema": {
                "name": "operational_sitrep",
                "strict": True,
                "schema": _SCHEMA,
            },
        }

        crewai = adapter._get_crewai()
        llm = crewai.LLM(**options)
        request = llm._prepare_completion_params([{"role": "user", "content": "test"}])
        safe_request = {key: value for key, value in request.items() if key != "api_key"}

        assert safe_request["model"] == "anthropic/claude-sonnet-4.6"
        assert safe_request["response_format"] == wire_format
        assert safe_request["response_format"]["json_schema"]["strict"] is True
        assert safe_request["response_format"]["json_schema"]["schema"]["additionalProperties"] is False
        assert safe_request["response_format"]["json_schema"]["schema"]["required"] == ["facts"]
        assert "construction-only-key" not in json.dumps(safe_request, sort_keys=True)
    finally:
        adapter.configure_structured_output_mode("off")


def test_openrouter_structured_request_is_not_sent_as_crewai_direct_json_schema():
    adapter.configure_structured_output_mode("auto")
    try:
        options = adapter._llm_options(_descriptor(), timeout_seconds=45.0, invocation_policy=_policy())

        assert "response_format" not in options
        assert options["additional_params"]["response_format"]["type"] == "json_schema"
    finally:
        adapter.configure_structured_output_mode("off")


class _FakeCompletions:
    def __init__(self, content: str | None = None, error: Exception | None = None):
        self.content = content
        self.error = error
        self.captured_params = None

    def create(self, **params):
        self.captured_params = params
        if self.error is not None:
            raise self.error
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=self.content, tool_calls=None, refusal=None),
                    finish_reason="stop",
                    id="response-id",
                )
            ],
            usage=None,
            id="response-id",
        )


def _llm_with_response(content: str | None = None, error: Exception | None = None):
    crewai = adapter._get_crewai()
    llm = crewai.LLM(
        model=_MODEL,
        api_key="construction-only-key",
        max_tokens=650,
        timeout=45.0,
        max_retries=0,
        additional_params={"response_format": {"type": "json_object"}},
    )
    completions = _FakeCompletions(content=content, error=error)
    llm._client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    return llm, completions


@pytest.mark.parametrize(
    "content",
    (
        '{"facts":[],"assessments":[],"recommendations":[]}',
        '{"facts":[{"text":"x"}],"assessments":[],"recommendations":[]}',
        '{"facts":[',
        "not json",
    ),
)
def test_openrouter_message_content_is_normalized_without_repair_or_coercion(content):
    llm, completions = _llm_with_response(content=content)

    normalized = llm._handle_completion(
        params=llm._prepare_completion_params([{"role": "user", "content": "test"}]),
        available_functions=None,
        from_task=None,
        from_agent=None,
        response_model=None,
    )

    assert normalized == content
    assert completions.captured_params["response_format"] == {"type": "json_object"}

    if content.startswith("{") and content.endswith("}"):
        json.loads(normalized)
    else:
        with pytest.raises(ValueError):
            _parse_reasoning_json(normalized)


def test_provider_error_stays_an_error_at_the_provider_boundary():
    llm, _ = _llm_with_response(error=RuntimeError("provider error"))

    with pytest.raises(RuntimeError, match="provider error"):
        llm._handle_completion(
            params=llm._prepare_completion_params([{"role": "user", "content": "test"}]),
            available_functions=None,
            from_task=None,
            from_agent=None,
            response_model=None,
        )


def test_valid_transport_payload_still_enters_the_existing_agent_result_boundary():
    result = AgentResult(
        status="success",
        text='{"facts":[],"assessments":[],"recommendations":[]}',
    )

    assert result.status == "success"
    assert _parse_reasoning_json(result.text)["facts"] == []
