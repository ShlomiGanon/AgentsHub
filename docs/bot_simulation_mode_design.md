# Bot Simulation-Mode Design

A dedicated, second bot process that lets the admin simulator's scripted
persona traffic flow through the real Telegram bot's own decision-making
code — its message handlers and its background loops — with only the
literal Telegram network legs (inbound delivery, outbound send) replaced by
stubs, and only ever for simulation-reserved identities. Planning only; no
implementation yet, per explicit instruction.

This document assumes familiarity with `docs/profile_simulations_design.md`
(the underlying `SimulationPersona`/`SimulationGroup`/`SimulationScenario`/
`SimulationRoster` mechanism, the reserved-ID scheme, and
`materialize_simulation()`) and with `docs/work_process.md`'s record of the
investigation that led here (§§10–13). Nothing in that mechanism changes —
this document is about how a materialized scenario's *message* steps get
delivered, not how they're built.

---

## 1. Research findings (current codebase, and the PTB library underneath it)

### 1.1 The bot process today

`bot/app.py`'s `main()` is a separate OS process from `api/app.py`'s — they
only ever talk to each other over HTTP, via `bot.transports.HttpApiClient`
pointed at `http://localhost:{loaded_profile.api_port}`
(`bot/app.py:176`). `run_stack.py`'s `StackSupervisor.start()` already
launches exactly these two as sibling subprocesses
(`python -m api.app <profile>`, `python -m bot.app <profile>`,
`run_stack.py:100-113`) for local dev.

`build_deps()` (`bot/app.py:156-178`) constructs one `BotDeps`
(`bot/contracts.py:44-48`: `loaded_profile`, `telegram_client`,
`api_client`) per process. Both `telegram_client` and `api_client` are
already clean ABCs with more than one implementation:

- `bot.contracts.BotApiClient` — `HttpApiClient` (real, `bot/transports.py`)
  is the only production implementation; `UnimplementedApiClient`
  (`bot/contracts.py:415`) exists to fail loudly during earlier bootstrap
  stages, not as a test double.
- `bot.transports.TelegramClient` — `PTBTelegramClient` (real,
  `bot/transports.py:587-672`) wraps a `python-telegram-bot` `Application`;
  `FakeTelegramClient` (`tests/bot_fakes.py:43-82`) already exists, fully
  implements the same interface, and records every send in memory instead
  of touching a network.

Critically, `build_deps()` requires only that the configured token
(`BOT_TOKEN_ENV`) be a **non-blank string** — `_resolve_bot_token()`
(`bot/app.py:138-153`) never validates it. `PTBTelegramClient.__init__`
(`bot/transports.py:588-591`) does
`ApplicationBuilder().token(token).build()`, which is synchronous, local
object construction with **no network call**. Real Telegram network access
only happens when something later calls `validate_token()`
(`self._application.bot.get_me()`) or `run_polling()` (whose own bootstrap
also calls `get_me()`). Neither is called by `build_deps()` itself.

**Handler functions are already decoupled from PTB's update-delivery
machinery.** `_on_text_message`, `_on_attendance_callback`,
`_on_callback_query`, `_guarded(...)`, etc. (`bot/app.py`) are plain
`async def handler(update, context)` functions. They read only a handful of
duck-typed attributes: `update.effective_user.id`,
`update.effective_chat.id`/`.type`, `update.message.text`/`.message_id`/
`.message_thread_id`/`.reply_to_message`, `update.callback_query.data`/
`.id`/`.message`, and `context.bot_data["deps"]`/`context.args`.
`tests/test_bot_app.py:234-264` already proves this by calling
`app._on_text_message(update, context)` directly with a bare
`SimpleNamespace` standing in for both — no PTB `Update`/`CallbackContext`
class, no polling.

**Outbound replies are already a clean seam.** Every handler ends a turn by
calling `deps.telegram_client.<method>` — never
`self._application.bot.*` inline — for `send_text`, `send_status`,
`edit_status`, `send_with_buttons`, `send_reply`, `answer_callback_query`.
`present_incoming_message()` (`bot/app.py:461-545`) is the shared
lifecycle: `send_status` → `_submit_and_format_message()` (which calls
`deps.api_client.submit_message()`, i.e. `POST /Msg`) → `replace_status()`
(`bot/presentation.py`, ultimately `edit_status`). None of this touches PTB
internals directly.

**The two existing background loops** (`bot/background_services.py`) are
likewise plain functions taking `BotDeps`, unrelated to PTB's
`Application`/dispatcher:

- `run_notification_poll_loop()` — polls `GET` notifications and delivers
  held-approval/clarification/job-result/failure notices via
  `deps.telegram_client`.
- `run_attendance_check_loop()` → `run_attendance_check_once()`
  (`bot/background_services.py:191-264`) — calls
  `deps.api_client.run_attendance_check()` (`POST /TeamStatus/AttendanceCheck`,
  the real cycle-opening decision, entirely server-side — see
  `docs/work_process.md` §12/13 for the earlier investigation), then, only
  if a cycle was opened, calls `deps.telegram_client.send_with_buttons()`
  per bound group. Both loops are started, unconditionally, from
  `register_handlers()`'s `application.post_init` hook
  (`bot/app.py:1085-1117`) — i.e. automatically, the moment the real bot
  process starts, not opt-in.

### 1.2 What PTB's `Application.process_update()` actually requires

This is the one place the earlier investigation left a real gap, and it's
worth being precise, since it changes what "generalize `_fake_update()`"
actually means.

- `Application.process_update(update)`
  (`telegram.ext._application.Application.process_update`) begins with
  `self._check_initialized()`, which **raises `RuntimeError` if
  `Application.initialize()` was never called.** There is no way to skip
  this.
- `Application.initialize()` calls `await self.bot.initialize()`
  unconditionally, and `Bot.initialize()`
  (`telegram._bot.Bot.initialize`) **always calls `await self.get_me()`** —
  a real Bot-API call — to both cache the bot's own identity and validate
  the token, raising `InvalidToken` on failure. There is no flag to skip
  this inside `Application.initialize()`.
- So: to use PTB's *real* dispatcher, something in the process still has
  to make `get_me()` succeed — but not necessarily against real Telegram
  servers. `telegram.request.BaseRequest`
  (`telegram.request._baserequest.BaseRequest`) is PTB's own, official,
  already-abstracted transport seam: exactly four abstract methods
  (`do_request`, `initialize`, `shutdown`, `read_timeout`). A `Bot`
  constructed with a custom `BaseRequest` whose `do_request()` returns a
  canned `getMe`-shaped JSON payload (`{"id": ..., "is_bot": true,
  "first_name": ...}`) satisfies `Application.initialize()` with **zero
  real network I/O and no real token** — the exact same kind of seam PTB
  already uses internally for its own test suite.
- **`process_update()`'s handler dispatch needs a real `telegram.Update`,
  not a loose stand-in.** This is the concrete difference from today's
  tests: `tests/test_bot_app.py`'s `SimpleNamespace` trick works only
  because those tests call `_on_text_message` *directly*, bypassing
  `Application.process_update()`'s routing entirely. Going through the real
  dispatcher means each registered handler's `check_update()` runs for
  real — e.g. `filters.COMMAND.filter()`
  (`telegram.ext.filters`) does `if not message.entities: return False`,
  so a synthetic `Message` with no `entities` and non-empty `text`
  correctly falls through to `filters.TEXT & ~filters.COMMAND` (the
  `_on_text_message` registration) and never matches any `CommandHandler`.
  `telegram.Update.de_json(payload, bot)` is PTB's own, standard
  deserialization path (the same one real webhook/getUpdates delivery
  uses) — it turns a Bot-API-shaped JSON dict into a real, fully-typed
  `Update`, satisfying every handler's `check_update()` faithfully. The
  required fields are small: `Update` needs `update_id` and one of
  `message`/`callback_query`/etc.; `Message` needs `message_id`, `date`,
  `chat` — everything else (`from_user`, `text`, `entities`, ...) is
  optional (confirmed directly against the installed `python-telegram-bot`
  22.8's constructor signatures).

**Conclusion**: nothing here is a hard blocker. `Application.initialize()`'s
`get_me()` call is unavoidable, but it's satisfiable with a small, local,
already-precedented stub (`BaseRequest`) rather than real credentials or
network access, and the dispatcher itself needs a real (but easily
constructible) `Update`, not a hand-rolled reimplementation of PTB's filter
rules.

### 1.3 The admin simulator's current call path

`api/admin_simulator.py`'s client-side JS runs in the operator's **browser**
and calls `fetch('/Msg', ...)` / `fetch('/Event', ...)` directly,
same-origin, with `X-Identity` set to the step's `sender_identity`
(`api/admin_simulator.py:601-625`, confirmed by the module's own docstring
at line 5). This is a browser-to-`api/app.py` call — `bot/app.py` is never
involved today, for any step kind.

### 1.4 `tests/test_architecture.py`'s package-boundary rule

`ENTRY_POINTS["bot"] = {"bot", "bot.app"}` (`tests/test_architecture.py:20`):
any other governed package (`api` included) may import only `bot` or
`bot.app` — never a submodule like `bot.transports`/`bot.contracts`
directly (`tools` has one narrow, unrelated exception). This is enforced by
`test_no_cross_package_imports_outside_entry_points`, and it **confirms**
(not just recommends) that `api/admin.py` cannot import anything from a new
`bot.simulator_*` module directly — communication with the simulation-mode
process has to be over HTTP, exactly like `HttpApiClient`/`api/app.py`
already are. This validates §2's "proxy through `api/app.py`" decision as
architecturally required, not merely preferred.

---

## 2. Decisions made while scoping this (resolved, driving the design below)

Confirmed with the user via `AskUserQuestion` before finalizing:

1. **Update-kind scope for v1: plain text messages only.** Every scenario
   that exists today (the pilot, SEC_001, FIRE_002) scripts only text
   messages — no scenario simulates a button press or a slash command.
   Building faithful `CallbackQuery`/`CommandHandler`/`ChatMemberHandler`
   synthetic-update support now would be speculative scope with nothing to
   exercise it. Deferred explicitly — see §10.
2. **Browser routing: proxy through `api/app.py`, not direct.** The
   browser keeps calling same-origin; a new route on the existing admin
   blueprint makes the actual call to the simulation-mode bot process
   server-side. Avoids CORS, avoids exposing a second port to the browser
   or the network, and — per §1.4 — is the only option the architecture
   test's package boundaries actually allow for direct imports (HTTP is
   required regardless).
