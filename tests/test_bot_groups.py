"""Telegram group support in the bot: binding cache, ignoring unbound groups, chat metadata on /Msg, attendance prompts and buttons."""

import asyncio
from types import SimpleNamespace

import pytest

from bot import app, background_services
from bot.api_client import ApiRequestError, AttendanceCheckResult, GroupBindingView, MessageSubmissionResult
from bot.deps import BotDeps
from messages import get_catalog
from tests.bot_fakes import FakeBotApiClient, FakeTelegramClient
from tests.test_bot_app import _fake_context, _fake_update

GROUP = "-100777"
BOUND = (GroupBindingView(chat_id=GROUP, agent_name="team_status_agent", label="readiness"),)


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _fresh_caches():
    app.clear_caller_cache()
    yield
    app.clear_caller_cache()


def _deps(api, telegram=None, profile=None):
    return BotDeps(loaded_profile=profile, telegram_client=telegram or FakeTelegramClient(), api_client=api)


# -- unregistered groups are ignored ------------------------------------------


def test_text_from_an_unbound_group_is_ignored_without_any_api_or_telegram_traffic():
    api = FakeBotApiClient(users={"42": "viewer"}, message_submission_result=MessageSubmissionResult(kind="question", answer_text="x"))
    telegram = FakeTelegramClient()
    deps = _deps(api, telegram)

    _run(app._on_text_message(_fake_update(chat_id=GROUP, chat_type="supergroup", text="hello"), _fake_context(deps)))

    assert api.calls == [("list_groups",)]
    assert telegram.sent == [] and telegram.status_events == []


def test_commands_and_callbacks_from_an_unbound_group_are_ignored():
    api = FakeBotApiClient(users={"42": "commander"})
    telegram = FakeTelegramClient()
    deps = _deps(api, telegram)

    _run(app._on_start_command(_fake_update(chat_id=GROUP, chat_type="group", text="/start"), _fake_context(deps)))
    _run(app._on_profile_command(_fake_update(chat_id=GROUP, chat_type="group", text="/profile"), _fake_context(deps)))
    _run(app._on_settings_command(_fake_update(chat_id=GROUP, chat_type="group", text="/settings"), _fake_context(deps)))
    _run(app._on_callback_query(_fake_update(chat_id=GROUP, chat_type="group", callback_data="attend:available"), _fake_context(deps)))

    assert [c for c in api.calls if c[0] != "list_groups"] == []
    assert telegram.sent == []
    # The callback itself is still acknowledged so Telegram's spinner stops.
    assert telegram.answered_callback_query_ids == ["cbq-1"]


def test_group_bindings_are_fetched_once_per_ttl_not_per_message():
    api = FakeBotApiClient(users={"42": "viewer"}, groups=BOUND, message_submission_result=MessageSubmissionResult(kind="question", answer_text="x"))
    deps = _deps(api)

    for _ in range(3):
        _run(app._on_text_message(_fake_update(chat_id=GROUP, chat_type="supergroup", text="hi"), _fake_context(deps)))

    assert api.calls.count(("list_groups",)) == 1
    assert len([c for c in api.calls if c[0] == "submit_message"]) == 3


def test_private_chats_never_consult_the_group_table():
    api = FakeBotApiClient(users={"42": "viewer"}, message_submission_result=MessageSubmissionResult(kind="question", answer_text="x"))
    deps = _deps(api)

    _run(app._on_text_message(_fake_update(text="hi"), _fake_context(deps)))

    assert ("list_groups",) not in api.calls


# -- chat metadata reaches /Msg -----------------------------------------------


def test_bound_group_message_carries_chat_id_and_type_to_the_api():
    api = FakeBotApiClient(users={"42": "viewer"}, groups=BOUND, message_submission_result=MessageSubmissionResult(kind="question", answer_text="ok"))
    telegram = FakeTelegramClient()
    deps = _deps(api, telegram)

    _run(app._on_text_message(_fake_update(chat_id=GROUP, chat_type="supergroup", text="who is available?"), _fake_context(deps)))

    assert ("submit_message", "who is available?", "42", "777") in api.calls
    assert ("submit_message_chat", GROUP, "supergroup") in api.calls
    assert ("submit_message_conversation", f"telegram:{GROUP}:main") in api.calls
    assert telegram.status_events[-1] == ("edit", GROUP, "1", "ok")


