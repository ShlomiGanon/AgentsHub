"""Admin HTML page templates assembled from chrome style, shell, and page bodies."""

from api.admin_api_pages import API_CLIENT_SCRIPT, API_CONSOLE_STYLE, EVENTS_BODY, PROFILES_BODY, PROTOCOLS_BODY
from api.admin_chrome_shell import (
    _ICON_EVENTS,
    _ICON_GROUPS,
    _ICON_PROFILES,
    _ICON_PROTOCOLS,
    _ICON_SERVER,
    _ICON_SIMULATOR,
    _ICON_TABLE,
    _ICON_USERS,
    _SHELL_CLOSE,
    _SHELL_OPEN,
)
from api.admin_chrome_style import _BOOTSTRAP_CSS_LINK, _DASHBOARD_STYLE, _LOGIN_STYLE, _SERVER_STYLE
from api.admin_simulator_assets import SIMULATOR_BODY, SIMULATOR_STYLE
from api.admin_tables import ADMIN_TABLES_EDIT_BODY, ADMIN_TABLES_LIST_BODY

_LOGIN_TEMPLATE = """<!DOCTYPE html>
<html lang="{{ lang }}" dir="{{ dir }}">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{{ t('admin.login_title') }}</title>
""" + _BOOTSTRAP_CSS_LINK + _LOGIN_STYLE + """
</head>
<body class="ls-login">

  <div class="login-brand">
    <img class="ls-logo" src="{{ url_for('static', filename='leadspotting-logo.gif') }}" alt="LeadSpotting">
  </div>
  <div class="login-card">
    <h1>{{ t('admin.login_title') }}</h1>
    <p class="subtitle">{{ t('admin.login_subtitle') }}</p>

    {% if lockout %}
      <div class="alert-console-error">
        {{ lockout.message }}
        <div class="lockout-progress-track" role="progressbar"
             aria-valuenow="{{ lockout.percent_elapsed }}" aria-valuemin="0" aria-valuemax="100"
             aria-label="Lockout time elapsed">
          <div class="lockout-progress-fill" style="width: {{ lockout.percent_elapsed }}%;"></div>
        </div>
      </div>
    {% else %}
      {% for category, message in get_flashed_messages(with_categories=true) %}
        <div class="alert-console-error">{{ message }}</div>
      {% endfor %}
    {% endif %}

    <form id="loginForm" method="post">
      <div class="field-group">
        <label class="form-label-console visually-hidden" for="username">{{ t('admin.username') }}</label>
        <input type="text" class="form-control-console" id="username" name="username" placeholder="{{ t('admin.username') }}" autofocus required>
      </div>
      <div class="field-group">
        <label class="form-label-console visually-hidden" for="password">{{ t('admin.password') }}</label>
        <input type="password" class="form-control-console" id="password" name="password" placeholder="{{ t('admin.password') }}" required>
      </div>
      <div class="login-actions">
        <button type="submit" class="btn-console-primary">{{ t('admin.sign_in') }}</button>
      </div>
    </form>

    <div class="status-pill"><span class="dot"></span>{{ t('admin.connected') }}</div>
  </div>

</body>
</html>
"""

