"""Scenario simulator page for the admin panel (`api/admin.py` mounts it at `/admin/simulator`).

The page loads a scenario JSON (file, drag-and-drop, or paste), draws one card per chat or
sensor the scenario declares, and lets the operator release the steps one at a time, in order.

`kind: "event"` (sensor) steps are sent by the **browser** directly to the real `POST /Event`
on the same origin, under that step's own `X-Identity` — a sensor has no Telegram identity and
was never bot/Telegram traffic to begin with (docs/bot_simulation_mode_design.md §2 decision 3),
so this path is unchanged. `kind: "message"` (persona chat) steps instead go through
`POST /admin/simulator/bot-msg` (`api/admin.py`) — a same-origin proxy to a dedicated
simulation-mode bot process (`bot/simulator_app.py`) that feeds the step through the real bot's
own handler/dispatch code (`bot/app.py`, unmodified), so the same registration, permission,
group-scoping *and* real bot behavior (its own derivation of conversation_id/protocol_hint, its
background loops) apply exactly as they would for a real Telegram message — see
docs/bot_simulation_mode_design.md for the full design. If the loaded profile hasn't declared
`SIMULATOR_PORT`, or the simulator process isn't running, the proxy route reports that clearly
rather than silently falling back to a shortcut.

This module holds only the page's style, body and script as constants plus the helper that
gathers the page's data; the route, session check and shared admin chrome stay in `api/admin.py`
(which assembles the full template) so there is one place that knows how admin pages are served.
All user-visible text comes from the `admin.simulator.*` catalog keys (`messages/en.py`,
`messages/he.py`) — the server-rendered parts through the usual `t()` helper, the script's parts
as raw templates embedded in the page and formatted client-side with the same `{name}` syntax.

Scenario JSON shape (documented in docs/unified_command_guide.md):

    {
      "scenario": {"id": "...", "title": "...", "description": "...", "tags": ["..."]},
      "chats": [
        {"key": "response_team", "kind": "message", "label": "...",
         "telegram_chat_id": "-1001234567890", "telegram_chat_type": "supergroup"},
        {"key": "commander_dm", "kind": "message", "label": "...", "telegram_chat_type": "private"},
        {"key": "fence_sensors", "kind": "event", "label": "..."}
      ],
      "steps": [
        {"step": 1, "chat": "response_team", "sender_identity": "1002003", "sender_name": "...",
         "text": "...", "timestamp": "2026-09-06T07:30:00Z"},
        {"step": 2, "chat": "fence_sensors", "sender_identity": "sensor-north-1", "text": "..."},
        {"step": 3, "chat": "commander_dm", "sender_identity": "5551", "text": "...",
         "protocol_hint": "overall_situational_picture", "source_message_id": "4821"}
      ]
    }

`kind: "message"` steps are proxied with `telegram_chat_id`/`telegram_chat_type` exactly as
declared on the chat — `conversation_id`/`protocol_hint` are no longer caller-supplied for this
path, since the real bot handler now derives them itself, exactly as it would for a real Telegram
message (docs/bot_simulation_mode_design.md §10); `kind: "event"` steps still go straight to
`POST /Event`. An edited pending step updates the in-memory queue and the
payload that is actually sent: `text` and `sender_identity` always, and
`timestamp` when set (message-kind steps carry it to the simulation-mode bot
as Telegram `message.date`; event-kind steps send it to `POST /Event` as
`received_at`). `sender_name`, `label`, `title`, `description` and `tags`
remain display-only.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from api.admin_api_pages import FLASH_MESSAGES, IDENTITY_BAR
from api.simulations import simulation_catalog_payload
from messages import MessageCatalog

if TYPE_CHECKING:
    from api.app import ApiContext

SIMULATOR_STRING_PREFIX = "admin.simulator."


def simulator_page_context(
    ctx: "ApiContext", catalog: MessageCatalog, bot_service_identity: str, api_identity: str = ""
) -> dict:
    """Everything the page's script needs, as one JSON-serialisable dict: the live group
    bindings and registered users (so cards can show how a chat will be routed and warn about an
    unregistered sender before the API refuses it), the routable agents, and the raw
    `admin.simulator.*` message templates of the current catalog (formatted client-side).

    `api_identity` is the admin's currently-selected registered identity (the same
    `IDENTITY_BAR`/`api_identity` mechanism the Profiles/Protocols/Events pages already
    use, `api/admin_api_pages.py`) — it gates the server-rendered simulation catalog and
    authenticates the selected scenario's `GET /Simulations/<key>` materialization request;
    it is unrelated to any scenario step's own `sender_identity`, which is always used for
    that step's own request."""

    groups = [
        {"chat_id": binding.chat_id, "agent_name": binding.agent_name, "label": binding.label}
        for binding in ctx.group_routing.all()
    ]
    users = [
        {
            "telegram_identity": user["telegram_identity"],
            "permission_level": user["permission_level"],
            "full_name": user.get("full_name", ""),
        }
        for user in sorted(ctx.deps.persistence.list_users(), key=lambda user: user["telegram_identity"])
    ]
    selected_user = next((user for user in users if user["telegram_identity"] == api_identity), None)
    can_view_simulations = bool(selected_user and selected_user["permission_level"] == "commander")
    profile_simulations = simulation_catalog_payload(ctx.loaded_profile) if can_view_simulations else []
    if not api_identity:
        profile_simulations_hint_key = "admin.simulator.select_identity_first"
    elif not can_view_simulations:
        profile_simulations_hint_key = "admin.simulator.profile_simulations_commander_required"
    elif not profile_simulations:
        profile_simulations_hint_key = "admin.simulator.no_profile_simulations"
    else:
        profile_simulations_hint_key = ""
    strings = {
        key[len(SIMULATOR_STRING_PREFIX):]: template
        for key, template in catalog.messages.items()
        if key.startswith(SIMULATOR_STRING_PREFIX)
    }
    return {
        "groups": groups,
        "users": users,
        "routable_agents": list(ctx.group_routing.routable_targets),
        "bot_service_identity": bot_service_identity,
        "api_identity": api_identity,
        "profile_simulations": profile_simulations,
        "profile_simulations_hint_key": profile_simulations_hint_key,
        "strings": strings,
        "queued_ack_prefixes": [
            catalog.messages[key] for key in ("api.queued_report", "api.queued_request") if key in catalog.messages
        ],
    }


SIMULATOR_STYLE = """
<style>
  .container-wide { max-width: 1400px; }
  .sim-toolbar { display: flex; flex-wrap: wrap; gap: 12px; align-items: stretch; margin-bottom: 20px; }
  .sim-drop {
    flex: 1 1 320px;
    border: 2px dashed var(--line-strong);
    border-radius: 16px;
    padding: 18px;
    text-align: center;
    cursor: pointer;
    background: var(--panel);
    color: var(--text-dim);
    font-size: 14px;
    display: flex; align-items: center; justify-content: center;
  }
  .sim-drop.dragover { border-color: var(--lime); background: #F3FAE8; color: #3F6B12; }
  .sim-paste { flex: 1 1 320px; display: flex; flex-direction: column; gap: 6px; }
  .sim-paste textarea { min-height: 72px; resize: vertical; }
  .sim-actions { display: flex; flex-direction: column; gap: 6px; justify-content: center; }
  .sim-actions .btn { min-width: 170px; }
  .sim-header {
    background: var(--panel);
    border: 1px solid var(--line);
    border-radius: 16px;
    padding: 18px 22px;
    margin-bottom: 20px;
    box-shadow: 0 8px 24px rgba(11, 31, 58, .05);
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
    border-radius: 16px;
    display: flex; flex-direction: column;
    height: 620px;
    transition: border-color 0.2s ease, box-shadow 0.2s ease;
    box-shadow: 0 8px 24px rgba(11, 31, 58, .05);
  }
  .chat-card.active-next { border-color: var(--lime); box-shadow: 0 0 0 3px rgba(140,198,63,.28); }
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

# Body + script. Server-rendered text uses the usual `t()`; the script reads its strings from the
# embedded `sim-data` JSON (see simulator_page_context) and formats them with the same `{name}`
# placeholder syntax the catalog uses.
SIMULATOR_BODY = """
<div class="ls-page-wide">

  <h1 class="mb-1">{{ t('admin.simulator.title') }}</h1>
  <p class="subtitle mb-4">{{ t('admin.simulator.subtitle') }}</p>

  """ + IDENTITY_BAR + FLASH_MESSAGES + """

  <div class="sim-toolbar">
    <div class="sim-drop" id="drop-zone">
      <span>{{ t('admin.simulator.drop_zone') }}</span>
      <input type="file" id="file-input" accept=".json,application/json" style="display:none">
    </div>
    <div class="sim-paste">
      <div class="form-label-console">{{ t('admin.simulator.paste_label') }}</div>
      <textarea id="paste-input" class="form-control form-control-console" spellcheck="false" dir="ltr"></textarea>
      <button type="button" class="btn btn-console btn-sm" id="load-pasted">{{ t('admin.simulator.load_pasted') }}</button>
    </div>
    <div class="sim-actions">
      <button type="button" class="btn btn-console-primary" id="send-next" disabled>{{ t('admin.simulator.send_next') }}</button>
      <button type="button" class="btn btn-console-danger" id="reset-view" disabled>{{ t('admin.simulator.reset_view') }}</button>
      <button type="button" class="btn btn-console" id="toggle-bts" title="פתח גרף ביצוע ותקשורת סוכנים בזמן אמת בחלון נפרד">🔍 {{ t('admin.simulator.bts.toggle_btn') }} (חלון נפרד) ↗</button>
    </div>
    <div class="sim-actions">
      <label class="form-label-console" for="profile-simulation-select">{{ t('admin.simulator.profile_simulations') }}</label>
      <select id="profile-simulation-select" class="form-select form-select-console" {% if not page_data.profile_simulations %}disabled{% endif %}>
        <option value="">{{ t('admin.simulator.choose_profile_simulation') }}</option>
        {% for simulation in page_data.profile_simulations %}
        <option value="{{ simulation.key }}">{{ simulation.title or simulation.key }}</option>
        {% endfor %}
      </select>
      <button type="button" class="btn btn-console-primary" id="load-profile-simulation" {% if not page_data.profile_simulations %}disabled{% endif %}>{{ t('admin.simulator.load_profile_simulation') }}</button>
      <div class="subtitle" id="profile-sim-hint" style="font-size:12px; margin:0;">{% if page_data.profile_simulations_hint_key %}{{ t(page_data.profile_simulations_hint_key) }}{% endif %}</div>
    </div>
  </div>

  <div class="block-console mapping-panel" id="mapping-panel">
    <span class="block-label">{{ t('admin.simulator.mapping_title') }}</span>
    <p class="subtitle">{{ t('admin.simulator.mapping_help') }}</p>
    <div class="mapping-grid" id="mapping-fields"></div>
    <button type="button" class="btn btn-console-primary mt-3" id="apply-mapping">{{ t('admin.simulator.apply_mapping') }}</button>
  </div>

  <div id="sim-alert"></div>

  <div class="sim-header" id="scenario-info">
    <h2 id="scenario-title">{{ t('admin.simulator.no_scenario') }}</h2>
    <p id="scenario-desc" class="description"></p>
    <div class="sim-badges" id="scenario-badges"></div>
    <div class="expected-actions" id="expected-actions"></div>
  </div>

  <div class="sim-grid" id="chats-container"></div>

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
        <span id="bts-status-badge" class="bts-badge bts-badge-pending">○ ממתין</span>
        <span id="bts-active-badge" class="bts-badge-active-count">⚡ 0 בקשות פעילות</span>
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
      <div id="bts-metric-breakdown" class="bts-kpi-sub">מודל: — | כלים: —</div>
    </div>
    <div class="bts-kpi-card">
      <div class="bts-kpi-label">🧠 {{ t('admin.simulator.bts.metric_llm_calls') }}</div>
      <div id="bts-metric-llm" class="bts-kpi-val">—</div>
      <div id="bts-metric-retries" class="bts-kpi-sub">ניסיונות חוזרים: 0</div>
    </div>
    <div class="bts-kpi-card">
      <div class="bts-kpi-label">📊 {{ t('admin.simulator.bts.metric_tokens') }}</div>
      <div id="bts-metric-tokens" class="bts-kpi-val">—</div>
      <div id="bts-metric-tokens-sub" class="bts-kpi-sub">קלט/פלט/מטמון</div>
    </div>
    <div class="bts-kpi-card">
      <div class="bts-kpi-label">👥 סוכנים וכלים שהופעלו</div>
      <div id="bts-metric-agents" class="bts-kpi-val">—</div>
      <div id="bts-metric-agents-sub" class="bts-kpi-sub">ענפים במקביל: 0</div>
    </div>
  </div>

  <!-- Graph Viewport Area -->
  <div class="bts-viewport-container" id="bts-viewport">
    <!-- Graph Legend -->
    <div class="bts-legend-bar">
      <div class="bts-legend-item"><span class="bts-legend-dot" style="background:#38bdf8;"></span> סוכן ראשי</div>
      <div class="bts-legend-item"><span class="bts-legend-dot" style="background:#a855f7;"></span> מומחה</div>
      <div class="bts-legend-item"><span class="bts-legend-dot" style="background:#c084fc; box-shadow:0 0 6px #c084fc;"></span> ⚡ ריצה במקביל</div>
      <div class="bts-legend-item"><span class="bts-legend-dot" style="background:#10b981;"></span> כלי תפעולי</div>
      <div class="bts-legend-item"><span class="bts-legend-dot" style="background:#f59e0b;"></span> מסד נתונים</div>
    </div>

    <!-- Floating Zoom/Pan Controls -->
    <div class="bts-graph-controls">
      <button type="button" class="bts-ctrl-btn" id="bts-zoom-in" title="התקרב (+)">+</button>
      <button type="button" class="bts-ctrl-btn" id="bts-zoom-out" title="התרחק (-)">−</button>
      <button type="button" class="bts-ctrl-btn" id="bts-zoom-fit" title="איפוס מבט ומרכוז">⌖</button>
    </div>

    <!-- SVG Graph -->
    <svg id="bts-graph-svg" class="bts-graph-canvas" xmlns="http://www.w3.org/2000/svg">
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
        <button type="button" id="bts-det-close" class="btn-console-close" title="סגור פרטים">✕</button>
      </div>
      <div class="bts-detail-body">
        <!-- Status & Metrics Section -->
        <div class="bts-detail-section">
          <div class="bts-detail-sec-title">סטטוס ומדדים</div>
          <div class="bts-detail-prop-row">
            <span class="bts-detail-prop-label">סטטוס:</span>
            <span id="bts-det-status" class="bts-badge bts-badge-pending">—</span>
          </div>
          <div class="bts-detail-prop-row">
            <span class="bts-detail-prop-label">זמן ריצה:</span>
            <span id="bts-det-dur" class="bts-detail-prop-val">—</span>
          </div>
          <div class="bts-detail-prop-row">
            <span class="bts-detail-prop-label">הפעלות / קריאות:</span>
            <span id="bts-det-calls" class="bts-detail-prop-val">—</span>
          </div>
          <div class="bts-detail-prop-row">
            <span class="bts-detail-prop-label">ביצוע במקביל:</span>
            <span id="bts-det-parallel" class="bts-detail-prop-val">—</span>
          </div>
          <div id="bts-det-retries-row" class="bts-detail-prop-row" style="display:none;">
            <span class="bts-detail-prop-label">ניסיונות חוזרים:</span>
            <span id="bts-det-retries" class="bts-detail-prop-val" style="color:#f87171;">0</span>
          </div>
        </div>

        <!-- Task / Directives Section -->
        <div class="bts-detail-section">
          <div id="bts-det-task-title" class="bts-detail-sec-title">מה התבקש ממנו</div>
          <div id="bts-det-task-content" style="color:#cbd5e1; white-space:pre-wrap; line-height:1.5;">—</div>
        </div>

        <!-- Tools / Side-Effects Section -->
        <div id="bts-det-tools-section" class="bts-detail-section">
          <div class="bts-detail-sec-title">כלים ואימות תפעולי</div>
          <div id="bts-det-tools-list" class="d-flex flex-column gap-2 mt-1"></div>
        </div>

        <!-- Error / Note Section (if present) -->
        <div id="bts-det-error-section" class="bts-detail-section" style="display:none; border-color:#ef4444; background:rgba(239,68,68,0.08);">
          <div class="bts-detail-sec-title" style="color:#f87171;">פירוט שגיאה או עיכוב</div>
          <div id="bts-det-error-content" style="color:#fca5a5;"></div>
        </div>
      </div>
    </div>
  </div>
