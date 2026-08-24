"""bot/http_api_client.py — every method against a real, running api/*
server over a genuine HTTP socket (work_plan.md §8.12-§8.14's
"HttpApiClient" build step).

`tests/api_fakes.py::RunningApiServer` is what makes this genuine —
`app.test_client()` dispatches WSGI calls in-process and never opens a
real socket, so it cannot prove `HttpApiClient`'s own `urllib` requests
actually work. This file is what proves `UnimplementedApiClient`'s old
methods are genuinely superseded, not just renamed: every one of these
tests would fail if `HttpApiClient` silently fell back to raising
`ApiNotImplementedError`, or produced a response shape `BotApiClient`'s
DTOs can't parse.
"""

import asyncio
import sys
import types
import uuid

import pytest

from agents import adapter
from bot.api_client import BOT_SERVICE_IDENTITY
from bot.errors import ApiRequestError
from bot.http_api_client import HttpApiClient
from history.write import record_event_outcome
from orchestrator.holds import create_approval_hold, create_clarification_hold
from orchestrator.main_agent import RiskAssessment
from orchestrator.selection import ProtocolSelectionResult
from tests.api_fakes import COMMANDER_IDENTITY, VIEWER_IDENTITY, RunningApiServer, build_context, happy_path_agent

_PROFILE_TEMPLATE = """
from protocols.model import Protocol, CriticalityLevel

AGENTS = []
PROTOCOLS = [
    Protocol(
        name="status_check",
        description="applies to a routine status check",
        participating_agents=("reference_agent",),
        approved_tools=("check_status",),
        expected_success_output="a status report",
        criticality=CriticalityLevel.LOW,
        approval_flag=False,
    ),
]
EVENT_TYPES = ["fire"]
AREAS = ["north"]
DB_PATH = {db_path!r}
API_PORT = 9999
RETRY_COUNT = 1
RISK_THRESHOLD = 0.5
LOOKBACK_WINDOW_DAYS = 10
BOT_TOKEN_ENV = "TEST_TOKEN"
MODEL_CREDENTIAL_ENVS = []
"""


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _mock_crewai(monkeypatch):
    class _FakeOutput:
        def __init__(self, raw):
            self.raw = raw

    class _FakeCrewAgent:
        def __init__(self, **kwargs):
            pass

        def kickoff(self, text):
            return _FakeOutput("status nominal")

    fake_module = types.SimpleNamespace(Agent=_FakeCrewAgent, tools=types.SimpleNamespace(BaseTool=object))
    monkeypatch.setattr(adapter, "_get_crewai", lambda: fake_module)


@pytest.fixture
def server(tmp_path):
    """A running api/* server whose bot-service identity is already
    registered — the ordinary case every method except the dedicated
    unregistered-service-identity test exercises.
    """

    ctx = build_context(tmp_path)
    ctx.deps.persistence.write_user(BOT_SERVICE_IDENTITY, "commander")
    with RunningApiServer(ctx) as running:
        yield running


