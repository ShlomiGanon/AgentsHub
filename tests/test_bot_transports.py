"""Bot transport contracts and concrete HTTP and Telegram clients."""

import asyncio

import pytest

from bot.api_client import BotApiClient, UnimplementedApiClient
from bot.startup import ApiNotImplementedError

unimplemented_client = UnimplementedApiClient()


def _run(coro):
    return asyncio.run(coro)


_DUMMY_ARGS = {
    "resolve_user": ("u1",),
    "list_commander_chat_ids": (),
    "submit_message": ("text", "u1", "m1"),
    "answer_clarification_hold": ("h1", "fire", "u1"),
    "answer_approval_hold": ("h1", "approved", "u1"),
    "get_profile_view": ("u1",),
    "get_profile_diff_status": (),
    "write_protocol": ("add", {}, "u1"),
    "get_settings_view": ("u1",),
    "write_setting": ("retry_count", 3, "u1"),
    "get_job_result": ("job1", "u1"),
    "poll_pending_notifications": (0,),
}


def test_every_abstract_method_has_a_dummy_args_entry():
    abstract_names = {name for name in dir(BotApiClient) if getattr(getattr(BotApiClient, name), "__isabstractmethod__", False)}
    assert abstract_names == set(_DUMMY_ARGS)


@pytest.mark.parametrize("method_name", sorted(_DUMMY_ARGS))
def test_unimplemented_client_raises_naming_the_blocked_subtask(method_name):
    method = getattr(unimplemented_client, method_name)
    args = _DUMMY_ARGS[method_name]

    with pytest.raises(ApiNotImplementedError) as excinfo:
        _run(method(*args))

    assert excinfo.value.operation == method_name
    assert "§7" in excinfo.value.blocked_on
    assert method_name in str(excinfo.value)


def test_error_is_also_a_not_implemented_error():
    with pytest.raises(NotImplementedError):
        _run(unimplemented_client.resolve_user("anyone"))


def test_cannot_construct_bot_api_client_directly():
    with pytest.raises(TypeError):
        BotApiClient()


import asyncio
import sys
import types
import uuid

import pytest

from agents import adapter
from bot.api_client import BOT_SERVICE_IDENTITY
from bot.startup import ApiRequestError
from bot.http_api_client import HttpApiClient
from history.write import record_event_outcome
from orchestrator.holds import create_approval_hold, create_clarification_hold
from orchestrator.main_agent import RiskAssessment
from orchestrator.main_agent import ProtocolSelectionResult
from tests.api_fakes import COMMANDER_IDENTITY, VIEWER_IDENTITY, RunningApiServer, build_context, happy_path_agent