_ACTING_IDENTITY_TEMPLATE = """<!DOCTYPE html>
<html lang="{{ lang }}" dir="{{ dir }}">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{{ t('admin.api.identity_title') }}</title>
""" + _BOOTSTRAP_CSS_LINK + _LOGIN_STYLE + """
</head>
<body class="ls-login">

  <div class="login-brand">
    <img class="ls-logo" src="{{ url_for('static', filename='leadspotting-logo.gif') }}" alt="LeadSpotting">
  </div>
  <div class="login-card identity-card">
    <h1>{{ t('admin.api.identity_title') }}</h1>
    <p class="subtitle">{{ t('admin.api.identity_subtitle') }}</p>

    {% for category, message in get_flashed_messages(with_categories=true) %}
      <div class="alert-console-error">{{ message }}</div>
    {% endfor %}

    {% if not api_users %}
      <p class="api-hint">{{ t('admin.api.no_identity') }}</p>
    {% else %}
      <p class="api-hint">{{ t('admin.api.identity_help') }}</p>
    {% endif %}

    <div class="identity-split">
      <form method="post" class="identity-pane">
        <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
        <h2>{{ t('admin.api.identity_profile_heading') }}</h2>
        <div class="field-group">
          <label class="form-label-console" for="api-identity-select">{{ t('admin.api.identity_label') }}</label>
          <select id="api-identity-select" name="api_identity" class="form-select form-select-console" {% if not api_users %}disabled{% endif %}>
            {% for user in api_users %}
              <option value="{{ user.telegram_identity }}">
                {{ user.full_name or t('admin.api.missing_name') }} — {{ user.telegram_identity }} ({{ user.permission_level }})
              </option>
            {% endfor %}
          </select>
        </div>
        <div class="login-actions">
          <button type="submit" class="btn-console-primary">{{ t('admin.api.identity_save') }}</button>
        </div>
      </form>
      <div class="identity-divider" role="separator"></div>
      <form method="post" class="identity-pane">
        <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
        <input type="hidden" name="use_system_admin" value="1">
        <h2>{{ t('admin.api.identity_admin_heading') }}</h2>
        <div class="login-actions">
          <button type="submit" class="btn-console-primary">{{ t('admin.api.identity_admin_button') }}</button>
        </div>
      </form>
    </div>
  </div>

</body>
</html>
"""

_MENU_TEMPLATE = """<!DOCTYPE html>
<html lang="{{ lang }}" dir="{{ dir }}"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{{ t('admin.menu_title') }}</title>""" + _BOOTSTRAP_CSS_LINK + _DASHBOARD_STYLE + """
</head>""" + _SHELL_OPEN + """
<div class="ls-page ls-home">
  <div class="ls-page-header">
    <div>
      <h1 class="ls-home-title">{{ t('admin.menu_title') }}</h1>
      <p class="subtitle ls-home-sub">{{ t('admin.menu_subtitle') }}</p>
    </div>
  </div>
  {% for category, message in get_flashed_messages(with_categories=true) %}
    <div class="alert-console{% if category == 'error' %}-error{% endif %} px-3 py-2 mb-4">{{ message }}</div>
  {% endfor %}
  <section class="ls-home-group">
    <h2 class="ls-home-group-title">{{ t('admin.home_group_configuration') }}</h2>
    <div class="ls-service-grid">
      <a class="ls-service-card" href="{{ url_for('admin.users') }}"><span class="ls-service-icon">""" + _ICON_USERS + """</span><h2>{{ t('admin.menu_users') }}</h2><span class="subtitle">{{ t('admin.users_subtitle') }}</span></a>
      <a class="ls-service-card" href="{{ url_for('admin.groups') }}"><span class="ls-service-icon">""" + _ICON_GROUPS + """</span><h2>{{ t('admin.menu_groups') }}</h2><span class="subtitle">{{ t('admin.groups_page_subtitle') }}</span></a>
    </div>
  </section>
  <section class="ls-home-group">
    <h2 class="ls-home-group-title">{{ t('admin.home_group_operations') }}</h2>
    <div class="ls-service-grid">
      <a class="ls-service-card is-featured" href="{{ url_for('admin.profiles') }}"><span class="ls-service-icon">""" + _ICON_PROFILES + """</span><h2>{{ t('admin.menu_profiles') }}</h2><span class="subtitle">{{ t('admin.profiles.subtitle') }}</span></a>
      <a class="ls-service-card is-featured" href="{{ url_for('admin.protocols') }}"><span class="ls-service-icon">""" + _ICON_PROTOCOLS + """</span><h2>{{ t('admin.menu_protocols') }}</h2><span class="subtitle">{{ t('admin.protocols.subtitle') }}</span></a>
      <a class="ls-service-card is-featured" href="{{ url_for('admin.events') }}"><span class="ls-service-icon">""" + _ICON_EVENTS + """</span><h2>{{ t('admin.menu_events') }}</h2><span class="subtitle">{{ t('admin.events.subtitle') }}</span></a>
    </div>
  </section>
  <section class="ls-home-group">
    <h2 class="ls-home-group-title">{{ t('admin.home_group_system') }}</h2>
    <div class="ls-service-grid">
      <a class="ls-service-card is-featured is-featured-navy" href="{{ url_for('admin.server') }}"><span class="ls-service-icon">""" + _ICON_SERVER + """</span><h2>{{ t('admin.menu_server') }}</h2><span class="subtitle">{{ t('admin.server_subtitle') }}</span></a>
      <a class="ls-service-card is-featured is-featured-navy" href="{{ url_for('admin.simulator') }}"><span class="ls-service-icon">""" + _ICON_SIMULATOR + """</span><h2>{{ t('admin.menu_simulator') }}</h2><span class="subtitle">{{ t('admin.simulator.subtitle') }}</span></a>
    </div>
  </section>
  {% if admin_tables %}
  <section class="ls-home-group">
    <h2 class="ls-home-group-title">{{ t('admin.home_group_data') }}</h2>
    <div class="ls-service-grid">
      {% for table in admin_tables %}
      <a class="ls-service-card is-live" href="{{ url_for('admin.admin_table_list', table_key=table.key) }}"><span class="ls-service-icon">""" + _ICON_TABLE + """</span><h2>{{ table.label }}</h2><span class="subtitle">{{ t('admin.tables.menu_subtitle') }}</span></a>
      {% endfor %}
    </div>
  </section>
  {% endif %}
</div>
""" + _SHELL_CLOSE

