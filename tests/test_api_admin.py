"""The admin web panel (api/admin.py): login, session, CSRF, rate limiting, user management."""

import re
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


# -- Telegram groups ----------------------------------------------------------


def test_dashboard_lists_groups_and_routable_agents(tmp_path, teardown_ctx, _admin_env):
    client = _client(tmp_path, teardown_ctx)
    teardown_ctx[0].group_routing.upsert("-1001", "reference_agent", "ops room")
    _login(client)

    page = client.get("/admin/").data

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
        data={"csrf_token": csrf_token, "chat_id": "-1002", "agent_name": "main_agent", "label": "readiness"},
        follow_redirects=True,
    )
    assert updated.status_code == 200
    assert ctx.group_routing.get("-1002").agent_name == "main_agent"

    removed = client.post("/admin/groups/-1002/remove", data={"csrf_token": csrf_token}, follow_redirects=True)
    assert removed.status_code == 200
    assert b"removed" in removed.data
    assert ctx.group_routing.get("-1002") is None
    assert ctx.deps.persistence.read_group("-1002") is None


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
    assert b"User administration" in dashboard


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
    assert hebrew.text("admin.dashboard_title") in dashboard
    assert hebrew.text("admin.groups_title") in dashboard
    assert hebrew.text("admin.nav_simulator") in dashboard

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
    """The page's script sends steps to /Msg and /Event and polls /Job — the bot's own
    endpoints — and does not route through any admin-side proxy."""

    client = _client(tmp_path, teardown_ctx)
    _login(client)

    page = client.get("/admin/simulator").data.decode("utf-8")
    assert "'/Msg'" in page
    assert "'/Event'" in page
    assert "'/Job/'" in page
    assert "'X-Identity'" in page
    assert "/admin/simulator/" not in page  # no dispatch/proxy sub-route exists or is referenced


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
