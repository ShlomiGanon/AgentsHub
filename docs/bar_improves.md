# Task: Add operational capabilities to this codebase without changing its architecture

## Background — read this first
A teammate built, on a separate copy of this project that you cannot access, support for two
operational organizations — a Security / Standby Response Team ("SEC") and a Fire and Rescue
station ("FIRE") — plus simulation runs and several robustness fixes found during acceptance
testing. His version added heavy new infrastructure (operational units, memberships, in-process
LIVE/Simulation scopes, Telegram-to-simulation binding). We are NOT reproducing that
infrastructure. You will rebuild the *capabilities* from the descriptions below, using only
mechanisms that already exist in this codebase.

The central idea: this codebase already isolates everything at the deployment level —
  one deployment = one profile module = one database file = one API port = one Telegram bot.
Two organizations are therefore simply two profile modules run as two deployments, and a
simulation is simply a third deployment with its own database and bot. This gives complete
isolation (events, precedents, summaries, notifications, conversation memory, users, queue)
with zero new isolation code.

Before starting, read: `docs/progress.md` (append-only project log), `docs/allowed_calls.md`,
`docs/vocabulary.md`, `docs/profile_spec.md`, `docs/agent_authoring.md`, `docs/api_spec.md`,
`docs/operator_guide.md`, the demo profile (`profiles/demo.py`), the reference agent
(`agents/reference.py` or its current location — check `docs/file_catalog.md`), the history
event pipeline (extraction), `history/field_catalog.py`, `persistence/migrations.py`,
`persistence/schema.py`, and `persistence/sqlite_store.py`.
The docs describe intent; the code describes actual behavior. If they disagree, trust the
code and tell me.

## Hard constraints (if a stage seems to need breaking one, STOP and ask)
1. No new top-level packages; no change to package boundaries or entry points in
   `docs/allowed_calls.md`; `tests/test_architecture.py` must pass unchanged.
2. No unit/tenant/scope concept: no OperationalUnit, membership, scenario or run IDs,
   bind/unbind, or runtime context object. Isolation stays deployment-level.
3. Profiles stay frozen for the life of a run. No runtime profile switching. `PUT /SYSTEM`
   keeps accepting only its existing live settings.
4. `cli/user_admin` remains the only way to create, change or remove users.
5. Authorization model unchanged (`PermissionLevel`, `RequestedOperation`,
   `ViewerAllowedAction`, sender-identity ownership scoping). Add no new operations.
   The existing rule that a commander's own request bypasses a protocol's approval flag
   stays exactly as it is.
6. No Hebrew literal outside `messages/en.py` / `messages/he.py` — this includes profile
   modules (`tests/test_hebrew_leakage.py`). All profile content is written in English.
   No emoji in user-facing text. Any new user-facing string goes through both catalogs with
   identical keys and placeholders; never interpolate a raw internal value into a message.
7. Extraction stays model-based. No keyword/rule-based extraction. No domain terms
   (camera IDs, resource names, gate or sector names) anywhere in core packages — domain
   content lives only in profile modules.
8. Persistence changes only via the persistence interface and `persistence/sqlite_store.py`.
   New migration number = highest existing number + 1 (the log suggests 16; verify).
   Migrations are idempotent (inspect columns before ALTER) and the fresh-schema DDL is
   updated in the same change.
9. Do not weaken or delete existing test assertions. If a stage intentionally changes a
   behavior an existing test pins, update that test and report it with the reason.
10. No network calls, no real/paid model calls, no git push, PR, merge or workflow dispatch.

## Working method
- One stage at a time, in order. For each stage: first describe in 1–3 sentences what the
  code currently does in that area; if it already satisfies the stage, say so and change
  nothing. Then implement, run focused tests, then the full offline suite, then STOP and
  report: what changed, files touched, test counts, open questions. Wait for my go-ahead.
- Every new test file is added to its mission step in `.github/workflows/ci.yml` and to
  `docs/file_catalog.md` (both have exactness tests). Put these under a new
  "Mission 10 — Operational profiles" CI step.