3. **`/Event` (sensor) steps: untouched, unchanged.** A sensor has no
   Telegram identity; `profiles/loader.py`'s own validation already exempts
   event-kind steps from needing a declared persona
   (`profiles/loader.py:248-253`, "a sensor step's sender_identity is not a
   persona"). Only `message`-kind steps move to the new path; `/Event`
   keeps going directly to the API server exactly as today.
4. **Process lifecycle: auto-started by `run_stack.py`.** The
   simulation-mode process is started as a third subprocess alongside
   `api.app` and `bot.app` for every local-dev stack run, not a separate
   manual step the operator has to remember.

---

## 3. Architecture overview

Three processes per running profile (local dev, via `run_stack.py`):

```
┌─────────────┐   HTTP    ┌──────────────┐   HTTP    ┌────────────────────┐
│  api.app     │──────────▶│  bot.app     │           │ bot.simulator_app  │
│ (Flask,      │           │ (real PTB    │           │ (PTB Application,  │
│  api_port)   │◀──────────│  polling +   │           │  no run_polling;   │
│              │   HTTP    │  bg loops)   │           │  bg loops; stub    │
│ admin pages  │           └──────────────┘           │  Telegram in/out)  │
│ /Msg /Event  │                                       └─────────┬──────────┘
│ ...          │──────HTTP (proxy, new)─────────────────────────▶│
└─────────────┘         POST /Simulator-msg                      │
                                                         PTB Application
                                                         .process_update()
                                                                   │
                                                          real handler code
                                                        (bot/app.py, unmodified)
                                                                   │
                                                         deps.api_client
                                                       (real HttpApiClient)
                                                                   │
                                                                   ▼
                                                         back to api.app
                                                        (POST /Msg internally,
                                                        real persistence writes)
```

Two independent, real Telegram connections never exist for reserved IDs —
`bot.app` (the real bot, polling real Telegram) is untouched and never
sees simulation traffic; `bot.simulator_app` never touches real Telegram at
all, for anything.

The admin simulator's browser JS, materialized scenario JSON, and
`materialize_simulation()` itself are **all unchanged** — only which URL a
`message`-kind step's JS `fetch()` call targets changes (§7).

---

## 4. New components

### 4.1 `bot/simulator_transport.py` (new) — the two Telegram-network stubs

**`FakeBotRequest(BaseRequest)`** — satisfies `Application.initialize()`'s
mandatory `get_me()` call (§1.2) with zero network I/O. Implements PTB's
four abstract methods; `do_request()` returns a small, fixed, valid `User`
JSON payload for a `getMe` call and raises `NotImplementedError` for
anything else — deliberately, since no other PTB-internal Bot-API call
should ever be reached (all real sends go through `deps.telegram_client`,
never through `self._application.bot.*` — see §1.1). This is the one place
"the real dispatcher's own bootstrap requirement" gets satisfied; it is not
a general-purpose Telegram mock.

**`SimulatorTelegramClient(TelegramClient)`** — the outbound stub,
extending `FakeTelegramClient`'s existing shape (`tests/bot_fakes.py`)
rather than duplicating it: same in-memory `sent`/`status_events`
recording, plus one addition needed for a synchronous-feeling HTTP
response — a way to read back "the final rendered text for chat X from
this call", mirroring exactly what `replace_status()` does for a real
Telegram reply (the last `edit_status` for that `chat_id`/`message_id`
pair; `send_with_buttons`/`send_text` calls captured the same way for
`_on_attendance_callback`'s available-button path, which never goes
through `send_status`/`edit_status` at all). Whether this becomes a
literal subclass of `FakeTelegramClient` (moving it to a shared,
non-test-only location) or a sibling implementing the same `TelegramClient`
ABC is an implementation-time choice; either way it is a small extension
of already-tested code, not new design.

**Synthetic-`Update` construction** — one function,
`build_synthetic_text_update(update_id, message_id, sender_identity,
chat_id, chat_type, text, date, bot) -> telegram.Update`, generalizing
`tests/test_bot_app.py`'s `_fake_update()` from a loose `SimpleNamespace`
into a real `telegram.Update.de_json(...)`-built object (§1.2). Scope
matches §2 decision 1: only the `message` update shape — `update_id`,
`message.message_id`, `message.date`, `message.chat.id`/`.type`,
`message.from.id`, `message.text`. No `entities`, so `filters.COMMAND`
never matches (§1.2) and every scenario message routes to
`_on_text_message`, exactly as a typed message would.

### 4.2 `bot/simulator_app.py` (new) — the entry point

`main(argv)` mirrors `bot/app.py main()`'s shape (parses `profile_module`,
resolves `core_model`/`sub_model` from the environment, calls a
`build_deps`-equivalent), with two differences:

- `telegram_client = SimulatorTelegramClient()` instead of
  `PTBTelegramClient(bot_token)` — no token required at all for this
  process (`_resolve_bot_token`'s non-blank check is bypassed entirely,
  since this process never intends to reach real Telegram).
- `api_client = HttpApiClient(f"http://localhost:{loaded_profile.api_port}",
  bot_service_key=...)` — **the real one**, unchanged. This is what makes
  everything downstream genuinely real: the same `POST /Msg`, the same
  persistence writes, the same `POST /TeamStatus/AttendanceCheck` a real
  bot would make.

`register_handlers()` (`bot/app.py`) is called **unmodified** — the exact
same function, same handler registrations, same `post_init`/`post_shutdown`
hooks that start/stop `run_notification_poll_loop` and
`run_attendance_check_loop`. This is the core maintainability property (see
§8): nothing about handler logic or background-loop logic is copied,
re-implemented, or forked.

Instead of `deps.telegram_client.run_polling(...)`, `simulator_app.py`:

1. Builds the `Application` via `ApplicationBuilder().bot(fake_bot).build()`
   where `fake_bot = telegram.Bot(token="simulator", request=FakeBotRequest(),
   get_updates_request=FakeBotRequest())` (§1.2/§4.1) — real `Bot`, fake
   transport.
2. Calls `register_handlers(application, deps)`, then `await
   application.initialize()` (succeeds immediately, no network — §1.2)
   and `await application.start()` (starts PTB's internal job queue /
   update-processor machinery `post_init` needs; does not start polling).
   `post_init` fires here too, exactly as it does for the real bot — the
   two background loops start.
