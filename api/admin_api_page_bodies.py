"""HTML bodies for the admin profiles, protocols, and events consoles."""

from api.admin_api_console import API_CLIENT_SCRIPT, FLASH_MESSAGES

PROFILES_BODY = """
<div class="ls-page">
  <div class="ls-page-header">
    <div>
      <h1>{{ t('admin.profiles.title') }}</h1>
      <p class="subtitle">{{ t('admin.profiles.subtitle') }}</p>
    </div>
  </div>
  """ + FLASH_MESSAGES + """
  <section class="ls-section">
    <h2 class="ls-section-title">{{ t('admin.profiles.active') }}</h2>
    <div class="block-console">
      <h2 class="h5 mb-2">{{ profile_name }}</h2>
      <div class="identity tech">{{ profile_module }}</div>
      <div class="ls-status-strip mt-3 mb-0">
        <div class="ls-status-chip">{{ t('admin.profiles.agents_count', count=agents|length) }}</div>
        <div class="ls-status-chip">{{ t('admin.profiles.protocols_count', count=protocols|length) }}</div>
      </div>
    </div>
  </section>
  <section class="ls-section">
    <h2 class="ls-section-title">{{ t('admin.profiles.operational_scope') }}</h2>
    <div class="block-console">
      <div class="mb-3"><strong>{{ t('admin.profiles.event_types') }}</strong><div class="mt-2">{% for item in event_types %}<span class="tag">{{ item }}</span> {% else %}—{% endfor %}</div></div>
      <div><strong>{{ t('admin.profiles.areas') }}</strong><div class="mt-2">{% for item in areas %}<span class="tag">{{ item }}</span> {% else %}—{% endfor %}</div></div>
    </div>
  </section>
  <section class="ls-section">
    <h2 class="ls-section-title">{{ t('admin.profiles.runtime_title') }}</h2>
    <div class="block-console">
      <div class="d-flex justify-content-between align-items-center gap-2 flex-wrap"><span class="block-label mb-0">{{ t('admin.profiles.settings_title') }}</span><button id="system-get" class="btn btn-console btn-sm">{{ t('admin.api.refresh') }}</button></div>
      <p class="api-hint mt-2">{{ t('admin.profiles.put_help') }}</p>
      <form id="system-put-form" class="api-form-grid">
        <div><label for="system-retry">{{ t('admin.profiles.retry_count') }}</label><input id="system-retry" type="number" min="0" value="{{ settings.retry_count }}" class="form-control form-control-console"></div>
        <div><label for="system-risk">{{ t('admin.profiles.risk_threshold') }}</label><input id="system-risk" type="number" min="0" max="1" step="0.01" value="{{ settings.risk_threshold }}" class="form-control form-control-console"></div>
        <div><label for="system-lookback">{{ t('admin.profiles.lookback_days') }}</label><input id="system-lookback" type="number" min="1" value="{{ settings.lookback_window_days }}" class="form-control form-control-console"></div>
        <div class="wide"><button class="btn btn-console-primary">{{ t('admin.save') }}</button></div>
      </form>
      <pre id="system-put-output" class="api-output" hidden></pre>
    </div>
    <pre id="system-get-output" class="api-output mb-0" hidden></pre>
  </section>
  <section class="ls-section">
    <h2 class="ls-section-title">{{ t('admin.profiles.components') }}</h2>
    <div class="block-console"><div class="api-form-grid"><div><strong>{{ t('admin.profiles.agents') }}</strong><div class="ls-chip-list mt-2">{% for item in agents %}<span class="tag tech">{{ item }}</span>{% endfor %}</div></div><div><strong>{{ t('admin.profiles.protocols') }}</strong><div class="ls-chip-list mt-2">{% for item in protocols %}<span class="tag tech">{{ item }}</span>{% endfor %}</div></div></div></div>
  </section>
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
</script>
"""

