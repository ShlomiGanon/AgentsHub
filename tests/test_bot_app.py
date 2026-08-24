"""bot/app.py (work_plan.md §8.1 and the package's wiring)."""

import asyncio
from types import SimpleNamespace

import pytest

from bot import app
from bot.api_client import MessageSubmissionResult, ProfileDiffStatus, ProfileView, ProtocolWriteResult, SettingsView, SettingsWriteResult
from bot.deps import BotDeps
from bot.errors import ApiNotImplementedError, BotStartupError
from bot.singleton_lock import SingleInstanceLock
from tests.bot_fakes import FakeBotApiClient, FakeTelegramClient
from tests.helpers import write_profile_module

BOT_TOKEN_ENV = "TEST_APP_BOT_TOKEN"
MODEL_CRED_ENV = "TEST_APP_MODEL_KEY"


def _run(coro):
    return asyncio.run(coro)


def _write_and_load_test_profile(tmp_path, monkeypatch, module_name="test_app_profile"):
    monkeypatch.setenv(BOT_TOKEN_ENV, "the-real-token")
    monkeypatch.setenv(MODEL_CRED_ENV, "cred")
    write_profile_module(tmp_path, monkeypatch, module_name, bot_token_env=BOT_TOKEN_ENV, model_cred_env=MODEL_CRED_ENV)
    return module_name


# -- §8.1: startup, token resolution, token validation, single instance ----


def test_build_deps_resolves_the_already_loaded_token_and_port(tmp_path, monkeypatch):
    module_name = _write_and_load_test_profile(tmp_path, monkeypatch)

    deps = app.build_deps(module_name)

    assert deps.loaded_profile.api_port == 9999  # from tests/helpers.py's PROFILE_ATTR_LINES
    # The token was already resolved at profile-load time (§1.5) and never
    # re-read from the environment by bot.app itself.
    assert deps.telegram_client._application.bot.token == "the-real-token"


def test_build_deps_fails_loudly_naming_the_missing_env_var(tmp_path, monkeypatch):
    from profiles.loader import ProfileLoadError

    monkeypatch.delenv(BOT_TOKEN_ENV, raising=False)
    monkeypatch.setenv(MODEL_CRED_ENV, "cred")
    write_profile_module(tmp_path, monkeypatch, "test_app_profile_missing_token", bot_token_env=BOT_TOKEN_ENV, model_cred_env=MODEL_CRED_ENV)

    with pytest.raises(ProfileLoadError, match=BOT_TOKEN_ENV):
        app.build_deps("test_app_profile_missing_token")


def test_validate_bot_token_raises_when_telegram_rejects_it():
    deps = BotDeps(loaded_profile=None, telegram_client=FakeTelegramClient(token_is_valid=False), api_client=FakeBotApiClient())

    with pytest.raises(BotStartupError):
        _run(app._validate_bot_token(deps))


def test_validate_bot_token_passes_when_telegram_accepts_it():
    deps = BotDeps(loaded_profile=None, telegram_client=FakeTelegramClient(token_is_valid=True), api_client=FakeBotApiClient())
    _run(app._validate_bot_token(deps))  # must not raise


def test_a_second_bot_for_the_same_deployment_cannot_start(tmp_path, monkeypatch):
    lock_path = tmp_path / "deployment.db.bot.lock"
    first = SingleInstanceLock(lock_path)
    first.acquire()

    try:
        second = SingleInstanceLock(lock_path)
        with pytest.raises(BotStartupError):
            second.acquire()
    finally:
        first.release()


# -- Command routing and text parsing ---------------------------------------


def test_registered_commands_contain_no_user_management():
    for command in app.REGISTERED_COMMANDS:
        assert "user" not in command


@pytest.mark.parametrize(
    "rest,expect_error",
    [
        ("status_check | checks status | reference_agent | check_status | a status report | LOW | false", False),
        ("only two | fields", True),
        ("name | desc | agents | tools | output | crit | maybe", True),
    ],
)
def test_parse_protocol_write_command(rest, expect_error):
    result = app._parse_protocol_write_command(rest)
    assert isinstance(result, str) == expect_error


