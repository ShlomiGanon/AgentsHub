"""Shared admin CSS: fonts, Bootstrap, dashboard, login, and server pages."""

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
  .form-control-console:focus, .form-select-console:focus {
    outline: none;
    border-color: var(--blue);
    box-shadow: 0 0 0 0.2rem rgba(37, 99, 235, 0.18);
    background: #fff;
  }
  .field-group { margin-bottom: 14px; }
  .login-card .api-hint { font-size: 13px; color: var(--text-dim); line-height: 1.45; margin: 0 0 16px; }
  .system-admin-row {
    display: flex;
    align-items: flex-start;
    gap: 10px;
    margin: 4px 0 16px;
    font-size: 14px;
    color: var(--text);
  }
  .system-admin-row input { margin-top: 3px; flex: 0 0 auto; }

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

  .identity-card {
    display: flex;
    flex-direction: column;
    box-sizing: border-box;
    min-width: 0;
  }
  .identity-card .subtitle { margin-bottom: 8px; }
  .identity-card > .api-hint { text-align: center; margin: 0 0 16px; }
  .identity-split {
    display: grid;
    grid-template-rows: minmax(0, 1fr) auto minmax(0, 1fr);
    min-height: 292px;
    min-width: 0;
  }
  .identity-pane {
    display: flex;
    flex-direction: column;
    justify-content: center;
    align-items: stretch;
    text-align: center;
    min-width: 0;
    min-height: 0;
    padding-block: 12px;
  }
  .identity-pane .form-select-console {
    width: 100%;
    max-width: 100%;
    min-width: 0;
  }
  .identity-pane .field-group { margin-bottom: 10px; }
  .identity-pane .api-hint { margin-bottom: 12px; }
  .identity-pane h2 {
    font-size: 16px;
    font-weight: 700;
    margin: 0 0 10px;
  }
  .identity-divider {
    height: 1px;
    background: #e2e8f0;
    width: 100%;
  }
  @media (max-width: 768px) {
    .identity-split {
      display: flex;
      flex-direction: column;
      min-height: 0;
    }
    .identity-pane { padding-block: 16px; }
    .identity-pane .btn-console-primary {
      width: 100%;
      min-height: 48px;
    }
  }
</style>
"""

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
