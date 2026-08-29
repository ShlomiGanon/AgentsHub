import threading
import time

from api.app import build_app
from agents import AgentResult
from orchestrator.event_queue import PolicyAwareEventQueue, WorkItem
from persistence import open_persistence
from protocols import Step, execute_steps
from tests.api_fakes import VIEWER_IDENTITY, auth_headers, build_context


def _event(event_id: str, source_message_id: str | None = None) -> dict:
    return {
        "event_id": event_id,
        "received_at": "2026-08-28T10:00:00+00:00",
        "source": "telegram",
        "sender_identity": "viewer-1",
        "source_message_id": source_message_id,
        "occurred_at": "2026-08-28T10:00:00+00:00",
        "raw_text": "smoke reported",
    }


def test_conversation_history_is_isolated_bounded_and_chronological(tmp_path):
    store = open_persistence(str(tmp_path / "conversation.db"))
    try:
        for index in range(5):
            store.append_conversation_message(
                "chat-a", "user" if index % 2 == 0 else "assistant", f"a-{index}", ttl_hours=24, max_turns=2
            )
        store.append_conversation_message("chat-b", "user", "b-0", ttl_hours=24, max_turns=2)

        assert [row["content"] for row in store.fetch_conversation_messages("chat-a", 10)] == ["a-1", "a-2", "a-3", "a-4"]
        assert [row["content"] for row in store.fetch_conversation_messages("chat-b", 10)] == ["b-0"]
    finally:
        store.close()


def test_long_poll_wakes_after_notification_commit(tmp_path):
    store = open_persistence(str(tmp_path / "notifications.db"))
    try:
        event_id = store.append_event(_event("event-1"))

        def _finish():
            time.sleep(0.05)
            store.update_event(event_id, {"outcome": "succeeded"})

        worker = threading.Thread(target=_finish)
        worker.start()
        started = time.monotonic()
        rows = store.wait_for_notifications_since(0, 1.0)
        worker.join()

        assert rows[0]["event_id"] == event_id
        assert time.monotonic() - started < 0.8
    finally:
        store.close()


def test_duplicate_source_message_returns_the_existing_event(tmp_path):
    store = open_persistence(str(tmp_path / "duplicates.db"))
    try:
        first = store.append_event(_event("first", "telegram-message-7"))
        second = store.append_event(_event("second", "telegram-message-7"))

        assert second == first
        assert store.fetch_event("second") is None
    finally:
        store.close()


def test_policy_queue_preserves_same_resource_order_and_runs_to_idle():
    processed: list[str] = []
    event_queue = PolicyAwareEventQueue(processed.append, workers=2, max_size=10, reserved_continuation_percent=20)
    event_queue.start()
    event_queue.submit(WorkItem("first", concurrency_keys=("sender:a",)))
    event_queue.submit(WorkItem("second", concurrency_keys=("sender:a",)))
    event_queue.wait_until_idle()
    event_queue.stop()

    assert processed == ["first", "second"]


def test_policy_queue_reserves_capacity_for_continuations():
    event_queue = PolicyAwareEventQueue(lambda _item: None, workers=1, max_size=5, reserved_continuation_percent=20)
    normal = [event_queue.reserve(False) for _ in range(4)]

    assert all(reservation is not None for reservation in normal)
    assert event_queue.reserve(False) is None
    continuation = event_queue.reserve(True)
    assert continuation is not None
    assert event_queue.reserve(True) is None

    for reservation in [*normal, continuation]:
        event_queue.release_reservation(reservation)


def test_repeating_an_outcome_does_not_duplicate_the_notification(tmp_path):
    store = open_persistence(str(tmp_path / "outcome-idempotency.db"))
    try:
        event_id = store.append_event(_event("event-1"))
        store.update_event(event_id, {"outcome": "succeeded"})
        store.update_event(event_id, {"outcome": "succeeded"})

        rows = store.fetch_notifications_since(0)
        assert [(row["kind"], row["event_id"]) for row in rows] == [("job_finished", event_id)]
    finally:
        store.close()


