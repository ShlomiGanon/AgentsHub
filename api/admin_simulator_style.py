"""Inline CSS for the admin scenario simulator page."""

SIMULATOR_STYLE = """
<style>
  .container-wide { max-width: 1400px; }
  .sim-toolbar { display: grid; grid-template-columns: minmax(220px, .9fr) 16px minmax(280px, 1.2fr) 16px minmax(220px, .8fr); gap: 0; align-items: stretch; margin-bottom: 16px; }
  .sim-flow-join { align-self: center; height: 2px; background: var(--line-strong); }
  @media (max-width: 980px) { .sim-toolbar { grid-template-columns: 1fr; } .sim-flow-join { height: 16px; width: 2px; justify-self: center; } }
  .sim-step {
    background: var(--panel);
    border: 1px solid var(--line);
    border-radius: var(--radius);
    padding: 12px;
    box-shadow: var(--shadow);
    display: flex; flex-direction: column; gap: 8px;
  }
  .sim-step-head { display: flex; align-items: center; gap: 8px; }
  .sim-step-num {
    width: 22px; height: 22px; border-radius: 50%; flex-shrink: 0;
    display: grid; place-items: center; background: var(--navy); color: #fff;
    font-size: 11px; font-weight: 700;
  }
  .sim-step.is-primary .sim-step-num { background: var(--lime); color: var(--lime-text); }
  .sim-step-label {
    font-size: 11px; font-weight: 700; letter-spacing: .04em; text-transform: uppercase; color: var(--text-faint);
  }
  .sim-drop {
    flex: 1 1 auto;
    border: 1.5px dashed var(--line-strong);
    border-radius: var(--radius-sm);
    padding: 14px;
    text-align: center;
    cursor: pointer;
    background: var(--panel-muted);
    color: var(--text-dim);
    font-size: 13px;
    display: flex; align-items: center; justify-content: center;
    min-height: 72px;
  }
  .sim-drop.dragover { border-color: var(--lime); background: var(--lime-dim); color: #3f6212; }
  .sim-paste { flex: 1 1 320px; display: flex; flex-direction: column; gap: 6px; }
  .sim-paste textarea { min-height: 72px; resize: vertical; }
  .sim-actions { display: flex; flex-direction: column; gap: 6px; justify-content: center; }
  .sim-actions .btn { min-width: 170px; }
  .sim-header {
    background: var(--panel);
    border: 1px solid var(--line);
    border-radius: var(--radius);
    padding: 16px 18px;
    margin-bottom: 16px;
    box-shadow: var(--shadow);
  }
  .sim-header h2 { font-size: 20px; font-weight: 500; margin: 0 0 6px; }
  .sim-header .description { color: var(--text-dim); font-size: 15px; margin: 0; line-height: 1.5; }
  .sim-badges { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 10px; }
  .sim-badge {
    font-family: var(--mono);
    font-size: 12px;
    background: var(--viewer-dim);
    color: var(--viewer);
    padding: 2px 10px;
    border-radius: 10px;
  }
  .sim-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 20px; }
  .chat-card {
    background: var(--panel);
    border: 1px solid var(--line);
    border-radius: var(--radius);
    display: flex; flex-direction: column;
    height: 620px;
    transition: border-color 0.3s ease, box-shadow 0.3s ease, transform 0.3s ease;
    box-shadow: var(--shadow);
  }
  .chat-card:hover { box-shadow: var(--shadow-lg); }
  .chat-card.active-next { border-color: var(--lime); box-shadow: 0 0 0 3px rgba(132,204,22,.28); }
  @media (prefers-reduced-motion: reduce) {
    .chat-card { transition: none; }
  }
  .chat-header { padding: 12px 16px; border-bottom: 1px solid var(--line); }
  .chat-title { font-weight: 600; font-size: 15px; }
  .chat-meta { font-family: var(--mono); font-size: 12px; color: var(--text-faint); margin-top: 2px; }
  .route-badge {
    display: inline-block;
    font-size: 12px;
    padding: 2px 8px;
    border-radius: 10px;
    background: var(--viewer-dim);
    color: var(--viewer);
    margin-top: 6px;
  }
  .route-badge.warn { background: var(--danger-dim); color: var(--danger); }
  .chat-messages {
    flex: 1;
    overflow-y: auto;
    padding: 14px;
    display: flex; flex-direction: column; gap: 10px;
    background: #fff;
  }
  .bubble {
    border-radius: 6px;
    padding: 8px 12px;
    font-size: 14px;
    border-inline-start: 3px solid var(--viewer);
    background: var(--viewer-dim);
    animation: sim-fade 0.25s ease-in-out;
  }
  .bubble.sys { border-inline-start-color: var(--commander); background: var(--commander-dim); }
  .bubble.err { border-inline-start-color: var(--danger); background: var(--danger-dim); }
  @keyframes sim-fade { from { opacity: 0; transform: translateY(4px); } to { opacity: 1; transform: none; } }
  .bubble-head {
    display: flex; justify-content: space-between; gap: 8px;
    font-family: var(--mono); font-size: 12px; color: var(--text-faint);
    margin-bottom: 4px;
  }
  .bubble-head .sender { color: var(--text-dim); font-weight: 600; }
  .bubble-text { margin: 0; white-space: pre-wrap; line-height: 1.4; }
  .bubble-status { font-family: var(--mono); font-size: 12px; color: var(--text-dim); margin-top: 6px; }
  .bubble-step { font-size: 11px; color: var(--text-faint); margin-top: 4px; }
  .chat-footer { padding: 12px 16px; border-top: 1px solid var(--line); }
  .preview-box {
    border: 1px dashed var(--line-strong);
    border-radius: 4px;
    padding: 8px 10px;
    margin-bottom: 8px;
    font-size: 13px;
    background: var(--bg);
  }
  .chat-card.active-next .preview-box { border-style: solid; border-color: var(--commander); background: var(--commander-dim); }
  .preview-title {
    display: flex; justify-content: space-between; gap: 8px;
    font-family: var(--mono); font-size: 11px; color: var(--text-faint);
    margin-bottom: 4px;
  }
  .preview-content { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .preview-warn { color: var(--danger); font-size: 12px; margin-top: 4px; }
  .preview-title-actions { display: flex; align-items: center; gap: 8px; flex-shrink: 0; }
  .send-btn { width: 100%; }
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
