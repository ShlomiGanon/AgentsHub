"""POST /Msg with the merged planner: one call classifies the message, routes a question, and
decides a report's fast lane; an unusable plan falls back to the stand-alone classifier."""

import dataclasses
import json

import pytest

from agents import AgentTimeoutError
from api.app import build_app
from profiles import OptimizationPolicy
from protocols import ProtocolSet
from tests.api_fakes import VIEWER_IDENTITY, auth_headers, build_context, happy_path_agent, teardown_ctx
from tests.crewai_fakes import install_crewai_stub

PLAN_PROMPT = "Plan one incoming message in a single answer"
LEGACY_PROMPTS = (
    "kind of message",
    "Decide whether this question can be answered by directly looking up",
    "Decide which of the following agents",
    "You triage one incoming message",
)


@pytest.fixture(autouse=True)
def _mock_crewai(monkeypatch):
    """Mock crewai."""
    install_crewai_stub(monkeypatch)


def _plan(intent: str, evidence: str, **extra) -> str:
    """A merged plan JSON for one message."""
    payload = {
        "primary_intent": intent,
        "asks_for_information": intent == "question",
        "reports_occurrence": intent == "report",
        "requests_action": intent == "request",
        "social_only": intent == "conversational",
        "is_followup_without_context": False,
        "evidence": {intent: evidence},
        "matched_protocol_names": [],
        "reason": "a few words",
        "ambiguity_reason": None,
        "clarification_question": None,
        "question_plan": None,
        "conversational_reply": None,
    }
    payload.update(extra)
    return json.dumps(payload)


def _merged_ctx(tmp_path, agent, *, fast_lane_protocol=False):
    """An API context whose profile runs the merged planner."""
    ctx = build_context(tmp_path, main_agent=agent)
    ctx.loaded_profile.optimization_policy = OptimizationPolicy(planner_mode="merged")
    if fast_lane_protocol:
        protocols = tuple(
            dataclasses.replace(protocol, direct_lane_eligible=True) if protocol.name == "status_check" else protocol
            for protocol in ctx.deps.protocol_set.all()
        )
        ctx = dataclasses.replace(ctx, deps=dataclasses.replace(ctx.deps, protocol_set=ProtocolSet(protocols)))
    return ctx


def test_a_planned_question_is_answered_without_separate_classification_or_routing_calls(tmp_path, teardown_ctx):
    """Classification, the most-recent lookup check, and routing are one call."""
    agent = happy_path_agent()
    agent._dispatch[PLAN_PROMPT] = _plan(
        "question", "status at gate 3",
        question_plan={"route": "agents", "tasks": [{"agent_name": "reference_agent", "task": "status of gate 3"}], "reason": "live"},
    )
    ctx = _merged_ctx(tmp_path, agent)
    teardown_ctx.append(ctx)

    resp = build_app(ctx).test_client().post(
        "/Msg", headers=auth_headers(VIEWER_IDENTITY), json={"text": "what's the status at gate 3?", "sender_identity": VIEWER_IDENTITY},
    )

    assert resp.status_code == 200
    assert resp.get_json() == {"taken_as": "question", "answer": "gate 3 is nominal"}
    assert sum(PLAN_PROMPT in call for call in agent.calls) == 1
    assert not any(legacy in call for call in agent.calls for legacy in LEGACY_PROMPTS)


def test_an_unusable_plan_falls_back_to_the_stand_alone_classifier(tmp_path, teardown_ctx):
    """A plan that fails validation is not a clarification request to the sender."""
    agent = happy_path_agent(intent="question")
    agent._dispatch[PLAN_PROMPT] = "this is not a plan"
    agent._dispatch["Decide which of the following agents"] = "AGENT: reference_agent\nTASK: status of gate 3"
    ctx = _merged_ctx(tmp_path, agent)
    teardown_ctx.append(ctx)

    resp = build_app(ctx).test_client().post(
        "/Msg", headers=auth_headers(VIEWER_IDENTITY), json={"text": "what's the status at gate 3?", "sender_identity": VIEWER_IDENTITY},
    )

    assert resp.status_code == 200
    assert resp.get_json()["taken_as"] == "question"
    assert any("kind of message" in call for call in agent.calls)


