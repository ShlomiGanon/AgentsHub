# AgentsHub — Telegram Bot UX Investigation Findings

Third read-only investigation pass, per `TELEGRAM_UX_FINDINGS_PLAN.md`, same
discipline as `docs/IMPROVES/DIAGNOSTIC_FINDINGS.MD` and
`docs/IMPROVES/UX_PREDICTABILITY_PLAN.MD`. Every finding is grounded in the
real, current source. This pass adds no new folder and moves nothing.

**Investigation date:** 2026-09-04

**Method note on live reproduction:** this environment has no interactive
Telegram client available — there is no way to actually open a chat with a
real Telegram bot and visually observe rendering (bidi text shaping, inline
keyboard layout on a phone screen, etc.) from here. Per the plan's own
instruction, this is stated explicitly wherever it matters (C.2 in
particular) rather than silently falling back to code-reading and implying
visual confirmation that didn't happen. Where a claim is fully decidable
from the code's own deterministic control flow (e.g., "is there a handler
for `/start`"), no live reproduction was attempted, since code reading
already gives a more certain answer than an ad hoc live check would.

---

## Executive Summary

| Priority | Item | Verdict |
|---|---|---|
| **Critical** | **C.1 (new finding)** — the reassuring async-ACK wording from the second pass's D.3 entry is dead code | **Confirmed cause**, found by careful control-flow reading. `bot/app.py`'s `_submit_and_format_message` returns at `if submission_result.job_id: return messages.text("status.async_ack", ...)` — the `elif submission_result.job_id:` guarding `"bot.job_queued"` ("...You'll hear back here once it's done.") two lines later is unreachable, since `job_id` is already known false to get there. **This corrects `UX_PREDICTABILITY_PLAN.MD`'s D.3 entry**, which quoted the unreachable text as what users see — the actual live message for every queued report/request is just `"Got it — your request is queued.\nTask ID: {id}"`, with no explicit promise of a follow-up message. |
| High | **A.1** — first-contact experience | **Confirmed cause.** No `/start` handler exists at all (`REGISTERED_COMMANDS = ("profile", "settings")`); `filters.TEXT & ~filters.COMMAND` means an unregistered slash command matches nothing — the bot sends **literally nothing** in response to `/start`, which is the first thing most Telegram users try with any new bot. A registered *or* unregistered user typing a plain sentence instead does get a real reply (an informative "not registered" message for unregistered users) — only the command path is silent. |
| High | **A.2** — command discoverability | **Confirmed cause.** No `setMyCommands`-equivalent call exists anywhere in `bot/` — Telegram's native "/" command menu is never populated. The only two real commands (`/profile`, `/settings`) are invisible in-client unless a user already knows them from documentation. |
| High | **C.2** — Hebrew rendering | **Confirmed cause**, via code reading (visual bidi rendering not independently confirmed — see method note). `"result.verdict": "תוצאה: {outcome}"` interpolates `event["outcome"]`, one of `VALID_OUTCOMES`'s fixed **English** enum strings (`succeeded`, `failed`, `uncertain`, ...) — never translated — so a Hebrew job-result message reads "תוצאה: succeeded," an English word inside an otherwise-Hebrew sentence. Compounds with the second pass's C.3 finding that `error.request_failed`'s Hebrew template splices in a raw English internal error string too. |
| High | **B.3** — multi-hold disambiguation | **Confirmed cause** for the one free-text-driven hold type. `event_data_hold` resolution (`api/routes.py` lines 205–215) scans *all* pending event-data holds, takes the first match by `(conversation_id, sender_identity)` in reverse order, and applies a free-text reply to it — silently — with no disambiguation if a sender has two reports simultaneously waiting on missing details in the same conversation. Approval/clarification holds are **not** affected — they resolve via inline-keyboard callback data that embeds the exact `event_id`, so they're unambiguous by construction regardless of how many are pending. |
| Medium-High | **B.1/B.2** — hold-resolution ergonomics | **Mostly already good news, one real gap found.** Both approval and clarification holds already use native Telegram inline keyboards (`InlineKeyboardButton`/`InlineKeyboardMarkup`, confirmed in `bot/transports.py`) — the plan's own suggested fix is already implemented for the two highest-frequency, highest-stakes actions. The one place still driven by fragile typed syntax is `/profile add\|edit` (seven pipe-separated fields in one command string) — a real but comparatively low-frequency (commander-only, infrequent) admin action, not the "single most frequent interaction" the plan worried buttons were missing from. |
| Medium | A.3 | Confirmed: no static `/help`; every "what can you do" question pays the full model-generation cost, no fast path exists. |
| Medium | D.1/D.2 | Inventoried: 5 of 8 notification kinds broadcast to *every* commander unconditionally (clarification, approval, uncertain-verdict, precedent-closure, no-match); 3 go to the specific reporter only. No mute/batch/digest mechanism exists anywhere in the code — confirmed gap, not a defect, with a concrete proposal. |
| Low-Medium | C.1 (formatting consistency, broader) | The header system itself (`[RESULT]`, `[NOTICE — ...]`) is consistently applied everywhere via one shared `format_header` function — genuinely disciplined. The one real inconsistency found (beyond the dead-code item above): the same identifier is labeled "Task ID" in the live ACK path and "Event {id}" (no "ID" suffix) elsewhere — a minor terminology drift, not a structural problem. **No emoji exist anywhere in the message catalog** — worth noting because the second pass's own A.3 proposal used a "⚠️" emoji, which would be the *first* emoji in the system and should be reconsidered against the existing bracketed-tag convention for consistency. |

