"""Admin HTML/CSS chrome, page templates, and simulator shell."""

from __future__ import annotations

from api.admin_api_pages import API_CLIENT_SCRIPT, API_CONSOLE_STYLE, EVENTS_BODY, IDENTITY_BAR, PROFILES_BODY, PROTOCOLS_BODY
from api.admin_simulator_assets import SIMULATOR_BODY, SIMULATOR_STYLE
from api.admin_tables import ADMIN_TABLES_EDIT_BODY, ADMIN_TABLES_LIST_BODY

# Bootstrap ships a mirrored build for right-to-left pages; the template picks one by the
# catalog's language (`dir` below), so the Hebrew catalog gets a genuinely RTL layout rather
# than an LTR grid with Hebrew text poured into it.
_FONTS_LINK = (
    '<link rel="preconnect" href="https://fonts.googleapis.com">'
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
    '<link href="https://fonts.googleapis.com/css2?family=Heebo:wght@400;500;600;700;800&family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">'
)
_BOOTSTRAP_CSS_LINK = _FONTS_LINK + (
    '{% if dir == "rtl" %}'
    '<link href="https://cdnjs.cloudflare.com/ajax/libs/bootstrap/5.3.3/css/bootstrap.rtl.min.css" rel="stylesheet">'
    "{% else %}"
    '<link href="https://cdnjs.cloudflare.com/ajax/libs/bootstrap/5.3.3/css/bootstrap.min.css" rel="stylesheet">'
    "{% endif %}"
)

