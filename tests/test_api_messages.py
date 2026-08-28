import types

import pytest

from agents import adapter
from api.app import build_app
from api.operations import job_status
from tests.api_fakes import COMMANDER_IDENTITY, VIEWER_IDENTITY, ScriptedAgent, auth_headers, build_context, happy_path_agent


@pytest.fixture(autouse=True)
def _mock_crewai(monkeypatch):
    class _FakeOutput:
        def __init__(self, raw):
            self.raw = raw

    class _FakeCrewAgent:
        def __init__(self, **kwargs):
            pass

        def kickoff(self, text):
            return _FakeOutput("status nominal, no anomalies")

    fake_module = types.SimpleNamespace(Agent=_FakeCrewAgent, tools=types.SimpleNamespace(BaseTool=object))
    monkeypatch.setattr(adapter, "_get_crewai", lambda: fake_module)


def _ctx_with(tmp_path, main_agent):
    return build_context(tmp_path, main_agent=main_agent)


@pytest.fixture
def teardown_ctx():
    contexts = []
    yield contexts
    for ctx in contexts:
        ctx.queue.stop()
        ctx.deps.persistence.close()


def test_a_question_is_answered_directly_with_no_job(tmp_path, teardown_ctx):
    agent = happy_path_agent(intent="question")
    agent._dispatch["Decide which of the following agents"] = "AGENT: reference_agent\nTASK: what's the status?"
    ctx = _ctx_with(tmp_path, agent)
    teardown_ctx.append(ctx)
    client = build_app(ctx).test_client()

    resp = client.post("/Msg", headers=auth_headers(VIEWER_IDENTITY), json={"text": "what's the status at gate 3?", "sender_identity": VIEWER_IDENTITY})

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["taken_as"] == "question"
    assert "answer" in body
    assert ctx.deps.persistence.fetch_events_range("2000-01-01", "2100-01-01") == []  # no event written


def test_a_conversational_message_is_answered_directly_with_no_job(tmp_path, teardown_ctx):
    agent = happy_path_agent(intent="conversational")
    agent._dispatch["Reply naturally and directly"] = "Doing well, thanks for asking!"
    ctx = _ctx_with(tmp_path, agent)
    teardown_ctx.append(ctx)
    client = build_app(ctx).test_client()

    resp = client.post("/Msg", headers=auth_headers(VIEWER_IDENTITY), json={"text": "hey, how are you?", "sender_identity": VIEWER_IDENTITY})

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["taken_as"] == "conversational"
    assert body["answer"] == "Doing well, thanks for asking!"
    conversational_prompt = next(call for call in agent.calls if "Reply naturally and directly" in call)
    assert '"profile_name": "For Tests"' in conversational_prompt
    assert '"name": "reference_agent"' in conversational_prompt
    assert '"name": "status_check"' in conversational_prompt
    assert ctx.deps.persistence.fetch_events_range("2000-01-01", "2100-01-01") == []  # no event written


def test_a_report_returns_202_with_a_job_id(tmp_path, teardown_ctx):
    agent = happy_path_agent(risk_score="0.1", selected="status_check", intent="report")
    ctx = _ctx_with(tmp_path, agent)
    teardown_ctx.append(ctx)
    client = build_app(ctx).test_client()

    resp = client.post("/Msg", headers=auth_headers(VIEWER_IDENTITY), json={"text": "smoke seen at gate 3", "sender_identity": VIEWER_IDENTITY})

    assert resp.status_code == 202
    body = resp.get_json()
    assert body["taken_as"] == "report"
    assert body["status"] == "queued"

    ctx.queue.wait_until_idle()
    assert job_status(ctx, body["event_id"])["status"] == "succeeded"


def test_a_request_returns_202_and_is_classified_human_activation(tmp_path, teardown_ctx):
    agent = happy_path_agent(risk_score="0.1", selected="status_check", intent="request")
    ctx = _ctx_with(tmp_path, agent)
    teardown_ctx.append(ctx)
    client = build_app(ctx).test_client()

    resp = client.post("/Msg", headers=auth_headers(VIEWER_IDENTITY), json={"text": "please check on gate 3", "sender_identity": VIEWER_IDENTITY})

    assert resp.status_code == 202
    body = resp.get_json()
    assert body["taken_as"] == "request"

    event = ctx.deps.persistence.fetch_event(body["event_id"])
    assert event["classification"] == "human_activation"


