"""Intent parser regressions for the narrow Markdown JSON-fence normalization."""

import json

import pytest

from orchestrator.main_agent import OrchestrationParseError, _parse_intent_response, classify_intent


class _ScriptedAgent:
    def __init__(self, response: str):
        self.response = response
        self.calls: list[str] = []

    def process(self, prompt, allowed_tools):
        self.calls.append(prompt)

        class _Result:
            status = "success"

            def __init__(self, text):
                self.text = text

        return _Result(self.response)


def _valid_payload(message: str = "smoke at gate 3") -> dict:
    return {
        "primary_intent": "report",
        "asks_for_information": False,
        "reports_occurrence": True,
        "requests_action": False,
        "social_only": False,
        "is_quoted": False,
        "is_hypothetical": False,
        "is_followup_without_context": False,
        "evidence": {"report": message},
        "matched_protocol_names": [],
        "reason": "reports an operational occurrence",
        "ambiguity_reason": None,
        "clarification_question": None,
    }


def _json(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False)


@pytest.mark.parametrize(
    "response",
    [
        _json(_valid_payload()),
        "```json\n" + _json(_valid_payload()) + "\n```",
        "```\n" + _json(_valid_payload()) + "\n```",
        "  \n```json  \n" + _json(_valid_payload()) + "\n```\n  ",
    ],
)
def test_valid_plain_and_fenced_json_are_accepted_without_retry(response):
    agent = _ScriptedAgent(response)

    result = classify_intent(agent, (), "smoke at gate 3")

    assert result.intent == "report"
    assert len(agent.calls) == 1  # model calls = 1, retries = 0


def test_malformed_fenced_json_is_still_rejected():
    with pytest.raises(OrchestrationParseError):
        _parse_intent_response("```json\n{\"primary_intent\":\n```", "smoke at gate 3", ())


def test_schema_invalid_fenced_json_is_still_rejected_by_existing_validation():
    payload = _valid_payload()
    payload["primary_intent"] = "delete_everything"

    with pytest.raises(OrchestrationParseError, match="invalid primary_intent"):
        _parse_intent_response("```json\n" + _json(payload) + "\n```", "smoke at gate 3", ())


@pytest.mark.parametrize(
    "response",
    [
        "Here is the JSON:\n```json\n" + _json(_valid_payload()) + "\n```",
        _json(_valid_payload()) + "\nAdditional explanation",
        "```text\n" + _json(_valid_payload()) + "\n```",
    ],
)
def test_prose_or_unsupported_fence_language_is_not_accepted_as_intent_json(response):
    with pytest.raises(OrchestrationParseError):
        _parse_intent_response(response, "smoke at gate 3", ())
