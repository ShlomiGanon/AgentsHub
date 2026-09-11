import json

import pytest

from api.app import build_app
from config.live_settings import SettingsStore
from persistence.sqlite_store import SQLitePersistence
from tests.api_fakes import COMMANDER_IDENTITY, VIEWER_IDENTITY, auth_headers, build_context


BOT_SERVICE_KEY = "unsafe-system-test-service-key"


@pytest.fixture
def ctx(tmp_path):
    context = build_context(tmp_path)
    yield context
    context.queue.stop()
    context.deps.persistence.close()


def _enable_bot_service(ctx, monkeypatch):
    monkeypatch.setenv("BOT_SERVICE_KEY", BOT_SERVICE_KEY)
    ctx.deps.persistence.write_user("bot-service", "commander")


def _service_headers(identity="bot-service", chat_id=None, chat_type=None):
    headers = {"X-Identity": identity, "X-Service-Key": BOT_SERVICE_KEY}
    if chat_id is not None:
        headers["X-Telegram-Chat-ID"] = str(chat_id)
    if chat_type is not None:
        headers["X-Telegram-Chat-Type"] = chat_type
    return headers


def test_new_and_legacy_settings_default_to_open_mode(tmp_path):
    db_path = str(tmp_path / "deployment.db")
    new_store = SettingsStore(db_path, 3, 0.5, 30)
    assert new_store.get_safe_mode() is False

    settings_path = tmp_path / "legacy.db.settings.json"
    settings_path.write_text(
        json.dumps({"retry_count": 4, "risk_threshold": 0.6, "lookback_window_days": 20}),
        encoding="utf-8",
    )
    legacy_store = SettingsStore(str(tmp_path / "legacy.db"), 3, 0.5, 30)
    assert legacy_store.get_safe_mode() is False
    assert json.loads(settings_path.read_text(encoding="utf-8"))["safe_mode"] is False


def test_manual_and_automatic_records_keep_distinct_sources(tmp_path):
    store = SQLitePersistence(str(tmp_path / "records.db"))
    try:
        store.write_user("manual", "viewer", "Manual User")
        automatic = store.register_telegram_user_if_missing("automatic")
        assert store.read_user("manual")["auto_register"] is False
        assert automatic["permission_level"] == "viewer"
        assert automatic["full_name"] == ""
        assert automatic["auto_register"] is True
        assert "agent_name" not in automatic

        store.write_user("automatic", "commander", "Automatic User")
        assert store.read_user("automatic")["auto_register"] is True
        approved = store.approve_user("automatic")
        assert approved["auto_register"] is False
        assert approved["permission_level"] == "commander"

        group = store.register_telegram_group_if_missing("-1001", "New group")
        assert group["agent_name"] == "main_agent"
        assert group["auto_register"] is True
        store.write_group("-1001", "main_agent", "Edited")
        assert store.read_group("-1001")["auto_register"] is True
        assert store.approve_group("-1001")["auto_register"] is False
    finally:
        store.close()


def test_system_exposes_and_changes_safe_mode_only_for_commander(ctx):
    client = build_app(ctx).test_client()
    commander_view = client.get("/SYSTEM", headers=auth_headers(COMMANDER_IDENTITY))
    assert commander_view.status_code == 200
    assert commander_view.get_json()["settings"]["safe_mode"] is False

    viewer_view = client.get("/SYSTEM", headers=auth_headers(VIEWER_IDENTITY))
    assert viewer_view.status_code == 200
    assert "settings" not in viewer_view.get_json()

    assert client.put("/SYSTEM", headers=auth_headers(VIEWER_IDENTITY), json={"safe_mode": True}).status_code == 403
    changed = client.put("/SYSTEM", headers=auth_headers(COMMANDER_IDENTITY), json={"safe_mode": True})
    assert changed.status_code == 200
    assert changed.get_json()["safe_mode"] is True

    for invalid in (0, 1, "true", None, [], {}):
        response = client.put("/SYSTEM", headers=auth_headers(COMMANDER_IDENTITY), json={"safe_mode": invalid})
        assert response.status_code == 400
        assert response.get_json()["field"] == "safe_mode"
    assert ctx.deps.settings_store.get_safe_mode() is True


def test_invalid_multi_setting_request_is_not_partially_applied(ctx):
    client = build_app(ctx).test_client()
    response = client.put(
        "/SYSTEM",
        headers=auth_headers(COMMANDER_IDENTITY),
        json={"retry_count": 99, "safe_mode": "yes"},
    )
    assert response.status_code == 400
    assert ctx.deps.settings_store.get_retry_count() == 3
    assert ctx.deps.settings_store.get_safe_mode() is False


