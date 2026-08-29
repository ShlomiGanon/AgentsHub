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
    # docs/Next_Plan.md §5 decision record: sub_agents/protocols are
    # view_system_internals, commander-only — absent entirely from a
    # viewer's conversational prompt (see the commander-role test below).
    assert '"name": "reference_agent"' not in conversational_prompt
    assert '"name": "status_check"' not in conversational_prompt
    assert '"sub_agents"' not in conversational_prompt
    assert '"protocols"' not in conversational_prompt
    assert ctx.deps.persistence.fetch_events_range("2000-01-01", "2100-01-01") == []  # no event written


def test_a_conversational_message_from_a_commander_includes_protocols_and_sub_agents(tmp_path, teardown_ctx):
    agent = happy_path_agent(intent="conversational")
    agent._dispatch["Reply naturally and directly"] = "Doing well, thanks for asking!"
    ctx = _ctx_with(tmp_path, agent)
    teardown_ctx.append(ctx)
    client = build_app(ctx).test_client()

    resp = client.post("/Msg", headers=auth_headers(COMMANDER_IDENTITY), json={"text": "hey, how are you?", "sender_identity": COMMANDER_IDENTITY})

    assert resp.status_code == 200
    conversational_prompt = next(call for call in agent.calls if "Reply naturally and directly" in call)
    assert '"profile_name": "For Tests"' in conversational_prompt
    assert '"name": "reference_agent"' in conversational_prompt
    assert '"name": "status_check"' in conversational_prompt


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
    # Uses a commander caller: ask_question ownership scoping (docs/Next_Plan.md
    # §5 decision record) would otherwise restrict this count to the caller's
    # own events, and these fixture events belong to a sensor, not the caller —
    # that scoping has its own dedicated coverage elsewhere.
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
        headers=auth_headers(COMMANDER_IDENTITY),
        json={"text": "How many northern fires were there in August?", "sender_identity": COMMANDER_IDENTITY},
    )

    assert response.status_code == 200
    assert response.get_json()["taken_as"] == "question"
    assert response.get_json()["answer"] == "2 matching events."


def test_ask_question_end_to_end_ownership_scoping(tmp_path, teardown_ctx):
    # docs/Next_Plan.md §5 decision record, end to end through POST /Msg: a
    # viewer's structured count only ever covers events they themselves
    # submitted, even though two matching events exist in the database — a
    # commander asking the identical question sees both.
    import json

    def _count_history_query():
        return {
            "route": "history",
            "history_query": {
                "operation": "count", "time_start": "2026-08-01T00:00:00", "time_end": "2026-09-01T00:00:00",
                "time_basis": "occurred_at", "classifications": ["fire"], "areas": ["north_sector"],
                "outcomes": [], "protocol_names": [], "event_ids": [], "risk_levels": [],
                "order": "newest", "group_by": "none", "limit": 50,
            },
            "reason": "count stored fires",
        }

    agent = happy_path_agent(intent="question")
    agent._dispatch["Decide whether this question can be answered by directly looking up"] = "ROUTE: normal"
    agent._dispatch["Decide which of the following agents"] = json.dumps(_count_history_query())
    ctx = _ctx_with(tmp_path, agent)
    teardown_ctx.append(ctx)
    for event_id, sender in (("mine", VIEWER_IDENTITY), ("theirs", COMMANDER_IDENTITY)):
        ctx.deps.persistence.append_event({
            "event_id": event_id,
            "received_at": "2026-08-10T10:00:00",
            "source": "sensor",
            "sender_identity": sender,
            "occurred_at": "2026-08-10T10:00:00",
            "raw_text": "fire in north sector",
            "classification": "fire",
            "area": "north_sector",
        })
    client = build_app(ctx).test_client()

    viewer_response = client.post(
        "/Msg", headers=auth_headers(VIEWER_IDENTITY),
        json={"text": "How many northern fires were there in August?", "sender_identity": VIEWER_IDENTITY},
    )
    commander_response = client.post(
        "/Msg", headers=auth_headers(COMMANDER_IDENTITY),
        json={"text": "How many northern fires were there in August?", "sender_identity": COMMANDER_IDENTITY},
    )

    assert viewer_response.get_json()["answer"] == "1 matching event."
    assert commander_response.get_json()["answer"] == "2 matching events."


def test_question_follow_up_carries_conversation_context_into_the_routing_prompt(tmp_path, teardown_ctx):
    # docs/Next_Plan.md §10, end to end: a follow-up question reusing the
    # same conversation_id must reach orchestrator routing with the prior
    # turn's content available, so a reference like "and that one?" can be
    # resolved to a stable Event ID. Conversation memory is off by default
    # in this test harness (matching every other test here); this is the
    # one test that turns it on.
    agent = happy_path_agent(intent="question")
    agent._dispatch["Decide whether this question can be answered by directly looking up"] = "ROUTE: normal"
    agent._dispatch["Decide which of the following agents"] = "AGENT: reference_agent\nTASK: describe status"
    ctx = build_context(tmp_path, main_agent=agent, conversation_history_turns=6)
    teardown_ctx.append(ctx)
    client = build_app(ctx).test_client()
    conversation_id = "conv-follow-up-1"

    first = client.post(
        "/Msg", headers=auth_headers(VIEWER_IDENTITY),
        json={
            "text": "what's the status at gate 3?", "sender_identity": VIEWER_IDENTITY,
            "conversation_id": conversation_id,
        },
    )
    assert first.status_code == 200

    second = client.post(
        "/Msg", headers=auth_headers(VIEWER_IDENTITY),
        json={"text": "and what about that?", "sender_identity": VIEWER_IDENTITY, "conversation_id": conversation_id},
    )

    assert second.status_code == 200
    routing_prompts = [call for call in agent.calls if "Decide which of the following agents" in call]
    assert routing_prompts, "the routing prompt was never sent"
    assert "gate 3" in routing_prompts[-1]  # the prior turn reached the second call's prompt