3. Starts a small Flask app (§4.3) on its own thread, bridged into this
   process's asyncio loop via `asyncio.run_coroutine_threadsafe` for every
   request — reusing Flask (the project's one existing, declared HTTP-server
   dependency; see §9's "why not aiohttp" note) rather than adding a new
   one, at the cost of one standard thread-bridge, instead of introducing
   a second async-native web framework for one endpoint.
4. Blocks (main thread runs the asyncio loop hosting the two background
   tasks + serving the bridge) until interrupted; `post_shutdown`-equivalent
   cleanup on exit (cancel background tasks, `application.stop()`, close
   the API client).

A dedicated `SingleInstanceLock` path — `f"{db_path}.bot-simulator.lock"`,
distinct from the real bot's `f"{db_path}.bot.lock"` — so a simulation-mode
process and a real bot process for the same profile can run concurrently
without conflicting (they are answering fundamentally different traffic),
while two simulator processes for the same profile still can't
double-start.

### 4.3 `POST /Simulator-msg` — the endpoint contract

Hosted on `bot.simulator_app`'s own Flask app, on a new profile-declared
port (`SIMULATOR_PORT`, mirroring `API_PORT`'s existing pattern in
`profiles/contracts.py`/`profiles/loader.py` — optional, defaulting to
unset/disabled so a profile that never uses this feature is entirely
unaffected, matching every other addition in this feature so far).

Request (from `api/admin.py`'s proxy route, §4.4 — never called directly by
the browser, per §2 decision 2):

```json
{
  "sender_identity": "9000000000000002",
  "chat_id": "9000000000000002",
  "chat_type": "private",
  "text": "...",
  "source_message_id": "sim-step-3"
}
```

Shaped to mirror exactly what `_on_text_message` would receive from a real
Telegram update for that chat kind — `chat_id == sender_identity` for a
private chat (Telegram's own convention), or the group's reserved negative
ID for a group chat, `chat_type` one of `private`/`group`/`supergroup`.

Auth: the same shared-secret pattern already used for every bot-service
call, reused rather than invented — the proxy route sends
`X-Service-Key: <BOT_SERVICE_KEY>` (`bot.contracts.BOT_SERVICE_KEY_ENV_VAR`,
already read identically by both processes today), and the endpoint refuses
any request without a matching key. This is not a new secret or a new
concept — it's the existing bot-service credential, shared between
`api.app`'s admin server and this new process the same way it's already
shared between `api.app` and the real `bot.app`.

**Identity gating (the core "never touches real traffic" guarantee)**:
before doing anything else, the handler checks `sender_identity` against
`loaded_profile.simulation_users` and (for a group chat) `chat_id` against
`loaded_profile.simulation_groups` — an **allowlist match against the
profile's own declared reserved IDs**, not just a numeric-range check
(`profiles.simulation.SIMULATION_USER_ID_BASE`/`SIMULATION_GROUP_ID_BASE`
as defense-in-depth on top of the allowlist, not instead of it). Any
identity that isn't a currently-declared simulation persona/group is
refused with `403`, unconditionally — this endpoint can *only* ever be
reached for exactly the reserved IDs `ensure_simulation_entities()` already
provisions, never for anything else.

Handling: build the synthetic `Update` (§4.1), call
`await application.process_update(update)`, then read back
`SimulatorTelegramClient`'s capture for this `chat_id` (§4.1) and return it
in the same shape the admin simulator's JS already expects from `/Msg`
today (`kind`, `answer_text`, `job_id`, `awaiting_approval` — sourced from
what `present_incoming_message` actually did, not fabricated).

### 4.4 `api/admin.py` — the proxy route (new)

One new route, e.g. `POST /admin/simulator/bot-msg` — same Flask-session
admin auth as every other admin route (unchanged pattern), reads
`loaded_profile`'s configured `SIMULATOR_PORT` (or 404s cleanly with a
clear message if the profile never declared one, or if the simulator
process isn't reachable — this is the operator's signal that
`bot.simulator_app` isn't running for this profile), forwards the request
to `http://localhost:{simulator_port}/Simulator-msg` with the shared
`X-Service-Key`, and relays the JSON response back to the browser
unchanged. Pure plumbing — no business logic, matching `HttpApiClient`'s
own role on the `bot.app` side.

---

## 5. Isolation from production Telegram traffic

Layered, not single-point:

1. **Process separation.** `bot.app` (real Telegram polling) and
   `bot.simulator_app` (stub-only) are different processes; the real bot
   never imports or runs any simulator code, and vice versa.
2. **No real Telegram credentials in the simulator process at all** — not
   a fake token gated behind a flag, but genuinely never constructing a
   real `PTBTelegramClient`/real `Bot.initialize()` call anywhere in this
   process's lifetime (§4.1's `FakeBotRequest`).
3. **Allowlist gating at the one entry point** (§4.3) — every request is
   checked against the loaded profile's own declared reserved IDs before
   any handler code runs at all.
4. **The real bot's own admission gate stays in force.** `_guarded()`
   still calls `deps.api_client.admit_telegram_update(...)` for every
   synthetic update — the exact same `POST /Telegram/Admission` real
   Telegram traffic goes through. Simulation personas are already
   provisioned with `auto_register=False`
   (`profiles/simulation_provisioning.py`, docs/profile_simulations_design.md
   §8) specifically so this call finds them already-registered and admits
   them cleanly — this was already validated by the existing provisioning
   mechanism; nothing new is needed here.
5. **`SimulatorTelegramClient` cannot reach Telegram even if every other
   layer failed** — it has no HTTP client, no token, no network code of any
   kind; it is structurally incapable of sending anything anywhere but its
   own in-memory record.

---

## 6. Background-loop behavior in simulation mode

Both loops run for real, unmodified, for as long as `bot.simulator_app` is
up (started by `run_stack.py` alongside the stack, per §2 decision 4):

- `run_attendance_check_loop` polls `POST /TeamStatus/AttendanceCheck`
  every 60s (default), calling into the **real** API server. Exactly as in
  production, it never forces (`force` defaults to `False` — see
  `docs/work_process.md`'s earlier investigation) — so it will only open a
  cycle once real elapsed time actually crosses the profile's configured
  attendance hour. This is now **genuinely** true bot behavior, not a
  stand-in for it — the previously-discussed `force=true` direct-call
  option remains available as a separate, manual operator action for
  immediate testing (unchanged, still the right tool for "I want to see
  this right now" rather than "wait for real time"), but is no longer
  needed to reflect what a real bot would eventually do on its own — this
  process *is* that real bot, for this specific piece of business logic.
- `run_notification_poll_loop` delivers held-approval/clarification/
  job-result/failure notices exactly as production does, landing in
  `SimulatorTelegramClient`'s in-memory record instead of a real chat —
  meaningful for scenarios that exercise async job completion, without any
  special-casing in the delivery logic itself.

---

## 7. Integration with the existing simulation mechanism

- **`api/simulations.py`'s `materialize_simulation()`: entirely unchanged.**
  It still produces the exact same `{scenario, chats, steps}` JSON with
  reserved IDs substituted in; nothing about this design touches the JSON
  adapter or the profile-declaration data model.
- **`GET /Simulations`/`GET /Simulations/<key>`: unchanged.** Still how the
  admin simulator discovers and loads a scenario.
- **`api/admin_simulator.py`'s step-execution flow: one small, scoped
  change.** `buildRequest(chat, step)` (`api/admin_simulator.py:601`)
  currently returns `{url: '/Event', ...}` for an event-kind chat or
  `{url: '/Msg', ...}` for a message-kind one. For a message-kind step,
  the URL becomes the new proxy route (§4.4) instead of `/Msg` directly;
  the request body stays essentially the same shape (`sender_identity`,
  `text`, `source_message_id`) minus fields the real bot now derives
  itself rather than accepting from a caller (see §10's edge case on
  `conversation_id`/`protocol_hint`). `apiCall()`'s `X-Identity` header
  goes away for this path — the proxy route authenticates itself to the
  simulator process via the shared service key (§4.4), not per-persona
  headers, since the persona identity now travels inside the JSON body the
  same way a real Telegram update carries `effective_user.id` rather than
  an HTTP auth header.
- **`/Event` steps: fully unchanged**, per §2 decision 3 — same `fetch('/Event', ...)`
  call, same body shape, same direct browser-to-`api.app` path, forever
  (sensors were never bot/Telegram traffic to begin with).
- **Is `/Msg` "superseded" for simulation traffic?** For message-kind
  scenario steps specifically, yes — the browser stops calling `/Msg`
  directly for those, in favor of the proxy → simulator-process →
  *real bot handler* → `/Msg` path. `/Msg` itself is untouched and still
  the one true ingestion endpoint; it simply now has exactly one caller for
  simulation traffic (the simulator process's own `HttpApiClient`, acting
  as a real bot would) instead of two (the browser directly, and every
  real bot). This is precisely the original design principle restated:
  simulation traffic flows through the bot, and the bot talks to `/Msg`,
  the same way it always has for real traffic.

---

## 8. Maintainability

- **No duplicated logic, by construction.** `simulator_app.py` imports and
  calls `bot.app.register_handlers`, `bot.background_services.
  run_notification_poll_loop`, and `run_attendance_check_loop` directly —
  it does not reimplement, copy, or fork any of them. Any future change to
  `_on_text_message`'s parsing rules, the attendance loop's retry/backoff,
  notification delivery formatting, or anything else in `bot/app.py`/
  `bot/background_services.py` is automatically reflected the next time a
  simulation runs, with zero simulator-side changes required. This is the
  direct payoff of the investigation in `docs/work_process.md` §§10–13:
  the thing being reused is real, shared code, not a parallel
  reimplementation that could silently drift.
- **The only simulator-specific code is the two stubs (§4.1), the entry
  point/wiring (§4.2), and the one endpoint (§4.3)** — everything that
  actually decides *what happens* to a message stays in `bot/app.py`/
  `bot/background_services.py`, untouched.
- **Isolation is structural, not a runtime check someone could accidentally
  weaken** (§5) — a future change to real handler code cannot accidentally
  leak into production Telegram traffic through this path, because this
  path never has real Telegram credentials to leak through in the first
  place.
- **Test coverage**, mirroring `tests/bot_fakes.py`/`tests/test_bot_app.py`'s
  existing rigor:
  - `tests/test_bot_simulator_transport.py` (new) — `FakeBotRequest`
    satisfies `Application.initialize()` with no network access (assert via
    a test that patches/asserts no real HTTP call is attempted);
    `SimulatorTelegramClient` records sends correctly and implements the
    full `TelegramClient` ABC; `build_synthetic_text_update()` produces an
    `Update` that real PTB filters (`filters.TEXT`, `filters.COMMAND`,
    `CallbackQueryHandler.check_update`) classify exactly as expected for
    representative inputs.
  - `tests/test_bot_simulator_app.py` (new) — end-to-end: build a real
    `Application` with the stubs, register real handlers, call
    `process_update()` with a synthetic update against a `FakeBotApiClient`
    (reusing the existing fake, since this layer doesn't need the real API
    server), and assert the same observable outcomes
    `tests/test_bot_app.py`'s direct-call tests already assert — proving
    the dispatcher-based path and the direct-call path agree. A smaller
    number of tests using the **real** `HttpApiClient` against a live test
    instance of `api.app` (mirroring `tests/test_integration_profile_simulations.py`'s
    existing pattern) to prove the full chain persists real state.
  - `tests/test_api_admin.py` — the new proxy route: forwards correctly,
    refuses when the simulator process is unreachable with a clear error,
    never leaks the service key to the browser.
  - `tests/test_profile_simulations.py` / `profiles/loader.py` validation
    — if `SIMULATOR_PORT` is added as a profile-level field (§4.3), the
    same "optional, defaults to unset, existing profiles unaffected"
    validation style as every other addition in this feature.
  - The identity-allowlist gate (§4.3) gets its own dedicated tests: a
    non-reserved identity is refused; a reserved identity not currently
    declared by the loaded profile is refused; a currently-declared one is
    accepted.

---

## 9. File impact

**New:**
- `bot/simulator_transport.py` — `FakeBotRequest`, `SimulatorTelegramClient`,
  `build_synthetic_text_update()`.
- `bot/simulator_app.py` — the entry point (`main()`, deps construction,
  Flask app + `/Simulator-msg`, asyncio/thread bridge, background-loop
  startup/shutdown, `SingleInstanceLock` on its own lock path).
- `tests/test_bot_simulator_transport.py`, `tests/test_bot_simulator_app.py`
  (new test files, per §8).

**Modified:**
- `api/admin.py` — new `POST /admin/simulator/bot-msg` proxy route (§4.4).
- `api/admin_simulator.py` — `buildRequest()`'s message-kind branch targets
  the new proxy route instead of `/Msg` directly (§7); no change to the
  event-kind branch.
- `profiles/contracts.py` / `profiles/loader.py` — optional
  `simulator_port` field (mirrors `api_port`'s existing shape exactly),
  defaulting to unset/disabled.
- `profiles/unified_test.py` — declares `SIMULATOR_PORT` to actually use
  this mechanism for its own SEC_001/FIRE_002 scenarios (a config addition,
  not a scenario-content change — no scenario JSON needs to change).
- `run_stack.py` — starts a third subprocess
  (`python -m bot.simulator_app <profile>`) alongside `api.app`/`bot.app`
  (§2 decision 4); `config/server_control.py`'s `write_status()` gains a
  `bot_sim_pid` field, following the existing `api_pid`/`bot_pid` pattern.
- `docs/file_catalog.md` — new-file entries.
- `requirements.txt` — no new dependency (§4.2 reuses Flask, the project's
  one existing HTTP-server dependency, over a new async-native framework —
  see the note below).

**Untouched:**
- `bot/app.py`, `bot/background_services.py`, `bot/interactions.py`,
  `bot/presentation.py`, `bot/transports.py`'s `PTBTelegramClient`/
  `HttpApiClient` — the entire point of this design.
- `api/simulations.py`, `api/routes.py`'s `GET /Simulations` endpoints,
  `profiles/simulation.py`, `profiles/simulation_provisioning.py` — the
  underlying simulation-declaration mechanism.
- `/Event` handling anywhere.
- `tests/test_architecture.py`'s `ENTRY_POINTS` — no change needed;
  `bot.simulator_app`/`bot.simulator_transport` are same-package (`bot`)
  imports from `bot.app`/`bot.background_services`, already permitted, and
  nothing outside `bot/` imports them directly (§1.4).

**A brief note on the Flask-vs-aiohttp choice** (§4.2 point 3): PTB's
`Application` is asyncio-native, so hosting an inbound HTTP endpoint inside
the same process means either (a) an async-native server (`aiohttp`,
already present in the environment but not a declared project dependency
today) sharing the same event loop directly, or (b) Flask (already the
project's one declared, consistently-used HTTP-server dependency) on its
own thread, bridged into the asyncio loop via the standard
`asyncio.run_coroutine_threadsafe` pattern for the one route that needs it.
This plan recommends (b) — zero new dependencies, one well-established
bridging pattern, consistent with "reuse existing seams" — but flags (a) as
the alternative if the thread-bridge is judged not worth avoiding a new
dependency at implementation time.

---

## 10. Risks / edge cases to verify during implementation

- **`conversation_id`/`protocol_hint` on message-kind scenario steps.**
  Today's `/Msg`-direct path lets a scenario author set these explicitly in
  the step JSON. Once message-kind steps go through the real
  `_on_text_message`, these are **derived** by the handler itself (from
  `chat_id`/`message_thread_id`, and from button-text/attendance detection
  for `protocol_hint`) — a caller can no longer dictate them, exactly as a
  real Telegram message never could. Any existing scenario step relying on
  an explicit `conversation_id`/`protocol_hint` override needs checking;
  `source_message_id` remains controllable and still gives `/Msg`'s
  existing dedup behavior.
- **The `FakeBotRequest`/synthetic-`Update` boundary is the one place a
  future PTB upgrade could silently break something** — a new PTB version
  changing `Application.initialize()`'s internals, or a filter's
  `check_update()` reading a field the synthetic `Message` doesn't
  populate, would surface as a simulator-only failure. Pinning the PTB
  version (already standard practice) and the dedicated filter-classification
  tests in §8 are the mitigation, not a way to eliminate the risk entirely.
- **Two long-lived background loops per running-simulation-mode profile**
  add a small, ongoing resource footprint (a third process, HTTP polling
  every 5s/60s) compared to today's zero-footprint direct-`/Msg` approach,
  for the lifetime `run_stack.py`'s stack is up — acceptable for local dev
  (per §2 decision 4) but worth being explicit that this is a real,
  ongoing cost, not a one-shot call.
- **`SIMULATOR_PORT` collisions** — needs the same kind of "must not equal
  `API_PORT` or another profile's port" sanity check `API_PORT` presumably
  already gets (verify at implementation time), extended to the new port.
- **Flask-in-a-thread bridging correctness** — `asyncio.run_coroutine_threadsafe`
  is standard, but needs care around exception propagation back to the
  Flask thread (so a handler exception becomes a clean 5xx, not a silently
  swallowed background-task failure) and around shutdown ordering (the
  Flask thread must stop before/alongside the asyncio loop, not leak past
  process shutdown).
- **What happens when `bot.simulator_app` isn't running** (operator started
  only `api.app`, e.g. outside `run_stack.py`) — the proxy route (§4.4)
  needs a clear, fast failure (connection refused → a specific admin-UI
  error message), not a generic timeout the operator has to diagnose.

---

## 11. Open follow-ups (not blocking this plan)

- **Callback-query (attendance button) simulation** — deferred per §2
  decision 1. When needed: extend `build_synthetic_text_update()`'s sibling
  for `callback_query` updates (`CallbackQuery.de_json`-based, same
  pattern), and extend the `/Simulator-msg` contract with a `callback_data`
  field alternative to `text`.
- **Slash-command simulation** (`/start`, `/profile`, `/settings`) —
  deferred for the same reason; would need `entities` populated with a
  `bot_command` `MessageEntity` in the synthetic `Message`, per §1.2's
  `filters.COMMAND` finding.
- **`ChatMemberHandler` (bot-added-to-group) simulation** — not currently
  needed by any scenario; no design work done here.
- Whether a later iteration wants `bot.simulator_app` reachable outside
  local dev (e.g. a hosted admin environment) — out of scope for this plan,
  which targets `run_stack.py`'s existing local-dev shape.