# Shared LeadSpotting-style chrome for every authenticated admin page. Physical left/right
# properties are written as CSS logical properties so the same stylesheet lays out correctly
# under both `dir="ltr"` (sidebar on the start/left edge) and `dir="rtl"` (sidebar on the
# start/right edge).
_DASHBOARD_STYLE = """
<style>
  :root {
    --navy: #0B192C;
    --navy-soft: #15263d;
    --bg: #f5f7fb;
    --panel: #ffffff;
    --panel-muted: #f4f7fb;
    --line: #e2e8f0;
    --line-strong: #cbd5e1;
    --text: #0B192C;
    --text-dim: #5b6b80;
    --text-faint: #8b9bb0;
    --sidebar: #ffffff;
    --sidebar-text: #0B192C;
    --teal: #0f766e;
    --lime: #84cc16;
    --lime-hover: #65a30d;
    --lime-dim: #ecfccb;
    --lime-text: #0B192C;
    --blue: #2563eb;
    --blue-hover: #1d4ed8;
    --blue-dim: #dbeafe;
    --commander: #84cc16;
    --commander-dim: #ecfccb;
    --viewer: #2563eb;
    --viewer-dim: #dbeafe;
    --danger: #b91c1c;
    --danger-dim: #fee2e2;
    --warning: #b45309;
    --warning-dim: #fef3c7;
    --gold: #eab308;
    --shadow: 0 1px 2px rgba(11, 25, 44, .04), 0 6px 18px rgba(11, 25, 44, .05);
    --shadow-lg: 0 8px 22px rgba(11, 25, 44, .09);
    --radius: 10px;
    --radius-sm: 6px;
    --control-h: 36px;
    --btn-h: 36px;
    --space-1: 4px;
    --space-2: 8px;
    --space-3: 12px;
    --space-4: 16px;
    --space-5: 20px;
    --sans: Heebo, Inter, -apple-system, 'Segoe UI', sans-serif;
    --mono: 'SF Mono', 'JetBrains Mono', ui-monospace, Consolas, monospace;
  }
  * { box-sizing: border-box; }
  body {
    background: var(--bg);
    color: var(--text);
    font-family: var(--sans);
    font-size: 14.5px;
    line-height: 1.45;
    font-weight: 400;
    margin: 0;
    min-height: 100vh;
  }
  :focus-visible { outline: 2px solid var(--blue); outline-offset: 2px; }
  .ls-app { display: flex; min-height: 100vh; background: var(--bg); }
  .ls-sidebar {
    width: 236px;
    flex-shrink: 0;
    position: sticky;
    top: 0;
    height: 100vh;
    background: var(--sidebar);
    color: var(--sidebar-text);
    display: flex;
    flex-direction: column;
    padding: 14px 8px 10px;
    transition: width .2s ease;
    border-inline-end: 1px solid var(--line);
  }
  .ls-app.ls-sidebar-collapsed .ls-sidebar { width: 72px; padding-inline: 10px; }
  .ls-brand {
    display: flex; align-items: center; justify-content: center;
    gap: 8px; text-decoration: none; color: inherit;
    padding: 4px 8px 12px; min-height: 40px;
    border-bottom: 1px solid var(--line);
    margin-bottom: 8px;
  }
  .ls-logo {
    display: block; height: 28px; width: auto; max-width: 100%;
  }
  .ls-mark {
    display: none; width: 32px; height: 32px; object-fit: contain;
  }
  .ls-app.ls-sidebar-collapsed .ls-logo { display: none; }
  .ls-app.ls-sidebar-collapsed .ls-mark { display: block; }
  .ls-app.ls-sidebar-collapsed .ls-brand { border-bottom: 0; margin-bottom: 8px; padding-bottom: 8px; }
  .ls-nav { display: flex; flex-direction: column; gap: 2px; flex: 1; overflow: auto; padding-inline: 2px; }
  .ls-nav-group { margin-top: 8px; padding-top: 2px; }
  .ls-nav-group:first-child { margin-top: 0; }
  .ls-nav-group-label {
    display: block;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: .08em;
    text-transform: uppercase;
    color: var(--text-faint);
    padding: 4px 10px 3px;
  }
  .ls-app.ls-sidebar-collapsed .ls-nav-group-label {
    height: 1px; padding: 0; margin: 8px 10px 6px; color: transparent;
    background: var(--line); overflow: hidden;
  }
  .ls-nav-item {
    position: relative;
    display: flex; align-items: center; gap: 10px;
    color: var(--navy); text-decoration: none;
    padding: 7px 10px; border-radius: var(--radius-sm); font-size: 13px; font-weight: 500;
    transition: background .15s ease, color .15s ease;
  }
  .ls-nav-item .ls-icon { color: var(--blue); }
  .ls-nav-item:hover { background: var(--blue-dim); color: var(--blue); }
  .ls-nav-item.is-active {
    background: var(--blue-dim);
    color: var(--blue);
  }
  .ls-nav-item.is-active::before {
    content: "";
    position: absolute;
    inset-inline-start: 0;
    top: 7px; bottom: 7px;
    width: 3px;
    border-radius: 999px;
    background: var(--lime);
  }
  .ls-nav-item.is-active .ls-icon { color: var(--blue); }
  .ls-icon { width: 18px; height: 18px; flex-shrink: 0; stroke: currentColor; fill: none; stroke-width: 1.8; stroke-linecap: round; stroke-linejoin: round; }
  .ls-app.ls-sidebar-collapsed .ls-nav-label { display: none; }
  .ls-app.ls-sidebar-collapsed .ls-nav-item { justify-content: center; padding: 10px; }
  .ls-sidebar-toggle {
    margin-top: 8px; border: 0; background: var(--panel-muted); color: var(--navy);
    border-radius: var(--radius-sm); padding: 8px; cursor: pointer;
    transition: background .15s ease, color .15s ease;
  }
  .ls-sidebar-toggle:hover { background: var(--blue-dim); color: var(--blue); }
  .ls-app.ls-sidebar-collapsed .ls-sidebar-toggle .ls-icon { transform: rotate(180deg); }
  [dir="rtl"] .ls-sidebar-toggle .ls-icon { transform: scaleX(-1); }
  [dir="rtl"] .ls-app.ls-sidebar-collapsed .ls-sidebar-toggle .ls-icon { transform: scaleX(-1) rotate(180deg); }
  .ls-main { flex: 1; min-width: 0; display: flex; flex-direction: column; }
  .ls-topbar {
    display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
    padding: 8px 24px; min-height: 56px;
    background: var(--panel);
    border-bottom: 1px solid var(--line);
    position: sticky; top: 0; z-index: 20;
  }
  .ls-topbar-end { margin-inline-start: auto; display: flex; align-items: center; gap: 10px; }
  .ls-btn-fill, .ls-btn-outline, .ls-user-btn,
  .btn-console, .btn-console-primary, .btn-console-danger {
    display: inline-flex; align-items: center; justify-content: center; gap: 8px;
    min-height: var(--btn-h); border-radius: var(--radius-sm);
    font-size: 13px; font-weight: 600; letter-spacing: .01em; line-height: 1;
    text-decoration: none; border: 1.5px solid transparent; padding: 7px 14px;
    cursor: pointer; transition: background .15s ease, border-color .15s ease, color .15s ease, box-shadow .15s ease, filter .15s ease;
  }
  .ls-btn-fill { background: var(--navy); color: #fff; border-color: var(--navy); }
  .ls-btn-fill:hover { background: var(--navy-soft); color: #fff; box-shadow: 0 6px 14px rgba(11, 25, 44, .18); }
  .ls-btn-outline { background: #fff; color: var(--blue); border-color: var(--blue); font-weight: 600; }
  .ls-status-live {
    display: inline-flex; align-items: center; gap: 6px;
    min-height: 28px; padding: 4px 10px;
    border: 1px solid var(--line); border-radius: 999px;
    background: var(--panel-muted); color: var(--text-dim);
    font-size: 12px; font-weight: 600;
  }
  .ls-status-live .dot {
    display: inline-block; width: 6px; height: 6px; border-radius: 50%;
    background: var(--lime); box-shadow: 0 0 0 3px rgba(132,204,22,.22);
  }
  .ls-user-btn {
    background: #fff; color: var(--text); border-color: var(--line-strong); font-weight: 600;
  }
  .ls-user-btn:hover { border-color: var(--blue); color: var(--blue); }
  .ls-content { flex: 1; padding: 16px 24px 32px; }
  .ls-page { max-width: 1080px; }
  .ls-page-wide { max-width: 1360px; }
  .container-narrow, .container-wide { max-width: none; padding: 0; }
  .ls-page-header { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; flex-wrap: wrap; margin-bottom: 14px; }
  .ls-page-header h1, .ls-page > h1:first-child { font-size: 22px; font-weight: 600; letter-spacing: -.02em; color: var(--navy); margin: 0 0 4px; }
  .ls-page-header .subtitle, .ls-page > h1 + .subtitle { margin: 0; max-width: 680px; }
  .ls-section { margin-bottom: 16px; }
  .ls-section-title, .ls-home-group-title, .block-label {
    display: block;
    font-size: 11px; font-weight: 700; letter-spacing: .08em; text-transform: uppercase;
    color: var(--text-faint); margin: 0 0 8px;
  }

  h1 { font-size: 22px; font-weight: 600; letter-spacing: -.02em; color: var(--navy); }
  h2 { font-weight: 600; color: var(--navy); font-size: 17px; }
  h3 { font-weight: 600; color: var(--navy); }
  .ls-content:has(.ls-home) { padding: 20px 28px 40px; }
  .ls-home { max-width: 1120px; }
  .ls-home .ls-page-header { margin-bottom: 20px; gap: 16px; }
  .ls-home-title { text-align: start; font-size: 22px; font-weight: 600; margin-bottom: 4px; }
  .ls-home-sub { text-align: start; max-width: 680px; margin: 0; }
  .ls-home-group {
    margin-bottom: 26px; padding: 0;
    background: transparent; border: 0; border-radius: 0; box-shadow: none;
  }
  .ls-home-group-title {
    font-size: 12px; font-weight: 700; letter-spacing: .08em; text-transform: uppercase;
    color: var(--blue); margin: 0 0 10px;
  }
  .ls-service-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; }
  .ls-tabs { display: flex; gap: 2px; flex-wrap: wrap; border-bottom: 1px solid var(--line); margin: 0 0 14px; }
  .ls-tab {
    background: transparent; border: 0; border-bottom: 2px solid transparent; margin-bottom: -1px;
    padding: 8px 12px; font-size: 13px; font-weight: 600; color: var(--text-dim); cursor: pointer;
    transition: color .15s ease, border-color .15s ease, background .15s ease;
  }
  .ls-tab:hover { color: var(--navy); background: var(--panel-muted); }
  .ls-tab.is-active { color: var(--blue); border-bottom-color: var(--blue); }
  .protocol-layout { display: grid; grid-template-columns: 220px minmax(0, 1fr); gap: 16px; align-items: start; }
  .ls-content:has(.ls-protocols) { padding: 20px 28px 40px; }
  .ls-protocols { max-width: 1400px; }
  .ls-protocols .ls-page-header { margin-bottom: 20px; gap: 16px; }
  .ls-protocols .ls-table-toolbar { margin-bottom: 16px; }
  .ls-protocols .ls-identity-block { margin-bottom: 16px; padding: 16px 18px; }
  .ls-protocols .protocol-layout { grid-template-columns: 240px minmax(0, 1fr); gap: 20px; }
  .ls-protocols .protocol-nav { padding: 12px; }
  .ls-protocols .protocol-nav-item { padding: 10px 12px; }
  .ls-protocols .protocol-section { margin-bottom: 16px; padding-bottom: 16px; }
  .ls-protocols .block-console { padding: 18px 18px 16px; }
  .ls-protocols .api-form-grid { gap: 12px; }
  .ls-protocols textarea.form-control-console { min-height: 88px; }
  .protocol-nav { background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); padding: 8px; box-shadow: var(--shadow); position: sticky; top: 72px; }
  .protocol-nav-item {
    display: block; width: 100%; text-align: start; border: 0; background: transparent;
    padding: 8px 10px; border-radius: 8px; font-size: 13px; font-weight: 400; color: var(--text); cursor: pointer;
  }
  .protocol-nav-item:hover { background: var(--panel-muted); }
  .protocol-nav-item.is-active { background: var(--blue-dim); color: var(--blue); }
  .protocol-editor-block[hidden], [data-panel][hidden] { display: none !important; }
  .ls-status-strip { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 8px; margin-bottom: 12px; }
  .ls-status-chip {
    background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius-sm);
    padding: 8px 10px; font-size: 12px; color: var(--text-dim); min-height: 56px;
    display: flex; flex-direction: column; justify-content: center; gap: 2px;
  }
  .ls-status-chip .ls-status-kicker { font-size: 10px; font-weight: 700; letter-spacing: .06em; text-transform: uppercase; color: var(--text-faint); }
  .ls-status-chip strong { color: var(--text); font-weight: 600; }
  .ls-status-chip:first-child { border-inline-start: 3px solid var(--blue); }
  .ls-danger-zone { border-color: #fecaca; }
  .ls-compact-table td .form-control-console, .ls-compact-table td .form-select-console { min-height: 30px; padding: 3px 8px; font-size: 13px; }
  .ls-compact-table tbody td { padding: 5px 8px; }
  .ls-table-toolbar { display: flex; align-items: center; justify-content: space-between; gap: 10px; flex-wrap: wrap; margin-bottom: 8px; }
  .ls-count { font-size: 12px; font-weight: 600; color: var(--text-dim); }
  .ls-actions { display: flex; gap: 4px; flex-wrap: wrap; align-items: center; }
  .ls-actions .btn { min-height: 28px; padding: 2px 8px; font-size: 12px; font-weight: 600; }
  .ls-empty-state { text-align: center; padding: 28px 18px; color: var(--text-dim); background: var(--panel); border: 1px dashed var(--line-strong); border-radius: var(--radius); }
  .ls-empty-icon { width: 32px; height: 32px; margin: 0 auto 8px; color: var(--blue); }
  .ls-empty-state strong { display: block; color: var(--navy); font-size: 15px; font-weight: 600; margin-bottom: 4px; }
  .ls-empty-state p { margin: 0 auto; max-width: 400px; font-size: 13px; }
  .ls-empty-state .btn { margin-top: 12px; }
  .ls-chip-list { display: flex; flex-wrap: wrap; gap: 6px; }
  .tech, .tag.tech, .form-control-console.tech, .form-select-console.tech {
    direction: ltr; unicode-bidi: isolate; text-align: start; font-family: var(--mono);
  }
  .protocol-section { margin-bottom: 12px; padding-bottom: 12px; border-bottom: 1px solid var(--line); }
  .protocol-section:last-child { border-bottom: 0; margin-bottom: 0; padding-bottom: 0; }
  .protocol-nav-create { color: var(--blue); }
  .sim-step.is-primary { border-color: var(--blue); box-shadow: 0 0 0 3px rgba(37, 99, 235, .12); }
  @media (max-width: 900px) { .protocol-layout { grid-template-columns: 1fr; } .protocol-nav { position: static; } }
  .ls-service-card {
    display: block; background: var(--panel); border-radius: var(--radius); padding: 18px 18px 16px;
    text-decoration: none; color: inherit; height: 100%;
    box-shadow: var(--shadow);
    border: 1px solid var(--line);
    transition: transform .25s ease, box-shadow .25s ease, border-color .25s ease;
  }
  .ls-service-card:hover, .ls-service-card:focus-visible {
    transform: scale(1.02);
    box-shadow: var(--shadow-lg);
    border-color: rgba(37, 99, 235, .35);
    color: inherit;
  }
  .ls-service-icon {
    width: 36px; height: 36px; border-radius: 10px; display: grid; place-items: center;
    background: var(--blue-dim); color: var(--blue); margin-bottom: 12px;
    transition: background .25s ease, color .25s ease;
  }
  .ls-service-card:hover .ls-service-icon, .ls-service-card:focus-visible .ls-service-icon {
    background: var(--blue); color: #fff;
  }
  .ls-service-card h2 { font-size: 15px; font-weight: 600; margin: 0 0 4px; color: var(--navy); }
  .ls-service-card .subtitle { font-size: 13px; display: block; color: var(--text-dim); }
  .ls-service-card.is-featured {
    background: var(--blue); border-color: var(--blue); color: #fff;
  }
  .ls-service-card.is-featured h2, .ls-service-card.is-featured .subtitle { color: #fff; }
  .ls-service-card.is-featured .subtitle { color: rgba(255,255,255,.82); }
  .ls-service-card.is-featured .ls-service-icon { background: rgba(255,255,255,.16); color: #fff; }
  .ls-service-card.is-featured:hover, .ls-service-card.is-featured:focus-visible {
    background: var(--blue-hover); border-color: var(--blue-hover); color: #fff;
  }
  .ls-service-card.is-featured:hover .ls-service-icon, .ls-service-card.is-featured:focus-visible .ls-service-icon {
    background: rgba(255,255,255,.22); color: #fff;
  }
  .ls-service-card.is-featured-navy { background: var(--navy); border-color: var(--navy); }
  .ls-service-card.is-featured-navy:hover, .ls-service-card.is-featured-navy:focus-visible {
    background: var(--navy-soft); border-color: var(--navy-soft);
  }
  .ls-service-card.is-live {
    background: var(--panel); border: 2px solid var(--gold);
  }
  .ls-service-card.is-live:hover, .ls-service-card.is-live:focus-visible {
    border-color: var(--gold);
  }
  @media (max-width: 980px) { .ls-service-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
  @media (max-width: 640px) {
    .ls-service-grid { grid-template-columns: 1fr; }
    .ls-sidebar { position: sticky; top: 0; align-self: flex-start; max-height: 100vh; }
    .ls-content { padding: 12px 14px 28px; }
    .ls-topbar { padding-inline: 16px; }
  }
  @media (prefers-reduced-motion: reduce) {
    .ls-service-card, .ls-nav-item, .ls-btn-fill, .ls-btn-outline, .ls-user-btn, .btn-console, .btn-console-primary, .btn-console-danger {
      transition: none;
    }
    .ls-service-card:hover, .ls-service-card:focus-visible { transform: none; }
  }

  .status-pill { font-size: 13px; color: var(--text-faint); }
  .status-pill .dot {
    display: inline-block; width: 6px; height: 6px; border-radius: 50%;
    background: var(--lime); box-shadow: 0 0 0 3px rgba(132,204,22,.28); margin-inline-end: 6px;
  }
  .subtitle { color: var(--text-dim); font-size: 13.5px; line-height: 1.45; }
  .nav-console { font-size: 13px; color: var(--blue); text-decoration: none; font-weight: 600; }
  .nav-console:hover { color: var(--blue-hover); text-decoration: underline; }

  .alert-console {
    background: var(--commander-dim);
    border: 1px solid #bef264;
    border-inline-start: 3px solid var(--lime);
    border-radius: var(--radius-sm);
    color: #3f6212;
    font-size: 14px;
  }
  .alert-console b { font-weight: 600; }
  .alert-console-error {
    background: var(--danger-dim);
    border: 1px solid #fca5a5;
    border-inline-start: 3px solid var(--danger);
    border-radius: var(--radius-sm);
    color: #7f1d1d;
    font-size: 14px;
  }
  .alert-console-error b { font-weight: 600; }

  .ls-page > table.table-console,
  .ls-table-card {
    background: var(--panel);
    border: 1px solid var(--line);
    border-radius: var(--radius);
    box-shadow: var(--shadow);
    padding: 0;
    overflow: auto;
  }
  .table-responsive { overflow: auto; }
  table.table-console {
    --bs-table-bg: transparent;
    border-collapse: collapse;
    font-size: 14px;
    margin-bottom: 0;
  }
  table.table-console thead th {
    font-size: 11px;
    font-weight: 700;
    color: var(--text-faint);
    letter-spacing: 0.06em;
    text-transform: uppercase;
    background: var(--panel-muted);
    border-bottom: 1px solid var(--line) !important;
    border-top: none;
    padding: 8px 12px;
    white-space: nowrap;
  }
  table.table-console tbody td {
    border-color: var(--line);
    vertical-align: middle;
    padding: 8px 12px;
    font-size: 13px;
  }
  table.table-console tbody tr:nth-child(even) { background: #f7f9fc; }
  table.table-console tbody tr:hover { background: #eef4ff; }
  table.table-console tbody td:first-child,
  table.table-console thead th:first-child { padding-inline-start: 16px; }
  table.table-console .ls-empty,
  table.table-console td[colspan] {
    color: var(--text-dim);
    text-align: center;
    padding: 28px 16px;
    background: var(--panel-muted);
  }

  .identity {
    font-family: var(--mono);
    font-size: 12.5px;
    direction: ltr;
    unicode-bidi: isolate;
  }
  .identity-id {
    display: inline-block;
    max-width: 260px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    vertical-align: bottom;
  }
  .identity .tag { font-family: var(--sans); font-size: 11px; color: var(--text-faint); margin-inline-start: 8px; }
  .tag {
    display: inline-block; font-size: 11px; font-weight: 600; color: var(--text-dim);
    background: #f1f5f9; border-radius: 999px; padding: 2px 8px; line-height: 1.4;
  }

  .level-dot {
    display: inline-block;
    width: 5px; height: 5px;
    border-radius: 50%;
    margin-inline-end: 6px;
  }
  .badge-commander { color: var(--commander); }
  .badge-commander .level-dot { background: var(--commander); }
  .badge-viewer { color: var(--viewer); }
  .badge-viewer .level-dot { background: var(--viewer); }
  .level-label { font-family: var(--mono); font-size: 13px; }

  .form-select-console, .form-control-console {
    background: #fff;
    border: 1px solid var(--line);
    border-radius: var(--radius-sm);
    color: var(--text);
    font-size: 13.5px;
    min-height: var(--control-h);
    padding: 6px 10px;
  }
  textarea.form-control-console { min-height: 76px; padding: 8px 10px; }
  .form-select-console:focus, .form-control-console:focus {
    border-color: var(--blue);
    box-shadow: 0 0 0 0.2rem rgba(37, 99, 235, 0.16);
  }
  .form-select-console:disabled, .form-control-console:disabled,
  .form-control-console[readonly] {
    background: var(--panel-muted);
    color: var(--text-dim);
  }

  .btn-console {
    background: #fff;
    color: var(--blue);
    border-color: var(--blue);
  }
  .btn-console:hover { border-color: var(--blue-hover); color: var(--blue-hover); background: var(--blue-dim); }
  .btn-console-danger { color: var(--danger); border-color: #fca5a5; background: #fff; }
  .btn-console-danger:hover { border-color: var(--danger); color: var(--danger); background: var(--danger-dim); }
  .btn-console-primary {
    background: var(--lime); border-color: var(--lime); color: var(--lime-text); font-weight: 700;
  }
  .btn-console-primary:hover { background: var(--lime-hover); border-color: var(--lime-hover); color: var(--lime-text); filter: brightness(1.02); box-shadow: 0 6px 14px rgba(132, 204, 22, .28); }
  .btn-console:disabled, .btn-console-primary:disabled, .btn-console-danger:disabled,
  .ls-btn-fill:disabled, .ls-user-btn:disabled {
    opacity: .55; cursor: not-allowed; box-shadow: none; filter: none;
  }
  .btn-sm.btn-console, .btn-sm.btn-console-primary, .btn-sm.btn-console-danger {
    min-height: 28px; padding: 3px 9px; font-size: 12px; font-weight: 600;
  }

  .block-console {
    border: 1px solid var(--line);
    border-radius: var(--radius);
    padding: 14px 16px 14px;
    position: relative;
    background: var(--panel);
    box-shadow: var(--shadow);
  }
  .ls-section > .ls-section-title + .block-console > .block-label:first-child { display: none; }
  .ls-identity-block { margin-bottom: 12px; }
  .ls-create-panel { background: var(--panel-muted); box-shadow: none; }
  .form-label-console {
    font-size: 12px;
    font-weight: 600;
    color: var(--text-dim);
    letter-spacing: 0.01em;
    margin-bottom: 4px;
  }
  code.console-code {
    font-family: var(--mono);
    font-size: 12px;
    color: var(--viewer);
    background: var(--viewer-dim);
    padding: 1px 5px;
    border-radius: 3px;
    direction: ltr;
    unicode-bidi: isolate;
  }
</style>
"""