def test_parse_protocol_write_command_builds_the_expected_payload():
    rest = "status_check | checks status | reference_agent,other_agent | check_status | a status report | LOW | true"
    name, payload = app._parse_protocol_write_command(rest)

    assert name == "status_check"
    assert payload == {
        "name": "status_check",
        "description": "checks status",
        "participating_agents": ["reference_agent", "other_agent"],
        "approved_tools": ["check_status"],
        "expected_success_output": "a status report",
        "criticality": "LOW",
        "approval_flag": True,
    }


# -- Handler behavior, using lightweight fake Update/Context objects -------


class _FakeMessage:
    def __init__(self, text, message_id="777"):
        self.text = text
        self.message_id = message_id


def _fake_update(user_id="42", chat_id="99", text=None, callback_data=None, callback_query_id="cbq-1", message_id="777"):
    message = _FakeMessage(text, message_id) if text is not None else None
    callback_query = None
    if callback_data is not None:
        callback_query = SimpleNamespace(data=callback_data, id=callback_query_id)
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=user_id),
        effective_chat=SimpleNamespace(id=chat_id),
        message=message,
        callback_query=callback_query,
    )


def _fake_context(deps, args=None):
    return SimpleNamespace(bot_data={"deps": deps}, args=args or [])


def test_on_text_message_replies_in_the_same_chat():
    api = FakeBotApiClient(users={"42": "viewer"}, message_submission_result=MessageSubmissionResult(kind="question", answer_text="42 events"))
    telegram = FakeTelegramClient()
    deps = BotDeps(loaded_profile=None, telegram_client=telegram, api_client=api)

    update = _fake_update(text="how many events?")
    _run(app._on_text_message(update, _fake_context(deps)))

    assert telegram.sent[-1].chat_id == "99"
    assert telegram.sent[-1].text == "42 events"


def test_on_text_message_forwards_the_real_incoming_message_id():
    # Problem 2's fix: this is the one place the original Telegram
    # message's ID is available at all — lost here means lost for good,
    # long before any later async reply could reference it.
    api = FakeBotApiClient(users={"42": "viewer"}, message_submission_result=MessageSubmissionResult(kind="report", job_id="j1"))
    telegram = FakeTelegramClient()
    deps = BotDeps(loaded_profile=None, telegram_client=telegram, api_client=api)

    update = _fake_update(text="smoke seen", message_id="12345")
    _run(app._on_text_message(update, _fake_context(deps)))

    assert ("submit_message", "smoke seen", "42", "12345") in api.calls


def test_on_profile_command_view_replies_with_the_profile():
    api = FakeBotApiClient(users={"42": "viewer"}, profile_view=ProfileView(profile_name="demo", agent_names=(), protocols=(), event_types=(), areas=()))
    telegram = FakeTelegramClient()
    deps = BotDeps(loaded_profile=None, telegram_client=telegram, api_client=api)

    update = _fake_update()
    _run(app._on_profile_command(update, _fake_context(deps, args=["view"])))

    assert "demo" in telegram.sent[-1].text


def test_on_profile_command_diff_replies_with_diff_status():
    api = FakeBotApiClient(users={"42": "viewer"}, profile_diff_status=ProfileDiffStatus(differs_from_running=False))
    telegram = FakeTelegramClient()
    deps = BotDeps(loaded_profile=None, telegram_client=telegram, api_client=api)

    update = _fake_update()
    _run(app._on_profile_command(update, _fake_context(deps, args=["diff"])))

    assert "no restart is pending" in telegram.sent[-1].text.lower()


