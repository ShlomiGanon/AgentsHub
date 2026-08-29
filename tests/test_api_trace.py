"""Commander-only deterministic Deep Debug trace feed."""

import config.base as base_config

from api.app import build_app
from tests.api_fakes import (
    COMMANDER_IDENTITY,
    VIEWER_IDENTITY,
    auth_headers,
    build_context,
)


def _close(ctx):
    ctx.queue.stop()
    ctx.scheduler.stop()
    ctx.deps.persistence.close()


def test_commander_receives_rendered_ordered_trace_without_raw_model_io(tmp_path, monkeypatch):
    monkeypatch.setattr(base_config, "DEEP_DEBUG", True)
    ctx = build_context(tmp_path)
    try:
        ctx.deps.persistence.write_log_entry(
            "trace-1",
            {"event": "intent_classified", "intent": "question", "reason": "private detail"},
        )
        ctx.deps.persistence.write_log_entry(
            "trace-1",
            {"event": "model_io", "prompt": "secret prompt", "response": "secret response"},
        )
        ctx.deps.persistence.write_log_entry(
            "trace-1",
            {
                "event": "provider_request_finished",
                "provider": "openai",
                "model": "openai/test",
                "latency_ms": 12.5,
                "total_tokens": 9,
            },
        )

        response = build_app(ctx).test_client().get(
            "/Trace/trace-1?since=0&wait_seconds=0",
            headers=auth_headers(COMMANDER_IDENTITY),
        )

        assert response.status_code == 200
        payload = response.get_json()
        assert [entry["id"] for entry in payload["entries"]] == sorted(
            entry["id"] for entry in payload["entries"]
        )
        combined = "\n".join(entry["text"] for entry in payload["entries"])
        assert "question" in combined
        assert "provider=openai, model=openai/test" in combined
        assert "secret prompt" not in combined
        assert "secret response" not in combined
        assert [entry["id"] for entry in payload["entries"]] == [1, 3]
        assert payload["next_cursor"] == 3
    finally:
        _close(ctx)


def test_viewer_cannot_read_live_trace(tmp_path, monkeypatch):
    monkeypatch.setattr(base_config, "DEEP_DEBUG", True)
    ctx = build_context(tmp_path)
    try:
        ctx.deps.persistence.write_log_entry("trace-2", {"event": "protocol_selection", "protocol_name": "secret"})
        response = build_app(ctx).test_client().get(
            "/Trace/trace-2",
            headers=auth_headers(VIEWER_IDENTITY),
        )
        assert response.status_code == 403
        assert "secret" not in response.get_data(as_text=True)
    finally:
        _close(ctx)


def test_trace_feed_is_hidden_when_deep_debug_is_off(tmp_path, monkeypatch):
    monkeypatch.setattr(base_config, "DEEP_DEBUG", False)
    ctx = build_context(tmp_path)
    try:
        response = build_app(ctx).test_client().get(
            "/Trace/trace-3",
            headers=auth_headers(COMMANDER_IDENTITY),
        )
        assert response.status_code == 404
    finally:
        _close(ctx)


def test_trace_cursor_is_exclusive(tmp_path, monkeypatch):
    monkeypatch.setattr(base_config, "DEEP_DEBUG", True)
    ctx = build_context(tmp_path)
    try:
        ctx.deps.persistence.write_log_entry("trace-4", {"event": "intent_classified", "intent": "question"})
        first = build_app(ctx).test_client().get(
            "/Trace/trace-4",
            headers=auth_headers(COMMANDER_IDENTITY),
        ).get_json()
        second = build_app(ctx).test_client().get(
            f"/Trace/trace-4?since={first['next_cursor']}",
            headers=auth_headers(COMMANDER_IDENTITY),
        ).get_json()
        assert second["entries"] == []
        assert second["next_cursor"] == first["next_cursor"]
    finally:
        _close(ctx)