_LOGIN_STYLE = """
<style>
  :root {
    --bg: #f8fafc;
    --text: #0B192C;
    --text-dim: #475569;
    --text-faint: #94a3b8;
    --lime: #84cc16;
    --lime-hover: #65a30d;
    --blue: #2563eb;
    --danger: #b91c1c;
    --danger-dim: #fee2e2;
    --sans: Heebo, Inter, -apple-system, 'Segoe UI', sans-serif;
  }
  body.ls-login {
    background: var(--bg);
    color: var(--text);
    font-family: var(--sans);
    font-size: 16px;
    min-height: 100vh;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    padding: 24px;
    margin: 0;
  }
  .login-brand {
    margin-bottom: 28px;
    text-align: center;
    background: #0B192C;
    border-radius: 16px;
    padding: 18px 28px;
    box-shadow: 0 12px 32px rgba(11, 25, 44, .18);
  }
  .login-brand .ls-logo { display: block; height: 44px; width: auto; max-width: 280px; margin: 0 auto; }
  .login-card {
    width: 100%;
    max-width: 420px;
    background: #ffffff;
    border: 1px solid #e2e8f0;
    border-radius: 12px;
    padding: 32px 28px 28px;
    box-shadow: 0 1px 2px rgba(11, 25, 44, .04), 0 8px 24px rgba(11, 25, 44, .06);
  }
  .login-card h1 {
    font-size: 22px;
    font-weight: 700;
    letter-spacing: -0.02em;
    margin: 0 0 8px;
    text-align: center;
  }
  .login-card .subtitle {
    font-size: 13px;
    color: var(--text-faint);
    margin-bottom: 24px;
    text-align: center;
  }
  .form-label-console {
    font-size: 12px;
    color: var(--text-faint);
    letter-spacing: 0.02em;
    margin-bottom: 4px;
    display: block;
  }
  .form-control-console, .form-select-console {
    background: #fff;
    border: 1px solid #e2e8f0;
    border-radius: 8px;
    color: var(--text);
    font-size: 15px;
    width: 100%;
    min-height: 44px;
    padding: 12px 14px;
  }
  .form-control-console::placeholder { color: #94a3b8; }
  .form-control-console:focus {
    outline: none;
    border-color: var(--blue);
    box-shadow: 0 0 0 0.2rem rgba(37, 99, 235, 0.18);
    background: #fff;
  }
  .field-group { margin-bottom: 14px; }

  .alert-console-error {
    background: var(--danger-dim);
    border: 1px solid #fca5a5;
    border-inline-start: 3px solid var(--danger);
    border-radius: 12px;
    color: #7f1d1d;
    font-size: 13px;
    padding: 10px 14px;
    margin-bottom: 20px;
  }
  .lockout-progress-track {
    margin-top: 10px;
    height: 6px;
    border-radius: 3px;
    background: rgba(185, 28, 28, 0.18);
    overflow: hidden;
  }
  .lockout-progress-fill {
    height: 100%;
    border-radius: 3px;
    background: var(--danger);
  }

  .login-actions { text-align: center; margin-top: 8px; }
  .btn-console-primary {
    background: var(--lime);
    border: 0;
    color: #0B192C;
    font-size: 15px;
    font-weight: 700;
    letter-spacing: .02em;
    border-radius: 8px;
    padding: 12px 32px;
    min-width: 180px;
    min-height: 44px;
    transition: background .2s ease, box-shadow .2s ease;
  }
  .btn-console-primary:hover { background: var(--lime-hover); color: #0B192C; box-shadow: 0 8px 18px rgba(132, 204, 22, .28); }

  .status-pill {
    font-size: 12px;
    color: var(--text-faint);
    display: flex;
    align-items: center;
    justify-content: center;
    margin-top: 22px;
  }
  .status-pill .dot {
    display: inline-block;
    width: 6px; height: 6px;
    border-radius: 50%;
    background: var(--lime);
    box-shadow: 0 0 0 3px rgba(132,204,22,.28);
    margin-inline-end: 6px;
  }
</style>
"""