def test_on_callback_query_dispatches_to_clarification():
    from bot.api_client import HoldAnswerOutcome

    api = FakeBotApiClient(users={"42": "commander"}, clarification_answer_outcome=HoldAnswerOutcome(status="resolved"))
    telegram = FakeTelegramClient()
    deps = BotDeps(loaded_profile=None, telegram_client=telegram, api_client=api)

    update = _fake_update(callback_data="clarify:event-1:fire")
    _run(app._on_callback_query(update, _fake_context(deps)))

    assert telegram.answered_callback_query_ids == ["cbq-1"]
    assert api.calls[-1] == ("answer_clarification_hold", "event-1", "fire", "42")


def test_on_callback_query_dispatches_to_approval():
    from bot.api_client import HoldAnswerOutcome

    api = FakeBotApiClient(users={"42": "commander"}, approval_answer_outcome=HoldAnswerOutcome(status="approved"))
    telegram = FakeTelegramClient()
    deps = BotDeps(loaded_profile=None, telegram_client=telegram, api_client=api)

    update = _fake_update(callback_data="approve:event-1:approved")
    _run(app._on_callback_query(update, _fake_context(deps)))

    assert telegram.answered_callback_query_ids == ["cbq-1"]
    assert api.calls[-1] == ("answer_approval_hold", "event-1", "approved", "42")


# -- The same "isolated function had the check, the real entry point ------
# didn't" bug class the §8.2 profile/settings fix caught, checked again for
# the callback-query paths: handle_clarification_answer/handle_approval_answer
# are only ever reached, in production, through _on_callback_query — so
# these three cases go through the real handler, not the isolated functions.


def test_on_callback_query_refuses_a_viewer_answering_a_clarification():
    api = FakeBotApiClient(users={"42": "viewer"})
    telegram = FakeTelegramClient()
    deps = BotDeps(loaded_profile=None, telegram_client=telegram, api_client=api)

    update = _fake_update(callback_data="clarify:event-1:fire")
    _run(app._on_callback_query(update, _fake_context(deps)))

    assert "resolve_hold" in telegram.sent[-1].text
    assert not any(call[0] == "answer_clarification_hold" for call in api.calls)


def test_on_callback_query_refuses_a_viewer_answering_an_approval():
    api = FakeBotApiClient(users={"42": "viewer"})
    telegram = FakeTelegramClient()
    deps = BotDeps(loaded_profile=None, telegram_client=telegram, api_client=api)

    update = _fake_update(callback_data="approve:event-1:approved")
    _run(app._on_callback_query(update, _fake_context(deps)))

    assert "approve_run" in telegram.sent[-1].text
    assert not any(call[0] == "answer_approval_hold" for call in api.calls)


def test_on_callback_query_second_commander_answering_a_resolved_clarification_is_told_who_resolved_it():
    from bot.api_client import HoldAnswerOutcome

    api = FakeBotApiClient(
        users={"42": "commander"},
        clarification_answer_outcome=HoldAnswerOutcome(status="not_found", resolved_by="c1", message="already resolved"),
    )
    telegram = FakeTelegramClient()
    deps = BotDeps(loaded_profile=None, telegram_client=telegram, api_client=api)

    update = _fake_update(callback_data="clarify:event-1:fire")
    _run(app._on_callback_query(update, _fake_context(deps)))

    assert "already resolved by c1" in telegram.sent[-1].text.lower()


def test_on_callback_query_second_commander_answering_an_answered_approval_is_told_who_answered_it():
    from bot.api_client import HoldAnswerOutcome

    api = FakeBotApiClient(
        users={"42": "commander"},
        approval_answer_outcome=HoldAnswerOutcome(status="not_found", resolved_by="c1", message="already answered"),
    )
    telegram = FakeTelegramClient()
    deps = BotDeps(loaded_profile=None, telegram_client=telegram, api_client=api)

    update = _fake_update(callback_data="approve:event-1:approved")
    _run(app._on_callback_query(update, _fake_context(deps)))

    assert "already answered by c1" in telegram.sent[-1].text.lower()


