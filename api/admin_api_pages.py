"""Browser-side API controls used by the login-gated administration pages.

The forms in this module deliberately call the public JSON endpoints from the
browser.  The admin session only grants access to the pages; it does not grant
API permissions.  Every API request therefore carries the selected registered
identity in ``X-Identity`` and receives the normal authentication,
authorization, validation, and routing behaviour.
"""

API_CONSOLE_STYLE = """
<style>
  .api-identity-bar { display:flex; gap:12px; align-items:end; flex-wrap:wrap; }
  .api-identity-bar .identity-field { min-width:280px; flex:1; }
  .api-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(320px,1fr)); gap:18px; }
  .api-card { border:1px solid var(--line); background:rgba(255,255,255,.16); padding:18px; }
  .api-endpoint { direction:ltr; unicode-bidi:isolate; display:inline-flex; gap:8px; align-items:center;
    font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; font-size:13px; }
  .api-method { min-width:56px; padding:2px 7px; border:1px solid var(--line-strong); text-align:center; font-weight:700; }
  .api-output { direction:ltr; text-align:left; unicode-bidi:plaintext; white-space:pre-wrap; overflow-wrap:anywhere;
    min-height:80px; max-height:360px; overflow:auto; margin:12px 0 0; padding:12px;
    border:1px solid var(--line); background:rgba(255,255,255,.25); font-size:12px; }
  .api-output[data-state="ok"] { border-inline-start:4px solid #2f7d4f; }
  .api-output[data-state="error"] { border-inline-start:4px solid #a33a3a; }
  .api-output[data-state="loading"] { opacity:.72; }
  .api-hint { color:var(--text-dim); font-size:13px; line-height:1.55; }
  .api-form-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:12px; }
  .api-form-grid .wide { grid-column:1/-1; }
  .api-form-grid label { display:block; font-size:12px; color:var(--text-dim); margin-bottom:5px; }
  .api-list { display:grid; gap:10px; margin-top:12px; }
  .api-list-item { border:1px solid var(--line); padding:12px; }
  @media (max-width:640px) { .api-form-grid { grid-template-columns:1fr; } .api-form-grid .wide { grid-column:auto; } }
</style>
"""


IDENTITY_BAR = """
<div class="block-console mb-4">
  <span class="block-label">{{ t('admin.api.identity_title') }}</span>
  <form class="api-identity-bar" method="post" action="{{ url_for('admin.select_api_identity') }}">
    <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
    <input type="hidden" name="next_page" value="{{ current_page }}">
    <div class="identity-field">
      <label class="form-label-console" for="api-identity-select">{{ t('admin.api.identity_label') }}</label>
      <select id="api-identity-select" name="api_identity" class="form-select form-select-console" {% if not api_users %}disabled{% endif %}>
        {% for user in api_users %}
          <option value="{{ user.telegram_identity }}" {% if user.telegram_identity == api_identity %}selected{% endif %}>
            {{ user.full_name or t('admin.api.missing_name') }} — {{ user.telegram_identity }} ({{ user.permission_level }})
          </option>
        {% endfor %}
      </select>
    </div>
    <button class="btn btn-console" {% if not api_users %}disabled{% endif %}>{{ t('admin.api.identity_save') }}</button>
  </form>
  {% if not api_users %}<p class="api-hint mt-3 mb-0">{{ t('admin.api.no_identity') }}</p>{% else %}
  <p class="api-hint mt-3 mb-0">{{ t('admin.api.identity_help') }}</p>{% endif %}
</div>
"""


FLASH_MESSAGES = """
{% for category, message in get_flashed_messages(with_categories=true) %}
  <div class="alert-console{% if category == 'error' %}-error{% endif %} px-3 py-2 mb-4">{{ message }}</div>
{% endfor %}
"""