_LOGIN_TEMPLATE = """<!DOCTYPE html>
<html lang="{{ lang }}" dir="{{ dir }}">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{{ t('admin.login_title') }}</title>
""" + _BOOTSTRAP_CSS_LINK + _LOGIN_STYLE + """
</head>
<body class="ls-login">

  <div class="login-brand">
    <img class="ls-logo" src="{{ url_for('static', filename='leadspotting-logo.gif') }}" alt="LeadSpotting">
  </div>
  <div class="login-card">
    <h1>{{ t('admin.login_title') }}</h1>
    <p class="subtitle">{{ t('admin.login_subtitle') }}</p>

    {% if lockout %}
      <div class="alert-console-error">
        {{ lockout.message }}
        <div class="lockout-progress-track" role="progressbar"
             aria-valuenow="{{ lockout.percent_elapsed }}" aria-valuemin="0" aria-valuemax="100"
             aria-label="Lockout time elapsed">
          <div class="lockout-progress-fill" style="width: {{ lockout.percent_elapsed }}%;"></div>
        </div>
      </div>
    {% else %}
      {% for category, message in get_flashed_messages(with_categories=true) %}
        <div class="alert-console-error">{{ message }}</div>
      {% endfor %}
    {% endif %}

    <form id="loginForm" method="post">
      <div class="field-group">
        <label class="form-label-console visually-hidden" for="username">{{ t('admin.username') }}</label>
        <input type="text" class="form-control-console" id="username" name="username" placeholder="{{ t('admin.username') }}" autofocus required>
      </div>
      <div class="field-group">
        <label class="form-label-console visually-hidden" for="password">{{ t('admin.password') }}</label>
        <input type="password" class="form-control-console" id="password" name="password" placeholder="{{ t('admin.password') }}" required>
      </div>
      <div class="login-actions">
        <button type="submit" class="btn-console-primary">{{ t('admin.sign_in') }}</button>
      </div>
    </form>

    <div class="status-pill"><span class="dot"></span>{{ t('admin.connected') }}</div>
  </div>

</body>
</html>
"""

_ICON_HOME = '<svg class="ls-icon" viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="3" width="8" height="8" rx="1.5"/><rect x="13" y="3" width="8" height="8" rx="1.5"/><rect x="3" y="13" width="8" height="8" rx="1.5"/><rect x="13" y="13" width="8" height="8" rx="1.5"/></svg>'
_ICON_PROFILES = '<svg class="ls-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M4 8h16"/><path d="M4 12h16"/><path d="M4 16h16"/><path d="M8 6v4"/><path d="M12 10v4"/><path d="M16 14v4"/></svg>'
_ICON_PROTOCOLS = '<svg class="ls-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M8 6h12"/><path d="M8 12h12"/><path d="M8 18h12"/><circle cx="4.5" cy="6" r="1.2" fill="currentColor" stroke="none"/><circle cx="4.5" cy="12" r="1.2" fill="currentColor" stroke="none"/><circle cx="4.5" cy="18" r="1.2" fill="currentColor" stroke="none"/></svg>'
_ICON_EVENTS = '<svg class="ls-icon" viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18"/><path d="M8 3v4"/><path d="M16 3v4"/></svg>'
_ICON_USERS = '<svg class="ls-icon" viewBox="0 0 24 24" aria-hidden="true"><circle cx="9" cy="8" r="3"/><path d="M3.5 19c.8-3 2.8-4.5 5.5-4.5S13.7 16 14.5 19"/><circle cx="17" cy="9" r="2.4"/><path d="M16.2 14.6c2.2.3 3.8 1.6 4.3 4.4"/></svg>'
_ICON_GROUPS = '<svg class="ls-icon" viewBox="0 0 24 24" aria-hidden="true"><circle cx="8" cy="9" r="2.5"/><circle cx="16" cy="9" r="2.5"/><circle cx="12" cy="8" r="2.7"/><path d="M4 19c.7-2.6 2.4-4 5-4"/><path d="M20 19c-.7-2.6-2.4-4-5-4"/><path d="M8.5 19c.7-2.4 2-3.6 3.5-3.6s2.8 1.2 3.5 3.6"/></svg>'
_ICON_SIMULATOR = '<svg class="ls-icon" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M10 8.5v7l6-3.5z" fill="currentColor" stroke="none"/></svg>'
_ICON_SERVER = '<svg class="ls-icon" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M3 12h18"/><path d="M12 3c3 3.2 4.5 6.2 4.5 9S15 17.8 12 21"/><path d="M12 3c-3 3.2-4.5 6.2-4.5 9S9 17.8 12 21"/></svg>'
_ICON_TABLE = '<svg class="ls-icon" viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 9h18"/><path d="M3 14h18"/><path d="M9 9v11"/><path d="M15 9v11"/></svg>'
_ICON_TOGGLE = '<svg class="ls-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M15 6l-6 6 6 6"/></svg>'
_ICON_PERSON = '<svg class="ls-icon" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="8" r="3.2"/><path d="M5 19c1-3.4 3.4-5 7-5s6 1.6 7 5"/></svg>'

_SHELL_SCRIPT = """
<script>
(function () {
  const root = document.querySelector('.ls-app');
  if (!root) return;
  const key = 'admin-sidebar-collapsed';
  if (window.localStorage.getItem(key) === '1') root.classList.add('ls-sidebar-collapsed');
  const toggle = document.getElementById('ls-sidebar-toggle');
  if (toggle) toggle.addEventListener('click', function () {
    root.classList.toggle('ls-sidebar-collapsed');
    window.localStorage.setItem(key, root.classList.contains('ls-sidebar-collapsed') ? '1' : '0');
  });
  document.querySelectorAll('[data-ls-tabs]').forEach(function (group) {
    const buttons = Array.from(group.querySelectorAll('[data-tab]'));
    const scope = group.closest('.ls-page, .ls-page-wide') || document;
    function show(name) {
      buttons.forEach(function (button) { button.classList.toggle('is-active', button.dataset.tab === name); });
      scope.querySelectorAll('[data-panel]').forEach(function (panel) {
        if (panel.closest('[data-ls-tabs]') && panel.closest('[data-ls-tabs]') !== group) return;
        panel.hidden = panel.dataset.panel !== name;
      });
    }
    buttons.forEach(function (button) { button.addEventListener('click', function () { show(button.dataset.tab); }); });
    const initial = group.getAttribute('data-initial') || (buttons[0] && buttons[0].dataset.tab);
    if (initial) show(initial);
  });
  const protocolNav = document.querySelector('[data-protocol-nav]');
  if (protocolNav) {
    const items = Array.from(protocolNav.querySelectorAll('[data-protocol-target]'));
    function showProtocol(id) {
      items.forEach(function (item) { item.classList.toggle('is-active', item.getAttribute('data-protocol-target') === id); });
      document.querySelectorAll('.protocol-editor-block').forEach(function (block) { block.hidden = block.id !== id; });
    }
    items.forEach(function (item) {
      item.addEventListener('click', function () { showProtocol(item.getAttribute('data-protocol-target')); });
    });
    if (items[0]) showProtocol(items[0].getAttribute('data-protocol-target'));
  }
  document.querySelectorAll('[data-tab-goto]').forEach(function (button) {
    button.addEventListener('click', function () {
      const target = document.querySelector('[data-tab="' + button.getAttribute('data-tab-goto') + '"]');
      if (target) target.click();
    });
  });
})();
</script>
"""

