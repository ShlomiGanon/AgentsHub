"""The admin web panel (api/admin.py): login, session, CSRF, rate limiting, user management."""

import contextlib
import http.server
import json
import re
import threading
import types

import pytest

from agents import adapter
from api.admin import AdminConfigError, LoginRateLimiter, _format_duration_phrase, resolve_admin_config
from api.app import build_app
from messages import get_catalog, get_current_catalog, set_current_catalog
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


def test_admin_menu_links_to_all_seven_management_pages(tmp_path, teardown_ctx, _admin_env):
    client = _client(tmp_path, teardown_ctx)
    _login(client)
    page = client.get("/admin/").data
    for path in (
        b'/admin/profiles', b'/admin/protocols', b'/admin/events', b'/admin/users',
        b'/admin/groups', b'/admin/simulator', b'/admin/server',
    ):
        assert b'href="' + path + b'"' in page


@pytest.mark.parametrize(
    "path",
    ["/admin/profiles", "/admin/protocols", "/admin/events", "/admin/server"],
)
def test_new_api_management_pages_require_an_admin_session(path, tmp_path, teardown_ctx, _admin_env):
    client = _client(tmp_path, teardown_ctx)

    response = client.get(path, follow_redirects=False)

    assert response.status_code in (302, 303)
    assert "/admin/login" in response.headers["Location"]


def test_profiles_page_exposes_both_system_methods_through_the_live_api(tmp_path, teardown_ctx, _admin_env):
    client = _client(tmp_path, teardown_ctx)
    _login(client)

    page = client.get("/admin/profiles").data.decode("utf-8")

    assert "GET" in page and "PUT" in page
    assert page.count("/SYSTEM") >= 2
    assert "AdminApi.call('GET', '/SYSTEM'" in page
    assert "AdminApi.call('PUT', '/SYSTEM'" in page
    assert "'X-Identity':identity" in page
    assert "For Tests" in page
    assert 'value="0.5"' in page
    assert "reference_agent" in page


def test_protocols_page_exposes_every_protocol_method_through_the_live_api(tmp_path, teardown_ctx, _admin_env):
    client = _client(tmp_path, teardown_ctx)
    _login(client)

    page = client.get("/admin/protocols").data.decode("utf-8")

    for method in ("GET", "POST", "PUT", "DELETE"):
        assert f"AdminApi.call('{method}'" in page
    assert "/Protocol" in page
    assert "participating_agents" in page
    assert "approved_tools" in page
    assert "approval_flag" in page
    assert "status_check" in page
    assert "protocol-edit-form" in page


def test_events_page_exposes_every_operational_endpoint_through_the_live_api(tmp_path, teardown_ctx, _admin_env):
    client = _client(tmp_path, teardown_ctx)
    ctx = teardown_ctx[0]
    ctx.deps.persistence.append_event({
        "event_id": "event-visible-in-admin",
        "received_at": "2026-09-10T10:00:00+00:00",
        "source": "telegram",
        "sender_identity": COMMANDER_IDENTITY,
        "raw_text": "Smoke beside the north gate",
    })
    _login(client)

    page = client.get("/admin/events").data.decode("utf-8")

    for endpoint in (
        "/Event", "/Msg", "/Job/", "/Holds/Pending", "/Clarify/", "/Approve/",
        "/Notifications", "/Trace/", "/TeamStatus/AttendanceCheck",
    ):
        assert endpoint in page
    assert "sender_identity:AdminApi.identity" in page
    assert "telegram_chat_id" in page
    assert "successLabel" in page and "failedLabel" in page
    assert "event-visible-in-admin" in page
    assert "Smoke beside the north gate" in page
    assert "recent-job" in page
    assert "holds-list" in page and "notifications-list" in page