API_CLIENT_SCRIPT = """
<script>
window.AdminApi = (() => {
  const identity = document.body.dataset.apiIdentity || '';
  const networkErrorLabel = {{ t('admin.api.network_error')|tojson }};
  const pretty = value => typeof value === 'string' ? value : JSON.stringify(value, null, 2);
  const value = id => document.getElementById(id).value.trim();
  const checked = id => document.getElementById(id).checked;
  const csv = id => value(id).split(',').map(item => item.trim()).filter(Boolean);
  const optional = (target, key, id) => { const item = value(id); if (item !== '') target[key] = item; };
  const numberOptional = (target, key, id, parse) => { const item = value(id); if (item !== '') target[key] = parse(item); };

  function setOutput(outputId, state, content) {
    const output = document.getElementById(outputId);
    output.dataset.state = state;
    output.textContent = content;
  }

  async function call(method, path, body, outputId) {
    setOutput(outputId, 'loading', {{ t('admin.api.sending')|tojson }});
    const options = {method, headers:{'Accept':'application/json', 'X-Identity':identity}};
    if (body !== undefined) {
      options.headers['Content-Type'] = 'application/json';
      options.body = JSON.stringify(body);
    }
    try {
      const response = await fetch(path, options);
      const raw = await response.text();
      let payload;
      try { payload = raw ? JSON.parse(raw) : null; } catch (_) { payload = raw; }
      const trace = response.headers.get('X-Trace-ID');
      const summary = `${method} ${path}\nHTTP ${response.status}${trace ? `\nTrace: ${trace}` : ''}\n\n${pretty(payload)}`;
      setOutput(outputId, response.ok ? 'ok' : 'error', summary);
      return {ok:response.ok, status:response.status, payload};
    } catch (error) {
      setOutput(outputId, 'error', `${method} ${path}\n\n${networkErrorLabel}: ${error.message}`);
      return {ok:false, status:0, payload:null};
    }
  }

  function protocolBody(prefix) {
    return {
      name:value(`${prefix}-name`),
      description:value(`${prefix}-description`),
      participating_agents:csv(`${prefix}-agents`),
      approved_tools:csv(`${prefix}-tools`),
      expected_success_output:value(`${prefix}-success`),
      criticality:value(`${prefix}-criticality`),
      approval_flag:checked(`${prefix}-approval`)
    };
  }

  return {identity, value, checked, csv, optional, numberOptional, call, protocolBody};
})();
</script>
"""


PROFILES_BODY = """
<body data-api-identity="{{ api_identity }}"><div class="container container-narrow">
  <div class="d-flex justify-content-between align-items-baseline"><h1>{{ t('admin.profiles.title') }}</h1><a class="nav-console" href="{{ url_for('admin.dashboard') }}">{{ t('admin.nav_menu') }}</a></div>
  <p class="subtitle mb-4">{{ t('admin.profiles.subtitle') }}</p>
  """ + IDENTITY_BAR + FLASH_MESSAGES + """
  <div class="api-grid">
    <section class="api-card">
      <div class="api-endpoint"><span class="api-method">GET</span><span>/SYSTEM</span></div>
      <p class="api-hint mt-3">{{ t('admin.profiles.get_help') }}</p>
      <button id="system-get" class="btn btn-console-primary">{{ t('admin.api.execute') }}</button>
      <pre id="system-get-output" class="api-output">{{ t('admin.api.not_run') }}</pre>
    </section>
    <section class="api-card">
      <div class="api-endpoint"><span class="api-method">PUT</span><span>/SYSTEM</span></div>
      <p class="api-hint mt-3">{{ t('admin.profiles.put_help') }}</p>
      <form id="system-put-form" class="api-form-grid">
        <div><label for="system-retry">retry_count</label><input id="system-retry" type="number" min="0" class="form-control form-control-console"></div>
        <div><label for="system-risk">risk_threshold</label><input id="system-risk" type="number" min="0" max="1" step="0.01" class="form-control form-control-console"></div>
        <div><label for="system-lookback">lookback_window_days</label><input id="system-lookback" type="number" min="1" class="form-control form-control-console"></div>
        <div class="wide"><button class="btn btn-console-primary">{{ t('admin.api.execute') }}</button></div>
      </form>
      <pre id="system-put-output" class="api-output">{{ t('admin.api.not_run') }}</pre>
    </section>
  </div>
</div>
""" + API_CLIENT_SCRIPT + """
<script>
document.getElementById('system-get').addEventListener('click', () => AdminApi.call('GET', '/SYSTEM', undefined, 'system-get-output'));
document.getElementById('system-put-form').addEventListener('submit', event => {
  event.preventDefault();
  const body = {};
  AdminApi.numberOptional(body, 'retry_count', 'system-retry', Number.parseInt);
  AdminApi.numberOptional(body, 'risk_threshold', 'system-risk', Number.parseFloat);
  AdminApi.numberOptional(body, 'lookback_window_days', 'system-lookback', Number.parseInt);
  AdminApi.call('PUT', '/SYSTEM', body, 'system-put-output');
});
</script></body>
"""