_SHELL_OPEN = """
<body class="ls-app"{% if api_identity is defined %} data-api-identity="{{ api_identity }}"{% endif %}>
<aside class="ls-sidebar">
  <a class="ls-brand" href="{{ url_for('admin.dashboard') }}" aria-label="LeadSpotting">
    <img class="ls-mark" src="{{ url_for('static', filename='leadspotting-mark.gif') }}" alt="">
    <img class="ls-logo" src="{{ url_for('static', filename='leadspotting-logo.gif') }}" alt="">
  </a>
    <nav class="ls-nav" aria-label="{{ t('admin.menu_title') }}">
    <div class="ls-nav-group">
      <a class="ls-nav-item{% if request.endpoint == 'admin.dashboard' %} is-active{% endif %}" href="{{ url_for('admin.dashboard') }}">""" + _ICON_HOME + """<span class="ls-nav-label">{{ t('admin.menu_all') }}</span></a>
    </div>
    <div class="ls-nav-group">
      <span class="ls-nav-group-label">{{ t('admin.nav_group_management') }}</span>
      <a class="ls-nav-item{% if request.endpoint == 'admin.users' %} is-active{% endif %}" href="{{ url_for('admin.users') }}">""" + _ICON_USERS + """<span class="ls-nav-label">{{ t('admin.menu_users') }}</span></a>
      <a class="ls-nav-item{% if request.endpoint == 'admin.groups' %} is-active{% endif %}" href="{{ url_for('admin.groups') }}">""" + _ICON_GROUPS + """<span class="ls-nav-label">{{ t('admin.menu_groups') }}</span></a>
    </div>
    <div class="ls-nav-group">
      <span class="ls-nav-group-label">{{ t('admin.nav_group_operations') }}</span>
      <a class="ls-nav-item{% if request.endpoint == 'admin.profiles' %} is-active{% endif %}" href="{{ url_for('admin.profiles') }}">""" + _ICON_PROFILES + """<span class="ls-nav-label">{{ t('admin.menu_profiles') }}</span></a>
      <a class="ls-nav-item{% if request.endpoint == 'admin.protocols' %} is-active{% endif %}" href="{{ url_for('admin.protocols') }}">""" + _ICON_PROTOCOLS + """<span class="ls-nav-label">{{ t('admin.menu_protocols') }}</span></a>
      <a class="ls-nav-item{% if request.endpoint == 'admin.events' %} is-active{% endif %}" href="{{ url_for('admin.events') }}">""" + _ICON_EVENTS + """<span class="ls-nav-label">{{ t('admin.menu_events') }}</span></a>
    </div>
    <div class="ls-nav-group">
      <span class="ls-nav-group-label">{{ t('admin.nav_group_system') }}</span>
      <a class="ls-nav-item{% if request.endpoint == 'admin.server' %} is-active{% endif %}" href="{{ url_for('admin.server') }}">""" + _ICON_SERVER + """<span class="ls-nav-label">{{ t('admin.menu_server') }}</span></a>
      <a class="ls-nav-item{% if request.endpoint == 'admin.simulator' %} is-active{% endif %}" href="{{ url_for('admin.simulator') }}">""" + _ICON_SIMULATOR + """<span class="ls-nav-label">{{ t('admin.menu_simulator') }}</span></a>
    </div>
    <div class="ls-nav-group">
      <span class="ls-nav-group-label">{{ t('admin.nav_group_data') }}</span>
      {% for table in admin_tables|default([]) %}
      <a class="ls-nav-item{% if request.view_args and request.view_args.get('table_key') == table.key %} is-active{% endif %}" href="{{ url_for('admin.admin_table_list', table_key=table.key) }}">""" + _ICON_TABLE + """<span class="ls-nav-label">{{ table.label }}</span></a>
      {% endfor %}
    </div>
  </nav>
  <button type="button" class="ls-sidebar-toggle" id="ls-sidebar-toggle" aria-label="{{ t('admin.menu_title') }}">""" + _ICON_TOGGLE + """</button>
</aside>
<div class="ls-main">
  <header class="ls-topbar">
    <a class="ls-btn-fill" href="{{ url_for('admin.dashboard') }}">{{ t('admin.nav_menu') }}</a>
    <span class="ls-status-live"><span class="dot"></span>{{ t('admin.connected') }}</span>
    <div class="ls-topbar-end">
      {% if csrf_token %}
      <form method="post" action="{{ url_for('admin.logout') }}">
        <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
        <button type="submit" class="ls-user-btn">""" + _ICON_PERSON + """<span>{{ t('admin.log_out') }}</span></button>
      </form>
      {% endif %}
    </div>
  </header>
  <div class="ls-content">
"""

_SHELL_CLOSE = """
  </div>
</div>
""" + _SHELL_SCRIPT + """
</body></html>
"""

_MENU_TEMPLATE = """<!DOCTYPE html>
<html lang="{{ lang }}" dir="{{ dir }}"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{{ t('admin.menu_title') }}</title>""" + _BOOTSTRAP_CSS_LINK + _DASHBOARD_STYLE + """
</head>""" + _SHELL_OPEN + """
<div class="ls-page ls-home">
  <div class="ls-page-header">
    <div>
      <h1 class="ls-home-title">{{ t('admin.menu_title') }}</h1>
      <p class="subtitle ls-home-sub">{{ t('admin.menu_subtitle') }}</p>
    </div>
  </div>
  {% for category, message in get_flashed_messages(with_categories=true) %}
    <div class="alert-console{% if category == 'error' %}-error{% endif %} px-3 py-2 mb-4">{{ message }}</div>
  {% endfor %}
  <section class="ls-home-group">
    <h2 class="ls-home-group-title">{{ t('admin.home_group_configuration') }}</h2>
    <div class="ls-service-grid">
      <a class="ls-service-card" href="{{ url_for('admin.users') }}"><span class="ls-service-icon">""" + _ICON_USERS + """</span><h2>{{ t('admin.menu_users') }}</h2><span class="subtitle">{{ t('admin.users_subtitle') }}</span></a>
      <a class="ls-service-card" href="{{ url_for('admin.groups') }}"><span class="ls-service-icon">""" + _ICON_GROUPS + """</span><h2>{{ t('admin.menu_groups') }}</h2><span class="subtitle">{{ t('admin.groups_page_subtitle') }}</span></a>
    </div>
  </section>
  <section class="ls-home-group">
    <h2 class="ls-home-group-title">{{ t('admin.home_group_operations') }}</h2>
    <div class="ls-service-grid">
      <a class="ls-service-card is-featured" href="{{ url_for('admin.profiles') }}"><span class="ls-service-icon">""" + _ICON_PROFILES + """</span><h2>{{ t('admin.menu_profiles') }}</h2><span class="subtitle">{{ t('admin.profiles.subtitle') }}</span></a>
      <a class="ls-service-card is-featured" href="{{ url_for('admin.protocols') }}"><span class="ls-service-icon">""" + _ICON_PROTOCOLS + """</span><h2>{{ t('admin.menu_protocols') }}</h2><span class="subtitle">{{ t('admin.protocols.subtitle') }}</span></a>
      <a class="ls-service-card is-featured" href="{{ url_for('admin.events') }}"><span class="ls-service-icon">""" + _ICON_EVENTS + """</span><h2>{{ t('admin.menu_events') }}</h2><span class="subtitle">{{ t('admin.events.subtitle') }}</span></a>
    </div>
  </section>
  <section class="ls-home-group">
    <h2 class="ls-home-group-title">{{ t('admin.home_group_system') }}</h2>
    <div class="ls-service-grid">
      <a class="ls-service-card is-featured is-featured-navy" href="{{ url_for('admin.server') }}"><span class="ls-service-icon">""" + _ICON_SERVER + """</span><h2>{{ t('admin.menu_server') }}</h2><span class="subtitle">{{ t('admin.server_subtitle') }}</span></a>
      <a class="ls-service-card is-featured is-featured-navy" href="{{ url_for('admin.simulator') }}"><span class="ls-service-icon">""" + _ICON_SIMULATOR + """</span><h2>{{ t('admin.menu_simulator') }}</h2><span class="subtitle">{{ t('admin.simulator.subtitle') }}</span></a>
    </div>
  </section>
  {% if admin_tables %}
  <section class="ls-home-group">
    <h2 class="ls-home-group-title">{{ t('admin.home_group_data') }}</h2>
    <div class="ls-service-grid">
      {% for table in admin_tables %}
      <a class="ls-service-card is-live" href="{{ url_for('admin.admin_table_list', table_key=table.key) }}"><span class="ls-service-icon">""" + _ICON_TABLE + """</span><h2>{{ table.label }}</h2><span class="subtitle">{{ t('admin.tables.menu_subtitle') }}</span></a>
      {% endfor %}
    </div>
  </section>
  {% endif %}
</div>
""" + _SHELL_CLOSE