def test_on_callback_query_with_an_unrecognized_namespace_does_nothing_but_answer_the_query():
    api = FakeBotApiClient()
    telegram = FakeTelegramClient()
    deps = BotDeps(loaded_profile=None, telegram_client=telegram, api_client=api)

    update = _fake_update(callback_data="bogus:event-1:choice")
    _run(app._on_callback_query(update, _fake_context(deps)))

    assert telegram.answered_callback_query_ids == ["cbq-1"]
    assert telegram.sent == []
    assert api.calls == []


def test_on_callback_query_with_malformed_data_in_a_known_namespace_is_reported_gracefully_not_crashed():
    # "clarify:only-one-field" has no second colon, so parse_callback_data's
    # unpack fails — going through the real _guarded(_on_callback_query)
    # composition (what register_handlers actually wires up) to confirm
    # the malformed input becomes a chat reply, not a crash.
    api = FakeBotApiClient()
    telegram = FakeTelegramClient()
    deps = BotDeps(loaded_profile=None, telegram_client=telegram, api_client=api)

    update = _fake_update(callback_data="clarify:only-one-field")
    _run(app._guarded(app._on_callback_query)(update, _fake_context(deps)))

    assert telegram.answered_callback_query_ids == ["cbq-1"]
    assert "went wrong" in telegram.sent[-1].text.lower()


def test_guarded_handler_reports_a_not_implemented_dependency_without_crashing():
    telegram = FakeTelegramClient()
    deps = BotDeps(loaded_profile=None, telegram_client=telegram, api_client=FakeBotApiClient())

    async def _boom(update, context):
        raise ApiNotImplementedError("some_operation", "§7.9")

    update = _fake_update()
    _run(app._guarded(_boom)(update, _fake_context(deps)))

    assert "isn't available yet" in telegram.sent[-1].text
    assert "some_operation" in telegram.sent[-1].text


def test_guarded_handler_reports_an_unexpected_error_without_leaking_it():
    telegram = FakeTelegramClient()
    deps = BotDeps(loaded_profile=None, telegram_client=telegram, api_client=FakeBotApiClient())

    async def _boom(update, context):
        raise ValueError("some internal detail")

    update = _fake_update()
    _run(app._guarded(_boom)(update, _fake_context(deps)))

    assert "some internal detail" not in telegram.sent[-1].text
    assert "went wrong" in telegram.sent[-1].text


# -- §8.2's "every interaction" against the real command handlers, not the ---
# internal view_profile/profile_diff_status/get_settings_view functions in
# isolation (those can't see this bug by construction — see docs/work_plan.md
# §8.2's note on this).


@pytest.mark.parametrize(
    "command_args",
    [["view"], ["diff"], []],
)
def test_an_unregistered_identity_cannot_read_the_profile_through_the_real_handler(command_args):
    api = FakeBotApiClient()  # zero registered users
    telegram = FakeTelegramClient()
    deps = BotDeps(loaded_profile=None, telegram_client=telegram, api_client=api)

    update = _fake_update()
    _run(app._on_profile_command(update, _fake_context(deps, args=command_args)))

    assert "not a registered user" in telegram.sent[-1].text
    assert api.calls == [("resolve_user", "42")]  # refused before any read/write call was attempted


def test_an_unregistered_identity_cannot_read_settings_through_the_real_handler():
    api = FakeBotApiClient()  # zero registered users
    telegram = FakeTelegramClient()
    deps = BotDeps(loaded_profile=None, telegram_client=telegram, api_client=api)

    update = _fake_update()
    _run(app._on_settings_command(update, _fake_context(deps, args=["view"])))

    assert "not a registered user" in telegram.sent[-1].text
    assert api.calls == [("resolve_user", "42")]


def test_a_registered_viewer_can_read_the_profile_through_the_real_handler():
    api = FakeBotApiClient(users={"42": "viewer"}, profile_view=ProfileView(profile_name="demo", agent_names=(), protocols=(), event_types=(), areas=()))
    telegram = FakeTelegramClient()
    deps = BotDeps(loaded_profile=None, telegram_client=telegram, api_client=api)

    update = _fake_update()
    _run(app._on_profile_command(update, _fake_context(deps, args=["view"])))

    assert "demo" in telegram.sent[-1].text