PROTOCOLS_BODY = """
<body data-api-identity="{{ api_identity }}"><div class="container container-narrow">
  <div class="d-flex justify-content-between align-items-baseline"><h1>{{ t('admin.protocols.title') }}</h1><a class="nav-console" href="{{ url_for('admin.dashboard') }}">{{ t('admin.nav_menu') }}</a></div>
  <p class="subtitle mb-4">{{ t('admin.protocols.subtitle') }}</p>
  """ + IDENTITY_BAR + FLASH_MESSAGES + """
  <datalist id="known-agents">{% for agent in agents %}<option value="{{ agent }}">{% endfor %}</datalist>
  <datalist id="known-tools">{% for tool in tools %}<option value="{{ tool }}">{% endfor %}</datalist>
  <div class="api-grid">
    <section class="api-card">
      <div class="api-endpoint"><span class="api-method">GET</span><span>/Protocol</span></div>
      <p class="api-hint mt-3">{{ t('admin.protocols.list_help') }}</p>
      <button id="protocol-list" class="btn btn-console-primary">{{ t('admin.api.execute') }}</button>
      <pre id="protocol-list-output" class="api-output">{{ t('admin.api.not_run') }}</pre>
    </section>
    <section class="api-card">
      <div class="api-endpoint"><span class="api-method">POST</span><span>/Protocol</span></div>
      <form id="protocol-create-form" class="api-form-grid mt-3">
        <div><label>{{ t('admin.protocols.name') }}</label><input id="create-name" required class="form-control form-control-console"></div>
        <div><label>{{ t('admin.protocols.criticality') }}</label><select id="create-criticality" class="form-select form-select-console"><option>low</option><option>medium</option><option>high</option></select></div>
        <div class="wide"><label>{{ t('admin.protocols.description') }}</label><textarea id="create-description" required class="form-control form-control-console"></textarea></div>
        <div><label>{{ t('admin.protocols.agents') }}</label><input id="create-agents" list="known-agents" required class="form-control form-control-console" placeholder="agent_a, agent_b"></div>
        <div><label>{{ t('admin.protocols.tools') }}</label><input id="create-tools" list="known-tools" class="form-control form-control-console" placeholder="tool_a, tool_b"></div>
        <div class="wide"><label>{{ t('admin.protocols.success') }}</label><input id="create-success" required class="form-control form-control-console"></div>
        <div class="wide form-check"><input id="create-approval" type="checkbox" class="form-check-input"><label class="form-check-label" for="create-approval">{{ t('admin.protocols.approval') }}</label></div>
        <div class="wide"><button class="btn btn-console-primary">{{ t('admin.api.execute') }}</button></div>
      </form>
      <pre id="protocol-create-output" class="api-output">{{ t('admin.api.not_run') }}</pre>
    </section>
    <section class="api-card">
      <div class="api-endpoint"><span class="api-method">PUT</span><span>/Protocol/&lt;name&gt;</span></div>
      <form id="protocol-update-form" class="api-form-grid mt-3">
        <div><label>{{ t('admin.protocols.name') }}</label><input id="update-name" required class="form-control form-control-console"></div>
        <div><label>{{ t('admin.protocols.criticality') }}</label><select id="update-criticality" class="form-select form-select-console"><option>low</option><option>medium</option><option>high</option></select></div>
        <div class="wide"><label>{{ t('admin.protocols.description') }}</label><textarea id="update-description" required class="form-control form-control-console"></textarea></div>
        <div><label>{{ t('admin.protocols.agents') }}</label><input id="update-agents" list="known-agents" required class="form-control form-control-console"></div>
        <div><label>{{ t('admin.protocols.tools') }}</label><input id="update-tools" list="known-tools" class="form-control form-control-console"></div>
        <div class="wide"><label>{{ t('admin.protocols.success') }}</label><input id="update-success" required class="form-control form-control-console"></div>
        <div class="wide form-check"><input id="update-approval" type="checkbox" class="form-check-input"><label class="form-check-label" for="update-approval">{{ t('admin.protocols.approval') }}</label></div>
        <div class="wide"><button class="btn btn-console-primary">{{ t('admin.api.execute') }}</button></div>
      </form>
      <pre id="protocol-update-output" class="api-output">{{ t('admin.api.not_run') }}</pre>
    </section>
    <section class="api-card">
      <div class="api-endpoint"><span class="api-method">DELETE</span><span>/Protocol/&lt;name&gt;</span></div>
      <form id="protocol-delete-form" class="api-form-grid mt-3">
        <div class="wide"><label>{{ t('admin.protocols.name') }}</label><input id="delete-protocol-name" required class="form-control form-control-console"></div>
        <div class="wide"><button class="btn btn-console-danger">{{ t('admin.api.execute') }}</button></div>
      </form>
      <pre id="protocol-delete-output" class="api-output">{{ t('admin.api.not_run') }}</pre>
    </section>
  </div>
</div>
""" + API_CLIENT_SCRIPT + """
<script>
document.getElementById('protocol-list').addEventListener('click', () => AdminApi.call('GET', '/Protocol', undefined, 'protocol-list-output'));
document.getElementById('protocol-create-form').addEventListener('submit', event => { event.preventDefault(); AdminApi.call('POST', '/Protocol', AdminApi.protocolBody('create'), 'protocol-create-output'); });
document.getElementById('protocol-update-form').addEventListener('submit', event => { event.preventDefault(); const name=AdminApi.value('update-name'); AdminApi.call('PUT', `/Protocol/${encodeURIComponent(name)}`, AdminApi.protocolBody('update'), 'protocol-update-output'); });
document.getElementById('protocol-delete-form').addEventListener('submit', event => { event.preventDefault(); const name=AdminApi.value('delete-protocol-name'); if (confirm({{ t('admin.protocols.delete_confirm')|tojson }})) AdminApi.call('DELETE', `/Protocol/${encodeURIComponent(name)}`, undefined, 'protocol-delete-output'); });
</script></body>
"""