_BOT_SERVICE_PROVISION_SECTION = """  <section class="ls-section"><div class="block-console"><span class="block-label">{{ t('admin.bot_service_title') }}</span><p class="subtitle">{{ t('admin.bot_service_help', identity=bot_service_identity) }}</p><form method="post" action="{{ url_for('admin.provision_bot_service') }}"><input type="hidden" name="csrf_token" value="{{ csrf_token }}"><button class="btn btn-console">{{ t('admin.bot_service_button') }}</button></form></div></section>"""


_USERS_TEMPLATE = """<!DOCTYPE html>
<html lang="{{ lang }}" dir="{{ dir }}"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{{ t('admin.users_title') }}</title>""" + _BOOTSTRAP_CSS_LINK + _DASHBOARD_STYLE + """
</head>""" + _SHELL_OPEN + """
<div class="ls-page-wide">
  <div class="ls-page-header"><div>
  <h1>{{ t('admin.users_title') }}</h1>
  <p class="subtitle">{{ t('admin.users_subtitle') }}</p>
  </div></div>
  {% for category, message in get_flashed_messages(with_categories=true) %}<div class="alert-console{% if category == 'error' %}-error{% endif %} px-3 py-2 mb-4">{{ message }}</div>{% endfor %}
  <section class="ls-section">
    <div class="ls-table-toolbar"><h2 class="ls-section-title mb-0">{{ t('admin.users.list_heading') }}</h2><span class="ls-count">{{ t('admin.tables.record_count', count=users|length) }}</span></div>
    <div class="table-responsive ls-table-card"><table class="table table-console ls-compact-table mb-0"><thead><tr><th>{{ t('admin.col_identity') }}</th><th>{{ t('admin.col_full_name') }}</th><th>{{ t('admin.col_level') }}</th><th>{{ t('admin.col_status') }}</th><th>{{ t('admin.col_actions') }}</th></tr></thead><tbody>
    {% for user in users %}
    <tr>
      <td class="identity" title="{{ user.telegram_identity }}"><span class="identity-id">{{ user.telegram_identity }}</span>{% if user.telegram_identity == bot_service_identity %} <span class="tag">{{ t('admin.tag_bot_service') }}</span>{% endif %}</td>
      <td><form id="user-save-{{ loop.index }}" method="post" action="{{ url_for('admin.write_user') }}"><input type="hidden" name="csrf_token" value="{{ csrf_token }}"><input type="hidden" name="telegram_identity" value="{{ user.telegram_identity }}"><input type="text" name="full_name" value="{{ user.full_name }}" class="form-control form-control-console" maxlength="120" placeholder="{{ t('admin.col_full_name') }}"></form></td>
      <td><select name="permission_level" form="user-save-{{ loop.index }}" class="form-select form-select-console">{% for level in levels %}<option value="{{ level }}" {% if level == user.permission_level %}selected{% endif %}>{{ level }}</option>{% endfor %}</select></td>
      <td><span class="tag">{% if user.auto_register %}{{ t('admin.registration_automatic') }}{% else %}{{ t('admin.registration_approved') }}{% endif %}</span> <span class="tag">{% if safe_mode and user.auto_register %}{{ t('admin.registration_blocked') }}{% else %}{{ t('admin.registration_active') }}{% endif %}</span></td>
      <td><div class="ls-actions"><button class="btn btn-console btn-sm" form="user-save-{{ loop.index }}">{{ t('admin.save') }}</button>{% if user.auto_register %}<form method="post" action="{{ url_for('admin.approve_user', identity=user.telegram_identity) }}"><input type="hidden" name="csrf_token" value="{{ csrf_token }}"><button class="btn btn-console-primary btn-sm">{{ t('admin.approve_registration') }}</button></form>{% endif %}<form method="post" action="{{ url_for('admin.remove_user', identity=user.telegram_identity) }}" onsubmit="return confirm({{ t('admin.confirm_remove_user', identity=user.telegram_identity)|tojson|forceescape }});"><input type="hidden" name="csrf_token" value="{{ csrf_token }}"><button class="btn btn-console-danger btn-sm">{{ t('admin.remove') }}</button></form></div></td>
    </tr>
    {% endfor %}
    </tbody></table></div>
  </section>
  <section class="ls-section"><div class="block-console ls-create-panel"><span class="block-label">{{ t('admin.add_user') }}</span><form class="row g-3 align-items-end" method="post" action="{{ url_for('admin.write_user') }}"><input type="hidden" name="csrf_token" value="{{ csrf_token }}">
    <div class="col"><div class="form-label-console">{{ t('admin.col_identity') }}</div><input name="telegram_identity" class="form-control form-control-console" required></div>
    <div class="col"><div class="form-label-console">{{ t('admin.col_full_name') }}</div><input name="full_name" class="form-control form-control-console" maxlength="120"></div>
    <div class="col-auto"><div class="form-label-console">{{ t('admin.col_level') }}</div><select name="permission_level" class="form-select form-select-console">{% for level in levels %}<option value="{{ level }}">{{ level }}</option>{% endfor %}</select></div>
    <div class="col-auto"><button class="btn btn-console-primary">{{ t('admin.add') }}</button></div></form></div></section>
""" + _BOT_SERVICE_PROVISION_SECTION + """
</div>
""" + _SHELL_CLOSE


_GROUPS_TEMPLATE = """<!DOCTYPE html>
<html lang="{{ lang }}" dir="{{ dir }}"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>{{ t('admin.groups_title') }}</title>""" + _BOOTSTRAP_CSS_LINK + _DASHBOARD_STYLE + """</head>
""" + _SHELL_OPEN + """
<div class="ls-page-wide"><div class="ls-page-header"><div><h1>{{ t('admin.groups_title') }}</h1><p class="subtitle">{{ t('admin.groups_page_subtitle') }}</p></div></div>
{% for category, message in get_flashed_messages(with_categories=true) %}<div class="alert-console{% if category == 'error' %}-error{% endif %} px-3 py-2 mb-4">{{ message }}</div>{% endfor %}
<section class="ls-section">
  <div class="ls-table-toolbar"><h2 class="ls-section-title mb-0">{{ t('admin.groups.list_heading') }}</h2><span class="ls-count">{{ t('admin.tables.record_count', count=groups|length) }}</span></div>
  <div class="table-responsive ls-table-card"><table class="table table-console ls-compact-table mb-0"><thead><tr><th>{{ t('admin.col_chat_id') }}</th><th>{{ t('admin.col_label') }}</th><th>{{ t('admin.col_routed_to') }}</th><th>{{ t('admin.col_attendance_check') }}</th><th>{{ t('admin.col_attendance_hour') }}</th><th>{{ t('admin.col_status') }}</th><th>{{ t('admin.col_actions') }}</th></tr></thead><tbody>
  {% for group in groups %}
  <tr>
    <td class="identity" title="{{ group.chat_id }}"><span class="identity-id">{{ group.chat_id }}</span></td>
    <td><form id="group-save-{{ loop.index }}" method="post" action="{{ url_for('admin.write_group') }}"><input type="hidden" name="csrf_token" value="{{ csrf_token }}"><input type="hidden" name="chat_id" value="{{ group.chat_id }}"><input name="label" value="{{ group.label }}" maxlength="200" class="form-control form-control-console" placeholder="{{ t('admin.col_label') }}"></form></td>
    <td><select name="agent_name" form="group-save-{{ loop.index }}" class="form-select form-select-console">{% for agent_name in routable_agents %}<option value="{{ agent_name }}" {% if agent_name == group.agent_name %}selected{% endif %}>{{ agent_name }}</option>{% endfor %}</select></td>
    <td><input type="hidden" name="attendance_check_enabled" value="0" form="group-save-{{ loop.index }}"><input type="checkbox" name="attendance_check_enabled" value="1" class="form-check-input" form="group-save-{{ loop.index }}" {% if group.attendance_check_enabled %}checked{% endif %}></td>
    <td><input type="number" name="attendance_check_hour" min="0" max="23" value="{{ group.attendance_check_hour }}" class="form-control form-control-console" form="group-save-{{ loop.index }}"></td>
    <td><span class="tag">{% if group.auto_register %}{{ t('admin.registration_automatic') }}{% else %}{{ t('admin.registration_approved') }}{% endif %}</span> <span class="tag">{% if safe_mode and group.auto_register %}{{ t('admin.registration_blocked') }}{% else %}{{ t('admin.registration_active') }}{% endif %}</span></td>
    <td>
      <div class="ls-actions">
        <button class="btn btn-console btn-sm" form="group-save-{{ loop.index }}">{{ t('admin.save') }}</button>
        {% if group.auto_register %}<form method="post" action="{{ url_for('admin.approve_group', chat_id=group.chat_id) }}"><input type="hidden" name="csrf_token" value="{{ csrf_token }}"><button class="btn btn-console-primary btn-sm">{{ t('admin.approve_registration') }}</button></form>{% endif %}
        <form method="post" action="{{ url_for('admin.remove_group', chat_id=group.chat_id) }}" onsubmit="return confirm({{ t('admin.confirm_remove_group', chat_id=group.chat_id)|tojson|forceescape }});"><input type="hidden" name="csrf_token" value="{{ csrf_token }}"><button class="btn btn-console-danger btn-sm">{{ t('admin.remove') }}</button></form>
      </div>
      <form class="d-flex gap-2 mt-2" method="post" action="{{ url_for('admin.rename_group', chat_id=group.chat_id) }}"><input type="hidden" name="csrf_token" value="{{ csrf_token }}"><input name="new_chat_id" class="form-control form-control-console form-control-sm" placeholder="{{ t('admin.new_chat_id_placeholder') }}" title="{{ t('admin.group_rename_help') }}"><button class="btn btn-console btn-sm" title="{{ t('admin.group_rename_help') }}">{{ t('admin.rename_group') }}</button></form>
    </td>
  </tr>
  {% else %}<tr><td class="ls-empty" colspan="7">{{ t('admin.no_groups') }}</td></tr>{% endfor %}
  </tbody></table></div>
</section>
<section class="ls-section"><div class="block-console ls-create-panel"><span class="block-label">{{ t('admin.add_group') }}</span><p class="subtitle">{{ t('admin.add_group_help', main_agent='main_agent') }}</p><form class="row g-3 align-items-end" method="post" action="{{ url_for('admin.write_group') }}"><input type="hidden" name="csrf_token" value="{{ csrf_token }}"><div class="col"><div class="form-label-console">{{ t('admin.col_chat_id') }}</div><input name="chat_id" class="form-control form-control-console" placeholder="-1001234567890" required></div><div class="col"><div class="form-label-console">{{ t('admin.col_label') }}</div><input name="label" class="form-control form-control-console" maxlength="200"></div><div class="col-auto"><select name="agent_name" class="form-select form-select-console">{% for agent_name in routable_agents %}<option value="{{ agent_name }}">{{ agent_name }}</option>{% endfor %}</select></div><div class="col-auto"><div class="form-check mb-1"><input type="hidden" name="attendance_check_enabled" value="0"><input class="form-check-input" type="checkbox" name="attendance_check_enabled" value="1" id="new-attendance-enabled"><label class="form-check-label" for="new-attendance-enabled">{{ t('admin.col_attendance_check') }}</label></div></div><div class="col-auto"><div class="form-label-console">{{ t('admin.col_attendance_hour') }}</div><input name="attendance_check_hour" type="number" min="0" max="23" value="8" class="form-control form-control-console"></div><div class="col-auto"><button class="btn btn-console-primary">{{ t('admin.add') }}</button></div></form></div></section>
</div>
""" + _SHELL_CLOSE