@pytest.mark.parametrize(
    "path",
    [
        "/admin/", "/admin/profiles", "/admin/protocols", "/admin/events",
        "/admin/users", "/admin/groups", "/admin/server", "/admin/simulator",
    ],
)
def test_admin_pages_do_not_show_http_methods_or_endpoint_paths(path, tmp_path, teardown_ctx, _admin_env):
    from html import unescape

    client = _client(tmp_path, teardown_ctx)
    _login(client)
    html = client.get(path).data.decode("utf-8")
    without_code = re.sub(r"<(script|style)\b.*?</\1>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    visible_text = unescape(re.sub(r"<[^>]+>", " ", without_code))

    assert re.search(r"\b(?:GET|POST|PUT|DELETE)\b", visible_text) is None
    for endpoint in ("/SYSTEM", "/Protocol", "/Event", "/Msg", "/Job", "/Trace", "/Groups", "/User"):
        assert endpoint not in visible_text


def test_api_identity_selection_is_registered_session_scoped_and_shared_between_pages(tmp_path, teardown_ctx, _admin_env):
    client = _client(tmp_path, teardown_ctx)
    _login(client)
    csrf_token = _extract_csrf(client.get("/admin/events").data)

    selected = client.post(
        "/admin/identity",
        data={
            "csrf_token": csrf_token,
            "api_identity": VIEWER_IDENTITY,
            "next_page": "events",
        },
        follow_redirects=False,
    )

    assert selected.status_code in (302, 303)
    assert selected.headers["Location"].endswith("/admin/events")
    for path in ("/admin/events", "/admin/profiles", "/admin/protocols"):
        page = client.get(path).data.decode("utf-8")
        assert f'data-api-identity="{VIEWER_IDENTITY}"' in page
        assert f'<option value="{VIEWER_IDENTITY}" selected>' in page


def test_api_identity_selection_rejects_unregistered_and_service_identities(tmp_path, teardown_ctx, _admin_env):
    client = _client(tmp_path, teardown_ctx)
    _login(client)
    page = client.get("/admin/events")
    csrf_token = _extract_csrf(page.data)
    assert '<option value="bot-service"' not in page.data.decode("utf-8")

    for identity in ("not-registered", "bot-service"):
        response = client.post(
            "/admin/identity",
            data={"csrf_token": csrf_token, "api_identity": identity, "next_page": "events"},
            follow_redirects=True,
        )
        assert b"not a registered human user" in response.data


def test_api_identity_selection_requires_csrf(tmp_path, teardown_ctx, _admin_env):
    client = _client(tmp_path, teardown_ctx)
    _login(client)

    response = client.post(
        "/admin/identity",
        data={"api_identity": VIEWER_IDENTITY, "next_page": "events"},
        follow_redirects=False,
    )

    assert response.status_code in (302, 303)
    assert response.headers["Location"].endswith("/admin/")


@pytest.mark.parametrize(
    "path",
    ["/admin/profiles", "/admin/protocols", "/admin/events"],
)
def test_api_management_page_javascript_is_syntactically_valid(path, tmp_path, teardown_ctx, _admin_env):
    import shutil
    import subprocess

    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    client = _client(tmp_path, teardown_ctx)
    _login(client)
    page = client.get(path).data.decode("utf-8")
    scripts = re.findall(r"<script>(.*?)</script>", page, re.DOTALL)
    assert scripts

    script_path = tmp_path / (path.rsplit("/", 1)[-1] + ".js")
    script_path.write_text("\n".join(scripts), encoding="utf-8")
    result = subprocess.run([node, "--check", str(script_path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_server_page_requires_session_and_disables_controls_without_supervisor(tmp_path, teardown_ctx, _admin_env):
    client = _client(tmp_path, teardown_ctx)
    assert client.get("/admin/server", follow_redirects=False).status_code == 302
    _login(client)
    page = client.get("/admin/server")
    assert page.status_code == 200
    assert b"run_stack.py" in page.data
    assert b"profiles.demo" in page.data


def test_server_safe_mode_control_reflects_the_live_system_setting(tmp_path, teardown_ctx, _admin_env):
    client = _client(tmp_path, teardown_ctx)
    ctx = teardown_ctx[0]
    _login(client)
    enabled = client.put(
        "/SYSTEM",
        headers={"X-Identity": COMMANDER_IDENTITY},
        json={"safe_mode": True},
    )
    assert enabled.status_code == 200
    assert ctx.deps.settings_store.get_safe_mode() is True
    page = client.get("/admin/server")
    assert b"SAFE_MODE = true" in page.data
    assert b"Safe mode is active" in page.data
    assert b"AdminApi.call('PUT', '/SYSTEM'" in page.data


def test_correct_login_reaches_the_dashboard_and_lists_existing_users(tmp_path, teardown_ctx, _admin_env):
    client = _client(tmp_path, teardown_ctx)

    login_resp = _login(client)
    assert login_resp.status_code == 302
    assert "/admin/" in login_resp.headers["Location"]

    dashboard = client.get("/admin/")
    assert dashboard.status_code == 200
    assert b'href="/admin/users"' in dashboard.data
    users = client.get("/admin/users")
    assert COMMANDER_IDENTITY.encode() in users.data
    assert VIEWER_IDENTITY.encode() in users.data


def test_users_page_is_a_complete_crud_ui(tmp_path, teardown_ctx, _admin_env):
    client = _client(tmp_path, teardown_ctx)
    _login(client)

    page = client.get("/admin/users").data.decode("utf-8")

    assert COMMANDER_IDENTITY in page and VIEWER_IDENTITY in page
    assert 'name="full_name"' in page
    assert 'name="permission_level"' in page
    assert 'action="/admin/users"' in page
    assert f'action="/admin/users/{COMMANDER_IDENTITY}/remove"' in page


def test_groups_page_is_a_complete_inline_crud_ui(tmp_path, teardown_ctx, _admin_env):
    client = _client(tmp_path, teardown_ctx)
    ctx = teardown_ctx[0]
    ctx.group_routing.upsert("-10055", "reference_agent", "Operations room")
    _login(client)

    page = client.get("/admin/groups").data.decode("utf-8")

    assert "-10055" in page and "Operations room" in page
    assert 'name="label" value="Operations room"' in page
    assert 'name="agent_name"' in page
    assert 'action="/admin/groups"' in page
    assert 'action="/admin/groups/-10055/remove"' in page


def test_wrong_password_does_not_authenticate_and_gives_a_generic_message(tmp_path, teardown_ctx, _admin_env):
    client = _client(tmp_path, teardown_ctx)

    resp = _login(client, password="wrong")
    assert resp.status_code == 302  # Post/Redirect/Get -- never re-renders the POST result directly
    assert "/admin/login" in resp.headers["Location"]

    reloaded = client.get(resp.headers["Location"])
    assert b"Wrong username or password" in reloaded.data
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
    assert resp.status_code == 302  # Post/Redirect/Get -- never re-renders the POST result directly
    reloaded = client.get(resp.headers["Location"])
    assert b"Too many failed attempts" in reloaded.data
    assert client.get("/admin/", follow_redirects=False).status_code == 302


# -- LoginRateLimiter: global lockout mechanism (deep coverage) -----------
#
# docs/IMPROVES/ADMIN_LOGIN_LOCKOUT_DIAGNOSIS.MD's per-IP lockout is gone, deliberately, in
# favor of one global failure count and one global lockout timestamp for the whole endpoint.
# These tests cover: (1) the threshold read from actual config, not hardcoded; (2) storage stays
# minimal (timestamp only, no redundant "remaining"/"locked" fields); (3) remaining-time is a
# live computation, checked at several points across the window; (4) the exact 15:00 boundary;
# (5) scope is genuinely global across independent sessions; (6) the two Step-3 message-mismatch
# bugs are fixed; (7) Post/Redirect/Get -- a refresh never resubmits/re-records a failure; (8)
# display is minutes/hours only, never seconds; (9) the lightweight progress indicator's
# underlying numbers; (10) a successful login resets the failure count.


def test_rate_limiter_storage_is_minimal_timestamp_only():
    """Requirement 2: guard against a future change reintroducing redundant, driftable state --
    the only lockout-relevant fields are one failure counter and one lockout timestamp. No
    separate "remaining time" value and no separate "is locked" boolean may exist."""

    limiter = LoginRateLimiter(max_attempts=5, lockout_minutes=15)
    configuration_fields = {"_max_attempts", "_lockout_minutes", "_lock"}
    state_fields = {"_failure_count", "_locked_at_monotonic"}
    assert set(vars(limiter)) == configuration_fields | state_fields


def test_threshold_boundary_matches_the_configured_max_attempts(tmp_path, teardown_ctx, monkeypatch, _admin_env):
    """Requirement 1: N-1 failures never locks out, the Nth always does -- N read from the actual
    configured env value, not a literal duplicated in the test."""

    monkeypatch.setenv("ADMIN_LOGIN_MAX_ATTEMPTS", "4")
    monkeypatch.setenv("ADMIN_LOGIN_LOCKOUT_MINUTES", "15")
    max_attempts = resolve_admin_config().login_max_attempts  # reads the same env the app itself reads
    client = _client(tmp_path, teardown_ctx)

    for _ in range(max_attempts - 1):
        _login(client, password="wrong")
    still_open = client.get("/admin/login")
    assert b"Too many failed attempts" not in still_open.data  # one short of the threshold

    resp = _login(client, password="wrong")  # the Nth failure
    assert resp.status_code == 302
    locked = client.get(resp.headers["Location"])
    assert b"Too many failed attempts" in locked.data


def test_remaining_time_is_computed_live_at_several_points_in_the_window(monkeypatch):
    """Requirement 3: freeze/mock time at several points across the 15-minute window and check
    both the remaining time and the lockout status at each point -- not just one snapshot."""

    import api.admin as admin_module

    clock = {"now": 0.0}
    monkeypatch.setattr(admin_module.time, "monotonic", lambda: clock["now"])
    limiter = LoginRateLimiter(max_attempts=3, lockout_minutes=15)

    for _ in range(2):
        assert limiter.record_failure() == 0.0
    remaining_at_lockout = limiter.record_failure()  # the 3rd failure locks it out at now=0.0
    assert remaining_at_lockout == pytest.approx(15.0)

    # Immediately after lockout.
    assert limiter.remaining_minutes() == pytest.approx(15.0, abs=0.01)

    # Midpoint.
    clock["now"] = 7.5 * 60
    assert limiter.remaining_minutes() == pytest.approx(7.5, abs=0.01)

    # One minute before expiry.
    clock["now"] = 14 * 60
    assert limiter.remaining_minutes() == pytest.approx(1.0, abs=0.01)

    # Exactly at expiry.
    clock["now"] = 15 * 60
    assert limiter.remaining_minutes() == 0.0

    # One minute after expiry.
    clock["now"] = 16 * 60
    assert limiter.remaining_minutes() == 0.0


def test_lockout_lifts_exactly_at_the_boundary_not_early_or_late(monkeypatch):
    """Requirement 4: an explicit off-by-one check at exactly 15:00."""

    import api.admin as admin_module

    clock = {"now": 0.0}
    monkeypatch.setattr(admin_module.time, "monotonic", lambda: clock["now"])
    limiter = LoginRateLimiter(max_attempts=1, lockout_minutes=15)
    limiter.record_failure()  # single-attempt threshold -> immediate lockout at now=0.0

    clock["now"] = 15 * 60 - 1
    assert limiter.remaining_minutes() > 0.0  # one second early -- still locked

    clock["now"] = 15 * 60
    assert limiter.remaining_minutes() == 0.0  # exactly the boundary -- lifted, not one minute late

    clock["now"] = 15 * 60 + 1
    assert limiter.remaining_minutes() == 0.0  # stays lifted afterward


def test_lockout_is_global_across_independent_sessions(tmp_path, teardown_ctx, monkeypatch, _admin_env):
    """Requirement 5: this is the intended design now (not a bug) -- two independent
    clients/sessions share one lockout state and are equally locked out and equally unlocked."""

    monkeypatch.setenv("ADMIN_LOGIN_MAX_ATTEMPTS", "3")
    monkeypatch.setenv("ADMIN_LOGIN_LOCKOUT_MINUTES", "15")
    ctx = build_context(tmp_path)
    teardown_ctx.append(ctx)
    app = build_app(ctx)
    client_a = app.test_client()
    client_b = app.test_client()  # a totally independent cookie jar, zero attempts of its own

    for _ in range(3):
        _login(client_a, password="wrong")

    # Client B's very first attempt ever, with the CORRECT password, is still rejected -- the
    # lockout is global, not scoped to whoever actually failed.
    resp = _login(client_b)
    assert resp.status_code == 302
    locked = client_b.get(resp.headers["Location"])
    assert b"Too many failed attempts" in locked.data
    assert client_b.get("/admin/", follow_redirects=False).status_code == 302  # not authenticated

    # And a plain GET on client B, with no submission at all, shows the same global lockout.
    fresh_get = client_b.get("/admin/login")
    assert b"Too many failed attempts" in fresh_get.data


def test_threshold_crossing_request_shows_lockout_message_not_generic(tmp_path, teardown_ctx, monkeypatch, _admin_env):
    """Requirement 6 / diagnosis Step-3 bug (a): the exact request that crosses the threshold
    must show the lockout message, never the generic wrong-credentials message."""

    monkeypatch.setenv("ADMIN_LOGIN_MAX_ATTEMPTS", "3")
    monkeypatch.setenv("ADMIN_LOGIN_LOCKOUT_MINUTES", "15")
    client = _client(tmp_path, teardown_ctx)

    for _ in range(2):
        resp = _login(client, password="wrong")
        reloaded = client.get(resp.headers["Location"])
        assert b"Wrong username or password" in reloaded.data
        assert b"Too many failed attempts" not in reloaded.data

    resp = _login(client, password="wrong")  # the 3rd failure crosses the threshold
    reloaded = client.get(resp.headers["Location"])
    assert b"Too many failed attempts" in reloaded.data
    assert b"Wrong username or password" not in reloaded.data


def test_remaining_time_on_a_later_get_is_recomputed_not_stale(tmp_path, teardown_ctx, monkeypatch, _admin_env):
    """Requirement 6 / diagnosis Step-3 bug (b): a plain GET reload while still locked out shows
    the lockout message with correctly-recomputed remaining time, not the time frozen from the
    first response."""

    monkeypatch.setenv("ADMIN_LOGIN_MAX_ATTEMPTS", "1")
    monkeypatch.setenv("ADMIN_LOGIN_LOCKOUT_MINUTES", "15")
    client = _client(tmp_path, teardown_ctx)

    import api.admin as admin_module

    clock = {"now": 0.0}
    monkeypatch.setattr(admin_module.time, "monotonic", lambda: clock["now"])

    _login(client, password="wrong")  # single-attempt threshold -> immediate lockout
    first = client.get("/admin/login")
    assert b"about 15 minutes" in first.data

    clock["now"] = 10 * 60  # 10 of 15 minutes elapsed -- 5 minutes remain
    second = client.get("/admin/login")
    assert b"about 5 minutes" in second.data
    assert b"about 15 minutes" not in second.data  # not frozen/stale from the first response


def test_plain_refresh_after_a_failed_login_does_not_record_another_failure(tmp_path, teardown_ctx, monkeypatch, _admin_env):
    """Requirement 7 / diagnosis symptom 1: Post/Redirect/Get means a browser "refresh" of the
    failed-login result is a plain GET, not a resubmitted POST -- it must not count as another
    failure."""

    monkeypatch.setenv("ADMIN_LOGIN_MAX_ATTEMPTS", "3")
    monkeypatch.setenv("ADMIN_LOGIN_LOCKOUT_MINUTES", "15")
    client = _client(tmp_path, teardown_ctx)

    resp = _login(client, password="wrong")
    assert resp.status_code == 302  # never re-renders the POST result directly

    for _ in range(5):  # simulate repeated "refresh" as plain GETs
        client.get("/admin/login")

    still_open = client.get("/admin/login")
    assert b"Too many failed attempts" not in still_open.data  # 5 refreshes recorded nothing
    login_resp = _login(client)  # the correct password still works
    assert login_resp.status_code == 302
    assert "/admin/" in login_resp.headers["Location"]


@pytest.mark.parametrize(
    "remaining_minutes,expected_substring",
    [
        (0.5, "less than a minute"),
        (1.0, "about 1 minute"),
        (5.0, "about 5 minutes"),
        (59.0, "about 59 minutes"),
        (60.0, "about 1 hour"),
        (90.0, "about 1.5 hours"),
        (120.0, "about 2 hours"),
        (180.0, "about 3 hours"),
    ],
)
def test_duration_phrase_uses_minutes_or_hours_never_seconds(remaining_minutes, expected_substring):
    """Requirement 8: minutes/hours phrasing, no raw seconds, for representative durations."""

    phrase = _format_duration_phrase(remaining_minutes)
    assert expected_substring in phrase
    assert "second" not in phrase


def test_duration_phrase_never_shows_seconds_anywhere_in_the_default_window():
    """Requirement 8, swept finely across the whole default 15-minute window rather than just a
    few sample points."""

    for tenths_of_a_minute in range(0, 151):
        phrase = _format_duration_phrase(tenths_of_a_minute / 10)
        assert "second" not in phrase
        assert not re.search(r"\b\d+\s*s\b", phrase)  # no stray "47s"-style shorthand either


def test_duration_phrase_in_hebrew_also_avoids_seconds():
    """Requirement 8, other language: same rule holds through the Hebrew catalog entry, not just
    the English one."""

    original = get_current_catalog()
    try:
        set_current_catalog(get_catalog("he"))
        phrase = _format_duration_phrase(5.0)
        assert "שניות" not in phrase and "שנייה" not in phrase
        assert "דקות" in phrase or "דקה" in phrase
    finally:
        set_current_catalog(original)


def _extract_lockout_progress_percent(html_bytes: bytes) -> int:
    html = html_bytes.decode()
    marker = 'lockout-progress-fill" style="width: '
    start = html.index(marker) + len(marker)
    end = html.index("%;", start)
    return int(html[start:end])


def test_lockout_progress_indicator_reflects_the_elapsed_fraction(tmp_path, teardown_ctx, monkeypatch, _admin_env):
    """Requirement 9: the static progress indicator's underlying elapsed/remaining numbers are
    correct at a couple of sample points -- not a pixel-level check, just the driving values."""

    monkeypatch.setenv("ADMIN_LOGIN_MAX_ATTEMPTS", "1")
    monkeypatch.setenv("ADMIN_LOGIN_LOCKOUT_MINUTES", "15")
    client = _client(tmp_path, teardown_ctx)

    import api.admin as admin_module

    clock = {"now": 0.0}
    monkeypatch.setattr(admin_module.time, "monotonic", lambda: clock["now"])

    _login(client, password="wrong")  # locks out immediately (max_attempts=1)

    just_locked = client.get("/admin/login")
    assert _extract_lockout_progress_percent(just_locked.data) == 0  # no time elapsed yet

    clock["now"] = 5 * 60  # 5 of 15 minutes elapsed -- roughly a third
    third_elapsed = client.get("/admin/login")
    percent = _extract_lockout_progress_percent(third_elapsed.data)
    assert 30 <= percent <= 36

    clock["now"] = 15 * 60  # fully elapsed -- lockout has lifted, no banner left to show
    fully_elapsed = client.get("/admin/login")
    # "lockout-progress-fill" is a CSS class name that's always present in the page's <style>
    # block; check for the actual rendered element (role="progressbar" only appears there).
    assert b'role="progressbar"' not in fully_elapsed.data


def test_successful_login_resets_the_failure_count(tmp_path, teardown_ctx, monkeypatch, _admin_env):
    """Requirement 10: a successful login below the threshold resets the failure count -- a
    near-miss must not "carry over" into unrelated future attempts."""

    monkeypatch.setenv("ADMIN_LOGIN_MAX_ATTEMPTS", "3")
    monkeypatch.setenv("ADMIN_LOGIN_LOCKOUT_MINUTES", "15")
    client = _client(tmp_path, teardown_ctx)

    _login(client, password="wrong")  # one near-miss failure, below the threshold
    success = _login(client)
    assert success.status_code == 302
    assert "/admin/" in success.headers["Location"]

    # If the earlier near-miss had carried over, 1 old + 2 new = max_attempts(3) would already
    # trip the lockout here. It must not -- the successful login must have reset the count to 0.
    _login(client, password="wrong")
    resp = _login(client, password="wrong")
    reloaded = client.get(resp.headers["Location"])
    assert b"Too many failed attempts" not in reloaded.data


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

    users = client.get("/admin/users")
    assert b"new-1" not in users.data


def test_write_user_with_a_stale_or_wrong_csrf_token_is_rejected(tmp_path, teardown_ctx, _admin_env):
    client = _client(tmp_path, teardown_ctx)
    _login(client)

    client.post(
        "/admin/users",
        data={"telegram_identity": "new-1", "permission_level": "viewer", "csrf_token": "not-the-real-token"},
    )

    users = client.get("/admin/users")
    assert b"new-1" not in users.data


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


def test_admin_can_set_a_single_full_name_and_level_only_edits_preserve_it(tmp_path, teardown_ctx, _admin_env):
    client = _client(tmp_path, teardown_ctx)
    _login(client)
    csrf_token = _extract_csrf(client.get("/admin/").data)
    client.post("/admin/users", data={"telegram_identity": VIEWER_IDENTITY, "permission_level": "viewer", "full_name": "Dana Levi", "csrf_token": csrf_token})
    client.post("/admin/users", data={"telegram_identity": VIEWER_IDENTITY, "permission_level": "commander", "csrf_token": csrf_token})
    assert teardown_ctx[0].deps.persistence.read_user(VIEWER_IDENTITY)["full_name"] == "Dana Levi"


def test_admin_lists_and_explicitly_approves_an_automatic_user(tmp_path, teardown_ctx, _admin_env):
    client = _client(tmp_path, teardown_ctx)
    teardown_ctx[0].deps.persistence.register_telegram_user_if_missing("auto-1")
    _login(client)
    page = client.get("/admin/users")
    csrf_token = _extract_csrf(page.data)
    assert b"auto-1" in page.data
    assert b"Automatic" in page.data

    response = client.post(
        "/admin/users/auto-1/approve",
        data={"csrf_token": csrf_token},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert teardown_ctx[0].deps.persistence.read_user("auto-1")["auto_register"] is False


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


# -- Telegram groups ----------------------------------------------------------


def test_dashboard_lists_groups_and_routable_agents(tmp_path, teardown_ctx, _admin_env):
    client = _client(tmp_path, teardown_ctx)
    teardown_ctx[0].group_routing.upsert("-1001", "reference_agent", "ops room")
    _login(client)

    menu = client.get("/admin/").data
    page = client.get("/admin/groups").data

    assert b'href="/admin/groups"' in menu
    assert b"Telegram groups" in page
    assert b"-1001" in page
    assert b"ops room" in page
    assert b'value="main_agent"' in page
    assert b'value="reference_agent"' in page


def test_admin_adds_updates_and_removes_a_group_binding(tmp_path, teardown_ctx, _admin_env):
    client = _client(tmp_path, teardown_ctx)
    ctx = teardown_ctx[0]
    _login(client)
    csrf_token = _extract_csrf(client.get("/admin/").data)

    added = client.post(
        "/admin/groups",
        data={"csrf_token": csrf_token, "chat_id": "-1002", "agent_name": "reference_agent", "label": "readiness"},
        follow_redirects=True,
    )
    assert added.status_code == 200
    assert b"is now routed to" in added.data
    assert ctx.group_routing.get("-1002").agent_name == "reference_agent"
    assert ctx.deps.persistence.read_group("-1002")["label"] == "readiness"

    updated = client.post(
        "/admin/groups",
        data={"csrf_token": csrf_token, "chat_id": "-1002", "agent_name": "main_agent", "label": "command room"},
        follow_redirects=True,
    )
    assert updated.status_code == 200
    assert ctx.group_routing.get("-1002").agent_name == "main_agent"
    assert ctx.deps.persistence.read_group("-1002")["label"] == "command room"

    removed = client.post("/admin/groups/-1002/remove", data={"csrf_token": csrf_token}, follow_redirects=True)
    assert removed.status_code == 200
    assert b"removed" in removed.data
    assert ctx.group_routing.get("-1002") is None
    assert ctx.deps.persistence.read_group("-1002") is None


def test_admin_renames_a_groups_chat_id(tmp_path, teardown_ctx, _admin_env):
    """The generic "change chat ID" action (docs/profile_simulations_design.md) —
    e.g. replacing a simulation group's reserved placeholder with a real Telegram
    group ID once one exists. Not simulation-specific: works for any group."""

    client = _client(tmp_path, teardown_ctx)
    ctx = teardown_ctx[0]
    ctx.deps.persistence.write_group("-9000000000000000", "reference_agent", "placeholder")
    _login(client)
    csrf_token = _extract_csrf(client.get("/admin/groups").data)

    renamed = client.post(
        "/admin/groups/-9000000000000000/rename",
        data={"csrf_token": csrf_token, "new_chat_id": "-1009876543210"},
        follow_redirects=True,
    )
    assert renamed.status_code == 200
    assert b"is now" in renamed.data
    assert ctx.group_routing.get("-9000000000000000") is None
    assert ctx.group_routing.get("-1009876543210").agent_name == "reference_agent"
    assert ctx.deps.persistence.read_group("-9000000000000000") is None
    assert ctx.deps.persistence.read_group("-1009876543210")["label"] == "placeholder"


def test_admin_rename_group_rejects_bad_input_and_conflicts(tmp_path, teardown_ctx, _admin_env):
    client = _client(tmp_path, teardown_ctx)
    ctx = teardown_ctx[0]
    ctx.deps.persistence.write_group("-1005", "reference_agent", "existing")
    ctx.deps.persistence.write_group("-1006", "reference_agent", "other")
    _login(client)
    csrf_token = _extract_csrf(client.get("/admin/groups").data)

    empty = client.post("/admin/groups/-1005/rename", data={"csrf_token": csrf_token, "new_chat_id": ""}, follow_redirects=True)
    assert b"new Telegram chat ID is required" in empty.data

    not_negative = client.post(
        "/admin/groups/-1005/rename", data={"csrf_token": csrf_token, "new_chat_id": "1007"}, follow_redirects=True
    )
    assert b"must be a negative number" in not_negative.data

    unknown_old = client.post(
        "/admin/groups/-9999/rename", data={"csrf_token": csrf_token, "new_chat_id": "-1007"}, follow_redirects=True
    )
    assert b"No such group" in unknown_old.data

    taken = client.post(
        "/admin/groups/-1005/rename", data={"csrf_token": csrf_token, "new_chat_id": "-1006"}, follow_redirects=True
    )
    assert b"already used by another group" in taken.data
    # Rolled back, not partially applied.
    assert ctx.deps.persistence.read_group("-1005")["label"] == "existing"
    assert ctx.deps.persistence.read_group("-1006")["label"] == "other"


def test_admin_lists_and_explicitly_approves_an_automatic_group(tmp_path, teardown_ctx, _admin_env):
    client = _client(tmp_path, teardown_ctx)
    ctx = teardown_ctx[0]
    ctx.group_routing.register_telegram_group_if_missing("-1004", "Visitors")
    _login(client)
    page = client.get("/admin/groups")
    csrf_token = _extract_csrf(page.data)
    assert b"-1004" in page.data
    assert b"Automatic" in page.data

    response = client.post(
        "/admin/groups/-1004/approve",
        data={"csrf_token": csrf_token},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert ctx.deps.persistence.read_group("-1004")["auto_register"] is False
    assert ctx.group_routing.get("-1004").auto_register is False


def test_server_page_has_a_friendly_safe_mode_control(tmp_path, teardown_ctx, _admin_env):
    client = _client(tmp_path, teardown_ctx)
    _login(client)
    page = client.get("/admin/server").data
    assert b"SAFE_MODE = false" in page
    assert b"Open mode is active" in page
    assert b'data-safe-mode="true"' in page
    assert b'data-safe-mode="false"' in page


def test_admin_group_writes_flash_errors_for_bad_input(tmp_path, teardown_ctx, _admin_env):
    client = _client(tmp_path, teardown_ctx)
    _login(client)
    csrf_token = _extract_csrf(client.get("/admin/").data)

    bad_agent = client.post(
        "/admin/groups",
        data={"csrf_token": csrf_token, "chat_id": "-1003", "agent_name": "history_agent"},
        follow_redirects=True,
    )
    assert b"is not a routable agent" in bad_agent.data
    assert teardown_ctx[0].group_routing.get("-1003") is None

    no_chat = client.post("/admin/groups", data={"csrf_token": csrf_token, "agent_name": "reference_agent"}, follow_redirects=True)
    assert b"chat ID is required" in no_chat.data

    unknown = client.post("/admin/groups/-9999/remove", data={"csrf_token": csrf_token}, follow_redirects=True)
    assert b"No such group" in unknown.data


def test_admin_group_routes_require_a_session_and_csrf(tmp_path, teardown_ctx, _admin_env):
    client = _client(tmp_path, teardown_ctx)

    anonymous = client.post("/admin/groups", data={"chat_id": "-1", "agent_name": "reference_agent"}, follow_redirects=False)
    assert anonymous.status_code in (302, 303)

    _login(client)
    no_csrf = client.post("/admin/groups", data={"chat_id": "-1", "agent_name": "reference_agent"}, follow_redirects=False)
    assert no_csrf.status_code != 200 or b"is now routed to" not in no_csrf.data
    assert teardown_ctx[0].group_routing.get("-1") is None


# -- Language / direction -----------------------------------------------------


def test_admin_pages_render_ltr_english_for_an_english_profile(tmp_path, teardown_ctx, _admin_env):
    client = _client(tmp_path, teardown_ctx)

    login_page = client.get("/admin/login").data
    assert b'<html lang="en" dir="ltr">' in login_page
    assert b"bootstrap.min.css" in login_page
    assert b"bootstrap.rtl.min.css" not in login_page

    _login(client)
    dashboard = client.get("/admin/").data
    assert b'<html lang="en" dir="ltr">' in dashboard
    assert b"Administration" in dashboard


def test_admin_pages_render_rtl_hebrew_for_a_hebrew_profile(tmp_path, teardown_ctx, _admin_env):
    """The panel's language is the profile's DEFAULT_LANGUAGE, through the same catalog the bot
    uses — api/app.py sets it per request from `loaded_profile.message_catalog`."""

    client = _client(tmp_path, teardown_ctx)
    teardown_ctx[0].loaded_profile.message_catalog = get_catalog("he")
    hebrew = get_catalog("he")

    login_page = client.get("/admin/login").data.decode("utf-8")
    assert '<html lang="he" dir="rtl">' in login_page
    assert "bootstrap.rtl.min.css" in login_page
    assert hebrew.text("admin.login_title") in login_page
    assert hebrew.text("admin.username") in login_page

    _login(client)
    dashboard = client.get("/admin/").data.decode("utf-8")
    assert '<html lang="he" dir="rtl">' in dashboard
    assert hebrew.text("admin.menu_title") in dashboard
    assert hebrew.text("admin.menu_groups") in dashboard
    assert hebrew.text("admin.menu_simulator") in dashboard
    assert hebrew.text("admin.menu_profiles") in dashboard
    assert hebrew.text("admin.menu_protocols") in dashboard
    assert hebrew.text("admin.menu_events") in dashboard

    csrf_token = _extract_csrf(dashboard.encode("utf-8"))
    flashed = client.post(
        "/admin/users",
        data={"csrf_token": csrf_token, "telegram_identity": "he-1", "permission_level": "viewer"},
        follow_redirects=True,
    ).data.decode("utf-8")
    from html import unescape

    assert hebrew.text("admin.user_written", identity="he-1", level="viewer") in unescape(flashed)

    simulator = client.get("/admin/simulator").data.decode("utf-8")
    assert '<html lang="he" dir="rtl">' in simulator
    assert hebrew.text("admin.simulator.title") in simulator


# -- Scenario simulator -------------------------------------------------------


def test_simulator_requires_a_session(tmp_path, teardown_ctx, _admin_env):
    client = _client(tmp_path, teardown_ctx)

    anonymous = client.get("/admin/simulator", follow_redirects=False)
    assert anonymous.status_code in (302, 303)
    assert "/admin/login" in anonymous.headers["Location"]


def test_dashboard_links_to_the_simulator_and_back(tmp_path, teardown_ctx, _admin_env):
    client = _client(tmp_path, teardown_ctx)
    _login(client)

    dashboard = client.get("/admin/").data
    assert b'href="/admin/simulator"' in dashboard
    assert b"Scenario simulator" in dashboard

    simulator = client.get("/admin/simulator").data
    assert b'href="/admin/"' in simulator
    assert b"User administration" in simulator


def _embedded_simulator_data(page: bytes) -> dict:
    import json
    from html import unescape

    match = re.search(rb'<script id="sim-data" type="application/json">(.*?)</script>', page, re.DOTALL)
    assert match, "simulator page must embed its data as JSON"
    return json.loads(unescape(match.group(1).decode("utf-8")))


def test_simulator_embeds_live_groups_users_and_catalog_strings(tmp_path, teardown_ctx, _admin_env):
    client = _client(tmp_path, teardown_ctx)
    ctx = teardown_ctx[0]
    ctx.group_routing.upsert("-1001", "reference_agent", "ops room")
    _login(client)

    page = client.get("/admin/simulator").data
    assert page is not None
    data = _embedded_simulator_data(page)

    assert data["groups"] == [{"chat_id": "-1001", "agent_name": "reference_agent", "label": "ops room"}]
    identities = {user["telegram_identity"] for user in data["users"]}
    assert {COMMANDER_IDENTITY, VIEWER_IDENTITY} <= identities
    assert "main_agent" in data["routable_agents"] and "reference_agent" in data["routable_agents"]
    assert data["bot_service_identity"] == "bot-service"

    english = get_catalog("en")
    # Every admin.simulator.* key is forwarded, prefix stripped, as a raw template for the script.
    expected_keys = {key[len("admin.simulator."):] for key in english.messages if key.startswith("admin.simulator.")}
    assert set(data["strings"]) == expected_keys
    assert data["strings"]["route_bound"] == english.messages["admin.simulator.route_bound"]


def test_simulator_page_talks_to_the_real_endpoints_only(tmp_path, teardown_ctx, _admin_env):
    """Event-kind steps still go straight to /Event and poll /Job — the bot's own
    endpoints, unchanged (docs/bot_simulation_mode_design.md §2 decision 3).
    Message-kind steps are proxied through /admin/simulator/bot-msg instead of
    calling /Msg directly (§4.4/§7) — no client-side dispatch shortcut, and the
    legacy bundled-fixture route stays gone."""

    client = _client(tmp_path, teardown_ctx)
    _login(client)

    page = client.get("/admin/simulator").data.decode("utf-8")
    assert "'/Event'" in page
    assert "'/Job/'" in page
    assert "'X-Identity'" in page
    assert "/admin/simulator/bot-msg" in page
    assert "'/Msg'" not in page
    assert "/admin/simulator/dispatch" not in page  # never a client-side dispatch shortcut
    assert "/admin/simulator/example" not in page  # the legacy bundled-fixture route is gone


def test_simulator_script_is_syntactically_valid_javascript(tmp_path, teardown_ctx, _admin_env):
    import shutil
    import subprocess

    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")

    client = _client(tmp_path, teardown_ctx)
    _login(client)
    page = client.get("/admin/simulator").data.decode("utf-8")
    script = re.search(r"<script>\s*(\(function \(\) \{.*?\}\)\(\);)\s*</script>", page, re.DOTALL)
    assert script, "simulator script block not found"

    script_path = tmp_path / "simulator.js"
    script_path.write_text(script.group(1), encoding="utf-8")
    result = subprocess.run([node, "--check", str(script_path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def _extract_between(script: str, start_marker: str, end_marker: str) -> str:
    start = script.index(start_marker)
    end = script.index(end_marker, start)
    return script[start:end]


def test_generic_missing_id_collection_and_substitution_are_functionally_correct(tmp_path, teardown_ctx, _admin_env):
    """docs/profile_simulations_design.md (c): collectMissingIdentifiers()/applyManualMapping()
    are pure functions (no DOM) — extracted straight from the rendered page and executed for
    real under node, not just syntax-checked, since this is genuinely new logic."""

    import json
    import shutil
    import subprocess

    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")

    client = _client(tmp_path, teardown_ctx)
    _login(client)
    page = client.get("/admin/simulator").data.decode("utf-8")

    collect_fn = _extract_between(page, "function collectMissingIdentifiers(raw) {", "  function applyManualMapping")
    apply_fn = _extract_between(page, "function applyManualMapping(raw, mapping) {", "  function offerManualMapping")
    assert collect_fn.strip() and apply_fn.strip(), "expected functions not found in the rendered page"

    driver = f"""
{collect_fn}
{apply_fn}

const cases = JSON.parse(require('fs').readFileSync(process.argv[2], 'utf8'));
const results = cases.map(function (testCase) {{
  const missing = collectMissingIdentifiers(testCase.raw);
  const applied = testCase.mapping ? applyManualMapping(testCase.raw, testCase.mapping) : null;
  return {{ missing: missing, applied: applied }};
}});
console.log(JSON.stringify(results));
"""
    driver_path = tmp_path / "driver.js"
    driver_path.write_text(driver, encoding="utf-8")

    fully_specified = {
        "scenario": {}, "chats": [{"key": "dm", "kind": "message", "telegram_chat_type": "private"}],
        "steps": [{"step": 1, "chat": "dm", "sender_identity": "12345", "text": "hi"}],
    }
    needs_mapping = {
        "scenario": {},
        "chats": [
            {"key": "dm", "kind": "message", "telegram_chat_type": "private"},
            {"key": "team", "kind": "message", "label": "Response team", "telegram_chat_type": "supergroup", "telegram_chat_id": "team"},
        ],
        "steps": [
            {"step": 1, "chat": "dm", "sender_identity": "viewer", "text": "hi"},
            {"step": 2, "chat": "team", "sender_identity": "viewer", "text": "status?"},  # same placeholder, reused
            {"step": 3, "chat": "team", "sender_identity": "commander", "text": "go"},
        ],
    }
    mapping = {"personaIds": {"viewer": "9000000000000001", "commander": "9000000000000000"}, "groupIds": {"team": "-9000000000000000"}}
    missing_but_empty_sender = {
        "scenario": {}, "chats": [{"key": "dm", "kind": "message", "telegram_chat_type": "private"}],
        "steps": [{"step": 1, "chat": "dm", "sender_identity": "", "text": "hi"}],
    }
    cases_path = tmp_path / "cases.json"
    cases_path.write_text(
        json.dumps([
            {"raw": fully_specified, "mapping": None},
            {"raw": needs_mapping, "mapping": mapping},
            {"raw": missing_but_empty_sender, "mapping": None},
        ]),
        encoding="utf-8",
    )

    result = subprocess.run([node, str(driver_path), str(cases_path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    outcomes = json.loads(result.stdout)

    # 1. A fully-specified scenario has nothing to map.
    assert outcomes[0]["missing"] == {"groupsNeedingId": [], "personaValues": []}

    # 2. Each distinct placeholder appears exactly once, regardless of how many steps use it;
    #    groups are identified by the chat's own key, not by the (shared/empty) placeholder text.
    assert outcomes[1]["missing"]["personaValues"] == ["viewer", "commander"]
    assert outcomes[1]["missing"]["groupsNeedingId"] == [{"key": "team", "label": "Response team"}]
    # Substitution replaces every occurrence of each placeholder, and never mutates the input.
    applied = outcomes[1]["applied"]
    assert applied["steps"][0]["sender_identity"] == "9000000000000001"
    assert applied["steps"][1]["sender_identity"] == "9000000000000001"
    assert applied["steps"][2]["sender_identity"] == "9000000000000000"
    assert applied["chats"][1]["telegram_chat_id"] == "-9000000000000000"
    assert needs_mapping["steps"][0]["sender_identity"] == "viewer"  # original untouched

    # 3. A completely empty sender_identity is not offered a mapping slot (nothing to label it
    #    with) — it stays a hard validation error from validateScenario() itself, unchanged.
    assert outcomes[2]["missing"] == {"groupsNeedingId": [], "personaValues": []}


# -- POST /admin/simulator/bot-msg: proxy to bot.simulator_app (docs/bot_simulation_mode_design.md §4.4) --


class _FakeSimulatorHandler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        self.server.received.append({
            "path": self.path,
            "headers": dict(self.headers),
            "body": json.loads(body) if body else None,
        })
        self._respond()

    def do_GET(self):
        self.server.received.append({"path": self.path, "headers": dict(self.headers), "body": None})
        self._respond()

    def _respond(self):
        payload = json.dumps(self.server.response_body).encode("utf-8")
        self.send_response(self.server.response_status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format, *args):  # keep test output quiet
        pass


@contextlib.contextmanager
def _fake_simulator_server(status=200, body=None):
    server = http.server.HTTPServer(("127.0.0.1", 0), _FakeSimulatorHandler)
    server.received = []
    server.response_status = status
    server.response_body = body if body is not None else {}
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_simulator_bot_msg_requires_an_admin_session(tmp_path, teardown_ctx, _admin_env):
    client = _client(tmp_path, teardown_ctx, simulator_port=9999)

    response = client.post("/admin/simulator/bot-msg", json={}, follow_redirects=False)

    assert response.status_code in (302, 303)
    assert "/admin/login" in response.headers["Location"]


def test_simulator_bot_msg_reports_a_clear_error_when_the_profile_has_no_simulator_port(tmp_path, teardown_ctx, _admin_env):
    client = _client(tmp_path, teardown_ctx)  # simulator_port defaults to None
    _login(client)

    response = client.post("/admin/simulator/bot-msg", json={"sender_identity": "1"})

    assert response.status_code == 501
    assert "SIMULATOR_PORT" in response.get_json()["error"]["message"]


def test_simulator_bot_msg_reports_a_clear_error_when_the_process_is_unreachable(tmp_path, teardown_ctx, _admin_env):
    # Port 1 is a privileged, essentially-always-refused port — nothing is listening.
    client = _client(tmp_path, teardown_ctx, simulator_port=1)
    _login(client)

    response = client.post("/admin/simulator/bot-msg", json={"sender_identity": "1"})

    assert response.status_code == 502


def test_simulator_bot_msg_forwards_the_request_and_relays_the_response(tmp_path, teardown_ctx, _admin_env, monkeypatch):
    monkeypatch.setenv("BOT_SERVICE_KEY", "test-service-key")
    with _fake_simulator_server(status=200, body={"reply_text": "42 events"}) as server:
        port = server.server_address[1]
        client = _client(tmp_path, teardown_ctx, simulator_port=port)
        _login(client)

        response = client.post(
            "/admin/simulator/bot-msg",
            json={
                "sender_identity": "9000000000000002", "chat_id": "9000000000000002",
                "chat_type": "private", "text": "hi", "source_message_id": "s1",
            },
        )

        assert response.status_code == 200
        assert response.get_json() == {"reply_text": "42 events"}
        assert len(server.received) == 1
        assert server.received[0]["path"] == "/Simulator-msg"
        assert server.received[0]["headers"]["X-Service-Key"] == "test-service-key"
        assert server.received[0]["body"] == {
            "sender_identity": "9000000000000002", "chat_id": "9000000000000002",
            "chat_type": "private", "text": "hi", "source_message_id": "s1",
        }


def test_simulator_bot_msg_relays_a_refusal_status_and_body_unchanged(tmp_path, teardown_ctx, _admin_env, monkeypatch):
    """The simulator process's own identity-allowlist refusal (403) must reach the
    browser exactly as-is — the proxy is pure plumbing, not another decision point."""

    monkeypatch.setenv("BOT_SERVICE_KEY", "test-service-key")
    with _fake_simulator_server(status=403, body={"error": {"message": "not a currently-declared persona"}}) as server:
        port = server.server_address[1]
        client = _client(tmp_path, teardown_ctx, simulator_port=port)
        _login(client)

        response = client.post("/admin/simulator/bot-msg", json={"sender_identity": "1"})

        assert response.status_code == 403
        assert response.get_json() == {"error": {"message": "not a currently-declared persona"}}


def test_simulator_bot_msg_never_leaks_the_service_key_to_the_browser(tmp_path, teardown_ctx, _admin_env, monkeypatch):
    monkeypatch.setenv("BOT_SERVICE_KEY", "test-service-key")
    with _fake_simulator_server(status=200, body={"reply_text": "ok"}) as server:
        port = server.server_address[1]
        client = _client(tmp_path, teardown_ctx, simulator_port=port)
        _login(client)

        response = client.post("/admin/simulator/bot-msg", json={"sender_identity": "1"})

        assert b"test-service-key" not in response.data
        for header_value in response.headers.values():
            assert "test-service-key" not in header_value


# -- GET /admin/simulator/bot-poll: Priority 3's polling proxy (docs/work_process.md §16) --


def test_simulator_bot_poll_requires_an_admin_session(tmp_path, teardown_ctx, _admin_env):
    client = _client(tmp_path, teardown_ctx, simulator_port=9999)

    response = client.get("/admin/simulator/bot-poll?chat_id=1&status_len=0&sent_len=0", follow_redirects=False)

    assert response.status_code in (302, 303)
    assert "/admin/login" in response.headers["Location"]


def test_simulator_bot_poll_reports_a_clear_error_when_unconfigured(tmp_path, teardown_ctx, _admin_env):
    client = _client(tmp_path, teardown_ctx)  # simulator_port defaults to None
    _login(client)

    response = client.get("/admin/simulator/bot-poll?chat_id=1&status_len=0&sent_len=0")

    assert response.status_code == 501


def test_simulator_bot_poll_forwards_query_params_and_relays_the_response(tmp_path, teardown_ctx, _admin_env, monkeypatch):
    monkeypatch.setenv("BOT_SERVICE_KEY", "test-service-key")
    with _fake_simulator_server(status=200, body={"reply_text": "job finished", "watermark": {"status_len": 3, "sent_len": 0}}) as server:
        port = server.server_address[1]
        client = _client(tmp_path, teardown_ctx, simulator_port=port)
        _login(client)

        response = client.get("/admin/simulator/bot-poll?chat_id=9000000000000002&status_len=2&sent_len=0")

        assert response.status_code == 200
        assert response.get_json() == {"reply_text": "job finished", "watermark": {"status_len": 3, "sent_len": 0}}
        assert len(server.received) == 1
        assert server.received[0]["path"] == "/Simulator-msg/poll?chat_id=9000000000000002&status_len=2&sent_len=0"
        assert server.received[0]["headers"]["X-Service-Key"] == "test-service-key"


# -- pollSimulatorChat()'s per-chat generation guard: docs/work_process.md §19 --
# (the duplicate-ack / stray "model thinking" bubble fix — a second step sent to the
# same chat must make an earlier, still-running poll loop for that chat stand down.)


def test_a_superseded_poll_loop_never_polls_or_renders_anything(tmp_path, teardown_ctx, _admin_env):
    """Executed for real under node, not just syntax-checked, since this is genuinely
    new logic: runs a *single* pollSimulatorChat() loop as a same-process timing
    baseline, then two loops started back-to-back for the same chat_id (exactly what
    a second message-kind step sent to an already-being-watched chat does), with a
    fake apiCall that always finds "new" content. Comparing against the baseline
    (rather than a fixed call count) keeps this robust across machines: without the
    generation guard, two independent loops poll roughly *twice* as often as one;
    with it, the superseded loop dies on its very first check (both loops claim
    their generation synchronously before either ever awaits), so two-loop and
    one-loop call counts land close together."""

    import shutil
    import subprocess

    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")

    client = _client(tmp_path, teardown_ctx)
    _login(client)
    page = client.get("/admin/simulator").data.decode("utf-8")

    poll_fn = _extract_between(page, "async function pollSimulatorChat(chatKey, chatId, watermark) {", "  async function sendNext")
    assert poll_fn.strip(), "pollSimulatorChat not found in the rendered page"

    driver = f"""
const POLL_INTERVAL_MS = 5;
const POLL_TIMEOUT_MS = 200;

function makeHarness() {{
  const pollGenerationByChatId = {{}};
  let callCount = 0;
  async function apiCall() {{
    callCount += 1;
    return {{ status: 200, payload: {{ reply_text: 'reply-' + callCount, watermark: {{ status_len: callCount, sent_len: 0 }} }} }};
  }}
  const appended = [];
  function appendBubble(chatKey, kind, sender, text) {{ appended.push(text); }}
  function t(key) {{ return key; }}

  {poll_fn}

  return {{ pollSimulatorChat, getCallCount: function () {{ return callCount; }}, getAppendedCount: function () {{ return appended.length; }} }};
}}

(async function () {{
  const baseline = makeHarness();
  await baseline.pollSimulatorChat('k', 'chat-baseline', {{ status_len: 0, sent_len: 0 }});

  const twoLoop = makeHarness();
  // Step 1's loop starts (generation 1), then step 2's loop starts for the *same*
  // chat immediately after, still synchronously — exactly like sendNext() firing
  // pollSimulatorChat() again for a chat that's already being watched.
  const oldLoop = twoLoop.pollSimulatorChat('k', 'chat-x', {{ status_len: 0, sent_len: 0 }});
  const newLoop = twoLoop.pollSimulatorChat('k', 'chat-x', {{ status_len: 0, sent_len: 0 }});
  await Promise.all([oldLoop, newLoop]);

  console.log(JSON.stringify({{
    baselineCallCount: baseline.getCallCount(),
    baselineAppendedCount: baseline.getAppendedCount(),
    twoLoopCallCount: twoLoop.getCallCount(),
    twoLoopAppendedCount: twoLoop.getAppendedCount(),
  }}));
}})();
"""
    driver_path = tmp_path / "poll_driver.js"
    driver_path.write_text(driver, encoding="utf-8")

    result = subprocess.run([node, str(driver_path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    outcome = json.loads(result.stdout)

    assert outcome["baselineCallCount"] > 1  # the timing window is actually producing several polls
    # Every poll that happened has a matching bubble, in both scenarios (no orphaned calls).
    assert outcome["baselineAppendedCount"] == outcome["baselineCallCount"]
    assert outcome["twoLoopAppendedCount"] == outcome["twoLoopCallCount"]
    # The actual bug: without the generation guard, two independent loops poll roughly
    # *twice* as often as one (confirmed by reverting the fix locally: ~26 vs. ~13 calls
    # in this same window). With the guard, the superseded loop contributes nothing, so
    # the two-loop count stays close to the single-loop baseline — well under double it.
    assert outcome["twoLoopCallCount"] <= outcome["baselineCallCount"] * 1.5