EVENTS_BODY = """
<body data-api-identity="{{ api_identity }}"><div class="container container-narrow">
  <div class="d-flex justify-content-between align-items-baseline"><h1>{{ t('admin.events.title') }}</h1><a class="nav-console" href="{{ url_for('admin.dashboard') }}">{{ t('admin.nav_menu') }}</a></div>
  <p class="subtitle mb-4">{{ t('admin.events.subtitle') }}</p>
  """ + IDENTITY_BAR + FLASH_MESSAGES + """
  <div class="api-grid">
    <section class="api-card"><div class="api-endpoint"><span class="api-method">POST</span><span>/Event</span></div>
      <form id="event-form" class="api-form-grid mt-3"><div class="wide"><label>{{ t('admin.events.text') }}</label><textarea id="event-text" required class="form-control form-control-console"></textarea></div><div class="wide"><button class="btn btn-console-primary">{{ t('admin.api.execute') }}</button></div></form><pre id="event-output" class="api-output">{{ t('admin.api.not_run') }}</pre></section>
    <section class="api-card"><div class="api-endpoint"><span class="api-method">POST</span><span>/Msg</span></div>
      <form id="message-form" class="api-form-grid mt-3">
        <div class="wide"><label>{{ t('admin.events.text') }}</label><textarea id="message-text" required class="form-control form-control-console"></textarea></div>
        <div><label>conversation_id</label><input id="message-conversation" class="form-control form-control-console"></div><div><label>source_message_id</label><input id="message-source" class="form-control form-control-console"></div>
        <div><label>telegram_chat_id</label><input id="message-chat-id" class="form-control form-control-console"></div><div><label>telegram_chat_type</label><select id="message-chat-type" class="form-select form-select-console"><option value="">—</option><option>private</option><option>group</option><option>supergroup</option></select></div>
        <div><label>protocol_hint</label><input id="message-protocol" class="form-control form-control-console"></div><div><label>event_data_event_id</label><input id="message-event-data" class="form-control form-control-console"></div>
        <div class="wide"><button class="btn btn-console-primary">{{ t('admin.api.execute') }}</button></div>
      </form><pre id="message-output" class="api-output">{{ t('admin.api.not_run') }}</pre></section>
    <section class="api-card"><div class="api-endpoint"><span class="api-method">GET</span><span>/Job/&lt;event_id&gt;</span></div>
      <form id="job-form" class="api-form-grid mt-3"><div class="wide"><label>event_id</label><input id="job-id" required class="form-control form-control-console"></div><div class="wide"><button class="btn btn-console-primary">{{ t('admin.api.execute') }}</button></div></form><pre id="job-output" class="api-output">{{ t('admin.api.not_run') }}</pre></section>
    <section class="api-card"><div class="api-endpoint"><span class="api-method">GET</span><span>/Holds/Pending</span></div><p class="api-hint mt-3">{{ t('admin.events.holds_help') }}</p><button id="holds-get" class="btn btn-console-primary">{{ t('admin.api.execute') }}</button><pre id="holds-output" class="api-output">{{ t('admin.api.not_run') }}</pre></section>
    <section class="api-card"><div class="api-endpoint"><span class="api-method">POST</span><span>/Clarify/&lt;event_id&gt;</span></div>
      <form id="clarify-form" class="api-form-grid mt-3"><div><label>event_id</label><input id="clarify-id" required class="form-control form-control-console"></div><div><label>classification</label><input id="clarify-classification" required list="event-types" class="form-control form-control-console"></div><div class="wide"><button class="btn btn-console-primary">{{ t('admin.api.execute') }}</button></div></form><pre id="clarify-output" class="api-output">{{ t('admin.api.not_run') }}</pre></section>
    <section class="api-card"><div class="api-endpoint"><span class="api-method">POST</span><span>/Approve/&lt;event_id&gt;</span></div>
      <form id="approve-form" class="api-form-grid mt-3"><div><label>event_id</label><input id="approve-id" required class="form-control form-control-console"></div><div><label>decision</label><input id="approve-decision" required list="approval-decisions" value="approved" class="form-control form-control-console"></div><div class="wide"><button class="btn btn-console-primary">{{ t('admin.api.execute') }}</button></div></form><p class="api-hint mt-2">{{ t('admin.events.approval_help') }}</p><pre id="approve-output" class="api-output">{{ t('admin.api.not_run') }}</pre></section>
    <section class="api-card"><div class="api-endpoint"><span class="api-method">GET</span><span>/Notifications</span></div>
      <form id="notifications-form" class="api-form-grid mt-3"><div><label>since</label><input id="notifications-since" type="number" min="0" value="0" class="form-control form-control-console"></div><div><label>wait_seconds</label><input id="notifications-wait" type="number" min="0" max="30" value="0" class="form-control form-control-console"></div><div class="wide"><button class="btn btn-console-primary">{{ t('admin.api.execute') }}</button></div></form><pre id="notifications-output" class="api-output">{{ t('admin.api.not_run') }}</pre></section>
    <section class="api-card"><div class="api-endpoint"><span class="api-method">GET</span><span>/Trace/&lt;trace_id&gt;</span></div>
      <form id="trace-form" class="api-form-grid mt-3"><div class="wide"><label>trace_id</label><input id="trace-id" required class="form-control form-control-console"></div><div><label>since</label><input id="trace-since" type="number" min="0" value="0" class="form-control form-control-console"></div><div><label>wait_seconds</label><input id="trace-wait" type="number" min="0" max="30" value="0" class="form-control form-control-console"></div><div class="wide"><button class="btn btn-console-primary">{{ t('admin.api.execute') }}</button></div></form><pre id="trace-output" class="api-output">{{ t('admin.api.not_run') }}</pre></section>
    <section class="api-card"><div class="api-endpoint"><span class="api-method">POST</span><span>/TeamStatus/AttendanceCheck</span></div>
      <form id="attendance-form" class="api-form-grid mt-3"><div><label>now_iso</label><input id="attendance-now" type="datetime-local" class="form-control form-control-console"></div><div class="form-check align-self-end"><input id="attendance-force" type="checkbox" class="form-check-input"><label for="attendance-force" class="form-check-label">force</label></div><div class="wide"><button class="btn btn-console-primary">{{ t('admin.api.execute') }}</button></div></form><pre id="attendance-output" class="api-output">{{ t('admin.api.not_run') }}</pre></section>
  </div>
  <datalist id="event-types">{% for event_type in event_types %}<option value="{{ event_type }}">{% endfor %}</datalist>
  <datalist id="approval-decisions"><option value="approved"><option value="rejected"></datalist>
</div>
""" + API_CLIENT_SCRIPT + """
<script>
document.getElementById('event-form').addEventListener('submit', event => { event.preventDefault(); AdminApi.call('POST','/Event',{text:AdminApi.value('event-text'),sender_identity:AdminApi.identity},'event-output'); });
document.getElementById('message-form').addEventListener('submit', event => { event.preventDefault(); const body={text:AdminApi.value('message-text'),sender_identity:AdminApi.identity}; AdminApi.optional(body,'conversation_id','message-conversation'); AdminApi.optional(body,'source_message_id','message-source'); AdminApi.optional(body,'telegram_chat_id','message-chat-id'); AdminApi.optional(body,'telegram_chat_type','message-chat-type'); AdminApi.optional(body,'protocol_hint','message-protocol'); AdminApi.optional(body,'event_data_event_id','message-event-data'); AdminApi.call('POST','/Msg',body,'message-output'); });
document.getElementById('job-form').addEventListener('submit', event => { event.preventDefault(); AdminApi.call('GET',`/Job/${encodeURIComponent(AdminApi.value('job-id'))}`,undefined,'job-output'); });
document.getElementById('holds-get').addEventListener('click', () => AdminApi.call('GET','/Holds/Pending',undefined,'holds-output'));
document.getElementById('clarify-form').addEventListener('submit', event => { event.preventDefault(); AdminApi.call('POST',`/Clarify/${encodeURIComponent(AdminApi.value('clarify-id'))}`,{classification:AdminApi.value('clarify-classification')},'clarify-output'); });
document.getElementById('approve-form').addEventListener('submit', event => { event.preventDefault(); AdminApi.call('POST',`/Approve/${encodeURIComponent(AdminApi.value('approve-id'))}`,{decision:AdminApi.value('approve-decision')},'approve-output'); });
document.getElementById('notifications-form').addEventListener('submit', event => { event.preventDefault(); const query=new URLSearchParams({since:AdminApi.value('notifications-since'),wait_seconds:AdminApi.value('notifications-wait')}); AdminApi.call('GET',`/Notifications?${query}`,undefined,'notifications-output'); });
document.getElementById('trace-form').addEventListener('submit', event => { event.preventDefault(); const trace=encodeURIComponent(AdminApi.value('trace-id')); const query=new URLSearchParams({since:AdminApi.value('trace-since'),wait_seconds:AdminApi.value('trace-wait')}); AdminApi.call('GET',`/Trace/${trace}?${query}`,undefined,'trace-output'); });
document.getElementById('attendance-form').addEventListener('submit', event => { event.preventDefault(); const body={force:AdminApi.checked('attendance-force')}; AdminApi.optional(body,'now_iso','attendance-now'); AdminApi.call('POST','/TeamStatus/AttendanceCheck',body,'attendance-output'); });
</script></body>
"""


