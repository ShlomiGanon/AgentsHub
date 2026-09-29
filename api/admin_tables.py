"""Generic admin-panel table rendering (docs/Admin_Tables_Plan.md) -- page bodies + form
validation for whatever `AdminTable`s the active profile declares (`profiles.admin_tables`).

Follows the same split `api/admin_api_pages.py`/`api/admin_simulator.py` already use: this
module supplies template bodies and pure-Python helpers, never a Flask `Blueprint` of its own --
the actual `/admin/tables/...` routes are registered inside `api/admin.py`'s
`build_admin_blueprint`, reusing that module's own session/CSRF/flash machinery rather than
duplicating it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from profiles.admin_tables import AdminTable
    from profiles.contracts import LoadedProfile


class AdminFormError(Exception):
    """A submitted admin-table edit form failed generic, column-metadata-driven validation
    (required field missing, a `select` value not in its own `choices`) -- purely mechanical,
    never a business rule (those live in the store method `write_fn` calls, and raise their own
    persistence-layer exception instead)."""


def find_admin_table(loaded_profile: "LoadedProfile", table_key: str) -> "AdminTable | None":
    for table in loaded_profile.admin_tables:
        if table.key == table_key:
            return table
    return None


def parse_admin_table_form(table: "AdminTable", form) -> dict:
    """Every editable column's submitted value, validated against its own `AdminColumn`
    metadata only -- required-ness and, for `kind="select"`, membership in `choices`. Read-only
    columns (`editable=False`) are never accepted from the form, even if present, since they
    are either the primary key (routed separately, via the URL) or system-stamped."""

    values: dict = {}
    for column in table.columns:
        if not column.editable:
            continue
        raw = form.get(column.name)
        if raw is not None:
            raw = raw.strip()
        if not raw:
            if column.required:
                raise AdminFormError(f"{column.label} is required.")
            continue
        if column.kind == "select" and column.choices and raw not in column.choices:
            raise AdminFormError(f"{column.label}: {raw!r} is not one of {', '.join(column.choices)}.")
        if column.kind == "number":
            try:
                raw = int(raw)
            except ValueError:
                raise AdminFormError(f"{column.label} must be a whole number.") from None
        values[column.name] = raw
    return values


# -- Templates ----------------------------------------------------------------------------------
#
# Body-only fragments, assembled into full pages by api/admin.py the same way PROFILES_BODY/
# PROTOCOLS_BODY/EVENTS_BODY (api/admin_api_pages.py) already are -- one shared <head>/style,
# many page bodies.

ADMIN_TABLES_LIST_BODY = """<body><div class="container container-narrow">
  <div class="d-flex justify-content-between align-items-baseline mb-1"><h1>{{ table.label }}</h1>
    <a class="nav-console" href="{{ url_for('admin.dashboard') }}">{{ t('admin.nav_menu') }}</a></div>
  <p class="subtitle mb-4">{{ t('admin.tables.list_subtitle') }}</p>
  {% for category, message in get_flashed_messages(with_categories=true) %}
    <div class="alert-console{% if category == 'error' %}-error{% endif %} px-3 py-2 mb-4">{{ message }}</div>
  {% endfor %}
  <table class="table table-console mb-4">
    <thead><tr>
      {% for column in table.columns %}<th>{{ column.label }}</th>{% endfor %}
      <th></th>
    </tr></thead>
    <tbody>
      {% for row in rows %}
      <tr>
        {% for column in table.columns %}<td>{{ row.get(column.name, "") }}</td>{% endfor %}
        <td class="text-end">
          <a class="btn btn-console btn-sm" href="{{ url_for('admin.admin_table_edit', table_key=table.key, row_id=row.get(table.primary_key)) }}">{{ t('admin.tables.edit') }}</a>
          {% if table.delete_fn %}
          <form class="d-inline" method="post" action="{{ url_for('admin.admin_table_delete', table_key=table.key, row_id=row.get(table.primary_key)) }}"
                onsubmit="return confirm({{ t('admin.tables.confirm_delete')|tojson|forceescape }});">
            <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
            <button type="submit" class="btn btn-console-danger btn-sm">{{ t('admin.remove') }}</button>
          </form>
          {% endif %}
        </td>
      </tr>
      {% else %}
      <tr><td colspan="{{ table.columns|length + 1 }}">{{ t('admin.tables.no_rows') }}</td></tr>
      {% endfor %}
    </tbody>
  </table>
</div></body>"""


ADMIN_TABLES_EDIT_BODY = """<body><div class="container container-narrow">
  <div class="d-flex justify-content-between align-items-baseline mb-1"><h1>{{ t('admin.tables.edit_title', label=table.label) }}</h1>
    <a class="nav-console" href="{{ url_for('admin.admin_table_list', table_key=table.key) }}">{{ t('admin.tables.back_to_list') }}</a></div>
  {% for category, message in get_flashed_messages(with_categories=true) %}
    <div class="alert-console{% if category == 'error' %}-error{% endif %} px-3 py-2 mb-4">{{ message }}</div>
  {% endfor %}
  <form method="post" action="{{ url_for('admin.admin_table_edit', table_key=table.key, row_id=row_id) }}" class="block-console">
    <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
    {% for column in table.columns %}
    <div class="mb-3">
      <div class="form-label-console">{{ column.label }}</div>
      {% if not column.editable %}
        <div class="form-control form-control-console" style="background:var(--panel);">{{ row.get(column.name, "") }}</div>
      {% elif column.kind == "select" %}
        <select name="{{ column.name }}" class="form-select form-select-console" {% if column.required %}required{% endif %}>
          {% for choice in column.choices %}<option value="{{ choice }}" {% if row.get(column.name) == choice %}selected{% endif %}>{{ choice }}</option>{% endfor %}
        </select>
      {% elif column.kind == "checkbox" %}
        <input type="checkbox" name="{{ column.name }}" value="1" {% if row.get(column.name) %}checked{% endif %}>
      {% else %}
        <input type="{% if column.kind == 'number' %}number{% elif column.kind == 'datetime' %}text{% else %}text{% endif %}"
               name="{{ column.name }}" value="{{ row.get(column.name, "") if row.get(column.name) is not none else '' }}"
               class="form-control form-control-console" {% if column.required %}required{% endif %}>
      {% endif %}
    </div>
    {% endfor %}
    <button type="submit" class="btn btn-console-primary">{{ t('admin.save') }}</button>
  </form>
</div></body>"""