def test_private_chat_message_carries_private_chat_type():
    api = FakeBotApiClient(users={"42": "viewer"}, message_submission_result=MessageSubmissionResult(kind="question", answer_text="ok"))
    deps = _deps(api)

    _run(app._on_text_message(_fake_update(chat_id="99", text="hi"), _fake_context(deps)))

    assert ("submit_message_chat", "99", "private") in api.calls


def test_server_refusal_for_a_group_is_shown_as_a_refusal():
    class _RefusingApi(FakeBotApiClient):
        async def submit_message(self, *args, **kwargs):
            raise ApiRequestError(403, "group not registered")

    api = _RefusingApi(users={"42": "viewer"}, groups=BOUND)
    telegram = FakeTelegramClient()
    deps = _deps(api, telegram)

    _run(app._on_text_message(_fake_update(chat_id=GROUP, chat_type="supergroup", text="hi"), _fake_context(deps)))

    assert "group not registered" in telegram.status_events[-1][3]


# -- my_chat_member -----------------------------------------------------------


def _member_update(chat_id, chat_type, old_status, new_status):
    return SimpleNamespace(
        my_chat_member=SimpleNamespace(
            chat=SimpleNamespace(id=chat_id, type=chat_type),
            old_chat_member=SimpleNamespace(status=old_status),
            new_chat_member=SimpleNamespace(status=new_status),
        ),
        effective_chat=SimpleNamespace(id=chat_id, type=chat_type),
        effective_user=SimpleNamespace(id="1"),
    )


def test_bot_added_to_an_unbound_group_posts_the_chat_id_hint_once():
    api = FakeBotApiClient()
    telegram = FakeTelegramClient()
    deps = _deps(api, telegram)

    _run(app._on_my_chat_member(_member_update(GROUP, "supergroup", "left", "member"), _fake_context(deps)))

    assert len(telegram.sent) == 1
    assert telegram.sent[0].chat_id == GROUP
    assert GROUP in telegram.sent[0].text
    assert telegram.sent[0].text == get_catalog("en").text("bot.group_added_hint", chat_id=GROUP)


def test_bot_added_to_an_already_bound_group_stays_silent():
    api = FakeBotApiClient(groups=BOUND)
    telegram = FakeTelegramClient()
    deps = _deps(api, telegram)

    _run(app._on_my_chat_member(_member_update(GROUP, "supergroup", "left", "member"), _fake_context(deps)))

    assert telegram.sent == []


def test_leaving_a_group_or_private_chat_changes_post_nothing():
    api = FakeBotApiClient()
    telegram = FakeTelegramClient()
    deps = _deps(api, telegram)

    _run(app._on_my_chat_member(_member_update(GROUP, "supergroup", "member", "left"), _fake_context(deps)))
    _run(app._on_my_chat_member(_member_update(GROUP, "supergroup", "member", "administrator"), _fake_context(deps)))
    _run(app._on_my_chat_member(_member_update("42", "private", "kicked", "member"), _fake_context(deps)))

    assert telegram.sent == []
    assert api.calls == []


# -- attendance prompt loop ---------------------------------------------------


def _opened_result(targets=(GROUP,), members=("Alex Cohen", "Dana Levi")):
    return AttendanceCheckResult(
        opened=True, agent_name="team_status_agent", target_chat_ids=tuple(targets),
        cycle_key="2026-09-10", deadline_at="2026-09-10T06:00:00+00:00", members_required=tuple(members),
    )


def test_attendance_loop_posts_the_prompt_with_buttons_to_every_bound_group():
    api = FakeBotApiClient(attendance_check_result=_opened_result(targets=(GROUP, "-2")))
    telegram = FakeTelegramClient()
    deps = _deps(api, telegram, profile=SimpleNamespace(message_catalog=get_catalog("en"), timezone_name="Asia/Jerusalem"))

    _run(background_services.run_attendance_check_loop(deps, poll_interval_seconds=0, max_iterations=1))

    assert api.calls == [("run_attendance_check",)]
    assert [m.chat_id for m in telegram.sent] == [GROUP, "-2"]
    prompt = telegram.sent[0]
    assert "Alex Cohen" in prompt.text and "Dana Levi" in prompt.text
    assert "09:00" in prompt.text  # 06:00 UTC rendered in the deployment's timezone
    assert prompt.buttons == (
        ("Available for duty", "attend:available"),
        ("Unavailable", "attend:unavailable"),
    )