USERS_API_SECTION = """
<div class="block-console mb-5">
  <span class="block-label">{{ t('admin.users.api_title') }}</span>
  <div class="api-grid">
    <section class="api-card"><div class="api-endpoint"><span class="api-method">GET</span><span>/User/&lt;identity&gt;</span></div><form id="user-get-form" class="api-form-grid mt-3"><div class="wide"><label>identity</label><input id="user-get-id" value="{{ api_identity }}" required class="form-control form-control-console"></div><div class="wide"><button class="btn btn-console-primary">{{ t('admin.api.execute') }}</button></div></form><pre id="user-get-output" class="api-output">{{ t('admin.api.not_run') }}</pre></section>
    <section class="api-card"><div class="api-endpoint"><span class="api-method">PUT</span><span>/User/&lt;identity&gt;/name</span></div><form id="user-name-form" class="api-form-grid mt-3"><div><label>identity</label><input id="user-name-id" value="{{ api_identity }}" required class="form-control form-control-console"></div><div><label>full_name</label><input id="user-full-name" required maxlength="120" class="form-control form-control-console"></div><div class="wide"><button class="btn btn-console-primary">{{ t('admin.api.execute') }}</button></div></form><p class="api-hint mt-2">{{ t('admin.users.self_name_help') }}</p><pre id="user-name-output" class="api-output">{{ t('admin.api.not_run') }}</pre></section>
    <section class="api-card"><div class="api-endpoint"><span class="api-method">GET</span><span>/Commanders</span></div><p class="api-hint mt-3">{{ t('admin.users.commanders_help') }}</p><button id="commanders-get" class="btn btn-console-primary">{{ t('admin.api.execute') }}</button><pre id="commanders-output" class="api-output">{{ t('admin.api.not_run') }}</pre></section>
  </div>
</div>
"""


