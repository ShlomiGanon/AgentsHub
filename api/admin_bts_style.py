"""Standalone Behind-the-Scenes page CSS fragment."""

BTS_HEAD_AND_STYLE = r"""<!DOCTYPE html>
<html lang="__BTS_LANG__" dir="__BTS_DIR__">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>AgentsHub • Live Agent Execution & Communication Graph</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Heebo:wght@400;500;600;700;800&family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
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
      --accent-emerald: #84cc16;
      --accent-rose: #f43f5e;
      --accent-amber: #f59e0b;
      --accent-purple: #c084fc;
      --glow-cyan: 0 0 24px rgba(56, 189, 248, 0.25);
      --glow-emerald: 0 0 24px rgba(132, 204, 22, 0.25);
      --glow-rose: 0 0 24px rgba(244, 63, 94, 0.25);
      --font-family: Heebo, Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
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
      direction: __BTS_DIR__;
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
      direction: ltr;
      unicode-bidi: isolate;
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

    /* Graph cards live inside SVG foreignObject. Isolate layout to LTR so the
       card box matches the foreignObject coordinate system; label glyphs still
       follow their own script via unicode-bidi. */
    .bts-node-host {
      width: 100%;
      height: 100%;
      overflow: hidden;
      direction: ltr;
      box-sizing: border-box;
    }
    .bts-node-card {
      background: rgba(15, 23, 42, 0.95);
      backdrop-filter: blur(8px);
      border: 1.5px solid var(--border-medium);
      border-radius: 10px;
      padding: 10px 12px;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      gap: 6px;
      cursor: pointer;
      transition: border-color 0.2s ease, box-shadow 0.2s ease;
      width: 100%;
      height: 100%;
      overflow: hidden;
      box-sizing: border-box;
      direction: ltr;
      text-align: center;
      box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.6);
    }
    .bts-node-card:hover {
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
      justify-content: center;
      gap: 10px;
      width: 100%;
      min-width: 0;
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
      flex: 1 1 auto;
      min-width: 0;
      overflow: hidden;
      text-align: center;
    }

    .bts-node-name {
      font-size: 13.5px;
      font-weight: 700;
      color: var(--text-main);
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      unicode-bidi: plaintext;
      text-align: center;
    }

    .bts-node-sub {
      font-size: 10.5px;
      color: var(--text-dim);
      font-family: monospace;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      unicode-bidi: plaintext;
      text-align: center;
    }

    .bts-node-badge-row {
      display: flex;
      align-items: center;
      justify-content: center;
      flex-wrap: wrap;
      gap: 6px;
      width: 100%;
      margin-top: 2px;
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
      margin-top: 2px;
      max-width: 100%;
      unicode-bidi: plaintext;
      text-align: center;
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
  </style>"""