_PROFILE_TEMPLATE = """
from protocols.model import Protocol, CriticalityLevel

PROFILE_NAME = "For Tests"
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


def test_submit_message_conversational_preserves_the_direct_answer(tmp_path):
    agent = happy_path_agent(intent="conversational")
    agent._dispatch["Reply naturally and directly"] = "Hello!"
    ctx = build_context(tmp_path, main_agent=agent)
    ctx.deps.persistence.write_user(BOT_SERVICE_IDENTITY, "commander")
    with RunningApiServer(ctx) as running:
        client = HttpApiClient(running.base_url)

        result = _run(client.submit_message("hello", VIEWER_IDENTITY, "m1"))

        assert result.kind == "conversational"
        assert result.answer_text == "Hello!"
        assert result.job_id is None


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


def test_answer_approval_hold_unauthorized(server):
    # §9.18's own permission matrix — the one action-cell no existing
    # HttpApiClient test covered: a viewer, over real HTTP, may not
    # approve_run.
    client = HttpApiClient(server.base_url)
    selection = ProtocolSelectionResult(status="selected", protocol_name="dispatch_response", reason="matched")
    risk = RiskAssessment(level="high", score=0.9, reason="r")
    event_id = _minimal_event(server.ctx.deps.persistence)
    create_approval_hold(server.ctx.deps.persistence, event_id, "flagged_protocol", selection, risk)

    outcome = _run(client.answer_approval_hold(event_id, "approved", VIEWER_IDENTITY))

    assert outcome.status == "unauthorized"


def test_an_unregistered_identity_is_refused_across_the_matrix(server):
    # §9.18's third row: an unregistered identity is refused the same way
    # regardless of which real HTTP status this method's own mapping
    # translates it into — HoldAnswerOutcome.status="unauthorized" for
    # answer_approval_hold (HoldAnswerStatus already has a slot for it,
    # per docs/api_spec.md's own mapping table — no raise here,
    # deliberately), a raised ApiRequestError for get_profile_view (no
    # DTO slot exists for an auth failure there). Both real HTTP calls
    # return 401 underneath either way — confirmed distinctly from a
    # registered-but-insufficient (viewer) identity in the tests above.
    client = HttpApiClient(server.base_url)
    selection = ProtocolSelectionResult(status="selected", protocol_name="dispatch_response", reason="matched")
    risk = RiskAssessment(level="high", score=0.9, reason="r")
    event_id = _minimal_event(server.ctx.deps.persistence)
    create_approval_hold(server.ctx.deps.persistence, event_id, "flagged_protocol", selection, risk)

    outcome = _run(client.answer_approval_hold(event_id, "approved", "nobody-registered"))
    assert outcome.status == "unauthorized"

    with pytest.raises(ApiRequestError) as excinfo:
        _run(client.get_profile_view("nobody-registered"))
    assert excinfo.value.status_code == 401


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
    # protocols are commander-only (view_system_internals) — a commander
    # caller is required to see them via the profile view.
    client = HttpApiClient(server.base_url)

    view = _run(client.get_profile_view(COMMANDER_IDENTITY))

    assert view.profile_name == server.ctx.loaded_profile.module_path
    names = [p.name for p in view.protocols]
    assert "status_check" in names


def test_get_profile_view_omits_protocols_for_a_viewer(server):
    # docs/Next_Plan.md §5 decision record: view_system_internals is
    # commander-only — a viewer's ProfileView carries no protocols/agents.
    client = HttpApiClient(server.base_url)

    view = _run(client.get_profile_view(VIEWER_IDENTITY))

    assert view.profile_name == server.ctx.loaded_profile.module_path
    assert view.protocols == ()
    assert view.agent_names == ()


def test_get_profile_diff_status_reports_false_when_unchanged(server):
    client = HttpApiClient(server.base_url)

    status = _run(client.get_profile_diff_status())

    assert status is False


def test_get_and_write_settings_round_trip(server):
    # settings are commander-only (view_settings) — use a commander caller.
    client = HttpApiClient(server.base_url)

    before = _run(client.get_settings_view(COMMANDER_IDENTITY))
    assert before.retry_count == 3

    result = _run(client.write_setting("retry_count", 9, COMMANDER_IDENTITY))
    assert result.accepted is True

    after = _run(client.get_settings_view(COMMANDER_IDENTITY))
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


def test_get_settings_view_denied_for_a_viewer_server_side(server):
    # docs/Next_Plan.md §5 decision record: view_settings is commander-only.
    client = HttpApiClient(server.base_url)

    with pytest.raises(ApiRequestError) as excinfo:
        _run(client.get_settings_view(VIEWER_IDENTITY))

    assert excinfo.value.status_code == 403


def test_get_settings_view_still_works_for_a_commander_server_side(server):
    client = HttpApiClient(server.base_url)

    view = _run(client.get_settings_view(COMMANDER_IDENTITY))

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


import asyncio
from unittest.mock import AsyncMock

import pytest
import telegram.error

from bot.telegram_client import PTBTelegramClient


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def client():
    return PTBTelegramClient("123456:fake-token-for-testing")


def test_validate_token_true_when_telegram_accepts_it(client, monkeypatch):
    monkeypatch.setattr(type(client._application.bot), "get_me", AsyncMock(return_value=object()))
    assert _run(client.validate_token()) is True


def test_validate_token_false_when_telegram_rejects_it(client, monkeypatch):
    monkeypatch.setattr(type(client._application.bot), "get_me", AsyncMock(side_effect=telegram.error.InvalidToken()))
    assert _run(client.validate_token()) is False


def test_send_text_sends_one_message_when_short(client, monkeypatch):
    send = AsyncMock()
    monkeypatch.setattr(type(client._application.bot), "send_message", send)

    _run(client.send_text("chat-1", "hello"))

    send.assert_awaited_once_with(chat_id="chat-1", text="hello")


def test_send_text_splits_long_text_into_multiple_messages(client, monkeypatch):
    send = AsyncMock()
    monkeypatch.setattr(type(client._application.bot), "send_message", send)

    long_text = "a" * 9000
    _run(client.send_text("chat-1", long_text))

    assert send.await_count == 3


def test_send_with_buttons_attaches_an_inline_keyboard(client, monkeypatch):
    send = AsyncMock()
    monkeypatch.setattr(type(client._application.bot), "send_message", send)

    _run(client.send_with_buttons("chat-1", "choose", [("fire", "clarify:h1:fire"), ("medical", "clarify:h1:medical")]))

    send.assert_awaited_once()
    _, kwargs = send.await_args
    assert kwargs["chat_id"] == "chat-1"
    assert kwargs["text"] == "choose"
    markup = kwargs["reply_markup"]
    labels = [button.text for row in markup.inline_keyboard for button in row]
    assert labels == ["fire", "medical"]


def test_send_reply_references_the_original_message(client, monkeypatch):
    send = AsyncMock()
    monkeypatch.setattr(type(client._application.bot), "send_message", send)

    _run(client.send_reply("chat-1", "here's your result", "msg-42"))

    send.assert_awaited_once_with(chat_id="chat-1", text="here's your result", reply_to_message_id="msg-42")


def test_answer_callback_query_acknowledges_the_button_press(client, monkeypatch):
    answer = AsyncMock()
    monkeypatch.setattr(type(client._application.bot), "answer_callback_query", answer)

    _run(client.answer_callback_query("cbq-1", text="got it"))

    answer.assert_awaited_once_with(callback_query_id="cbq-1", text="got it")


def test_run_polling_registers_handlers_then_polls(client, monkeypatch):
    calls = []
    monkeypatch.setattr(type(client._application), "run_polling", lambda self: calls.append("polled"))

    client.run_polling(lambda application: calls.append(("registered", application is client._application)))

    assert calls == [("registered", True), "polled"]
