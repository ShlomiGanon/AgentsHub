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
  const successLabel = {{ t('admin.api.success')|tojson }};
  const failedLabel = {{ t('admin.api.failed')|tojson }};
  const statusLabel = {{ t('admin.api.result_status')|tojson }};
  const eventLabel = {{ t('admin.api.result_event')|tojson }};
  const typeLabel = {{ t('admin.api.result_type')|tojson }};
  const value = id => document.getElementById(id).value.trim();
  const checked = id => document.getElementById(id).checked;
  const csv = id => value(id).split(',').map(item => item.trim()).filter(Boolean);
  const optional = (target, key, id) => { const item = value(id); if (item !== '') target[key] = item; };
  const numberOptional = (target, key, id, parse) => { const item = value(id); if (item !== '') target[key] = parse(item); };

  function setOutput(outputId, state, content) {
    const output = document.getElementById(outputId);
    output.hidden = false;
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
      const details = [];
      if (payload && typeof payload === 'object') {
        if (payload.message) details.push(payload.message);
        if (payload.answer) details.push(payload.answer);
        if (payload.event_id || payload.job_id) details.push(`${eventLabel}: ${payload.event_id || payload.job_id}`);
        if (payload.status || payload.outcome) details.push(`${statusLabel}: ${payload.status || payload.outcome}`);
        if (payload.taken_as) details.push(`${typeLabel}: ${payload.taken_as}`);
        if (payload.detail) details.push(payload.detail);
        if (payload.insight_text) details.push(payload.insight_text);
        if (Array.isArray(payload.steps_completed)) details.push(...payload.steps_completed);
        if (Array.isArray(payload.entries)) details.push(...payload.entries.map(entry => entry.text));
      } else if (payload) details.push(String(payload));
      const content = details.join('\\n');
      const summary = `${response.ok ? successLabel : failedLabel}${content ? `\n\n${content}` : ''}`;
      setOutput(outputId, response.ok ? 'ok' : 'error', summary);
      return {ok:response.ok, status:response.status, payload};
    } catch (error) {
      setOutput(outputId, 'error', `${networkErrorLabel}: ${error.message}`);
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
  <div class="row g-3 mb-4">
    <div class="col-md-6"><div class="block-console h-100"><span class="block-label">{{ t('admin.profiles.active') }}</span><h2 class="h5 mb-2">{{ profile_name }}</h2><div class="identity">{{ profile_module }}</div><div class="mt-3"><span class="tag">{{ t('admin.profiles.agents_count', count=agents|length) }}</span> <span class="tag">{{ t('admin.profiles.protocols_count', count=protocols|length) }}</span></div></div></div>
    <div class="col-md-6"><div class="block-console h-100"><span class="block-label">{{ t('admin.profiles.operational_scope') }}</span><div class="mb-3"><strong>{{ t('admin.profiles.event_types') }}</strong><div class="mt-2">{% for item in event_types %}<span class="tag">{{ item }}</span> {% else %}—{% endfor %}</div></div><div><strong>{{ t('admin.profiles.areas') }}</strong><div class="mt-2">{% for item in areas %}<span class="tag">{{ item }}</span> {% else %}—{% endfor %}</div></div></div></div>
  </div>
  <div class="block-console mb-4">
    <div class="d-flex justify-content-between align-items-center gap-2 flex-wrap"><span class="block-label mb-0">{{ t('admin.profiles.settings_title') }}</span><button id="system-get" class="btn btn-console btn-sm">{{ t('admin.api.refresh') }}</button></div>
    <p class="api-hint mt-3">{{ t('admin.profiles.put_help') }}</p>
    <form id="system-put-form" class="api-form-grid">
      <div><label for="system-retry">{{ t('admin.profiles.retry_count') }}</label><input id="system-retry" type="number" min="0" value="{{ settings.retry_count }}" class="form-control form-control-console"></div>
      <div><label for="system-risk">{{ t('admin.profiles.risk_threshold') }}</label><input id="system-risk" type="number" min="0" max="1" step="0.01" value="{{ settings.risk_threshold }}" class="form-control form-control-console"></div>
      <div><label for="system-lookback">{{ t('admin.profiles.lookback_days') }}</label><input id="system-lookback" type="number" min="1" value="{{ settings.lookback_window_days }}" class="form-control form-control-console"></div>
      <div class="wide"><button class="btn btn-console-primary">{{ t('admin.save') }}</button></div>
    </form>
    <pre id="system-put-output" class="api-output" hidden></pre>
  </div>
  <pre id="system-get-output" class="api-output mb-4" hidden></pre>
  <div class="block-console"><span class="block-label">{{ t('admin.profiles.components') }}</span><div class="api-form-grid"><div><strong>{{ t('admin.profiles.agents') }}</strong><ul class="mt-2">{% for item in agents %}<li>{{ item }}</li>{% endfor %}</ul></div><div><strong>{{ t('admin.profiles.protocols') }}</strong><ul class="mt-2">{% for item in protocols %}<li>{{ item }}</li>{% endfor %}</ul></div></div>
  </div>
</div>
""" + API_CLIENT_SCRIPT + """
<script>
document.getElementById('system-get').addEventListener('click', async () => {
  const result = await AdminApi.call('GET', '/SYSTEM', undefined, 'system-get-output');
  if (result.ok && result.payload && result.payload.settings) {
    document.getElementById('system-retry').value = result.payload.settings.retry_count;
    document.getElementById('system-risk').value = result.payload.settings.risk_threshold;
    document.getElementById('system-lookback').value = result.payload.settings.lookback_window_days;
  }
});
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
  <div class="d-flex justify-content-between align-items-center gap-2 flex-wrap mb-3"><h2 class="h5 mb-0">{{ t('admin.protocols.existing', count=protocols|length) }}</h2><button id="protocol-list" class="btn btn-console btn-sm">{{ t('admin.api.refresh') }}</button></div>
  <p class="api-hint">{{ t('admin.protocols.restart_note') }}</p>
  <div class="api-list mb-5">
    {% for protocol in protocols %}<section class="api-list-item">
      <form class="protocol-edit-form api-form-grid" data-name="{{ protocol.name }}">
        <div><label>{{ t('admin.protocols.name') }}</label><input name="name" value="{{ protocol.name }}" readonly class="form-control form-control-console"></div>
        <div><label>{{ t('admin.protocols.criticality') }}</label><select name="criticality" class="form-select form-select-console">{% for level in ('low','medium','high') %}<option value="{{ level }}" {% if protocol.criticality == level %}selected{% endif %}>{{ level }}</option>{% endfor %}</select></div>
        <div class="wide"><label>{{ t('admin.protocols.description') }}</label><textarea name="description" required class="form-control form-control-console">{{ protocol.description }}</textarea></div>
        <div><label>{{ t('admin.protocols.agents') }}</label><input name="agents" value="{{ protocol.participating_agents|join(', ') }}" required class="form-control form-control-console"></div>
        <div><label>{{ t('admin.protocols.tools') }}</label><input name="tools" value="{{ protocol.approved_tools|join(', ') }}" class="form-control form-control-console"></div>
        <div class="wide"><label>{{ t('admin.protocols.success') }}</label><input name="success" value="{{ protocol.expected_success_output }}" required class="form-control form-control-console"></div>
        <div class="wide d-flex justify-content-between align-items-center gap-2"><label class="form-check mb-0"><input name="approval" type="checkbox" class="form-check-input" {% if protocol.approval_flag %}checked{% endif %}> <span class="form-check-label">{{ t('admin.protocols.approval') }}</span></label><div><button class="btn btn-console me-2">{{ t('admin.save') }}</button><button type="button" class="btn btn-console-danger protocol-delete">{{ t('admin.remove') }}</button></div></div>
      </form>
    </section>{% else %}<div class="api-list-item">{{ t('admin.protocols.none') }}</div>{% endfor %}
  </div>
  <div class="block-console mb-4"><span class="block-label">{{ t('admin.protocols.add') }}</span>
      <form id="protocol-create-form" class="api-form-grid mt-3">
        <div><label>{{ t('admin.protocols.name') }}</label><input id="create-name" required class="form-control form-control-console"></div>
        <div><label>{{ t('admin.protocols.criticality') }}</label><select id="create-criticality" class="form-select form-select-console"><option>low</option><option>medium</option><option>high</option></select></div>
        <div class="wide"><label>{{ t('admin.protocols.description') }}</label><textarea id="create-description" required class="form-control form-control-console"></textarea></div>
        <div><label>{{ t('admin.protocols.agents') }}</label><input id="create-agents" list="known-agents" required class="form-control form-control-console" placeholder="agent_a, agent_b"></div>
        <div><label>{{ t('admin.protocols.tools') }}</label><input id="create-tools" list="known-tools" class="form-control form-control-console" placeholder="tool_a, tool_b"></div>
        <div class="wide"><label>{{ t('admin.protocols.success') }}</label><input id="create-success" required class="form-control form-control-console"></div>
        <div class="wide form-check"><input id="create-approval" type="checkbox" class="form-check-input"><label class="form-check-label" for="create-approval">{{ t('admin.protocols.approval') }}</label></div>
        <div class="wide"><button class="btn btn-console-primary">{{ t('admin.add') }}</button></div>
      </form>
  </div>
  <pre id="protocol-status" class="api-output" hidden></pre>
  <pre id="protocol-list-output" class="api-output" hidden></pre>
</div>
""" + API_CLIENT_SCRIPT + """
<script>
document.getElementById('protocol-list').addEventListener('click', () => AdminApi.call('GET', '/Protocol', undefined, 'protocol-list-output'));
document.getElementById('protocol-create-form').addEventListener('submit', event => { event.preventDefault(); document.getElementById('protocol-status').hidden=false; AdminApi.call('POST', '/Protocol', AdminApi.protocolBody('create'), 'protocol-status'); });
function editableProtocolBody(form) {
  const list = name => form.elements[name].value.split(',').map(item => item.trim()).filter(Boolean);
  return {name:form.elements.name.value,description:form.elements.description.value,participating_agents:list('agents'),approved_tools:list('tools'),expected_success_output:form.elements.success.value,criticality:form.elements.criticality.value,approval_flag:form.elements.approval.checked};
}
document.querySelectorAll('.protocol-edit-form').forEach(form => {
  form.addEventListener('submit', event => { event.preventDefault(); document.getElementById('protocol-status').hidden=false; AdminApi.call('PUT', `/Protocol/${encodeURIComponent(form.dataset.name)}`, editableProtocolBody(form), 'protocol-status'); });
  form.querySelector('.protocol-delete').addEventListener('click', () => { if(confirm({{ t('admin.protocols.delete_confirm')|tojson }})){ document.getElementById('protocol-status').hidden=false; AdminApi.call('DELETE', `/Protocol/${encodeURIComponent(form.dataset.name)}`, undefined, 'protocol-status'); } });
});
</script></body>
"""


EVENTS_BODY = """
<body data-api-identity="{{ api_identity }}"><div class="container container-narrow">
  <div class="d-flex justify-content-between align-items-baseline"><h1>{{ t('admin.events.title') }}</h1><a class="nav-console" href="{{ url_for('admin.dashboard') }}">{{ t('admin.nav_menu') }}</a></div>
  <p class="subtitle mb-4">{{ t('admin.events.subtitle') }}</p>
  """ + IDENTITY_BAR + FLASH_MESSAGES + """
  <div class="block-console mb-4">
    <div class="d-flex justify-content-between align-items-center gap-2 flex-wrap"><span class="block-label mb-0">{{ t('admin.events.recent', count=recent_events|length) }}</span><span class="api-hint">{{ t('admin.events.recent_help') }}</span></div>
    <div class="table-responsive mt-3"><table class="table table-console"><thead><tr><th>{{ t('admin.events.received') }}</th><th>{{ t('admin.events.description') }}</th><th>{{ t('admin.events.sender') }}</th><th>{{ t('admin.events.classification') }}</th><th>{{ t('admin.events.status') }}</th><th></th></tr></thead><tbody>
      {% for item in recent_events %}<tr><td class="identity">{{ item.received_at }}</td><td><div>{{ item.text }}</div><small class="identity">{{ item.event_id }}</small></td><td>{{ item.sender_name or item.sender_identity }}</td><td>{{ item.classification or '—' }}{% if item.area %} · {{ item.area }}{% endif %}</td><td><span class="tag">{{ item.status }}</span></td><td><button type="button" class="btn btn-console btn-sm recent-job" data-event-id="{{ item.event_id }}">{{ t('admin.events.check_status') }}</button></td></tr>
      {% else %}<tr><td colspan="6">{{ t('admin.events.none') }}</td></tr>{% endfor %}
    </tbody></table></div><pre id="recent-job-output" class="api-output" hidden></pre>
  </div>
  <h2 class="h5 mb-3">{{ t('admin.events.new_activity') }}</h2>
  <div class="api-grid">
    <section class="api-card"><h3 class="h6">{{ t('admin.events.sensor_report') }}</h3><p class="api-hint">{{ t('admin.events.sensor_report_help') }}</p>
      <form id="event-form" class="api-form-grid mt-3"><div class="wide"><label>{{ t('admin.events.text') }}</label><textarea id="event-text" required class="form-control form-control-console"></textarea></div><div class="wide"><button class="btn btn-console-primary">{{ t('admin.events.send_report') }}</button></div></form><pre id="event-output" class="api-output" hidden></pre></section>
    <section class="api-card"><h3 class="h6">{{ t('admin.events.user_message') }}</h3><p class="api-hint">{{ t('admin.events.user_message_help') }}</p>
      <form id="message-form" class="api-form-grid mt-3">
        <div class="wide"><label>{{ t('admin.events.text') }}</label><textarea id="message-text" required class="form-control form-control-console"></textarea></div>
        <div><label>{{ t('admin.events.conversation') }}</label><input id="message-conversation" class="form-control form-control-console"></div><div><label>{{ t('admin.events.source_message') }}</label><input id="message-source" class="form-control form-control-console"></div>
        <div><label>{{ t('admin.events.chat_id') }}</label><input id="message-chat-id" class="form-control form-control-console"></div><div><label>{{ t('admin.events.chat_type') }}</label><select id="message-chat-type" class="form-select form-select-console"><option value="">—</option><option value="private">{{ t('admin.events.private_chat') }}</option><option value="group">{{ t('admin.events.group_chat') }}</option><option value="supergroup">{{ t('admin.events.supergroup_chat') }}</option></select></div>
        <div><label>{{ t('admin.events.preferred_protocol') }}</label><input id="message-protocol" class="form-control form-control-console"></div><div><label>{{ t('admin.events.related_event') }}</label><input id="message-event-data" class="form-control form-control-console"></div>
        <div class="wide"><button class="btn btn-console-primary">{{ t('admin.events.send_message') }}</button></div>
      </form><pre id="message-output" class="api-output" hidden></pre></section>
    <section class="api-card"><h3 class="h6">{{ t('admin.events.find_job') }}</h3><p class="api-hint">{{ t('admin.events.find_job_help') }}</p>
      <form id="job-form" class="api-form-grid mt-3"><div class="wide"><label>{{ t('admin.events.event_id') }}</label><input id="job-id" required class="form-control form-control-console"></div><div class="wide"><button class="btn btn-console-primary">{{ t('admin.events.check_status') }}</button></div></form><pre id="job-output" class="api-output" hidden></pre></section>
    <section class="api-card"><div class="d-flex justify-content-between align-items-center gap-2"><h3 class="h6 mb-0">{{ t('admin.events.pending_holds') }}</h3><button id="holds-get" class="btn btn-console btn-sm">{{ t('admin.api.refresh') }}</button></div><p class="api-hint mt-3">{{ t('admin.events.holds_help') }}</p><div id="holds-list" class="api-list"><div class="api-list-item">{{ t('admin.api.loading') }}</div></div><pre id="holds-output" class="api-output" hidden></pre><pre id="hold-action-output" class="api-output" hidden></pre></section>
    <section class="api-card"><div class="d-flex justify-content-between align-items-center gap-2"><h3 class="h6 mb-0">{{ t('admin.events.notifications') }}</h3><button id="notifications-refresh" class="btn btn-console btn-sm">{{ t('admin.api.refresh') }}</button></div>
      <div class="api-form-grid mt-3"><div><label>since</label><input id="notifications-since" type="number" min="0" value="0" class="form-control form-control-console"></div><div><label>wait_seconds</label><input id="notifications-wait" type="number" min="0" max="30" value="0" class="form-control form-control-console"></div></div><div id="notifications-list" class="api-list"><div class="api-list-item">{{ t('admin.api.loading') }}</div></div><pre id="notifications-output" class="api-output" hidden></pre></section>
    <section class="api-card"><h3 class="h6">{{ t('admin.events.live_trace') }}</h3><p class="api-hint">{{ t('admin.events.live_trace_help') }}</p>
      <form id="trace-form" class="api-form-grid mt-3"><div class="wide"><label>{{ t('admin.events.trace_id') }}</label><input id="trace-id" required class="form-control form-control-console"></div><div><label>{{ t('admin.events.from_cursor') }}</label><input id="trace-since" type="number" min="0" value="0" class="form-control form-control-console"></div><div><label>{{ t('admin.events.wait_seconds') }}</label><input id="trace-wait" type="number" min="0" max="30" value="0" class="form-control form-control-console"></div><div class="wide"><button class="btn btn-console-primary">{{ t('admin.events.show_log') }}</button></div></form><pre id="trace-output" class="api-output" hidden></pre></section>
    <section class="api-card"><h3 class="h6">{{ t('admin.events.attendance') }}</h3><p class="api-hint">{{ t('admin.events.attendance_help') }}</p>
      <form id="attendance-form" class="api-form-grid mt-3"><div><label>{{ t('admin.events.check_time') }}</label><input id="attendance-now" type="datetime-local" class="form-control form-control-console"></div><div class="form-check align-self-end"><input id="attendance-force" type="checkbox" class="form-check-input"><label for="attendance-force" class="form-check-label">{{ t('admin.events.force_check') }}</label></div><div class="wide"><button class="btn btn-console-primary">{{ t('admin.events.start_check') }}</button></div></form><pre id="attendance-output" class="api-output" hidden></pre></section>
  </div>
  <datalist id="event-types">{% for event_type in event_types %}<option value="{{ event_type }}">{% endfor %}</datalist>
</div>
""" + API_CLIENT_SCRIPT + """
<script>
document.getElementById('event-form').addEventListener('submit', event => { event.preventDefault(); AdminApi.call('POST','/Event',{text:AdminApi.value('event-text'),sender_identity:AdminApi.identity},'event-output'); });
document.getElementById('message-form').addEventListener('submit', event => { event.preventDefault(); const body={text:AdminApi.value('message-text'),sender_identity:AdminApi.identity}; AdminApi.optional(body,'conversation_id','message-conversation'); AdminApi.optional(body,'source_message_id','message-source'); AdminApi.optional(body,'telegram_chat_id','message-chat-id'); AdminApi.optional(body,'telegram_chat_type','message-chat-type'); AdminApi.optional(body,'protocol_hint','message-protocol'); AdminApi.optional(body,'event_data_event_id','message-event-data'); AdminApi.call('POST','/Msg',body,'message-output'); });
document.getElementById('job-form').addEventListener('submit', event => { event.preventDefault(); AdminApi.call('GET',`/Job/${encodeURIComponent(AdminApi.value('job-id'))}`,undefined,'job-output'); });
document.querySelectorAll('.recent-job').forEach(button => button.addEventListener('click', () => { const output=document.getElementById('recent-job-output'); output.hidden=false; AdminApi.call('GET',`/Job/${encodeURIComponent(button.dataset.eventId)}`,undefined,'recent-job-output'); }));
const uiText={emptyHolds:{{ t('admin.events.no_holds')|tojson }},resolve:{{ t('admin.events.resolve')|tojson }},approve:{{ t('admin.events.approve')|tojson }},reject:{{ t('admin.events.reject')|tojson }},emptyNotifications:{{ t('admin.events.no_notifications')|tojson }},failed:{{ t('admin.api.failed')|tojson }}};
function field(tag,className,text){const element=document.createElement(tag);if(className)element.className=className;element.textContent=text;return element;}
async function loadHolds(){
  const output=document.getElementById('holds-output'); const result=await AdminApi.call('GET','/Holds/Pending',undefined,'holds-output'); output.hidden=true;
  const list=document.getElementById('holds-list'); list.replaceChildren();
  if(!result.ok||!result.payload){list.append(field('div','api-list-item',result.payload?.message||uiText.failed));return;}
  if(!result.payload.holds.length){list.append(field('div','api-list-item',uiText.emptyHolds));return;}
  result.payload.holds.forEach(hold=>{const card=field('div','api-list-item','');card.append(field('strong','',`${hold.kind} · ${hold.event_id}`));card.append(field('p','api-hint mt-2',hold.raw_text||hold.reason||''));
    if(hold.kind==='clarification'){const select=document.createElement('select');select.className='form-select form-select-console mb-2';(hold.available_classifications||[]).forEach(value=>{const option=document.createElement('option');option.value=value;option.textContent=value;select.append(option);});const button=field('button','btn btn-console-primary btn-sm',uiText.resolve);button.addEventListener('click',async()=>{const action=document.getElementById('hold-action-output');action.hidden=false;await AdminApi.call('POST',`/Clarify/${encodeURIComponent(hold.event_id)}`,{classification:select.value},'hold-action-output');loadHolds();});card.append(select,button);}
    else{const approve=field('button','btn btn-console-primary btn-sm me-2',uiText.approve);const reject=field('button','btn btn-console-danger btn-sm',uiText.reject);approve.addEventListener('click',async()=>{const action=document.getElementById('hold-action-output');action.hidden=false;await AdminApi.call('POST',`/Approve/${encodeURIComponent(hold.event_id)}`,{decision:'approved'},'hold-action-output');loadHolds();});reject.addEventListener('click',async()=>{const action=document.getElementById('hold-action-output');action.hidden=false;await AdminApi.call('POST',`/Approve/${encodeURIComponent(hold.event_id)}`,{decision:'rejected'},'hold-action-output');loadHolds();});card.append(approve,reject);}list.append(card);});
}
document.getElementById('holds-get').addEventListener('click',loadHolds);
async function loadNotifications(){const query=new URLSearchParams({since:AdminApi.value('notifications-since'),wait_seconds:AdminApi.value('notifications-wait')});const output=document.getElementById('notifications-output');const result=await AdminApi.call('GET',`/Notifications?${query}`,undefined,'notifications-output');output.hidden=true;const list=document.getElementById('notifications-list');list.replaceChildren();if(!result.ok||!result.payload){list.append(field('div','api-list-item',result.payload?.message||uiText.emptyNotifications));return;}document.getElementById('notifications-since').value=result.payload.next_cursor;if(!result.payload.notifications.length){list.append(field('div','api-list-item',uiText.emptyNotifications));return;}result.payload.notifications.forEach(item=>{const card=field('div','api-list-item','');const payload=item.payload||{};card.append(field('strong','',`${item.kind} · ${payload.event_id||payload.job_id||''}`));const description=payload.raw_text||payload.question||payload.reason||payload.insight_text||payload.outcome||'';if(description)card.append(field('p','api-hint mt-2 mb-0',description));list.append(card);});}
document.getElementById('notifications-refresh').addEventListener('click',loadNotifications);
document.getElementById('trace-form').addEventListener('submit', event => { event.preventDefault(); const trace=encodeURIComponent(AdminApi.value('trace-id')); const query=new URLSearchParams({since:AdminApi.value('trace-since'),wait_seconds:AdminApi.value('trace-wait')}); AdminApi.call('GET',`/Trace/${trace}?${query}`,undefined,'trace-output'); });
document.getElementById('attendance-form').addEventListener('submit', event => { event.preventDefault(); const body={force:AdminApi.checked('attendance-force')}; AdminApi.optional(body,'now_iso','attendance-now'); AdminApi.call('POST','/TeamStatus/AttendanceCheck',body,'attendance-output'); });
loadHolds(); loadNotifications();
</script></body>
"""