PROTOCOLS_BODY = """
<div class="ls-page-wide ls-protocols">
  <div class="ls-page-header">
    <div>
      <h1>{{ t('admin.protocols.title') }}</h1>
      <p class="subtitle">{{ t('admin.protocols.subtitle') }}</p>
    </div>
    <button id="protocol-list" class="btn btn-console">{{ t('admin.api.refresh') }}</button>
  </div>
  """ + FLASH_MESSAGES + """
  <datalist id="known-agents">{% for agent in agents %}<option value="{{ agent }}">{% endfor %}</datalist>
  <datalist id="known-tools">{% for tool in tools %}<option value="{{ tool }}">{% endfor %}</datalist>
  <div class="ls-table-toolbar">
    <h2 class="ls-section-title mb-0">{{ t('admin.protocols.existing', count=protocols|length) }}</h2>
    <p class="api-hint mb-0">{{ t('admin.protocols.restart_note') }}</p>
  </div>
  <div class="protocol-layout">
    <nav class="protocol-nav" data-protocol-nav aria-label="{{ t('admin.protocols.editor') }}">
      {% for protocol in protocols %}
      <button type="button" class="protocol-nav-item tech" data-protocol-target="protocol-{{ loop.index0 }}">{{ protocol.name }}</button>
      {% endfor %}
      <button type="button" class="protocol-nav-item protocol-nav-create" data-protocol-target="protocol-create">{{ t('admin.protocols.add') }}</button>
    </nav>
    <div>
      {% for protocol in protocols %}
      <section class="protocol-editor-block block-console mb-0" id="protocol-{{ loop.index0 }}" {% if not loop.first %}hidden{% endif %}>
        <form class="protocol-edit-form" data-name="{{ protocol.name }}">
          <div class="protocol-section">
            <h3 class="ls-section-title">{{ t('admin.protocols.summary') }}</h3>
            <div class="api-form-grid">
              <div><label>{{ t('admin.protocols.name') }}</label><input name="name" value="{{ protocol.name }}" readonly class="form-control form-control-console tech"></div>
              <div><label>{{ t('admin.protocols.criticality') }}</label><select name="criticality" class="form-select form-select-console">{% for level in ('low','medium','high') %}<option value="{{ level }}" {% if protocol.criticality == level %}selected{% endif %}>{{ level }}</option>{% endfor %}</select></div>
            </div>
          </div>
          <div class="protocol-section">
            <h3 class="ls-section-title">{{ t('admin.protocols.definition') }}</h3>
            <div class="api-form-grid">
              <div class="wide"><label>{{ t('admin.protocols.description') }}</label><textarea name="description" required class="form-control form-control-console">{{ protocol.description }}</textarea></div>
              <div class="wide"><label>{{ t('admin.protocols.success') }}</label><input name="success" value="{{ protocol.expected_success_output }}" required class="form-control form-control-console"></div>
            </div>
          </div>
          <div class="protocol-section">
            <h3 class="ls-section-title">{{ t('admin.protocols.routing') }}</h3>
            <div class="api-form-grid">
              <div><label>{{ t('admin.protocols.agents') }}</label><input name="agents" value="{{ protocol.participating_agents|join(', ') }}" required class="form-control form-control-console"></div>
              <div><label>{{ t('admin.protocols.tools') }}</label><input name="tools" value="{{ protocol.approved_tools|join(', ') }}" class="form-control form-control-console"></div>
            </div>
          </div>
          <div class="protocol-section">
            <h3 class="ls-section-title">{{ t('admin.protocols.advanced') }}</h3>
            <label class="form-check mb-0"><input name="approval" type="checkbox" class="form-check-input" {% if protocol.approval_flag %}checked{% endif %}> <span class="form-check-label">{{ t('admin.protocols.approval') }}</span></label>
          </div>
          <div class="ls-protocol-actions d-flex justify-content-between align-items-center gap-2 flex-wrap">
            <span class="ls-section-title mb-0">{{ t('admin.protocols.actions') }}</span>
            <div><button class="btn btn-console me-2">{{ t('admin.save') }}</button><button type="button" class="btn btn-console-danger protocol-delete">{{ t('admin.remove') }}</button></div>
          </div>
        </form>
      </section>
      {% else %}
      <div class="protocol-editor-block ls-empty-state" id="protocol-empty">{{ t('admin.protocols.none') }}</div>
      {% endfor %}
      <section class="protocol-editor-block block-console mb-0" id="protocol-create" hidden>
        <span class="block-label">{{ t('admin.protocols.add') }}</span>
        <form id="protocol-create-form" class="mt-1">
          <div class="protocol-section">
            <h3 class="ls-section-title">{{ t('admin.protocols.summary') }}</h3>
            <div class="api-form-grid">
              <div><label>{{ t('admin.protocols.name') }}</label><input id="create-name" required class="form-control form-control-console tech"></div>
              <div><label>{{ t('admin.protocols.criticality') }}</label><select id="create-criticality" class="form-select form-select-console"><option>low</option><option>medium</option><option>high</option></select></div>
            </div>
          </div>
          <div class="protocol-section">
            <h3 class="ls-section-title">{{ t('admin.protocols.definition') }}</h3>
            <div class="api-form-grid">
              <div class="wide"><label>{{ t('admin.protocols.description') }}</label><textarea id="create-description" required class="form-control form-control-console"></textarea></div>
              <div class="wide"><label>{{ t('admin.protocols.success') }}</label><input id="create-success" required class="form-control form-control-console"></div>
            </div>
          </div>
          <div class="protocol-section">
            <h3 class="ls-section-title">{{ t('admin.protocols.routing') }}</h3>
            <div class="api-form-grid">
              <div><label>{{ t('admin.protocols.agents') }}</label><input id="create-agents" list="known-agents" required class="form-control form-control-console" placeholder="agent_a, agent_b"></div>
              <div><label>{{ t('admin.protocols.tools') }}</label><input id="create-tools" list="known-tools" class="form-control form-control-console" placeholder="tool_a, tool_b"></div>
            </div>
          </div>
          <div class="protocol-section">
            <h3 class="ls-section-title">{{ t('admin.protocols.advanced') }}</h3>
            <div class="form-check"><input id="create-approval" type="checkbox" class="form-check-input"><label class="form-check-label" for="create-approval">{{ t('admin.protocols.approval') }}</label></div>
          </div>
          <div class="wide"><button class="btn btn-console-primary">{{ t('admin.add') }}</button></div>
        </form>
      </section>
    </div>
  </div>
  <pre id="protocol-status" class="api-output mt-3" hidden></pre>
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
</script>
"""

