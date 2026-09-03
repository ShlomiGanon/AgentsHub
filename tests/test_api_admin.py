"""The admin web panel (api/admin.py): login, session, CSRF, rate limiting, user management."""

import types

import pytest

from agents import adapter
from api.admin import AdminConfigError, resolve_admin_config
from api.app import build_app
from tests.api_fakes import COMMANDER_IDENTITY, VIEWER_IDENTITY, build_context

ADMIN_USERNAME = "test-admin"
ADMIN_PASSWORD = "test-admin-password"
ADMIN_SESSION_SECRET = "test-admin-session-secret"


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

    fake_module = types.SimpleNamespace(Agent=_FakeCrewAgent, LLM=lambda **kwargs: kwargs["model"], tools=types.SimpleNamespace(BaseTool=object))
    monkeypatch.setattr(adapter, "_get_crewai", lambda: fake_module)


@pytest.fixture
def teardown_ctx():
    contexts = []
    yield contexts
    for ctx in contexts:
        ctx.queue.stop()
        ctx.deps.persistence.close()


@pytest.fixture
def _admin_env(monkeypatch):
    monkeypatch.setenv("ADMIN_USERNAME", ADMIN_USERNAME)
    monkeypatch.setenv("ADMIN_PASSWORD", ADMIN_PASSWORD)
    monkeypatch.setenv("ADMIN_SESSION_SECRET", ADMIN_SESSION_SECRET)
    monkeypatch.delenv("ADMIN_SESSION_TIMEOUT_MINUTES", raising=False)
    monkeypatch.delenv("ADMIN_LOGIN_MAX_ATTEMPTS", raising=False)
    monkeypatch.delenv("ADMIN_LOGIN_LOCKOUT_MINUTES", raising=False)


def _client(tmp_path, teardown_ctx, **kwargs):
    ctx = build_context(tmp_path, **kwargs)
    teardown_ctx.append(ctx)
    return build_app(ctx).test_client()


def _login(client, username=ADMIN_USERNAME, password=ADMIN_PASSWORD):
    return client.post("/admin/login", data={"username": username, "password": password}, follow_redirects=False)


# -- Enablement -------------------------------------------------------------


def test_admin_routes_do_not_exist_when_unconfigured(tmp_path, teardown_ctx, monkeypatch):
    monkeypatch.delenv("ADMIN_USERNAME", raising=False)
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    client = _client(tmp_path, teardown_ctx)

    resp = client.get("/admin/")

    assert resp.status_code == 404


@pytest.mark.parametrize("missing", ["ADMIN_USERNAME", "ADMIN_PASSWORD"])
def test_admin_stays_disabled_if_only_one_credential_is_set(tmp_path, teardown_ctx, monkeypatch, missing):
    monkeypatch.setenv("ADMIN_USERNAME", ADMIN_USERNAME)
    monkeypatch.setenv("ADMIN_PASSWORD", ADMIN_PASSWORD)
    monkeypatch.delenv(missing, raising=False)
    client = _client(tmp_path, teardown_ctx)

    assert client.get("/admin/").status_code == 404


def test_resolve_admin_config_requires_session_secret_when_enabled(monkeypatch):
    monkeypatch.setenv("ADMIN_USERNAME", ADMIN_USERNAME)
    monkeypatch.setenv("ADMIN_PASSWORD", ADMIN_PASSWORD)
    monkeypatch.delenv("ADMIN_SESSION_SECRET", raising=False)

    with pytest.raises(AdminConfigError, match="ADMIN_SESSION_SECRET"):
        resolve_admin_config()


def test_resolve_admin_config_returns_none_when_fully_unset(monkeypatch):
    monkeypatch.delenv("ADMIN_USERNAME", raising=False)
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)

    assert resolve_admin_config() is None


# -- Login / session ----------------------------------------------------


def test_login_page_renders_when_configured(tmp_path, teardown_ctx, _admin_env):
    client = _client(tmp_path, teardown_ctx)

    resp = client.get("/admin/login")

    assert resp.status_code == 200
    assert b"Username" in resp.data


def test_dashboard_redirects_to_login_when_not_authenticated(tmp_path, teardown_ctx, _admin_env):
    client = _client(tmp_path, teardown_ctx)

    resp = client.get("/admin/", follow_redirects=False)

    assert resp.status_code == 302
    assert "/admin/login" in resp.headers["Location"]


def test_correct_login_reaches_the_dashboard_and_lists_existing_users(tmp_path, teardown_ctx, _admin_env):
    client = _client(tmp_path, teardown_ctx)

    login_resp = _login(client)
    assert login_resp.status_code == 302
    assert "/admin/" in login_resp.headers["Location"]

    dashboard = client.get("/admin/")
    assert dashboard.status_code == 200
    assert COMMANDER_IDENTITY.encode() in dashboard.data
    assert VIEWER_IDENTITY.encode() in dashboard.data


def test_wrong_password_does_not_authenticate_and_gives_a_generic_message(tmp_path, teardown_ctx, _admin_env):
    client = _client(tmp_path, teardown_ctx)

    resp = _login(client, password="wrong")

    assert resp.status_code == 200  # re-renders the login page, no redirect
    assert b"Wrong username or password" in resp.data
    assert client.get("/admin/", follow_redirects=False).status_code == 302  # still not authenticated


def test_logout_clears_the_session(tmp_path, teardown_ctx, _admin_env):
    client = _client(tmp_path, teardown_ctx)
    _login(client)
    dashboard = client.get("/admin/")
    csrf_token = _extract_csrf(dashboard.data)

    client.post("/admin/logout", data={"csrf_token": csrf_token})

    assert client.get("/admin/", follow_redirects=False).status_code == 302


