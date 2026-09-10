"""Scenario simulator page for the admin panel (`api/admin.py` mounts it at `/admin/simulator`).

The page loads a scenario JSON (file, drag-and-drop, or paste), draws one card per chat or
sensor the scenario declares, and lets the operator release the steps one at a time, in order.
Every released step is sent by the **browser** to the real `POST /Msg` / `POST /Event` on the
same origin with `X-Identity` set to that step's own `sender_identity` — the exact request the
bot makes (`bot/transports.py`), so the same registration, permission and group-scoping rules
apply as in production, and nothing here bypasses or duplicates the API's ingestion logic. The
system's answer (an inline answer, or a queued job polled through `GET /Job/<event_id>` under the
same identity) is shown in the same card.

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

`kind: "message"` steps go to `POST /Msg` (with `telegram_chat_id`/`telegram_chat_type` exactly as
declared on the chat, `conversation_id` derived the way the bot derives it, and an optional
`protocol_hint`/`source_message_id`); `kind: "event"` steps go to `POST /Event`. `timestamp`,
`sender_name`, `label`, `title`, `description` and `tags` are display-only — `occurred_at` is
always the server's receipt time, and the simulator never pretends otherwise.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from messages import MessageCatalog

if TYPE_CHECKING:
    from api.app import ApiContext

SIMULATOR_STRING_PREFIX = "admin.simulator."


def simulator_page_context(ctx: "ApiContext", catalog: MessageCatalog, bot_service_identity: str) -> dict:
    """Everything the page's script needs, as one JSON-serialisable dict: the live group
    bindings and registered users (so cards can show how a chat will be routed and warn about an
    unregistered sender before the API refuses it), the routable agents, and the raw
    `admin.simulator.*` message templates of the current catalog (formatted client-side)."""

    groups = [
        {"chat_id": binding.chat_id, "agent_name": binding.agent_name, "label": binding.label}
        for binding in ctx.group_routing.all()
    ]
    users = [
        {"telegram_identity": user["telegram_identity"], "permission_level": user["permission_level"]}
        for user in sorted(ctx.deps.persistence.list_users(), key=lambda user: user["telegram_identity"])
    ]
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
        "strings": strings,
    }


SIMULATOR_STYLE = """
<style>
  .container-wide { max-width: 1400px; }
  .sim-toolbar { display: flex; flex-wrap: wrap; gap: 12px; align-items: stretch; margin-bottom: 20px; }
  .sim-drop {
    flex: 1 1 320px;
    border: 2px dashed var(--line-strong);
    border-radius: 6px;
    padding: 18px;
    text-align: center;
    cursor: pointer;
    background: var(--panel);
    color: var(--text-dim);
    font-size: 14px;
    display: flex; align-items: center; justify-content: center;
  }
  .sim-drop.dragover { border-color: var(--commander); background: var(--commander-dim); color: #075A47; }
  .sim-paste { flex: 1 1 320px; display: flex; flex-direction: column; gap: 6px; }
  .sim-paste textarea { min-height: 72px; resize: vertical; }
  .sim-actions { display: flex; flex-direction: column; gap: 6px; justify-content: center; }
  .sim-actions .btn { min-width: 170px; }
  .sim-header {
    background: var(--panel);
    border: 1px solid var(--line-strong);
    border-radius: 6px;
    padding: 18px 22px;
    margin-bottom: 20px;
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
    border: 2px solid var(--line-strong);
    border-radius: 6px;
    display: flex; flex-direction: column;
    height: 620px;
    transition: border-color 0.2s ease, box-shadow 0.2s ease;
  }
  .chat-card.active-next { border-color: var(--commander); box-shadow: 0 0 0 3px var(--commander-dim); }
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
  .send-btn { width: 100%; }
  .sim-empty { color: var(--text-dim); font-size: 15px; padding: 24px 0; text-align: center; }
</style>
"""

# Body + script. Server-rendered text uses the usual `t()`; the script reads its strings from the
# embedded `sim-data` JSON (see simulator_page_context) and formats them with the same `{name}`
# placeholder syntax the catalog uses.
SIMULATOR_BODY = """
<div class="container container-wide">

  <div class="d-flex justify-content-between align-items-baseline mb-1">
    <h1 class="mb-0">{{ t('admin.simulator.title') }}</h1>
    <div class="d-flex align-items-center gap-3">
      <a class="nav-console" href="{{ url_for('admin.dashboard') }}">{{ t('admin.nav_dashboard') }}</a>
      <span class="status-pill"><span class="dot"></span>{{ t('admin.connected') }}</span>
      <form method="post" action="{{ url_for('admin.logout') }}">
        <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
        <button type="submit" class="btn btn-console-danger btn-sm">{{ t('admin.log_out') }}</button>
      </form>
    </div>
  </div>
  <p class="subtitle mb-4">{{ t('admin.simulator.subtitle') }}</p>

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
      <button type="button" class="btn btn-console" id="load-example">{{ t('admin.simulator.load_example') }}</button>
      <button type="button" class="btn btn-console-primary" id="send-next" disabled>{{ t('admin.simulator.send_next') }}</button>
      <button type="button" class="btn btn-console-danger" id="reset-view" disabled>{{ t('admin.simulator.reset_view') }}</button>
    </div>
  </div>

  <div id="sim-alert"></div>

  <div class="sim-header" id="scenario-info">
    <h2 id="scenario-title">{{ t('admin.simulator.no_scenario') }}</h2>
    <p id="scenario-desc" class="description"></p>
    <div class="sim-badges" id="scenario-badges"></div>
  </div>

  <div class="sim-grid" id="chats-container"></div>

</div>

<script id="sim-data" type="application/json">{{ page_data|tojson }}</script>
<script>
(function () {
  'use strict';

  const DATA = JSON.parse(document.getElementById('sim-data').textContent);
  const STRINGS = DATA.strings || {};
  const POLL_INTERVAL_MS = 2000;
  const POLL_TIMEOUT_MS = 5 * 60 * 1000;
  const TERMINAL_STATUSES = new Set(['succeeded', 'failed', 'uncertain', 'closed_on_precedent', 'declined']);
  const INLINE_KINDS = new Set(['question', 'conversational', 'clarification', 'event_update']);
  const CHAT_TYPES = new Set(['private', 'group', 'supergroup']);
  const CHAT_KINDS = new Set(['message', 'event']);

  const groupsByChatId = {};
  (DATA.groups || []).forEach(function (group) { groupsByChatId[String(group.chat_id)] = group; });
  const registeredIdentities = new Set((DATA.users || []).map(function (user) { return String(user.telegram_identity); }));

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

  const state = { scenario: null, chats: [], chatsByKey: {}, queues: {}, runId: null };

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
      if (!sender) throw new Error(t('err_step_sender', { step: number }));
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
      },
      chats: chats,
      chatsByKey: chatsByKey,
      steps: steps,
    };
  }

  function loadScenario(raw) {
    const parsed = validateScenario(raw);
    state.scenario = parsed.scenario;
    state.chats = parsed.chats;
    state.chatsByKey = parsed.chatsByKey;
    state.queues = {};
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
    parsed.scenario.tags.forEach(function (tag) { badges.appendChild(el('span', 'sim-badge', tag)); });

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
      title.appendChild(el('span', null, formatTimestamp(step.timestamp)));
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
      button.disabled = false;
    });
  }

  function appendBubble(chatKey, kind, sender, text, stepNumber) {
    const messages = document.getElementById('messages-' + chatKey);
    const bubble = el('div', 'bubble' + (kind ? ' ' + kind : ''));
    const head = el('div', 'bubble-head');
    head.appendChild(el('span', 'sender', sender));
    head.appendChild(el('span', null, formatTimestamp(new Date().toISOString())));
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

  function buildRequest(chat, step) {
    if (chat.kind === 'event') {
      return { url: '/Event', body: { text: step.text, sender_identity: step.sender_identity } };
    }
    const chatId = chat.telegram_chat_id || step.sender_identity;
    const body = {
      text: step.text,
      sender_identity: step.sender_identity,
      // A unique id per run unless the scenario pins one: /Msg de-duplicates on
      // (sender, source_message_id), so a re-run must not be swallowed as a duplicate.
      source_message_id: step.source_message_id || ('sim-' + state.runId + '-' + step.step),
      conversation_id: 'telegram:' + chatId + ':main',
    };
    if (chat.telegram_chat_type) {
      body.telegram_chat_type = chat.telegram_chat_type;
      if (chat.telegram_chat_id) body.telegram_chat_id = chat.telegram_chat_id;
    }
    if (step.protocol_hint) body.protocol_hint = step.protocol_hint;
    return { url: '/Msg', body: body };
  }

  async function apiCall(method, url, identity, body) {
    const response = await fetch(url, {
      method: method,
      headers: { 'Content-Type': 'application/json', 'X-Identity': identity },
      body: body === undefined ? undefined : JSON.stringify(body),
      credentials: 'same-origin',
    });
    let payload = null;
    try { payload = await response.json(); } catch (error) { payload = null; }
    return { status: response.status, payload: payload };
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
        setBubbleText(bubble, header, t('network_error', { message: error.message }), true);
        return;
      }
      if (result.status !== 200 || !result.payload) {
        setBubbleText(bubble, header, errorMessage(result), true);
        return;
      }
      const job = result.payload;
      const body = jobBodyText(job);
      setBubbleText(bubble, body ? header + '\\n\\n' + body : header, jobStatusText(job), job.status === 'failed');
      if (TERMINAL_STATUSES.has(job.status)) return;
    }
    setBubbleText(bubble, header, t('poll_timeout', { minutes: POLL_TIMEOUT_MS / 60000 }), true);
  }

  async function sendNext(chatKey) {
    const queue = state.queues[chatKey];
    if (!queue || queue.length === 0) return;
    const chat = state.chatsByKey[chatKey];
    const step = queue.shift();
    updateGlobalState();

    appendBubble(chatKey, null, step.sender_name, step.text, step.step);
    const reply = appendBubble(chatKey, 'sys', t('system_label'), t('sending'), null);

    const request = buildRequest(chat, step);
    let result;
    try {
      result = await apiCall('POST', request.url, step.sender_identity, request.body);
    } catch (error) {
      setBubbleText(reply, t('network_error', { message: error.message }), null, true);
      return;
    }
    if (result.status >= 400 || !result.payload) {
      setBubbleText(reply, errorMessage(result), null, true);
      return;
    }

    const payload = result.payload;
    if (chat.kind === 'event') {
      const header = t('event_id', { event_id: payload.event_id });
      setBubbleText(reply, header, jobStatusText({ status: payload.status }), false);
      pollJob(payload.event_id, step.sender_identity, reply, header);
      return;
    }

    let header = t('taken_as', { kind: payload.taken_as });
    if (payload.duplicate) header += ' - ' + t('duplicate');
    if (INLINE_KINDS.has(payload.taken_as) && !payload.event_id) {
      setBubbleText(reply, payload.answer ? header + '\\n\\n' + payload.answer : header, null, false);
      return;
    }
    if (payload.event_id) header += ' - ' + t('event_id', { event_id: payload.event_id });
    const answer = payload.answer ? header + '\\n\\n' + payload.answer : header;
    const status = payload.status ? jobStatusText({ status: payload.status }) : null;
    setBubbleText(reply, answer, status, false);
    // Only the acknowledgment shape ("queued") means a job is running that GET /Job will
    // progress; a duplicate carries its final outcome, and a clarification that still names
    // an event is waiting on the operator, not on the job.
    if (payload.event_id && payload.status === 'queued') {
      pollJob(payload.event_id, step.sender_identity, reply, answer);
    }
  }

  // ---- example built from the live registrations ------------------------------------------

  function buildExample() {
    const humans = (DATA.users || []).filter(function (user) { return String(user.telegram_identity) !== DATA.bot_service_identity; });
    if (humans.length === 0) return null;
    const commander = humans.find(function (user) { return user.permission_level === 'commander'; }) || humans[0];
    const identity = String(commander.telegram_identity);
    const group = (DATA.groups || [])[0];

    const chats = [
      { key: 'commander_dm', kind: 'message', label: t('example_private_label'), telegram_chat_type: 'private', telegram_chat_id: identity },
    ];
    const steps = [];
    let stepNumber = 1;
    if (group) {
      chats.push({
        key: 'registered_group', kind: 'message', label: group.label || t('example_group_label'),
        telegram_chat_id: String(group.chat_id), telegram_chat_type: 'supergroup',
      });
      steps.push({ step: stepNumber++, chat: 'registered_group', sender_identity: identity, sender_name: identity, text: t('example_text_group') });
    }
    chats.push({ key: 'fence_sensors', kind: 'event', label: t('example_sensor_label') });
    steps.push({ step: stepNumber++, chat: 'fence_sensors', sender_identity: identity, text: t('example_text_event') });
    steps.push({ step: stepNumber++, chat: 'commander_dm', sender_identity: identity, sender_name: identity, text: t('example_text_private') });

    return {
      scenario: { id: 'EXAMPLE', title: t('example_title'), description: t('example_description'), tags: [] },
      chats: chats,
      steps: steps,
    };
  }

  // ---- wiring --------------------------------------------------------------------------------

  function loadFromText(text) {
    let raw;
    try {
      raw = JSON.parse(text);
    } catch (error) {
      showAlert(t('err_parse', { message: error.message }), true);
      return;
    }
    try {
      loadScenario(raw);
    } catch (error) {
      showAlert(error.message, true);
    }
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

  document.getElementById('load-pasted').addEventListener('click', function () {
    loadFromText(document.getElementById('paste-input').value);
  });
  document.getElementById('load-example').addEventListener('click', function () {
    const example = buildExample();
    if (!example) { showAlert(t('example_needs_user'), true); return; }
    document.getElementById('paste-input').value = JSON.stringify(example, null, 2);
    try { loadScenario(example); } catch (error) { showAlert(error.message, true); }
  });
  document.getElementById('send-next').addEventListener('click', function () {
    const key = nextChatKey();
    if (key !== null) sendNext(key);
  });
  document.getElementById('reset-view').addEventListener('click', function () {
    // View only: clears the cards and the queues. Nothing already sent is undone on the server.
    state.scenario = null; state.chats = []; state.chatsByKey = {}; state.queues = {}; state.runId = null;
    document.getElementById('chats-container').innerHTML = '';
    document.getElementById('scenario-title').textContent = t('no_scenario');
    document.getElementById('scenario-desc').textContent = '';
    document.getElementById('scenario-badges').innerHTML = '';
    document.getElementById('send-next').disabled = true;
    document.getElementById('reset-view').disabled = true;
    showAlert('', false);
  });
})();
</script>
"""