@pytest.fixture
def writable_profile_module(tmp_path, monkeypatch):
    module_name = f"http_client_test_profile_{uuid.uuid4().hex}"
    (tmp_path / f"{module_name}.py").write_text(_PROFILE_TEMPLATE.format(db_path=str(tmp_path / "test.db")), encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    yield module_name
    sys.modules.pop(module_name, None)


def _minimal_event(persistence, **overrides):
    event = {
        "received_at": "2026-08-24T10:00:00",
        "source": "sensor",
        "sender_identity": "submitter-1",
        "occurred_at": "2026-08-24T10:00:00",
        "raw_text": "text",
    }
    event.update(overrides)
    return persistence.append_event(event)


# -- resolve_user -------------------------------------------------------


def test_resolve_user_known_and_unknown(server):
    client = HttpApiClient(server.base_url)

    known = _run(client.resolve_user(COMMANDER_IDENTITY))
    assert known.registered is True
    assert known.permission_level == "commander"

    unknown = _run(client.resolve_user("nobody"))
    assert unknown.registered is False


def test_an_unregistered_service_identity_raises(tmp_path):
    ctx = build_context(tmp_path)  # bot-service deliberately not registered
    with RunningApiServer(ctx) as running:
        client = HttpApiClient(running.base_url)

        with pytest.raises(ApiRequestError) as excinfo:
            _run(client.resolve_user(COMMANDER_IDENTITY))

        assert excinfo.value.status_code == 401


# -- list_commander_chat_ids ----------------------------------------------


def test_list_commander_chat_ids_includes_every_registered_commander(server):
    client = HttpApiClient(server.base_url)

    identities = _run(client.list_commander_chat_ids())

    assert COMMANDER_IDENTITY in identities


# -- submit_message ---------------------------------------------------------


def test_submit_message_question(tmp_path):
    agent = happy_path_agent(intent="question")
    agent._dispatch["Decide which of the following agents"] = "AGENT: reference_agent\nTASK: status?"
    ctx = build_context(tmp_path, main_agent=agent)
    ctx.deps.persistence.write_user(BOT_SERVICE_IDENTITY, "commander")
    with RunningApiServer(ctx) as running:
        client = HttpApiClient(running.base_url)

        result = _run(client.submit_message("how many events today?", VIEWER_IDENTITY, "m1"))

        assert result.kind == "question"
        assert result.answer_text


def test_submit_message_report_never_claims_to_know_awaiting_approval(tmp_path):
    ctx = build_context(tmp_path, main_agent=happy_path_agent(intent="report"))
    ctx.deps.persistence.write_user(BOT_SERVICE_IDENTITY, "commander")
    with RunningApiServer(ctx) as running:
        client = HttpApiClient(running.base_url)

        result = _run(client.submit_message("smoke seen near gate 3", VIEWER_IDENTITY, "m1"))

        assert result.kind == "report"
        assert result.job_id is not None
        assert result.awaiting_approval is False


# -- answer_clarification_hold / answer_approval_hold ------------------------


def test_answer_clarification_hold_resolved_then_conflict(server):
    client = HttpApiClient(server.base_url)
    event_id = _minimal_event(server.ctx.deps.persistence)
    create_clarification_hold(server.ctx.deps.persistence, event_id, "raw text")

    first = _run(client.answer_clarification_hold(event_id, "fire", COMMANDER_IDENTITY))
    assert first.status == "resolved"

    second = _run(client.answer_clarification_hold(event_id, "fire", COMMANDER_IDENTITY))
    assert second.status == "not_found"
    assert second.resolved_by == COMMANDER_IDENTITY


def test_answer_clarification_hold_unauthorized(server):
    client = HttpApiClient(server.base_url)
    event_id = _minimal_event(server.ctx.deps.persistence)
    create_clarification_hold(server.ctx.deps.persistence, event_id, "raw text")

    outcome = _run(client.answer_clarification_hold(event_id, "fire", VIEWER_IDENTITY))

    assert outcome.status == "unauthorized"


def test_answer_approval_hold_approved_and_rejected(server):
    client = HttpApiClient(server.base_url)
    selection = ProtocolSelectionResult(status="selected", protocol_name="dispatch_response", reason="matched")
    risk = RiskAssessment(level="high", score=0.9, reason="r")

    approved_event = _minimal_event(server.ctx.deps.persistence)
    create_approval_hold(server.ctx.deps.persistence, approved_event, "flagged_protocol", selection, risk)
    approved = _run(client.answer_approval_hold(approved_event, "approved", COMMANDER_IDENTITY))
    assert approved.status == "approved"

    rejected_event = _minimal_event(server.ctx.deps.persistence)
    create_approval_hold(server.ctx.deps.persistence, rejected_event, "flagged_protocol", selection, risk)
    rejected = _run(client.answer_approval_hold(rejected_event, "rejected", COMMANDER_IDENTITY))
    assert rejected.status == "rejected"


# -- profile / settings -------------------------------------------------


def test_get_profile_view_matches_the_loaded_profile(server):
    client = HttpApiClient(server.base_url)

    view = _run(client.get_profile_view(VIEWER_IDENTITY))

    assert view.profile_name == server.ctx.loaded_profile.module_path
    names = [p.name for p in view.protocols]
    assert "status_check" in names


def test_get_profile_diff_status_reports_false_when_unchanged(server):
    client = HttpApiClient(server.base_url)

    status = _run(client.get_profile_diff_status())

    assert status.differs_from_running is False


def test_get_and_write_settings_round_trip(server):
    client = HttpApiClient(server.base_url)

    before = _run(client.get_settings_view(VIEWER_IDENTITY))
    assert before.retry_count == 3

    result = _run(client.write_setting("retry_count", 9, COMMANDER_IDENTITY))
    assert result.accepted is True

    after = _run(client.get_settings_view(VIEWER_IDENTITY))
    assert after.retry_count == 9


def test_write_setting_rejects_an_invalid_value(server):
    client = HttpApiClient(server.base_url)

    result = _run(client.write_setting("retry_count", -1, COMMANDER_IDENTITY))

    assert result.accepted is False
    assert "non-negative" in result.message


def test_write_protocol_add_and_reject(tmp_path, writable_profile_module):
    ctx = build_context(tmp_path, module_path=writable_profile_module)
    ctx.deps.persistence.write_user(BOT_SERVICE_IDENTITY, "commander")
    with RunningApiServer(ctx) as running:
        client = HttpApiClient(running.base_url)

        new_protocol = {
            "name": "new_check",
            "description": "d",
            "participating_agents": ["reference_agent"],
            "approved_tools": ["check_status"],
            "expected_success_output": "o",
            "criticality": "LOW",
            "approval_flag": False,
        }
        added = _run(client.write_protocol("add", new_protocol, COMMANDER_IDENTITY))
        assert added.accepted is True

        rejected = _run(client.write_protocol("add", {"name": "incomplete"}, COMMANDER_IDENTITY))
        assert rejected.accepted is False


# -- Problem 1: these five now enforce the *real* caller's permission ------
# level server-side, not the bot's own blanket commander-level service
# identity — a viewer asking directly (bypassing the bot's own client-side
# check entirely, the same "what if the client-side check is buggy or
# removed" scenario the §8.2 audit fix already covered once) must still be
# refused by the API itself.


def test_get_profile_view_still_works_for_a_viewer_server_side(server):
    # Confirms the fix didn't accidentally make reads commander-only —
    # §8.7's own "allow viewers to read" still holds, enforced server-side
    # against the real caller now, not just client-side.
    client = HttpApiClient(server.base_url)

    view = _run(client.get_profile_view(VIEWER_IDENTITY))

    assert view is not None


def test_get_settings_view_still_works_for_a_viewer_server_side(server):
    client = HttpApiClient(server.base_url)

    view = _run(client.get_settings_view(VIEWER_IDENTITY))

    assert view is not None


def test_get_job_result_still_works_for_a_viewer_server_side(server):
    client = HttpApiClient(server.base_url)
    event_id = _minimal_event(server.ctx.deps.persistence)

    result = _run(client.get_job_result(event_id, VIEWER_IDENTITY))

    assert result is None  # no outcome yet — but no auth error either


def test_write_protocol_is_refused_server_side_for_a_viewer(tmp_path, writable_profile_module):
    ctx = build_context(tmp_path, module_path=writable_profile_module)
    ctx.deps.persistence.write_user(BOT_SERVICE_IDENTITY, "commander")
    with RunningApiServer(ctx) as running:
        client = HttpApiClient(running.base_url)

        with pytest.raises(ApiRequestError) as excinfo:
            _run(client.write_protocol("add", {"name": "x"}, VIEWER_IDENTITY))

        assert excinfo.value.status_code == 403


def test_write_setting_is_refused_server_side_for_a_viewer(server):
    client = HttpApiClient(server.base_url)

    with pytest.raises(ApiRequestError) as excinfo:
        _run(client.write_setting("retry_count", 1, VIEWER_IDENTITY))

    assert excinfo.value.status_code == 403


# -- get_job_result -----------------------------------------------------


def test_get_job_result_unknown_pending_and_finished(server):
    client = HttpApiClient(server.base_url)

    assert _run(client.get_job_result("does-not-exist", COMMANDER_IDENTITY)) is None

    event_id = _minimal_event(server.ctx.deps.persistence)
    assert _run(client.get_job_result(event_id, COMMANDER_IDENTITY)) is None  # no outcome recorded yet

    record_event_outcome(server.ctx.deps.persistence, event_id, "succeeded", insight_text="ok")
    result = _run(client.get_job_result(event_id, COMMANDER_IDENTITY))
    assert result.outcome == "succeeded"
    assert result.insight_text == "ok"


# -- poll_pending_notifications -----------------------------------------


def test_poll_pending_notifications_full_round_trip(server):
    client = HttpApiClient(server.base_url)

    empty, cursor0 = _run(client.poll_pending_notifications(0))
    assert empty == ()

    event_id = _minimal_event(server.ctx.deps.persistence, sender_identity="alice")
    record_event_outcome(server.ctx.deps.persistence, event_id, "succeeded", insight_text="ok")

    notifications, cursor1 = _run(client.poll_pending_notifications(cursor0))
    assert len(notifications) == 1
    assert notifications[0].kind == "job_finished"
    assert notifications[0].target_chat_ids == ("alice",)
    assert notifications[0].payload.outcome == "succeeded"

    # No redelivery.
    again, cursor2 = _run(client.poll_pending_notifications(cursor1))
    assert again == ()
    assert cursor2 == cursor1


def test_reply_to_message_id_survives_the_full_real_path_from_submit_message_to_the_notification(tmp_path):
    # Problem 2's fix, exercised end to end through the real HTTP client on
    # both ends: submit_message (the one place the original Telegram
    # message ID is ever supplied) all the way through to
    # poll_pending_notifications (the one place it must reappear) — a
    # deliberately distinctive value, not a coincidental match, and
    # asserted on the exact value, not just "some ID was present."
    ctx = build_context(tmp_path, main_agent=happy_path_agent(intent="report"))
    ctx.deps.persistence.write_user(BOT_SERVICE_IDENTITY, "commander")
    with RunningApiServer(ctx) as running:
        client = HttpApiClient(running.base_url)

        submission = _run(client.submit_message("smoke seen near gate 3", VIEWER_IDENTITY, "telegram-msg-77341"))
        event_id = submission.job_id
        assert event_id is not None

        record_event_outcome(ctx.deps.persistence, event_id, "succeeded", insight_text="handled")

        notifications, _cursor = _run(client.poll_pending_notifications(0))
        [notification] = [n for n in notifications if n.kind == "job_finished"]

        assert notification.reply_to_message_id == "telegram-msg-77341"