_PROFILES_TEMPLATE = """<!DOCTYPE html><html lang="{{ lang }}" dir="{{ dir }}"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>{{ t('admin.profiles.title') }}</title>""" + _BOOTSTRAP_CSS_LINK + _DASHBOARD_STYLE + API_CONSOLE_STYLE + """</head>""" + _SHELL_OPEN + PROFILES_BODY + _SHELL_CLOSE


_PROTOCOLS_TEMPLATE = """<!DOCTYPE html><html lang="{{ lang }}" dir="{{ dir }}"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>{{ t('admin.protocols.title') }}</title>""" + _BOOTSTRAP_CSS_LINK + _DASHBOARD_STYLE + API_CONSOLE_STYLE + """</head>""" + _SHELL_OPEN + PROTOCOLS_BODY + _SHELL_CLOSE


_ADMIN_TABLES_LIST_TEMPLATE = """<!DOCTYPE html><html lang="{{ lang }}" dir="{{ dir }}"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>{{ table.label }}</title>""" + _BOOTSTRAP_CSS_LINK + _DASHBOARD_STYLE + """</head>""" + _SHELL_OPEN + ADMIN_TABLES_LIST_BODY + _SHELL_CLOSE


_ADMIN_TABLES_EDIT_TEMPLATE = """<!DOCTYPE html><html lang="{{ lang }}" dir="{{ dir }}"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>{{ table.label }}</title>""" + _BOOTSTRAP_CSS_LINK + _DASHBOARD_STYLE + """</head>""" + _SHELL_OPEN + ADMIN_TABLES_EDIT_BODY + _SHELL_CLOSE


_EVENTS_TEMPLATE = """<!DOCTYPE html><html lang="{{ lang }}" dir="{{ dir }}"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>{{ t('admin.events.title') }}</title>""" + _BOOTSTRAP_CSS_LINK + _DASHBOARD_STYLE + API_CONSOLE_STYLE + """</head>""" + _SHELL_OPEN + EVENTS_BODY + _SHELL_CLOSE


_SERVER_STYLE = """
<style>
  .ls-content:has(.ls-server) { padding: 20px 28px 40px; }
  .ls-server { max-width: 1080px; }
  .ls-server .ls-page-header { margin-bottom: 20px; }
  .ls-server .ls-section { margin-bottom: 28px; }
  .ls-server .block-console { padding: 22px 24px; }
  .ls-server .ls-identity-block { padding: 16px 18px; margin-bottom: 20px; }
  .mode-panel {
    position: relative;
    overflow: hidden;
    border: 1px solid var(--line);
    border-inline-start: 3px solid var(--blue);
    background: var(--panel);
    padding: 28px 28px 24px;
  }
  .mode-panel.open-active { background: var(--panel); }
  .mode-panel.safe-active { background: var(--panel); border-inline-start-color: var(--blue); }
  .mode-layout { display: grid; grid-template-columns: minmax(150px, .62fr) minmax(280px, 1.38fr); gap: 28px; align-items: center; }
  .mode-visual { display: flex; align-items: center; justify-content: center; min-height: 150px; }
  .mode-orbit {
    position: relative;
    width: 116px;
    height: 116px;
    display: grid;
    place-items: center;
    border-radius: 50%;
    border: 1px solid rgba(37, 99, 235, .28);
    background: var(--panel-muted);
  }
  .open-active .mode-orbit { border-color: rgba(132, 204, 22, .45); }
  .safe-active .mode-orbit { border-color: rgba(37, 99, 235, .38); background: #eff6ff; }
  .mode-orbit::before, .mode-orbit::after {
    content: "";
    position: absolute;
    border-radius: 50%;
    border: 1px solid currentColor;
    opacity: .14;
  }
  .mode-orbit::before { inset: 12px; }
  .mode-orbit::after { inset: 27px; }
  .mode-shield {
    width: 48px;
    height: 56px;
    display: grid;
    place-items: center;
    color: var(--lime-text);
    font-family: var(--mono);
    font-weight: 700;
    font-size: 12px;
    background: var(--lime);
    clip-path: polygon(50% 0, 92% 17%, 84% 70%, 50% 100%, 16% 70%, 8% 17%);
  }
  .safe-active .mode-shield { background: var(--blue); color: #fff; }
  .mode-value { font-family: var(--mono); font-size: 12px; color: var(--text-faint); letter-spacing: .02em; direction: ltr; unicode-bidi: isolate; }
  .mode-title { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-bottom: 4px; }
  .mode-title h2 { font-size: 22px; font-weight: 600; color: var(--navy); }
  .mode-state-dot { width: 9px; height: 9px; border-radius: 50%; background: var(--lime); box-shadow: 0 0 0 5px rgba(132, 204, 22, .16); }
  .safe-active .mode-state-dot { background: var(--blue); box-shadow: 0 0 0 5px rgba(37, 99, 235, .16); }
  .mode-actions { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-top: 20px; }
  .mode-choice {
    border: 1px solid var(--line);
    border-radius: var(--radius);
    padding: 16px 18px;
    background: #fff;
    color: var(--navy);
    text-align: start;
    min-height: 88px;
    transition: border-color .15s ease, box-shadow .15s ease, background .15s ease, transform .2s ease;
  }
  .mode-choice:hover { transform: translateY(-2px); border-color: var(--blue); box-shadow: var(--shadow); }
  .mode-choice[data-safe-mode="false"].active {
    border-color: var(--blue);
    background: var(--blue-dim);
  }
  .mode-choice[data-safe-mode="true"].active {
    border-color: var(--line-strong);
    background: var(--panel);
  }
  .mode-choice strong { display: block; margin-bottom: 4px; font-weight: 600; color: var(--navy); }
  .mode-choice small { display: block; color: var(--text-dim); line-height: 1.4; }
  .mode-counts { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 16px; }
  .ls-server .ls-action-card { margin-bottom: 16px; }
  .ls-server .ls-action-card:last-child { margin-bottom: 0; }
  @media (max-width: 700px) { .mode-layout { grid-template-columns: 1fr; gap: 12px; } .mode-visual { min-height: 118px; } .mode-actions { grid-template-columns: 1fr; } }
  @media (prefers-reduced-motion: reduce) { .mode-choice { transition: border-color .15s ease, box-shadow .15s ease, background .15s ease; transform: none; } }
</style>
"""


