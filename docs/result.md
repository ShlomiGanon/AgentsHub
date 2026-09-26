# AgentsHub — Read-Only Architecture Report

**Purpose:** a precise, as-is picture of how AgentsHub works today, for use in a scenario-based-simulation redesign discussion. No files were modified to produce this report. Citations are `file:line`. Anything not directly read is marked **(inferred)**. Anything searched for and not found is marked **not implemented**.

---

## 0. Critical framing correction — read this first

The task's own description of the system (JSON scenarios with `scenario_metadata` / `event_stream` / `expected_agent_actions`, and agents named `main_orchestrator`, `personnel_agent`, `vision_agent`, `external_comm_agent`) describes a **legacy, no-longer-executed** shape of this system, not its current state.

- That exact JSON shape exists only in six untouched historical files: `fixtures/admin_scenarios/*.json` (three "כיתת כוננת" / Standby-Squad files, three "מכבי אש" / Firefighting files). **No code loads or parses these files anymore.** The code that once did (`api/admin_scenarios.py` — `scenario_catalog()`, `map_legacy_scenario()`, `SCENARIO_FILES`, `SEMANTIC_AGENT_MAP`, etc.) was **deleted outright** during a refactor (see `docs/profile_simulations_design.md:639-648`). The files were deliberately left on disk "as the historical source the SEC_001/FIRE_002 migrations were transcribed from, just no longer read by any code path" (`docs/profile_simulations_design.md:743-746`).
- Their content was hand-migrated into a **different** JSON shape — `{"scenario": {...}, "chats": [...], "steps": [...]}` — declared as Python data (`SimulationScenario` objects) inside profile modules (`profiles/response_team.py:695-889`, `profiles/firefighting.py`). **`expected_agent_actions` was dropped entirely in that migration** — the current `SimulationScenario`/`SimulationPersona`/`SimulationGroup` dataclasses (`profiles/simulation.py`) have no field for it at all, and nothing anywhere compares actual behavior against it.
- The agent names in the legacy files (`main_orchestrator`, `personnel_agent`, `vision_agent`, `external_comm_agent`) do not exist in the current codebase. The real names are `main_agent`, `roster_agent`/`team_status_agent`, `surveillance_agent`, and `neighboring_forces_agent`/`friendly_forces_agent` — see §2.

If the redesign is meant to resurrect or reinterpret the `event_stream`/`expected_agent_actions` concept, that is **new work**, not a modification of something currently wired up. See §6 and §10 for what would need to be built.

---

## 1. Overview

**Tech stack.** Python 3.11+, stdlib-heavy. Flask 3.1+ (HTTP API), Waitress (optional production WSGI server — **not installed** in the current test environment, see §8), `python-telegram-bot==22.8` (Telegram frontend), `httpx>=0.28` (bot→API calls), OpenTelemetry (optional OTLP export). Storage is plain SQLite via the stdlib `sqlite3` module — no ORM, no vector store. Agent framework: **CrewAI** (`crewai>=1.15`), wrapped by this repo's own `agents/runtime.py`; the test suite mocks CrewAI entirely and never makes a real model call.

**LLM provider/models.** Provider-agnostic through CrewAI's `provider/model` string convention; `.env.example` ships OpenRouter as the example provider (`CORE_MODEL_NAME=anthropic/claude-3.5-sonnet`, `SUB_MODEL_NAME=meta-llama/llama-3.1-8b-instruct:free`), but any CrewAI-supported provider works. Two model **tiers** exist system-wide: `core` (orchestrator/insights/history agents) and `sub` (profile specialists).

**Agent framework.** Not LangChain/AutoGen — a **thin, hand-rolled wrapper around CrewAI** (`agents/runtime.py`). Each "agent" is a Python class with a fixed `system_prompt`/`role`, `@tool`-decorated methods, and no autonomous multi-turn loop of its own — `Agent.process(task_text, allowed_tools)` builds one `crewai.Agent`, kicks it off once, returns text. There is no persistent CrewAI Crew object; orchestration is entirely this repo's own Python code (`orchestrator/`, `protocols/executor.py`).

**Directory structure (top 2 levels, important folders):**

