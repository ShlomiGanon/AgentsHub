"""Behind-the-Scenes Standalone Live Agent Execution & Communication Dashboard.

Renders an independent, high-performance, cybernetic dark-themed dashboard
that operators can open in a separate window or tab to watch multi-agent
reasoning, inter-agent communication, parallel execution branches, tool side-effects,
and database verifications in real-time.
"""

from __future__ import annotations

import html

HTML_PAGE_TEMPLATE = r"""<!DOCTYPE html>
<html lang="he" dir="rtl">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>AgentsHub • Live Agent Execution & Communication Graph</title>
  <style>
    :root {
      --bg-base: #060913;
      --bg-surface: #0b1120;
      --bg-card: #0f172a;
      --bg-card-hover: #1e293b;
      --border-subtle: #1e293b;
      --border-medium: #334155;
      --border-bright: #38bdf8;
      --text-main: #f8fafc;
      --text-muted: #94a3b8;
      --text-dim: #64748b;
      --accent-cyan: #38bdf8;
      --accent-blue: #3b82f6;
      --accent-indigo: #818cf8;
      --accent-emerald: #10b981;
      --accent-rose: #f43f5e;
      --accent-amber: #f59e0b;
      --accent-purple: #c084fc;
      --glow-cyan: 0 0 24px rgba(56, 189, 248, 0.25);
      --glow-emerald: 0 0 24px rgba(16, 185, 129, 0.25);
      --glow-rose: 0 0 24px rgba(244, 63, 94, 0.25);
      --font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
    }

    * {
      box-sizing: border-box;
      margin: 0;
      padding: 0;
    }

    body {
      background: var(--bg-base);
      color: var(--text-main);
      font-family: var(--font-family);
      height: 100vh;
      overflow: hidden;
      display: flex;
      flex-direction: column;
      user-select: none;
      direction: rtl;
    }

    /* Header Bar */
    .bts-header {
      background: rgba(11, 17, 32, 0.85);
      backdrop-filter: blur(12px);
      border-bottom: 1px solid var(--border-subtle);
      padding: 10px 20px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      z-index: 100;
      flex-shrink: 0;
    }

    .bts-header-left {
      display: flex;
      align-items: center;
      gap: 14px;
    }

    .bts-title-group {
      display: flex;
      align-items: center;
      gap: 10px;
    }

    .bts-logo-badge {
      font-size: 20px;
      background: linear-gradient(135deg, rgba(56,189,248,0.2), rgba(129,140,248,0.2));
      border: 1px solid var(--accent-cyan);
      border-radius: 8px;
      width: 38px;
      height: 38px;
      display: flex;
      align-items: center;
      justify-content: center;
      box-shadow: var(--glow-cyan);
    }

    .bts-title {
      font-size: 16px;
      font-weight: 700;
      letter-spacing: -0.2px;
      color: var(--text-main);
      display: flex;
      align-items: center;
      gap: 8px;
    }

    .bts-subtitle {
      font-size: 11px;
      color: var(--accent-cyan);
      font-weight: 500;
      text-transform: uppercase;
      letter-spacing: 0.5px;
    }

    .bts-live-beacon {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      font-size: 12px;
      font-weight: 600;
      padding: 4px 10px;
      border-radius: 20px;
      background: rgba(16, 185, 129, 0.12);
      border: 1px solid rgba(16, 185, 129, 0.3);
      color: var(--accent-emerald);
    }

    .bts-live-beacon.is-idle {
      background: rgba(100, 116, 139, 0.12);
      border-color: rgba(100, 116, 139, 0.3);
      color: var(--text-muted);
    }

    .bts-pulse-dot {
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background: currentColor;
      box-shadow: 0 0 8px currentColor;
      animation: pulse-ring 1.8s infinite;
    }

    .bts-header-center {
      display: flex;
      align-items: center;
      gap: 12px;
      flex: 1;
      max-width: 600px;
      justify-content: center;
    }

    .bts-trace-select {
      background: #090e1a;
      border: 1px solid var(--border-medium);
      color: var(--text-main);
      border-radius: 6px;
      padding: 6px 12px;
      font-size: 13px;
      width: 100%;
      max-width: 380px;
      outline: none;
      cursor: pointer;
    }
    .bts-trace-select:focus {
      border-color: var(--accent-cyan);
    }

    .bts-autofollow {
      display: flex;
      align-items: center;
      gap: 6px;
      font-size: 12px;
      color: var(--text-muted);
      cursor: pointer;
      white-space: nowrap;
    }

    .bts-header-right {
      display: flex;
      align-items: center;
      gap: 10px;
    }

    .bts-pill {
      font-size: 12px;
      padding: 4px 10px;
      border-radius: 6px;
      background: #090e1a;
      border: 1px solid var(--border-subtle);
      color: var(--text-muted);
      font-family: monospace;
      cursor: pointer;
    }
    .bts-pill:hover {
      border-color: var(--accent-cyan);
      color: var(--text-main);
    }

    .bts-btn {
      background: #1e293b;
      border: 1px solid var(--border-medium);
      color: var(--text-main);
      border-radius: 6px;
      padding: 6px 12px;
      font-size: 12px;
      font-weight: 500;
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      gap: 6px;
      transition: all 0.15s ease;
    }
    .bts-btn:hover {
      background: #334155;
      border-color: var(--accent-cyan);
    }

    /* Metrics Bar */
    .bts-metrics-bar {
      background: rgba(11, 17, 32, 0.6);
      border-bottom: 1px solid var(--border-subtle);
      padding: 8px 20px;
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 14px;
      flex-shrink: 0;
    }

    .bts-metric-card {
      background: rgba(15, 23, 42, 0.7);
      border: 1px solid rgba(51, 65, 85, 0.6);
      border-radius: 8px;
      padding: 8px 14px;
      display: flex;
      flex-direction: column;
      gap: 2px;
      transition: border-color 0.2s;
    }
    .bts-metric-card:hover {
      border-color: var(--border-bright);
    }

    .bts-metric-title {
      font-size: 11px;
      color: var(--text-dim);
      font-weight: 500;
      display: flex;
      justify-content: space-between;
      align-items: center;
    }

    .bts-metric-val {
      font-size: 17px;
      font-weight: 700;
      color: var(--text-main);
      font-feature-settings: "tnum";
    }

    .bts-metric-sub {
      font-size: 11px;
      color: var(--text-muted);
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }

    /* Main Workspace */
    .bts-workspace {
      flex: 1;
      display: flex;
      position: relative;
      overflow: hidden;
    }

    /* Graph View (Canvas) */
    .bts-canvas-container {
      flex: 1;
      position: relative;
      overflow: hidden;
      background: radial-gradient(circle at 50% 25%, #0b1426 0%, #060913 85%);
      cursor: grab;
    }
    .bts-canvas-container:active {
      cursor: grabbing;
    }

    .bts-grid-pattern {
      position: absolute;
      top: 0; left: 0; right: 0; bottom: 0;
      background-image: radial-gradient(rgba(56, 189, 248, 0.08) 1px, transparent 1px);
      background-size: 32px 32px;
      pointer-events: none;
    }

    .bts-canvas-svg {
      width: 100%;
      height: 100%;
      position: absolute;
      top: 0;
      left: 0;
    }

    /* Zoom Controls Floating Box */
    .bts-zoom-controls {
      position: absolute;
      bottom: 20px;
      left: 20px;
      display: flex;
      flex-direction: column;
      gap: 6px;
      background: rgba(15, 23, 42, 0.9);
      border: 1px solid var(--border-medium);
      border-radius: 8px;
      padding: 6px;
      z-index: 50;
      box-shadow: 0 8px 24px rgba(0,0,0,0.5);
    }

    .bts-zoom-btn {
      width: 32px;
      height: 32px;
      background: transparent;
      border: 1px solid transparent;
      color: var(--text-muted);
      border-radius: 6px;
      font-size: 16px;
      display: flex;
      align-items: center;
      justify-content: center;
      cursor: pointer;
      transition: all 0.15s;
    }
    .bts-zoom-btn:hover {
      background: #1e293b;
      color: var(--text-main);
      border-color: var(--border-medium);
    }

    /* Right Panel (Stream & Details) */
    .bts-right-panel {
      width: 440px;
      background: rgba(11, 17, 32, 0.95);
      backdrop-filter: blur(16px);
      border-right: 1px solid var(--border-subtle);
      display: flex;
      flex-direction: column;
      z-index: 40;
      box-shadow: -8px 0 32px rgba(0,0,0,0.5);
      flex-shrink: 0;
    }

    .bts-panel-tabs {
      display: flex;
      border-bottom: 1px solid var(--border-subtle);
      background: rgba(15, 23, 42, 0.8);
    }

    .bts-tab-btn {
      flex: 1;
      padding: 12px 14px;
      background: transparent;
      border: none;
      border-bottom: 2px solid transparent;
      color: var(--text-muted);
      font-size: 13px;
      font-weight: 600;
      cursor: pointer;
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 8px;
      transition: all 0.2s;
    }
    .bts-tab-btn:hover {
      color: var(--text-main);
      background: rgba(30, 41, 59, 0.4);
    }
    .bts-tab-btn.is-active {
      color: var(--accent-cyan);
      border-bottom-color: var(--accent-cyan);
      background: rgba(14, 165, 233, 0.08);
    }

    .bts-tab-content {
      flex: 1;
      overflow-y: auto;
      padding: 16px;
      display: none;
    }
    .bts-tab-content.is-active {
      display: flex;
      flex-direction: column;
      gap: 12px;
    }

    /* Message Stream Card Styles */
    .bts-msg-card {
      background: rgba(15, 23, 42, 0.8);
      border: 1px solid var(--border-subtle);
      border-radius: 8px;
      padding: 12px 14px;
      display: flex;
      flex-direction: column;
      gap: 8px;
      transition: all 0.2s;
      position: relative;
    }
    .bts-msg-card:hover {
      border-color: var(--border-medium);
      background: rgba(30, 41, 59, 0.6);
    }

    .bts-msg-card.is-running {
      border-color: var(--accent-cyan);
      box-shadow: var(--glow-cyan);
    }
    .bts-msg-card.is-failed {
      border-color: var(--accent-rose);
      box-shadow: var(--glow-rose);
    }

    .bts-msg-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
    }

    .bts-msg-actors {
      display: flex;
      align-items: center;
      gap: 6px;
      font-size: 13px;
      font-weight: 600;
    }

    .bts-msg-arrow {
      color: var(--accent-cyan);
      font-size: 14px;
    }

    .bts-msg-badge {
      font-size: 10px;
      font-weight: 600;
      padding: 2px 7px;
      border-radius: 12px;
      background: rgba(56, 189, 248, 0.15);
      color: var(--accent-cyan);
      border: 1px solid rgba(56, 189, 248, 0.3);
    }
    .bts-msg-badge.badge-result {
      background: rgba(16, 185, 129, 0.15);
      color: var(--accent-emerald);
      border-color: rgba(16, 185, 129, 0.3);
    }
    .bts-msg-badge.badge-tool {
      background: rgba(192, 132, 252, 0.15);
      color: var(--accent-purple);
      border-color: rgba(192, 132, 252, 0.3);
    }
    .bts-msg-badge.badge-failed {
      background: rgba(244, 63, 94, 0.15);
      color: var(--accent-rose);
      border-color: rgba(244, 63, 94, 0.3);
    }

    .bts-msg-time {
      font-size: 11px;
      color: var(--text-dim);
      font-family: monospace;
    }

    .bts-msg-title {
      font-size: 12px;
      font-weight: 600;
      color: var(--text-main);
      line-height: 1.4;
    }

    .bts-msg-body {
      font-size: 12px;
      color: var(--text-muted);
      line-height: 1.5;
      background: #090e1a;
      border-radius: 6px;
      padding: 8px 10px;
      word-break: break-word;
      white-space: pre-wrap;
      max-height: 140px;
      overflow-y: auto;
    }

    /* Node Inspector */
    .bts-inspect-empty {
      text-align: center;
      color: var(--text-dim);
      font-size: 13px;
      padding: 40px 20px;
    }

    .bts-inspect-card {
      background: rgba(15, 23, 42, 0.8);
      border: 1px solid var(--border-medium);
      border-radius: 8px;
      padding: 16px;
      display: flex;
      flex-direction: column;
      gap: 14px;
    }

    .bts-inspect-title-row {
      display: flex;
      align-items: center;
      gap: 12px;
    }

    .bts-inspect-icon {
      font-size: 28px;
      width: 50px;
      height: 50px;
      border-radius: 10px;
      background: #1e293b;
      display: flex;
      align-items: center;
      justify-content: center;
      border: 1px solid var(--border-bright);
    }

    .bts-inspect-title {
      font-size: 16px;
      font-weight: 700;
      color: var(--text-main);
    }

    .bts-inspect-sub {
      font-size: 12px;
      color: var(--text-dim);
      font-family: monospace;
    }

    .bts-inspect-row {
      display: flex;
      justify-content: space-between;
      align-items: center;
      font-size: 12px;
      border-bottom: 1px solid rgba(51, 65, 85, 0.4);
      padding: 6px 0;
    }
    .bts-inspect-label {
      color: var(--text-dim);
    }
    .bts-inspect-val {
      font-weight: 600;
      color: var(--text-main);
    }

    .bts-inspect-section-title {
      font-size: 12px;
      font-weight: 700;
      color: var(--accent-cyan);
      margin-top: 6px;
    }

    .bts-inspect-box {
      background: #090e1a;
      border: 1px solid var(--border-subtle);
      border-radius: 6px;
      padding: 10px;
      font-size: 12px;
      color: var(--text-muted);
      line-height: 1.5;
      white-space: pre-wrap;
      max-height: 200px;
      overflow-y: auto;
    }

    /* SVG Nodes & Edges */
    .bts-edge-path {
      fill: none;
      stroke-width: 2.5px;
      transition: stroke 0.3s;
    }
    .bts-edge-path.edge-pending {
      stroke: #334155;
      stroke-dasharray: 6 4;
    }
    .bts-edge-path.edge-active {
      stroke: var(--accent-cyan);
      stroke-dasharray: 8 6;
      animation: packet-flow 1s linear infinite;
      filter: drop-shadow(0 0 6px rgba(56, 189, 248, 0.6));
    }
    .bts-edge-path.edge-completed {
      stroke: var(--accent-emerald);
      stroke-width: 2px;
    }
    .bts-edge-path.edge-failed {
      stroke: var(--accent-rose);
      stroke-width: 2px;
    }
    .bts-edge-path.edge-unattributed {
      stroke: #64748b;
      stroke-dasharray: 3 5;
      opacity: 0.85;
    }

    .bts-edge-label-bg {
      fill: #0b1120;
      stroke: var(--border-subtle);
      stroke-width: 1px;
      rx: 4px;
    }
    .bts-edge-label-text {
      fill: var(--text-muted);
      font-size: 10.5px;
      font-family: var(--font-family);
      text-anchor: middle;
      dominant-baseline: central;
    }

    /* Node Foreign Object Card */
    .bts-node-card {
      background: rgba(15, 23, 42, 0.95);
      backdrop-filter: blur(8px);
      border: 1.5px solid var(--border-medium);
      border-radius: 10px;
      padding: 12px;
      display: flex;
      flex-direction: column;
      gap: 6px;
      cursor: pointer;
      transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
      width: 100%;
      height: 100%;
      box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.6);
      direction: rtl;
    }
    .bts-node-card:hover {
      transform: translateY(-2px);
      border-color: var(--accent-cyan);
      box-shadow: 0 12px 30px rgba(56, 189, 248, 0.25);
    }
    .bts-node-card.is-selected {
      border-color: var(--accent-cyan);
      outline: 2px solid rgba(56, 189, 248, 0.4);
      box-shadow: var(--glow-cyan);
    }
    .bts-node-card.status-running {
      border-color: var(--accent-cyan);
      animation: pulse-ring 2s infinite;
    }
    .bts-node-card.status-success {
      border-color: rgba(16, 185, 129, 0.5);
    }
    .bts-node-card.status-failed {
      border-color: rgba(244, 63, 94, 0.7);
    }
    .bts-node-card.status-waiting {
      border-color: rgba(245, 158, 11, 0.7);
    }
    .bts-node-card.type-invocation { border-inline-start: 4px solid var(--accent-purple); }
    .bts-node-card.type-model { border-inline-start: 4px solid var(--accent-cyan); }
    .bts-node-card.type-tool { border-inline-start: 4px solid #14b8a6; }
    .bts-node-card.type-routing { border-inline-start: 4px solid #64748b; }
    .bts-node-card.type-persistence { border-inline-start: 4px solid var(--accent-amber); }
    .bts-node-card.type-composition { border-inline-start: 4px solid var(--accent-indigo); }
    .bts-node-card.type-result,
    .bts-node-card.type-outcome { border-inline-start: 4px solid var(--accent-emerald); }

    .bts-node-top {
      display: flex;
      align-items: center;
      gap: 10px;
    }

    .bts-node-avatar {
      font-size: 22px;
      width: 36px;
      height: 36px;
      border-radius: 8px;
      background: rgba(30, 41, 59, 0.7);
      border: 1px solid var(--border-medium);
      display: flex;
      align-items: center;
      justify-content: center;
      flex-shrink: 0;
    }

    .bts-node-titles {
      flex: 1;
      overflow: hidden;
    }

    .bts-node-name {
      font-size: 13.5px;
      font-weight: 700;
      color: var(--text-main);
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }

    .bts-node-sub {
      font-size: 10.5px;
      color: var(--text-dim);
      font-family: monospace;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }

    .bts-node-badge-row {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 6px;
      margin-top: 4px;
    }

    .bts-node-status-pill {
      font-size: 10.5px;
      font-weight: 600;
      padding: 2px 7px;
      border-radius: 10px;
      background: #1e293b;
      color: var(--text-muted);
    }
    .bts-node-status-pill.status-running {
      background: rgba(56, 189, 248, 0.2);
      color: var(--accent-cyan);
    }
    .bts-node-status-pill.status-success {
      background: rgba(16, 185, 129, 0.2);
      color: var(--accent-emerald);
    }
    .bts-node-status-pill.status-failed {
      background: rgba(244, 63, 94, 0.2);
      color: var(--accent-rose);
    }

    .bts-node-timer {
      font-size: 10.5px;
      color: var(--text-dim);
      font-family: monospace;
    }

    .bts-node-preview {
      font-size: 11px;
      color: var(--text-muted);
      background: #090e1a;
      border-radius: 4px;
      padding: 5px 8px;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      margin-top: 4px;
    }

    @keyframes pulse-ring {
      0% { box-shadow: 0 0 0 0 rgba(56, 189, 248, 0.4); }
      70% { box-shadow: 0 0 0 10px rgba(56, 189, 248, 0); }
      100% { box-shadow: 0 0 0 0 rgba(56, 189, 248, 0); }
    }

    @keyframes packet-flow {
      to { stroke-dashoffset: -28; }
    }

    @media (max-width: 1100px) {
      body { height: auto; min-height: 100vh; overflow: auto; }
      .bts-header { flex-wrap: wrap; }
      .bts-header-center { order: 3; flex-basis: 100%; max-width: none; }
      .bts-workspace { flex-direction: column; min-height: 90vh; overflow: visible; }
      .bts-canvas-container { min-height: 52vh; flex: 0 0 52vh; }
      .bts-right-panel { width: 100%; min-height: 32vh; border-right: 0; border-top: 1px solid var(--border-subtle); }
      .bts-metrics-bar { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    }

    @media (max-width: 640px) {
      body { height: auto; min-height: 100vh; overflow: auto; }
      .bts-header { padding: 10px; gap: 8px; }
      .bts-header-left, .bts-header-right { width: 100%; justify-content: space-between; }
      .bts-header-center { flex-direction: column; align-items: stretch; }
      .bts-trace-select { max-width: none; }
      .bts-metrics-bar { grid-template-columns: 1fr 1fr; padding: 8px; gap: 8px; }
      .bts-metric-card { padding: 10px; }
      .bts-metric-val { font-size: 14px; }
      .bts-workspace { min-height: 100vh; overflow: visible; }
      .bts-canvas-container { min-height: 58vh; flex-basis: 58vh; }
      .bts-right-panel { min-height: 40vh; }
      .bts-panel-tabs { position: sticky; top: 0; }
    }
  </style>
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
        <span id="beacon-text">שידור חי פעיל</span>
      </div>
    </div>

    <div class="bts-header-center">
      <select id="trace-select" class="bts-trace-select">
        <option value="">טוען רשימת בקשות (Traces)...</option>
      </select>
      <label class="bts-autofollow">
        <input type="checkbox" id="auto-follow-check" checked>
        עקוב אוטומטית אחר בקשות חדשות
      </label>
    </div>

    <div class="bts-header-right">
      <div id="trace-id-pill" class="bts-pill" title="לחץ להעתקת Trace ID">—</div>
      <button id="refresh-btn" class="bts-btn" title="רענן נתונים">🔄 רענן</button>
    </div>
  </header>

  <!-- Performance Metrics Bar -->
  <section class="bts-metrics-bar">
    <div class="bts-metric-card">
      <div class="bts-metric-title">
        <span>זמן ביצוע כולל (Wall-clock)</span>
        <span>⏱️</span>
      </div>
      <div id="m-wall" class="bts-metric-val">—</div>
      <div id="m-wall-breakdown" class="bts-metric-sub">מודל: — | כלים: —</div>
    </div>

    <div class="bts-metric-card">
      <div class="bts-metric-title">
        <span>קריאות ספק LLM</span>
        <span>🧠</span>
      </div>
      <div id="m-llm" class="bts-metric-val">—</div>
      <div id="m-retries" class="bts-metric-sub">ניסיונות חוזרים: 0</div>
    </div>

    <div class="bts-metric-card">
      <div class="bts-metric-title">
        <span>טוקנים</span>
        <span>📊</span>
      </div>
      <div id="m-tokens" class="bts-metric-val">—</div>
      <div id="m-tokens-sub" class="bts-metric-sub">קלט: — | פלט: —</div>
    </div>

    <div class="bts-metric-card">
      <div class="bts-metric-title">
        <span>סוכנים וכלים שהופעלו</span>
        <span>👥</span>
      </div>
      <div id="m-agents" class="bts-metric-val">—</div>
      <div id="m-agents-sub" class="bts-metric-sub">ענפים במקביל: 0</div>
    </div>
  </section>

  <!-- Main Split Workspace -->
  <main class="bts-workspace">
    <!-- Interactive Graph Canvas -->
    <div id="canvas-container" class="bts-canvas-container">
      <div class="bts-grid-pattern"></div>
      <svg id="graph-svg" class="bts-canvas-svg">
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
        <button id="zoom-in" class="bts-zoom-btn" title="התקרב">+</button>
        <button id="zoom-out" class="bts-zoom-btn" title="התרחק">−</button>
        <button id="zoom-fit" class="bts-zoom-btn" title="התאם לתצוגה">⛶</button>
      </div>
    </div>

    <!-- Right Side Panel: Stream & Inspector -->
    <aside class="bts-right-panel">
      <div class="bts-panel-tabs">
        <button id="tab-stream-btn" class="bts-tab-btn is-active">
          <span>💬</span> זרם תקשורת סוכנים
        </button>
        <button id="tab-inspect-btn" class="bts-tab-btn">
          <span>🔍</span> פרטי צומת שנבחר
        </button>
      </div>

      <!-- Tab 1: Live Message Stream -->
      <div id="tab-stream" class="bts-tab-content is-active">
        <div id="stream-list">
          <div class="bts-inspect-empty">ממתין להעברת הודעות בין הסוכנים...</div>
        </div>
      </div>

      <!-- Tab 2: Node Inspector -->
      <div id="tab-inspect" class="bts-tab-content">
        <div id="inspector-content">
          <div class="bts-inspect-empty">בחר צומת כלשהו בגרף להצגת פרטי משימה ואימותים מעמיקים.</div>
        </div>
      </div>
    </aside>
  </main>

  <script>
    (function () {
      let currentTraceId = "__SAFE_TRACE_ID__";
      let pollTimer = null;
      let recentTimer = null;
      let currentData = null;
      let selectedNodeId = null;

      // Canvas Transform State
      let transform = { x: 0, y: 0, scale: 1 };
      let isPanning = false;
      let panStart = { x: 0, y: 0 };

      // Node Geometry layout
      const NODE_WIDTH = 260;
      const NODE_HEIGHT = 100;

      // Elements
      const canvasContainer = document.getElementById('canvas-container');
      const sceneGroup = document.getElementById('scene-group');
      const edgesLayer = document.getElementById('edges-layer');
      const nodesLayer = document.getElementById('nodes-layer');
      const traceSelect = document.getElementById('trace-select');
      const traceIdPill = document.getElementById('trace-id-pill');
      const autoFollowCheck = document.getElementById('auto-follow-check');
      const beacon = document.getElementById('beacon');
      const beaconText = document.getElementById('beacon-text');
      const refreshBtn = document.getElementById('refresh-btn');

      // Metric elements
      const mWall = document.getElementById('m-wall');
      const mWallBreakdown = document.getElementById('m-wall-breakdown');
      const mLlm = document.getElementById('m-llm');
      const mRetries = document.getElementById('m-retries');
      const mTokens = document.getElementById('m-tokens');
      const mTokensSub = document.getElementById('m-tokens-sub');
      const mAgents = document.getElementById('m-agents');
      const mAgentsSub = document.getElementById('m-agents-sub');

      // Tab buttons
      const tabStreamBtn = document.getElementById('tab-stream-btn');
      const tabInspectBtn = document.getElementById('tab-inspect-btn');
      const tabStream = document.getElementById('tab-stream');
      const tabInspect = document.getElementById('tab-inspect');
      const streamList = document.getElementById('stream-list');
      const inspectorContent = document.getElementById('inspector-content');

      // Tabs switcher
      tabStreamBtn.addEventListener('click', () => switchTab('stream'));
      tabInspectBtn.addEventListener('click', () => switchTab('inspect'));

      function switchTab(tab) {
        if (tab === 'stream') {
          tabStreamBtn.classList.add('is-active');
          tabInspectBtn.classList.remove('is-active');
          tabStream.classList.add('is-active');
          tabInspect.classList.remove('is-active');
        } else {
          tabInspectBtn.classList.add('is-active');
          tabStreamBtn.classList.remove('is-active');
          tabInspect.classList.add('is-active');
          tabStream.classList.remove('is-active');
        }
      }

      function esc(value) {
        return String(value == null ? '' : value).replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;').replaceAll("'", '&#39;');
      }

      // Transform application
      function applyTransform() {
        if (sceneGroup) {
          sceneGroup.setAttribute('transform', 'translate(' + transform.x + ', ' + transform.y + ') scale(' + transform.scale + ')');
        }
      }

      // Zoom engine
      function zoomBy(factor) {
        const rect = canvasContainer.getBoundingClientRect();
        const cx = rect.width / 2;
        const cy = rect.height / 2;
        const newScale = Math.max(0.35, Math.min(2.5, transform.scale * factor));
        transform.x = cx - (cx - transform.x) * (newScale / transform.scale);
        transform.y = cy - (cy - transform.y) * (newScale / transform.scale);
        transform.scale = newScale;
        applyTransform();
      }

      document.getElementById('zoom-in').addEventListener('click', () => zoomBy(1.2));
      document.getElementById('zoom-out').addEventListener('click', () => zoomBy(0.8));
      document.getElementById('zoom-fit').addEventListener('click', fitToView);

      // Pan engine
      canvasContainer.addEventListener('mousedown', (e) => {
        if (e.target.closest('.bts-node-card') || e.target.closest('.bts-zoom-controls')) return;
        isPanning = true;
        panStart = { x: e.clientX - transform.x, y: e.clientY - transform.y };
      });

      window.addEventListener('mousemove', (e) => {
        if (!isPanning) return;
        transform.x = e.clientX - panStart.x;
        transform.y = e.clientY - panStart.y;
        applyTransform();
      });

      window.addEventListener('mouseup', () => {
        isPanning = false;
      });

      canvasContainer.addEventListener('wheel', (e) => {
        e.preventDefault();
        const factor = e.deltaY < 0 ? 1.1 : 0.9;
        const rect = canvasContainer.getBoundingClientRect();
        const mouseX = e.clientX - rect.left;
        const mouseY = e.clientY - rect.top;
        const newScale = Math.max(0.35, Math.min(2.5, transform.scale * factor));
        transform.x = mouseX - (mouseX - transform.x) * (newScale / transform.scale);
        transform.y = mouseY - (mouseY - transform.y) * (newScale / transform.scale);
        transform.scale = newScale;
        applyTransform();
      }, { passive: false });

      // Fit View
      function fitToView() {
        if (!currentData || !currentData.graph || !currentData.graph.nodes || currentData.graph.nodes.length === 0) {
          transform = { x: 60, y: 60, scale: 1 };
          applyTransform();
          return;
        }
        const containerW = canvasContainer.clientWidth;
        const containerH = canvasContainer.clientHeight;
        const layout = computeLayout(currentData.graph.nodes);
        let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
        layout.forEach(pos => {
          minX = Math.min(minX, pos.x);
          maxX = Math.max(maxX, pos.x + NODE_WIDTH);
          minY = Math.min(minY, pos.y);
          maxY = Math.max(maxY, pos.y + NODE_HEIGHT);
        });
        const graphW = (maxX - minX) + 120;
        const graphH = (maxY - minY) + 120;
        const scale = Math.max(0.45, Math.min(1.2, Math.min(containerW / graphW, containerH / graphH)));
        transform.scale = scale;
        transform.x = (containerW - graphW * scale) / 2 - minX * scale + 60;
        transform.y = (containerH - graphH * scale) / 2 - minY * scale + 40;
        applyTransform();
      }

      // Compute Hierarchical Tree Coordinates
      function computeLayout(nodes) {
        const positions = new Map();
        const userNode = nodes.find(n => n.type === 'user');
        const mainNode = nodes.find(n => n.type === 'main');
        const routing = nodes.filter(n => n.type === 'routing');
        const invocations = nodes.filter(n => n.type === 'invocation');
        const models = nodes.filter(n => n.type === 'model');
        const tools = nodes.filter(n => n.type === 'tool');
        const persistence = nodes.filter(n => n.type === 'persistence');
        const composition = nodes.filter(n => n.type === 'composition');
        const otherNodes = nodes.filter(n => n.type === 'result' || n.type === 'outcome');

        if (userNode) positions.set(userNode.id, { x: 420, y: 40 });
        if (mainNode) positions.set(mainNode.id, { x: 420, y: userNode ? 205 : 40 });
        let nextY = userNode ? 390 : 225;
        function placeRows(items) {
          for (let start = 0; start < items.length; start += 4) {
            const row = items.slice(start, start + 4);
            const width = row.length * (NODE_WIDTH + 40);
            const left = Math.max(40, 550 - width / 2);
            row.forEach((item, index) => positions.set(item.id, { x: left + index * (NODE_WIDTH + 40), y: nextY }));
            nextY += 185;
          }
        }
        placeRows(routing);
        placeRows(invocations);
        placeRows(models);
        placeRows(tools);
        placeRows(persistence);
        placeRows(composition);
        placeRows(otherNodes);

        return positions;
      }

      // Fetch active trace without overlapping polls; retain last known data on disconnect.
      let traceFetchInFlight = false;
      async function fetchTrace() {
        if (!currentTraceId) return;
        if (traceFetchInFlight) return;
        traceFetchInFlight = true;
        const requestedTraceId = currentTraceId;
        let controller = null;
        let requestTimeout = null;
        try {
          if (typeof AbortController !== 'undefined') {
            controller = new AbortController();
            requestTimeout = setTimeout(() => controller.abort(), 8000);
          }
          const res = await fetch('/admin/simulator/trace/' + encodeURIComponent(requestedTraceId), {
            signal: controller ? controller.signal : undefined,
          });
          if (!res.ok) throw new Error('Trace endpoint returned ' + res.status);
          const data = await res.json();
          if (requestedTraceId !== currentTraceId) return;
          currentData = data;
          render(data);

          if (data.execution_status === 'awaiting_approval') {
            beacon.className = 'bts-live-beacon is-idle';
            beaconText.textContent = 'ממתין לאישור';
          } else if (data.execution_status === 'partial') {
            beacon.className = 'bts-live-beacon is-idle';
            beaconText.textContent = 'הביצוע הסתיים חלקית';
          } else if (data.execution_status === 'unknown' || data.diagnostic_state === 'job_stopped_without_outcome') {
            beacon.className = 'bts-live-beacon is-idle';
            beaconText.textContent = 'ה־Job נעצר בלי תוצאה שמורה — נדרש בירור';
          } else if (data.terminal) {
            beacon.className = 'bts-live-beacon is-idle';
            beaconText.textContent = data.execution_status === 'succeeded'
              ? (data.delivery_status === 'confirmed' ? 'הושלם ונמסר' : 'ה־API הושלם · אישור מסירה לא זמין')
              : 'הסתיים (' + (data.outcome || 'סיום') + ')';
          } else {
            beacon.className = 'bts-live-beacon';
            beaconText.textContent = 'שידור חי פעיל';
          }
        } catch (err) {
          console.warn('Trace fetch error:', err);
          beacon.className = 'bts-live-beacon is-idle';
          beaconText.textContent = currentData ? 'אין חיבור ל־Trace · מוצגים הנתונים האחרונים' : 'אין חיבור ל־Trace · ממתין לחיבור מחדש';
        } finally {
          if (requestTimeout !== null) clearTimeout(requestTimeout);
          traceFetchInFlight = false;
        }
      }

      // Main Render
      function render(data) {
        // 1. Update Metrics
        const m = data.metrics || {};
        mWall.textContent = m.total_wall_clock_ms ? (m.total_wall_clock_ms.toLocaleString() + ' מ"ש') : '—';
        const queueTime = m.queue_wait_ms == null ? 'לא זמין' : m.queue_wait_ms;
        mWallBreakdown.textContent = 'ספק מצטבר: ' + (m.model_latency_ms || 0) + ' מ"ש | כלים: ' + (m.tools_duration_ms || 0) + ' מ"ש | תור: ' + queueTime + ' מ"ש';
        mLlm.textContent = m.llm_call_count ? (m.llm_call_count + ' קריאות') : '0';
        mRetries.textContent = 'ניסיונות חוזרים: ' + (m.retries_count || 0);
        mTokens.textContent = m.tokens ? m.tokens.total.toLocaleString() : '—';
        mTokensSub.textContent = m.tokens ? ('קלט: ' + m.tokens.input.toLocaleString() + ' | פלט: ' + m.tokens.output.toLocaleString()) : 'ללא נתוני טוקנים';

        const agCount = data.graph ? (data.graph.specialist_count || 0) : 0;
        const toolCount = data.graph ? (data.graph.tool_count || 0) : 0;
        mAgents.textContent = agCount + ' מומחים | ' + toolCount + ' כלים';
        mAgentsSub.textContent = data.graph && data.graph.explanation ? data.graph.explanation : (data.graph && data.graph.has_parallel ? ('⚡ ריצה במקביל (' + data.graph.parallel_batches_count + ' ענפים חופפים)') : 'ענפים במקביל: 0');

        // 2. Render Graph
        renderGraph(data.graph);

        // 3. Render Message Stream
        renderMessageStream(data.messages || []);

        // 4. Update Inspector if node is selected
        if (selectedNodeId) {
          const found = (data.graph && data.graph.nodes) ? data.graph.nodes.find(n => n.id === selectedNodeId) : null;
          if (found) renderInspector(found);
        }
      }

      // Render Graph Nodes and Edges
      function renderGraph(graph) {
        if (!graph || !graph.nodes || graph.nodes.length === 0) {
          nodesLayer.innerHTML = '<text x="450" y="250" fill="#64748b" font-size="14" text-anchor="middle">ממתין להפעלת בקשה בסימולציה...</text>';
          edgesLayer.innerHTML = '';
          return;
        }

        const nodePositions = computeLayout(graph.nodes);

        // Render Edges
        let edgesHtml = '';
        (graph.edges || []).forEach(edge => {
          const p1 = nodePositions.get(edge.source);
          const p2 = nodePositions.get(edge.target);
          if (!p1 || !p2) return;

          const x1 = p1.x + NODE_WIDTH / 2;
          const y1 = p1.y + NODE_HEIGHT;
          const x2 = p2.x + NODE_WIDTH / 2;
          const y2 = p2.y;

          const dx = x2 - x1;
          const dy = y2 - y1;
          const cx1 = x1 + dx * 0.1;
          const cy1 = y1 + dy * 0.6;
          const cx2 = x2 - dx * 0.1;
          const cy2 = y2 - dy * 0.6;

          const d = 'M ' + x1 + ' ' + y1 + ' C ' + cx1 + ' ' + cy1 + ', ' + cx2 + ' ' + cy2 + ', ' + x2 + ' ' + y2;
          const statusClass = edge.type === 'unattributed' ? 'edge-unattributed' : (edge.status === 'active' ? 'edge-active' : (edge.status === 'completed' ? 'edge-completed' : (edge.status === 'failed' ? 'edge-failed' : 'edge-pending')));
          const markerId = edge.type === 'unattributed' ? 'arrow-pending' : (edge.status === 'active' ? 'arrow-active' : (edge.status === 'completed' ? 'arrow-completed' : (edge.status === 'failed' ? 'arrow-failed' : 'arrow-pending')));

          // Edge path
          edgesHtml += '<path d="' + d + '" class="bts-edge-path ' + statusClass + '" marker-end="url(#' + markerId + ')"></path>';

          // Label in the middle
          if (edge.label) {
            const mx = (x1 + x2) / 2;
            const my = (y1 + y2) / 2;
            const labelWidth = Math.max(60, edge.label.length * 8 + 14);
            edgesHtml += '<rect x="' + (mx - labelWidth/2) + '" y="' + (my - 10) + '" width="' + labelWidth + '" height="20" class="bts-edge-label-bg"></rect>' +
                         '<text x="' + mx + '" y="' + my + '" class="bts-edge-label-text">' + esc(edge.label) + '</text>';
          }
        });
        edgesLayer.innerHTML = edgesHtml;

        // Render Nodes
        let nodesHtml = '';
        graph.nodes.forEach(node => {
          const pos = nodePositions.get(node.id);
          if (!pos) return;

          const isSelected = node.id === selectedNodeId ? 'is-selected' : '';
          const statusClass = 'status-' + (node.status || 'pending');

          let statusText = 'ממתין';
          if (node.status === 'running') statusText = 'פעיל כעת';
          else if (node.status === 'success') statusText = 'הושלם';
          else if (node.status === 'failed') statusText = 'נכשל';
          else if (node.status === 'waiting') statusText = 'ממתין לאישור';
          else if (node.status === 'unknown') statusText = 'מצב לא ידוע';

          const timerText = node.duration_ms ? (node.duration_ms + ' מ"ש') : (node.status === 'running' ? '⏱️ פועל' : '');
          const previewText = node.selected_agents && node.selected_agents.length
            ? node.selected_agents.join(', ')
            : (node.sublabel || node.protocol || node.intent || (node.tasks && node.tasks[0]) || node.summary || node.details || '');
          const nodeTypeClass = 'type-' + (node.type || 'unknown');

          nodesHtml += '<foreignObject x="' + pos.x + '" y="' + pos.y + '" width="' + NODE_WIDTH + '" height="' + NODE_HEIGHT + '">' +
            '<div class="bts-node-card ' + isSelected + ' ' + esc(statusClass) + ' ' + esc(nodeTypeClass) + '" data-id="' + esc(node.id) + '">' +
              '<div class="bts-node-top">' +
                '<div class="bts-node-avatar">' + esc(node.icon || '🤖') + '</div>' +
                '<div class="bts-node-titles">' +
                '<div class="bts-node-name">' + esc(node.label) + '</div>' +
                '<div class="bts-node-sub">' + esc(node.sublabel || '') + '</div>' +
                '</div>' +
              '</div>' +
              '<div class="bts-node-badge-row">' +
                '<span class="bts-node-status-pill ' + statusClass + '">' + statusText + '</span>' +
                '<span class="bts-node-timer">' + esc(timerText) + '</span>' +
              '</div>' +
              (previewText ? ('<div class="bts-node-preview" title="' + esc(previewText) + '">' + esc(previewText) + '</div>') : '') +
            '</div>' +
          '</foreignObject>';
        });
        nodesLayer.innerHTML = nodesHtml;

        // Wire node clicks
        document.querySelectorAll('.bts-node-card').forEach(el => {
          el.addEventListener('click', () => {
            const id = el.getAttribute('data-id');
            selectNode(id);
          });
        });
      }

      // Select and Inspect Node
      function selectNode(nodeId) {
        selectedNodeId = nodeId;
        document.querySelectorAll('.bts-node-card').forEach(c => {
          c.classList.toggle('is-selected', c.getAttribute('data-id') === nodeId);
        });
        const found = currentData && currentData.graph && currentData.graph.nodes ? currentData.graph.nodes.find(n => n.id === nodeId) : null;
        if (found) {
          renderInspector(found);
          switchTab('inspect');
        }
      }

      // Render Node Inspector
      function renderInspector(node) {
        let statusBadge = node.status;
        if (node.status === 'running') statusBadge = '🟢 פעיל כעת';
        else if (node.status === 'success') statusBadge = '✔ הושלם בהצלחה';
        else if (node.status === 'failed') statusBadge = '✖ שגיאה';
        else if (node.status === 'waiting') statusBadge = '⏳ ממתין לאישור';

        let html = '<div class="bts-inspect-card">' +
            '<div class="bts-inspect-title-row">' +
              '<div class="bts-inspect-icon">' + (node.icon || '🤖') + '</div>' +
              '<div>' +
                '<div class="bts-inspect-title">' + esc(node.label) + '</div>' +
                '<div class="bts-inspect-sub">' + esc(node.sublabel || node.id) + '</div>' +
              '</div>' +
            '</div>' +
            '<div class="bts-inspect-row">' +
              '<span class="bts-inspect-label">סטטוס תפעולי:</span>' +
              '<span class="bts-inspect-val">' + esc(statusBadge) + '</span>' +
            '</div>' +
            (node.duration_ms ? ('<div class="bts-inspect-row"><span class="bts-inspect-label">זמן ביצוע:</span><span class="bts-inspect-val">' + node.duration_ms + ' מ"ש</span></div>') : '') +
            (node.call_count ? ('<div class="bts-inspect-row"><span class="bts-inspect-label">מספר הפעלות / סבבים:</span><span class="bts-inspect-val">' + node.call_count + '</span></div>') : '') +
            (node.is_parallel ? ('<div class="bts-inspect-row"><span class="bts-inspect-label">מצב הרצה:</span><span class="bts-inspect-val" style="color:var(--accent-cyan)">⚡ הרצה במקביל (Concurrent)</span></div>') : '') +
            (node.verification ? ('<div class="bts-inspect-row"><span class="bts-inspect-label">רמת אימות פעולה:</span><span class="bts-inspect-val">' + esc(node.verification_note || node.verification) + '</span></div>') : '') +
            (node.task ? ('<div class="bts-inspect-section-title">תקציר משימה</div><div class="bts-inspect-box">' + esc(node.task) + '</div>') : '') +
            (node.result ? ('<div class="bts-inspect-section-title">תוצאה / שגיאה</div><div class="bts-inspect-box">' + esc(node.result) + '</div>') : '') +
            (node.llm_calls && node.llm_calls.length ? ('<div class="bts-inspect-section-title">קריאות מודל (' + node.llm_calls.length + ')</div><div class="bts-inspect-box">' + node.llm_calls.map(c => esc(
              '#' + (c.sequence_number || '?') + ' · ' + (c.purpose || 'unattributed') + ' · ' + (c.agent_name || node.label || 'unattributed') +
              ' · ' + (c.latency_ms ?? '?') + ' מ״ש · ' + (c.finish_reason || c.status || 'unknown') +
              ' · טוקנים קלט/פלט/מטמון: ' + (c.input_tokens ?? '?') + '/' + (c.output_tokens ?? '?') + '/' + (c.cache_tokens ?? '?') +
              '\nיוזם: ' + (c.parent_agent || 'unattributed') + ' · שלב: ' + (c.stage || 'unattributed') +
              ' · parent invocation: ' + (c.parent_invocation_id || 'לא נשמר') +
              ' · פרוטוקול: ' + (c.protocol_name || 'לא רלוונטי/לא זמין') +
              ' · ' + (c.tool_name ? ('אחרי כלי ' + c.tool_name) : (c.call_type && String(c.call_type).toLowerCase().includes('tool_call') ? 'החלטת כלי' : 'לפני כלי / סינתזה לא ידוע')) +
              '\nמזהה בקשת ספק: ' + (c.provider_request_id || 'unavailable') + ' · הפעלה: ' + (c.agent_invocation_id || 'unattributed') +
              '\nהתחלה: ' + (c.started_at || 'unavailable') + ' · סיום: ' + (c.finished_at || 'unavailable') +
              ' · סיכום בטוח: ' + (c.result_summary || 'אין תקציר בטוח זמין')
            )).join('<br><br>') + '</div>') : '') +
            (node.tools && node.tools.length ? ('<div class="bts-inspect-section-title">כלים (' + node.tools.length + ')</div><div class="bts-inspect-box">' + esc(node.tools.join(', ')) + '</div>') : '') +
            (node.type === 'tool' ? ('<div class="bts-inspect-section-title">שיוך והיקף הכלי</div><div class="bts-inspect-box">' +
              'Caller: ' + esc(node.caller_agent_name || 'לא נשמר') + '<br>' +
              'Invocation ID: ' + esc(node.agent_invocation_id || 'לא נשמר') + '<br>' +
              'סוג: ' + (node.side_effecting ? 'פעולת כתיבה' : 'קריאה בלבד') + '<br>' +
              'אימות: ' + esc(node.verification_note || node.verification || 'לא זמין') +
              '</div>') : '') +
            (node.type === 'invocation' && node.model_status ? ('<div class="bts-inspect-section-title">ריצת מודל (נפרדת מתוצאת Agent)</div><div class="bts-inspect-box">' +
              'סטטוס מודל: ' + esc(node.model_status) + '<br>' +
              'זמן מודל: ' + esc(node.model_duration_ms ?? 'לא זמין') + ' מ״ש<br>' +
              'טוקנים קלט/פלט: ' + esc(node.model_input_tokens ?? '?') + '/' + esc(node.model_output_tokens ?? '?') +
              '</div>') : '') +
            (node.summary ? ('<div class="bts-inspect-section-title">סיכום כלי</div><div class="bts-inspect-box">' + esc(node.summary) + '</div>') : '') +
            (node.details ? ('<div class="bts-inspect-section-title">פרטים נוספים</div><div class="bts-inspect-box">' + esc(node.details) + '</div>') : '') +
          '</div>';
        inspectorContent.innerHTML = html;
      }

      // Render Message Stream (How agents talk to each other)
      function renderMessageStream(messages) {
        if (!messages || messages.length === 0) {
          streamList.innerHTML = '<div class="bts-inspect-empty">ממתין להעברת הודעות ובקשות בין הסוכנים...</div>';
          return;
        }

        let html = '';
        messages.forEach(msg => {
          const isRunning = msg.status === 'running' ? 'is-running' : '';
          const isFailed = msg.status === 'failed' ? 'is-failed' : '';
          let badgeClass = 'bts-msg-badge';
          if (msg.kind === 'result') badgeClass += ' badge-result';
          else if (msg.kind === 'tool_call') badgeClass += ' badge-tool';
          else if (msg.status === 'failed') badgeClass += ' badge-failed';

          const timeStr = msg.time ? (msg.time.split('T')[1] || '').split('.')[0] : '';

          html += '<div class="bts-msg-card ' + isRunning + ' ' + isFailed + '">' +
              '<div class="bts-msg-header">' +
                '<div class="bts-msg-actors">' +
                  '<span>' + esc(msg.from_icon || '🤖') + ' ' + esc(msg.from_label) + '</span>' +
                  '<span class="bts-msg-arrow">➔</span>' +
                  '<span>' + esc(msg.to_icon || '🤖') + ' ' + esc(msg.to_label) + '</span>' +
                '</div>' +
                '<div class="bts-msg-time">' + timeStr + '</div>' +
              '</div>' +
              '<div style="display:flex; justify-content:space-between; align-items:center;">' +
                '<div class="bts-msg-title">' + esc(msg.title || msg.summary) + '</div>' +
                '<span class="' + badgeClass + '">' + esc(msg.badge || msg.kind) + '</span>' +
              '</div>' +
              (msg.body ? ('<div class="bts-msg-body">' + esc(msg.body) + '</div>') : '') +
            '</div>';
        });
        streamList.innerHTML = html;
      }

      // Recent Traces Loading
      async function loadRecentTraces() {
        try {
          const res = await fetch('/admin/simulator/traces/recent?limit=20');
          if (!res.ok) throw new Error('Recent traces endpoint returned ' + res.status);
          const data = await res.json();
          const items = data.items || [];
          if (!items.length) {
            traceSelect.innerHTML = '<option value="">אין Traces להצגה כרגע</option>';
            if (!currentTraceId) {
              beacon.className = 'bts-live-beacon is-idle';
              beaconText.textContent = 'ממתין לבקשה ראשונה';
            }
            return;
          }

          traceSelect.innerHTML = '';
          items.forEach(item => {
            const opt = document.createElement('option');
            opt.value = item.trace_id;
            const timeStr = (item.received_at || '').split('T')[1] || '';
            const statusIcon = item.status === 'running' ? '⚡' : (item.status === 'succeeded' ? '✔' : '✖');
            opt.textContent = '[' + timeStr.slice(0,8) + '] ' + statusIcon + ' ' + (item.text || item.trace_id);
            if (item.trace_id === currentTraceId) opt.selected = true;
            traceSelect.appendChild(opt);
          });

          // Auto-follow: if enabled and the newest trace is running or different from current
          if (autoFollowCheck.checked && items.length > 0) {
            const latest = items[0];
            if (!currentTraceId || (latest.status === 'running' && latest.trace_id !== currentTraceId)) {
              switchTrace(latest.trace_id);
            }
          }
        } catch (err) {
          console.warn('Recent traces error:', err);
          if (!currentTraceId) {
            beacon.className = 'bts-live-beacon is-idle';
            beaconText.textContent = 'אין חיבור לרשימת ה־Traces';
          }
        }
      }

      function switchTrace(newTraceId) {
        if (!newTraceId || newTraceId === currentTraceId) return;
        currentTraceId = newTraceId;
        currentData = null;
        selectedNodeId = null;
        traceIdPill.textContent = newTraceId;
        beacon.className = 'bts-live-beacon is-idle';
        beaconText.textContent = 'טוען Trace…';
        render({ metrics: {}, graph: { nodes: [], edges: [] }, messages: [] });
        window.history.replaceState(null, '', '?trace_id=' + encodeURIComponent(newTraceId));
        fetchTrace();
      }

      traceSelect.addEventListener('change', (e) => {
        switchTrace(e.target.value);
      });

      refreshBtn.addEventListener('click', () => {
        loadRecentTraces();
        fetchTrace();
      });

      traceIdPill.addEventListener('click', () => {
        if (currentTraceId) {
          navigator.clipboard.writeText(currentTraceId);
          const old = traceIdPill.textContent;
          traceIdPill.textContent = 'הועתק!';
          setTimeout(() => traceIdPill.textContent = old, 1200);
        }
      });

      // Cross-Window Synchronization
      if (window.BroadcastChannel) {
        try {
          const channel = new BroadcastChannel('agentshub_trace_sync');
          channel.onmessage = (msg) => {
            if (msg.data && msg.data.traceId) {
              switchTrace(msg.data.traceId);
            }
          };
        } catch (e) {}
      }

      window.addEventListener('storage', (e) => {
        if (e.key === 'agentshub_active_trace') {
          try {
            const parsed = JSON.parse(e.newValue);
            if (parsed && parsed.traceId) switchTrace(parsed.traceId);
          } catch (err) {}
        }
      });

      // Initial load
      traceIdPill.textContent = currentTraceId || '—';
      loadRecentTraces();
      if (currentTraceId) fetchTrace();

      // Poll Loop
      pollTimer = setInterval(fetchTrace, 1000);
      recentTimer = setInterval(loadRecentTraces, 2500);

      // Fit to view once layout rendered
      setTimeout(fitToView, 800);
    })();
  </script>
</body>
</html>
"""


def render_behind_the_scenes_html(*, trace_id: str = "", profile_name: str = "") -> str:
    """Return the complete standalone HTML page for Behind the Scenes."""
    safe_trace = html.escape(trace_id or "")
    return HTML_PAGE_TEMPLATE.replace("__SAFE_TRACE_ID__", safe_trace)