_BOT_SERVICE_PROVISION_SECTION = """  <section class="ls-section"><div class="block-console"><span class="block-label">{{ t('admin.bot_service_title') }}</span><p class="subtitle">{{ t('admin.bot_service_help', identity=bot_service_identity) }}</p><form method="post" action="{{ url_for('admin.provision_bot_service') }}"><input type="hidden" name="csrf_token" value="{{ csrf_token }}"><button class="btn btn-console">{{ t('admin.bot_service_button') }}</button></form></div></section>"""

_USERS_TEMPLATE = """<!DOCTYPE html>
<html lang="{{ lang }}" dir="{{ dir }}"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{{ t('admin.users_title') }}</title>""" + _BOOTSTRAP_CSS_LINK + _DASHBOARD_STYLE + """
</head>""" + _SHELL_OPEN + """
<div class="ls-page-wide">
  <div class="ls-page-header"><div>
  <h1>{{ t('admin.users_title') }}</h1>
  <p class="subtitle">{{ t('admin.users_subtitle') }}</p>
  </div></div>
  {% for category, message in get_flashed_messages(with_categories=true) %}<div class="alert-console{% if category == 'error' %}-error{% endif %} px-3 py-2 mb-4">{{ message }}</div>{% endfor %}
  <section class="ls-section">
    <div class="ls-table-toolbar"><h2 class="ls-section-title mb-0">{{ t('admin.users.list_heading') }}</h2><span class="ls-count">{{ t('admin.tables.record_count', count=users|length) }}</span></div>
    <div class="table-responsive ls-table-card"><table class="table table-console ls-compact-table mb-0"><thead><tr><th>{{ t('admin.col_identity') }}</th><th>{{ t('admin.col_full_name') }}</th><th>{{ t('admin.col_level') }}</th><th>{{ t('admin.col_status') }}</th><th>{{ t('admin.col_actions') }}</th></tr></thead><tbody>
    {% for user in users %}
    <tr>
      <td class="identity" title="{{ user.telegram_identity }}"><span class="identity-id">{{ user.telegram_identity }}</span>{% if user.telegram_identity == bot_service_identity %} <span class="tag">{{ t('admin.tag_bot_service') }}</span>{% endif %}</td>
      <td><form id="user-save-{{ loop.index }}" method="post" action="{{ url_for('admin.write_user') }}"><input type="hidden" name="csrf_token" value="{{ csrf_token }}"><input type="hidden" name="telegram_identity" value="{{ user.telegram_identity }}"><input type="text" name="full_name" value="{{ user.full_name }}" class="form-control form-control-console" maxlength="120" placeholder="{{ t('admin.col_full_name') }}"></form></td>
      <td><select name="permission_level" form="user-save-{{ loop.index }}" class="form-select form-select-console">{% for level in levels %}<option value="{{ level }}" {% if level == user.permission_level %}selected{% endif %}>{{ level }}</option>{% endfor %}</select></td>
      <td><span class="tag">{% if user.auto_register %}{{ t('admin.registration_automatic') }}{% else %}{{ t('admin.registration_approved') }}{% endif %}</span> <span class="tag">{% if safe_mode and user.auto_register %}{{ t('admin.registration_blocked') }}{% else %}{{ t('admin.registration_active') }}{% endif %}</span></td>
      <td><div class="ls-actions"><button class="btn btn-console btn-sm" form="user-save-{{ loop.index }}">{{ t('admin.save') }}</button>{% if user.auto_register %}<form method="post" action="{{ url_for('admin.approve_user', identity=user.telegram_identity) }}"><input type="hidden" name="csrf_token" value="{{ csrf_token }}"><button class="btn btn-console-primary btn-sm">{{ t('admin.approve_registration') }}</button></form>{% endif %}<form method="post" action="{{ url_for('admin.remove_user', identity=user.telegram_identity) }}" onsubmit="return confirm({{ t('admin.confirm_remove_user', identity=user.telegram_identity)|tojson|forceescape }});"><input type="hidden" name="csrf_token" value="{{ csrf_token }}"><button class="btn btn-console-danger btn-sm">{{ t('admin.remove') }}</button></form></div></td>
    </tr>
    {% endfor %}
    </tbody></table></div>
  </section>
  <section class="ls-section"><div class="block-console ls-create-panel"><span class="block-label">{{ t('admin.add_user') }}</span><form class="row g-3 align-items-end" method="post" action="{{ url_for('admin.write_user') }}"><input type="hidden" name="csrf_token" value="{{ csrf_token }}">
    <div class="col"><div class="form-label-console">{{ t('admin.col_identity') }}</div><input name="telegram_identity" class="form-control form-control-console" required></div>
    <div class="col"><div class="form-label-console">{{ t('admin.col_full_name') }}</div><input name="full_name" class="form-control form-control-console" maxlength="120"></div>
    <div class="col-auto"><div class="form-label-console">{{ t('admin.col_level') }}</div><select name="permission_level" class="form-select form-select-console">{% for level in levels %}<option value="{{ level }}">{{ level }}</option>{% endfor %}</select></div>
    <div class="col-auto"><button class="btn btn-console-primary">{{ t('admin.add') }}</button></div></form></div></section>
""" + _BOT_SERVICE_PROVISION_SECTION + """
</div>
""" + _SHELL_CLOSE