- After each stage append one entry to `docs/progress.md` in its existing format
  (Status / Deviations). Never edit earlier entries.
- Where this prompt leaves a detail unspecified, choose the option most consistent with
  existing code, and list the choice under "Deviations".

---

## Stage 1 — Tolerate a malformed OPTIONAL extracted field

The idea: during acceptance testing, the model sometimes returned a non-scalar value (e.g.
an object or list) for an optional field such as a severity-like field. Strict parsing
rejected the ENTIRE report, so a perfectly usable report was lost. The rule we want:
a bad optional value is dropped, not the report. We never invent a value the source did not
establish.

Implementation:
- Find the extraction response parser. Determine which fields are optional and what happens
  today when one of them has a wrong type or invalid value.
- If the whole report is rejected: normalize an invalid OPTIONAL field to null, emit a
  structured INFO log record (`extraction_optional_field_dropped`, naming the field and the
  received type — never the full value) through the existing logging machinery, and continue.
- Required fields and an out-of-registry classification keep their current behavior exactly.

Tests: invalid optional field → field null, report proceeds, log record present;
invalid required field → unchanged behavior; valid values → unchanged.

## Stage 2 — One retry of extraction on a model timeout or model error

The idea: reports were lost when the model provider timed out during extraction. The other
branch solved this with keyword-based extraction, which we reject (constraint 7). Here the raw
text is already persisted before extraction runs, so the report itself is not lost — but the
event ends as failed after a single transient timeout. A single retry covers the transient case.

Implementation:
- Confirm the raw text is persisted before extraction; confirm what outcome and notification
  a timeout currently produces.
- Retry the extraction call exactly once on `AgentTimeoutError` / `AgentModelError`
  (or their current equivalents), mirroring the existing retry-once pattern used for task
  formulation and success judgment. Do not retry on a parse error that Stage 1 now tolerates,
  and do not change MODEL_TIMEOUT_SECONDS or the zero provider-retry configuration.
- Log the retry with its cause (existing retry logging style).
- If the second attempt also fails, keep the existing failure outcome and notification path.

Tests: first attempt times out, second succeeds → normal flow and one retry log;
both fail → existing failure path; never more than two attempts.

## Stage 3 — Availability fields for absence reports

