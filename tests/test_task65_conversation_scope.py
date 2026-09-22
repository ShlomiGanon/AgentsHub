"""Task 65 — conversation history is isolated by trusted OperationalScope."""

import sqlite3

from persistence import OperationalScope, operational_scope_context, scoped_conversation_id
from persistence.sqlite_store import SQLitePersistence


LIVE = OperationalScope.live()
SEC_A = OperationalScope.simulation("SEC_001_PHASE_1", "run-a")
SEC_B = OperationalScope.simulation("SEC_001_PHASE_1", "run-b")
FIRE = OperationalScope.simulation("FIRE_002_PHASE_1", "run-fire")


def _append(store, conversation_id, role, content, scope):
    store.append_conversation_message(
        conversation_id,
        role,
        content,
        ttl_hours=24,
        max_turns=6,
        scope=scope,
    )


def test_same_transport_chat_id_gets_distinct_canonical_simulation_conversations(tmp_path):
    store = SQLitePersistence(str(tmp_path / "history.db"))
    transport_id = "telegram:-9000000000000000:main"
    run_a_id = scoped_conversation_id(transport_id, SEC_A)
    run_b_id = scoped_conversation_id(transport_id, SEC_B)

    _append(store, run_a_id, "user", "Run A question", SEC_A)
    _append(store, run_a_id, "assistant", "Run A answer", SEC_A)
    _append(store, run_b_id, "user", "Run B question", SEC_B)
    _append(store, run_b_id, "assistant", "Run B answer", SEC_B)

    assert run_a_id != run_b_id
    assert [row["content"] for row in store.fetch_conversation_messages(run_a_id, 12, scope=SEC_A)] == [
        "Run A question", "Run A answer"
    ]
    assert [row["content"] for row in store.fetch_conversation_messages(run_b_id, 12, scope=SEC_B)] == [
        "Run B question", "Run B answer"
    ]
    assert store.fetch_conversation_messages(run_b_id, 12, scope=SEC_A) == []
    store.close()


def test_simulation_does_not_read_live_or_legacy_unscoped_history(tmp_path):
    path = tmp_path / "history.db"
    store = SQLitePersistence(str(path))
    transport_id = "telegram:-9000000000000000:main"
    _append(store, transport_id, "user", "LIVE question", LIVE)

    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE conversation_messages SET scope_key = NULL WHERE conversation_id = ?",
            (transport_id,),
        )
        connection.commit()

    assert [row["content"] for row in store.fetch_conversation_messages(transport_id, 12, scope=LIVE)] == [
        "LIVE question"
    ]
    assert store.fetch_conversation_messages(transport_id, 12, scope=SEC_A) == []
    store.close()


def test_live_chat_continuity_and_separate_chats_within_one_run_remain_intact(tmp_path):
    store = SQLitePersistence(str(tmp_path / "history.db"))
    live_chat = "telegram:live-chat:main"
    chat_a = scoped_conversation_id("telegram:sim-chat-a:main", SEC_A)
    chat_b = scoped_conversation_id("telegram:sim-chat-b:main", SEC_A)

    _append(store, live_chat, "user", "live one", LIVE)
    _append(store, live_chat, "assistant", "live two", LIVE)
    _append(store, chat_a, "user", "chat A", SEC_A)
    _append(store, chat_b, "user", "chat B", SEC_A)

    assert [row["content"] for row in store.fetch_conversation_messages(live_chat, 12, scope=LIVE)] == [
        "live one", "live two"
    ]
    assert [row["content"] for row in store.fetch_conversation_messages(chat_a, 12, scope=SEC_A)] == ["chat A"]
    assert [row["content"] for row in store.fetch_conversation_messages(chat_b, 12, scope=SEC_A)] == ["chat B"]
    assert store.fetch_conversation_messages(chat_a, 12, scope=FIRE) == []
    store.close()


def test_scope_context_is_used_when_a_trusted_caller_omits_the_explicit_scope(tmp_path):
    store = SQLitePersistence(str(tmp_path / "history.db"))
    conversation_id = scoped_conversation_id("telegram:context-chat:main", SEC_A)

    with operational_scope_context(SEC_A):
        store.append_conversation_message(
            conversation_id, "user", "scoped context", ttl_hours=24, max_turns=6
        )

    with operational_scope_context(SEC_B):
        assert store.fetch_conversation_messages(conversation_id, 12) == []
    with operational_scope_context(SEC_A):
        assert [row["content"] for row in store.fetch_conversation_messages(conversation_id, 12)] == ["scoped context"]
    store.close()


def test_migration_27_adds_scope_metadata_without_deleting_legacy_rows(tmp_path):
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE conversation_messages ("
            "message_id INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id TEXT NOT NULL, "
            "role TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT NOT NULL, event_id TEXT)"
        )
        connection.execute(
            "INSERT INTO conversation_messages (conversation_id, role, content, created_at, event_id) "
            "VALUES (?, ?, ?, ?, ?)",
            ("legacy-chat", "user", "legacy content", "2026-09-20T10:00:00+00:00", None),
        )
        connection.execute("PRAGMA user_version = 26")
        connection.commit()

    store = SQLitePersistence(str(path))
    with sqlite3.connect(path) as connection:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        columns = {row[1] for row in connection.execute("PRAGMA table_info(conversation_messages)")}
        count = connection.execute("SELECT COUNT(*) FROM conversation_messages").fetchone()[0]

    assert version == 27
    assert "scope_key" in columns
    assert count == 1
    assert [row["content"] for row in store.fetch_conversation_messages("legacy-chat", 12, scope=LIVE)] == [
        "legacy content"
    ]
    assert store.fetch_conversation_messages("legacy-chat", 12, scope=SEC_A) == []
    store.close()

    reopened = SQLitePersistence(str(path))
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 27
        assert connection.execute("SELECT COUNT(*) FROM conversation_messages").fetchone()[0] == 1
    reopened.close()