def test_session_expires_after_the_configured_inactivity_window(tmp_path, teardown_ctx, monkeypatch, _admin_env):
    monkeypatch.setenv("ADMIN_SESSION_TIMEOUT_MINUTES", "1")
    client = _client(tmp_path, teardown_ctx)
    _login(client)
    assert client.get("/admin/").status_code == 200

    import api.admin as admin_module

    real_time = admin_module.time.time
    monkeypatch.setattr(admin_module.time, "time", lambda: real_time() + 61)

    resp = client.get("/admin/", follow_redirects=False)
    assert resp.status_code == 302
    assert "/admin/login" in resp.headers["Location"]


# -- Rate limiting --------------------------------------------------------


def test_repeated_failed_logins_lock_out_further_attempts(tmp_path, teardown_ctx, monkeypatch, _admin_env):
    monkeypatch.setenv("ADMIN_LOGIN_MAX_ATTEMPTS", "3")
    monkeypatch.setenv("ADMIN_LOGIN_LOCKOUT_MINUTES", "15")
    client = _client(tmp_path, teardown_ctx)

    for _ in range(3):
        _login(client, password="wrong")

    # Even the *correct* password is now rejected — locked out, not just still-wrong-password.
    resp = _login(client)
    assert resp.status_code == 200
    assert b"Too many failed attempts" in resp.data
    assert client.get("/admin/", follow_redirects=False).status_code == 302


# -- CSRF -----------------------------------------------------------------


def _extract_csrf(html_bytes: bytes) -> str:
    html = html_bytes.decode()
    marker = 'name="csrf_token" value="'
    start = html.index(marker) + len(marker)
    end = html.index('"', start)
    return html[start:end]


def test_write_user_without_csrf_token_is_rejected(tmp_path, teardown_ctx, _admin_env):
    client = _client(tmp_path, teardown_ctx)
    _login(client)

    client.post("/admin/users", data={"telegram_identity": "new-1", "permission_level": "viewer"})

    dashboard = client.get("/admin/")
    assert b"new-1" not in dashboard.data


def test_write_user_with_a_stale_or_wrong_csrf_token_is_rejected(tmp_path, teardown_ctx, _admin_env):
    client = _client(tmp_path, teardown_ctx)
    _login(client)

    client.post(
        "/admin/users",
        data={"telegram_identity": "new-1", "permission_level": "viewer", "csrf_token": "not-the-real-token"},
    )

    dashboard = client.get("/admin/")
    assert b"new-1" not in dashboard.data


# -- User management ------------------------------------------------------


def test_add_user_via_the_dashboard_form(tmp_path, teardown_ctx, _admin_env):
    client = _client(tmp_path, teardown_ctx)
    _login(client)
    csrf_token = _extract_csrf(client.get("/admin/").data)

    resp = client.post(
        "/admin/users",
        data={"telegram_identity": "new-1", "permission_level": "viewer", "csrf_token": csrf_token},
        follow_redirects=True,
    )

    assert resp.status_code == 200
    assert b"new-1" in resp.data
    assert b"is now" in resp.data
    added = teardown_ctx[0].deps.persistence.read_user("new-1")
    assert added is not None
    assert added["permission_level"] == "viewer"


def test_editing_an_existing_users_level_upserts_via_write_user(tmp_path, teardown_ctx, _admin_env):
    client = _client(tmp_path, teardown_ctx)
    _login(client)
    csrf_token = _extract_csrf(client.get("/admin/").data)

    resp = client.post(
        "/admin/users",
        data={"telegram_identity": VIEWER_IDENTITY, "permission_level": "commander", "csrf_token": csrf_token},
        follow_redirects=True,
    )

    assert resp.status_code == 200
    updated = teardown_ctx[0].deps.persistence.read_user(VIEWER_IDENTITY)
    assert updated["permission_level"] == "commander"


def test_remove_user(tmp_path, teardown_ctx, _admin_env):
    ctx_list = teardown_ctx
    client = _client(tmp_path, ctx_list)
    _login(client)
    csrf_token = _extract_csrf(client.get("/admin/").data)

    resp = client.post(
        f"/admin/users/{VIEWER_IDENTITY}/remove",
        data={"csrf_token": csrf_token},
        follow_redirects=True,
    )

    assert resp.status_code == 200
    assert b"removed" in resp.data
    remaining_identities = {user["telegram_identity"] for user in ctx_list[0].deps.persistence.list_users()}
    assert VIEWER_IDENTITY not in remaining_identities


def test_remove_unknown_user_flashes_an_error_without_crashing(tmp_path, teardown_ctx, _admin_env):
    client = _client(tmp_path, teardown_ctx)
    _login(client)
    csrf_token = _extract_csrf(client.get("/admin/").data)

    resp = client.post(
        "/admin/users/nobody-at-all/remove",
        data={"csrf_token": csrf_token},
        follow_redirects=True,
    )

    assert resp.status_code == 200
    assert b"No such user" in resp.data


def test_provision_bot_service_registers_it_at_commander_level(tmp_path, teardown_ctx, _admin_env):
    client = _client(tmp_path, teardown_ctx)
    _login(client)
    csrf_token = _extract_csrf(client.get("/admin/").data)

    resp = client.post(
        "/admin/bot-service/provision",
        data={"csrf_token": csrf_token},
        follow_redirects=True,
    )

    assert resp.status_code == 200
    assert b"bot-service" in resp.data
    assert b"is registered at commander level" in resp.data
