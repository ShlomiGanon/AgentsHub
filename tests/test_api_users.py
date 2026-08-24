"""GET /User/<identity> and GET /Commanders (work_plan.md §8.14, §8.13)."""

import pytest

from api.app import build_app
from tests.api_fakes import COMMANDER_IDENTITY, VIEWER_IDENTITY, auth_headers, build_context


@pytest.fixture
def teardown_ctx():
    contexts = []
    yield contexts
    for ctx in contexts:
        ctx.queue.stop()
        ctx.deps.persistence.close()


# -- GET /User/<identity> ----------------------------------------------------


def test_a_known_identity_reports_registered_and_its_level(tmp_path, teardown_ctx):
    ctx = build_context(tmp_path)
    teardown_ctx.append(ctx)
    client = build_app(ctx).test_client()

    resp = client.get(f"/User/{COMMANDER_IDENTITY}", headers=auth_headers(VIEWER_IDENTITY))

    assert resp.status_code == 200
    assert resp.get_json() == {"registered": True, "permission_level": "commander"}


def test_an_unknown_identity_reports_unregistered_not_an_error(tmp_path, teardown_ctx):
    ctx = build_context(tmp_path)
    teardown_ctx.append(ctx)
    client = build_app(ctx).test_client()

    resp = client.get("/User/nobody", headers=auth_headers(VIEWER_IDENTITY))

    assert resp.status_code == 200
    assert resp.get_json() == {"registered": False, "permission_level": None}


def test_viewer_level_is_sufficient_to_resolve_another_identity(tmp_path, teardown_ctx):
    ctx = build_context(tmp_path)
    teardown_ctx.append(ctx)
    client = build_app(ctx).test_client()

    resp = client.get(f"/User/{COMMANDER_IDENTITY}", headers=auth_headers(VIEWER_IDENTITY))

    assert resp.status_code == 200


def test_requires_authentication(tmp_path, teardown_ctx):
    ctx = build_context(tmp_path)
    teardown_ctx.append(ctx)
    client = build_app(ctx).test_client()

    resp = client.get(f"/User/{COMMANDER_IDENTITY}")

    assert resp.status_code == 401


# -- GET /Commanders ----------------------------------------------------------


def test_commander_gets_the_full_roster(tmp_path, teardown_ctx):
    ctx = build_context(tmp_path)
    teardown_ctx.append(ctx)
    client = build_app(ctx).test_client()

    resp = client.get("/Commanders", headers=auth_headers(COMMANDER_IDENTITY))

    assert resp.status_code == 200
    assert resp.get_json() == {"commanders": [{"telegram_identity": COMMANDER_IDENTITY}]}


def test_viewer_is_refused_the_roster(tmp_path, teardown_ctx):
    ctx = build_context(tmp_path)
    teardown_ctx.append(ctx)
    client = build_app(ctx).test_client()

    resp = client.get("/Commanders", headers=auth_headers(VIEWER_IDENTITY))

    assert resp.status_code == 403


def test_the_roster_excludes_viewers(tmp_path, teardown_ctx):
    ctx = build_context(tmp_path)
    teardown_ctx.append(ctx)
    ctx.deps.persistence.write_user("commander-2", "commander")
    client = build_app(ctx).test_client()

    resp = client.get("/Commanders", headers=auth_headers(COMMANDER_IDENTITY))

    identities = {c["telegram_identity"] for c in resp.get_json()["commanders"]}
    assert identities == {COMMANDER_IDENTITY, "commander-2"}
    assert VIEWER_IDENTITY not in identities