</aside>

<script id="sim-data" type="application/json">{{ page_data|tojson }}</script>
<script>
(function () {
  'use strict';

  const DATA = JSON.parse(document.getElementById('sim-data').textContent);
  const STRINGS = DATA.strings || {};
  const QUEUED_ACK_PREFIXES = DATA.queued_ack_prefixes || [];
  const POLL_INTERVAL_MS = 2000;
  const POLL_TIMEOUT_MS = 5 * 60 * 1000;
  const TERMINAL_STATUSES = new Set(['succeeded', 'failed', 'uncertain', 'closed_on_precedent', 'declined']);
  const CHAT_TYPES = new Set(['private', 'group', 'supergroup']);
  const CHAT_KINDS = new Set(['message', 'event']);

  const groupsByChatId = {};
  (DATA.groups || []).forEach(function (group) { groupsByChatId[String(group.chat_id)] = group; });

  // One active pollSimulatorChat() "generation" per chat_id (docs/work_process.md §19) — a
  // second message-kind step sent to the same chat starts a *newer* generation, and any
  // still-running loop from an *earlier* step notices and stops itself immediately, rather
  // than racing that newer step's own poll loop to rediscover its own send/edit exchange
  // (both loops watch the same chat's shared event stream; only the latest step's own loop
  // should ever be reading it at a time).
  //
  // §20 (docs/work_process.md): the generation must be claimed the moment a step *begins*
  // sending — not only once its own POST (including the real, synchronous /Msg round trip:
  // status.thinking send, then an LLM classification call, then the edit to a final ack) has
  // fully returned. Two steps landing closer together than that round trip's own latency
  // (observed live ~8-18s apart, against a ~10s classification call) otherwise leave an older
  // generation free to poll *during* the newer step's still-in-flight send/edit window,
  // rediscovering its un-edited "status.thinking" placeholder as if it were a new arrival.
  // claimPollGeneration() is called synchronously before that POST even starts, so any older
  // generation is superseded (and stops itself, at its own next check) well before the new
  // step's own status message is even sent, let alone edited.
  const pollGenerationByChatId = {};
  const statusBubblesByChatId = {};
  // The simulator's notification stream is global, while follow-up messages can be sent to a
  // reporter's private chat even when the triggering step came from a group. Keep a per-DM
  // watermark so a newer watcher for that same persona can resume without replaying messages.
  const privatePollWatermarksByIdentity = {};

  function copyPollWatermark(mark) {
    return {
      status_len: Number(mark && mark.status_len) || 0,
      sent_len: Number(mark && mark.sent_len) || 0,
    };
  }

  function privatePollWatermark(identity, fallback) {
    const baseline = copyPollWatermark(fallback);
    const previous = privatePollWatermarksByIdentity[identity];
    if (!previous) return baseline;
    return {
      status_len: Math.max(previous.status_len, baseline.status_len),
      sent_len: Math.max(previous.sent_len, baseline.sent_len),
    };
  }

  function claimPollGeneration(chatId) {
    const myGeneration = (pollGenerationByChatId[chatId] || 0) + 1;
    pollGenerationByChatId[chatId] = myGeneration;
    return myGeneration;
  }

  function invalidatePollWatchers() {
    Object.keys(pollGenerationByChatId).forEach(function (chatId) {
      pollGenerationByChatId[chatId] += 1;
    });
    Object.keys(privatePollWatermarksByIdentity).forEach(function (identity) {
      delete privatePollWatermarksByIdentity[identity];
    });
  }
  const registeredIdentities = new Set((DATA.users || []).map(function (user) { return String(user.telegram_identity); }));
  const usersByIdentity = {};
  (DATA.users || []).forEach(function (user) { usersByIdentity[String(user.telegram_identity)] = user; });

  // Catalog-style formatting: the same "{name}" placeholders messages/en.py and messages/he.py use.
  function t(key, values) {
    const template = Object.prototype.hasOwnProperty.call(STRINGS, key) ? STRINGS[key] : key;
    return template.replace(/\\{(\\w+)\\}/g, function (match, name) {
      return values && Object.prototype.hasOwnProperty.call(values, name) ? String(values[name]) : match;
    });
  }

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function formatTimestamp(value) {
    if (!value) return '';
    const date = new Date(value);
    if (isNaN(date.getTime())) return String(value);
    return date.toLocaleString(document.documentElement.lang === 'he' ? 'he-IL' : 'en-GB', {
      day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit',
    });
  }

  function showAlert(message, isError) {
    const box = document.getElementById('sim-alert');
    box.innerHTML = '';
    if (!message) return;
    const node = el('div', 'alert-console' + (isError ? '-error' : '') + ' px-3 py-2 mb-4', message);
    box.appendChild(node);
  }

  // ---- scenario model -------------------------------------------------------------------

  const state = { scenario: null, scenarioSteps: [], chats: [], chatsByKey: {}, queues: {}, runId: null, busy: false };
  // Non-null while #mapping-panel is open for a manually-pasted/uploaded scenario missing IDs
  // (docs/profile_simulations_design.md) — {groupsNeedingId, personaValues}; null when closed.
  let mappingMode = null;

  function closeMappingPanel() {
    document.getElementById('mapping-panel').style.display = 'none';
    document.getElementById('mapping-fields').innerHTML = '';
    mappingMode = null;
  }

  function validateScenario(raw) {
    if (!raw || typeof raw !== 'object') throw new Error(t('err_chats_required'));
    if (!Array.isArray(raw.chats) || raw.chats.length === 0) throw new Error(t('err_chats_required'));
    if (!Array.isArray(raw.steps) || raw.steps.length === 0) throw new Error(t('err_steps_required'));

    const chats = [];
    const chatsByKey = {};
    raw.chats.forEach(function (chat, index) {
      const key = chat && typeof chat.key === 'string' ? chat.key.trim() : '';
      if (!key || chatsByKey[key]) throw new Error(t('err_chat_key', { index: index + 1 }));
      const kind = chat.kind === undefined ? 'message' : String(chat.kind);
      if (!CHAT_KINDS.has(kind)) throw new Error(t('err_chat_kind', { key: key, kind: kind }));
      let chatType = null;
      let chatId = null;
      if (kind === 'message') {
        chatType = chat.telegram_chat_type === undefined || chat.telegram_chat_type === null
          ? 'private' : String(chat.telegram_chat_type);
        if (!CHAT_TYPES.has(chatType)) throw new Error(t('err_chat_type', { key: key, type: chatType }));
        chatId = chat.telegram_chat_id === undefined || chat.telegram_chat_id === null ? null : String(chat.telegram_chat_id);
        if (chatType !== 'private' && !chatId) throw new Error(t('err_chat_id_required', { key: key }));
        if (chatType !== 'private' && (!/^-\\d+$/.test(chatId) || Number(chatId) >= 0)) throw new Error(t('err_chat_id_negative', { key: key }));
      }
      const normalized = {
        key: key,
        kind: kind,
        label: chat.label ? String(chat.label) : key,
        telegram_chat_id: chatId,
        telegram_chat_type: chatType,
      };
      chats.push(normalized);
      chatsByKey[key] = normalized;
    });

    const seenSteps = new Set();
    const steps = raw.steps.map(function (step, index) {
      const number = step ? Number(step.step) : NaN;
      if (!Number.isFinite(number) || seenSteps.has(number)) throw new Error(t('err_step_number', { index: index + 1 }));
      seenSteps.add(number);
      const chatKey = typeof step.chat === 'string' ? step.chat.trim() : '';
      if (!chatsByKey[chatKey]) throw new Error(t('err_step_chat', { step: number, chat: chatKey }));
      const sender = step.sender_identity === undefined || step.sender_identity === null ? '' : String(step.sender_identity).trim();
      if (!/^\\d+$/.test(sender) || Number(sender) <= 0) throw new Error(t('err_step_sender', { step: number }));
      const text = typeof step.text === 'string' ? step.text : '';
      if (!text.trim()) throw new Error(t('err_step_text', { step: number }));
      return {
        step: number,
        chat: chatKey,
        sender_identity: sender,
        sender_name: step.sender_name ? String(step.sender_name) : sender,
        text: text,
        timestamp: step.timestamp || null,
        protocol_hint: step.protocol_hint ? String(step.protocol_hint) : null,
        source_message_id: step.source_message_id !== undefined && step.source_message_id !== null ? String(step.source_message_id) : null,
      };
    });
    steps.sort(function (a, b) { return a.step - b.step; });

    const meta = raw.scenario && typeof raw.scenario === 'object' ? raw.scenario : {};
    return {
      scenario: {
        id: meta.id ? String(meta.id) : '',
        title: meta.title ? String(meta.title) : t('untitled'),
        description: meta.description ? String(meta.description) : '',
        tags: Array.isArray(meta.tags) ? meta.tags.map(String) : [],
        expected_agent_actions: Array.isArray(meta.expected_agent_actions) ? meta.expected_agent_actions : [],
      },
      chats: chats,
      chatsByKey: chatsByKey,
      steps: steps,
    };
  }

  function loadScenario(raw) {
    // A scenario about to load always supersedes any pending mapping prompt — no matter which
    // entry point got us here (paste, drop, or a profile-driven simulation), so no leftover
    // panel from a different path can stay on screen (docs/profile_simulations_design.md).
    closeMappingPanel();
    closeEditDialog();
    invalidatePollWatchers();
    const parsed = validateScenario(raw);
    state.scenario = parsed.scenario;
    state.scenarioSteps = parsed.steps;
    state.chats = parsed.chats;
    state.chatsByKey = parsed.chatsByKey;
    state.queues = {};
    state.busy = false;
    state.runId = Date.now().toString(36);
    parsed.chats.forEach(function (chat) { state.queues[chat.key] = []; });
    parsed.steps.forEach(function (step) { state.queues[step.chat].push(step); });

    document.getElementById('scenario-title').textContent = parsed.scenario.title;
    document.getElementById('scenario-desc').textContent = parsed.scenario.description;
    const badges = document.getElementById('scenario-badges');
    badges.innerHTML = '';
    if (parsed.scenario.id) badges.appendChild(el('span', 'sim-badge', t('badge_id', { id: parsed.scenario.id })));
    badges.appendChild(el('span', 'sim-badge', t('badge_chats', { count: parsed.chats.length })));
    badges.appendChild(el('span', 'sim-badge', t('badge_steps', { count: parsed.steps.length })));
    if (parsed.scenario.expected_agent_actions.length) badges.appendChild(el('span', 'sim-badge', t('badge_expected', { count: parsed.scenario.expected_agent_actions.length })));
    parsed.scenario.tags.forEach(function (tag) { badges.appendChild(el('span', 'sim-badge', tag)); });
    const expectedBox = document.getElementById('expected-actions');
    expectedBox.innerHTML = '';
    if (parsed.scenario.expected_agent_actions.length) {
      expectedBox.appendChild(el('strong', null, t('expected_actions_title')));
      const list = el('ul');
      parsed.scenario.expected_agent_actions.forEach(function (action) {
        list.appendChild(el('li', null, t('expected_action', { step: action.trigger_step || '?', description: action.description || action.action_type || '' })));
      });
      expectedBox.appendChild(list);
    }

    renderCards();
    document.getElementById('reset-view').disabled = false;
    showAlert(t('loaded', { title: parsed.scenario.title }), false);
  }

  // ---- routing / preflight (mirrors what the API will decide, from the embedded live data) --

  function routeInfo(chat) {
    if (chat.kind === 'event') return { text: t('route_sensor'), warn: false };
    if (chat.telegram_chat_type === 'private') return { text: t('route_private'), warn: false };
    const binding = groupsByChatId[chat.telegram_chat_id];
    if (!binding) return { text: t('route_unregistered'), warn: true };
    return { text: t('route_bound', { agent: binding.agent_name }), warn: false };
  }

  function senderWarning(step) {
    if (registeredIdentities.has(step.sender_identity)) return null;
    return t('warn_sender_unregistered', { identity: step.sender_identity });
  }

  // ---- rendering ---------------------------------------------------------------------------

  function renderCards() {
    const container = document.getElementById('chats-container');
    container.innerHTML = '';
    state.chats.forEach(function (chat) {
      const card = el('div', 'chat-card');
      card.id = 'card-' + chat.key;

      const header = el('div', 'chat-header');
      header.appendChild(el('div', 'chat-title', chat.label));
      const meta = chat.kind === 'event'
        ? t('kind_event')
        : t('kind_message') + ' - ' + chat.telegram_chat_type + (chat.telegram_chat_id ? ' - ' + chat.telegram_chat_id : '');
      header.appendChild(el('div', 'chat-meta', meta));
      const route = routeInfo(chat);
      header.appendChild(el('span', 'route-badge' + (route.warn ? ' warn' : ''), route.text));
      card.appendChild(header);

      const messages = el('div', 'chat-messages');
      messages.id = 'messages-' + chat.key;
      card.appendChild(messages);

      const footer = el('div', 'chat-footer');
      const preview = el('div', 'preview-box');
      preview.id = 'preview-' + chat.key;
      footer.appendChild(preview);
      const button = el('button', 'btn btn-console send-btn');
      button.type = 'button';
      button.id = 'btn-' + chat.key;
      button.addEventListener('click', function () { sendNext(chat.key); });
      footer.appendChild(button);
      card.appendChild(footer);

      container.appendChild(card);
    });
    updateGlobalState();
  }

  function nextChatKey() {
    if (state.busy) return null;
    let best = null;
    let bestStep = Infinity;
    Object.keys(state.queues).forEach(function (key) {
      const queue = state.queues[key];
      if (queue.length > 0 && queue[0].step < bestStep) {
        bestStep = queue[0].step;
        best = key;
      }
    });
    return best;
  }

  function updateGlobalState() {
    const globalNext = nextChatKey();
    document.getElementById('send-next').disabled = globalNext === null;

    state.chats.forEach(function (chat) {
      const queue = state.queues[chat.key];
      const card = document.getElementById('card-' + chat.key);
      const preview = document.getElementById('preview-' + chat.key);
      const button = document.getElementById('btn-' + chat.key);
      const isNext = chat.key === globalNext;
      card.classList.toggle('active-next', isNext);
      button.classList.toggle('btn-console-primary', isNext);
      button.classList.toggle('btn-console', !isNext);
      preview.innerHTML = '';

      if (queue.length === 0) {
        const title = el('div', 'preview-title');
        title.appendChild(el('span', null, t('no_more')));
        preview.appendChild(title);
        preview.appendChild(el('div', 'preview-content', t('all_sent')));
        button.textContent = t('no_more');
        button.disabled = true;
        return;
      }

      const step = queue[0];
      const title = el('div', 'preview-title');
      title.appendChild(el('span', null, (isNext ? t('next_in_queue') : t('next_in_chat')) + ' - ' + t('step_label', { step: step.step })));
      const actions = el('div', 'preview-title-actions');
      actions.appendChild(el('span', null, formatTimestamp(step.timestamp)));
      const editBtn = el('button', 'btn btn-console btn-sm');
      editBtn.type = 'button';
      editBtn.textContent = t('edit');
      editBtn.disabled = state.busy;
      editBtn.addEventListener('click', function (event) {
        event.stopPropagation();
        openEditDialog(chat.key);
      });
      actions.appendChild(editBtn);
      title.appendChild(actions);
      preview.appendChild(title);
      const content = el('div', 'preview-content');
      content.dir = 'auto';
      const sender = el('strong', null, step.sender_name + ': ');
      content.appendChild(sender);
      content.appendChild(document.createTextNode(step.text));
      preview.appendChild(content);
      const warning = senderWarning(step);
      if (warning) preview.appendChild(el('div', 'preview-warn', warning));

      button.textContent = isNext ? t('send_this', { step: step.step }) : t('wait_turn', { step: step.step });
      button.disabled = !isNext || state.busy;
    });
  }

  function appendBubble(chatKey, kind, sender, text, stepNumber, timestamp, traceId) {
    const messages = document.getElementById('messages-' + chatKey);
    const bubble = el('div', 'bubble' + (kind ? ' ' + kind : ''));
    if (traceId) bubble.dataset.traceId = traceId;
    const head = el('div', 'bubble-head');
    head.appendChild(el('span', 'sender', sender));
    const headEnd = el('span', 'd-flex align-items-center gap-2');
    headEnd.appendChild(el('span', null, formatTimestamp(timestamp || new Date().toISOString())));
    if (traceId) {
      const traceBtn = el('button', 'btn-trace-link', 'Trace');
      traceBtn.type = 'button';
      traceBtn.title = t('bts.inspect_bubble');
      traceBtn.addEventListener('click', function (e) {
        e.stopPropagation();
        BehindTheScenes.open(traceId);
      });
      headEnd.appendChild(traceBtn);
    }
    head.appendChild(headEnd);
    bubble.appendChild(head);
    const body = el('p', 'bubble-text', text);
    body.dir = 'auto';
    bubble.appendChild(body);
    if (stepNumber !== undefined && stepNumber !== null) bubble.appendChild(el('div', 'bubble-step', t('step_label', { step: stepNumber })));
    messages.appendChild(bubble);
    messages.scrollTop = messages.scrollHeight;
    return bubble;
  }

  function setBubbleText(bubble, text, statusText, isError) {
    const body = bubble.querySelector('.bubble-text');
    body.textContent = text;
    let status = bubble.querySelector('.bubble-status');
    if (statusText) {
      if (!status) { status = el('div', 'bubble-status'); bubble.appendChild(status); }
      status.textContent = statusText;
    } else if (status) {
      status.remove();
    }
    bubble.classList.toggle('err', !!isError);
    const messages = bubble.parentElement;
    if (messages) messages.scrollTop = messages.scrollHeight;
  }

  // ---- dispatch (the bot's own requests, made from the browser) --------------------------

  function applyStepEdit(step, fields, usersLookup) {
    const text = typeof fields.text === 'string' ? fields.text : '';
    if (!text.trim()) throw new Error(t('err_step_text', { step: step.step }));
    const sender = fields.sender_identity === undefined || fields.sender_identity === null ? '' : String(fields.sender_identity).trim();
    if (!/^\\d+$/.test(sender) || Number(sender) <= 0) throw new Error(t('err_step_sender', { step: step.step }));
    let timestamp = null;
    if (fields.timestamp) {
      const parsed = new Date(fields.timestamp);
      if (isNaN(parsed.getTime())) throw new Error(t('err_edit_timestamp'));
      timestamp = parsed.toISOString().replace(/\\.\\d{3}Z$/, 'Z');
    }
    const lookup = usersLookup || {};
    const user = lookup[sender];
    const senderName = user && user.full_name ? String(user.full_name) : sender;
    step.text = text;
    step.sender_identity = sender;
    step.sender_name = senderName;
    step.timestamp = timestamp;
    return step;
  }

  function isoToDatetimeLocal(value) {
    if (!value) return '';
    const date = new Date(value);
    if (isNaN(date.getTime())) return '';
    const pad = function (n) { return String(n).padStart(2, '0'); };
    return date.getFullYear() + '-' + pad(date.getMonth() + 1) + '-' + pad(date.getDate())
      + 'T' + pad(date.getHours()) + ':' + pad(date.getMinutes());
  }

  function senderSelectOptions(currentIdentity) {
    const seen = new Set();
    const options = [];
    (DATA.users || []).forEach(function (user) {
      const identity = String(user.telegram_identity);
      seen.add(identity);
      const name = user.full_name ? String(user.full_name) : identity;
      options.push({ identity: identity, label: identity + ' — ' + name });
    });
    if (currentIdentity && !seen.has(String(currentIdentity))) {
      options.unshift({ identity: String(currentIdentity), label: String(currentIdentity) });
    }
    return options;
  }

  let editingChatKey = null;

  function closeEditDialog() {
    editingChatKey = null;
    const overlay = document.getElementById('edit-step-overlay');
    if (overlay) overlay.hidden = true;
  }

  function openEditDialog(chatKey) {
    if (state.busy) return;
    const queue = state.queues[chatKey];
    if (!queue || queue.length === 0) return;
    const step = queue[0];
    editingChatKey = chatKey;
    document.getElementById('edit-step-title').textContent = t('edit_title', { step: step.step });
    document.getElementById('edit-step-text').value = step.text;
    const select = document.getElementById('edit-step-sender');
    select.innerHTML = '';
    senderSelectOptions(step.sender_identity).forEach(function (option) {
      const node = document.createElement('option');
      node.value = option.identity;
      node.textContent = option.label;
      if (option.identity === step.sender_identity) node.selected = true;
      select.appendChild(node);
    });
    document.getElementById('edit-step-timestamp').value = isoToDatetimeLocal(step.timestamp);
    document.getElementById('edit-step-overlay').hidden = false;
  }

  function savePendingEdit() {
    const chatKey = editingChatKey;
    if (!chatKey || state.busy) return;
    const queue = state.queues[chatKey];
    if (!queue || queue.length === 0) { closeEditDialog(); return; }
    try {
      applyStepEdit(queue[0], {
        text: document.getElementById('edit-step-text').value,
        sender_identity: document.getElementById('edit-step-sender').value,
        timestamp: document.getElementById('edit-step-timestamp').value,
      }, usersByIdentity);
    } catch (error) {
      showAlert(error.message, true);
      return;
    }
    closeEditDialog();
    updateGlobalState();
  }

  function buildRequest(chat, step) {
    const traceId = 'sim-' + state.runId + '-' + step.step + '-' + Date.now().toString(36);
    if (chat.kind === 'event') {
      // Sensors have no Telegram identity and were never bot traffic — unchanged
      // (docs/bot_simulation_mode_design.md §2 decision 3).
      const eventBody = { text: step.text, sender_identity: step.sender_identity };
      if (step.timestamp) eventBody.timestamp = step.timestamp;
      return { url: '/Event', body: eventBody, identity: step.sender_identity, traceId: traceId };
    }
    // Proxied to bot.simulator_app through api/admin.py (docs/bot_simulation_mode_design.md
    // §4.3/§4.4) so the step is fed through the real bot's own handler code, not /Msg
    // directly. `identity: null` — this call authenticates as the admin's own session
    // (cookies), not a per-persona X-Identity header; the persona identity travels inside
    // the body instead, the same way a real Telegram update carries it.
    // Private chats always use the current sender: simulator_app requires
    // chat_id == sender_identity, so an edited identity must not keep the old persona's id.
    const chatId = chat.telegram_chat_type === 'private'
      ? step.sender_identity
      : (chat.telegram_chat_id || step.sender_identity);
    const body = {
      sender_identity: step.sender_identity,
      chat_id: chatId,
      chat_type: chat.telegram_chat_type || 'private',
      text: step.text,
      // A unique id per run unless the scenario pins one — the real bot handler re-derives
      // its own numeric message_id from this string, deterministically, so a re-run with the
      // same id still gets /Msg's existing dedup-on-source_message_id behavior.
      source_message_id: step.source_message_id || ('sim-' + state.runId + '-' + step.step),
      trace_id: traceId,
    };
    if (step.timestamp) body.timestamp = step.timestamp;
    return { url: '/admin/simulator/bot-msg', body: body, identity: null, traceId: traceId };
  }

  async function apiCall(method, url, identity, body, traceId) {
    const headers = { 'Content-Type': 'application/json' };
    if (identity) headers['X-Identity'] = identity;
    if (traceId) headers['X-Trace-ID'] = traceId;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), method === 'POST' ? 85000 : 15000);
    try {
      const response = await fetch(url, {
        method: method,
        headers: headers,
        body: body === undefined ? undefined : JSON.stringify(body),
        credentials: 'same-origin',
        signal: controller.signal,
      });
      let payload = null;
      try { payload = await response.json(); } catch (error) { payload = null; }
      return { status: response.status, payload: payload };
    } finally {
      clearTimeout(timer);
    }
  }

  function errorMessage(result) {
    const payload = result.payload || {};
    const error = payload.error || {};
    return t('request_failed', { status: result.status, message: error.message || JSON.stringify(payload) });
  }

  function jobStatusText(job) {
    const status = job.status;
    switch (status) {
      case 'queued': return t('status_queued');
      case 'running': return t('status_running');
      case 'held_for_clarification': return t('status_held_for_clarification', { field: job.unresolved_field || '' });
      case 'held_for_approval': return t('status_held_for_approval', { reason: job.reason || '' });
      case 'waiting_for_event_data': return t('status_waiting_for_event_data', { fields: (job.missing_fields || []).join(', ') });
      case 'succeeded': return t('status_succeeded');
      case 'failed': return t('status_failed');
      case 'uncertain': return t('status_uncertain');
      case 'closed_on_precedent': return t('status_closed_on_precedent');
      case 'declined': return t('status_declined');
      default: return t('status_other', { status: String(status) });
    }
  }

  function jobBodyText(job) {
    const parts = [];
    if (job.insight_text) parts.push(job.insight_text);
    if (job.question) parts.push(job.question);
    if (job.detail) parts.push(job.detail);
    if (Array.isArray(job.steps_completed) && job.steps_completed.length > 0) {
      parts.push(t('steps_completed') + '\\n' + job.steps_completed.map(function (line) { return '- ' + line; }).join('\\n'));
    }
    return parts.join('\\n\\n');
  }

  async function pollJob(eventId, identity, bubble, header) {
    const startedAt = Date.now();
    while (Date.now() - startedAt < POLL_TIMEOUT_MS) {
      await new Promise(function (resolve) { setTimeout(resolve, POLL_INTERVAL_MS); });
      let result;
      try {
        result = await apiCall('GET', '/Job/' + encodeURIComponent(eventId), identity);
      } catch (error) {
        setBubbleText(bubble, header, t('delivery_unknown'), true);
        return true; // The event may have been persisted; do not offer an implicit replay.
      }
      if (result.status !== 200 || !result.payload) {
        setBubbleText(bubble, header, result.status >= 500 ? t('delivery_unknown') : errorMessage(result), true);
        return true;
      }
      const job = result.payload;
      const body = jobBodyText(job);
      setBubbleText(bubble, body ? header + '\\n\\n' + body : header, jobStatusText(job), job.status === 'failed');
      if (TERMINAL_STATUSES.has(job.status) || job.status === 'held_for_clarification' || job.status === 'held_for_approval' || job.status === 'waiting_for_event_data') return true;
    }
    setBubbleText(bubble, header, t('delivery_unknown'), true);
    return true;
  }

  // Priority 3 (docs/work_process.md §16): watches one chat, in the background, for
  // whatever run_notification_poll_loop's own real, unmodified background delivery
  // eventually sends there (a job result, a held-approval/clarification prompt, ...) —
  // the same real event a real Telegram user would see as a second message. Not
  // awaited by sendNext(): unlike pollJob() (which the operator is explicitly waiting
  // on for one sensor event), most message-kind steps resolve inline immediately, so
  // blocking every step's queue on a multi-minute watch would make stepping through a
  // scenario painfully slow for no benefit — this runs quietly alongside it instead,
  // appending a new bubble only if and when something actually arrives.
  //
  // §19/§20's fix: `myGeneration` was already claimed by claimPollGeneration() before this
  // step's own POST even started (see pollGenerationByChatId's own comment for why it can't
  // wait until here) — this loop just needs to know the generation it's watching for, not
  // claim its own. Checked both before each request (skip a poll entirely once superseded)
  // and after (discard a response that was already in flight when superseded).
  function privateChatKeyForIdentity(identity) {
    const matches = state.chats.filter(function (chat) {
      if (chat.kind !== 'message' || chat.telegram_chat_type !== 'private') return false;
      return state.scenarioSteps.some(function (step) {
        return step.chat === chat.key && step.sender_identity === identity;
      });
    });
    return matches.length === 1 ? matches[0].key : null;
  }

  async function pollSimulatorChat(
    chatKey, chatId, watermark, myGeneration, reply, ackMessageId, traceId,
    deliveryState, privateTargetIdentity, privateFollowupWatcher, privateMessageChatKey
  ) {
    const startedAt = Date.now();
    let mark = watermark || { status_len: 0, sent_len: 0 };
    let pollFailures = 0;
    let lastTraceCheck = 0;
    let terminalSeenAt = null;
    while (Date.now() - startedAt < POLL_TIMEOUT_MS) {
      await new Promise(function (resolve) { setTimeout(resolve, POLL_INTERVAL_MS); });
      if (pollGenerationByChatId[chatId] !== myGeneration) {
        if (deliveryState.waitingForJob && !privateFollowupWatcher) setBubbleText(reply, reply.querySelector('.bubble-text').textContent, t('delivery_unknown'), true);
        return;
      }
      let result;
      try {
        result = await apiCall(
          'GET',
          '/admin/simulator/bot-poll?chat_id=' + encodeURIComponent(chatId) +
            '&status_len=' + encodeURIComponent(mark.status_len) + '&sent_len=' + encodeURIComponent(mark.sent_len),
          null
        );
      } catch (error) {
        pollFailures += 1;
        if (pollFailures >= 3 && deliveryState.waitingForJob && !privateFollowupWatcher) {
          setBubbleText(reply, reply.querySelector('.bubble-text').textContent, t('delivery_unknown'), true);
          return;
        }
        continue;
      }
      if (pollGenerationByChatId[chatId] !== myGeneration) {
        if (deliveryState.waitingForJob && !privateFollowupWatcher) setBubbleText(reply, reply.querySelector('.bubble-text').textContent, t('delivery_unknown'), true);
        return;
      }
      if (result.status !== 200 || !result.payload) {
        pollFailures += 1;
        if (pollFailures >= 3 && deliveryState.waitingForJob && !privateFollowupWatcher) {
          setBubbleText(reply, reply.querySelector('.bubble-text').textContent, t('delivery_unknown'), true);
          return;
        }
        continue;
      }
      pollFailures = 0;
      if (result.payload.watermark) mark = result.payload.watermark;
      if (privateTargetIdentity && result.payload.watermark) {
        privatePollWatermarksByIdentity[privateTargetIdentity] = copyPollWatermark(result.payload.watermark);
      }
      const structured = Array.isArray(result.payload.status_updates) && Array.isArray(result.payload.sent_messages);
      let receivedPrivateMessage = false;
      if (structured) {
        for (const update of result.payload.status_updates) {
          if (privateFollowupWatcher && (update.kind === 'send' || update.kind === 'edit')) {
            if (!statusBubblesByChatId[chatId]) statusBubblesByChatId[chatId] = new Map();
            const privateStatusBubbles = statusBubblesByChatId[chatId];
            const knownPrivateBubble = privateStatusBubbles.get(String(update.message_id));
            if (update.kind === 'edit' && knownPrivateBubble) {
              setBubbleText(knownPrivateBubble, update.text, null, false);
            } else {
              const bubble = appendBubble(
                privateMessageChatKey || chatKey,
                'sys',
                t('private_followup_label', { identity: privateTargetIdentity }),
                update.text,
                null,
                null,
                traceId
              );
              privateStatusBubbles.set(String(update.message_id), bubble);
            }
            receivedPrivateMessage = true;
            continue;
          }
          const knownBubble = statusBubblesByChatId[chatId] && statusBubblesByChatId[chatId].get(String(update.message_id));
          if (update.kind === 'edit' && knownBubble) {
            setBubbleText(knownBubble, update.text, null, false);
            if (String(update.message_id) === String(ackMessageId)) deliveryState.waitingForJob = false;
          } else if (update.kind === 'send' || update.kind === 'edit') {
            appendBubble(chatKey, 'sys', t('system_label'), update.text, null);
            if (privateTargetIdentity) receivedPrivateMessage = true;
          }
        }
        for (const text of result.payload.sent_messages) {
          appendBubble(
            privateFollowupWatcher ? (privateMessageChatKey || chatKey) : chatKey,
            'sys',
            privateFollowupWatcher ? t('private_followup_label', { identity: privateTargetIdentity }) : t('system_label'),
            text,
            null,
            null,
            privateFollowupWatcher ? traceId : null
          );
          if (privateTargetIdentity) receivedPrivateMessage = true;
        }
      } else if (result.payload.reply_text) {
        if (deliveryState.waitingForJob && !privateFollowupWatcher) {
          setBubbleText(reply, result.payload.reply_text, null, false);
        } else {
          appendBubble(
            privateFollowupWatcher ? (privateMessageChatKey || chatKey) : chatKey,
            'sys',
            privateFollowupWatcher ? t('private_followup_label', { identity: privateTargetIdentity }) : t('system_label'),
            result.payload.reply_text,
            null,
            null,
            privateFollowupWatcher ? traceId : null
          );
          if (privateFollowupWatcher) receivedPrivateMessage = true;
        }
      }
      if (receivedPrivateMessage && deliveryState.waitingForJob) {
        setBubbleText(
          reply,
          t('private_followup_waiting', { identity: privateTargetIdentity }),
          t('private_followup_status'),
          false
        );
        deliveryState.waitingForJob = false;
      }
      if (deliveryState.waitingForJob && Date.now() - lastTraceCheck >= 8000) {
        lastTraceCheck = Date.now();
        try {
          const traceResult = await apiCall('GET', '/admin/simulator/trace/' + encodeURIComponent(traceId), null);
          if (traceResult.status === 200 && traceResult.payload) {
            if (traceResult.payload.diagnostic_state === 'job_stopped_without_outcome') {
              setBubbleText(reply, reply.querySelector('.bubble-text').textContent, t('job_stopped'), true);
              return;
            }
            if (traceResult.payload.terminal) {
              if (terminalSeenAt === null) terminalSeenAt = Date.now();
              setBubbleText(reply, reply.querySelector('.bubble-text').textContent,
                t('job_done_no_delivery', { outcome: traceResult.payload.outcome || 'unknown' }), false);
              if (Date.now() - terminalSeenAt > 15000) {
                setBubbleText(reply, reply.querySelector('.bubble-text').textContent, t('delivery_unknown'), true);
                return;
              }
              continue;
            }
            const stages = traceResult.payload.stages || [];
            const active = stages.filter(stage => stage.status === 'running').at(-1);
            if (active) {
              setBubbleText(reply, reply.querySelector('.bubble-text').textContent,
                t('waiting_stage', { seconds: Math.floor((Date.now() - startedAt) / 1000), stage: active.name }), false);
            }
          }
        } catch (error) { /* Trace display is diagnostic only. */ }
      }
    }
    if (deliveryState.waitingForJob && !privateFollowupWatcher) setBubbleText(reply, reply.querySelector('.bubble-text').textContent, t('delivery_unknown'), true);
  }

  async function sendNext(chatKey) {
    const queue = state.queues[chatKey];
    if (!queue || queue.length === 0 || state.busy || nextChatKey() !== chatKey) return;
    const chat = state.chatsByKey[chatKey];
    const step = queue[0];
    state.busy = true;
    closeEditDialog();
    updateGlobalState();

    const request = buildRequest(chat, step);
    const traceId = request.traceId;

    // Immediately notify Behind-The-Scenes so real-time tracking can begin while in flight
    BehindTheScenes.track(traceId, {
      title: step.text.length > 40 ? step.text.slice(0, 40) + '...' : step.text,
      sender: step.sender_name,
      step: step.step,
      chatKey: chatKey,
    });

    appendBubble(chatKey, null, step.sender_name, step.text, step.step, step.timestamp, traceId);
    const reply = appendBubble(chatKey, 'sys', t('system_label'), t('sending'), null, null, traceId);

    // §20: claimed here, synchronously, before the POST (and its own real send/edit cycle)
    // even starts — see pollGenerationByChatId's own comment for why. Event-kind steps never
    // poll a chat (they use pollJob against an event_id instead), so this is skipped for them.
    const myGeneration = chat.kind === 'event' ? null : claimPollGeneration(request.body.chat_id);
    let result;
    try {
      result = await apiCall('POST', request.url, request.identity, request.body, traceId);
    } catch (error) {
      setBubbleText(reply, t('delivery_unknown'), t('network_error', { message: error.message }), true);
      queue.shift(); // Outcome may already be persisted; never make an implicit replay the next step.
      state.busy = false;
      updateGlobalState();
      return;
    }
    if (result.status >= 400 || !result.payload) {
      setBubbleText(reply, result.status >= 500 ? t('delivery_unknown') : errorMessage(result), null, true);
      queue.shift();
      state.busy = false;
      updateGlobalState();
      return;
    }

    const payload = result.payload;
    const finalTraceId = payload.trace_id || traceId;
    BehindTheScenes.onStepComplete(finalTraceId);

    if (chat.kind === 'event') {
      const header = t('event_id', { event_id: payload.event_id });
      setBubbleText(reply, header, jobStatusText({ status: payload.status }), false);
      const completed = await pollJob(payload.event_id, step.sender_identity, reply, header);
      if (completed) queue.shift();
      state.busy = false;
      updateGlobalState();
      return;
    }

    // The bot-msg path (docs/bot_simulation_mode_design.md §4.3/§10): the real bot handler
    // ran and sent its reply through the simulator process's stub Telegram client, exactly
    // as it would send a real Telegram message — `reply_text` is that rendered text, not a
    // reconstruction of /Msg's richer {taken_as, event_id, status} shape (which is only ever
    // observed from *outside* the handler, not returned by it). An async job's eventual
    // outcome is delivered later, out-of-band, by the same background notification loop a
    // real bot uses — pollSimulatorChat() (Priority 3) watches for it quietly in the
    // background, without blocking this step's queue.
    setBubbleText(reply, payload.reply_text || t('bot_no_reply'), null, false);
    queue.shift();
    state.busy = false;
    updateGlobalState();
    const ackEvent = (payload.status_updates || []).find(event => event.kind === 'send');
    if (ackEvent) {
      if (!statusBubblesByChatId[request.body.chat_id]) statusBubblesByChatId[request.body.chat_id] = new Map();
      statusBubblesByChatId[request.body.chat_id].set(String(ackEvent.message_id), reply);
    }
    const waitingForJob = QUEUED_ACK_PREFIXES.some(prefix => (payload.reply_text || '').startsWith(prefix));
    const deliveryState = { waitingForJob: waitingForJob };
    const senderIdentity = String(request.body.sender_identity || '');
    const isPrivateOrigin = request.body.chat_type === 'private';
    const originWatermark = payload.watermark;
    pollSimulatorChat(
      chatKey,
      request.body.chat_id,
      originWatermark,
      myGeneration,
      reply,
      ackEvent && ackEvent.message_id,
      finalTraceId,
      deliveryState,
      null,
      false,
      chatKey
    );
    if (waitingForJob && !isPrivateOrigin && senderIdentity && senderIdentity !== String(request.body.chat_id || '')) {
      const privateGeneration = claimPollGeneration(senderIdentity);
      pollSimulatorChat(
        chatKey,
        senderIdentity,
        privatePollWatermark(senderIdentity, payload.request_watermark || payload.watermark),
        privateGeneration,
        reply,
        null,
        finalTraceId,
        deliveryState,
        senderIdentity,
        true,
        privateChatKeyForIdentity(senderIdentity)
      );
    }
  }

  // ---- mapping panel: prompts for any Telegram ID a manually-provided scenario is missing ----

  function mappingInput(kind, key, label, listId) {
    const wrapper = el('div', 'mapping-field');
    const caption = el('label', null, label);
    const input = el('input', 'form-control form-control-console');
    input.type = 'number'; input.dataset.kind = kind; input.dataset.key = key; input.setAttribute('list', listId);
    input.placeholder = kind === 'person' ? t('telegram_id_placeholder') : t('chat_id_placeholder');
    wrapper.appendChild(caption); wrapper.appendChild(input);
    return wrapper;
  }

  // `groupsNeedingId`: [{key, label}] — one row per chat still needing a real telegram_chat_id,
  // keyed by the chat's own (always-unique) `key`. `personaValues`: [string] — one row per
  // distinct placeholder value found in sender_identity, keyed by that value itself, so the same
  // placeholder reused across several steps (e.g. the same person reporting more than once) gets
  // exactly one input.
  function renderMappingRows(groupsNeedingId, personaValues) {
    const fields = document.getElementById('mapping-fields');
    fields.innerHTML = '';
    const usersList = el('datalist'); usersList.id = 'registered-user-ids';
    (DATA.users || []).filter(function (user) { return String(user.telegram_identity) !== DATA.bot_service_identity; }).forEach(function (user) {
      const option = el('option'); option.value = String(user.telegram_identity); option.label = user.full_name || t('missing_name'); usersList.appendChild(option);
    });
    const groupsList = el('datalist'); groupsList.id = 'registered-group-ids';
    (DATA.groups || []).forEach(function (group) { const option = el('option'); option.value = String(group.chat_id); option.label = group.label || group.agent_name; groupsList.appendChild(option); });
    fields.appendChild(usersList); fields.appendChild(groupsList);
    personaValues.forEach(function (persona) { fields.appendChild(mappingInput('person', persona, t('map_person', { persona: persona }), usersList.id)); });
    groupsNeedingId.forEach(function (group) { fields.appendChild(mappingInput('group', group.key, t('map_group', { group: group.label }), groupsList.id)); });
  }

  function collectMappingValues(groupsNeedingId, personaValues) {
    const personaIds = {}, groupIds = {};
    document.querySelectorAll('#mapping-fields input').forEach(function (input) {
      if (input.dataset.kind === 'person') personaIds[input.dataset.key] = input.value.trim();
      else groupIds[input.dataset.key] = input.value.trim();
    });
    personaValues.forEach(function (persona) {
      if (!/^\\d+$/.test(personaIds[persona] || '') || Number(personaIds[persona]) <= 0) throw new Error(t('err_positive_identity', { persona: persona }));
    });
    groupsNeedingId.forEach(function (group) {
      if (!/^-\\d+$/.test(groupIds[group.key] || '') || Number(groupIds[group.key]) >= 0) throw new Error(t('err_negative_group', { group: group.label }));
    });
    return { personaIds: personaIds, groupIds: groupIds };
  }

  // ---- generic manual-JSON mapping: any pasted/uploaded (or, defensively, profile-driven) ----
  // scenario missing a Telegram ID — works off whatever validateScenario() would otherwise
  // reject. A step whose sender_identity is completely empty stays a hard validation error
  // (there is no placeholder name to label an input with); only a *non-empty-but-invalid*
  // placeholder (e.g. a profile scenario's own persona/group key, "viewer", "team", ...) is
  // offered a mapping row.

  function collectMissingIdentifiers(raw) {
    const groupsNeedingId = [];
    const personaValues = [];
    if (!raw || !Array.isArray(raw.chats) || !Array.isArray(raw.steps)) {
      return { groupsNeedingId: groupsNeedingId, personaValues: personaValues };
    }
    raw.chats.forEach(function (chat) {
      if (!chat || typeof chat.key !== 'string') return;
      const kind = chat.kind === undefined ? 'message' : String(chat.kind);
      if (kind !== 'message') return;
      const chatType = chat.telegram_chat_type === undefined || chat.telegram_chat_type === null ? 'private' : String(chat.telegram_chat_type);
      if (chatType !== 'group' && chatType !== 'supergroup') return;
      const chatId = chat.telegram_chat_id === undefined || chat.telegram_chat_id === null ? '' : String(chat.telegram_chat_id).trim();
      if (!/^-\\d+$/.test(chatId) || Number(chatId) >= 0) {
        groupsNeedingId.push({ key: chat.key, label: chat.label ? String(chat.label) : chat.key });
      }
    });
    raw.steps.forEach(function (step) {
      if (!step) return;
      const sender = step.sender_identity === undefined || step.sender_identity === null ? '' : String(step.sender_identity).trim();
      if (sender && (!/^\\d+$/.test(sender) || Number(sender) <= 0) && personaValues.indexOf(sender) === -1) {
        personaValues.push(sender);
      }
    });
    return { groupsNeedingId: groupsNeedingId, personaValues: personaValues };
  }

  // Deep-clones `raw` (never mutates the caller's object — e.g. the exact text still sitting in
  // the paste box) and substitutes the mapped real IDs: groups by the chat's own key, personas by
  // the original placeholder string value, everywhere it appears.
  function applyManualMapping(raw, mapping) {
    const substituted = JSON.parse(JSON.stringify(raw));
    (substituted.chats || []).forEach(function (chat) {
      if (chat && typeof chat.key === 'string' && Object.prototype.hasOwnProperty.call(mapping.groupIds, chat.key)) {
        chat.telegram_chat_id = mapping.groupIds[chat.key];
      }
    });
    (substituted.steps || []).forEach(function (step) {
      if (!step) return;
      const sender = step.sender_identity === undefined || step.sender_identity === null ? '' : String(step.sender_identity).trim();
      if (Object.prototype.hasOwnProperty.call(mapping.personaIds, sender)) {
        step.sender_identity = mapping.personaIds[sender];
      }
    });
    return substituted;
  }

  function offerManualMapping(raw, groupsNeedingId, personaValues) {
    renderMappingRows(groupsNeedingId, personaValues);
    mappingMode = { type: 'manual', raw: raw, groupsNeedingId: groupsNeedingId, personaValues: personaValues };
    document.getElementById('mapping-panel').style.display = 'block';
  }

  // The one entry point every "I have a raw scenario object, load it" path funnels through:
  // paste, drop, and the profile-driven Load button (defensively — a materialized simulation is
  // never expected to have anything left to map, but this costs nothing and keeps every path on
  // one rule). Offers the mapping panel only when something is actually missing, so a
  // fully-specified scenario never sees an extra click.
  function loadRawScenario(raw) {
    const missing = collectMissingIdentifiers(raw);
    if (missing.groupsNeedingId.length || missing.personaValues.length) {
      offerManualMapping(raw, missing.groupsNeedingId, missing.personaValues);
      return;
    }
    try {
      loadScenario(raw);
    } catch (error) {
      showAlert(error.message, true);
    }
  }

  // ---- profile-declared simulations: server-rendered catalog, no manual ID entry ----------
  // The catalog is rendered from the already-loaded profile and is exposed only for an acting
  // commander. Selecting an entry still fetches its canonical materialized scenario from
  // GET /Simulations/<key>; the browser never constructs simulation Telegram IDs.

  const profileSimSelect = document.getElementById('profile-simulation-select');
  const profileSimLoadButton = document.getElementById('load-profile-simulation');

  profileSimLoadButton.addEventListener('click', async function () {
    const key = profileSimSelect.value;
    if (!key) return;
    profileSimLoadButton.disabled = true;
    try {
      const result = await apiCall('GET', '/Simulations/' + encodeURIComponent(key), DATA.api_identity);
      if (result.status >= 400 || !result.payload) {
        showAlert(t('profile_simulation_load_failed', { message: errorMessage(result) }), true);
        return;
      }
      // Always the already-materialized shape (reserved IDs already embedded server-side) — routed
      // through loadRawScenario purely as defense-in-depth, on the same one rule every other entry
      // point uses; a real profile simulation is never expected to have anything left to map.
      loadRawScenario(result.payload);
    } catch (error) {
      showAlert(t('profile_simulation_load_failed', { message: error.message }), true);
    } finally {
      profileSimLoadButton.disabled = false;
    }
  });

  // ---- wiring --------------------------------------------------------------------------------

  function loadFromText(text) {
    let raw;
    try {
      raw = JSON.parse(text);
    } catch (error) {
      showAlert(t('err_parse', { message: error.message }), true);
      return;
    }
    loadRawScenario(raw);
  }

  function loadFromFile(file) {
    const reader = new FileReader();
    reader.onload = function (event) { loadFromText(String(event.target.result)); };
    reader.readAsText(file);
  }

  const dropZone = document.getElementById('drop-zone');
  const fileInput = document.getElementById('file-input');
  dropZone.addEventListener('click', function () { fileInput.click(); });
  fileInput.addEventListener('click', function (event) { event.stopPropagation(); });  // the input sits inside the drop zone
  fileInput.addEventListener('change', function (event) {
    if (event.target.files && event.target.files[0]) loadFromFile(event.target.files[0]);
    fileInput.value = '';
  });
  dropZone.addEventListener('dragover', function (event) { event.preventDefault(); dropZone.classList.add('dragover'); });
  dropZone.addEventListener('dragleave', function () { dropZone.classList.remove('dragover'); });
  dropZone.addEventListener('drop', function (event) {
    event.preventDefault();
    dropZone.classList.remove('dragover');
    if (event.dataTransfer.files && event.dataTransfer.files.length > 0) loadFromFile(event.dataTransfer.files[0]);
  });

  // ---- Live Agent Execution Graph (Behind-the-Scenes) --------------------------------
  const BehindTheScenes = (function () {
    let isOpen = false;
    let currentTraceId = null;
    let pollTimer = null;
    let pollFailures = 0;
    let traceFetchInFlight = false;
    const POLL_BTS_INTERVAL_MS = 1000;

    let selectedNodeId = null;
    let currentGraphData = null;
    let currentMetrics = null;

    // Pan & Zoom state
    const transform = { x: 0, y: 0, scale: 1 };
    let isPanning = false;
    let startPanX = 0;
    let startPanY = 0;

    // DOM Elements
    const drawer = document.getElementById('bts-drawer');
    const overlay = document.getElementById('bts-overlay');
    const statusBadge = document.getElementById('bts-status-badge');
    const activeBadge = document.getElementById('bts-active-badge');
    const traceIdLabel = document.getElementById('bts-trace-id-label');
    const recentSelect = document.getElementById('bts-recent-select');

    // Metrics elements
    const metricWall = document.getElementById('bts-metric-wall');
    const metricBreakdown = document.getElementById('bts-metric-breakdown');
    const metricLlm = document.getElementById('bts-metric-llm');
    const metricRetries = document.getElementById('bts-metric-retries');
    const metricTokens = document.getElementById('bts-metric-tokens');
    const metricTokensSub = document.getElementById('bts-metric-tokens-sub');
    const metricAgents = document.getElementById('bts-metric-agents');
    const metricAgentsSub = document.getElementById('bts-metric-agents-sub');

    // Graph elements
    const viewport = document.getElementById('bts-viewport');
    const svg = document.getElementById('bts-graph-svg');
    const sceneGroup = document.getElementById('bts-graph-scene');
    const edgesLayer = document.getElementById('bts-edges-layer');
    const nodesLayer = document.getElementById('bts-nodes-layer');

    // Detail Flyout elements
    const detailPanel = document.getElementById('bts-node-detail');
    const detIcon = document.getElementById('bts-det-icon');
    const detTitle = document.getElementById('bts-det-title');
    const detSub = document.getElementById('bts-det-sub');
    const detCloseBtn = document.getElementById('bts-det-close');
    const detStatus = document.getElementById('bts-det-status');
    const detDur = document.getElementById('bts-det-dur');
    const detCalls = document.getElementById('bts-det-calls');
    const detParallel = document.getElementById('bts-det-parallel');
    const detRetriesRow = document.getElementById('bts-det-retries-row');
    const detRetries = document.getElementById('bts-det-retries');
    const detTaskTitle = document.getElementById('bts-det-task-title');
    const detTaskContent = document.getElementById('bts-det-task-content');
    const detToolsSection = document.getElementById('bts-det-tools-section');
    const detToolsList = document.getElementById('bts-det-tools-list');
    const detErrorSection = document.getElementById('bts-det-error-section');
    const detErrorContent = document.getElementById('bts-det-error-content');

    function applyTransform() {
      if (sceneGroup) {
        sceneGroup.setAttribute('transform', 'translate(' + transform.x + ', ' + transform.y + ') scale(' + transform.scale + ')');
      }
    }

    function zoomBy(factor) {
      const rect = svg.getBoundingClientRect();
      const cx = rect.width / 2;
      const cy = rect.height / 2;
      const newScale = Math.max(0.35, Math.min(2.5, transform.scale * factor));
      transform.x = cx - (cx - transform.x) * (newScale / transform.scale);
      transform.y = cy - (cy - transform.y) * (newScale / transform.scale);
      transform.scale = newScale;
      applyTransform();
    }

    function resetZoomFit() {
      transform.x = 0;
      transform.y = 0;
      transform.scale = 1;
      applyTransform();
    }

    function stopPolling() {
      if (pollTimer) {
        clearInterval(pollTimer);
        pollTimer = null;
      }
    }

    function open(traceId) {
      isOpen = true;
      if (drawer) drawer.style.display = 'flex';
      if (overlay) overlay.style.display = 'block';
      loadRecentTraces();
      if (traceId) {
        currentTraceId = traceId;
        renderHeader(traceId, 'running');
        startPolling();
      } else if (currentTraceId) {
        startPolling();
      } else {
        renderEmpty();
      }
    }

    function close() {
      isOpen = false;
      stopPolling();
      if (drawer) drawer.style.display = 'none';
      if (overlay) overlay.style.display = 'none';
      hideNodeDetail();
    }

    function openSeparateWindow() {
      const trace = currentTraceId || '';
      const url = '/admin/simulator/behind-the-scenes' + (trace ? '?trace_id=' + encodeURIComponent(trace) : '');
      const win = window.open(url, 'AgentsHubBehindTheScenes', 'width=1520,height=940,menubar=no,toolbar=no,location=no,status=no,resizable=yes,scrollbars=yes');
      if (win) {
        win.focus();
      }
    }

    function toggle() {
      openSeparateWindow();
    }

    function track(traceId) {
      if (!traceId) return;
      currentTraceId = traceId;
      if (traceIdLabel) traceIdLabel.textContent = traceId;

      // Broadcast to separate window in real-time
      if (window.BroadcastChannel) {
        try {
          const ch = new BroadcastChannel('agentshub_trace_sync');
          ch.postMessage({ traceId: traceId, timestamp: Date.now() });
        } catch (e) {}
      }
      try {
        localStorage.setItem('agentshub_active_trace', JSON.stringify({ traceId: traceId, timestamp: Date.now() }));
      } catch (e) {}

      if (isOpen) {
        renderHeader(traceId, 'running');
        startPolling();
      }
    }

    function onStepComplete(traceId) {
      if (currentTraceId === traceId && isOpen) {
        fetchTrace();
      }
      loadRecentTraces();
    }

    function reset() {
      stopPolling();
      currentTraceId = null;
      currentGraphData = null;
      selectedNodeId = null;
      renderEmpty();
    }

    function renderEmpty() {
      renderHeader(null, 'pending');
      if (metricWall) metricWall.textContent = '—';
      if (metricBreakdown) metricBreakdown.textContent = 'מודל: — | כלים: —';
      if (metricLlm) metricLlm.textContent = '—';
      if (metricRetries) metricRetries.textContent = 'ניסיונות חוזרים: 0';
      if (metricTokens) metricTokens.textContent = '—';
      if (metricTokensSub) metricTokensSub.textContent = t('bts.na');
      if (metricAgents) metricAgents.textContent = '—';
      if (metricAgentsSub) metricAgentsSub.textContent = 'ענפים במקביל: 0';
      if (edgesLayer) edgesLayer.innerHTML = '';
      if (nodesLayer) {
        nodesLayer.innerHTML = '<text x="500" y="240" fill="#64748b" font-size="14" text-anchor="middle" font-family="sans-serif">ממתין לבקשת סימולציה... שלח הודעה או בחר בקשה מרשימת ה-Traces</text>';
      }
      hideNodeDetail();
    }

    function renderHeader(traceId, status, deliveryStatus) {
      if (traceIdLabel) traceIdLabel.textContent = traceId || '—';
      if (!statusBadge) return;
      if (status === 'running') {
        statusBadge.className = 'bts-badge bts-badge-live';
        statusBadge.textContent = '● ' + t('bts.live_badge');
      } else if (status === 'awaiting_approval') {
        statusBadge.className = 'bts-badge bts-badge-pending';
        statusBadge.textContent = '⏳ ממתין לאישור';
      } else if (status === 'partial') {
        statusBadge.className = 'bts-badge bts-badge-pending';
        statusBadge.textContent = '◐ הושלם חלקית';
      } else if (status === 'unknown') {
        statusBadge.className = 'bts-badge bts-badge-pending';
        statusBadge.textContent = '○ מצב לא ידוע — נדרש בירור';
      } else if (status === 'disconnected') {
        statusBadge.className = 'bts-badge bts-badge-pending';
        statusBadge.textContent = '↻ אין חיבור ל־Trace · מנסה להתחבר מחדש';
      } else if (status === 'succeeded' || status === 'completed') {
        statusBadge.className = 'bts-badge bts-badge-completed';
        statusBadge.textContent = deliveryStatus === 'confirmed'
          ? '✔ הושלם ונמסר'
          : '✔ ה־API הושלם · אישור מסירה לא זמין';
      } else if (status === 'failed') {
        statusBadge.className = 'bts-badge bts-badge-failed';
        statusBadge.textContent = '✖ ' + t('bts.failed_badge');
      } else {
        statusBadge.className = 'bts-badge bts-badge-pending';
        statusBadge.textContent = '○ ' + t('bts.waiting_badge');
      }
    }

    async function fetchTrace() {
      if (!currentTraceId || !isOpen) return;
      if (traceFetchInFlight) return;
      traceFetchInFlight = true;
      const requestedTraceId = currentTraceId;
      let controller = null;
      let requestTimeout = null;
      try {
        if (typeof AbortController !== 'undefined') {
          controller = new AbortController();
          requestTimeout = setTimeout(function () { controller.abort(); }, 8000);
        }
        const response = await fetch('/admin/simulator/trace/' + encodeURIComponent(requestedTraceId), {
          method: 'GET',
          headers: { 'Content-Type': 'application/json' },
          credentials: 'same-origin',
          signal: controller ? controller.signal : undefined,
        });
        if (!response.ok) {
          pollFailures++;
          if (pollFailures >= 3 && requestedTraceId === currentTraceId) renderHeader(currentTraceId, 'disconnected');
          return;
        }
        const data = await response.json();
        if (requestedTraceId !== currentTraceId) return;
        pollFailures = 0;
        render(data);

        if (data.terminal || data.execution_status === 'unknown' || data.diagnostic_state === 'job_stopped_without_outcome') {
          stopPolling();
        }
      } catch (err) {
        console.warn('Behind-the-scenes trace fetch error:', err);
        pollFailures++;
        if (pollFailures >= 3 && requestedTraceId === currentTraceId) renderHeader(currentTraceId, 'disconnected');
      } finally {
        if (requestTimeout !== null) clearTimeout(requestTimeout);
        traceFetchInFlight = false;
      }
    }

    function startPolling() {
      stopPolling();
      if (!isOpen || !currentTraceId) return;
      fetchTrace();
      pollTimer = setInterval(function () {
        if (!isOpen) {
          stopPolling();
          return;
        }
        fetchTrace();
      }, POLL_BTS_INTERVAL_MS);
    }

    async function loadRecentTraces() {
      if (!recentSelect) return;
      try {
        const response = await fetch('/admin/simulator/traces/recent?limit=15', {
          credentials: 'same-origin',
        });
        if (!response.ok) return;
        const data = await response.json();
        const items = data.items || [];

        // Concurrency badge
        const activeCount = data.active_count !== undefined ? data.active_count : items.filter(function (it) {
          return it.status === 'running' || it.status === 'processing';
        }).length;
        if (activeBadge) {
          activeBadge.textContent = '⚡ ' + activeCount + ' ' + (activeCount === 1 ? 'בקשה פעילה' : 'בקשות פעילות');
          activeBadge.style.display = activeCount > 0 ? 'inline-flex' : 'none';
        }

        recentSelect.innerHTML = '<option value="">' + t('bts.select_trace') + '</option>';
        items.forEach(function (item) {
          const opt = document.createElement('option');
          opt.value = item.trace_id;
          const time = item.received_at ? item.received_at.slice(11, 19) : '';
          const isLive = item.status === 'running' || item.status === 'processing';
          const srcIcon = isLive ? '⏳' : (item.source === 'telegram' ? '📱' : '🔬');
          opt.textContent = srcIcon + ' [' + time + '] ' + (item.text || item.trace_id);
          if (item.trace_id === currentTraceId) opt.selected = true;
          recentSelect.appendChild(opt);
        });
      } catch (err) {
        console.warn('Could not load recent traces:', err);
      }
    }

    function render(data) {
      if (!data) return;
      const m = data.metrics || {};
      const isDone = data.terminal;
      const hasEvents = (data.event_count || 0) > 0;
      const outcome = data.outcome;
      const executionStatus = data.execution_status || (!hasEvents ? 'unknown' : (isDone ? (outcome === 'failed' ? 'failed' : 'succeeded') : 'running'));

      renderHeader(
        data.trace_id || currentTraceId,
        executionStatus,
        data.delivery_status
      );

      // 1. KPI Metrics Bar
      if (metricWall) {
        metricWall.textContent = (hasEvents && m.total_wall_clock_ms) ? (m.total_wall_clock_ms.toLocaleString() + ' ' + t('bts.ms')) : t('bts.na');
      }
      if (metricBreakdown) {
        if (!hasEvents) {
          metricBreakdown.textContent = t('bts.no_trace_events') || 'אין נתוני מעקב עדיין';
        } else {
          const mod = m.model_latency_ms ? (m.model_latency_ms.toLocaleString() + ' ' + t('bts.ms')) : '0';
          const tool = m.tools_duration_ms ? (m.tools_duration_ms.toLocaleString() + ' ' + t('bts.ms')) : '0';
          const queue = m.queue_wait_ms == null ? 'לא זמין' : (m.queue_wait_ms.toLocaleString() + ' ' + t('bts.ms'));
          metricBreakdown.textContent = 'ספק מצטבר: ' + mod + ' | כלים: ' + tool + ' | תור: ' + queue;
        }
      }
      if (metricLlm) {
        if (!hasEvents) {
          metricLlm.textContent = t('bts.no_trace_events') || 'אין נתוני מעקב עדיין';
        } else {
          metricLlm.textContent = (m.llm_call_count || 0) + ' קריאות ספק';
        }
      }
      if (metricRetries) {
        metricRetries.textContent = 'ניסיונות חוזרים: ' + (m.retries_count || 0);
      }
      if (metricTokens) {
        if (m.tokens && m.tokens.total) {
          metricTokens.textContent = m.tokens.total.toLocaleString();
        } else {
          metricTokens.textContent = t('bts.na');
        }
      }
      if (metricTokensSub) {
        if (m.tokens && m.tokens.total) {
          metricTokensSub.textContent = 'קלט: ' + m.tokens.input.toLocaleString() + ' | פלט: ' + m.tokens.output.toLocaleString() + ' | מטמון: ' + m.tokens.cache.toLocaleString();
        } else {
          metricTokensSub.textContent = t('bts.na');
        }
      }

      // 2. Agents & Parallel KPI
      const graphData = data.graph || { nodes: [], edges: [] };
      const specialists = (graphData.nodes || []).filter(function (n) {
        return n.type === 'invocation' && !['main_agent', 'report_composer_agent', 'insights_agent'].includes(n.agent_name || n.label);
      });
      const toolsCount = (graphData.nodes || []).filter(function (n) { return n.type === 'tool'; }).length;
      const parallelCount = graphData.parallel_invocation_count || 0;

      if (metricAgents) {
        metricAgents.textContent = specialists.length + ' מומחים | ' + toolsCount + ' כלים';
      }
      if (metricAgentsSub) {
        metricAgentsSub.textContent = 'ענפים במקביל: ' + parallelCount + (graphData.parallel_batches_count ? ' (' + graphData.parallel_batches_count + ' מחזורים)' : '');
      }

      currentGraphData = graphData;
      currentMetrics = m;

      // 3. Render Live Agent Execution Graph
      renderGraph(graphData, m);

      // If a node was previously selected, refresh its details panel in place
      if (selectedNodeId) {
        const found = (graphData.nodes || []).find(function (n) { return n.id === selectedNodeId; });
        if (found) showNodeDetail(found);
      }
    }

    // ---- Layout & Graph Renderer -------------------------------------------------------------
    function renderGraph(graphData, metrics) {
      if (!edgesLayer || !nodesLayer) return;
      edgesLayer.innerHTML = '';
      nodesLayer.innerHTML = '';

      const nodes = graphData.nodes || [];
      const edges = graphData.edges || [];

      if (nodes.length === 0) {
        nodesLayer.innerHTML = '<text x="500" y="240" fill="#64748b" font-size="14" text-anchor="middle" font-family="sans-serif">ממתין לבקשת סימולציה... שלח הודעה או בחר בקשה מרשימת ה-Traces</text>';
        return;
      }

      const cx = 500;
      const posMap = {};
      const hasInvocationGraph = nodes.some(function (n) {
        return ['invocation', 'model', 'routing', 'result', 'persistence', 'composition'].includes(n.type);
      });

      if (hasInvocationGraph) {
        const rows = [
          nodes.filter(function (n) { return n.type === 'user'; }),
          nodes.filter(function (n) { return n.type === 'main'; }),
          nodes.filter(function (n) { return n.type === 'routing'; }),
          nodes.filter(function (n) { return n.type === 'invocation'; }),
          nodes.filter(function (n) { return n.type === 'model'; }),
          nodes.filter(function (n) { return n.type === 'tool'; }),
          nodes.filter(function (n) { return n.type === 'persistence'; }),
          nodes.filter(function (n) { return n.type === 'composition'; }),
          nodes.filter(function (n) { return n.type === 'result' || n.type === 'outcome'; }),
        ];
        rows.forEach(function (row, rowIndex) {
          if (!row.length) return;
          const width = 210;
          const gap = 34;
          const rowWidth = row.length * width + (row.length - 1) * gap;
          const left = Math.max(115, cx - rowWidth / 2 + width / 2);
          row.forEach(function (node, index) {
            node.w = width;
            node.h = node.type === 'main' ? 84 : 72;
            node.x = left + index * (width + gap);
            node.y = 55 + rowIndex * 112;
            posMap[node.id] = node;
          });
        });
      } else {
        const mainNode = nodes.find(function (n) { return n.type === 'main'; });
        const specialists = nodes.filter(function (n) { return n.type === 'specialist'; });
        const directTools = nodes.filter(function (n) { return n.type === 'tool' && (!n.parent || n.parent === 'main_agent'); });
        const persistence = nodes.find(function (n) { return n.type === 'persistence'; });

        if (mainNode) {
          mainNode.w = 260;
          mainNode.h = 92;
          mainNode.x = cx;
          mainNode.y = 80;
          posMap[mainNode.id] = mainNode;
        }

        const numSpec = specialists.length;
        let maxBottomY = 280;
        if (numSpec > 0) {
          const spacing = Math.max(250, Math.min(320, 840 / Math.max(1, numSpec)));
          const startX = cx - ((numSpec - 1) * spacing) / 2;
          specialists.forEach(function (spec, idx) {
            spec.w = 230;
            spec.h = 88;
            spec.x = startX + idx * spacing;
            spec.y = 260;
            posMap[spec.id] = spec;
            const specTools = nodes.filter(function (n) { return n.type === 'tool' && n.parent === spec.id; });
            specTools.forEach(function (tool, tIdx) {
              tool.w = 190;
              tool.h = 64;
              const offsetX = specTools.length > 1 ? (tIdx % 2 === 0 ? -60 : 60) : 0;
              tool.x = spec.x + offsetX;
              tool.y = 410 + Math.floor(tIdx / 2) * 76;
              posMap[tool.id] = tool;
              if (tool.y + 40 > maxBottomY) maxBottomY = tool.y + 40;
            });
          });
        }
        directTools.forEach(function (tool, idx) {
          tool.w = 180;
          tool.h = 60;
          tool.x = cx - 340;
          tool.y = 90 + idx * 72;
          posMap[tool.id] = tool;
          if (tool.y + 40 > maxBottomY) maxBottomY = tool.y + 40;
        });
        if (persistence) {
          persistence.w = 240;
          persistence.h = 76;
          persistence.x = numSpec > 0 ? cx : cx + 320;
          persistence.y = numSpec > 0 ? Math.max(480, maxBottomY + 70) : 80;
          posMap[persistence.id] = persistence;
        }
      }

      // 1. Draw Edges
      edges.forEach(function (edge) {
        const src = posMap[edge.source];
        const tgt = posMap[edge.target];
        if (!src || !tgt) return;

        const sx = src.x;
        const sy = src.y + src.h / 2;
        const tx = tgt.x;
        const ty = tgt.y - tgt.h / 2;
        const midY = (sy + ty) / 2;

        const d = 'M ' + sx + ' ' + sy + ' C ' + sx + ' ' + midY + ', ' + tx + ' ' + midY + ', ' + tx + ' ' + ty;

        const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
        path.setAttribute('d', d);
        path.setAttribute('fill', 'none');

        let strokeColor = '#334155';
        let markerId = 'marker-pending';

        if (edge.status === 'active') {
          strokeColor = edge.is_parallel ? '#c084fc' : '#38bdf8';
          markerId = edge.is_parallel ? 'marker-parallel' : 'marker-active';
          path.classList.add('bts-edge-flow');
          path.setAttribute('stroke-width', '2.5');
        } else if (edge.status === 'completed') {
          strokeColor = '#10b981';
          markerId = 'marker-completed';
          path.setAttribute('stroke-width', '1.8');
        } else if (edge.status === 'failed') {
          strokeColor = '#ef4444';
          markerId = 'marker-failed';
          path.setAttribute('stroke-width', '2');
        } else {
          path.setAttribute('stroke-width', '1.2');
        }

        if (edge.type === 'unattributed') {
          strokeColor = '#64748b';
          markerId = 'marker-pending';
          path.setAttribute('stroke-dasharray', '3 5');
        }

        path.setAttribute('stroke', strokeColor);
        path.setAttribute('marker-end', 'url(#' + markerId + ')');
        edgesLayer.appendChild(path);

        // Parallel annotation badge on edge
        if (edge.is_parallel) {
          const badgeG = document.createElementNS('http://www.w3.org/2000/svg', 'g');
          const midX = (sx + tx) / 2;

          const rect = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
          rect.setAttribute('x', midX - 38);
          rect.setAttribute('y', midY - 10);
          rect.setAttribute('width', '76');
          rect.setAttribute('height', '20');
          rect.setAttribute('rx', '4');
          rect.setAttribute('fill', '#3b0764');
          rect.setAttribute('stroke', '#c084fc');
          rect.setAttribute('stroke-width', '1');
          badgeG.appendChild(rect);

          const txt = document.createElementNS('http://www.w3.org/2000/svg', 'text');
          txt.setAttribute('x', midX);
          txt.setAttribute('y', midY + 4);
          txt.setAttribute('text-anchor', 'middle');
          txt.setAttribute('fill', '#f3e8ff');
          txt.setAttribute('font-size', '10');
          txt.setAttribute('font-weight', '700');
          txt.setAttribute('font-family', 'sans-serif');
          txt.textContent = '⚡ במקביל';
          badgeG.appendChild(txt);

          edgesLayer.appendChild(badgeG);
        }
      });

      // 2. Draw Nodes
      nodes.forEach(function (node) {
        const p = posMap[node.id];
        if (!p) return;

        const g = document.createElementNS('http://www.w3.org/2000/svg', 'g');
        g.setAttribute('transform', 'translate(' + (p.x - p.w / 2) + ', ' + (p.y - p.h / 2) + ')');
        g.setAttribute('class', 'bts-node-card');
        g.style.cursor = 'pointer';
        g.dataset.id = node.id;

        // Card Background
        const rect = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
        rect.setAttribute('width', p.w);
        rect.setAttribute('height', p.h);
        rect.setAttribute('rx', '8');

        let bgFill = '#0f172a';
        let strokeColor = '#334155';
        let strokeW = '1.5';

        if (node.type === 'main') {
          bgFill = '#0b1329';
          strokeColor = node.status === 'running' ? '#38bdf8' : (node.status === 'failed' ? '#ef4444' : '#2563eb');
          if (node.status === 'running') rect.setAttribute('filter', 'url(#bts-glow-cyan)');
        } else if (node.type === 'specialist') {
          bgFill = '#160d2b';
          strokeColor = node.is_parallel ? '#c084fc' : (node.status === 'running' ? '#a855f7' : '#6b21a8');
          if (node.status === 'running' || node.is_parallel) rect.setAttribute('filter', 'url(#bts-glow-purple)');
        } else if (node.type === 'tool') {
          bgFill = '#061a1a';
          strokeColor = node.status === 'failed' ? '#ef4444' : '#0d9488';
          if (node.status === 'success') strokeColor = '#059669';
        } else if (node.type === 'invocation') {
          bgFill = '#160d2b';
          strokeColor = node.status === 'failed' ? '#ef4444' : '#7c3aed';
        } else if (node.type === 'model') {
          bgFill = '#0c1d30';
          strokeColor = node.status === 'failed' ? '#ef4444' : '#0284c7';
        } else if (node.type === 'routing') {
          bgFill = '#172033';
          strokeColor = '#64748b';
        } else if (node.type === 'composition') {
          bgFill = '#11152b';
          strokeColor = '#818cf8';
        } else if (node.type === 'result') {
          bgFill = '#10251c';
          strokeColor = node.status === 'failed' ? '#ef4444' : '#059669';
        } else if (node.type === 'persistence') {
          bgFill = '#1a1306';
          strokeColor = node.status === 'failed' ? '#ef4444' : '#d97706';
        }

        if (selectedNodeId === node.id) {
          strokeColor = '#ffffff';
          strokeW = '2.5';
        }

        rect.setAttribute('fill', bgFill);
        rect.setAttribute('stroke', strokeColor);
        rect.setAttribute('stroke-width', strokeW);
        g.appendChild(rect);

        // Header Accent Strip
        const strip = document.createElementNS('http://www.w3.org/2000/svg', 'path');
        strip.setAttribute('d', 'M 0 8 Q 0 0 8 0 L ' + (p.w - 8) + ' 0 Q ' + p.w + ' 0 ' + p.w + ' 8 L ' + p.w + ' 20 L 0 20 Z');
        let stripFill = 'rgba(56, 189, 248, 0.12)';
        if (node.type === 'specialist') stripFill = node.is_parallel ? 'rgba(192, 132, 252, 0.22)' : 'rgba(168, 85, 247, 0.15)';
        if (node.type === 'tool') stripFill = 'rgba(16, 185, 129, 0.15)';
        if (node.type === 'invocation') stripFill = 'rgba(168, 85, 247, 0.15)';
        if (node.type === 'model') stripFill = 'rgba(56, 189, 248, 0.15)';
        if (node.type === 'routing') stripFill = 'rgba(100, 116, 139, 0.15)';
        if (node.type === 'composition') stripFill = 'rgba(129, 140, 248, 0.18)';
        if (node.type === 'result') stripFill = 'rgba(16, 185, 129, 0.15)';
        if (node.type === 'persistence') stripFill = 'rgba(245, 158, 11, 0.18)';
        strip.setAttribute('fill', stripFill);
        g.appendChild(strip);

        // Icon + Title
        let icon = '🤖';
        if (node.type === 'specialist') icon = '🔬';
        if (node.type === 'tool') icon = node.side_effecting ? '🛠️' : '🔍';
        if (node.type === 'invocation') icon = '🤖';
        if (node.type === 'model') icon = '🧠';
        if (node.type === 'routing') icon = '🧭';
        if (node.type === 'composition') icon = '📝';
        if (node.type === 'result') icon = '📥';
        if (node.type === 'persistence') icon = '💾';

        const titleText = document.createElementNS('http://www.w3.org/2000/svg', 'text');
        titleText.setAttribute('x', '10');
        titleText.setAttribute('y', '15');
        titleText.setAttribute('fill', '#f8fafc');
        titleText.setAttribute('font-size', '12');
        titleText.setAttribute('font-weight', '700');
        titleText.setAttribute('font-family', 'sans-serif');
        titleText.textContent = icon + ' ' + (node.label || node.id);
        g.appendChild(titleText);

        // Subtitle
        const subText = document.createElementNS('http://www.w3.org/2000/svg', 'text');
        subText.setAttribute('x', '10');
        subText.setAttribute('y', '38');
        subText.setAttribute('fill', '#94a3b8');
        subText.setAttribute('font-size', '10');
        subText.setAttribute('font-family', 'monospace');
        let subContent = node.sublabel || '';
        if (subContent.length > 26) subContent = subContent.slice(0, 25) + '…';
        subText.textContent = subContent;
        g.appendChild(subText);

        // Parallel Tag for Specialist
        if (node.type === 'specialist' && node.is_parallel) {
          const parG = document.createElementNS('http://www.w3.org/2000/svg', 'g');
          const pRect = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
          pRect.setAttribute('x', p.w - 74);
          pRect.setAttribute('y', '4');
          pRect.setAttribute('width', '68');
          pRect.setAttribute('height', '14');
          pRect.setAttribute('rx', '3');
          pRect.setAttribute('fill', '#581c87');
          pRect.setAttribute('stroke', '#d8b4fe');
          pRect.setAttribute('stroke-width', '0.5');
          parG.appendChild(pRect);

          const pTxt = document.createElementNS('http://www.w3.org/2000/svg', 'text');
          pTxt.setAttribute('x', p.w - 40);
          pTxt.setAttribute('y', '14');
          pTxt.setAttribute('text-anchor', 'middle');
          pTxt.setAttribute('fill', '#f3e8ff');
          pTxt.setAttribute('font-size', '9');
          pTxt.setAttribute('font-weight', '700');
          pTxt.setAttribute('font-family', 'sans-serif');
          pTxt.textContent = '⚡ במקביל';
          parG.appendChild(pTxt);
          g.appendChild(parG);
        }

        // Verification Tag for Tool
        if (node.type === 'tool' && node.verification) {
          const vG = document.createElementNS('http://www.w3.org/2000/svg', 'g');
          const vRect = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
          vRect.setAttribute('x', p.w - 70);
          vRect.setAttribute('y', '4');
          vRect.setAttribute('width', '64');
          vRect.setAttribute('height', '14');
          vRect.setAttribute('rx', '3');

          let vColor = '#10b981';
          let vBg = 'rgba(16, 185, 129, 0.25)';
          let vLabel = '✔ מאומת';
          if (node.verification === 'read_only') {
            vColor = '#38bdf8'; vBg = 'rgba(56, 189, 248, 0.25)'; vLabel = 'ℹ קריאה';
          } else if (node.verification === 'unverified') {
            vColor = '#f59e0b'; vBg = 'rgba(245, 158, 11, 0.25)'; vLabel = '⚠ ללא אימות';
          } else if (node.verification === 'failed') {
            vColor = '#ef4444'; vBg = 'rgba(239, 68, 68, 0.25)'; vLabel = '✖ נכשל';
          } else if (node.verification === 'blocked') {
            vColor = '#ef4444'; vBg = 'rgba(239, 68, 68, 0.25)'; vLabel = '⛔ נחסם';
          } else if (node.verification === 'verification_unavailable' || node.verification === 'unavailable') {
            vColor = '#94a3b8'; vBg = 'rgba(148, 163, 184, 0.2)'; vLabel = '? אימות לא זמין';
          }

          vRect.setAttribute('fill', vBg);
          vRect.setAttribute('stroke', vColor);
          vRect.setAttribute('stroke-width', '0.5');
          vG.appendChild(vRect);

          const vTxt = document.createElementNS('http://www.w3.org/2000/svg', 'text');
          vTxt.setAttribute('x', p.w - 38);
          vTxt.setAttribute('y', '14');
          vTxt.setAttribute('text-anchor', 'middle');
          vTxt.setAttribute('fill', vColor);
          vTxt.setAttribute('font-size', '8.5');
          vTxt.setAttribute('font-weight', '700');
          vTxt.setAttribute('font-family', 'sans-serif');
          vTxt.textContent = vLabel;
          vG.appendChild(vTxt);
          g.appendChild(vG);
        }

        // Status & Duration bottom strip
        const statText = document.createElementNS('http://www.w3.org/2000/svg', 'text');
        statText.setAttribute('x', '10');
        statText.setAttribute('y', p.h - 12);
        statText.setAttribute('font-size', '10');
        statText.setAttribute('font-family', 'sans-serif');

        let statStr = '○ ממתין';
        let statFill = '#94a3b8';

        if (node.status === 'running') {
          statStr = '⏳ פעיל...';
          statFill = '#38bdf8';
        } else if (node.status === 'success' || node.status === 'completed') {
          statStr = '✔ הושלם' + (node.duration_ms ? ' (' + node.duration_ms + ' ms)' : '');
          statFill = '#34d399';
        } else if (node.status === 'failed') {
          statStr = '✖ נכשל';
          statFill = '#f87171';
        } else if (node.status === 'retry') {
          statStr = '🔄 ניסיון חוזר (' + (node.retries || 1) + ')';
          statFill = '#fbbf24';
        } else if (node.status === 'waiting') {
          statStr = '⏸ ממתין להשהיה';
          statFill = '#fbbf24';
        }

        statText.setAttribute('fill', statFill);
        statText.setAttribute('font-weight', '600');
        statText.textContent = statStr;
        g.appendChild(statText);

        // Click Handler: Select Node and open Details Flyout
        g.addEventListener('click', function (e) {
          e.stopPropagation();
          selectedNodeId = node.id;
          renderGraph(graphData, metrics);
          showNodeDetail(node);
        });

        nodesLayer.appendChild(g);
      });
    }

    // ---- Node Detail Flyout ------------------------------------------------------------------
    function showNodeDetail(node) {
      if (!detailPanel) return;

      // Icon & Header
      let icon = '🤖';
      if (node.type === 'specialist') icon = '🔬';
      if (node.type === 'tool') icon = node.side_effecting ? '🛠️' : '🔍';
      if (node.type === 'persistence') icon = '💾';
      if (node.type === 'model') icon = '🧠';
      if (node.type === 'routing') icon = '🧭';
      if (node.type === 'result') icon = '📥';
      if (node.type === 'composition') icon = '📝';

      if (detIcon) detIcon.textContent = icon;
      if (detTitle) detTitle.textContent = node.label || node.id;
      if (detSub) detSub.textContent = node.sublabel || node.type;

      // Status Badge
      if (detStatus) {
        if (node.status === 'running') {
          detStatus.className = 'bts-badge bts-badge-live';
          detStatus.textContent = '⏳ פעיל כעת';
        } else if (node.status === 'success' || node.status === 'completed') {
          detStatus.className = 'bts-badge bts-badge-completed';
          detStatus.textContent = '✔ הושלם בהצלחה';
        } else if (node.status === 'failed') {
          detStatus.className = 'bts-badge bts-badge-failed';
          detStatus.textContent = '✖ נכשל';
        } else if (node.status === 'retry') {
          detStatus.className = 'bts-badge bts-badge-pending';
          detStatus.textContent = '🔄 ניסיון חוזר (' + (node.retries || 1) + ')';
        } else if (node.status === 'unknown') {
          detStatus.className = 'bts-badge bts-badge-pending';
          detStatus.textContent = '○ מצב לא ידוע — חסר אירוע סיום';
        } else {
          detStatus.className = 'bts-badge bts-badge-pending';
          detStatus.textContent = '○ ממתין';
        }
      }

      // Duration & Calls
      if (detDur) detDur.textContent = node.duration_ms ? (node.duration_ms + ' ' + t('bts.ms')) : t('bts.na');
      if (detCalls) {
        const llmCount = Array.isArray(node.llm_calls) ? node.llm_calls.length : 0;
        const count = Number(node.call_count) || llmCount || 1;
        detCalls.textContent = node.model_completion_event
          ? 'אירוע סיום מודל (לא קריאת ספק מזוהה)'
          : (count + (node.type === 'model' ? ' קריאת ספק' : ' הפעלות'));
      }

      // Parallel Status
      if (detParallel) {
        detParallel.textContent = node.is_parallel ? '⚡ כן (בו-זמנית עם מומחים נוספים)' : 'לא (סדרתי)';
        detParallel.style.color = node.is_parallel ? '#c084fc' : '#cbd5e1';
      }

      // Retries Row
      if (detRetriesRow && detRetries) {
        if (node.retries && node.retries > 0) {
          detRetriesRow.style.display = 'flex';
          detRetries.textContent = node.retries;
        } else {
          detRetriesRow.style.display = 'none';
        }
      }

      // Task / Directives
      if (detTaskTitle && detTaskContent) {
        if (node.type === 'model' && Array.isArray(node.llm_calls)) {
          detTaskTitle.textContent = 'Provider request — metadata בטוח';
          detTaskContent.textContent = node.llm_calls.map(function (c) {
            return [
              'Agent: ' + (c.agent_name || 'unattributed'),
              'יוזם: ' + (c.parent_agent || 'unattributed'),
              'Invocation: ' + (c.agent_invocation_id || 'unattributed'),
              'Purpose / stage: ' + (c.purpose || 'unattributed') + ' / ' + (c.stage || 'unattributed'),
              'Protocol / tool: ' + (c.protocol_name || 'לא זמין') + ' / ' + (c.tool_name || 'לא זמין'),
              'Provider request: ' + (c.provider_request_id || 'לא זמין') + ' · sequence ' + (c.sequence_number ?? 'לא זמין'),
              'שיוך: ' + (node.attribution_status || (c.agent_invocation_id ? 'attributed' : (c.agent_name ? 'partial' : 'unattributed'))),
              'התחלה/סיום: ' + (c.started_at || 'לא זמין') + ' / ' + (c.finished_at || 'לא זמין'),
              'זמן: ' + (c.latency_ms ?? 'לא זמין') + ' מ״ש · finish: ' + (c.finish_reason || c.status || 'לא ידוע'),
              'טוקנים קלט/פלט/מטמון: ' + (c.input_tokens ?? '?') + '/' + (c.output_tokens ?? '?') + '/' + (c.cache_tokens ?? '?'),
              'סיכום בטוח: ' + (c.result_summary || 'לא זמין'),
            ].join('\\n');
          }).join('\\n\\n');
        } else if (node.type === 'routing' || node.type === 'result') {
          detTaskTitle.textContent = node.type === 'routing' ? 'בחירת צעד — ללא הנחת invocation' : 'תוצאת invocation';
          detTaskContent.textContent = node.details || node.task || 'לא נשמרו פרטים נוספים.';
        } else if (node.type === 'invocation') {
          detTaskTitle.textContent = 'Agent invocation';
          const calls = Array.isArray(node.llm_calls) ? node.llm_calls : [];
          detTaskContent.textContent = (node.task || 'תקציר משימה לא נשמר.') +
            '\\nInvocation ID: ' + (node.invocation_id || 'לא זמין') +
            '\\nParent: ' + (node.parent_agent || 'Orchestrator') +
            '\\nProtocol: ' + (node.protocol_name || 'לא זמין') +
            '\\nProvider calls: ' + calls.length +
            (node.model_status ? ('\\nריצת מודל: ' + node.model_status + ' · ' + (node.model_duration_ms ?? 'לא זמין') + ' מ״ש · טוקנים קלט/פלט: ' + (node.model_input_tokens ?? '?') + '/' + (node.model_output_tokens ?? '?')) : '');
        } else if (node.type === 'main') {
          detTaskTitle.textContent = 'כוונה ופרוטוקול שנבחרו';
          detTaskContent.textContent = (node.intent || 'מעבד בקשה') + (node.protocol ? '\\nפרוטוקול: ' + node.protocol : '');
        } else if (node.type === 'specialist') {
          detTaskTitle.textContent = 'מה התבקש מהמומחה';
          if (Array.isArray(node.tasks) && node.tasks.length > 0) {
            detTaskContent.textContent = node.tasks.join('\\n\\n');
          } else {
            detTaskContent.textContent = 'ביצוע משימת מומחה במסגרת הפרוטוקול.';
          }
        } else if (node.type === 'tool') {
          detTaskTitle.textContent = 'מטרת הפעלת הכלי';
          detTaskContent.textContent = (node.summary || ('הפעלת ' + (node.label || 'כלי'))) +
            '\\nCaller: ' + (node.caller_agent_name || 'לא נשמר') +
            '\\nInvocation ID: ' + (node.agent_invocation_id || 'לא נשמר');
        } else if (node.type === 'persistence') {
          detTaskTitle.textContent = 'פעולת שמירה ואימות';
          detTaskContent.textContent = node.details || 'שמירה ב-SQLite ואימות מצב תפעולי.';
        } else if (node.type === 'composition') {
          detTaskTitle.textContent = 'הרכבת תשובה';
          detTaskContent.textContent = node.details || 'נרשם אירוע הרכבת תשובה; תוכן גולמי מוסתר.';
        }
      }

      // Tools / Verification Section
      if (detToolsSection && detToolsList) {
        detToolsList.innerHTML = '';
        let toolsToDisplay = [];
        if (node.type === 'specialist' && Array.isArray(node.tools)) {
          toolsToDisplay = node.tools;
        } else if (node.type === 'tool') {
          toolsToDisplay = [node];
        }

        if (toolsToDisplay.length === 0) {
          detToolsSection.style.display = 'none';
        } else {
          detToolsSection.style.display = 'block';
          toolsToDisplay.forEach(function (tItem) {
            const row = document.createElement('div');
            row.style.background = '#0f172a';
            row.style.border = '1px solid #334155';
            row.style.borderRadius = '5px';
            row.style.padding = '8px 10px';

            const top = document.createElement('div');
            top.className = 'd-flex justify-content-between align-items-center mb-1';

            const name = document.createElement('code');
            name.className = 'bts-code-pill';
            name.textContent = tItem.tool || tItem.label;
            top.appendChild(name);

            // Verification Tag - EVIDENCE BASED ONLY
            const vTag = document.createElement('span');
            const vKind = tItem.verification || 'unverified';
            vTag.className = 'bts-vtag bts-vtag-' + vKind;

            if (vKind === 'verified') {
              vTag.textContent = '✔ מאומת (ראיה מפורשת)';
            } else if (vKind === 'read_only') {
              vTag.textContent = 'ℹ קריאה בלבד (ללא שינוי מצב)';
            } else if (vKind === 'unverified') {
              vTag.textContent = '⚠ ללא אימות (בוצע ללא בדיקה)';
            } else if (vKind === 'failed') {
              vTag.textContent = '✖ נכשל';
            } else if (vKind === 'blocked') {
              vTag.textContent = '⛔ נחסם';
            } else if (vKind === 'unavailable' || vKind === 'verification_unavailable') {
              vTag.textContent = '? אימות לא זמין';
            } else {
              vTag.textContent = vKind;
            }
            top.appendChild(vTag);
            row.appendChild(top);

            // Note / summary
            if (tItem.verification_note || tItem.summary) {
              const noteDiv = document.createElement('div');
              noteDiv.style.fontSize = '11px';
              noteDiv.style.color = '#94a3b8';
              noteDiv.style.marginTop = '4px';
              noteDiv.textContent = tItem.verification_note || tItem.summary;
              row.appendChild(noteDiv);
            }

            detToolsList.appendChild(row);
          });
        }
      }

      // Error Section
      if (detErrorSection && detErrorContent) {
        if (node.status === 'failed' || (node.outcome_reason && node.is_terminal)) {
          detErrorSection.style.display = 'block';
          detErrorContent.textContent = node.outcome_reason || 'הפעולה נכשלה או נתקלה בחריגה במהלך העיבוד.';
        } else {
          detErrorSection.style.display = 'none';
        }
      }

      // Slide In
      detailPanel.classList.remove('hidden');
    }

    function hideNodeDetail() {
      if (detailPanel) detailPanel.classList.add('hidden');
      selectedNodeId = null;
      if (currentGraphData && currentMetrics) {
        renderGraph(currentGraphData, currentMetrics);
      }
    }

    // Wiring DOM Events
    const closeBtn = document.getElementById('bts-close-btn');
    if (closeBtn) closeBtn.addEventListener('click', close);
    if (overlay) overlay.addEventListener('click', close);

    const toggleBtn = document.getElementById('toggle-bts');
    if (toggleBtn) toggleBtn.addEventListener('click', toggle);

    if (detCloseBtn) detCloseBtn.addEventListener('click', hideNodeDetail);

    // Click outside nodes on SVG canvas hides the detail flyout
    if (svg) {
      svg.addEventListener('click', function (e) {
        if (!e.target.closest('.bts-node-card')) {
          hideNodeDetail();
        }
      });
    }

    // Pan & Zoom controls
    const zoomInBtn = document.getElementById('bts-zoom-in');
    const zoomOutBtn = document.getElementById('bts-zoom-out');
    const zoomFitBtn = document.getElementById('bts-zoom-fit');

    if (zoomInBtn) zoomInBtn.addEventListener('click', function () { zoomBy(1.25); });
    if (zoomOutBtn) zoomOutBtn.addEventListener('click', function () { zoomBy(0.8); });
    if (zoomFitBtn) zoomFitBtn.addEventListener('click', resetZoomFit);

    // Mouse Drag to Pan
    if (svg) {
      svg.addEventListener('pointerdown', function (e) {
        if (e.target.closest('.bts-node-card')) return;
        isPanning = true;
        startPanX = e.clientX - transform.x;
        startPanY = e.clientY - transform.y;
        try { svg.setPointerCapture(e.pointerId); } catch (_) {}
      });

      svg.addEventListener('pointermove', function (e) {
        if (!isPanning) return;
        transform.x = e.clientX - startPanX;
        transform.y = e.clientY - startPanY;
        applyTransform();
      });

      const endPan = function (e) {
        if (isPanning) {
          isPanning = false;
          try { svg.releasePointerCapture(e.pointerId); } catch (_) {}
        }
      };
      svg.addEventListener('pointerup', endPan);
      svg.addEventListener('pointercancel', endPan);
      svg.addEventListener('pointerleave', endPan);

      // Wheel Zoom
      if (viewport) {
        viewport.addEventListener('wheel', function (e) {
          e.preventDefault();
          const rect = svg.getBoundingClientRect();
          const mouseX = e.clientX - rect.left;
          const mouseY = e.clientY - rect.top;
          const delta = e.deltaY < 0 ? 1.15 : 0.87;
          const newScale = Math.max(0.35, Math.min(2.5, transform.scale * delta));
          transform.x = mouseX - (mouseX - transform.x) * (newScale / transform.scale);
          transform.y = mouseY - (mouseY - transform.y) * (newScale / transform.scale);
          transform.scale = newScale;
          applyTransform();
        }, { passive: false });
      }
    }

    // Switch between traces without mixing state
    if (recentSelect) {
      recentSelect.addEventListener('change', function () {
        const selTrace = recentSelect.value;
        if (selTrace) {
          currentTraceId = selTrace;
          selectedNodeId = null;
          hideNodeDetail();
          open(selTrace);
        }
      });
    }

    return {
      open: open,
      close: close,
      toggle: toggle,
      track: track,
      onStepComplete: onStepComplete,
      reset: reset,
      get isOpen() { return isOpen; },
      get currentTraceId() { return currentTraceId; }
    };
  })();

  document.getElementById('load-pasted').addEventListener('click', function () {
    loadFromText(document.getElementById('paste-input').value);
  });
  document.getElementById('apply-mapping').addEventListener('click', async function () {
    const button = document.getElementById('apply-mapping');
    if (!mappingMode) return;
    try {
      button.disabled = true;
      const mapping = collectMappingValues(mappingMode.groupsNeedingId, mappingMode.personaValues);
      loadScenario(applyManualMapping(mappingMode.raw, mapping));
      // loadScenario() itself already closed the panel — nothing left to clean up here on success.
    } catch (error) { showAlert(error.message, true); }
    finally { button.disabled = false; }
  });
  document.getElementById('send-next').addEventListener('click', function () {
    const key = nextChatKey();
    if (key !== null) sendNext(key);
  });
  document.getElementById('reset-view').addEventListener('click', function () {
    // View only: clears the cards and the queues. Nothing already sent is undone on the server.
    invalidatePollWatchers();
    state.scenario = null; state.scenarioSteps = []; state.chats = []; state.chatsByKey = {}; state.queues = {}; state.runId = null; state.busy = false;
    document.getElementById('chats-container').innerHTML = '';
    document.getElementById('scenario-title').textContent = t('no_scenario');
    document.getElementById('scenario-desc').textContent = '';
    document.getElementById('scenario-badges').innerHTML = '';
    document.getElementById('expected-actions').innerHTML = '';
    document.getElementById('send-next').disabled = true;
    document.getElementById('reset-view').disabled = true;
    closeMappingPanel();
    closeEditDialog();
    showAlert('', false);
    BehindTheScenes.reset();
  });
  document.getElementById('edit-step-save').addEventListener('click', savePendingEdit);
  document.getElementById('edit-step-cancel').addEventListener('click', closeEditDialog);
  document.getElementById('edit-step-overlay').addEventListener('click', function (event) {
    if (event.target === event.currentTarget) closeEditDialog();
  });
  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape') closeEditDialog();
  });
})();
</script>
"""