EVENTS_BODY = """
<div class="ls-page ls-events">
  <div class="ls-page-header">
    <div>
      <h1>{{ t('admin.events.title') }}</h1>
      <p class="subtitle">{{ t('admin.events.subtitle') }}</p>
    </div>
  </div>
  """ + FLASH_MESSAGES + """
  <div class="ls-tabs" data-ls-tabs data-initial="recent">
    <button type="button" class="ls-tab is-active" data-tab="recent">{{ t('admin.events.tab_recent') }}</button>
    <button type="button" class="ls-tab" data-tab="new">{{ t('admin.events.tab_new') }}</button>
  </div>
  <div data-panel="recent">
    <div class="ls-events-stage{% if recent_events %} has-events{% endif %}">
      <div class="ls-table-toolbar">
        <span class="block-label mb-0">{{ t('admin.events.recent', count=recent_events|length) }}</span>
        <span class="api-hint">{{ t('admin.events.recent_help') }}</span>
      </div>
      {% if recent_events %}
      <div class="table-responsive"><table class="table table-console mb-0"><thead><tr><th>{{ t('admin.events.received') }}</th><th>{{ t('admin.events.description') }}</th><th>{{ t('admin.events.sender') }}</th><th>{{ t('admin.events.classification') }}</th><th>{{ t('admin.events.status') }}</th><th>{{ t('admin.col_actions') }}</th></tr></thead><tbody>
        {% for item in recent_events %}<tr><td class="identity" title="{{ item.received_at }}"><span class="identity-id">{{ item.received_at }}</span></td><td><div>{{ item.text }}</div><small class="identity" title="{{ item.event_id }}">{{ item.event_id }}</small></td><td>{{ item.sender_name or item.sender_identity }}</td><td>{{ item.classification or '—' }}{% if item.area %} · {{ item.area }}{% endif %}</td><td><span class="tag">{{ item.status }}</span></td><td><button type="button" class="btn btn-console btn-sm recent-job" data-event-id="{{ item.event_id }}">{{ t('admin.events.check_status') }}</button></td></tr>{% endfor %}
      </tbody></table></div>
      {% else %}
      <div class="ls-empty-state">
        <svg class="ls-empty-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18"/><path d="M8 3v4"/><path d="M16 3v4"/></svg>
        <strong>{{ t('admin.events.empty_title') }}</strong>
        <p>{{ t('admin.events.none') }}</p>
        <p class="subtitle mt-2">{{ t('admin.events.empty_help') }}</p>
        <button type="button" class="btn btn-console-primary" data-tab-goto="new">{{ t('admin.events.tab_new') }}</button>
      </div>
      {% endif %}
      <pre id="recent-job-output" class="api-output" hidden></pre>
    </div>
    <div class="ls-events-tools">
      <section class="api-card"><h3 class="h6">{{ t('admin.events.find_job') }}</h3><p class="api-hint">{{ t('admin.events.find_job_help') }}</p>
        <form id="job-form" class="api-form-grid mt-3"><div class="wide"><label>{{ t('admin.events.event_id') }}</label><input id="job-id" required class="form-control form-control-console tech"></div><div class="wide"><button class="btn btn-console">{{ t('admin.events.check_status') }}</button></div></form><pre id="job-output" class="api-output" hidden></pre></section>
      <section class="api-card"><div class="d-flex justify-content-between align-items-center gap-2"><h3 class="h6 mb-0">{{ t('admin.events.pending_holds') }}</h3><button id="holds-get" class="btn btn-console btn-sm">{{ t('admin.api.refresh') }}</button></div><p class="api-hint mt-3">{{ t('admin.events.holds_help') }}</p><div id="holds-list" class="api-list"><div class="api-list-item">{{ t('admin.api.loading') }}</div></div><pre id="holds-output" class="api-output" hidden></pre><pre id="hold-action-output" class="api-output" hidden></pre></section>
      <section class="api-card"><div class="d-flex justify-content-between align-items-center gap-2"><h3 class="h6 mb-0">{{ t('admin.events.notifications') }}</h3><button id="notifications-refresh" class="btn btn-console btn-sm">{{ t('admin.api.refresh') }}</button></div>
        <div class="api-form-grid mt-3"><div><label>since</label><input id="notifications-since" type="number" min="0" value="0" class="form-control form-control-console"></div><div><label>wait_seconds</label><input id="notifications-wait" type="number" min="0" max="30" value="0" class="form-control form-control-console"></div></div><div id="notifications-list" class="api-list"><div class="api-list-item">{{ t('admin.api.loading') }}</div></div><pre id="notifications-output" class="api-output" hidden></pre></section>
    </div>
  </div>
  <div data-panel="new" hidden>
    <h2 class="h6 ls-section-title">{{ t('admin.events.new_activity') }}</h2>
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
      <section class="api-card"><h3 class="h6">{{ t('admin.events.live_trace') }}</h3><p class="api-hint">{{ t('admin.events.live_trace_help') }}</p>
        <form id="trace-form" class="api-form-grid mt-3"><div class="wide"><label>{{ t('admin.events.trace_id') }}</label><input id="trace-id" required class="form-control form-control-console tech"></div><div><label>{{ t('admin.events.from_cursor') }}</label><input id="trace-since" type="number" min="0" value="0" class="form-control form-control-console"></div><div><label>{{ t('admin.events.wait_seconds') }}</label><input id="trace-wait" type="number" min="0" max="30" value="0" class="form-control form-control-console"></div><div class="wide"><button class="btn btn-console">{{ t('admin.events.show_log') }}</button></div></form><pre id="trace-output" class="api-output" hidden></pre></section>
      <section class="api-card"><h3 class="h6">{{ t('admin.events.attendance') }}</h3><p class="api-hint">{{ t('admin.events.attendance_help') }}</p>
        <form id="attendance-form" class="api-form-grid mt-3"><div><label>{{ t('admin.events.check_time') }}</label><input id="attendance-now" type="datetime-local" class="form-control form-control-console"></div><div class="form-check align-self-end"><input id="attendance-force" type="checkbox" class="form-check-input"><label for="attendance-force" class="form-check-label">{{ t('admin.events.force_check') }}</label></div><div class="wide"><button class="btn btn-console">{{ t('admin.events.start_check') }}</button></div></form><pre id="attendance-output" class="api-output" hidden></pre></section>
    </div>
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
</script>
"""
