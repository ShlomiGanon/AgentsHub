"""Simulator page HTML fragments concatenated into SIMULATOR_BODY."""

SIMULATOR_HEADER = """
<div class="ls-page-wide ls-simulator">

  <div class="ls-page-header">
    <div>
      <h1 class="mb-1">{{ t('admin.simulator.title') }}</h1>
      <p class="subtitle mb-0">{{ t('admin.simulator.subtitle') }}</p>
    </div>
  </div>

  """

SIMULATOR_MAIN = """

  <div class="sim-workspace">
    <aside class="sim-setup" id="sim-setup" aria-labelledby="sim-setup-title">
      <div class="sim-setup-head">
        <h2 class="sim-panel-title" id="sim-setup-title">{{ t('admin.simulator.step_choose') }}</h2>
      </div>

      <div class="sim-setup-body">
        <div class="block-console mapping-panel" id="mapping-panel">
          <span class="block-label">{{ t('admin.simulator.mapping_title') }}</span>
          <p class="subtitle">{{ t('admin.simulator.mapping_help') }}</p>
          <div class="mapping-grid" id="mapping-fields"></div>
          <button type="button" class="btn btn-console-primary mt-3" id="apply-mapping">{{ t('admin.simulator.apply_mapping') }}</button>
        </div>

        <section class="sim-setup-section">
          <label class="form-label-console" for="profile-simulation-select">{{ t('admin.simulator.profile_simulations') }}</label>
          <div class="sim-select-row">
            <select id="profile-simulation-select" class="form-select form-select-console" {% if not page_data.profile_simulations %}disabled{% endif %}>
              <option value="">{{ t('admin.simulator.choose_profile_simulation') }}</option>
              {% for simulation in page_data.profile_simulations %}
              <option value="{{ simulation.key }}">{{ simulation.title or simulation.key }}</option>
              {% endfor %}
            </select>
            <div class="sim-select-actions">
              <button type="button" class="btn btn-console-primary" id="load-profile-simulation" disabled>{{ t('admin.simulator.load_profile_simulation') }}</button>
              <button type="button" class="btn btn-console" id="show-profile-simulation-json" disabled>{{ t('admin.simulator.show_simulation_json') }}</button>
            </div>
          </div>
          <div class="subtitle" id="profile-sim-hint">{% if page_data.profile_simulations_hint_key %}{{ t(page_data.profile_simulations_hint_key) }}{% endif %}</div>
        </section>

        <div class="sim-setup-divider">{{ t('admin.simulator.or_json') }}</div>

        <section class="sim-setup-section">
          <button type="button" class="sim-json-toggle" id="toggle-json-editor" aria-expanded="false" aria-controls="sim-json-panel">{{ t('admin.simulator.json_toggle') }}</button>
          <div class="sim-json-panel" id="sim-json-panel" hidden>
            <div class="sim-drop" id="drop-zone">
              <span>{{ t('admin.simulator.drop_zone') }}</span>
              <input type="file" id="file-input" accept=".json,application/json" style="display:none">
            </div>
            <div class="sim-paste">
              <div class="form-label-console">{{ t('admin.simulator.paste_label') }}</div>
              <textarea id="paste-input" class="form-control form-control-console sim-json-viewer" spellcheck="false" dir="ltr"></textarea>
              <button type="button" class="btn btn-console btn-sm" id="load-pasted">{{ t('admin.simulator.load_pasted') }}</button>
            </div>
          </div>
        </section>

        <div id="sim-setup-status" class="sim-setup-status" hidden></div>
      </div>

      <div class="sim-run-toolbar" role="group" aria-label="{{ t('admin.simulator.run_toolbar') }}">
        <button type="button" class="btn btn-console-primary send-btn" id="send-next" disabled>{{ t('admin.simulator.send_next') }}</button>
        <button type="button" class="btn btn-console-danger" id="reset-view">{{ t('admin.simulator.reset_view') }}</button>
        <button type="button" class="btn btn-console" id="toggle-bts" disabled title="{{ t('admin.simulator.bts.toggle_title') }}">{{ t('admin.simulator.bts.toggle_btn') }} ({{ t('admin.simulator.bts.separate_window') }})</button>
      </div>
    </aside>

    <section class="sim-review is-locked" id="scenario-info" aria-labelledby="sim-review-title">
      <div class="sim-review-head">
        <h2 class="sim-panel-title" id="sim-review-title">{{ t('admin.simulator.step_review') }}</h2>
        <h2 id="scenario-title">{{ t('admin.simulator.no_scenario') }}</h2>
        <div id="sim-alert"></div>
      </div>
      <div class="sim-review-scroll">
        <div class="sim-empty-state" id="sim-empty-state">
          <div class="sim-empty-mark" aria-hidden="true"></div>
          <p class="description" id="sim-empty-help">{{ t('admin.simulator.review_empty_help') }}</p>
        </div>
        <div class="sim-review-live" id="sim-review-live" hidden>
          <p id="scenario-desc" class="description"></p>
          <div class="sim-badges" id="scenario-badges"></div>
          <div class="expected-actions" id="expected-actions"></div>
          <div class="sim-grid" id="chats-container"></div>
        </div>
      </div>
    </section>
  </div>

  <div id="edit-step-overlay" class="sim-edit-overlay" hidden>
    <div class="block-console sim-edit-dialog" id="edit-step-dialog" role="dialog" aria-modal="true" aria-labelledby="edit-step-title">
      <span class="block-label" id="edit-step-title">{{ t('admin.simulator.edit') }}</span>
      <div class="form-label-console">{{ t('admin.simulator.edit_text') }}</div>
      <textarea id="edit-step-text" class="form-control form-control-console" dir="auto"></textarea>
      <div class="form-label-console">{{ t('admin.simulator.edit_sender') }}</div>
      <select id="edit-step-sender" class="form-select form-select-console"></select>
      <div class="form-label-console">{{ t('admin.simulator.edit_timestamp') }}</div>
      <input id="edit-step-timestamp" type="datetime-local" class="form-control form-control-console">
      <div class="sim-edit-actions">
        <button type="button" class="btn btn-console" id="edit-step-cancel">{{ t('admin.simulator.edit_cancel') }}</button>
        <button type="button" class="btn btn-console-primary" id="edit-step-save">{{ t('admin.simulator.edit_save') }}</button>
      </div>
    </div>
  </div>

</div>

<div id="bts-overlay" class="bts-overlay" style="display:none;"></div>
<aside id="bts-drawer" class="bts-drawer" style="display:none;" aria-label="Live Agent Execution Graph">
  <!-- Header -->
  <div class="bts-header">
    <div class="d-flex justify-content-between align-items-center">
      <div class="d-flex align-items-center gap-2">
        <span class="bts-header-title">🌐 Live Agent Execution Graph</span>
        <span id="bts-status-badge" class="bts-badge bts-badge-pending">{{ t('admin.simulator.bts.waiting_badge') }}</span>
        <span id="bts-active-badge" class="bts-badge-active-count">{{ t('admin.simulator.bts.active_requests', count=0) }}</span>
      </div>
      <button type="button" id="bts-close-btn" class="btn-console-close" title="{{ t('admin.simulator.bts.close') }}">✕</button>
    </div>
    <div class="d-flex justify-content-between align-items-center gap-2 flex-wrap" style="font-size:12px;">
      <div class="d-flex align-items-center gap-1">
        <span style="color:#94a3b8;">Trace ID:</span>
        <code id="bts-trace-id-label" class="bts-code-pill">—</code>
      </div>
      <div class="d-flex align-items-center gap-2">
        <label for="bts-recent-select" class="mb-0" style="font-size:11px; color:#94a3b8;">{{ t('admin.simulator.bts.recent_traces') }}</label>
        <select id="bts-recent-select" class="form-select form-select-sm" style="max-width:260px; font-size:11px; background:#1e293b; color:#f1f5f9; border-color:#334155;">
          <option value="">{{ t('admin.simulator.bts.select_trace') }}</option>
        </select>
      </div>
    </div>
  </div>

  <!-- KPI Metrics Bar -->
  <div class="bts-kpi-grid">
    <div class="bts-kpi-card">
      <div class="bts-kpi-label">⏱ {{ t('admin.simulator.bts.metric_wall_clock') }}</div>
      <div id="bts-metric-wall" class="bts-kpi-val">—</div>
      <div id="bts-metric-breakdown" class="bts-kpi-sub">{{ t('admin.simulator.bts.model_tools_breakdown', model='—', tools='—') }}</div>
    </div>
    <div class="bts-kpi-card">
      <div class="bts-kpi-label">🧠 {{ t('admin.simulator.bts.metric_llm_calls') }}</div>
      <div id="bts-metric-llm" class="bts-kpi-val">—</div>
      <div id="bts-metric-retries" class="bts-kpi-sub">{{ t('admin.simulator.bts.retries_count', count=0) }}</div>
    </div>
    <div class="bts-kpi-card">
      <div class="bts-kpi-label">📊 {{ t('admin.simulator.bts.metric_tokens') }}</div>
      <div id="bts-metric-tokens" class="bts-kpi-val">—</div>
      <div id="bts-metric-tokens-sub" class="bts-kpi-sub">{{ t('admin.simulator.bts.tokens_io_cache_label') }}</div>
    </div>
    <div class="bts-kpi-card">
      <div class="bts-kpi-label">{{ t('admin.simulator.bts.agents_tools_metric') }}</div>
      <div id="bts-metric-agents" class="bts-kpi-val">—</div>
      <div id="bts-metric-agents-sub" class="bts-kpi-sub">{{ t('admin.simulator.bts.parallel_branches', count=0) }}</div>
    </div>
  </div>

  <!-- Graph Viewport Area -->
  <div class="bts-viewport-container" id="bts-viewport">
    <!-- Graph Legend -->
    <div class="bts-legend-bar">
      <div class="bts-legend-item"><span class="bts-legend-dot" style="background:#38bdf8;"></span> {{ t('admin.simulator.bts.legend_main') }}</div>
      <div class="bts-legend-item"><span class="bts-legend-dot" style="background:#a855f7;"></span> {{ t('admin.simulator.bts.legend_specialist') }}</div>
      <div class="bts-legend-item"><span class="bts-legend-dot" style="background:#c084fc; box-shadow:0 0 6px #c084fc;"></span> {{ t('admin.simulator.bts.legend_parallel') }}</div>
      <div class="bts-legend-item"><span class="bts-legend-dot" style="background:#10b981;"></span> {{ t('admin.simulator.bts.legend_tool') }}</div>
      <div class="bts-legend-item"><span class="bts-legend-dot" style="background:#f59e0b;"></span> {{ t('admin.simulator.bts.legend_db') }}</div>
    </div>

    <!-- Floating Zoom/Pan Controls -->
    <div class="bts-graph-controls">
      <button type="button" class="bts-ctrl-btn" id="bts-zoom-in" title="{{ t('admin.simulator.bts.zoom_in') }}">+</button>
      <button type="button" class="bts-ctrl-btn" id="bts-zoom-out" title="{{ t('admin.simulator.bts.zoom_out') }}">−</button>
      <button type="button" class="bts-ctrl-btn" id="bts-zoom-fit" title="{{ t('admin.simulator.bts.zoom_fit') }}">⌖</button>
    </div>

    <!-- SVG Graph -->
    <svg id="bts-graph-svg" class="bts-graph-canvas" xmlns="http://www.w3.org/2000/svg" direction="ltr">
      <defs>
        <!-- Filter glow effects -->
        <filter id="bts-glow-cyan" x="-20%" y="-20%" width="140%" height="140%">
          <feDropShadow dx="0" dy="0" stdDeviation="6" flood-color="#38bdf8" flood-opacity="0.6"/>
        </filter>
        <filter id="bts-glow-purple" x="-20%" y="-20%" width="140%" height="140%">
          <feDropShadow dx="0" dy="0" stdDeviation="7" flood-color="#c084fc" flood-opacity="0.7"/>
        </filter>
        <filter id="bts-glow-emerald" x="-20%" y="-20%" width="140%" height="140%">
          <feDropShadow dx="0" dy="0" stdDeviation="5" flood-color="#10b981" flood-opacity="0.5"/>
        </filter>
        <filter id="bts-glow-red" x="-20%" y="-20%" width="140%" height="140%">
          <feDropShadow dx="0" dy="0" stdDeviation="6" flood-color="#ef4444" flood-opacity="0.6"/>
        </filter>

        <!-- Arrow Markers -->
        <marker id="marker-active" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">
          <polygon points="0 1, 8 4, 0 7" fill="#38bdf8"/>
        </marker>
        <marker id="marker-parallel" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">
          <polygon points="0 1, 8 4, 0 7" fill="#c084fc"/>
        </marker>
        <marker id="marker-completed" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">
          <polygon points="0 1, 8 4, 0 7" fill="#10b981"/>
        </marker>
        <marker id="marker-failed" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">
          <polygon points="0 1, 8 4, 0 7" fill="#ef4444"/>
        </marker>
        <marker id="marker-pending" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">
          <polygon points="0 1, 8 4, 0 7" fill="#475569"/>
        </marker>
      </defs>
      <g id="bts-graph-scene">
        <g id="bts-edges-layer"></g>
        <g id="bts-nodes-layer"></g>
      </g>
    </svg>

    <!-- Node Detail Flyout (Slides in on node click) -->
    <div id="bts-node-detail" class="bts-node-detail-panel hidden">
      <div class="bts-detail-head">
        <div class="d-flex align-items-center gap-2">
          <span id="bts-det-icon" style="font-size:18px;">🤖</span>
          <div>
            <div id="bts-det-title" style="font-size:14px; font-weight:700; color:#f8fafc;">—</div>
            <div id="bts-det-sub" style="font-size:10px; color:#94a3b8;">—</div>
          </div>
        </div>
        <button type="button" id="bts-det-close" class="btn-console-close" title="{{ t('admin.simulator.bts.close_details') }}">✕</button>
      </div>
      <div class="bts-detail-body">
        <!-- Status & Metrics Section -->
        <div class="bts-detail-section">
          <div class="bts-detail-sec-title">{{ t('admin.simulator.bts.detail_status_metrics') }}</div>
          <div class="bts-detail-prop-row">
            <span class="bts-detail-prop-label">{{ t('admin.simulator.bts.detail_status') }}</span>
            <span id="bts-det-status" class="bts-badge bts-badge-pending">—</span>
          </div>
          <div class="bts-detail-prop-row">
            <span class="bts-detail-prop-label">{{ t('admin.simulator.bts.detail_runtime') }}</span>
            <span id="bts-det-dur" class="bts-detail-prop-val">—</span>
          </div>
          <div class="bts-detail-prop-row">
            <span class="bts-detail-prop-label">{{ t('admin.simulator.bts.detail_calls') }}</span>
            <span id="bts-det-calls" class="bts-detail-prop-val">—</span>
          </div>
          <div class="bts-detail-prop-row">
            <span class="bts-detail-prop-label">{{ t('admin.simulator.bts.detail_parallel') }}</span>
            <span id="bts-det-parallel" class="bts-detail-prop-val">—</span>
          </div>
          <div id="bts-det-retries-row" class="bts-detail-prop-row" style="display:none;">
            <span class="bts-detail-prop-label">{{ t('admin.simulator.bts.detail_retries') }}</span>
            <span id="bts-det-retries" class="bts-detail-prop-val" style="color:#f87171;">0</span>
          </div>
        </div>

        <!-- Task / Directives Section -->
        <div class="bts-detail-section">
          <div id="bts-det-task-title" class="bts-detail-sec-title">{{ t('admin.simulator.bts.detail_task_asked') }}</div>
          <div id="bts-det-task-content" style="color:#cbd5e1; white-space:pre-wrap; line-height:1.5;">—</div>
        </div>

        <!-- Tools / Side-Effects Section -->
        <div id="bts-det-tools-section" class="bts-detail-section">
          <div class="bts-detail-sec-title">{{ t('admin.simulator.bts.detail_tools_verify') }}</div>
          <div id="bts-det-tools-list" class="d-flex flex-column gap-2 mt-1"></div>
        </div>

        <!-- Error / Note Section (if present) -->
        <div id="bts-det-error-section" class="bts-detail-section" style="display:none; border-color:#ef4444; background:rgba(239,68,68,0.08);">
          <div class="bts-detail-sec-title" style="color:#f87171;">{{ t('admin.simulator.bts.detail_error') }}</div>
          <div id="bts-det-error-content" style="color:#fca5a5;"></div>
        </div>
      </div>
    </div>
  </div>
</aside>

"""