**Recommended order to act on this document**, given what was actually
found: the C.1 dead-code correction first (it's a one-line control-flow fix
and directly affects what every queued report's ACK says), then A.1/A.2
(onboarding — cheap, high-impact, exactly as the plan predicted), then B.3
(multi-hold disambiguation), then C.2 (Hebrew outcome-word translation),
then the rest.

---

## Part B.2 — Hold-resolution UX

*(investigated first, per the plan's own Part E ordering)*

### B.2 — Check hold-resolution UX specifically

- **File(s)/function(s) examined:** `bot/interactions.py`
  (`format_approval_prompt`, lines 373–405; `push_approval_prompt`, lines
  408–412; `format_clarification_prompt`, lines 502–511;
  `push_clarification_prompt`, lines 514–519), `bot/transports.py`
  (`send_with_buttons`, lines 436, 492–496), `messages/en.py`
  (`header.approval_needed`, `approval.flagged`, `approval.ambiguous`,
  `approval.approve`, `approval.reject`, `header.clarification_needed`,
  `clarification.prompt`).
- **What the code actually does:** Both hold types are delivered as one
  Telegram message with an attached **inline keyboard**
  (`send_with_buttons` → `InlineKeyboardMarkup`), not typed commands:
  - **Approval** (`format_approval_prompt`): text is `"[APPROVAL
    NEEDED — please reply]\n\nProtocol flagged for approval:
    {protocol_name}\nRisk: {risk_level} ({risk_reason})\n\nShould this
    run?"` for a flagged protocol, with two buttons, "Approve" / "Reject"
    (`callback_data` encodes `approve:{event_id}:approved` /
    `...rejected`); for an ambiguous selection, one button per candidate
    protocol name instead.
  - **Clarification** (`format_clarification_prompt`): text is
    `"[CLARIFICATION NEEDED — please reply]\n\n{raw_text}\n\n{field}"`
    (the unresolved field, e.g. `classification`), with one button per
    available classification choice.
  - Both are pushed to **every registered commander**
    (`list_commander_chat_ids()`), not just the original reporter.
  - Button layout: each button is placed on its **own row**
    (`InlineKeyboardMarkup([[button] for ...])` — one-per-row, never
    grouped side by side). For a 2-choice approval this is a compact,
    perfectly phone-appropriate 2-row keyboard. For a clarification with
    many available classification choices, this becomes a tall,
    one-column list — still tappable, but pushes the keyboard further down
    the screen the more choices there are.
- **Verdict:** not an issue for the core mechanism (buttons, not typed
  syntax, already exist and work as the plan hoped) — contributing factor
  for the many-choices clarification layout specifically.
- **Evidence:** Code/message-catalog reading of the functions and strings
  named.
- **Telegram mockup — approval (flagged protocol), exactly as sent today:**
  ```
  [APPROVAL NEEDED — please reply]

  Protocol flagged for approval: dispatch_response
  Risk: high (matches historical fire-response pattern)

  Should this run?
  [ Approve ]
  [ Reject ]
  ```
- **Proposed fix:** No change needed for the 2-button approval case. For
  clarification prompts with more than ~4–5 candidate classifications,
  consider a 2-column grid (`InlineKeyboardMarkup` supports multiple
  buttons per row — this would be a one-line change to
  `push_clarification_prompt`'s list-comprehension shape) to keep the
  keyboard compact on a phone screen; not proposing a specific threshold
  without knowing real classification-list sizes in practice.
- **Suggested regression test:** A snapshot test on
  `push_clarification_prompt`'s button layout once (if) a grid change is
  made, asserting row/column count for a given number of choices.
- **Priority:** medium.

---

## Part A — Discoverability and onboarding

### A.1 — Inventory what a brand-new user sees on first contact ✅ COMPLETED

> **Status: implemented.** `CommandHandler("start", ...)` added, branching on
> registration exactly as proposed. See `docs/progress.md` (2026-09-04,
> "CRITICAL_FIXES_PLAN item 1: onboarding (/start + command menu)").

- **File(s)/function(s) examined:** `bot/app.py`
  (`REGISTERED_COMMANDS`, line 35; `register_handlers`, lines 472–481),
  `bot/interactions.py` (`_unregistered_message`, lines 157–158;
  `resolve_caller`, lines 161–173), `messages/en.py` (`auth.unregistered`).
- **What the code actually does:** `register_handlers` registers exactly
  three handlers: `CommandHandler("profile", ...)`,
  `CommandHandler("settings", ...)`, `CallbackQueryHandler(...)` (button
  presses), and `MessageHandler(filters.TEXT & ~filters.COMMAND, ...)`
  (free text, explicitly excluding anything starting with `/`). There is
  **no `CommandHandler("start", ...)`** and no catch-all for an unknown
  command. In python-telegram-bot, an update matching none of the
  registered handlers simply produces no response — confirmed by reading
  the handler list exhaustively; this isn't a matter of interpretation.
  So: a brand-new user's first, most natural action — sending `/start`,
  Telegram's own universal bot convention — gets **silence**, regardless of
  whether they're registered or not.
  By contrast, a user who instead types an ordinary sentence *does* get a
  real reply: if unregistered, `"You are not a registered user of this
  system (identity: {identity}). An administrator must add you before you
  can use this bot."` — a genuinely useful message (explains the situation,
  gives the exact identity string an admin needs to register them). If
  already registered, their message is processed normally.
- **Verdict:** confirmed cause.
- **Evidence:** `bot/app.py:35,472-481` (the complete, exhaustive handler
  list); `messages/en.py`'s `auth.unregistered` text quoted verbatim above.
- **Telegram mockup — today, for `/start`:** *(nothing — no message sent)*
- **Telegram mockup — today, for a plain first message from an
  unregistered user:**
  ```
  You are not a registered user of this system (identity: 7369649810).
  An administrator must add you before you can use this bot.
  ```
- **Proposed fix:** Add a `CommandHandler("start", ...)` (and, generously,
  treat any other unrecognized `/command` the same way via a fallback
  handler) that replies with one short, role-agnostic orientation message
  — it can't yet know the caller's role until `resolve_caller` runs, so it
  should call that first and branch: unregistered → the existing
  `auth.unregistered` text (already good, keep it); registered → a new,
  short greeting naming the system (`PROFILE_NAME`) and pointing at the
  existing model-generated "what can you do" capability (or the new static
  `/help` from A.3, once built) rather than restating the whole capability
  catalog inline.
- **Telegram mockup — proposed, registered viewer:**
  ```
  Hi — this is AgentsHub (For Tests). Ask me a question, report
  something, or request an action — just type it. Send /help for a
  quick reference.
  ```
  One short paragraph, no bullet list, no wall of capability text — the
  full catalog stays available on request (A.3), matching this whole
  series' "short first, detail on demand" theme.
- **Suggested regression test:** Send `/start` as both a fresh unregistered
  identity and a registered one; assert a non-empty reply in both cases
  (today: assert the *absence* of a reply, to document the gap until
  fixed).
- **Priority:** high.

### A.2 — Inventory available commands vs. what's discoverable ✅ COMPLETED

> **Status: implemented.** `set_my_commands` is now called in `_post_init`
> with `/start`, `/profile`, `/settings` and their descriptions. See
> `docs/progress.md` (2026-09-04, "CRITICAL_FIXES_PLAN item 1: onboarding
> (/start + command menu)").

- **File(s)/function(s) examined:** `bot/app.py` (full file, grepped for
  `setMyCommands`/`BotCommand` — zero matches), `register_handlers`
  (same as A.1).
- **What the code actually does:** No call anywhere in `bot/` sets
  Telegram's native bot-command menu (the `/`-tap-to-see-commands UI every
  Telegram client provides, backed by the `setMyCommands` Bot API method,
  which python-telegram-bot exposes as `Bot.set_my_commands`). The only two
  real commands, `/profile` and `/settings`, are therefore invisible in
  Telegram's own command picker — a user only finds them by reading
  `README.md` or being told directly. Both commands are open to any
  registered user at the handler level; permission is enforced *inside*
  each handler (`_resolve_caller_or_refuse` + `check_permission`) rather
  than by hiding the command from a viewer's menu — so today's behavior is
  "shown-but-rejected" in spirit, except there's no menu to show it in at
  all, since no menu exists for anyone.
- **Verdict:** confirmed cause.
- **Evidence:** Exhaustive grep of `bot/app.py` for command-menu-related
  API calls (none found); `register_handlers`'s complete handler list
  (A.1's citation).
- **Telegram mockup — today:** tapping the "/" icon in a Telegram chat with
  this bot shows Telegram's default empty/generic state — no commands
  listed.
- **Proposed fix:** Call `Application.bot.set_my_commands([...])` once
  during `_post_init` (`bot/app.py`'s `register_handlers`, alongside the
  existing `deps.api_client.start()` call) with the two real commands and
  short descriptions (`BotCommand("profile", "View or edit the active
  profile"), BotCommand("settings", "View or change live settings")`).
  Since permission is still enforced per-call (a viewer tapping `/settings`
  in the menu still gets the existing refusal message), this doesn't need
  a role-conditional menu — one shared menu is simplest and matches "a
  visible-but-rejected command is a worse UX than not showing it" only
  when there's no explanation; here the refusal message is already
  informative, so a shared menu is a reasonable, simple starting point
  (flagging the per-role-menu alternative as a possible future refinement,
  not proposing it now — python-telegram-bot supports per-scope commands,
  but that's more machinery than this gap needs to start with).
- **Telegram mockup — proposed:** tapping "/" shows:
  ```
  /profile   View or edit the active profile
  /settings  View or change live settings
  ```
- **Suggested regression test:** A test asserting `set_my_commands` (or
  equivalent) is called exactly once during bot startup with the expected
  command list — `PTBTelegramClient`/`register_handlers` would need a thin
  seam for this to be testable without a real Telegram connection (check
  existing test doubles in `tests/bot_fakes.py`/`tests/test_bot_transports.py`
  for the established pattern before adding one).
- **Priority:** high.

### A.3 — Check help/self-description quality for a first-timer specifically

- **File(s)/function(s) examined:** `bot/app.py` (`REGISTERED_COMMANDS`,
  confirming no `/help`), `orchestrator/flows.py` (`process_message`'s
  `"conversational"` branch → `answer_conversationally`, a full model call
  — cross-referenced with the second pass's C.2 entry on capability-answer
  grounding).
- **What the code actually does:** There is no static `/help` command (not
  in `REGISTERED_COMMANDS`). Every "what can you do" / "what is this"
  style question is classified as `conversational` intent and answered by
  a genuine model call (`answer_conversationally`) grounded in the
  role-filtered capability catalog (`orchestrator/capabilities.py`) — this
  is accurate and already role-aware, but it pays a full multi-second model
  round-trip (consistent with the ~2.4–3.4s single-call latencies measured
  live in the second pass's C.1) for something a first-time user
  reasonably wants to see instantly, and its length/shape varies by
  generation rather than being a fixed, predictable size.
- **Verdict:** confirmed cause.
- **Evidence:** `bot/app.py`'s command list (A.1/A.2's citation);
  `orchestrator/flows.py`'s intent-routing table (`"conversational"` →
  `answer_conversationally`, no static shortcut anywhere in that
  dispatch).
- **Telegram mockup — today**, asking "what can you do":
  *(a "The model is thinking..." status message, then an edit to)* a
  full, model-generated paragraph — length and exact wording vary run to
  run.
- **Proposed fix:** Add a static `/help` command (or fold into A.1's
  proposed `/start`/orientation flow) that returns a fixed, catalog-driven
  — but **not model-generated** — short text built directly from
  `visible_capabilities(level)` (already a pure, role-filtered function per
  the second pass's E.1 entry) with a simple template, not a full sentence
  generation: instant, deterministic, and correctly role-scoped without
  paying for a model call. The existing model-generated conversational
  answer stays available for genuinely open-ended "what can you do for
  X" questions — this isn't proposing to remove that path, only adding a
  faster, predictable one for the common, generic case.
- **Telegram mockup — proposed** (`/help`, viewer role):
  ```
  You can: report events, ask about status/history, request an action,
  and just chat. Try: "smoke near gate 3" or "what's the last event?"
  ```
  Instant (no "thinking" status needed at all), one short paragraph.
- **Suggested regression test:** `/help` reply time should be near-zero
  (no model call) — assert no `stage_context`/model invocation fires for
  this specific handler; assert the reply's content matches
  `visible_capabilities(level)` exactly for both roles (same assertion
  style as the second pass's C.2 suggested test).
- **Priority:** medium.

---

## Part B — Command and interaction ergonomics

### B.1 — Inventory every place the user must type exact syntax

- **File(s)/function(s) examined:** `bot/app.py`
  (`_parse_protocol_write_command`, `_on_profile_command`,
  `_on_settings_command`), `messages/en.py` (`command.profile_usage`,
  `command.settings_usage`, `protocol.expected_fields`, `settings.*`
  refusal messages).
- **What the code actually does:** Two typed-syntax surfaces exist:
  - `/settings set <retry_count|risk_threshold|lookback_window_days>
    <value>` — three words, one value. Errors are specific and
    field-named (`"Refused: 'retry_count' must be a whole number, got
    {value}."`) — a genuinely good error message, not generic.
  - `/profile add <name> | <description> | <agents,csv> | <tools,csv> |
    <expected_output> | <criticality> | <true/false>` — **seven
    pipe-separated fields in a single command argument string.** This is
    the one place this pass found real, concrete friction: on a phone
    keyboard, with no autocomplete, no per-field prompting, and no partial
    recovery (a single misplaced `|` anywhere requires retyping the whole
    command from scratch), this is meaningfully harder than the
    single-value settings command. The error message itself is accurate
    and complete (`protocol.expected_fields`, quoted in full in the
    Executive Summary), but accuracy doesn't fix the retyping cost.
  - Buttons already cover the two highest-frequency actions (B.2) — this
    typed-syntax surface is commander-only and, per this codebase's own
    apparent usage pattern (protocol editing is a rare, deliberate admin
    action, not a routine one), lower-frequency than hold resolution.
- **Verdict:** contributing factor (real friction, correctly lower
  priority than the plan initially worried about, since the highest-
  frequency action is already button-driven).
- **Evidence:** Code/message-catalog reading of the functions and strings
  named.
- **Proposed fix:** Not a button (this is free-form multi-field data
  entry, not a discrete choice) — the natural fit is a short guided
  multi-step conversation: `/profile add` with no arguments starts a
  stateful prompt sequence ("Name?" → "Description?" → ... one message per
  field), reusing the existing per-conversation state the bot already
  tracks for `event_data_hold` replies as a precedent for "the bot
  remembers it's mid-way through collecting fields from this sender." This
  is a real feature addition, not a one-line fix — sizing it accordingly.
- **Telegram mockup — proposed, first step:**
  ```
  Adding a new protocol. Name?
  > (user types: dispatch_response_v2)
  Description?
  ...
  ```
- **Suggested regression test:** Once built: start `/profile add` with no
  arguments, supply each field across separate messages, assert the final
  protocol is created identically to the equivalent single-line
  pipe-separated form; assert an in-progress add can be cancelled cleanly.
- **Priority:** medium.

### B.2 — *(see above — investigated first per plan ordering)*

### B.3 — Check multi-conversation / multi-event disambiguation ✅ COMPLETED

> **Status: implemented — the "fail loudly" fallback of the two proposed
> options, not the "queue a second hold" option (b).** `apply_event_data_reply`
> now detects more than one matching pending hold up front and returns their
> event IDs instead of silently applying the reply to the newest one; the
> `/Msg` handler turns that into an explicit "I can't tell which report this
> is about" clarification reply. See `docs/progress.md` (2026-09-04,
> "CRITICAL_FIXES_PLAN item 7: ambiguous event-data replies fail loudly
> instead of guessing").

- **File(s)/function(s) examined:** `api/routes.py` (lines 199–216, the
  `event_data_hold` matching loop, quoted in full below), `bot/app.py`
  (`_on_text_message`, `conversation_id = f"telegram:{chat_id}:{thread_id
  if thread_id is not None else 'main'}"`), `bot/interactions.py`
  (approval/clarification callback-data encoding, `build_callback_data`/
  `build_clarification_callback_data` — both embed `event_id` directly).
- **What the code actually does:**
  ```python
  matching_event_data_hold = False
  if conversation_id is not None:
      for pending_hold in reversed(ctx.deps.persistence.list_held_events("event_data")):
          pending_event = ctx.deps.persistence.fetch_event(pending_hold["event_id"])
          if (
              pending_event is not None
              and pending_event.get("conversation_id") == conversation_id
              and pending_event.get("sender_identity") == caller_identity
          ):
              matching_event_data_hold = True
              break
  ```
  This scans **every** pending `event_data` hold system-wide, in reverse
  (most-recent-first) order, and takes the **first** one matching this
  sender's `conversation_id` — with no check for whether more than one
  matches. If a commander reports two things in a row in the same chat and
  both end up `waiting_for_event_data` (a real, plan-first-pass-confirmed
  reachable state) before either is answered, a single free-text reply
  meant for the *first* report would silently be applied to the *second*
  (most recent) one instead — with no message telling the user which event
  their reply was actually routed to.
  By contrast, **approval and clarification holds are immune to this
  entirely** — their inline-keyboard `callback_data` embeds the specific
  `event_id` directly (`approve:{event_id}:{choice}`,
  `clarify:{event_id}:{classification}`), so a button press is always
  unambiguous regardless of how many holds are pending simultaneously.
  `conversation_id` itself is correctly scoped per Telegram chat+thread
  (confirmed: `f"telegram:{chat_id}:{thread_id or 'main'}"`), so
  cross-*chat* ambiguity (the plan's "group plus private chat" scenario)
  doesn't arise from this mechanism — the risk is specifically two pending
  items *within the same* chat/thread.
- **Verdict:** confirmed cause (for the one free-text-driven hold type).
- **Evidence:** `api/routes.py` lines 199–216 quoted verbatim above;
  cross-referenced against the button-based holds' callback-data encoding,
  which structurally avoids the same risk.
- **Telegram mockup — today, the ambiguous case:** a commander who has two
  pending `waiting_for_event_data` reports in the same chat replies "north
  sector, moderate severity" with no indication which report it updates —
  the reply is silently applied to whichever is more recent, and the
  bot's own reply just confirms an update happened, not to *which* event
  (per the second pass's own message-composition findings for
  `event_update`/`taken_as` responses).
- **Proposed fix:** When more than one pending `event_data` hold matches
  `(conversation_id, sender_identity)`, don't silently pick one — either
  (a) require the reply to name which event (echoing the specific event's
  short ID or a 1-based ordinal in each hold's original question, e.g.
  append `"(re: evt-2f9a)"` to each `event_data_hold` notification so a
  reply can reference it), or (b) simpler and more in keeping with this
  codebase's existing button-based pattern: only ever allow **one**
  in-flight `event_data_hold` per `(conversation_id, sender_identity)` at a
  time — queue a second one's clarifying question until the first is
  answered, so the ambiguity can't arise at all. (b) is simpler to
  implement and matches how the rest of this system already avoids
  ambiguity by construction (buttons) rather than by disambiguation UI.
- **Suggested regression test:** Create two events for the same sender in
  the same conversation, both `waiting_for_event_data`; reply once; assert
  the reply is applied to the intended (first) event, not silently to the
  second — this test would currently fail with option (a) unimplemented,
  demonstrating the gap; with (b), the second hold's question should be
  observably deferred rather than the reply misapplied.
- **Priority:** high.

---

## Part D — Notification load and control

### D.1 — Inventory what generates a proactive notification

- **File(s)/function(s) examined:** `bot/background_services.py`
  (`dispatch_notification`, lines 37–72, and each `notify_*`/`deliver_*`
  function it calls — the complete, exhaustive dispatch table),
  `persistence/sqlite_store.py` (lines 79–84, the outcome→notification-kind
  mapping).
- **What the code actually does — the complete inventory:**

  | Kind | Recipients | Trigger |
  |---|---|---|
  | `clarification_hold` | every commander (`list_commander_chat_ids`) | classification unresolved |
  | `approval_hold` | every commander | flagged or ambiguous protocol |
  | `event_data_hold` | the specific reporter (reply) | protocol step missing event field(s) |
  | `uncertain_verdict` | every commander | final verdict `uncertain` |
  | `precedent_closure` | every commander | auto-closed on a resolved precedent |
  | `no_match_notice` | every commander | no protocol matched |
  | `job_finished` | the specific reporter (reply) | `succeeded`/`declined`/`uncertain`/`closed_on_precedent`/`no_match_protocol` (note: fires *alongside* the more specific commander-broadcast kind for the last three, per `persistence/sqlite_store.py`'s mapping — confirmed in the second pass's B.2 entry for `uncertain` specifically) |
  | `job_failed` | the specific reporter (reply) | `failed` |

  **5 of 8 kinds broadcast to every registered commander, unconditionally,
  with no batching** — in a deployment with several commanders and a busy
  sensor/reporting volume, every hold, every uncertain verdict, every
  auto-closure, and every no-match all fan out to everyone's phone
  individually, every time.
- **Verdict:** confirmed inventory (not itself a defect — feeds D.2's
  judgment call on whether it's a problem worth addressing).
- **Evidence:** `dispatch_notification`'s complete dispatch table, read
  exhaustively; `persistence/sqlite_store.py` lines 79–84 for the
  outcome-to-kind mapping.
- **Priority:** N/A (inventory item, feeds D.2).

### D.2 — Check for any user-side control over notification volume

- **File(s)/function(s) examined:** Same files as D.1, plus a
  repository-wide grep for `mute`/`quiet`/`digest`/`batch` in `bot/` and
  `orchestrator/` — no matches beyond this document's own drafting.
- **What the code actually does:** No mechanism exists anywhere in the
  code for a commander to mute, batch, filter, or otherwise control
  notification volume. Every one of D.1's 5 broadcast-to-all-commanders
  kinds fires as an individual message the instant it's generated,
  regardless of how many commanders are registered or how busy the
  deployment is.
- **Verdict:** needs more investigation / feature gap (per the plan's own
  framing — not a defect in existing code, since nothing promises this
  today).
- **Evidence:** Exhaustive grep confirming absence; D.1's dispatch table.
- **Proposed fix** (concrete, Telegram-appropriate, not implemented): a
  `/quiet` toggle command (would need to be added to `REGISTERED_COMMANDS`
  and to A.2's proposed command menu) that sets a per-commander flag
  (a new column or small side-table keyed by `telegram_identity`,
  analogous to how `NotificationCursorStore` already tracks per-deployment
  delivery state) suppressing only the **low-stakes** broadcast kinds
  (`precedent_closure`, `no_match_notice` — both explicitly headed
  "no reply needed" already, per their own header text) while never
  suppressing anything requiring a reply (`clarification_hold`,
  `approval_hold`) or anything safety-relevant (`uncertain_verdict`) —
  this distinction already exists implicitly in the header wording
  (`"...no reply needed"` vs `"...please reply"`), so the toggle's scope
  follows a line the codebase has effectively already drawn. A periodic
  digest (batching suppressed low-stakes notices into one summary message
  every N minutes) is a reasonable second step but meaningfully more
  machinery (a scheduler, a batching queue) — proposing the simpler mute
  toggle first.
- **Telegram mockup — proposed:**
  ```
  /quiet on
  Low-priority notices (auto-closures, no-match) muted. Holds and
  uncertain verdicts still always notify you.
  ```
- **Suggested regression test:** With `/quiet on` set for one commander,
  trigger a `precedent_closure` and an `approval_hold`; assert the muted
  commander receives only the approval hold, while an unmuted commander
  receives both.
- **Priority:** medium.

---

## Part C — Visual and tonal consistency

### C.1 — Audit formatting consistency across message types ✅ COMPLETED

> **Status: all three findings closed.** Finding 1 (headers) needed no
> action — verdict was "not an issue." Finding 3 (dead code) was fixed by
> CRITICAL_FIXES_PLAN item 5 (2026-09-04). Finding 2 (the emoji-convention
> question) is now a **closed decision, dated 2026-09-04: no emoji, anywhere,
> in any user-facing message the bot sends**, in either language — not to
> be re-opened in future findings passes. A full-catalog audit (both
> `messages/en.py` and `messages/he.py`, plus every `send_text`/
> `send_reply`/`send_with_buttons` call site in `bot/`, confirming none
> passes a literal string bypassing the catalog) found zero emoji already
> present in actual source; the `⚠️`/mockup symbols referenced below and
> in `UX_PREDICTABILITY_PLAN.MD`'s A.3 entry were confirmed to be
> illustrative-only and were never implemented in code. A regression test
> (`test_no_emoji_in_any_catalog_message` in `tests/test_messages.py`)
> mechanically enforces this decision going forward by scanning both
> catalogs for emoji Unicode ranges. See `docs/progress.md` (2026-09-04,
> "Closed decision: no emoji in any user-facing message").

- **File(s)/function(s) examined:** `bot/interactions.py` (every
  `format_*` function — `format_job_result`, `format_approval_prompt`,
  `format_clarification_prompt`, `format_uncertain_verdict_notice`,
  `format_no_match_notice`; `format_header`/`_HEADER_KEYS`),
  `bot/background_services.py` (`format_precedent_closure_notice`),
  `messages/en.py` (full-file grep for emoji/non-ASCII symbol usage),
  `bot/app.py` (`_submit_and_format_message`, lines 152–189 — the dead-code
  finding).
- **What the code actually does, three separate findings:**
  1. **Headers are genuinely consistent.** Every notice type routes
     through one shared `format_header(kind, catalog)` /
     `_HEADER_KEYS` dict, producing a uniform `[BRACKETED TAG]` convention
     (`[RESULT]`, `[RUN FAILED]`, `[DECLINED]`, `[NOTICE — ... — no reply
     needed]`, `[APPROVAL NEEDED — please reply]`, etc.) — this is a real,
     disciplined, single-source-of-truth pattern, not something that grew
     inconsistently across the SPEED_PLAN/IMPROVE-A–E history the plan
     worried about.
  2. **No emoji exist anywhere in the message catalog** (confirmed via a
     full Unicode-range scan of `messages/en.py` — zero matches). This
     matters as a **correction to this series' own second document**:
     `UX_PREDICTABILITY_PLAN.MD`'s A.3 entry proposed a template using
     "⚠️" — that would be the *first* emoji introduced anywhere in this
     system's user-facing text, breaking the existing plain-bracketed-tag
     convention rather than following it. Flagging this back rather than
     silently letting two documents in the same series disagree: either
     adopt emoji deliberately and consistently everywhere (a bigger,
     separate decision), or restate that proposal using the existing
     `[BRACKETED TAG]` convention (e.g. `[UNRESOLVED — fire report, North
     Sector, 4 min ago]`) for consistency with everything else in the
     system today.
  3. **Confirmed dead code, corrects the second pass's D.3 entry**: in
     `bot/app.py`'s `_submit_and_format_message` (lines 152–189):
     ```python
     if submission_result.job_id:
         return messages.text("status.async_ack", task_id=submission_result.job_id), submission_result

     lines = [messages.text("bot.taken_as", kind=submission_result.kind)]
     if submission_result.awaiting_approval:
         lines.append(messages.text("bot.waiting_approval"))
     elif submission_result.job_id:
         lines.append(messages.text("bot.job_queued", job_id=submission_result.job_id))
     return "\n".join(lines), submission_result
     ```
     The first `if` returns unconditionally whenever `job_id` is truthy.
     The `elif submission_result.job_id:` three lines later can therefore
     **never** be true — by the time control reaches it, `job_id` is
     already known falsy. This means `"bot.job_queued"` (`"Job ID:
     {job_id}. You'll hear back here once it's done."` — the text
     `UX_PREDICTABILITY_PLAN.MD`'s D.3 entry quoted as evidence that the
     ACK "already sets a clear expectation of a follow-up") **is
     unreachable**. Every real queued report/request instead gets
     `"status.async_ack"`: `"Got it — your request is queued.\nTask ID:
     {task_id}"` — which does **not** explicitly promise a follow-up
     message. The `bot.taken_as`/`bot.job_queued` combination is only ever
     reachable in the narrower case where `job_id` is falsy but
     `awaiting_approval` is true (an immediate approval hold with no
     queued job yet) — a real but different, narrower scenario than "every
     queued report."
     This also explains the "Task ID" vs "Job ID" vs "Event {id}" (no "ID"
     suffix, `notice.uncertain`) three-way labeling drift noted in the
     Executive Summary: it isn't three live, inconsistently-worded paths —
     it's one live path ("Task ID") plus one dead path ("Job ID") plus one
     unrelated later-stage message (bare "Event").
- **Verdict:** confirmed cause for findings 2 and 3; "not an issue" for
  finding 1 (the header system). Explicitly noting, per the plan's own
  allowance, that "is this visually jarring" for finding 1/2 is inherently
  a bit subjective — the concrete facts (no emoji exist; headers are
  centrally sourced) are not in question, only how much that matters is a
  judgment call.
- **Evidence:** All three findings are grounded in direct code/text
  reading, quoted in full above; finding 3 is deterministic control-flow
  analysis, not something a live reproduction could show more certainly
  than the code itself already does.
- **Proposed fix:** Finding 2: pick one convention deliberately (recommend
  keeping the existing no-emoji, bracketed-tag style, and revising
  `UX_PREDICTABILITY_PLAN.MD`'s A.3 proposal to match it, rather than
  introducing emoji as a one-off). Finding 3: delete the unreachable
  `elif`/`bot.job_queued` branch (dead code cleanup) **and** decide
  whether `"status.async_ack"`'s actual live wording should be strengthened
  to include the "you'll hear back here" reassurance that was
  (incorrectly) assumed already present — this is the single most direct,
  concrete action item this whole three-document series has produced,
  since it's a one-string catalog edit plus a small dead-code removal, not
  a design question.
- **Telegram mockup — proposed fix for finding 3** (merging the
  unreachable text's good wording into the one that's actually live):
  ```
  Got it — your request is queued.
  Task ID: evt-2f9a. You'll hear back here once it's done.
  ```
- **Suggested regression test:** Submit a report, assert the immediate
  bot reply text matches the (revised) `status.async_ack` string exactly
  and contains an explicit follow-up promise — this test would have caught
  the original gap immediately, since `bot.job_queued`'s text was never
  reachable to begin with.
- **Priority:** critical (finding 3 — small, concrete, and corrects a
  previous document in this series); low (finding 1); medium (finding 2 —
  a consistency decision worth making deliberately rather than by accident
  across documents).

### C.2 — Check Hebrew rendering specifically

- **File(s)/function(s) examined:** `messages/he.py` (`result.verdict`,
  `notice.uncertain`, `error.request_failed` — cross-referenced against
  `messages/en.py`'s equivalents and `history/event_pipeline.py`'s
  `VALID_OUTCOMES`).
- **What the code actually does:**
  - `"result.verdict": "תוצאה: {outcome}"` — the surrounding sentence is
    properly Hebrew, but `{outcome}` is filled from `event["outcome"]`,
    always one of `VALID_OUTCOMES`'s fixed **English** strings
    (`succeeded`, `failed`, `uncertain`, `closed_on_precedent`, `declined`,
    `no_match_protocol`) — this value is never passed through any
    translation table anywhere in the codebase (confirmed: no
    outcome-to-Hebrew mapping exists in `messages/he.py` or elsewhere).
    So a Hebrew-language deployment's job-result message literally reads
    `"תוצאה: succeeded"` — one English word embedded in an otherwise fully
    Hebrew sentence.
  - This is the **same class of bug** as the second pass's already-confirmed
    C.3 finding (`error.request_failed`'s Hebrew template splicing in a raw
    English internal validation string) — two independent instances of the
    identical mechanism (an English-only value interpolated into a
    Hebrew template with no translation step), suggesting this is a
    systemic gap (nothing in this codebase's message-composition pattern
    currently translates *interpolated values*, only the surrounding
    template text) rather than two unrelated one-offs.
  - `notice.uncertain`'s Hebrew template (`"...אירוע {event_id}
    הסתיים..."`) embeds an LTR alphanumeric hex ID mid-RTL-sentence — a
    structurally real bidi-mixing case (grounded in the literal template
    text), but **this pass could not visually confirm how Telegram's
    client actually renders that specific mix** — no interactive Telegram
    client is available in this environment (per this document's opening
    method note). Recording this as the concrete, unverified risk it is,
    not as a confirmed rendering defect.
- **Verdict:** confirmed cause for the untranslated-outcome-word finding
  (fully decidable from code alone — no rendering ambiguity involved, it's
  a plain string-interpolation fact); needs more investigation for the
  bidi-rendering question specifically (code confirms the *structural*
  risk; visual confirmation wasn't possible here).
- **Evidence:** `messages/he.py`'s `result.verdict` text quoted verbatim;
  `history/event_pipeline.py`'s `VALID_OUTCOMES` frozenset (six English
  strings, no Hebrew equivalents defined anywhere); cross-reference to the
  second pass's C.3 entry for the identical-mechanism `error.request_failed`
  case.
- **Telegram mockup — today, Hebrew job result:**
  ```
  [RESULT]

  תוצאה: succeeded

  מה בוצע:
  - reference_agent: ...
  ```
- **Proposed fix:** Add a small outcome-word translation table (six
  entries, matching `VALID_OUTCOMES` exactly, one per supported language)
  and route `event["outcome"]` through it wherever it's interpolated into
  a user-facing string (`result.verdict` at minimum; audit
  `_job_payload`/`format_job_result`'s other outcome-adjacent call sites
  for the same gap while making this change, since the mechanism is
  generic). This is the same structural fix the second pass's C.3 entry
  already proposed for `error.request_failed` — implementing both together
  (one shared translation helper) is more efficient than fixing them as
  two separate patches.
- **Telegram mockup — proposed, Hebrew:**
  ```
  [RESULT]

  תוצאה: הצליח
  ```
- **Suggested regression test:** With `DEFAULT_LANGUAGE = "he"`, submit an
  event to each of the six `VALID_OUTCOMES`; assert no message contains
  any of the six raw English outcome words as a literal ASCII substring
  (a mechanical, reliable check — doesn't require visual/manual review, so
  not weakening this to "manual UX review" is deliberate: this specific
  claim is fully assertable). For the bidi-mixing question specifically:
  "manual UX review" (per the plan's own allowance) — assert only that the
  event ID is present as a literal substring, since automated bidi-
  rendering correctness isn't practically assertable from this
  environment.
- **Priority:** high (untranslated outcome word — mechanical, fixable,
  cheap); needs more investigation (bidi mixing — flag for a manual check
  with a real Telegram client, not resolvable from here).

---

## Confirmation: no files outside this document were changed

- `git status --porcelain` at the end of this pass:
  ```
  ?? docs/IMPROVES/
  ```
  This reflects the entire `docs/IMPROVES/` folder as still-untracked (it
  was created, and its contents added, across this and the previous pass;
  no prior pass's commit exists in this repository's history to diff
  against). Within it, this pass added exactly one new file,
  `TELEGRAM_UX_FINDINGS.MD`; `DIAGNOSTIC_FINDINGS.MD` and
  `UX_PREDICTABILITY_PLAN.MD` are unchanged from the previous pass (not
  re-opened or re-written in this pass, only read/quoted from where cited
  above).
- No new folder was created and nothing was moved in this pass, per the
  plan's own note that this document adds no filesystem change beyond
  itself.
- No live reproduction server was started this pass (all findings were
  fully decidable from code reading, as noted throughout — several items
  explicitly record where a live Telegram round-trip would have been
  preferred but wasn't feasible in this environment, per the plan's own
  instruction to say so rather than silently substitute code-reading).
- No source file in this repository was created, edited, or deleted during
  this pass other than this one document.
