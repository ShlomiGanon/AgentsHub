"""The generic admin-table mechanism (api/admin_tables.py + the /admin/tables/... routes in
api/admin.py) -- list/edit/new/delete rendering and form validation, against a fake AdminTable
built from plain dicts. No real profile store is involved; profiles/response_team.py's and
profiles/firefighting.py's own real wirings are covered by their own test files."""

import types

import pytest

from agents import adapter
from api.admin_tables import AdminFormError, parse_admin_table_form
from api.app import build_app
from profiles.admin_tables import AdminColumn, AdminTable
from tests.api_fakes import build_context

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


def _fake_table(rows, *, delete_fn=None, allow_create=False):
    def list_fn(deps):
        return list(rows)

    def get_fn(deps, row_id):
        return next((r for r in rows if r["id"] == row_id), None)

    def write_fn(deps, row):
        existing = next((r for r in rows if r["id"] == row["id"]), None)
        if existing is not None:
            existing.update(row)
        else:
            rows.append(row)

    return AdminTable(
        key="widgets",
        label="Widgets",
        primary_key="id",
        columns=(
            AdminColumn("id", "ID", editable=False),
            AdminColumn("name", "Name", required=True),
            AdminColumn("status", "Status", kind="select", choices=("on", "off")),
        ),
        list_fn=list_fn, get_fn=get_fn, write_fn=write_fn,
        delete_fn=delete_fn, allow_create=allow_create,
    )


def _client(tmp_path, teardown_ctx, admin_tables):
    ctx = build_context(tmp_path, admin_tables=admin_tables)
    teardown_ctx.append(ctx)
    return build_app(ctx).test_client()


def _login(client):
    return client.post("/admin/login", data={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD}, follow_redirects=False)


def _csrf_token(client):
    resp = client.get("/admin/")
    import re
    match = re.search(r'name="csrf_token" value="([^"]+)"', resp.get_data(as_text=True))
    return match.group(1)


# -- Generic form validation (no Flask involved) -------------------------------------------


def test_parse_admin_table_form_accepts_a_valid_select_value():
    rows = []
    table = _fake_table(rows)

    values = parse_admin_table_form(table, {"name": "Widget A", "status": "on"})

    assert values == {"name": "Widget A", "status": "on"}


def test_parse_admin_table_form_rejects_a_select_value_not_in_choices():
    table = _fake_table([])

    with pytest.raises(AdminFormError):
        parse_admin_table_form(table, {"name": "Widget A", "status": "sideways"})


def test_parse_admin_table_form_rejects_a_missing_required_field():
    table = _fake_table([])

    with pytest.raises(AdminFormError):
        parse_admin_table_form(table, {"status": "on"})


def test_parse_admin_table_form_never_accepts_a_readonly_column():
    table = _fake_table([])

    values = parse_admin_table_form(table, {"id": "999", "name": "Widget A"})

    assert "id" not in values


# -- Routes, end to end via the Flask test client ------------------------------------------


def test_list_page_requires_a_session(tmp_path, teardown_ctx, _admin_env):
    client = _client(tmp_path, teardown_ctx, admin_tables=(_fake_table([]),))

    resp = client.get("/admin/tables/widgets")

    assert resp.status_code in (302, 303)


def test_unknown_table_key_redirects_to_dashboard(tmp_path, teardown_ctx, _admin_env):
    client = _client(tmp_path, teardown_ctx, admin_tables=(_fake_table([]),))
    _login(client)

    resp = client.get("/admin/tables/does-not-exist", follow_redirects=False)

    assert resp.status_code in (302, 303)
    assert resp.headers["Location"].endswith("/admin/")


def test_list_page_renders_every_declared_row(tmp_path, teardown_ctx, _admin_env):
    rows = [{"id": "1", "name": "Widget A", "status": "on"}, {"id": "2", "name": "Widget B", "status": "off"}]
    client = _client(tmp_path, teardown_ctx, admin_tables=(_fake_table(rows),))
    _login(client)

    resp = client.get("/admin/tables/widgets")

    body = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "Widget A" in body
    assert "Widget B" in body


def test_dashboard_menu_links_to_every_declared_table(tmp_path, teardown_ctx, _admin_env):
    client = _client(tmp_path, teardown_ctx, admin_tables=(_fake_table([]),))
    _login(client)

    resp = client.get("/admin/")

    assert "/admin/tables/widgets" in resp.get_data(as_text=True)


