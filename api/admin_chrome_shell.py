"""Admin chrome shell: nav icons and the authenticated page frame."""


_ICON_HOME = '<svg class="ls-icon" viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="3" width="8" height="8" rx="1.5"/><rect x="13" y="3" width="8" height="8" rx="1.5"/><rect x="3" y="13" width="8" height="8" rx="1.5"/><rect x="13" y="13" width="8" height="8" rx="1.5"/></svg>'

_ICON_PROFILES = '<svg class="ls-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M4 8h16"/><path d="M4 12h16"/><path d="M4 16h16"/><path d="M8 6v4"/><path d="M12 10v4"/><path d="M16 14v4"/></svg>'

_ICON_PROTOCOLS = '<svg class="ls-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M8 6h12"/><path d="M8 12h12"/><path d="M8 18h12"/><circle cx="4.5" cy="6" r="1.2" fill="currentColor" stroke="none"/><circle cx="4.5" cy="12" r="1.2" fill="currentColor" stroke="none"/><circle cx="4.5" cy="18" r="1.2" fill="currentColor" stroke="none"/></svg>'

_ICON_EVENTS = '<svg class="ls-icon" viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18"/><path d="M8 3v4"/><path d="M16 3v4"/></svg>'

_ICON_USERS = '<svg class="ls-icon" viewBox="0 0 24 24" aria-hidden="true"><circle cx="9" cy="8" r="3"/><path d="M3.5 19c.8-3 2.8-4.5 5.5-4.5S13.7 16 14.5 19"/><circle cx="17" cy="9" r="2.4"/><path d="M16.2 14.6c2.2.3 3.8 1.6 4.3 4.4"/></svg>'

_ICON_GROUPS = '<svg class="ls-icon" viewBox="0 0 24 24" aria-hidden="true"><circle cx="8" cy="9" r="2.5"/><circle cx="16" cy="9" r="2.5"/><circle cx="12" cy="8" r="2.7"/><path d="M4 19c.7-2.6 2.4-4 5-4"/><path d="M20 19c-.7-2.6-2.4-4-5-4"/><path d="M8.5 19c.7-2.4 2-3.6 3.5-3.6s2.8 1.2 3.5 3.6"/></svg>'

_ICON_SIMULATOR = '<svg class="ls-icon" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M10 8.5v7l6-3.5z" fill="currentColor" stroke="none"/></svg>'

_ICON_SERVER = '<svg class="ls-icon" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M3 12h18"/><path d="M12 3c3 3.2 4.5 6.2 4.5 9S15 17.8 12 21"/><path d="M12 3c-3 3.2-4.5 6.2-4.5 9S9 17.8 12 21"/></svg>'

_ICON_TABLE = '<svg class="ls-icon" viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 9h18"/><path d="M3 14h18"/><path d="M9 9v11"/><path d="M15 9v11"/></svg>'

_ICON_TOGGLE = '<svg class="ls-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M15 6l-6 6 6 6"/></svg>'

_ICON_PERSON = '<svg class="ls-icon" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="8" r="3.2"/><path d="M5 19c1-3.4 3.4-5 7-5s6 1.6 7 5"/></svg>'

_SHELL_SCRIPT = """
<script>
(function () {
  const root = document.querySelector('.ls-app');
  if (!root) return;
  const key = 'admin-sidebar-collapsed';
  if (window.localStorage.getItem(key) === '1') root.classList.add('ls-sidebar-collapsed');
  const toggle = document.getElementById('ls-sidebar-toggle');
  if (toggle) toggle.addEventListener('click', function () {
    root.classList.toggle('ls-sidebar-collapsed');
    window.localStorage.setItem(key, root.classList.contains('ls-sidebar-collapsed') ? '1' : '0');
  });
  document.querySelectorAll('[data-ls-tabs]').forEach(function (group) {
    const buttons = Array.from(group.querySelectorAll('[data-tab]'));
    const scope = group.closest('.ls-page, .ls-page-wide') || document;
    function show(name) {
      buttons.forEach(function (button) { button.classList.toggle('is-active', button.dataset.tab === name); });
      scope.querySelectorAll('[data-panel]').forEach(function (panel) {
        if (panel.closest('[data-ls-tabs]') && panel.closest('[data-ls-tabs]') !== group) return;
        panel.hidden = panel.dataset.panel !== name;
      });
    }
    buttons.forEach(function (button) { button.addEventListener('click', function () { show(button.dataset.tab); }); });
    const initial = group.getAttribute('data-initial') || (buttons[0] && buttons[0].dataset.tab);
    if (initial) show(initial);
  });
  const protocolNav = document.querySelector('[data-protocol-nav]');
  if (protocolNav) {
    const items = Array.from(protocolNav.querySelectorAll('[data-protocol-target]'));
    function showProtocol(id) {
      items.forEach(function (item) { item.classList.toggle('is-active', item.getAttribute('data-protocol-target') === id); });
      document.querySelectorAll('.protocol-editor-block').forEach(function (block) { block.hidden = block.id !== id; });
    }
    items.forEach(function (item) {
      item.addEventListener('click', function () { showProtocol(item.getAttribute('data-protocol-target')); });
    });
    if (items[0]) showProtocol(items[0].getAttribute('data-protocol-target'));
  }
  document.querySelectorAll('[data-tab-goto]').forEach(function (button) {
    button.addEventListener('click', function () {
      const target = document.querySelector('[data-tab="' + button.getAttribute('data-tab-goto') + '"]');
      if (target) target.click();
    });
  });
})();
</script>
"""