def test_a_commanders_own_request_bypasses_the_approval_flag(tmp_path, teardown_ctx):
    agent = happy_path_agent(risk_score="0.9", selected="dispatch_response", intent="request")
    ctx = _ctx_with(tmp_path, agent)
    teardown_ctx.append(ctx)
    client = build_app(ctx).test_client()

    resp = client.post("/Msg", headers=auth_headers(COMMANDER_IDENTITY), json={"text": "dispatch someone to gate 3", "sender_identity": COMMANDER_IDENTITY})
    event_id = resp.get_json()["event_id"]
    ctx.queue.wait_until_idle()

    event = ctx.deps.persistence.fetch_event(event_id)
    assert event["approval_held"] is False
    assert job_status(ctx, event_id)["status"] == "succeeded"


def test_a_viewers_request_for_a_flagged_protocol_still_holds(tmp_path, teardown_ctx):
    agent = happy_path_agent(risk_score="0.9", selected="dispatch_response", intent="request")
    ctx = _ctx_with(tmp_path, agent)
    teardown_ctx.append(ctx)
    client = build_app(ctx).test_client()

    resp = client.post("/Msg", headers=auth_headers(VIEWER_IDENTITY), json={"text": "dispatch someone to gate 3", "sender_identity": VIEWER_IDENTITY})
    event_id = resp.get_json()["event_id"]
    ctx.queue.wait_until_idle()

    assert job_status(ctx, event_id)["status"] == "held_for_approval"


def test_post_msg_rejects_missing_text(tmp_path, teardown_ctx):
    ctx = _ctx_with(tmp_path, happy_path_agent())
    teardown_ctx.append(ctx)
    client = build_app(ctx).test_client()

    resp = client.post("/Msg", headers=auth_headers(VIEWER_IDENTITY), json={"sender_identity": VIEWER_IDENTITY})

    assert resp.status_code == 400
    assert resp.get_json()["field"] == "text"


def test_post_msg_requires_authentication(tmp_path, teardown_ctx):
    ctx = _ctx_with(tmp_path, happy_path_agent())
    teardown_ctx.append(ctx)
    client = build_app(ctx).test_client()

    resp = client.post("/Msg", json={"text": "hello", "sender_identity": VIEWER_IDENTITY})

    assert resp.status_code == 401


def test_a_question_matching_no_agent_gets_a_clean_reply_not_a_forced_dispatch(tmp_path, teardown_ctx):
    # The repro-1 shape: "do I have any tasks?" matches nothing any loaded
    # agent's role covers. The NONE: line lets the Main Agent say so
    # cleanly instead of being forced onto reference_agent.
    agent = happy_path_agent(intent="question")
    agent._dispatch["Decide which of the following agents"] = "NONE: no loaded agent tracks individual user tasks"
    ctx = _ctx_with(tmp_path, agent)
    teardown_ctx.append(ctx)
    client = build_app(ctx).test_client()

    resp = client.post("/Msg", headers=auth_headers(VIEWER_IDENTITY), json={"text": "do I have any tasks?", "sender_identity": VIEWER_IDENTITY})

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["taken_as"] == "question"
    assert body["answer"] == "I don't have a way to answer that. no loaded agent tracks individual user tasks"


def test_a_direct_lookup_question_bypasses_agent_selection_and_answers_from_history(tmp_path, teardown_ctx):
    # The repro-2 shape: "what is the last event?" — recognized by the new
    # classification step and answered via
    # HistoryQueryService.answer_most_recent_event directly, never through
    # the AGENT:/TASK: agent-selection call that used to crash on this
    # question shape with a 422.
    agent = happy_path_agent(risk_score="0.1", selected="status_check", intent="report")
    ctx = _ctx_with(tmp_path, agent)
    teardown_ctx.append(ctx)
    client = build_app(ctx).test_client()

    # Seed one real event so there is something for "the last event" to
    # resolve to.
    report_resp = client.post(
        "/Msg", headers=auth_headers(VIEWER_IDENTITY),
        json={"text": "smoke seen at gate 3", "sender_identity": VIEWER_IDENTITY},
    )
    assert report_resp.status_code == 202
    ctx.queue.wait_until_idle()
    assert job_status(ctx, report_resp.get_json()["event_id"])["status"] == "succeeded"

    agent._dispatch["kind of message"] = "INTENT: question\nREASON: asks about the last event"
    agent._dispatch["Decide whether this question can be answered by directly looking up"] = "DIRECT_LOOKUP: most_recent"

    resp = client.post("/Msg", headers=auth_headers(VIEWER_IDENTITY), json={"text": "what is the last event?", "sender_identity": VIEWER_IDENTITY})

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["taken_as"] == "question"
    assert "answer" in body and body["answer"]


