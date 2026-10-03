"""9.6 — Test ingestion parity.

`tests/test_api_unified_ingestion.py` ('s own convergence proof)
already confirms `POST /Event` and `POST /Msg` converge at the API layer.
This file's own, non-redundant scope per this subtask's refined text:
proving the bot's own real code path converges too — through
`bot.app.handle_incoming_message` calling a real
`bot.http_api_client.HttpApiClient` against a real running API, not a
second direct `POST /Msg` call.
"""

import asyncio
import types

import pytest

from agents import adapter
from bot.api_client import BOT_SERVICE_IDENTITY
from bot.deps import BotDeps
from bot.app import handle_incoming_message
from bot.http_api_client import HttpApiClient
from tests.api_fakes import VIEWER_IDENTITY, RunningApiServer, build_context, happy_path_agent
from tools.simulator import _post_event
from tests.crewai_fakes import install_crewai_stub


def _run(coro):
    """Run."""
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _mock_crewai(monkeypatch):
    """Mock crewai."""
    install_crewai_stub(monkeypatch)


class _FakeTelegramClient:
    """FakeTelegramClient."""
    async def send_text(self, chat_id, text):
        """Send text."""
        pass


def _agent():
    """Agent."""
    return happy_path_agent(risk_score="0.1", selected="status_check", intent="report")


def test_the_real_bot_path_converges_with_a_sensor_submission(tmp_path):
    """The real bot path converges with a sensor submission."""
    (tmp_path / "sensor").mkdir()
    (tmp_path / "bot").mkdir()

    sensor_ctx = build_context(tmp_path / "sensor", main_agent=_agent())
    bot_ctx = build_context(tmp_path / "bot", main_agent=_agent())
    bot_ctx.deps.persistence.write_user(BOT_SERVICE_IDENTITY, "commander")

    same_text = "smoke at gate 3"

    with RunningApiServer(sensor_ctx) as sensor_server:
        sensor_result = _post_event(sensor_server.base_url, "sensor-1", same_text)
        assert sensor_result["status_code"] == 202
        sensor_ctx.queue.wait_until_idle()
        # Read now, while the server (and its persistence) is still open —
        # RunningApiServer.close(), invoked by __exit__ below, stops the
        # writer thread; reads still work afterward against the same file,
        # but there is no need to rely on that here.
        via_sensor = sensor_ctx.deps.persistence.fetch_events_range("2000-01-01T00:00:00", "2100-01-01T00:00:00")[0]

    with RunningApiServer(bot_ctx) as bot_server:
        bot_deps = BotDeps(loaded_profile=None, telegram_client=_FakeTelegramClient(), api_client=HttpApiClient(bot_server.base_url))
        reply = _run(handle_incoming_message(bot_deps, VIEWER_IDENTITY, same_text, "12345"))
        # The friendly, default (non-DEEP_DEBUG) queued-report ack — messages/en.py's
        # "Api.queued_report" (server-side,) — confirms this
        # became a queued job, same as before; the raw task ID is no longer in the
        # default reply — the bot now purely relays whatever /Msg's own "answer" says.
        assert "Handling it" in reply
        bot_ctx.queue.wait_until_idle()
        via_bot = bot_ctx.deps.persistence.fetch_events_range("2000-01-01T00:00:00", "2100-01-01T00:00:00")[0]

    for field in ("classification", "area", "description", "severity", "risk_level", "selected_protocol", "outcome"):
        assert via_sensor[field] == via_bot[field], f"{field} diverged: {via_sensor[field]!r} != {via_bot[field]!r}"

    # The only two differences §7.5 permits.
    assert via_sensor["source"] == "sensor"
    assert via_bot["source"] == "telegram"
    assert via_sensor["occurred_at"] == via_sensor["received_at"]
    assert via_bot["occurred_at"] == "2026-08-20T09:00:00"  # extracted from happy_path_agent's scripted response

    # The bot path also carries what the API-only convergence test can't:
    # the real message ID, threaded all the way through.
    assert via_bot["source_message_id"] == "12345"
    assert via_sensor["source_message_id"] is None