_SHELL_OPEN = """
<body class="ls-app"{% if api_identity is defined %} data-api-identity="{{ api_identity }}"{% endif %}>
<aside class="ls-sidebar">
  <a class="ls-brand" href="{{ url_for('admin.dashboard') }}" aria-label="LeadSpotting">
    <img class="ls-mark" src="{{ url_for('static', filename='leadspotting-mark.gif') }}" alt="">
    <img class="ls-logo" src="{{ url_for('static', filename='leadspotting-logo.gif') }}" alt="">
  </a>
    <nav class="ls-nav" aria-label="{{ t('admin.menu_title') }}">
    <div class="ls-nav-group">
      <a class="ls-nav-item{% if request.endpoint == 'admin.dashboard' %} is-active{% endif %}" href="{{ url_for('admin.dashboard') }}">""" + _ICON_HOME + """<span class="ls-nav-label">{{ t('admin.menu_all') }}</span></a>
    </div>
    <div class="ls-nav-group">
      <span class="ls-nav-group-label">{{ t('admin.nav_group_management') }}</span>
      <a class="ls-nav-item{% if request.endpoint == 'admin.users' %} is-active{% endif %}" href="{{ url_for('admin.users') }}">""" + _ICON_USERS + """<span class="ls-nav-label">{{ t('admin.menu_users') }}</span></a>
      <a class="ls-nav-item{% if request.endpoint == 'admin.groups' %} is-active{% endif %}" href="{{ url_for('admin.groups') }}">""" + _ICON_GROUPS + """<span class="ls-nav-label">{{ t('admin.menu_groups') }}</span></a>
    </div>
    <div class="ls-nav-group">
      <span class="ls-nav-group-label">{{ t('admin.nav_group_operations') }}</span>
      <a class="ls-nav-item{% if request.endpoint == 'admin.profiles' %} is-active{% endif %}" href="{{ url_for('admin.profiles') }}">""" + _ICON_PROFILES + """<span class="ls-nav-label">{{ t('admin.menu_profiles') }}</span></a>
      <a class="ls-nav-item{% if request.endpoint == 'admin.protocols' %} is-active{% endif %}" href="{{ url_for('admin.protocols') }}">""" + _ICON_PROTOCOLS + """<span class="ls-nav-label">{{ t('admin.menu_protocols') }}</span></a>
      <a class="ls-nav-item{% if request.endpoint == 'admin.events' %} is-active{% endif %}" href="{{ url_for('admin.events') }}">""" + _ICON_EVENTS + """<span class="ls-nav-label">{{ t('admin.menu_events') }}</span></a>
    </div>
    <div class="ls-nav-group">
      <span class="ls-nav-group-label">{{ t('admin.nav_group_system') }}</span>
      <a class="ls-nav-item{% if request.endpoint == 'admin.server' %} is-active{% endif %}" href="{{ url_for('admin.server') }}">""" + _ICON_SERVER + """<span class="ls-nav-label">{{ t('admin.menu_server') }}</span></a>
      <a class="ls-nav-item{% if request.endpoint == 'admin.simulator' %} is-active{% endif %}" href="{{ url_for('admin.simulator') }}">""" + _ICON_SIMULATOR + """<span class="ls-nav-label">{{ t('admin.menu_simulator') }}</span></a>
    </div>
    <div class="ls-nav-group">
      <span class="ls-nav-group-label">{{ t('admin.nav_group_data') }}</span>
      {% for table in admin_tables|default([]) %}
      <a class="ls-nav-item{% if request.view_args and request.view_args.get('table_key') == table.key %} is-active{% endif %}" href="{{ url_for('admin.admin_table_list', table_key=table.key) }}">""" + _ICON_TABLE + """<span class="ls-nav-label">{{ table.label }}</span></a>
      {% endfor %}
    </div>
  </nav>
  <button type="button" class="ls-sidebar-toggle" id="ls-sidebar-toggle" aria-label="{{ t('admin.menu_title') }}">""" + _ICON_TOGGLE + """</button>
</aside>
<div class="ls-main">
  <header class="ls-topbar">
    <a class="ls-btn-fill" href="{{ url_for('admin.dashboard') }}">{{ t('admin.nav_menu') }}</a>
    <span class="ls-status-live"><span class="dot"></span>{% if acting_identity_status %}{{ acting_identity_status }}{% else %}{{ t('admin.connected') }}{% endif %}</span>
    <div class="ls-topbar-end">
      {% if csrf_token %}
      <form method="post" action="{{ url_for('admin.logout') }}">
        <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
        <button type="submit" class="ls-user-btn">""" + _ICON_PERSON + """<span>{{ t('admin.log_out') }}</span></button>
      </form>
      {% endif %}
    </div>
  </header>
  <div class="ls-content">
"""

_SHELL_CLOSE = """
  </div>
</div>
""" + _SHELL_SCRIPT + """
</body></html>
"""