def test_attendance_loop_is_quiet_when_nothing_is_due():
    api = FakeBotApiClient(attendance_check_result=AttendanceCheckResult(opened=False, agent_name="team_status_agent"))
    telegram = FakeTelegramClient()
    deps = _deps(api, telegram)

    _run(background_services.run_attendance_check_loop(deps, poll_interval_seconds=0, max_iterations=2))

    assert api.calls == [("run_attendance_check",), ("run_attendance_check",)]
    assert telegram.sent == []


def test_attendance_loop_stops_when_the_deployment_has_no_attendance_agent():
    class _NoAgentApi(FakeBotApiClient):
        async def run_attendance_check(self):
            self.calls.append(("run_attendance_check",))
            raise ApiRequestError(404, "no attendance agent")

    api = _NoAgentApi()
    deps = _deps(api)

    _run(background_services.run_attendance_check_loop(deps, poll_interval_seconds=0, max_iterations=5))

    assert api.calls == [("run_attendance_check",)]


def test_attendance_prompt_without_members_uses_the_nobody_text():
    deps = _deps(FakeBotApiClient(), profile=SimpleNamespace(message_catalog=get_catalog("en")))

    text = background_services.format_attendance_prompt(deps, _opened_result(members=()))

    assert text == get_catalog("en").text("attendance.group_prompt_nobody")


# -- attendance buttons -------------------------------------------------------


def test_available_button_submits_the_attendance_report_for_the_pressing_member():
    api = FakeBotApiClient(users={"42": "viewer"}, groups=BOUND, message_submission_result=MessageSubmissionResult(kind="event_update", answer_text="stored"))
    telegram = FakeTelegramClient()
    deps = _deps(api, telegram)
    update = _fake_update(user_id="42", chat_id=GROUP, chat_type="supergroup", callback_data="attend:available", text="prompt", message_id="500")

    _run(app._on_callback_query(update, _fake_context(deps)))

    submit = next(c for c in api.calls if c[0] == "submit_message")
    assert submit[2] == "42"
    assert submit[3] == "500:42"
    assert "42" in submit[1]  # the availability report names the member
    assert ("submit_message_protocol_hint", "record_attendance_response") in api.calls
    assert ("submit_message_conversation", f"telegram:{GROUP}:attendance:42") in api.calls
    assert ("submit_message_chat", GROUP, "supergroup") in api.calls
    assert telegram.status_events[-1] == ("edit", GROUP, "1", "stored")


def test_unavailable_button_starts_a_per_member_follow_up_that_the_next_text_completes():
    api = FakeBotApiClient(users={"42": "viewer", "43": "viewer"}, groups=BOUND, message_submission_result=MessageSubmissionResult(kind="event_update", answer_text="stored"))
    telegram = FakeTelegramClient()
    deps = _deps(api, telegram)

    _run(app._on_callback_query(_fake_update(user_id="42", chat_id=GROUP, chat_type="supergroup", callback_data="attend:unavailable"), _fake_context(deps)))

    assert telegram.sent[-1].chat_id == GROUP
    assert telegram.sent[-1].text == get_catalog("en").text("bot.unavailability_prompt_group", name="42")
    assert (GROUP, "42") in app._PENDING_UNAVAILABILITY

    # Another member's text in the same group is a normal message, not 42's reason.
    _run(app._on_text_message(_fake_update(user_id="43", chat_id=GROUP, chat_type="supergroup", text="morning all"), _fake_context(deps)))
    assert ("submit_message", "morning all", "43", "777") in api.calls

    # 42's own reply completes the report.
    _run(app._on_text_message(_fake_update(user_id="42", chat_id=GROUP, chat_type="supergroup", text="ill for 2 days"), _fake_context(deps)))
    report = [c for c in api.calls if c[0] == "submit_message" and c[2] == "42"][-1]
    assert "ill for 2 days" in report[1] and "2" in report[1]
    assert ("submit_message_protocol_hint", "record_attendance_response") in api.calls
    assert (GROUP, "42") not in app._PENDING_UNAVAILABILITY


def test_unregistered_member_pressing_a_button_is_refused():
    api = FakeBotApiClient(users={}, groups=BOUND)
    telegram = FakeTelegramClient()
    deps = _deps(api, telegram)

    _run(app._on_callback_query(_fake_update(user_id="999", chat_id=GROUP, chat_type="supergroup", callback_data="attend:available"), _fake_context(deps)))

    assert [c for c in api.calls if c[0] == "submit_message"] == []
    assert telegram.sent[-1].chat_id == GROUP
