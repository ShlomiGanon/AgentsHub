"""Inline CSS for the admin scenario simulator page."""

SIMULATOR_STYLE = """
<style>
  .container-wide { max-width: 1400px; }
  .ls-page-wide.ls-simulator { max-width: 1400px; min-width: 0; overflow-x: hidden; }
  .ls-content:has(.ls-simulator) { overflow-x: hidden; padding: 16px 16px 32px; }
  .ls-simulator .ls-page-header { margin-bottom: 16px; }
  .ls-simulator .ls-page-header .subtitle { max-width: 48rem; }

  .sim-workspace {
    display: flex;
    flex-direction: column;
    gap: 16px;
    width: 100%;
    min-width: 0;
  }

  .sim-review, .sim-setup {
    background: var(--panel);
    border: 1px solid var(--line);
    border-radius: var(--radius);
    box-shadow: var(--shadow);
    min-width: 0;
    overflow: hidden;
    display: flex;
    flex-direction: column;
  }

  .sim-setup.is-busy .sim-setup-body { opacity: .7; pointer-events: none; }

  .sim-setup-head, .sim-review-head {
    flex-shrink: 0;
    display: flex;
    flex-direction: column;
    gap: 8px;
    padding: 14px 16px 12px;
    border-bottom: 1px solid var(--line);
    background: var(--panel);
  }
  .sim-panel-title {
    font-size: 16px; font-weight: 600; margin: 0;
    color: var(--navy); line-height: 1.3; letter-spacing: 0;
    text-transform: none;
  }
  .sim-review-head #scenario-title {
    font-size: 18px; font-weight: 600; margin: 0;
    color: var(--navy); line-height: 1.3;
  }
  .sim-review-head #sim-alert:empty { display: none; }
  .sim-review-head .alert-console,
  .sim-review-head .alert-console-error {
    margin: 0; padding: 8px 10px; font-size: 13px;
  }

  .sim-setup-body, .sim-review-scroll {
    min-width: 0;
    overflow-x: hidden;
    padding: 16px;
  }
  .sim-setup-body { display: flex; flex-direction: column; gap: 16px; }
  .sim-review-scroll { overflow-y: auto; max-height: min(72vh, 820px); }

  .sim-setup-section { display: flex; flex-direction: column; gap: 8px; min-width: 0; }
  .sim-select-row {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto;
    gap: 8px;
    align-items: stretch;
  }
  .sim-select-row select { min-width: 0; }
  .sim-select-actions { display: flex; gap: 8px; }
  .sim-select-actions .btn { white-space: nowrap; }
  #profile-sim-hint { font-size: 12px; margin: 0; color: var(--text-dim); }

  .sim-setup-divider {
    display: flex; align-items: center; gap: 10px;
    font-size: 11px; font-weight: 700; letter-spacing: .04em; text-transform: uppercase;
    color: var(--text-faint);
  }
  .sim-setup-divider::before, .sim-setup-divider::after {
    content: ""; flex: 1 1 auto; height: 1px; background: var(--line);
  }

  .sim-json-toggle {
    appearance: none; width: 100%;
    display: flex; align-items: center; justify-content: space-between; gap: 8px;
    background: var(--panel-muted); border: 1px solid var(--line);
    border-radius: var(--radius-sm); padding: 8px 12px;
    font-size: 13px; font-weight: 600; color: var(--text);
    cursor: pointer; text-align: start;
    transition: border-color .15s ease, background .15s ease, color .15s ease;
  }
  .sim-json-toggle::after { content: "+"; font-family: var(--mono); color: var(--text-faint); }
  .sim-json-toggle[aria-expanded="true"]::after { content: "–"; }
  .sim-json-toggle:hover { border-color: var(--blue); color: var(--blue); background: var(--blue-dim); }
  .sim-json-panel { display: flex; flex-direction: column; gap: 10px; min-width: 0; }
  .sim-json-panel[hidden] { display: none !important; }

  .sim-setup-status {
    display: flex; align-items: flex-start; gap: 8px;
    font-size: 13px; line-height: 1.4; padding: 8px 10px;
    border-radius: var(--radius-sm); background: var(--panel-muted);
  }
  .sim-setup-status[hidden] { display: none !important; }
  .sim-setup-status.is-busy { color: var(--text-dim); }
  .sim-setup-status.is-ok { color: #3f6212; background: var(--lime-dim); }
  .sim-setup-status.is-error { color: var(--danger); background: var(--danger-dim); }
  .sim-spinner {
    width: 14px; height: 14px; flex-shrink: 0; margin-top: 2px;
    border: 2px solid currentColor; border-inline-end-color: transparent;
    border-radius: 50%; animation: sim-spin .6s linear infinite;
  }
  @keyframes sim-spin { to { transform: rotate(360deg); } }

  .sim-run-toolbar {
    flex-shrink: 0;
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 8px;
    padding: 12px 16px;
    border-top: 1px solid var(--line);
    background: var(--panel);
  }
  .sim-run-toolbar .btn { min-width: 0; }
  .sim-run-toolbar .btn:hover:not(:disabled) { transform: translateY(-1px); }
  .sim-run-toolbar .btn:active:not(:disabled) { transform: translateY(1px); filter: brightness(.97); }
  .sim-run-toolbar .btn:disabled { opacity: .45; cursor: not-allowed; transform: none; }

  .sim-review {
    min-height: 280px;
    transition: border-color .35s ease, box-shadow .35s ease, background .35s ease, min-height .35s ease;
  }
  .sim-review.is-locked {
    background: linear-gradient(180deg, var(--panel) 0%, var(--panel-muted) 100%);
    border-style: dashed;
  }
  .sim-review.is-ready {
    min-height: 420px;
    border-color: var(--blue);
    box-shadow: 0 0 0 3px rgba(37, 99, 235, .12);
  }
  .sim-review.is-ready .sim-review-live {
    animation: sim-review-in .35s ease;
  }
  @keyframes sim-review-in {
    from { opacity: 0; transform: translateY(8px); }
    to { opacity: 1; transform: none; }
  }
  .sim-empty-state {
    min-height: 220px;
    display: flex; flex-direction: column; align-items: center; justify-content: center;
    gap: 10px; text-align: center; padding: 32px 16px;
  }
  .sim-review.is-ready .sim-empty-state { display: none; }
  .sim-empty-mark {
    width: 48px; height: 48px; border-radius: 50%;
    border: 2px dashed var(--line-strong);
    background:
      linear-gradient(var(--text-faint), var(--text-faint)) center / 16px 2px no-repeat,
      linear-gradient(var(--text-faint), var(--text-faint)) center / 2px 16px no-repeat;
    opacity: .55;
  }
  .sim-review .description { color: var(--text-dim); font-size: 14px; margin: 0; line-height: 1.5; }

  .sim-step-head { display: flex; align-items: center; gap: 8px; }
  .sim-step-num {
    width: 22px; height: 22px; border-radius: 50%; flex-shrink: 0;
    display: grid; place-items: center; background: var(--navy); color: #fff;
    font-size: 11px; font-weight: 700;
  }
  .sim-setup .sim-step-num { background: var(--lime); color: var(--lime-text); }
  .sim-review.is-ready .sim-step-num { background: var(--blue); color: #fff; }
  .sim-step-label {
    font-size: 11px; font-weight: 700; letter-spacing: .04em; text-transform: uppercase; color: var(--text-faint);
  }
  .sim-drop {
    border: 1.5px dashed var(--line-strong);
    border-radius: var(--radius-sm);
    padding: 12px;
    text-align: center;
    cursor: pointer;
    background: var(--panel-muted);
    color: var(--text-dim);
    font-size: 13px;
    display: flex; align-items: center; justify-content: center;
    min-height: 56px;
    transition: border-color .15s ease, background .15s ease, color .15s ease;
  }
  .sim-drop.dragover { border-color: var(--lime); background: var(--lime-dim); color: #3f6212; }
  .sim-paste { display: flex; flex-direction: column; gap: 6px; min-width: 0; }
  .sim-paste textarea, .sim-json-viewer {
    min-height: 120px; max-height: 220px; resize: vertical;
    overflow: auto; font-family: var(--mono); font-size: 12px; line-height: 1.45;
    width: 100%; box-sizing: border-box;
  }
  .sim-actions { display: flex; flex-direction: column; gap: 6px; justify-content: center; }
  .sim-actions .btn { min-width: 170px; }

  @media (max-width: 720px) {
    .sim-select-row,
    .sim-run-toolbar { grid-template-columns: 1fr; }
    .sim-select-actions { flex-direction: column; }
    .sim-json-viewer { max-height: 160px; }
    .ls-content:has(.ls-simulator) { padding: 12px 12px 28px; }
  }
  @media (prefers-reduced-motion: reduce) {
    .sim-review, .sim-run-toolbar .btn, .sim-json-toggle, .sim-drop { transition: none; }
    .sim-review.is-ready .sim-review-live, .sim-spinner { animation: none; }
  }
  .sim-badges { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 10px; }
  .sim-badge {
    font-family: var(--mono);
    font-size: 12px;
    background: var(--viewer-dim);
    color: var(--viewer);
    padding: 2px 10px;
    border-radius: 10px;
  }
  .sim-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 380px), 1fr)); gap: 20px; }
  .chat-card {
    background: var(--panel);
    border: 1px solid var(--line);
    border-radius: var(--radius);
    display: flex; flex-direction: column;
    height: auto;
    min-width: 0;
    min-height: 480px;
    transition: border-color 0.3s ease, box-shadow 0.3s ease, transform 0.3s ease;
    box-shadow: var(--shadow);
  }
  .chat-card:hover { box-shadow: var(--shadow-lg); }
  .chat-card.active-next { border-color: var(--lime); box-shadow: 0 0 0 3px rgba(132,204,22,.28); }
  @media (prefers-reduced-motion: reduce) {
    .chat-card { transition: none; }
  }
  .chat-header { padding: 18px 22px; border-bottom: 1px solid var(--line); flex-shrink: 0; }
  .chat-title { font-weight: 600; font-size: 18px; line-height: 1.35; }
  .chat-meta { font-family: var(--mono); font-size: 13px; color: var(--text-faint); margin-top: 6px; line-height: 1.45; }
  .route-badge {
    display: inline-block;
    font-size: 13px;
    padding: 4px 12px;
    border-radius: 10px;
    background: var(--viewer-dim);
    color: var(--viewer);
    margin-top: 10px;
  }
  .route-badge.warn { background: var(--danger-dim); color: var(--danger); }
  .chat-messages {
    flex: 1 1 auto;
    overflow: visible;
    padding: 22px 24px;
    display: flex; flex-direction: column; gap: 16px;
    background: #fff;
    min-height: calc(120px + 44px);
  }
  .bubble {
    border-radius: 12px;
    padding: 16px 20px;
    font-size: 17px;
    min-height: 120px;
    box-sizing: border-box;
    border-inline-start: 4px solid var(--viewer);
    background: var(--viewer-dim);
    animation: sim-fade 0.25s ease-in-out;
  }
  .bubble.sys { border-inline-start-color: var(--commander); background: var(--commander-dim); }
  .bubble.err { border-inline-start-color: var(--danger); background: var(--danger-dim); }
  @keyframes sim-fade { from { opacity: 0; transform: translateY(4px); } to { opacity: 1; transform: none; } }
  .bubble-head {
    display: flex; justify-content: space-between; gap: 12px;
    font-family: var(--mono); font-size: 13px; color: var(--text-faint);
    margin-bottom: 8px;
    line-height: 1.4;
  }
  .bubble-head .sender { color: var(--text-dim); font-weight: 600; }
  .bubble-text { margin: 0; white-space: pre-wrap; font-size: 17px; line-height: 1.7; }
  .bubble-status { font-family: var(--mono); font-size: 13px; color: var(--text-dim); margin-top: 10px; line-height: 1.5; }
  .bubble-step { font-size: 13px; color: var(--text-faint); margin-top: 8px; line-height: 1.45; }
  .chat-footer {
    margin-top: auto;
    flex-shrink: 0;
    display: flex;
    flex-direction: column;
    justify-content: flex-end;
    padding: 16px 22px 20px;
    border-top: 1px solid var(--line);
  }
  .preview-box {
    border: 1px dashed var(--line-strong);
    border-radius: 12px;
    padding: 16px 20px;
    margin-bottom: 12px;
    font-size: 17px;
    line-height: 1.7;
    background: var(--bg);
    min-height: 120px;
    box-sizing: border-box;
  }
  .chat-card.active-next .preview-box { border-style: solid; border-color: var(--commander); background: var(--commander-dim); }
  .preview-title {
    display: flex; justify-content: space-between; gap: 8px;
    font-family: var(--mono); font-size: 12px; color: var(--text-faint);
    margin-bottom: 8px;
  }
  .preview-content {
    white-space: pre-wrap;
    overflow: visible;
    font-size: 17px;
    line-height: 1.7;
  }
  .preview-warn { color: var(--danger); font-size: 12px; margin-top: 4px; }
  .preview-title-actions { display: flex; align-items: center; gap: 8px; flex-shrink: 0; }
  .send-btn { width: 100%; }
  .chat-footer .send-btn {
    flex-shrink: 0;
    min-height: 48px;
    font-size: 15px;
  }
  .sim-edit-overlay {
    position: fixed; inset: 0; z-index: 80;
    display: flex; align-items: center; justify-content: center;
    background: rgba(11, 31, 58, .45);
    padding: 24px;
  }
  .sim-edit-overlay[hidden] { display: none !important; }
  .sim-edit-dialog { width: min(560px, 100%); max-height: 90vh; overflow: auto; }
  .sim-edit-dialog textarea { min-height: 120px; }
  .sim-edit-dialog .form-label-console { margin-top: 12px; }
  .sim-edit-actions { display: flex; gap: 8px; justify-content: flex-end; margin-top: 16px; }
  .sim-empty { color: var(--text-dim); font-size: 15px; padding: 24px 0; text-align: center; }
  .mapping-panel { display:none; margin-bottom:20px; }
  .mapping-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(260px,1fr)); gap:12px; }
  .mapping-field label { display:block; color:var(--text-dim); font-size:12px; margin-bottom:4px; }
  .expected-actions { margin-top:14px; color:var(--text-dim); font-size:13px; }
  .expected-actions li { margin-bottom:4px; }
  .btn-trace-link {
    background: transparent;
    border: 1px solid var(--line-strong);
    border-radius: 4px;
    font-size: 10px;
    line-height: 1.2;
    padding: 2px 6px;
    color: var(--commander);
    font-weight: 600;
    cursor: pointer;
    font-family: var(--mono);
    transition: background 0.15s, color 0.15s;
  }
  .btn-trace-link:hover {
    background: var(--commander);
    color: #fff;
  }
  /* Live Agent Execution Graph - Premium Dark Cyber-Ops Theme */
  .bts-drawer {
    position: fixed;
    top: 0;
    inset-inline-end: 0;
    width: min(1180px, 96vw);
    height: 100vh;
    background: #080c16;
    color: #e2e8f0;
    box-shadow: -8px 0 36px rgba(0,0,0,0.65);
    z-index: 1050;
    display: flex;
    flex-direction: column;
    border-inline-start: 1px solid #1e293b;
    font-family: var(--sans, system-ui, -apple-system, sans-serif);
  }
  .bts-overlay {
    position: fixed;
    top: 0;
    left: 0;
    right: 0;
    bottom: 0;
    background: rgba(2, 6, 23, 0.65);
    backdrop-filter: blur(4px);
    z-index: 1040;
  }
  .bts-header {
    padding: 12px 20px;
    border-bottom: 1px solid #1e293b;
    background: #0d1527;
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  .bts-header-title {
    font-size: 16px;
    font-weight: 700;
    color: #f8fafc;
    letter-spacing: -0.2px;
  }
  .btn-console-close {
    background: transparent;
    border: 1px solid transparent;
    font-size: 16px;
    cursor: pointer;
    color: #94a3b8;
    line-height: 1;
    padding: 5px 10px;
    border-radius: 6px;
    transition: all 0.15s;
  }
  .btn-console-close:hover {
    background: rgba(239, 68, 68, 0.15);
    color: #f87171;
    border-color: rgba(239, 68, 68, 0.3);
  }
  .bts-badge {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    padding: 2px 10px;
    border-radius: 9999px;
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.2px;
  }
  .bts-badge-live {
    background: rgba(239, 68, 68, 0.15);
    color: #f87171;
    border: 1px solid rgba(239, 68, 68, 0.35);
    animation: bts-pulse 1.3s infinite ease-in-out;
  }
  .bts-badge-completed {
    background: rgba(16, 185, 129, 0.15);
    color: #34d399;
    border: 1px solid rgba(16, 185, 129, 0.35);
  }
  .bts-badge-failed {
    background: rgba(239, 68, 68, 0.15);
    color: #f87171;
    border: 1px solid rgba(239, 68, 68, 0.35);
  }
  .bts-badge-pending {
    background: rgba(100, 116, 139, 0.15);
    color: #94a3b8;
    border: 1px solid rgba(100, 116, 139, 0.25);
  }
  .bts-badge-active-count {
    background: rgba(56, 189, 248, 0.15);
    color: #38bdf8;
    border: 1px solid rgba(56, 189, 248, 0.35);
    font-size: 11px;
    font-weight: 600;
    padding: 2px 9px;
    border-radius: 9999px;
  }
  @keyframes bts-pulse {
    0%, 100% { opacity: 1; transform: scale(1); }
    50% { opacity: 0.7; transform: scale(0.96); }
  }
  .bts-code-pill {
    font-family: var(--mono, monospace);
    font-size: 11px;
    background: #1e293b;
    padding: 2px 7px;
    border-radius: 4px;
    color: #cbd5e1;
    border: 1px solid #334155;
  }
  /* KPI Grid */
  .bts-kpi-grid {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 10px;
    padding: 10px 20px;
    background: #090e1c;
    border-bottom: 1px solid #1e293b;
  }
  .bts-kpi-card {
    background: #10172a;
    border: 1px solid #1e293b;
    border-radius: 8px;
    padding: 8px 12px;
    display: flex;
    flex-direction: column;
    justify-content: space-between;
  }
  .bts-kpi-label {
    font-size: 11px;
    color: #94a3b8;
    margin-bottom: 2px;
    display: flex;
    align-items: center;
    gap: 4px;
  }
  .bts-kpi-val {
    font-size: 17px;
    font-weight: 700;
    color: #f1f5f9;
    font-family: var(--mono, monospace);
  }
  .bts-kpi-sub {
    font-size: 10px;
    color: #64748b;
    margin-top: 2px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  /* Graph Viewport Area */
  .bts-viewport-container {
    flex: 1;
    position: relative;
    overflow: hidden;
    background: #060912;
    background-image: radial-gradient(rgba(255, 255, 255, 0.08) 1px, transparent 1px);
    background-size: 24px 24px;
    display: flex;
    flex-direction: column;
  }
  .bts-graph-canvas {
    width: 100%;
    height: 100%;
    cursor: grab;
    user-select: none;
    -webkit-user-select: none;
    direction: ltr;
    unicode-bidi: isolate;
  }
  .bts-graph-canvas:active {
    cursor: grabbing;
  }
  /* Graph Floating Controls */
  .bts-graph-controls {
    position: absolute;
    bottom: 16px;
    inset-inline-start: 16px;
    display: flex;
    gap: 6px;
    background: rgba(15, 23, 42, 0.85);
    backdrop-filter: blur(8px);
    border: 1px solid #334155;
    border-radius: 8px;
    padding: 4px 6px;
    z-index: 10;
  }
  .bts-ctrl-btn {
    background: transparent;
    border: 1px solid transparent;
    color: #cbd5e1;
    font-size: 14px;
    width: 30px;
    height: 30px;
    display: flex;
    align-items: center;
    justify-content: center;
    border-radius: 6px;
    cursor: pointer;
    transition: all 0.15s;
  }
  .bts-ctrl-btn:hover {
    background: #1e293b;
    color: #f8fafc;
    border-color: #475569;
  }
  .bts-legend-bar {
    position: absolute;
    top: 14px;
    inset-inline-start: 16px;
    display: flex;
    align-items: center;
    gap: 12px;
    background: rgba(15, 23, 42, 0.85);
    backdrop-filter: blur(8px);
    border: 1px solid #1e293b;
    border-radius: 8px;
    padding: 6px 12px;
    font-size: 11px;
    color: #94a3b8;
    z-index: 10;
  }
  .bts-legend-item {
    display: flex;
    align-items: center;
    gap: 5px;
  }
  .bts-legend-dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
  }
  /* Edge Animations */
  @keyframes bts-flow {
    from { stroke-dashoffset: 28; }
    to { stroke-dashoffset: 0; }
  }
  .bts-edge-flow {
    stroke-dasharray: 7 5;
    animation: bts-flow 1.1s linear infinite;
  }
  /* Node Detail Flyout */
  .bts-node-detail-panel {
    position: absolute;
    top: 12px;
    inset-inline-end: 12px;
    bottom: 12px;
    width: 380px;
    max-width: calc(100% - 24px);
    background: rgba(15, 23, 42, 0.94);
    backdrop-filter: blur(14px);
    border: 1px solid #334155;
    border-radius: 10px;
    box-shadow: -8px 0 32px rgba(0,0,0,0.5);
    z-index: 20;
    display: flex;
    flex-direction: column;
    overflow: hidden;
    transition: transform 0.25s cubic-bezier(0.16, 1, 0.3, 1), opacity 0.2s;
  }
  .bts-node-detail-panel.hidden {
    transform: translateX(110%);
    opacity: 0;
    pointer-events: none;
  }
  [dir="rtl"] .bts-node-detail-panel.hidden {
    transform: translateX(-110%);
  }
  .bts-detail-head {
    padding: 12px 16px;
    border-bottom: 1px solid #1e293b;
    background: #0f172a;
    display: flex;
    justify-content: space-between;
    align-items: center;
  }
  .bts-detail-body {
    flex: 1;
    overflow-y: auto;
    padding: 14px 16px;
    display: flex;
    flex-direction: column;
    gap: 12px;
    font-size: 12px;
  }
  .bts-detail-section {
    background: #1e293b;
    border: 1px solid #334155;
    border-radius: 6px;
    padding: 10px 12px;
  }
  .bts-detail-sec-title {
    font-size: 11px;
    font-weight: 700;
    color: #94a3b8;
    text-transform: uppercase;
    letter-spacing: 0.4px;
    margin-bottom: 6px;
  }
  .bts-detail-prop-row {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 3px 0;
    border-bottom: 1px solid rgba(255,255,255,0.04);
  }
  .bts-detail-prop-row:last-child {
    border-bottom: none;
  }
  .bts-detail-prop-label {
    color: #94a3b8;
  }
  .bts-detail-prop-val {
    color: #f1f5f9;
    font-family: var(--mono, monospace);
  }
  /* Tool verification tags */
  .bts-vtag {
    font-size: 10px;
    font-weight: 600;
    padding: 2px 6px;
    border-radius: 4px;
    display: inline-block;
  }
  .bts-vtag-verified { background: rgba(16, 185, 129, 0.2); color: #34d399; border: 1px solid rgba(16, 185, 129, 0.4); }
  .bts-vtag-read_only { background: rgba(56, 189, 248, 0.2); color: #38bdf8; border: 1px solid rgba(56, 189, 248, 0.4); }
  .bts-vtag-unverified { background: rgba(245, 158, 11, 0.2); color: #fbbf24; border: 1px solid rgba(245, 158, 11, 0.4); }
  .bts-vtag-failed { background: rgba(239, 68, 68, 0.2); color: #f87171; border: 1px solid rgba(239, 68, 68, 0.4); }
  .bts-vtag-blocked { background: rgba(148, 163, 184, 0.2); color: #94a3b8; border: 1px solid rgba(148, 163, 184, 0.4); }
</style>
"""