def test_edit_page_prefills_the_current_row(tmp_path, teardown_ctx, _admin_env):
    rows = [{"id": "1", "name": "Widget A", "status": "on"}]
    client = _client(tmp_path, teardown_ctx, admin_tables=(_fake_table(rows),))
    _login(client)

    resp = client.get("/admin/tables/widgets/edit/1")

    assert "Widget A" in resp.get_data(as_text=True)


def test_edit_page_for_a_missing_row_redirects_to_the_list(tmp_path, teardown_ctx, _admin_env):
    client = _client(tmp_path, teardown_ctx, admin_tables=(_fake_table([]),))
    _login(client)

    resp = client.get("/admin/tables/widgets/edit/does-not-exist", follow_redirects=False)

    assert resp.status_code in (302, 303)


def test_edit_submission_writes_through_and_takes_effect_immediately(tmp_path, teardown_ctx, _admin_env):
    rows = [{"id": "1", "name": "Widget A", "status": "on"}]
    client = _client(tmp_path, teardown_ctx, admin_tables=(_fake_table(rows),))
    _login(client)
    token = _csrf_token(client)

    resp = client.post(
        "/admin/tables/widgets/edit/1",
        data={"csrf_token": token, "name": "Widget A (renamed)", "status": "off"},
        follow_redirects=False,
    )

    assert resp.status_code in (302, 303)
    assert rows[0]["name"] == "Widget A (renamed)"
    assert rows[0]["status"] == "off"


def test_edit_submission_without_a_csrf_token_is_rejected_and_does_not_write(tmp_path, teardown_ctx, _admin_env):
    rows = [{"id": "1", "name": "Widget A", "status": "on"}]
    client = _client(tmp_path, teardown_ctx, admin_tables=(_fake_table(rows),))
    _login(client)

    client.post("/admin/tables/widgets/edit/1", data={"name": "Hijacked"}, follow_redirects=False)

    assert rows[0]["name"] == "Widget A"


def test_edit_submission_with_an_invalid_select_value_flashes_and_does_not_write(tmp_path, teardown_ctx, _admin_env):
    rows = [{"id": "1", "name": "Widget A", "status": "on"}]
    client = _client(tmp_path, teardown_ctx, admin_tables=(_fake_table(rows),))
    _login(client)
    token = _csrf_token(client)

    client.post(
        "/admin/tables/widgets/edit/1",
        data={"csrf_token": token, "name": "Widget A", "status": "sideways"},
        follow_redirects=False,
    )

    assert rows[0]["status"] == "on"


def test_delete_button_is_absent_when_the_table_declares_no_delete_fn(tmp_path, teardown_ctx, _admin_env):
    rows = [{"id": "1", "name": "Widget A", "status": "on"}]
    client = _client(tmp_path, teardown_ctx, admin_tables=(_fake_table(rows),))
    _login(client)

    resp = client.get("/admin/tables/widgets")

    assert "admin.remove" not in resp.get_data(as_text=True)  # no delete form rendered


def test_delete_route_removes_the_row_when_delete_fn_is_declared(tmp_path, teardown_ctx, _admin_env):
    rows = [{"id": "1", "name": "Widget A", "status": "on"}]

    def delete_fn(deps, row_id):
        rows[:] = [r for r in rows if r["id"] != row_id]

    client = _client(tmp_path, teardown_ctx, admin_tables=(_fake_table(rows, delete_fn=delete_fn),))
    _login(client)
    token = _csrf_token(client)

    resp = client.post("/admin/tables/widgets/1/delete", data={"csrf_token": token}, follow_redirects=False)

    assert resp.status_code in (302, 303)
    assert rows == []


def test_delete_route_404_equivalent_when_no_delete_fn_declared(tmp_path, teardown_ctx, _admin_env):
    rows = [{"id": "1", "name": "Widget A", "status": "on"}]
    client = _client(tmp_path, teardown_ctx, admin_tables=(_fake_table(rows),))
    _login(client)
    token = _csrf_token(client)

    client.post("/admin/tables/widgets/1/delete", data={"csrf_token": token}, follow_redirects=False)

    assert rows == [{"id": "1", "name": "Widget A", "status": "on"}]