def test_open_mode_admission_atomically_registers_user_and_group(ctx, monkeypatch):
    _enable_bot_service(ctx, monkeypatch)
    client = build_app(ctx).test_client()
    response = client.post(
        "/Telegram/Admission",
        headers=_service_headers(),
        json={
            "telegram_identity": "7001",
            "chat_id": "-1007001",
            "chat_type": "supergroup",
            "chat_label": "Visitors",
        },
    )
    assert response.status_code == 200
    body = response.get_json()
    assert body["allowed"] is True
    assert body["reason"] == "auto_registered"
    assert body["user"] == {
        "telegram_identity": "7001",
        "permission_level": "viewer",
        "full_name": "",
        "auto_register": True,
    }
    assert body["group"]["agent_name"] == "main_agent"
    assert body["group"]["auto_register"] is True
    assert ctx.group_routing.get("-1007001").agent_name == "main_agent"


def test_safe_mode_blocks_unknown_and_automatic_entities_until_approved(ctx, monkeypatch):
    _enable_bot_service(ctx, monkeypatch)
    client = build_app(ctx).test_client()
    admission_payload = {
        "telegram_identity": "7002",
        "chat_id": "-1007002",
        "chat_type": "group",
        "chat_label": "Pending",
    }
    assert client.post("/Telegram/Admission", headers=_service_headers(), json=admission_payload).get_json()["allowed"]
    assert client.put("/SYSTEM", headers=auth_headers(COMMANDER_IDENTITY), json={"safe_mode": True}).status_code == 200

    blocked = client.post("/Telegram/Admission", headers=_service_headers(), json=admission_payload).get_json()
    assert blocked["allowed"] is False
    assert blocked["reason"] == "user_awaiting_approval"
    assert client.get(
        "/User/7002",
        headers=_service_headers("7002", "-1007002", "group"),
    ).status_code == 403

    assert client.post("/User/7002/approve", headers=auth_headers(VIEWER_IDENTITY)).status_code == 403
    assert client.post("/Groups/-1007002/approve", headers=auth_headers(VIEWER_IDENTITY)).status_code == 403
    assert client.post("/User/7002/approve", headers=auth_headers(COMMANDER_IDENTITY)).status_code == 200
    still_blocked = client.post("/Telegram/Admission", headers=_service_headers(), json=admission_payload).get_json()
    assert still_blocked["reason"] == "group_awaiting_approval"
    assert client.post("/Groups/-1007002/approve", headers=auth_headers(COMMANDER_IDENTITY)).status_code == 200
    assert client.post("/Telegram/Admission", headers=_service_headers(), json=admission_payload).get_json()["allowed"] is True


def test_safe_mode_does_not_create_unknown_records(ctx, monkeypatch):
    _enable_bot_service(ctx, monkeypatch)
    ctx.deps.settings_store.set_safe_mode(True)
    client = build_app(ctx).test_client()
    response = client.post(
        "/Telegram/Admission",
        headers=_service_headers(),
        json={"telegram_identity": "7999", "chat_id": "7999", "chat_type": "private"},
    )
    assert response.status_code == 200
    assert response.get_json()["allowed"] is False
    assert ctx.deps.persistence.read_user("7999") is None


def test_generic_api_never_auto_registers_an_unknown_identity(ctx):
    client = build_app(ctx).test_client()
    response = client.post(
        "/Msg",
        headers={"X-Identity": "8123"},
        json={"text": "hello", "sender_identity": "8123"},
    )
    assert response.status_code == 401
    assert ctx.deps.persistence.read_user("8123") is None


def test_admission_requires_the_real_bot_service_credentials(ctx, monkeypatch):
    _enable_bot_service(ctx, monkeypatch)
    client = build_app(ctx).test_client()
    payload = {"telegram_identity": "7333", "chat_id": "7333", "chat_type": "private"}
    assert client.post("/Telegram/Admission", headers={"X-Identity": "bot-service"}, json=payload).status_code == 401
    assert client.post("/Telegram/Admission", headers=auth_headers(COMMANDER_IDENTITY), json=payload).status_code == 403
    assert ctx.deps.persistence.read_user("7333") is None


def test_private_admission_has_no_user_agent_assignment(ctx, monkeypatch):
    _enable_bot_service(ctx, monkeypatch)
    client = build_app(ctx).test_client()
    body = client.post(
        "/Telegram/Admission",
        headers=_service_headers(),
        json={"telegram_identity": "7444", "chat_id": "7444", "chat_type": "private"},
    ).get_json()
    assert body["allowed"] is True
    assert "agent_name" not in body["user"]
    assert "agent_name" not in ctx.deps.persistence.read_user("7444")
