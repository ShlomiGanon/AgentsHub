"""Standalone Behind-the-Scenes page body markup."""

BTS_MARKUP = r"""
</head>
<body>

  <!-- Top Header Bar -->
  <header class="bts-header">
    <div class="bts-header-left">
      <div class="bts-title-group">
        <div class="bts-logo-badge">🌐</div>
        <div>
          <div class="bts-title">Live Agent Execution Graph</div>
          <div class="bts-subtitle">AgentsHub Multi-Agent Diagnostics</div>
        </div>
      </div>
      <div id="beacon" class="bts-live-beacon">
        <span class="bts-pulse-dot"></span>
        <span id="beacon-text">{{ t('admin.simulator.bts.live_broadcast') }}</span>
      </div>
    </div>

    <div class="bts-header-center">
      <select id="trace-select" class="bts-trace-select">
        <option value="">{{ t('admin.simulator.bts.loading_traces') }}</option>
      </select>
      <label class="bts-autofollow">
        <input type="checkbox" id="auto-follow-check" checked>
        {{ t('admin.simulator.bts.auto_follow') }}
      </label>
    </div>

    <div class="bts-header-right">
      <div id="trace-id-pill" class="bts-pill" title="{{ t('admin.simulator.bts.copy_trace') }}">—</div>
      <button id="refresh-btn" class="bts-btn" title="{{ t('admin.simulator.bts.refresh_data') }}">{{ t('admin.simulator.bts.refresh') }}</button>
    </div>
  </header>

  <!-- Performance Metrics Bar -->
  <section class="bts-metrics-bar">
    <div class="bts-metric-card">
      <div class="bts-metric-title">
        <span>{{ t('admin.simulator.bts.metric_wall_clock') }}</span>
        <span>⏱️</span>
      </div>
      <div id="m-wall" class="bts-metric-val">—</div>
      <div id="m-wall-breakdown" class="bts-metric-sub">{{ t('admin.simulator.bts.model_tools_breakdown', model='—', tools='—') }}</div>
    </div>

    <div class="bts-metric-card">
      <div class="bts-metric-title">
        <span>{{ t('admin.simulator.bts.metric_llm_calls') }}</span>
        <span>🧠</span>
      </div>
      <div id="m-llm" class="bts-metric-val">—</div>
      <div id="m-retries" class="bts-metric-sub">{{ t('admin.simulator.bts.retries_count', count=0) }}</div>
    </div>

    <div class="bts-metric-card">
      <div class="bts-metric-title">
        <span>{{ t('admin.simulator.bts.metric_tokens') }}</span>
        <span>📊</span>
      </div>
      <div id="m-tokens" class="bts-metric-val">—</div>
      <div id="m-tokens-sub" class="bts-metric-sub">{{ t('admin.simulator.bts.tokens_io', input='—', output='—') }}</div>
    </div>

    <div class="bts-metric-card">
      <div class="bts-metric-title">
        <span>{{ t('admin.simulator.bts.agents_tools_metric') }}</span>
        <span>👥</span>
      </div>
      <div id="m-agents" class="bts-metric-val">—</div>
      <div id="m-agents-sub" class="bts-metric-sub">{{ t('admin.simulator.bts.parallel_branches', count=0) }}</div>
    </div>
  </section>

  <!-- Main Split Workspace -->
  <main class="bts-workspace">
    <!-- Interactive Graph Canvas -->
    <div id="canvas-container" class="bts-canvas-container">
      <div class="bts-grid-pattern"></div>
      <svg id="graph-svg" class="bts-canvas-svg" xmlns="http://www.w3.org/2000/svg" direction="ltr">
        <defs>
          <marker id="arrow-active" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto">
            <path d="M 0 1.5 L 8 5 L 0 8.5 z" fill="#38bdf8"></path>
          </marker>
          <marker id="arrow-completed" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto">
            <path d="M 0 1.5 L 8 5 L 0 8.5 z" fill="#10b981"></path>
          </marker>
          <marker id="arrow-failed" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto">
            <path d="M 0 1.5 L 8 5 L 0 8.5 z" fill="#f43f5e"></path>
          </marker>
          <marker id="arrow-pending" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto">
            <path d="M 0 1.5 L 8 5 L 0 8.5 z" fill="#475569"></path>
          </marker>
        </defs>
        <g id="scene-group">
          <g id="edges-layer"></g>
          <g id="nodes-layer"></g>
        </g>
      </svg>

      <!-- Zoom Floating Controls -->
      <div class="bts-zoom-controls">
        <button id="zoom-in" class="bts-zoom-btn" title="{{ t('admin.simulator.bts.zoom_in') }}">+</button>
        <button id="zoom-out" class="bts-zoom-btn" title="{{ t('admin.simulator.bts.zoom_out') }}">−</button>
        <button id="zoom-fit" class="bts-zoom-btn" title="{{ t('admin.simulator.bts.zoom_fit') }}">⛶</button>
      </div>
    </div>

    <!-- Right Side Panel: Stream & Inspector -->
    <aside class="bts-right-panel">
      <div class="bts-panel-tabs">
        <button id="tab-stream-btn" class="bts-tab-btn is-active">
          <span>💬</span> {{ t('admin.simulator.bts.stream_tab') }}
        </button>
        <button id="tab-inspect-btn" class="bts-tab-btn">
          <span>🔍</span> {{ t('admin.simulator.bts.inspect_tab') }}
        </button>
      </div>

      <!-- Tab 1: Live Message Stream -->
      <div id="tab-stream" class="bts-tab-content is-active">
        <div id="stream-list">
          <div class="bts-inspect-empty">{{ t('admin.simulator.bts.stream_empty') }}</div>
        </div>
      </div>

      <!-- Tab 2: Node Inspector -->
      <div id="tab-inspect" class="bts-tab-content">
        <div id="inspector-content">
          <div class="bts-inspect-empty">{{ t('admin.simulator.bts.inspect_empty') }}</div>
        </div>
      </div>
    </aside>
  </main>

"""