def test_an_unavailable_planner_keeps_the_message_as_a_failed_event_without_a_second_wait(tmp_path, teardown_ctx):
    """A model outage during planning is handled like a failed classification: the text survives."""

    class _PlannerDown:
        def __init__(self):
            self.calls = []

        def process(self, text, allowed_tools, invocation_policy=None):
            self.calls.append(text)
            if PLAN_PROMPT in text:
                raise AgentTimeoutError("main_agent", "provider timed out")
            raise AssertionError(f"unexpected call after the planner outage: {text[:80]!r}")

    agent = _PlannerDown()
    ctx = _merged_ctx(tmp_path, agent)
    teardown_ctx.append(ctx)

    resp = build_app(ctx).test_client().post(
        "/Msg", headers=auth_headers(VIEWER_IDENTITY), json={"text": "smoke at gate 3", "sender_identity": VIEWER_IDENTITY},
    )

    assert resp.status_code == 422
    assert len(agent.calls) == 1
    events = ctx.deps.persistence.fetch_events_range("2000-01-01", "2100-01-01")
    assert len(events) == 1 and events[0]["raw_text"] == "smoke at gate 3"


def test_a_planned_fast_lane_report_is_handled_without_a_triage_call(tmp_path, teardown_ctx):
    """The planner's fast-lane decision runs the approved tool directly; no separate triage call."""
    agent = happy_path_agent()
    agent._dispatch[PLAN_PROMPT] = _plan(
        "report", "gate 3 is all clear",
        fast_lane={"eligible": True, "actions": [{"tool_name": "check_status", "parameters": {"location": "gate 3"}}]},
    )
    agent._dispatch["Write the reply to the user's own message"] = "Gate 3 checked: all clear."
    ctx = _merged_ctx(tmp_path, agent, fast_lane_protocol=True)
    teardown_ctx.append(ctx)

    resp = build_app(ctx).test_client().post(
        "/Msg", headers=auth_headers(VIEWER_IDENTITY), json={"text": "gate 3 is all clear", "sender_identity": VIEWER_IDENTITY},
    )

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["taken_as"] == "report" and body["status"] == "completed"
    plan_prompt = next(call for call in agent.calls if PLAN_PROMPT in call)
    assert "FAST LANE (fast_lane)" in plan_prompt and "check_status" in plan_prompt
    assert not any("You triage one incoming message" in call for call in agent.calls)


def test_a_planned_report_the_fast_lane_declines_is_queued_for_the_full_pipeline(tmp_path, teardown_ctx):
    """A declined fast lane queues the report exactly as before, still with no triage call."""
    agent = happy_path_agent(risk_score="0.1", selected="status_check")
    agent._dispatch[PLAN_PROMPT] = _plan("report", "smoke seen at gate 3", fast_lane={"eligible": False, "reason": "a sighting"})
    ctx = _merged_ctx(tmp_path, agent, fast_lane_protocol=True)
    teardown_ctx.append(ctx)

    resp = build_app(ctx).test_client().post(
        "/Msg", headers=auth_headers(VIEWER_IDENTITY), json={"text": "smoke seen at gate 3", "sender_identity": VIEWER_IDENTITY},
    )

    assert resp.status_code == 202
    assert resp.get_json()["status"] == "queued"
    assert not any("You triage one incoming message" in call for call in agent.calls)
    ctx.queue.wait_until_idle()


def test_a_viewers_conversational_reply_never_comes_from_the_plan_that_saw_internals(tmp_path, teardown_ctx):
    """The merged plan sees protocols and agents; the reply is written by a call that never did."""
    agent = happy_path_agent()
    agent._dispatch[PLAN_PROMPT] = _plan("conversational", "what can you do")
    agent._dispatch["Reply naturally and directly"] = "I help you report events and check the current picture."
    ctx = _merged_ctx(tmp_path, agent)
    teardown_ctx.append(ctx)

    resp = build_app(ctx).test_client().post(
        "/Msg", headers=auth_headers(VIEWER_IDENTITY), json={"text": "what can you do?", "sender_identity": VIEWER_IDENTITY},
    )

    assert resp.status_code == 200
    assert resp.get_json() == {"taken_as": "conversational", "answer": "I help you report events and check the current picture."}
    plan_prompt = next(call for call in agent.calls if PLAN_PROMPT in call)
    assert "conversational_reply" not in plan_prompt
    reply_prompt = next(call for call in agent.calls if "Reply naturally and directly" in call)
    assert '"name": "reference_agent"' not in reply_prompt and '"protocols"' not in reply_prompt