| Path | Responsibility |
|---|---|
| `agents/` | Agent runtime/contracts + shared/reusable agent base classes. |
| `orchestrator/` | The actual brain: `reasoning.py` (Main/Insights agents + judgment prompts), `flows.py` (end-to-end business flows), `event_queue.py` (async job queue), `holds.py` (durable pause/resume), `group_routing.py` (chat→agent binding), `situational_picture.py`, `capabilities.py`. |
| `api/` | Flask app, routes, auth boundary, admin panel, simulation JSON adapter. |
| `bot/` | Telegram frontend, background notification delivery, plus a parallel simulation-mode bot. |
| `auth/` | Permission levels and username display rules. |
| `config/` | Env-backed model-tier resolution, live-editable settings, stack-supervisor IPC. |
| `history/` | Extraction pipeline, structured query service, field vocabulary, scheduled summaries. |
| `persistence/` | Schema/migrations, main SQLite store, per-domain dedicated stores. |
| `profiles/` | Profile contract, loader/validator, simulation declarations, shipped deployments (`response_team.py`, `fire_station.py`, `fire_station_sim.py`, `firefighting.py`), authoring template. |
| `protocols/` | Protocol contract, editable repository, step executor with retry/concurrency. |
| `tools/` | Random synthetic-event generator, response-pipeline evaluator, terminal test clients. |
| `fixtures/` | Deterministic test profiles/data, plus the **unused legacy** `admin_scenarios/*.json`. |
| `data/` | Per-profile SQLite databases. |
| `docs/` | Extensive design/ops documentation. |
| `tests/` | ~100 files: unit, integration, and scenario/simulation tests — see §8. |

**Entry points:**
```
python -m api.app <profile_module> [--server waitress --threads 16]
python -m bot.app <profile_module>
python -m cli.user_admin --profile <profile_module> ...
python -m tools.simulator --port <port> --identity <identity> ...
python -m tools.terminal_client_commander --profile <profile_module>
python -m tools.terminal_client_viewer --profile <profile_module>
python run_stack.py    # supervisor: starts/stops/switches api+bot(+bot-sim) as one unit
```
Required env: `BOT_TOKEN`, `BOT_SERVICE_KEY`, `CORE_MODEL_PROVIDER/NAME/API_KEY_ENV` (+key), same for `SUB_*`, loaded via `.\load-env.ps1`. `ADMIN_USERNAME`/`ADMIN_PASSWORD`/`ADMIN_SESSION_SECRET` optionally enable the admin panel. A profile is a plain Python module satisfying `profiles/contracts.py`'s required attributes (full contract in `docs/profile_spec.md`).

**Shipped profiles:** `profiles.response_team` (port 8907 — the integrated deployment: roster/attendance, camera/drone surveillance, neighboring-force dispatch; Hebrew default), `profiles.fire_station`, `profiles.fire_station_sim`, `profiles.firefighting` (port 8906). Only one profile's API+bot pair runs at a time per Telegram bot token.

Startup makes **one real, billed, minimal provider call per unique Provider/Model** before opening the HTTP listener, to fail fast on bad credentials.

---

## 2. Agents

**Naming correction (repeated for emphasis):** the codebase does not use `main_orchestrator`, `personnel_agent`, `vision_agent`, or `external_comm_agent`. Treat those as **not implemented**. The real orchestrator is `main_agent`.

### Framework base (`agents/runtime.py`, `agents/contracts.py`)

Every agent subclasses `agents.runtime.Agent` (`runtime.py:214-282`), requiring `name`, `role`, `system_prompt`. Shared contracts (`agents/contracts.py`):
- `ToolInfo` + `@tool(name, description, side_effecting, idempotent)` decorator — `idempotent` mandatory iff `side_effecting=True`, forbidden otherwise, enforced at class-definition time.
- `InvocationPolicy`: `max_output_tokens`, `timeout_seconds`, `reasoning_effort` (`none|low|medium|high`), `response_schema`. **No temperature control anywhere — not implemented.**
- `AgentResult`: `status: "success"|"unclear_task"`, `text: str`. **Every agent's output is free text**, parsed only for an `UNCLEAR_TASK:` sentinel. No general per-agent structured/JSON output contract exists.

`agents.runtime.invoke()` builds one `crewai.Agent` per call (`role`, `backstory=system_prompt + UNCLEAR_TASK_PROMPT_INSTRUCTION`, wrapped tools, profile-set `max_iter`, `max_execution_time`, `max_retry_limit=0`); `kickoff(text)` is the call, `.raw` is returned text.