# --- Stage 6 adversarial disclosure corpus (docs/Next_Plan.md §11) ---------


def _load_adversarial_corpus():
    import json
    from pathlib import Path

    path = Path(__file__).resolve().parent.parent / "fixtures" / "adversarial_disclosure_v1.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_adversarial_corpus_loads_every_mandatory_category():
    corpus = _load_adversarial_corpus()
    categories = {case["category"] for case in corpus}
    assert categories == {
        "identity", "capability_discovery", "protocol_extraction", "sub_agent_extraction",
        "tool_extraction", "prompt_injection", "quoted_instruction", "indirect_request", "event_follow_up",
    }
    assert {case["language"] for case in corpus} == {"en", "he"}


def test_adversarial_corpus_viewer_prompt_never_leaks_protected_names(tmp_path, teardown_ctx):
    # docs/Next_Plan.md §11: every mandatory disclosure scenario, direct,
    # indirect, injected, or quoted, and in both corpus languages — none of
    # them may cause a protocol, sub-agent, or tool name to reach the
    # prompt actually sent to the model for a viewer caller.
    agent = happy_path_agent(intent="conversational")
    agent._dispatch["Reply naturally and directly"] = "I can help with reports, requests, and questions."
    ctx = _ctx_with(tmp_path, agent)
    teardown_ctx.append(ctx)
    client = build_app(ctx).test_client()

    for case in _load_adversarial_corpus():
        resp = client.post(
            "/Msg", headers=auth_headers(VIEWER_IDENTITY),
            json={"text": case["message"], "sender_identity": VIEWER_IDENTITY},
        )
        assert resp.status_code == 200, case["id"]
        # Isolate the application-supplied system context specifically — a
        # quoted-instruction case's own adversarial text may legitimately
        # contain a protected name (the attacker typed it themselves); what
        # must never happen is the *application* handing that name to the
        # model as fact via the system context.
        conversational_prompt = agent.calls[-1]
        system_context_segment = conversational_prompt.split("System context JSON: ", 1)[1].split("\nMessage JSON:", 1)[0]
        for forbidden in case["forbidden_substrings"]:
            assert forbidden not in system_context_segment, f"{case['id']} leaked {forbidden!r} into the viewer's system context"


def test_adversarial_corpus_commander_prompt_retains_protected_names(tmp_path, teardown_ctx):
    # Positive control for the test above: proves the corpus and harness
    # actually exercise disclosure (protocol/agent names reach a
    # commander's prompt), rather than every case vacuously passing
    # because nothing is ever disclosed to anyone.
    agent = happy_path_agent(intent="conversational")
    agent._dispatch["Reply naturally and directly"] = "Here is what you asked for."
    ctx = _ctx_with(tmp_path, agent)
    teardown_ctx.append(ctx)
    client = build_app(ctx).test_client()

    corpus = [case for case in _load_adversarial_corpus() if case["forbidden_substrings"]]
    any_disclosed = False
    for case in corpus:
        resp = client.post(
            "/Msg", headers=auth_headers(COMMANDER_IDENTITY),
            json={"text": case["message"], "sender_identity": COMMANDER_IDENTITY},
        )
        assert resp.status_code == 200, case["id"]
        conversational_prompt = agent.calls[-1]
        system_context_segment = conversational_prompt.split("System context JSON: ", 1)[1].split("\nMessage JSON:", 1)[0]
        if any(name in system_context_segment for name in case["forbidden_substrings"]):
            any_disclosed = True

    assert any_disclosed, "no adversarial case reached a commander's full system context — the positive control is broken"


def test_permission_downgrade_mid_conversation_is_enforced_on_the_very_next_turn(tmp_path, teardown_ctx):
    # docs/Next_Plan.md §11: "a previously visible conversation turn is
    # followed by a permission downgrade" — authorization and context
    # filtering must be recomputed from the caller's *current* level on
    # every turn; a turn answered while still a commander must not leave
    # any residual access for the same conversation_id after a downgrade.
    agent = happy_path_agent(intent="conversational")
    agent._dispatch["Reply naturally and directly"] = "Sure, here you go."
    ctx = build_context(tmp_path, main_agent=agent, conversation_history_turns=6)
    teardown_ctx.append(ctx)
    client = build_app(ctx).test_client()
    identity = "downgraded-1"
    conversation_id = "conv-downgrade-1"
    ctx.deps.persistence.write_user(identity, "commander")

    first = client.post(
        "/Msg", headers=auth_headers(identity),
        json={"text": "what protocols do you have?", "sender_identity": identity, "conversation_id": conversation_id},
    )
    assert first.status_code == 200
    first_prompt = agent.calls[-1]
    first_context = first_prompt.split("System context JSON: ", 1)[1].split("\nMessage JSON:", 1)[0]
    assert "status_check" in first_context  # commander turn: full context, as expected

    ctx.deps.persistence.write_user(identity, "viewer")

    second = client.post(
        "/Msg", headers=auth_headers(identity),
        json={"text": "and what about now?", "sender_identity": identity, "conversation_id": conversation_id},
    )
    assert second.status_code == 200
    second_prompt = agent.calls[-1]
    second_context = second_prompt.split("System context JSON: ", 1)[1].split("\nMessage JSON:", 1)[0]
    assert "status_check" not in second_context
    assert '"protocols"' not in second_context
    assert '"sub_agents"' not in second_context