USERS_API_SCRIPT = API_CLIENT_SCRIPT + """
<script>
document.getElementById('user-get-form').addEventListener('submit', event => { event.preventDefault(); AdminApi.call('GET',`/User/${encodeURIComponent(AdminApi.value('user-get-id'))}`,undefined,'user-get-output'); });
document.getElementById('user-name-form').addEventListener('submit', event => { event.preventDefault(); const id=AdminApi.value('user-name-id'); AdminApi.call('PUT',`/User/${encodeURIComponent(id)}/name`,{full_name:AdminApi.value('user-full-name')},'user-name-output'); });
document.getElementById('commanders-get').addEventListener('click', () => AdminApi.call('GET','/Commanders',undefined,'commanders-output'));
</script>
"""


GROUPS_API_SECTION = """
<div class="block-console mb-5">
  <span class="block-label">{{ t('admin.groups.api_title') }}</span>
  <div class="api-grid">
    <section class="api-card"><div class="api-endpoint"><span class="api-method">GET</span><span>/Groups</span></div><button id="groups-get" class="btn btn-console-primary mt-3">{{ t('admin.api.execute') }}</button><pre id="groups-get-output" class="api-output">{{ t('admin.api.not_run') }}</pre></section>
    <section class="api-card"><div class="api-endpoint"><span class="api-method">PUT</span><span>/Groups/&lt;chat_id&gt;</span></div><form id="group-put-form" class="api-form-grid mt-3"><div><label>chat_id</label><input id="group-put-id" required placeholder="-1001234567890" class="form-control form-control-console"></div><div><label>agent_name</label><select id="group-put-agent" class="form-select form-select-console">{% for agent in routable_agents %}<option value="{{ agent }}">{{ agent }}</option>{% endfor %}</select></div><div class="wide"><label>label</label><input id="group-put-label" maxlength="200" class="form-control form-control-console"></div><div class="wide"><button class="btn btn-console-primary">{{ t('admin.api.execute') }}</button></div></form><pre id="group-put-output" class="api-output">{{ t('admin.api.not_run') }}</pre></section>
    <section class="api-card"><div class="api-endpoint"><span class="api-method">DELETE</span><span>/Groups/&lt;chat_id&gt;</span></div><form id="group-delete-form" class="api-form-grid mt-3"><div class="wide"><label>chat_id</label><input id="group-delete-id" required placeholder="-1001234567890" class="form-control form-control-console"></div><div class="wide"><button class="btn btn-console-danger">{{ t('admin.api.execute') }}</button></div></form><pre id="group-delete-output" class="api-output">{{ t('admin.api.not_run') }}</pre></section>
  </div>
</div>
"""


GROUPS_API_SCRIPT = API_CLIENT_SCRIPT + """
<script>
document.getElementById('groups-get').addEventListener('click', () => AdminApi.call('GET','/Groups',undefined,'groups-get-output'));
document.getElementById('group-put-form').addEventListener('submit', event => { event.preventDefault(); const id=AdminApi.value('group-put-id'); AdminApi.call('PUT',`/Groups/${encodeURIComponent(id)}`,{agent_name:AdminApi.value('group-put-agent'),label:AdminApi.value('group-put-label')},'group-put-output'); });
document.getElementById('group-delete-form').addEventListener('submit', event => { event.preventDefault(); const id=AdminApi.value('group-delete-id'); if(confirm({{ t('admin.groups.delete_confirm')|tojson }})) AdminApi.call('DELETE',`/Groups/${encodeURIComponent(id)}`,undefined,'group-delete-output'); });
</script>
"""