_GROUPS_TEMPLATE = """<!DOCTYPE html>
<html lang="{{ lang }}" dir="{{ dir }}"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>{{ t('admin.groups_title') }}</title>""" + _BOOTSTRAP_CSS_LINK + _DASHBOARD_STYLE + """</head>
""" + _SHELL_OPEN + """
<div class="ls-page-wide"><div class="ls-page-header"><div><h1>{{ t('admin.groups_title') }}</h1><p class="subtitle">{{ t('admin.groups_page_subtitle') }}</p></div></div>
{% for category, message in get_flashed_messages(with_categories=true) %}<div class="alert-console{% if category == 'error' %}-error{% endif %} px-3 py-2 mb-4">{{ message }}</div>{% endfor %}
<section class="ls-section">
  <div class="ls-table-toolbar"><h2 class="ls-section-title mb-0">{{ t('admin.groups.list_heading') }}</h2><span class="ls-count">{{ t('admin.tables.record_count', count=groups|length) }}</span></div>
  <div class="table-responsive ls-table-card"><table class="table table-console ls-compact-table mb-0"><thead><tr><th>{{ t('admin.col_chat_id') }}</th><th>{{ t('admin.col_label') }}</th><th>{{ t('admin.col_routed_to') }}</th><th>{{ t('admin.col_attendance_check') }}</th><th>{{ t('admin.col_attendance_hour') }}</th><th>{{ t('admin.col_status') }}</th><th>{{ t('admin.col_actions') }}</th></tr></thead><tbody>
  {% for group in groups %}
  <tr>
    <td class="identity" title="{{ group.chat_id }}"><span class="identity-id">{{ group.chat_id }}</span></td>
    <td><form id="group-save-{{ loop.index }}" method="post" action="{{ url_for('admin.write_group') }}"><input type="hidden" name="csrf_token" value="{{ csrf_token }}"><input type="hidden" name="chat_id" value="{{ group.chat_id }}"><input name="label" value="{{ group.label }}" maxlength="200" class="form-control form-control-console" placeholder="{{ t('admin.col_label') }}"></form></td>
    <td><select name="agent_name" form="group-save-{{ loop.index }}" class="form-select form-select-console">{% for agent_name in routable_agents %}<option value="{{ agent_name }}" {% if agent_name == group.agent_name %}selected{% endif %}>{{ agent_name }}</option>{% endfor %}</select></td>
    <td><input type="hidden" name="attendance_check_enabled" value="0" form="group-save-{{ loop.index }}"><input type="checkbox" name="attendance_check_enabled" value="1" class="form-check-input" form="group-save-{{ loop.index }}" {% if group.attendance_check_enabled %}checked{% endif %}></td>
    <td><input type="number" name="attendance_check_hour" min="0" max="23" value="{{ group.attendance_check_hour }}" class="form-control form-control-console" form="group-save-{{ loop.index }}"></td>
    <td><span class="tag">{% if group.auto_register %}{{ t('admin.registration_automatic') }}{% else %}{{ t('admin.registration_approved') }}{% endif %}</span> <span class="tag">{% if safe_mode and group.auto_register %}{{ t('admin.registration_blocked') }}{% else %}{{ t('admin.registration_active') }}{% endif %}</span></td>
    <td>
      <div class="ls-actions">
        <button class="btn btn-console btn-sm" form="group-save-{{ loop.index }}">{{ t('admin.save') }}</button>
        {% if group.auto_register %}<form method="post" action="{{ url_for('admin.approve_group', chat_id=group.chat_id) }}"><input type="hidden" name="csrf_token" value="{{ csrf_token }}"><button class="btn btn-console-primary btn-sm">{{ t('admin.approve_registration') }}</button></form>{% endif %}
        <form method="post" action="{{ url_for('admin.remove_group', chat_id=group.chat_id) }}" onsubmit="return confirm({{ t('admin.confirm_remove_group', chat_id=group.chat_id)|tojson|forceescape }});"><input type="hidden" name="csrf_token" value="{{ csrf_token }}"><button class="btn btn-console-danger btn-sm">{{ t('admin.remove') }}</button></form>
      </div>
      <form class="d-flex gap-2 mt-2" method="post" action="{{ url_for('admin.rename_group', chat_id=group.chat_id) }}"><input type="hidden" name="csrf_token" value="{{ csrf_token }}"><input name="new_chat_id" class="form-control form-control-console form-control-sm" placeholder="{{ t('admin.new_chat_id_placeholder') }}" title="{{ t('admin.group_rename_help') }}"><button class="btn btn-console btn-sm" title="{{ t('admin.group_rename_help') }}">{{ t('admin.rename_group') }}</button></form>
    </td>
  </tr>
  {% else %}<tr><td class="ls-empty" colspan="7">{{ t('admin.no_groups') }}</td></tr>{% endfor %}
  </tbody></table></div>
</section>
<section class="ls-section"><div class="block-console ls-create-panel"><span class="block-label">{{ t('admin.add_group') }}</span><p class="subtitle">{{ t('admin.add_group_help', main_agent='main_agent') }}</p><form class="row g-3 align-items-end" method="post" action="{{ url_for('admin.write_group') }}"><input type="hidden" name="csrf_token" value="{{ csrf_token }}"><div class="col"><div class="form-label-console">{{ t('admin.col_chat_id') }}</div><input name="chat_id" class="form-control form-control-console" placeholder="-1001234567890" required></div><div class="col"><div class="form-label-console">{{ t('admin.col_label') }}</div><input name="label" class="form-control form-control-console" maxlength="200"></div><div class="col-auto"><select name="agent_name" class="form-select form-select-console">{% for agent_name in routable_agents %}<option value="{{ agent_name }}">{{ agent_name }}</option>{% endfor %}</select></div><div class="col-auto"><div class="form-check mb-1"><input type="hidden" name="attendance_check_enabled" value="0"><input class="form-check-input" type="checkbox" name="attendance_check_enabled" value="1" id="new-attendance-enabled"><label class="form-check-label" for="new-attendance-enabled">{{ t('admin.col_attendance_check') }}</label></div></div><div class="col-auto"><div class="form-label-console">{{ t('admin.col_attendance_hour') }}</div><input name="attendance_check_hour" type="number" min="0" max="23" value="8" class="form-control form-control-console"></div><div class="col-auto"><button class="btn btn-console-primary">{{ t('admin.add') }}</button></div></form></div></section>
</div>
""" + _SHELL_CLOSE

