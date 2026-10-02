"""Browser-side API console CSS, flash markup, and client script."""

API_CONSOLE_STYLE = """
<style>
  .api-identity-bar { display:flex; gap:8px; align-items:end; flex-wrap:wrap; }
  .api-identity-bar .identity-field { min-width:240px; flex:1; }
  .api-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(280px,1fr)); gap:10px; }
  .api-card {
    border:1px solid var(--line); background:var(--panel); padding:14px 14px 12px; border-radius:var(--radius);
    box-shadow:var(--shadow);
  }
  .api-card h3 { margin-bottom: 2px; font-size: 15px; }
  .api-card.is-primary { border-inline-start: 3px solid var(--blue); }
  .api-card.is-muted { box-shadow: none; background: var(--panel-muted); }
  .api-output { direction:ltr; text-align:left; unicode-bidi:plaintext; white-space:pre-wrap; overflow-wrap:anywhere;
    min-height:72px; max-height:280px; overflow:auto; margin:12px 0 0; padding:10px 12px;
    border:1px solid var(--line); background:var(--panel-muted); font-size:12px; border-radius:var(--radius-sm); font-family:var(--mono); }
  .api-output[data-state="ok"] { border-inline-start:3px solid var(--lime); }
  .api-output[data-state="error"] { border-inline-start:3px solid var(--danger); }
  .api-output[data-state="loading"] { opacity:.72; }
  .api-hint { color:var(--text-dim); font-size:13px; line-height:1.5; }
  .api-form-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:10px; }
  .api-form-grid .wide { grid-column:1/-1; }
  .api-form-grid label { display:block; font-size:12px; font-weight:600; color:var(--text-dim); margin-bottom:5px; }
  .api-list { display:grid; gap:12px; margin-top:12px; }
  .api-list-item {
    border:1px solid var(--line); padding:16px; border-radius:var(--radius); background:var(--panel);
    box-shadow:var(--shadow);
  }
  .ls-field-group { display:contents; }
  .protocol-edit-form { row-gap: 10px; }
  .api-list-item.ls-empty { text-align:center; color:var(--text-dim); padding:28px 16px; }
  @media (max-width:640px) { .api-form-grid { grid-template-columns:1fr; } .api-form-grid .wide { grid-column:auto; } }

  .ls-content:has(.ls-events) { padding: 20px 28px 40px; }
  .ls-events { max-width: 1080px; }
  .ls-events .ls-page-header { margin-bottom: 16px; }
  .ls-events .ls-identity-block { margin-bottom: 14px; padding: 12px 16px; }
  .ls-events .ls-identity-block .api-hint { margin-top: 8px !important; }
  .ls-events .ls-tabs { margin: 0 0 16px; gap: 2px; }
  .ls-events .ls-tab { padding: 8px 12px; font-size: 13px; font-weight: 600; }

  .ls-events-stage {
    min-height: 360px;
    display: flex;
    flex-direction: column;
    border: 1px dashed var(--line-strong);
    border-radius: var(--radius);
    background: var(--panel);
    padding: 16px 20px 20px;
  }
  .ls-events-stage.has-events {
    border-style: solid;
    border-color: var(--line);
    box-shadow: var(--shadow);
  }
  .ls-events-stage .ls-table-toolbar { margin-bottom: 12px; }
  .ls-events-stage .table-responsive { flex: 1; }
  .ls-events-stage .ls-empty-state {
    flex: 1;
    min-height: 280px;
    margin: 0;
    padding: 56px 32px;
    border: 0;
    background: transparent;
    box-shadow: none;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    text-align: center;
  }
  .ls-events-stage .ls-empty-icon { width: 40px; height: 40px; margin: 0 auto 14px; color: var(--blue); }
  .ls-events-stage .ls-empty-state strong { font-size: 16px; margin-bottom: 8px; }
  .ls-events-stage .ls-empty-state p { max-width: 440px; }

  .ls-events-tools {
    margin-top: 28px;
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 14px;
    align-items: stretch;
  }
  .ls-events-tools .api-card {
    padding: 14px;
    display: flex;
    flex-direction: column;
    background: var(--panel);
    box-shadow: var(--shadow);
  }
  .ls-events-tools .api-card h3 { margin-bottom: 4px; font-size: 14px; font-weight: 600; }
  .ls-events-tools .api-hint { font-size: 12px; line-height: 1.4; margin-bottom: 0; }
  .ls-events-tools .api-form-grid { gap: 8px; margin-top: 10px; }
  .ls-events-tools .api-form-grid label { margin-bottom: 4px; }
  .ls-events-tools form { display: flex; flex-direction: column; flex: 1; }
  .ls-events-tools form .wide:last-child { margin-top: auto; padding-top: 10px; }
  .ls-events-tools form .btn { width: 100%; }
  .ls-events-tools .api-list { margin-top: 10px; gap: 8px; flex: 1; }
  .ls-events-tools .api-list-item {
    padding: 14px 12px;
    text-align: center;
    box-shadow: none;
    background: var(--panel-muted);
  }
  .ls-events [data-panel="new"] .ls-section-title { margin: 0 0 16px; }
  .ls-events [data-panel="new"] .api-grid { grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 16px; }
  .ls-events [data-panel="new"] .api-card { padding: 16px; }
  @media (max-width: 800px) { .ls-events-tools { grid-template-columns: 1fr; } }
</style>
"""

FLASH_MESSAGES = """
{% for category, message in get_flashed_messages(with_categories=true) %}
  <div class="alert-console{% if category == 'error' %}-error{% endif %} px-3 py-2 mb-3">{{ message }}</div>
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