def test_failed_dag_step_blocks_its_dependents_without_reordering_results():
    class _Settings:
        @staticmethod
        def get_retry_count():
            return 1

    class _Agent:
        name = "worker"

        @staticmethod
        def exposed_tools():
            return ()

        @staticmethod
        def process(text, _allowed_tools):
            return AgentResult("unclear_task" if text == "fail" else "success", text)

    steps = [
        Step("worker", "ok", (), step_id="first"),
        Step("worker", "fail", (), step_id="second", depends_on=("first",)),
        Step("worker", "must-not-run", (), step_id="third", depends_on=("second",)),
    ]
    result = execute_steps(steps, {"worker": _Agent()}, _Settings(), sleep_fn=lambda _seconds: None)

    assert result.completed is False
    assert [outcome.step.step_id for outcome in result.step_outcomes] == ["first", "second"]
    assert result.failed_step_index == 1


def test_step_waits_for_required_event_data_and_resumes_without_an_attempt():
    calls: list[str] = []

    class _Settings:
        @staticmethod
        def get_retry_count():
            return 1

    class _Agent:
        name = "worker"

        @staticmethod
        def exposed_tools():
            return ()

        @staticmethod
        def process(text, _allowed_tools):
            calls.append(text)
            return AgentResult("success", "done")

    step = Step("worker", "check the location", (), required_event_fields=("area",))
    waiting = execute_steps([step], {"worker": _Agent()}, _Settings(), event_data={"area": None})

    assert waiting.completed is False
    assert waiting.waiting_for_event_data is True
    assert waiting.missing_event_fields == ("area",)
    assert waiting.step_outcomes[0].attempt_count == 0
    assert waiting.step_outcomes[0].status == "waiting_for_event_data"
    assert calls == []

    resumed = execute_steps([step], {"worker": _Agent()}, _Settings(), event_data={"area": "south_sector"})

    assert resumed.completed is True
    assert calls == ["check the location"]


def test_wait_request_collects_all_missing_fields_from_the_remaining_plan():
    class _Settings:
        @staticmethod
        def get_retry_count():
            return 1

    class _Agent:
        name = "worker"

        @staticmethod
        def exposed_tools():
            return ()

        @staticmethod
        def process(_text, _allowed_tools):
            raise AssertionError("no step should run while its required data is missing")

    steps = [
        Step("worker", "locate", (), required_event_fields=("area",)),
        Step("worker", "assess", (), required_event_fields=("severity", "occurred_at")),
    ]

    result = execute_steps(
        steps,
        {"worker": _Agent()},
        _Settings(),
        event_data={"area": None, "severity": None, "occurred_at": None},
    )

    assert result.waiting_for_event_data is True
    assert result.missing_event_fields == ("area", "severity", "occurred_at")


def test_api_returns_and_accepts_trace_id(tmp_path):
    ctx = build_context(tmp_path)
    try:
        client = build_app(ctx).test_client()
        response = client.get("/SYSTEM", headers={**auth_headers(VIEWER_IDENTITY), "X-Trace-ID": "client-trace-42"})

        assert response.headers["X-Trace-ID"] == "client-trace-42"
    finally:
        ctx.queue.stop()
        ctx.scheduler.stop()
        ctx.deps.persistence.close()


def test_removed_streaming_endpoint_is_not_registered(tmp_path):
    ctx = build_context(tmp_path)
    try:
        response = build_app(ctx).test_client().post(
            "/Msg/Stream",
            headers=auth_headers(VIEWER_IDENTITY),
            json={"text": "hello", "sender_identity": VIEWER_IDENTITY},
        )
        assert response.status_code == 404
    finally:
        ctx.queue.stop()
        ctx.scheduler.stop()
        ctx.deps.persistence.close()