_PROFILES_TEMPLATE = """<!DOCTYPE html><html lang="{{ lang }}" dir="{{ dir }}"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>{{ t('admin.profiles.title') }}</title>""" + _BOOTSTRAP_CSS_LINK + _DASHBOARD_STYLE + API_CONSOLE_STYLE + """</head>""" + _SHELL_OPEN + PROFILES_BODY + _SHELL_CLOSE

_PROTOCOLS_TEMPLATE = """<!DOCTYPE html><html lang="{{ lang }}" dir="{{ dir }}"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>{{ t('admin.protocols.title') }}</title>""" + _BOOTSTRAP_CSS_LINK + _DASHBOARD_STYLE + API_CONSOLE_STYLE + """</head>""" + _SHELL_OPEN + PROTOCOLS_BODY + _SHELL_CLOSE

_ADMIN_TABLES_LIST_TEMPLATE = """<!DOCTYPE html><html lang="{{ lang }}" dir="{{ dir }}"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>{{ table.label }}</title>""" + _BOOTSTRAP_CSS_LINK + _DASHBOARD_STYLE + """</head>""" + _SHELL_OPEN + ADMIN_TABLES_LIST_BODY + _SHELL_CLOSE

_ADMIN_TABLES_EDIT_TEMPLATE = """<!DOCTYPE html><html lang="{{ lang }}" dir="{{ dir }}"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>{{ table.label }}</title>""" + _BOOTSTRAP_CSS_LINK + _DASHBOARD_STYLE + """</head>""" + _SHELL_OPEN + ADMIN_TABLES_EDIT_BODY + _SHELL_CLOSE