**Tool allowlisting:** each tool method is wrapped once at `__init__`; a `ContextVar` holds the caller-supplied `allowed_tools` for the current call — a disallowed call returns `"Tool '<name>' is not permitted for this task."` instead of executing.

**Tier wiring:** a profile's `AGENTS` list is `AgentSpec(cls=..., tier="core"|"sub")`; `profiles/loader.py:412-433` is the only place these are constructed, binding `core`→`CORE_MODEL_*` env vars and `sub`→`SUB_MODEL_*`.

### Core agents (every profile, always `core` tier)

**`main_agent`** — the orchestrator (closest analogue to "main_orchestrator"). `orchestrator/reasoning.py:49-63`.
- Role: *"The orchestrator, and the only component that makes judgment calls: risk assessment, protocol selection, task formulation, and success judgment. Reasons over what it is handed; the specialist agents act, this agent decides."*
- Full system prompt: *"You are the Main Agent, the orchestrator of a field-report multi-agent system. You are given one focused judgment to make at a time, with everything relevant already provided — never assume context from a different judgment. Follow the exact response format each prompt requests precisely; your response is parsed programmatically, not read by a person. When composing an answer or replying to messages in Hebrew, ALWAYS respond exclusively in concise, direct Hebrew (at most 3-5 lines), with no English."*
- **No tools** — always invoked with `allowed_tools=[]`. One long-lived instance reused for many distinct judgments (intent classification, risk assessment, protocol selection, task formulation/rewrite, message planning, final success judgment) — each a separate `process(prompt, [])` call with a bespoke line-based/regex-parsed response format, never JSON.
- Model: `core` tier only, with per-judgment tuned `InvocationPolicy` at several call sites.

**`insights_agent`** — `orchestrator/reasoning.py:1068-1081`. Role: *"Synthesizes the end of every protocol run... forms one conclusion setting this run against history. Concludes; does not act."* No tools.

**`history_agent`** — `agents/standard_agents.py:40-65`. Role: *"Summarize supplied historical records and answer historical questions only from the context supplied for the current task."* Prompt emphasizes: never use conversational memory or outside knowledge; preserve contradictions verbatim, never reconcile; a field absent from what it's given means "not available," never guessed; always cite Event IDs. **No tools** — SQL filtering happens in `history/query.py` before this agent ever sees records.

### Shared, reusable specialist base classes (`agents/`)

- **`SurveillanceAgent`** (`agents/surveillance_agent.py:74-404`) — closest analogue to "vision_agent." Tools: `get_camera_feeds`, `get_drone_fleet_status`, `dispatch_drone_to_area`, `get_active_missions`, `return_drone_to_base`, `get_surveillance_overview`, `update_camera_observation`. SQLite-backed. Subclassed by `ResponseTeamSurveillanceAgent` (adds `update_camera_status`, `recall_drone`).
- **`TeamStatusAgent`** (`agents/team_status_agent.py:21-249`) — closest analogue to "personnel_agent." Tools: `start_daily_attendance_check`, `record_attendance_response` (identity from an authenticated-request `ContextVar`, never a caller-supplied param), `report_team_availability`. Subclassed as `ResponseTeamRosterAgent`, **registered under the name `roster_agent`**.
- **`RosterAgent`** (`agents/roster_agent.py:13-55`) — used unmodified only in `profiles/fire_station.py`. **In-memory list only, no DB persistence**, no read/report tool.
- **`FriendlyForcesAgent`** (`agents/friendly_forces_agent.py:9-90`) — partial analogue to "external_comm_agent," but a dispatch-*record* specialist: *"does not contact any real ambulance, police, fire, or military system."* **In-memory-only** state. Its docstring says it's shared by `profiles.standby_squad` and `profiles.firefighting` — **`profiles/standby_squad.py` no longer exists** (superseded by `response_team.py`, which uses a different, DB-backed `NeighboringForcesAgent`).
- **`DispatchAgent`/`HazmatAgent`** (`agents/fire_station_agents.py`) — fire-station-only, in-memory-only.

### Profile-only agents

