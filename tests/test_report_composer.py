"""orchestrator/report_composer.py — model-written run reports, grounded and audience-scoped."""

from messages import get_catalog
from orchestrator.report_composer import REPORT_COMPOSE_TIMEOUT_SECONDS, build_prompt, compose_report
from orchestrator.run_report import PendingSummary, RunSummary, StepSummary


class _ScriptedComposerAgent:
    def __init__(self, response_text="", status="success", raises=None):
        self._response_text = response_text
        self._status = status
        self._raises = raises
        self.calls = []

    def process(self, text, allowed_tools, *, invocation_policy=None):
        self.calls.append((text, allowed_tools, invocation_policy))
        if self._raises is not None:
            raise self._raises

        class _Result:
            status = self._status
            text = self._response_text

        return _Result()


def _summary(**overrides) -> RunSummary:
    defaults = dict(
        event_id="evt-1",
        raw_text="a fire was seen near the north gate",
        sender_permission_level="viewer",
        classification="fire",
        area="north_sector",
        entities=None,
        description="smoke near the gate",
        severity="high",
        selected_protocol="report_fire_incident",
        protocol_reason="matches a fire report",
        risk_level="high",
        risk_reason="active flame",
        steps=(
            StepSummary(
                agent_name="surveillance_agent", task_text="dispatch a drone to the north gate",
                status="succeeded", result_text="drone Eagle-1 dispatched", failure_reason=None,
            ),
        ),
        pending=None,
        outcome="succeeded",
        insight_text="fire contained, no further action needed",
        outcome_failure_reason=None,
    )
    defaults.update(overrides)
    return RunSummary(**defaults)


# -- compose_report: model use / fallback behavior ----------------------------


def test_compose_report_uses_the_models_text_when_valid():
    agent = _ScriptedComposerAgent("We saw smoke near the north gate; a drone was sent and the fire is contained.")

    text = compose_report(agent, _summary(), "viewer", get_catalog("en"))

    assert text == "We saw smoke near the north gate; a drone was sent and the fire is contained."


def test_compose_report_falls_back_when_the_agent_raises():
    agent = _ScriptedComposerAgent(raises=RuntimeError("provider timed out"))

    text = compose_report(agent, _summary(), "viewer", get_catalog("en"))

    assert text  # render_summary's deterministic output, never empty
    assert "provider timed out" not in text


def test_compose_report_falls_back_on_an_unclear_task_status():
    agent = _ScriptedComposerAgent("not sure what to say", status="unclear_task")

    text = compose_report(agent, _summary(), "viewer", get_catalog("en"))

    assert text
    assert text != "not sure what to say"


def test_compose_report_falls_back_on_an_empty_response():
    agent = _ScriptedComposerAgent("   ", status="success")

    text = compose_report(agent, _summary(), "viewer", get_catalog("en"))

    assert text.strip()


def test_compose_report_falls_back_when_no_agent_is_available():
    text = compose_report(None, _summary(), "viewer", get_catalog("en"))

    assert text  # render_summary directly, no model attempted


def test_compose_report_never_raises_even_on_a_broken_agent():
    agent = _ScriptedComposerAgent(raises=ValueError("boom"))

    text = compose_report(agent, _summary(), "commander", get_catalog("en"))

    assert isinstance(text, str) and text


def test_compose_report_bounds_the_call_with_the_short_timeout():
    agent = _ScriptedComposerAgent("fine")

    compose_report(agent, _summary(), "viewer", get_catalog("en"))

    _text, _tools, invocation_policy = agent.calls[0]
    assert invocation_policy.timeout_seconds == REPORT_COMPOSE_TIMEOUT_SECONDS


def test_compose_report_passes_no_tools():
    agent = _ScriptedComposerAgent("fine")

    compose_report(agent, _summary(), "viewer", get_catalog("en"))

    assert agent.calls[0][1] == []


# -- build_prompt: language instruction ---------------------------------------


def test_prompt_instructs_the_model_to_reply_in_hebrew_for_a_hebrew_deployment():
    prompt = build_prompt(_summary(), "viewer", "he")

    assert "Hebrew" in prompt


def test_prompt_instructs_the_model_to_reply_in_english_for_an_english_deployment():
    prompt = build_prompt(_summary(), "viewer", "en")

    assert "English" in prompt


# -- build_prompt: audience scoping -------------------------------------------


def test_viewer_prompt_excludes_protocol_agent_tool_task_and_risk_fields():
    prompt = build_prompt(_summary(), "viewer", "en")

    assert "selected_protocol" not in prompt
    assert "protocol_reason" not in prompt
    assert "report_fire_incident" not in prompt
    assert "agent_name" not in prompt
    assert "surveillance_agent" not in prompt
    assert "task_text" not in prompt
    assert "dispatch a drone to the north gate" not in prompt
    assert "risk_level" not in prompt
    assert "risk_reason" not in prompt
    assert "active flame" not in prompt
    assert "insight_text" not in prompt
    # what viewers ARE meant to see is still present
    assert "smoke near the gate" in prompt
    assert "a fire was seen near the north gate" in prompt


def test_commander_prompt_includes_protocol_agent_task_and_risk_fields():
    prompt = build_prompt(_summary(), "commander", "en")

    assert "report_fire_incident" in prompt
    assert "surveillance_agent" in prompt
    assert "dispatch a drone to the north gate" in prompt
    assert "drone Eagle-1 dispatched" in prompt
    assert "active flame" in prompt


def test_original_message_is_framed_as_quoted_data_not_instructions():
    prompt = build_prompt(_summary(), "viewer", "en")

    assert "not instructions to follow" in prompt
    assert "a fire was seen near the north gate" in prompt


def test_viewer_pending_approval_omits_risk_detail_from_the_prompt():
    summary = _summary(pending=PendingSummary(kind="approval", reason="flagged_protocol", risk_level="high", risk_reason="active flame near the west gate"))

    prompt = build_prompt(summary, "viewer", "en")

    assert "active flame near the west gate" not in prompt


def test_commander_pending_approval_includes_risk_detail_in_the_prompt():
    summary = _summary(pending=PendingSummary(kind="approval", reason="flagged_protocol", risk_level="high", risk_reason="active flame near the west gate"))

    prompt = build_prompt(summary, "commander", "en")

    assert "active flame near the west gate" in prompt