_EVENTS_TEMPLATE = """<!DOCTYPE html><html lang="{{ lang }}" dir="{{ dir }}"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>{{ t('admin.events.title') }}</title>""" + _BOOTSTRAP_CSS_LINK + _DASHBOARD_STYLE + API_CONSOLE_STYLE + """</head>""" + _SHELL_OPEN + EVENTS_BODY + _SHELL_CLOSE

_SERVER_TEMPLATE = """<!DOCTYPE html>
<html lang="{{ lang }}" dir="{{ dir }}"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>{{ t('admin.server_title') }}</title>""" + _BOOTSTRAP_CSS_LINK + _DASHBOARD_STYLE + API_CONSOLE_STYLE + _SERVER_STYLE + """</head>
""" + _SHELL_OPEN + """
<div class="ls-page ls-server"><div class="ls-page-header"><div><h1>{{ t('admin.server_title') }}</h1>
<p class="subtitle">{{ t('admin.server_subtitle') }}</p></div></div>
{% for category, message in get_flashed_messages(with_categories=true) %}<div class="alert-console{% if category == 'error' %}-error{% endif %} px-3 py-2 mb-4">{{ message }}</div>{% endfor %}
{% if status.get('last_error') %}<div class="alert-console-error px-3 py-2 mb-4">{{ status.get('last_error') }}</div>{% endif %}
{% if not supervisor %}<div class="alert-console-error px-3 py-2 mb-4">{{ t('admin.server_unavailable') }}</div>{% endif %}
<section class="ls-section">
  <h2 class="ls-section-title">{{ t('admin.server_status_heading') }}</h2>
  <div class="block-console mode-panel {% if safe_mode %}safe-active{% else %}open-active{% endif %}">
    <div class="mode-layout">
      <div class="mode-visual"><div class="mode-orbit" aria-hidden="true"><div class="mode-shield">{% if safe_mode %}SAFE{% else %}OPEN{% endif %}</div></div></div>
      <div>
        <span class="block-label">SAFE_MODE</span>
        <div class="mode-title"><span class="mode-state-dot"></span><h2 class="mb-0">{% if safe_mode %}{{ t('admin.server_safe_on') }}{% else %}{{ t('admin.server_safe_off') }}{% endif %}</h2><span class="mode-value">SAFE_MODE = {{ safe_mode|string|lower }}</span></div>
        <p class="subtitle mt-2 mb-0">{% if safe_mode %}{{ t('admin.server_safe_on_help') }}{% else %}{{ t('admin.server_safe_off_help') }}{% endif %}</p>
        <div class="mode-counts">
          <span class="tag">{% if supervisor %}{{ t('admin.server.connection_ok') }}{% else %}{{ t('admin.server.connection_limited') }}{% endif %}</span>
          <span class="tag">{{ t('admin.server_pending_users', count=automatic_users) }}</span>
          <span class="tag">{{ t('admin.server_pending_groups', count=automatic_groups) }}</span>
        </div>
        <div class="mode-actions" data-confirm="{{ t('admin.server_safe_confirm', users=automatic_users, groups=automatic_groups) }}">
          <button type="button" data-safe-mode="false" class="mode-choice {% if not safe_mode %}active{% endif %}"><strong>{{ t('admin.server_choose_open') }}</strong><small>{{ t('admin.server_choose_open_help') }}</small></button>
          <button type="button" data-safe-mode="true" class="mode-choice {% if safe_mode %}active{% endif %}"><strong>{{ t('admin.server_choose_safe') }}</strong><small>{{ t('admin.server_choose_safe_help') }}</small></button>
        </div>
        <div id="safe-mode-feedback" class="api-hint mt-3" role="status" aria-live="polite"></div>
      </div>
    </div>
  </div>
</section>
<section class="ls-section">
  <h2 class="ls-section-title">{{ t('admin.server_actions_heading') }}</h2>
  <div class="block-console ls-action-card"><span class="block-label">{{ t('admin.server_profile') }}</span>
  <p class="subtitle">{{ t('admin.server_active_profile', profile=active_profile) }}</p>
  <form class="row g-3 align-items-end" method="post" action="{{ url_for('admin.switch_profile') }}"><input type="hidden" name="csrf_token" value="{{ csrf_token }}"><div class="col"><select class="form-select form-select-console" name="profile_module" {% if not supervisor %}disabled{% endif %}>{% for profile in profiles %}<option value="{{ profile.module_path }}" {% if profile.module_path == active_module %}selected{% endif %}>{{ profile.profile_name }} — {{ profile.module_path }} ({{ profile.api_port }})</option>{% endfor %}</select></div><div class="col-auto"><button class="btn btn-console-primary" {% if not supervisor %}disabled{% endif %}>{{ t('admin.server_load_profile') }}</button></div></form>
  <p class="subtitle mt-3 mb-0">{{ t('admin.server_restart_required') }}</p></div>
  <div class="block-console ls-action-card ls-danger-zone"><span class="block-label">{{ t('admin.server_reset') }}</span><p class="subtitle">{{ t('admin.server_reset_help') }}</p><form method="post" action="{{ url_for('admin.reset_server') }}" onsubmit="return confirm({{ t('admin.server_reset_confirm')|tojson|forceescape }});"><input type="hidden" name="csrf_token" value="{{ csrf_token }}"><input type="hidden" name="confirm" value="yes"><button class="btn btn-console-danger" {% if not supervisor %}disabled{% endif %}>{{ t('admin.server_reset_button') }}</button></form></div>
</section>
</div>""" + API_CLIENT_SCRIPT + """
<script>
(() => {
  const controls = document.querySelector('.mode-actions');
  const feedback = document.getElementById('safe-mode-feedback');
  const currentMode = {{ safe_mode|tojson }};
  const changingText = {{ t('admin.server_safe_changing')|tojson }};
  const changedText = {{ t('admin.server_safe_changed')|tojson }};
  const failedText = {{ t('admin.server_safe_change_failed')|tojson }};
  controls.querySelectorAll('[data-safe-mode]').forEach(button => {
    button.addEventListener('click', async () => {
      const requestedMode = button.dataset.safeMode === 'true';
      if (requestedMode === currentMode) return;
      if (requestedMode && !window.confirm(controls.dataset.confirm)) return;
      controls.querySelectorAll('button').forEach(item => { item.disabled = true; });
      feedback.textContent = changingText;
      const result = await AdminApi.call('PUT', '/SYSTEM', {safe_mode: requestedMode}, 'safe-mode-api-output');
      if (result.ok) {
        feedback.textContent = changedText;
        window.setTimeout(() => window.location.reload(), 450);
      } else {
        feedback.textContent = failedText;
        controls.querySelectorAll('button').forEach(item => { item.disabled = false; });
      }
    });
  });
})();
</script>
<pre id="safe-mode-api-output" class="d-none" hidden></pre>
""" + _SHELL_CLOSE

