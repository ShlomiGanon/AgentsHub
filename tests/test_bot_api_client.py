"""bot/api_client.py (work_plan.md §8, the Mission-7 seam).

Confirms `UnimplementedApiClient` — the seam every other §8 module is
built against — never pretends to succeed: every operation raises
`ApiNotImplementedError`, naming the exact work_plan.md §7 subtask it is
blocked on. This is the one test that keeps the "current, complete list
of Mission-7 gaps" property true as the interface grows.
"""

import asyncio

import pytest

from bot.api_client import BotApiClient, UnimplementedApiClient
from bot.errors import ApiNotImplementedError

client = UnimplementedApiClient()


def _run(coro):
    return asyncio.run(coro)


_DUMMY_ARGS = {
    "resolve_user": ("u1",),
    "list_commander_chat_ids": (),
    "submit_message": ("text", "u1"),
    "answer_clarification_hold": ("h1", "fire", "u1"),
    "answer_approval_hold": ("h1", "approved", "u1"),
    "get_profile_view": (),
    "get_profile_diff_status": (),
    "write_protocol": ("add", {}),
    "get_settings_view": (),
    "write_setting": ("retry_count", 3),
    "get_job_result": ("job1",),
    "poll_pending_notifications": (),
}


def test_every_abstract_method_has_a_dummy_args_entry():
    abstract_names = {name for name in dir(BotApiClient) if getattr(getattr(BotApiClient, name), "__isabstractmethod__", False)}
    assert abstract_names == set(_DUMMY_ARGS)


@pytest.mark.parametrize("method_name", sorted(_DUMMY_ARGS))
def test_unimplemented_client_raises_naming_the_blocked_subtask(method_name):
    method = getattr(client, method_name)
    args = _DUMMY_ARGS[method_name]

    with pytest.raises(ApiNotImplementedError) as excinfo:
        _run(method(*args))

    assert excinfo.value.operation == method_name
    assert "§7" in excinfo.value.blocked_on
    assert method_name in str(excinfo.value)


def test_error_is_also_a_not_implemented_error():
    with pytest.raises(NotImplementedError):
        _run(client.resolve_user("anyone"))


def test_cannot_construct_bot_api_client_directly():
    with pytest.raises(TypeError):
        BotApiClient()