**`NeighboringForcesAgent`** (`profiles/response_team.py:330-420`) — not in `agents/`; deliberately **not** built on `FriendlyForcesAgent`. Records ambulance/police/K9/YASAM dispatches against profile-declared `FORCE_BASES`, with computed ETA from a fixed area-to-area matrix, and the **one documented automatic status transition** (`en_route`→`arrived` once ETA elapses, computed, never agent-reported). Backed by a real SQLite table.

### Agents actually wired into the two production profiles

| Profile | Registered name | Class | Tier | Store |
|---|---|---|---|---|
| both | `main_agent` | Main Agent | core | none |
| both | `insights_agent` | Insights Agent | core | none |
| both | `history_agent` | `HistoryAgent` | core | reads pre-filtered context only |
| `response_team` | `roster_agent` | `ResponseTeamRosterAgent(TeamStatusAgent)` | sub | SQLite, `data/response_team/response_team_history.db` |
| `response_team` | `surveillance_agent` | `ResponseTeamSurveillanceAgent(SurveillanceAgent)` | sub | same DB, separate tables |
| `response_team` | `neighboring_forces_agent` | `NeighboringForcesAgent` | sub | same DB, `neighboring_force_dispatches` |
| `fire_station` | `dispatch_agent` | `FireStationDispatchAgent(DispatchAgent)` | sub | in-memory only |
| `fire_station` | `hazmat_agent` | `HazmatAgent` | sub | in-memory only |
| `fire_station` | `roster_agent` | `RosterAgent` (unmodified) | sub | in-memory only |

`profiles/fire_station_sim.py` and `data/response_team_sim/` exist but their `AGENTS` lists weren't independently re-verified — **(inferred)** they mirror their non-`_sim` counterparts.

---

## 3. Routing and orchestration

### How an incoming message reaches an agent

Two independent layers, both inside `POST /Msg` (`api/routes.py:254`):

1. **Chat-scoping by `telegram_chat_id`/`telegram_chat_type`** — not by any `target_agent`/`source_chat` field (those are legacy-fixture-only, §0). `resolve_scope()` (`orchestrator/group_routing.py:203-217`) returns `None` for private chats (→ always `main_agent`), the bound agent for a registered group, or raises `GroupNotRegisteredError`. Bindings live in a DB-backed `GroupRoutingTable`, editable via `cli.group_admin`/admin panel. If the resolved agent isn't `main_agent`, `scope_deps()` builds a request-local view exposing only that specialist + core agents.
2. **Intent classification** by an LLM call → `needs_clarification | conversational | question | report | request`. Deterministic shortcuts (button labels, explicit `protocol_hint`, keyword matches, pending hold replies) run first and skip the model entirely where possible.

`POST /Event` (sensor input) has no chat/agent concept — always becomes a `"report"`.

### How the orchestrator calls sub-agents

**Direct in-process Python calls — no message bus.** `Agent.process()` is called directly from route handlers (single-agent fast paths) or from `protocols/executor.py:execute_step_with_retry` (formulated multi-step plans). `execute_steps` picks plain sequential (legacy plans) or **dependency-graph execution** (steps with `step_id`/`depends_on`): ready read-only steps run **concurrently** (up to 4 workers); side-effecting steps run alone and are further serialized process-wide by a per-`agent:tool` lock.

**Sync vs. async:** `/Event` and most `/Msg` outcomes (report/request/event-data-resume) are **asynchronous** — persisted, queued, HTTP 202 returned immediately. Conversational replies, questions, and deterministic read-only protocols are **synchronous**.

The queue (`orchestrator/event_queue.py`) has two implementations: `SerialEventQueue` (one worker, strict FIFO) and `PolicyAwareEventQueue` (N workers, `PriorityQueue`, reserved "continuation" capacity, per-item deadlines, and `concurrency_keys` = `f"sender:{identity}"` so **two jobs from the same sender never run concurrently**, preserving per-sender order under multiple workers).

Every job's deadline is *also* re-checked inside the flow at each stage transition — except an approved-but-not-yet-executed hold is exempt from its original deadline so a slow human approval doesn't retroactively kill the run.

### Aggregating sub-agent responses

- Single-agent sync → the agent's own text, returned directly.
- Multi-agent situational-picture sync → fans out concurrently, composes one narrative + provenance breakdown.
- Async protocol execution → collects every step's outcome, persists them, then builds one final insight (`InsightsAgent.build_insight` or, for multi-agent protocols, a synthesis of specialists' results + recent event-log context), then a separate `judge_success` call turns this into `succeeded|failed|uncertain`. **The original HTTP caller never sees this directly** (already got its 202) — delivery is via the notification feed **(inferred exact mechanism — not independently re-verified against `bot/background_services.py` in this pass; the cursor-backed model is corroborated by the `notification_log` table, §4)**.