_RESTART_POLL_MS = 1500

_RESTART_TIMEOUT_MS = 60000

_SERVER_WAIT_TEMPLATE = """<!DOCTYPE html><html lang="{{ lang }}" dir="{{ dir }}"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>{{ t('admin.server_restarting') }}</title>""" + _BOOTSTRAP_CSS_LINK + _DASHBOARD_STYLE + """</head>""" + _SHELL_OPEN + """<div class="ls-page"><div class="block-console">
<div id="wait-status"><h1>{{ t('admin.server_restarting') }}</h1><p class="subtitle">{{ t('admin.server_restarting_help') }}</p></div>
<div id="wait-timeout" hidden><h1>{{ t('admin.server_restart_timeout_title') }}</h1><p class="subtitle">{{ t('admin.server_restart_timeout_help') }}</p><p><a id="target-link" href="{{ target_url }}">{{ t('admin.server_restart_timeout_target_link') }}</a></p><p><a id="old-link" href="{{ old_url }}">{{ t('admin.server_restart_timeout_previous_link') }}</a></p></div>
</div></div><script>
(function(){
  const oldUrl = {{ old_url|tojson }};
  const targetUrl = {{ target_url|tojson }};
  const deadline = Date.now() + {{ timeout_ms }};
  let phase = 'old-down';
  async function reachable(url) {
    try { await fetch(url, {mode: 'no-cors', credentials: 'include', cache: 'no-store'}); return true; }
    catch (error) { return false; }
  }
  async function tick() {
    if (Date.now() >= deadline) {
      document.getElementById('wait-status').hidden = true;
      document.getElementById('wait-timeout').hidden = false;
      return;
    }
    if (phase === 'old-down') {
      if (!(await reachable(oldUrl))) { phase = 'new-up'; }
    } else if (await reachable(targetUrl)) {
      window.location.href = targetUrl;
      return;
    }
    setTimeout(tick, {{ poll_ms }});
  }
  setTimeout(tick, {{ poll_ms }});
})();
</script>
""" + _SHELL_CLOSE

_SIMULATOR_TEMPLATE = """<!DOCTYPE html>
<html lang="{{ lang }}" dir="{{ dir }}">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{{ t('admin.simulator.title') }}</title>
""" + _BOOTSTRAP_CSS_LINK + _DASHBOARD_STYLE + SIMULATOR_STYLE + """
</head>
""" + _SHELL_OPEN + SIMULATOR_BODY + _SHELL_CLOSE