def test_a_registered_viewer_can_read_settings_through_the_real_handler():
    api = FakeBotApiClient(users={"42": "viewer"}, settings_view=SettingsView(retry_count=3, risk_threshold=0.5, lookback_window_days=30))
    telegram = FakeTelegramClient()
    deps = BotDeps(loaded_profile=None, telegram_client=telegram, api_client=api)

    update = _fake_update()
    _run(app._on_settings_command(update, _fake_context(deps, args=["view"])))

    assert "3" in telegram.sent[-1].text


# -- Problem 1 (post-Mission-8 audit): the real Telegram caller's own -------
# identity, not the bot's blanket service identity, must reach the API
# client for these — confirmed through the real command handler, the same
# way the §8.2 unauthenticated-read fix was confirmed, not through
# bot.profile_commands/bot.settings_commands in isolation.


def test_profile_view_through_the_real_handler_forwards_the_real_callers_identity():
    api = FakeBotApiClient(users={"42": "viewer"}, profile_view=ProfileView(profile_name="demo", agent_names=(), protocols=(), event_types=(), areas=()))
    telegram = FakeTelegramClient()
    deps = BotDeps(loaded_profile=None, telegram_client=telegram, api_client=api)

    update = _fake_update()
    _run(app._on_profile_command(update, _fake_context(deps, args=["view"])))

    assert ("get_profile_view", "42") in api.calls


def test_settings_view_through_the_real_handler_forwards_the_real_callers_identity():
    api = FakeBotApiClient(users={"42": "viewer"}, settings_view=SettingsView(retry_count=3, risk_threshold=0.5, lookback_window_days=30))
    telegram = FakeTelegramClient()
    deps = BotDeps(loaded_profile=None, telegram_client=telegram, api_client=api)

    update = _fake_update()
    _run(app._on_settings_command(update, _fake_context(deps, args=["view"])))

    assert ("get_settings_view", "42") in api.calls


def test_settings_change_through_the_real_handler_forwards_the_real_callers_identity():
    api = FakeBotApiClient(users={"42": "commander"}, settings_write_result=SettingsWriteResult(accepted=True, message="ok"))
    telegram = FakeTelegramClient()
    deps = BotDeps(loaded_profile=None, telegram_client=telegram, api_client=api)

    update = _fake_update()
    _run(app._on_settings_command(update, _fake_context(deps, args=["set", "retry_count", "5"])))

    assert ("write_setting", "retry_count", 5, "42") in api.calls


def test_protocol_write_through_the_real_handler_forwards_the_real_callers_identity():
    api = FakeBotApiClient(users={"42": "commander"}, protocol_write_result=ProtocolWriteResult(accepted=True, message="ok"))
    telegram = FakeTelegramClient()
    deps = BotDeps(loaded_profile=None, telegram_client=telegram, api_client=api)

    update = _fake_update()
    _run(app._on_profile_command(update, _fake_context(deps, args=["remove", "status_check"])))

    assert ("write_protocol", "remove", {"name": "status_check"}, "42") in api.calls


def test_write_branches_still_refuse_an_unregistered_identity_no_regression():
    # The write branches already had this check before this fix — confirm
    # it still works unchanged, through the same real handler.
    api = FakeBotApiClient()
    telegram = FakeTelegramClient()
    deps = BotDeps(loaded_profile=None, telegram_client=telegram, api_client=api)

    update = _fake_update()
    _run(app._on_profile_command(update, _fake_context(deps, args=["add", "p", "|", "d", "|", "reference_agent", "|", "check_status", "|", "o", "|", "LOW", "|", "true"])))

    assert "not a registered user" in telegram.sent[-1].text
    assert api.calls == [("resolve_user", "42")]