### Retries, timeouts, error handling

- **Step-level retry**: up to a live-editable `retry_count`, fixed 1s backoff. `AgentInvocationError` retries unchanged *unless* a non-idempotent side-effecting tool is involved (fails immediately). `unclear_task` status retries with a **rewritten task**.
- **One-extra-attempt pattern** for extraction and `judge_success` (input already durably persisted before the call) on `AgentTimeoutError`/`AgentModelError`.
- **Queue-full** → HTTP 503, before any persistence write.
- **Deadline enforcement** at every flow-stage boundary.
- **Uncaught worker exceptions** are caught/logged; the worker survives; the event is left in whatever state the flow function itself explicitly recorded.

### Request lifecycle (async report/request path)

1. Authenticate/authorize → 2. Chat-scoping → 3. Duplicate check by `source_message_id` → 4. Conversation memory append → 5. Fast-path match or LLM intent classification → 6. Report→`begin_report`→queued extraction (with required-fields gate); Request→`begin_request`→queued risk assessment → 7. Risk assessment → protocol selection → precedent lookup → approval-hold check → 8. Task formulation → step execution → 9. Insight synthesis → success judgment → terminal persistence → 10. Async delivery via notifications.

Holds pause this pipeline at steps 6-8 and resume by re-entering the same flow functions — never a restart from step 1.

---

## 4. State and memory (most important section)

**There is no shared/blackboard state between agents.** Cross-agent "pictures" are assembled fresh, at request time, by fanning out to each specialist's own store and composing the answer in that moment. Stated explicitly in `orchestrator/situational_picture.py:1-11`: *"A situational picture assembled at request time, never from prepared text... only then does the Main Agent write the picture from exactly those findings."*

### Where each agent's knowledge lives

| Agent | Store type | Key tables |
|---|---|---|
| Orchestration / event lifecycle | Main deployment SQLite DB (`persistence/sqlite_store.py`, `persistence/schema.py`) | `events`, `event_steps`, `held_events`, `notification_log`, `conversation_messages`, `log_entries`, `daily/monthly/yearly_summaries`, `users`, `telegram_groups` |
| `TeamStatusAgent` | Dedicated SQLite DB (`persistence/team_status_store.py`) | `team_members`, `roster_approval`, `attendance_cycles`, `attendance_responses` |
| `SurveillanceAgent` | Dedicated SQLite DB (`persistence/surveillance_store.py`) | `cameras`, `drones`, `drone_missions` |
| `RosterAgent`, `FriendlyForcesAgent`, `DispatchAgent`, `HazmatAgent` | **In-memory Python list only** | not a DB table — **lost on process restart** |

The last row matters for a redesign: these are the generic/reference agents; a real profile is expected to subclass them with real persistence (as `response_team.py` does), but as shipped they keep no durable state.

### Raw vs. structured storage — both, same row, nothing discarded

1. `begin_report` (`orchestrator/flows.py:217-248`) persists `raw_text`, `source`, `received_at`, `sender_identity` **before any model call runs**.
2. `extract_event` (`history/event_pipeline.py:115+`) asks the Main Agent (raw-text-in/JSON-out, no tools) for `classification`, `area`, `entities`, `description`, `severity`, `occurred_at`, `availability_start/end`, `absence_reason`, written into the **same row** as additional columns.

`raw_text` and structured fields coexist permanently; a field absent from extraction is stored as SQL `NULL`, never guessed.

### Persistence across restarts / simulation resets

Plain SQLite files under `data/<profile>/`, surviving restarts by default (idempotent versioned migrations via `PRAGMA user_version`, 20 migrations). Databases are wiped **only** on an explicit operator "reset" action (`run_stack.py:35-65`) — an admin-panel server-control action, not tied to simulation runs. **A simulation uses the same live database as a real deployment** unless an operator resets it first — "a simulation is just another deployment of this same profile module." There is no automatic ephemeral/sandboxed store per simulation run.

### Handling of outdated information — inconsistent, and mostly absent