def test_a_question_the_main_agent_cannot_route_becomes_a_run_failure_error(tmp_path, teardown_ctx):
    agent = ScriptedAgent(
        {
            "kind of message": "INTENT: question\nREASON: asks about status",
            "Decide whether this question can be answered by directly looking up": "ROUTE: normal",
            # Neither AGENT:/TASK: lines nor a NONE: line — a genuine parse
            # failure, distinct from a clean NONE decline.
            "Decide which of the following agents": "I cannot determine which agent to ask.",
        }
    )
    ctx = _ctx_with(tmp_path, agent)
    teardown_ctx.append(ctx)
    client = build_app(ctx).test_client()

    resp = client.post("/Msg", headers=auth_headers(VIEWER_IDENTITY), json={"text": "what's the status?", "sender_identity": VIEWER_IDENTITY})

    assert resp.status_code == 422
    assert resp.get_json()["error_class"] == "run_failure"


def test_ambiguous_intent_returns_clarification_without_writing_an_event(tmp_path, teardown_ctx):
    import json

    agent = happy_path_agent()
    agent._dispatch["kind of message"] = json.dumps({
        "primary_intent": "needs_clarification",
        "asks_for_information": False,
        "reports_occurrence": False,
        "requests_action": False,
        "social_only": False,
        "is_quoted": False,
        "is_hypothetical": False,
        "is_followup_without_context": True,
        "evidence": {},
        "matched_protocol_names": [],
        "reason": "missing referent",
        "ambiguity_reason": "there is no prior message context",
        "clarification_question": "Which location do you mean?",
    })
    ctx = _ctx_with(tmp_path, agent)
    teardown_ctx.append(ctx)
    client = build_app(ctx).test_client()

    response = client.post(
        "/Msg",
        headers=auth_headers(VIEWER_IDENTITY),
        json={"text": "check there", "sender_identity": VIEWER_IDENTITY},
    )

    assert response.status_code == 200
    assert response.get_json() == {"taken_as": "clarification", "answer": "Which location do you mean?"}
    assert ctx.deps.persistence.fetch_events_range("2000-01-01", "2100-01-01") == []


def test_history_count_question_uses_the_structured_database_route(tmp_path, teardown_ctx):
    import json

    agent = happy_path_agent(intent="question")
    agent._dispatch["Decide whether this question can be answered by directly looking up"] = "ROUTE: normal"
    agent._dispatch["Decide which of the following agents"] = json.dumps({
        "route": "history",
        "history_query": {
            "operation": "count",
            "time_start": "2026-08-01T00:00:00",
            "time_end": "2026-09-01T00:00:00",
            "time_basis": "occurred_at",
            "classifications": ["fire"],
            "areas": ["north_sector"],
            "outcomes": [],
            "protocol_names": [],
            "event_ids": [],
            "risk_levels": [],
            "order": "newest",
            "group_by": "none",
            "limit": 50,
        },
        "reason": "count stored fires",
    })
    ctx = _ctx_with(tmp_path, agent)
    teardown_ctx.append(ctx)
    for event_id in ("fire-1", "fire-2"):
        ctx.deps.persistence.append_event({
            "event_id": event_id,
            "received_at": "2026-08-10T10:00:00",
            "source": "sensor",
            "sender_identity": "sensor-1",
            "occurred_at": "2026-08-10T10:00:00",
            "raw_text": "fire in north sector",
            "classification": "fire",
            "area": "north_sector",
        })
    client = build_app(ctx).test_client()

    response = client.post(
        "/Msg",
        headers=auth_headers(VIEWER_IDENTITY),
        json={"text": "How many northern fires were there in August?", "sender_identity": VIEWER_IDENTITY},
    )

    assert response.status_code == 200
    assert response.get_json()["taken_as"] == "question"
    assert response.get_json()["answer"] == "2 matching events."