The idea: a team member reports being unavailable ("Michael: I won't be available, family
matter") without saying from when to when. The other branch found these reports were
discarded because the interval was missing. Correct behavior: store what was reported
(unavailable + reason), keep the interval unknown, and ask the reporter for it — never guess
a time range. This codebase already has the exact mechanism for "ask the reporter for missing
data": the required-fields gate plus the `event_data` hold. Only the fields are missing.

Implementation:
- Add three fields to the fixed event-data field vocabulary:
  `availability_start` (timestamp), `availability_end` (timestamp), `absence_reason` (text).
  Timestamps use the existing storage timestamp format.
- One idempotent migration adding the three nullable columns to `events`, plus fresh-schema
  DDL, plus `history/field_catalog.py` entries (narrative category, clear English label and
  meaning), plus the extraction schema/prompt field list, plus whatever validation checks
  `required_event_fields` and `EVENT_TYPE_REQUIRED_FIELDS` against the vocabulary.
- Behavior comes entirely from the existing gate: when an event type declares
  `availability_start`/`availability_end` as required and they are missing, the event is
  persisted with whatever was extracted, and an `event_data` hold asks the reporter only for
  the missing fields. Do not add a new hold kind. Do not call it a clarification hold —
  clarification holds remain classification-only in this codebase.
- If the reporter's reply includes the interval, the existing reply-parsing path fills the
  fields and resumes; validate that end is not before start (reject → ask again, using the
  existing mechanism's behavior for an invalid reply).

Tests: absence with an interval → proceeds; absence without an interval → persisted, fields
null, `event_data` hold listing exactly the two missing fields; reply fills them and the event
resumes; end-before-start rejected; migration works on an existing DB and a fresh DB.

## Stage 4 — The two operational profiles

The idea: SEC and FIRE are different organizations with different vocabularies, resources and
protocols, but the same agents framework, orchestration and persistence. In this codebase,
that is exactly what a profile module is. Protocol eligibility ("FIRE-only protocols are not
available to SEC") comes for free: a protocol that is not in a profile does not exist in that
deployment.

Create `profiles/response_team.py` and `profiles/fire_station.py`, following
`docs/profile_spec.md` and `profiles/demo.py`. Each declares every required attribute
(PROFILE_NAME, DEFAULT_LANGUAGE="he", MAX_ITER, MODEL_TIMEOUT_SECONDS, DB_PATH, API port,
BOT_TOKEN_ENV, model credential envs, event types, areas, agents, protocols,
EVENT_TYPE_REQUIRED_FIELDS). Use distinct default ports and DB paths for the two profiles.
Write all content in English.

Agents and tools follow the reference agent's pattern exactly (`@tool`, explicit
`side_effecting` / `idempotent`). There is no external system integration in this task: a
side-effecting tool records what it did and returns a precise text result. That result is
persisted by the existing step-execution recording, which is what makes a request durable and
readable later. A tool result must state only the tool's own effect ("friendly-force dispatch
request recorded for west_gate"), never an unobserved outcome ("force dispatched",
"force arrived").

### 4a. Response team (SEC) — `profiles/response_team.py`
Areas: `west_gate`, `east_gate`, `sector_a`, `sector_b`, `sector_c`, `perimeter_fence`,
`control_room`.

Event types (the description text is what extraction uses to classify — write each
description so it states what belongs in it and what does not):
- `perimeter_observation` — unusual vehicles, heavy equipment, people or activity at or near
  the perimeter or a gate (e.g. "heavy equipment parked near the western gate"). Required: area.
- `external_force_observation` — a vehicle or force not belonging to the team observed in or
  near the site. Required: area.
- `surveillance_fault` — a camera offline, degraded, or physically damaged, including a
  physically cut camera communications cable. Camera identifiers go into `entities`.
  The description must instruct extraction to record the physical observation (what was seen:
  "cable physically cut, camera offline") in the description field, and to keep any stated
  cause or suspicion from the reporter as the reporter's claim, not as fact.
  Required: area. If `entities` is part of the required-field vocabulary, also require it;
  if not, do not add it to the vocabulary in this stage — just report it.
- `team_status` — a team member's own movement or position (e.g. "Gil: on my way to sector B").
  Required: area (the destination or current sector).
- `attendance` — a team member reporting unavailability. Required: `availability_start`,
  `availability_end` (Stage 3). `absence_reason` optional.

Agents and tools:
- `security_ops_agent`:
  - `log_observation` — side_effecting=True, idempotent=True. Records an observation note.
  - `notify_team` — side_effecting=True, idempotent=False. Records that a team notification
    was issued.
  - `request_friendly_force_dispatch` — side_effecting=True, idempotent=False. Records a
    dispatch request for a named area.
  - `recall_drone` — side_effecting=True, idempotent=False. Records a drone recall request.
- `surveillance_agent`:
  - `log_camera_fault` — side_effecting=True, idempotent=True. Records a fault per camera
    identifier provided.
  - `request_technician` — side_effecting=True, idempotent=False.
- `roster_agent`:
  - `record_availability` — side_effecting=True, idempotent=True.

Protocols (criticality / approval_flag):
- `perimeter_check` — log_observation, notify_team — MEDIUM / False.
- `external_force_response` — log_observation, request_friendly_force_dispatch — HIGH / True.
- `surveillance_fault_response` — log_camera_fault, request_technician — MEDIUM / False.
- `team_movement_log` — log_observation — LOW / False.
- `attendance_update` — record_availability — LOW / False.
- `drone_recall` — recall_drone — MEDIUM / True.

Each protocol's description and expected success output must be specific enough for protocol
selection to choose it, and must describe the recorded effect, not a real-world outcome.

### 4b. Fire and Rescue (FIRE) — `profiles/fire_station.py`
Areas: `district_north`, `district_south`, `district_center`, `industrial_zone`.

Declare the named mutual-aid resources as a profile-level constant:
`MUTUAL_AID_RESOURCES = ("ASHED", "CARMEL")`. These names are identifiers only — do not
invent capabilities for them. The mutual-aid tool accepts only a name from this constant and
returns a refusal text for anything else.

Event types:
- `structure_fire` — fire in a building or structure. Required: area.
- `hazmat_fire` — fire involving hazardous materials, gas, chemicals, or an industrial
  facility. Required: area.
- `rescue` — a person trapped or in danger without fire. Required: area.
- `attendance` — same definition and required fields as in SEC.

Agents and tools:
- `dispatch_agent`:
  - `dispatch_station_crew` — side_effecting=True, idempotent=False.
  - `request_mutual_aid` — side_effecting=True, idempotent=False; argument: resource name from
    MUTUAL_AID_RESOURCES.
- `hazmat_agent`:
  - `request_hazmat_assessment` — side_effecting=True, idempotent=False.
- `roster_agent` — same as SEC (define it in the FIRE profile too; do not import agents across
  profile modules unless Stage 5 confirms that is supported).

Protocols:
- `structure_fire_response` — dispatch_station_crew — HIGH / False.
- `hazmat_response` — dispatch_station_crew, request_hazmat_assessment — HIGH / True.
- `mutual_aid_request` — request_mutual_aid — HIGH / True.
- `rescue_response` — dispatch_station_crew — MEDIUM / False.
- `attendance_update` — record_availability — LOW / False.

### 4c. Entities for multi-camera reports
The idea: one message can report several cameras ("CAM-03 and CAM-04 are down"). The other
branch added a special camera_ids field; we use the existing `entities` list instead.
- Verify `entities` is stored as a list and is populated by extraction. If the extraction
  prompt/schema does not currently ask the model for entities clearly enough to capture
  every identifier in a message, adjust the generic instruction (core, no domain terms):
  "list every identifier the report refers to, e.g. equipment or unit identifiers".
- No camera-state table, no "active cameras" projection in this task.

Tests (new file `tests/test_operational_profiles.py`):
- each profile loads and validates through `profiles.loader.load_profile`;
- each profile exposes exactly its declared event types, areas, agents and protocols;
- no FIRE protocol or tool name exists in the SEC profile and vice versa;
- every side-effecting tool declares `idempotent` correctly as specified above;
- `request_mutual_aid` refuses a name not in MUTUAL_AID_RESOURCES;
- the two profiles run side by side as two deployments with separate databases and ports
  (reuse the pattern of the existing profile-isolation integration tests).

## Stage 5 — Simulation as a separate deployment

The idea: rehearsals must never touch live data. The other branch built in-process simulation
scopes; here a simulation is simply another deployment of the same profile content with its own
DB_PATH, API port and BOT_TOKEN_ENV (a separate Telegram bot). A real person joins a simulation
by messaging the simulation bot; their live registration is untouched because the simulation
deployment has its own users table.

Implementation:
- Check whether a profile module can import content (event types, areas, agents, protocols)
  from another profile module and still pass loading/validation, including the profile-file
  hash used by GET /SYSTEM. If yes: add `profiles/response_team_sim.py` and
  `profiles/fire_station_sim.py` that reuse the live content and override only deployment
  values (DB_PATH, port, BOT_TOKEN_ENV, PROFILE_NAME with a "(Simulation)" suffix).
  If no: STOP and propose options.
- Confirm the bot singleton lock is per-deployment (beside the database) so live and
  simulation bots run concurrently.
- Document in `docs/operator_guide.md`: starting a simulation deployment, provisioning its
  users (including its own bot-service identity) with `cli/user_admin` against the simulation
  profile, that simulation data can never reach the live database, and that participants use
  the simulation bot.

Test: a live deployment and a simulation deployment of the same profile share no events,
precedents, notifications or users (extend `tests/test_operational_profiles.py`).

## Stage 6 — Acceptance scenarios as offline regression tests

The idea: the other branch validated its work manually through a browser and real Telegram.
We keep the scenarios but run them offline and deterministically, driven through the real API
with the model boundary faked (as the existing integration tests do). These tests verify
pipeline wiring — classification routing, required-field gating, holds, approval, persisted
fields, notifications — not model quality. Assert only what the system guarantees; never
assert model wording.

New file `tests/test_operational_scenarios.py`. Each scenario scripts the fake model's
extraction/risk/selection/formulation/judgment responses for its step.

SEC scenarios (response_team profile):
1. Absence without interval — viewer "michael" reports being unavailable with a reason, no
   times. Expect: `attendance` event persisted, absence_reason stored, availability fields null,
   `event_data` hold for exactly availability_start and availability_end, notification to
   michael. Then michael replies with an interval → fields stored, event resumes to
   `attendance_update`, outcome succeeded.
2. Heavy equipment near the western gate — viewer report. Expect `perimeter_observation`,
   area west_gate, protocol `perimeter_check`, outcome succeeded, visible to a commander's
   history question.
3. Team member in transit — viewer "gil" reports travelling to sector B. Expect `team_status`,
   area sector_b, persisted.
4. Two cameras in one message — report that CAM-03 and CAM-04 are offline. Expect
   `surveillance_fault`, entities contains both identifiers, protocol
   `surveillance_fault_response`, one log_camera_fault result per camera.
5. Cut communications cable — report that CAM-03's cable is physically cut. Expect
   `surveillance_fault`, CAM-03 in entities, description records the physical observation.
6. External-force vehicle — viewer report. Expect `external_force_observation`, protocol
   `external_force_response`, approval hold (approval_flag), NOT executed before approval.
   Commander approves → executes, step result says a dispatch request was recorded, outcome
   per judgment. A second variant where a commander rejects → declined, no tool executed.

FIRE scenario (fire_station profile):
7. Hazardous fire — viewer report of fire at a chemical facility in the industrial zone.
   Expect `hazmat_fire`, area industrial_zone, protocol `hazmat_response`, approval hold, no
   tool executed before approval. A variant submitted as a commander's own request confirms
   the existing commander-bypass rule still applies unchanged.

Robustness (either profile):
8. Extraction times out once then succeeds → event proceeds normally (Stage 2).
9. Extraction returns a malformed optional field → report proceeds with that field null
   (Stage 1).

## Stage 7 — The tool-result rule in documentation and judgment

The idea: a recorded request is not a confirmed execution. The success judgment must never
report "force arrived" because a tool recorded a dispatch request.
- Add the rule to `docs/agent_authoring.md`: a tool result proves only the tool's own defined
  effect; tools must word results accordingly; unobserved real-world outcomes are never
  claimed.
- Add the same rule to the success-judgment prompt, in whichever module that prompt text
  currently lives (verify; do not move it).
- Test: the judgment prompt contains the rule; a scripted run where the tool result says
  "dispatch request recorded" does not produce a result claiming arrival (assert on the prompt
  content and on the stored step result, not on model wording).

---

## Out of scope — do not implement, even partially
Operational units, memberships, runtime context objects, in-process LIVE/Simulation scopes,
Telegram-to-run binding, simulator UI, dashboards for unit selection, fixed request buttons,
keyword-based extraction, camera/team state projections or situational-picture counters,
tool-receipt objects, compound report+question decomposition, incident aggregation.

## Definition of done (after Stage 7)
Full offline suite green; `tests/test_architecture.py`, `tests/test_file_catalog.py`,
`tests/test_hebrew_leakage.py` and `tests/test_messages.py` pass; all new test files are in
CI and the file catalog; one `docs/progress.md` entry per stage; a final summary listing every
deviation, every assumption about existing code that turned out to be wrong, and anything left
for me to decide.