- **Team status: a real, working TTL/expiry mechanism exists.** `availability_snapshot(as_of)` (`persistence/team_status_store.py:294-331`) computes status *lazily at query time*: latest accepted response checked against `unavailable_until > as_of`; once expired (and outside the still-open attendance cycle), status silently reverts to `"awaiting_response"` — **not** automatically back to `"available"`. The one place with an explicit validity-period concept.
- **Surveillance (cameras/drones): no expiry, no confidence/verification level, no reconciliation loop.** `CameraInfo`/`DroneInfo` carry only a plain status enum and a `last_updated` timestamp that **nothing ever reads to judge staleness**. A camera marked `offline` stays `offline` forever until someone explicitly calls an update tool — no background job, no confidence field. **This directly answers your own example ("a camera coming back online") — not implemented.**
- **Events table generally:** no TTL/expiry concept — extracted fields are a permanent point-in-time snapshot; `outcome`/`insight_text` are terminal/immutable once written.

### Holds — the other durable cross-turn state

`orchestrator/holds.py`: persists everything needed to resume (event ID, candidate/selected protocol, risk assessment, missing fields, question text) as JSON in the `held_events` table, keyed by kind (`approval`, `clarification`, `event_data`) — survives restarts by construction.

---

## 5. Time handling

**"Current time" is always the real system clock**, injected at one seam per subsystem — never a scenario-declared or simulated clock: event ingestion (`received_at` = receipt-time UTC), history queries (`HistoryQueryService`'s `clock` param, defaults to real `datetime.now(timezone.utc)`, injectable only for tests), attendance cycles, drone ETAs. The admin-panel simulator is explicit: *"`occurred_at` is always the server's receipt time, and the simulator never pretends otherwise"* — a scenario step's `timestamp` field is display-only.

**Timezone handling — profile-declared IANA zone, applied inconsistently:**

- Every profile declares `TIMEZONE` (e.g. `"Asia/Jerusalem"`), validated at load.
- **History questions are timezone-aware:** the Main Agent gets both `current_time_utc` and `current_time_local` plus the zone name, to resolve relative periods against local wall-clock time.
- **`TeamStatusAgent` is timezone-aware for scheduling:** explicit `ZoneInfo` conversion for the local 08:00 attendance-check hour and local calendar-day key.
- **Report/event extraction is NOT timezone-aware.** The extraction prompt tells the model only `Resolve occurred_at relative to received_at={received_at}` — a bare UTC ISO string, no timezone name included. A Hebrew report saying "הלילה" ("tonight") is resolved against UTC with no local-time anchor, unlike the two paths above. **This inconsistency is worth flagging directly.**

**Relative time expressions ("tonight," "for two hours") are resolved entirely by the LLM — no deterministic parser exists anywhere.** For history questions the model emits ISO bounds itself from local/UTC "now" text. For extraction, the model emits ISO timestamps directly; anything that fails `datetime.fromisoformat` is **dropped and logged**, not rejected — triggering either a required-fields gate block or a reporter-facing clarification hold.

**No injected/virtual clock exists for simulation runs.** A scenario step's declared `timestamp` is inert, display-only metadata; nothing in the replay path sets `datetime.now` to it. The only clock-injection seam in the whole codebase is the test-only `clock=` parameter on `HistoryQueryService`. Deterministic/simulated time for scenarios would need to be built from scratch.

---

## 6. Simulation runner and evaluation

**Two unrelated things share the word "simulation" here:**

1. `fixtures/admin_scenarios/*.json` — the exact shape you described. **Legacy, unused** (§0).
2. The live admin-panel **Simulator** (`api/admin_simulator.py`, `profiles/simulation.py`, `profiles/simulation_provisioning.py`, `api/simulations.py`, `bot/simulator_app.py`) — a manually-operated, browser-driven step player. This is the actual current mechanism.

### Loading and replay

`tools/simulator.py` is unrelated: a random synthetic-sensor-event generator, never reads a scenario file. The real player: an operator loads a scenario (drag-drop/paste, or `GET /Simulations/<key>`, which materializes a profile's `SimulationScenario.raw` with placeholder IDs substituted for deterministic reserved Telegram IDs). Client-side JS queues one step per chat; the operator clicks **"Send next"** one step at a time — **no automatic/batch replay; every step is a manual browser action.** Message-kind steps proxy through a dedicated **second bot process** (`bot/simulator_app.py`) reusing real bot handler code with only Telegram network legs stubbed.

### Simulated vs. real time

**Entirely real** (§5). Steps advance strictly on human clicks.

### `expected_agent_actions` usage: **not implemented — display-only text at most, no evaluation code**

Legacy fixtures' `expected_agent_actions` is inert data in unused files. The current admin-simulator format parses it (if present) **purely to render a bulleted list for the human operator to read** while manually running the scenario. **No code anywhere compares an expected action against actual behavior.** No scoring, no pass/fail, no LLM-as-judge tied to this field. `tests/test_profile_simulations.py` (635 lines) has zero references to evaluation/scoring against it. **Evaluation today is 100% manual** — an operator reads the displayed list and judges correctness by eye.

### Where results/logs are written

**No dedicated simulation-run log/report exists.** A run's "output" is transient browser state. Side effects land in the profile's normal SQLite DB exactly like real traffic — indistinguishable from production data except for reserved simulation Telegram IDs. `tools/simulator.py` prints a one-line summary to stderr, no file. `tools/evaluate_response_pipeline.py` is a third, unrelated tool (classifier-accuracy + disclosure checks against JSONL corpora) with no relationship to scenario evaluation.

### Scenario file inventory

**Legacy, unused** (`fixtures/admin_scenarios/*.json`):

| File | scenario_id | phase | event_stream steps | expected_agent_actions |
|---|---|---|---|---|
| כיתת כוננת - חלק 1.json | SEC_001_PHASE_1 | 1_PREPARATION_ROUTINE | 9 | 2 |
| כיתת כוננת - חלק 2.json | SEC_001_PHASE_2 | 2_MAIN_ESCALATION | 9 | 2 |
| כיתת כוננת - חלק 3.json | SEC_001_PHASE_3 | 3_EXTREME_CRISIS | 10 | 2 |
| מכבי אש - חלק 1.json | FIRE_002_PHASE_1 | 1_PREPARATION_ROUTINE | 7 | 1 |
| מכבי אש - חלק 2.json | FIRE_002_PHASE_2 | 2_MAIN_ESCALATION | 7 | 1 |
| מכבי אש - חלק 3.json | FIRE_002_PHASE_3 | 3_EXTREME_CRISIS | 9 | 2 |

All `domain: "FIRST_RESPONDERS_TEAM"`. **Current, live** (Python-declared, no `expected_agent_actions`): `profiles/response_team.py` declares `sec001_phase1/2/3` (15 personas, 3 groups); `profiles/firefighting.py` declares `fire002_phase1/2/3` (13 personas, 3 groups).

### What the related tests assert

`tests/test_profile_simulations.py` — ID scheme, structural validation, provisioning, materialize/catalog functions. **Never touches replay or `expected_agent_actions`.** `tests/test_operational_scenarios.py` — an unrelated concept despite the name: hand-coded pytest functions driving the real pipeline with scripted/fake agents, asserting pipeline *wiring*, not model quality, and unrelated to the JSON files.

### Bottom line

**No automated scenario-based evaluation harness exists.** `expected_agent_actions` is either inert unused data or UI-display-only text. Building real automated evaluation is new work.

---

## 7. External integrations

**Telegram → chat-id mapping.** `source_chat` appears only in legacy fixtures. Real derivation: `bot/app.py:202-203`, `(str(update.effective_user.id), str(update.effective_chat.id))`, attached to outgoing API calls as `X-Telegram-Chat-ID`/`X-Telegram-Chat-Type` headers. Server-side, group→agent binding is a DB-backed table; private chats always resolve to `main_agent`.

**Bot service identity.** `"bot-service"` is a fixed public string; a caller claiming it must present `X-Service-Key`, compared with `hmac.compare_digest`. A wrong/missing key gets the *same* "identity unregistered" error a genuinely unregistered identity would — indistinguishable by design.

**"Vision"/camera I/O is entirely simulated — no real camera, RTSP/ONVIF, or vision-model integration exists.** `agents/surveillance_agent.py` is pure text/SQLite; `feed_summary` is a free-text field updated by tool calls. No image ingestion or vision API call exists anywhere. Drone ETA/dispatch is a deterministic in-memory/DB calculation, not a real fleet API.

**No other external I/O found.** No outbound calls to third-party services beyond the bot's own API calls and the admin panel's internal self-proxy. "External forces" dispatch is simulated via Telegram groups and specialist agents.

---

## 8. Tests and quality

**Inventory** (~100 files): simulation/scenario-related, 13 integration files, ~75 unit-style files, plus an architecture/legacy-import guard and a Hebrew-leakage lint. `tests/sanity_check_real_model_call.py` and `--live` evaluation are explicitly opt-in/billed and excluded.

**Dependencies:** all import successfully except **`waitress` is not installed** — affects only the optional production-server flag, not the test suite.

**Test run** (`python -m pytest -q`, documented `TEST_*` env vars, no real credentials/billed calls):

```
4 failed, 1516 passed, 6 warnings in 320.44s
```

Failures:
1. `test_api_messages.py::test_a_report_returns_202_with_a_job_id` — failed (detail not captured; needs targeted re-run).
2. `test_api_messages.py::test_a_request_returns_202_and_is_classified_human_activation` — failed (same).
3. `test_integration_log_sink.py::test_querying_by_trace_id_returns_every_log_row_for_one_request_in_order` — concrete ordering mismatch: a `model_io` trace event now appears between `step_start` and `step_result`, where the test expects them adjacent. Likely a genuine drift between `protocols/executor.py`'s emitted trace events and this test's hard-coded sequence.
4. `test_integration_user_administration.py::test_user_api_exposes_reads_self_name_update_and_commander_approval` — stale route-inventory assertion missing three admin routes (`/admin/users`, `/admin/users/<identity>/approve`, `/admin/users/<identity>/remove`) added after the test was last updated. Not a functional bug.

**TODO/FIXME:** none found in production or test code — only two tests *asserting the absence* of the string `"TODO"` in agent prompts.

---

## 9. Gaps and observations

1. **The exact scenario format and agent names in your own framing are dead** (§0). Decide: resurrect that shape, or design fresh against the live `{scenario, chats, steps}` shape and real agent names.
2. **No automated scenario evaluation exists at all** (§6) — very likely the actual gap motivating this redesign.
3. **State-staleness handling is inconsistent and, for vision/camera specifically, absent** — no expiry, no confidence, no reconciliation for cameras/drones (§4).
4. **Time-anchoring for relative expressions is inconsistent** — history/attendance are timezone-aware, report extraction is not (§5).
5. **No time simulation/virtual clock exists anywhere** — a scenario step's timestamp is cosmetic.
6. **Several agent base classes have no real persistence** (`RosterAgent`, `FriendlyForcesAgent`, `DispatchAgent`, `HazmatAgent` — in-memory, lost on restart).
7. **Simulation runs are not isolated from live data** — a "simulation" is literally the same DB as production unless explicitly reset first.
8. **Two live, unrelated bugs surfaced by the test run** — a trace-log ordering drift and a stale route-inventory test assertion.
9. **No general structured/JSON output contract for agents** — everything is free text parsed by bespoke string/regex logic. Relevant if the redesign wants machine-checkable structured actions (which would make automated evaluation much easier to build).
10. **No TODO/FIXME debt** — unusually clean codebase on this axis.

---

## 10. Open questions

1. Is the redesign meant to resurrect `expected_agent_actions`-style automated evaluation, or design something new against the current shape?
2. Should evaluation be structural (right protocol/agent/tool selected) or behavioral/LLM-judged (narrative response satisfies a criterion)? Both would be new work.
3. Does the redesign need simulated/virtual time (e.g. "3 hours pass," controlled replay rate)? No clock-injection seam exists in production paths today.
4. Does the redesign need scenario-run isolation from live data, or is reusing the live DB (today's behavior) acceptable given the existing manual profile-reset flow?
5. Which staleness/expiry model should apply outside team-status — the same lazy-computed-validity-period pattern, or something else (explicit TTL, background reconciler, confidence score)?
6. Should report/event extraction become timezone-aware like history queries and attendance scheduling already are, fixing the "tonight" resolution gap? Looks like a small, isolated fix.
7. Are `profiles/fire_station_sim.py` and `data/response_team_sim/` actually current, or leftover from an earlier structure? Not independently re-verified.
8. What should happen to the six legacy `fixtures/admin_scenarios/*.json` files — kept as reference, formally deprecated/removed, or reused as seed content for new scenario authoring?