_SERVER_TEMPLATE = """<!DOCTYPE html>
<html lang="{{ lang }}" dir="{{ dir }}"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>{{ t('admin.server_title') }}</title>""" + _BOOTSTRAP_CSS_LINK + _DASHBOARD_STYLE + API_CONSOLE_STYLE + _SERVER_STYLE + """</head>
""" + _SHELL_OPEN + """
<div class="ls-page ls-server"><div class="ls-page-header"><div><h1>{{ t('admin.server_title') }}</h1>
<p class="subtitle">{{ t('admin.server_subtitle') }}</p></div></div>
{% for category, message in get_flashed_messages(with_categories=true) %}<div class="alert-console{% if category == 'error' %}-error{% endif %} px-3 py-2 mb-4">{{ message }}</div>{% endfor %}
""" + IDENTITY_BAR + """
{% if status.get('last_error') %}<div class="alert-console-error px-3 py-2 mb-4">{{ status.get('last_error') }}</div>{% endif %}
{% if not supervisor %}<div class="alert-console-error px-3 py-2 mb-4">{{ t('admin.server_unavailable') }}</div>{% endif %}
<section class="ls-section">
  <h2 class="ls-section-title">{{ t('admin.server_status_heading') }}</h2>
  <div class="block-console mode-panel {% if safe_mode %}safe-active{% else %}open-active{% endif %}">
    <div class="mode-layout">
      <div class="mode-visual"><div class="mode-orbit" aria-hidden="true"><div class="mode-shield">{% if safe_mode %}SAFE{% else %}OPEN{% endif %}</div></div></div>
      <div>
        <span class="block-label">SAFE_MODE</span>
        <div class="mode-title"><span class="mode-state-dot"></span><h2 class="mb-0">{% if safe_mode %}{{ t('admin.server_safe_on') }}{% else %}{{ t('admin.server_safe_off') }}{% endif %}</h2><span class="mode-value">SAFE_MODE = {{ safe_mode|string|lower }}</span></div>
        <p class="subtitle mt-2 mb-0">{% if safe_mode %}{{ t('admin.server_safe_on_help') }}{% else %}{{ t('admin.server_safe_off_help') }}{% endif %}</p>
        <div class="mode-counts">
          <span class="tag">{% if supervisor %}{{ t('admin.server.connection_ok') }}{% else %}{{ t('admin.server.connection_limited') }}{% endif %}</span>
          <span class="tag">{{ t('admin.server_pending_users', count=automatic_users) }}</span>
          <span class="tag">{{ t('admin.server_pending_groups', count=automatic_groups) }}</span>
        </div>
        <div class="mode-actions" data-confirm="{{ t('admin.server_safe_confirm', users=automatic_users, groups=automatic_groups) }}">
          <button type="button" data-safe-mode="false" class="mode-choice {% if not safe_mode %}active{% endif %}"><strong>{{ t('admin.server_choose_open') }}</strong><small>{{ t('admin.server_choose_open_help') }}</small></button>
          <button type="button" data-safe-mode="true" class="mode-choice {% if safe_mode %}active{% endif %}"><strong>{{ t('admin.server_choose_safe') }}</strong><small>{{ t('admin.server_choose_safe_help') }}</small></button>
        </div>
        <div id="safe-mode-feedback" class="api-hint mt-3" role="status" aria-live="polite"></div>
      </div>
    </div>
  </div>
</section>
<section class="ls-section">
  <h2 class="ls-section-title">{{ t('admin.server_actions_heading') }}</h2>
  <div class="block-console ls-action-card"><span class="block-label">{{ t('admin.server_profile') }}</span>
  <p class="subtitle">{{ t('admin.server_active_profile', profile=active_profile) }}</p>
  <form class="row g-3 align-items-end" method="post" action="{{ url_for('admin.switch_profile') }}"><input type="hidden" name="csrf_token" value="{{ csrf_token }}"><div class="col"><select class="form-select form-select-console" name="profile_module" {% if not supervisor %}disabled{% endif %}>{% for profile in profiles %}<option value="{{ profile.module_path }}" {% if profile.module_path == active_module %}selected{% endif %}>{{ profile.profile_name }} — {{ profile.module_path }} ({{ profile.api_port }})</option>{% endfor %}</select></div><div class="col-auto"><button class="btn btn-console-primary" {% if not supervisor %}disabled{% endif %}>{{ t('admin.server_load_profile') }}</button></div></form>
  <p class="subtitle mt-3 mb-0">{{ t('admin.server_restart_required') }}</p></div>
  <div class="block-console ls-action-card ls-danger-zone"><span class="block-label">{{ t('admin.server_reset') }}</span><p class="subtitle">{{ t('admin.server_reset_help') }}</p><form method="post" action="{{ url_for('admin.reset_server') }}" onsubmit="return confirm({{ t('admin.server_reset_confirm')|tojson|forceescape }});"><input type="hidden" name="csrf_token" value="{{ csrf_token }}"><input type="hidden" name="confirm" value="yes"><button class="btn btn-console-danger" {% if not supervisor %}disabled{% endif %}>{{ t('admin.server_reset_button') }}</button></form></div>
</section>
</div>""" + API_CLIENT_SCRIPT + """
<script>
(() => {
  const controls = document.querySelector('.mode-actions');
  const feedback = document.getElementById('safe-mode-feedback');
  const currentMode = {{ safe_mode|tojson }};
  const changingText = {{ t('admin.server_safe_changing')|tojson }};
  const changedText = {{ t('admin.server_safe_changed')|tojson }};
  const failedText = {{ t('admin.server_safe_change_failed')|tojson }};
  controls.querySelectorAll('[data-safe-mode]').forEach(button => {
    button.addEventListener('click', async () => {
      const requestedMode = button.dataset.safeMode === 'true';
      if (requestedMode === currentMode) return;
      if (requestedMode && !window.confirm(controls.dataset.confirm)) return;
      controls.querySelectorAll('button').forEach(item => { item.disabled = true; });
      feedback.textContent = changingText;
      const result = await AdminApi.call('PUT', '/SYSTEM', {safe_mode: requestedMode}, 'safe-mode-api-output');
      if (result.ok) {
        feedback.textContent = changedText;
        window.setTimeout(() => window.location.reload(), 450);
      } else {
        feedback.textContent = failedText;
        controls.querySelectorAll('button').forEach(item => { item.disabled = false; });
      }
    });
  });
})();
</script>
<pre id="safe-mode-api-output" class="d-none" hidden></pre>
""" + _SHELL_CLOSE


# docs/Admin_Profile_Switch_Investigation.md §1/§4.1: the wait page must never navigate the
# browser to the OLD profile's port -- fetch() with mode:'no-cors' resolves on *any* HTTP
# response (even the old process, seconds from being killed) and only rejects when a port is
# genuinely down, so racing both candidate URLs and following whichever answers first can strand
# the browser on a port that dies moments later. Fixed sequence, never raced: (1) poll `old_url`
# until it stops answering (confirms the old process actually stopped -- also correct when
# `old_url == target_url`, i.e. a same-port reset, since that still requires observing a real
# down-then-up transition before declaring success), (2) only then poll `target_url` until it
# answers, and navigate there -- never anywhere else. A hard deadline shows both URLs as manual
# links instead of spinning or retrying forever if either phase never completes.
_RESTART_POLL_MS = 1500
_RESTART_TIMEOUT_MS = 60000

_SERVER_WAIT_TEMPLATE = """<!DOCTYPE html><html lang="{{ lang }}" dir="{{ dir }}"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>{{ t('admin.server_restarting') }}</title>""" + _BOOTSTRAP_CSS_LINK + _DASHBOARD_STYLE + """</head>""" + _SHELL_OPEN + """<div class="ls-page"><div class="block-console">
<div id="wait-status"><h1>{{ t('admin.server_restarting') }}</h1><p class="subtitle">{{ t('admin.server_restarting_help') }}</p></div>
<div id="wait-timeout" hidden><h1>{{ t('admin.server_restart_timeout_title') }}</h1><p class="subtitle">{{ t('admin.server_restart_timeout_help') }}</p><p><a id="target-link" href="{{ target_url }}">{{ t('admin.server_restart_timeout_target_link') }}</a></p><p><a id="old-link" href="{{ old_url }}">{{ t('admin.server_restart_timeout_previous_link') }}</a></p></div>
</div></div><script>
(function(){
  const oldUrl = {{ old_url|tojson }};
  const targetUrl = {{ target_url|tojson }};
  const deadline = Date.now() + {{ timeout_ms }};
  let phase = 'old-down';
  async function reachable(url) {
    try { await fetch(url, {mode: 'no-cors', credentials: 'include', cache: 'no-store'}); return true; }
    catch (error) { return false; }
  }
  async function tick() {
    if (Date.now() >= deadline) {
      document.getElementById('wait-status').hidden = true;
      document.getElementById('wait-timeout').hidden = false;
      return;
    }
    if (phase === 'old-down') {
      if (!(await reachable(oldUrl))) { phase = 'new-up'; }
    } else if (await reachable(targetUrl)) {
      window.location.href = targetUrl;
      return;
    }
    setTimeout(tick, {{ poll_ms }});
  }
  setTimeout(tick, {{ poll_ms }});
})();
</script>
""" + _SHELL_CLOSE


# The simulator page shares the dashboard's chrome (Bootstrap build, palette, header) and adds
# its own style + body from api/admin_simulator.py; assembled here so one module decides how
# admin pages are put together.
_SIMULATOR_TEMPLATE = """<!DOCTYPE html>
<html lang="{{ lang }}" dir="{{ dir }}">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{{ t('admin.simulator.title') }}</title>
""" + _BOOTSTRAP_CSS_LINK + _DASHBOARD_STYLE + SIMULATOR_STYLE + """
</head>
""" + _SHELL_OPEN + SIMULATOR_BODY + _SHELL_CLOSE
