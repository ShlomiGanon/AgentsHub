# Profile Split Plan

Status: **implemented**. §9's 7 steps are complete on branch `simulator1` (see §13, "Implementation
deviations," for every place the implementation diverged from what §§1–11 originally described).
The full `pytest` suite passes (1470 tests) after every step. §12 ("Manual verification checklist")
still needs a human running the real stack — it needs a real model call, Telegram/simulated traffic,
and a browser session, none of which the implementation step was allowed to use. Sections 1–11 below
are left as originally written (the approved plan); §12 and §13 were added during implementation.

---

## Decisions log (owner-resolved, this revision)

1. **Shared agent stays.** `agents/friendly_forces_agent.py` (the `FriendlyForcesAgent` class, registry key
   `"friendly_forces_agent"`) is unchanged and reusable by both new profiles. Only the `friendly_forces`
   **profile** (`profiles/friendly_forces.py`, its port, its DB path, every reference to that module) is
   deleted. "The name `friendly_forces` must not remain anywhere" referred to the profile, not the shared agent.
2. **Rename-and-trim approach approved, with a condition.** `unified_test.py` → `standby_squad.py` is the
   approved path. **All** existing protocols are deleted first — from `unified_test.py`, `friendly_forces.py`,
   `demo.py`, `sub_agent_surveillance.py`, and `sub_agent_team_status.py` alike — and each new profile's
   protocol set is rebuilt from nothing but that profile's own 3 simulations' steps. §5 now states this
   explicitly and lists every deleted protocol by name and source file.
3. **Sub-agent classes**: `SubAgentSurveillanceAgent`/`SubAgentTeamStatusAgent` move into `profiles/standby_squad.py`
   (as `StandbySquadSurveillanceAgent`/`StandbySquadTeamStatusAgent`, still two separate sub-agents) **before**
   `sub_agent_surveillance.py`/`sub_agent_team_status.py` are deleted — §4.1 and §9 now make this sequencing and
   destination explicit.
4. **Firefighting `AREAS`**: the derived list stands but is marked **PENDING OWNER REVIEW** in §5.2, with the
   exact scenario text quoted per area.
5. **Test sequencing**: `tests/test_server_control.py` and `tests/test_unified_role_and_security.py` (plus every
   other test file touched in §8.2) are rewritten in the **same** implementation step as the code they cover, not
   in a later cleanup pass — §9 is reordered around this; the suite is green after every step, not just at the end.
6. **New Firefighting tools approved for planning**: `dispatch_water_tankers`/`dispatch_aircraft` on
   `FirefightingExternalForcesAgent` — §4.2 now gives each a full contract (parameters, output, criticality,
   approval) tied to the specific step that needs it.
7. **Admin dropdown isolation**: added as an explicit verification step (§10) — each profile's simulator page
   must show only that profile's own 3 simulations, checked with both profiles running.

---

## 1. Goal and scope

Collapse the current profile set down to exactly two runnable profiles — **Standby Squad** (military /
readiness-team domain) and **Firefighting** (fire-and-rescue domain) — each backed by exactly three of
the six simulations already visible in the admin scenario simulator's "סימולציות הפרופיל" dropdown
(`/admin/simulator`). Every other profile module is deleted. Protocols are rebuilt from scratch, one set
per profile, each entry justified by at least one step of that profile's own three simulations. This
document is the plan; implementation is a separate, later step.

---

## 2. Current state findings

### 2.1 Correcting the stated assumption about `profiles/`

| File | What it actually is | Correction vs. the assumption in the task |
|---|---|---|
| `profiles/contracts.py` | Infrastructure — `LoadedProfile`, `AgentSpec`, `REQUIRED_PROFILE_ATTRS`, `PROTOCOL_REQUIRED_ATTRS`. | Confirmed. |
| `profiles/loader.py` | Infrastructure — `load_profile()`, `validate_profile()`, simulation-declaration validation. | Confirmed. |
| `profiles/simulation.py` | Infrastructure — `SimulationPersona`/`SimulationGroup`/`SimulationScenario`/`SimulationRoster` dataclasses, reserved-ID helpers. | Confirmed. |
| `profiles/simulation_provisioning.py` | Infrastructure — `ensure_simulation_entities()`, idempotent user/group/roster provisioning. | Confirmed. |
| `profiles/template.py` | The copy-me template. | Confirmed. |
| `profiles/demo.py` | A real, standalone, runnable profile (`PROFILE_NAME = "For Tests"`, port 8902, `ReferenceAgent`, 4 protocols). | Confirmed as a "current profile." |
| `profiles/friendly_forces.py` | A real, standalone, runnable profile (`PROFILE_NAME = "Friendly Forces"`, port 8903, `FriendlyForcesAgent`, 4 dispatch protocols). | Confirmed. |
| `profiles/unified_test.py` | A real, standalone, runnable profile (port 8905) — **and much more than "a profile"**: it already combines all three agent types (surveillance, team status, friendly forces) into one profile, already declares `SIMULATOR_PORT`, and **already declares all six of the target simulations** (see §2.2). | Confirmed as a "current profile," but it is the closest existing precedent for what "Standby Squad" needs to become. |
| `profiles/sub_agent_surveillance.py` | **Not a sub-agent module** — it is a full, standalone, independently-runnable profile in its own right: `PROFILE_NAME = "sub agent surveillance"`, its own `API_PORT` (8904), its own `DB_PATH`, its own 8 protocols, its own `EVENT_TYPES`/`AREAS`. It happens to declare exactly one agent (`SubAgentSurveillanceAgent`, tier `"sub"`). | **Correction**: this is a profile, not a sub-agent file. Only its `AGENTS` entry (the agent class) is reusable "as a sub-agent" inside a bigger profile. |
| `profiles/sub_agent_team_status.py` | Same shape and same correction as above: a full standalone profile (`PROFILE_NAME = "sub agent team status"`, `API_PORT = 8903` — **collides with `friendly_forces.py`'s port**, harmless today only because the two are never run in the same process at once), declaring one agent (`SubAgentTeamStatusAgent`). | Same correction. |

Net effect: today there are **six** independently loadable, `REQUIRED_PROFILE_ATTRS`-complete profile modules
(`demo`, `friendly_forces`, `sub_agent_surveillance`, `sub_agent_team_status`, `unified_test`, plus
`template.py` which is explicitly the non-runnable template), not "three profiles + two sub-agent files."
`config/server_control.py:discover_profiles()` confirms this by construction — it globs every `profiles/*.py`
except `_`-prefixed files, `contracts`, `loader`, and `template`, and currently discovers all five real
profiles above.

### 2.2 Where the six simulations come from today (the key finding)

**They are not hardcoded in the admin frontend, and they do not need a new mechanism built.** They already
live entirely inside `profiles/unified_test.py`, using the exact `SimulationPersona`/`SimulationGroup`/
`SimulationScenario`/`SimulationRoster` mechanism `docs/profile_simulations_design.md` designed — and that
design has **already been fully implemented** (see that doc's §8–§12, "implementation notes" / "bundled-fixture
migration" / "legacy path removed entirely"). Concretely, in `profiles/unified_test.py`:

- `SIMULATIONS` (module-level list, built with three `.append()` calls after the initial declaration) holds
  **seven** `SimulationScenario` entries today, not six:
  1. `overall_picture_query` — the original pilot/demo scenario (tags `("demo",)`), 1 step. **Not** one of
     the six named in this task.
  2–4. `sec001_phase1` / `sec001_phase2` / `sec001_phase3` — `scenario.id` `SEC_001_PHASE_1/2/3`, titles
     matching the task's Standby Squad items 1–3 **exactly**, 9/9/10 steps respectively.
  5–7. `fire002_phase1` / `fire002_phase2` / `fire002_phase3` — `scenario.id` `FIRE_002_PHASE_1/2/3`, titles
     matching the task's Firefighting items 4–6 **exactly**, 7/7/9 steps respectively.
- `SIMULATION_USERS` holds 27 personas (offsets 0–26): 2 for the pilot, 15 for SEC_001 (offsets 2–16), 10 for
  FIRE_002 (offsets 17–26).
- `SIMULATION_GROUPS` holds 6 groups (offsets 0–5): `response_team` (0, reused by both the pilot and SEC_001),
  `cameras`/`external_forces` (1–2, SEC_001), `fire_response_team`/`fire_cameras`/`fire_external_forces` (3–5,
  FIRE_002).
- `SIMULATION_ROSTERS` holds one entry, `team_status`, pointing at `open_team_status_persistence` /
  `UNIFIED_TEAM_STATUS_DB_PATH` — response-team-member personas from both series declare
  `pre_approved_rosters=("team_status",)` so `TeamStatusAgent`'s own separate roster store (outside the main
  `users` table) recognizes them.
- The Hebrew step text lives in `messages/he.py` under `unified.simulation.sec001.*` / `unified.simulation.fire002.*`
  keys (verified verbatim against `fixtures/admin_scenarios/*.json` — see below), with English parity translations
  in `messages/en.py` (required by `tests/test_hebrew_leakage.py`/catalog-parity tests).

**Serving path** (already built, nothing to design):

- `api/simulations.py` — `simulation_catalog_payload(loaded_profile)` (metadata list for the dropdown) and
  `materialize_simulation(scenario, users, groups)` (resolves persona/group *keys* to deterministic reserved
  Telegram IDs, `{scenario, chats, steps}` shape unchanged) — pure logic, computed fresh from the already-loaded
  `LoadedProfile` on every call, no DB round trip.
- `api/routes.py` — `GET /Simulations` and `GET /Simulations/<key>`, commander-only
  (`RequestedOperation.VIEW_SIMULATIONS`, `auth/permissions.py`).
- `api/admin_simulator.py` — the "סימולציות הפרופיל" `<select id="profile-simulation-select">` (line 261) is
  populated purely by calling `GET /Simulations` (line 990) and feeding the result into the existing
  `loadScenario()` function; picking one entry and clicking "load" calls `GET /Simulations/<key>` (line 1013).
  **Nothing about the simulation list is hardcoded client-side** — this already satisfies the task's "computed
  in real time from the loaded profile" requirement.
- `profiles/simulation_provisioning.py:ensure_simulation_entities()` is called once from `api/app.py:build_context()`
  right after `persistence` opens, so every declared persona/group/roster exists (idempotently) before the admin
  page or a real bot request can reference it.

**Historical origin**: `fixtures/admin_scenarios/*.json` (six files, Hebrew filenames matching the task's two
series, "event_stream"-shaped legacy format) are the original source the SEC_001/FIRE_002 text was transcribed
from. `docs/profile_simulations_design.md` §11 confirms the *old* consumer of these files
(`api/admin_scenarios.py`, the "Bundled examples" dropdown + manual ID-mapping panel) **was already deleted
outright** once migration finished. The fixture files themselves are left on disk, read by no code path —
purely historical reference. I verified this by reading one (`כיתת כוננת - חלק 1.json`): its
`event_stream[0].timestamp` is `2026-09-06T07:30:00Z`, matching `docs/bot_simulation_mode_design.md`'s own
worked example and the task's "known example" description of SEC_001_PHASE_1 word-for-word (סד"כ collection,
minor network faults/maintenance, perimeter reports with no defined threat — see
`unified.simulation.sec001.phase1.description` in `messages/he.py:880`).

### 2.3 The scenario simulator page

- Frontend: `api/admin_simulator.py` (HTML/CSS/JS constants + the one data-gathering helper; route/session/chrome
  live in `api/admin.py`).
- Backend: `api/admin.py` mounts it at `/admin/simulator`; message-kind steps proxy through
  `POST /admin/simulator/bot-msg` to `bot/simulator_app.py` (a dedicated simulation-mode bot process, started
  by `run_stack.py` alongside `api.app`/`bot.app` when the profile declares `SIMULATOR_PORT`), which runs the
  step through the real bot's own handler code (`bot/app.py`, unmodified); event-kind (sensor) steps go straight
  to `POST /Event`.
- The profile-switch admin page (separate from the simulator) already exists too: `api/admin.py:1392` renders a
  dropdown from `discover_profiles()`, and `switch_profile()` (line 1415) submits a `switch_profile` command via
  `config/server_control.submit_server_command`, consumed by `run_stack.py`'s `StackSupervisor` loop.

### 2.4 Protocol/Agent contracts (for §5)

- `protocols/contracts.py`: `Protocol` is a frozen dataclass —
  `name, description, participating_agents, approved_tools, expected_success_output, criticality
  (CriticalityLevel: LOW/MEDIUM/HIGH), approval_flag, requires_confirmation=False, commander_only=False`.
  `PROTOCOL_REQUIRED_ATTRS` (profiles/contracts.py) only requires the first seven of these (no `requires_confirmation`/
  `commander_only` requirement) — `unified_test.py` sets the two extra fields on every protocol; `friendly_forces.py`/
  `demo.py`/the two `sub_agent_*.py` profiles do not set them (they default to `False`). I recommend the two new
  profiles follow `unified_test.py`'s fuller style (set both explicitly) since it is more precise and is already
  proven in production use.
- `docs/agent_authoring.md`: 5-step recipe — subclass `agents.base.Agent`, set `name`/`role`/`system_prompt`,
  implement tools with `@tool(...)`, accept `model` in `__init__`, declare via `profiles.spec.AgentSpec` in a
  profile's `AGENTS` list (never construct directly). Reusing an existing agent class in a new profile (rebinding
  its DB path / localizing its text, the pattern every current profile already uses for `SurveillanceAgent`/
  `TeamStatusAgent`/`FriendlyForcesAgent`) needs no new authoring — it is exactly steps 4–5 reused. Adding a
  *new* tool to an agent (not just rebinding) is steps 1–3 again.

---

## 3. Target architecture

### 3.1 The two profiles

| | Standby Squad (כיתת כוננות) | Firefighting (כיבוי אש) |
|---|---|---|
| Module | `profiles/standby_squad.py` (new) | `profiles/firefighting.py` (new) |
| Built from | `unified_test.py` (renamed-and-trimmed) — its `friendly_forces.py`-derived agent already uses the **unchanged, shared** `agents.FriendlyForcesAgent` class (decision 1: only the `friendly_forces` profile is deleted, not the agent), and its two other agents are the same classes `sub_agent_surveillance.py`/`sub_agent_team_status.py` declare (decision 3: those two files' agent classes move into this profile before deletion) | `profiles/template.py`, populated with 3 new profile-bound sub-agents (see §4.2), one of which also uses the shared `agents.FriendlyForcesAgent` class as its base |
| `PROFILE_NAME` | `"כיתת כוננות"` (Hebrew, catalog-driven, English parity `"Standby Squad"`) | `"כיבוי אש"` (Hebrew, catalog-driven, English parity `"Firefighting"`) |
| `DEFAULT_LANGUAGE` | `"he"` | `"he"` (matches the six simulations' language) |
| `API_PORT` | `8905` (reused from `unified_test.py` — same successor role, minimizes operator/bookmark churn) | `8906` (new) |
| `SIMULATOR_PORT` | `8915` (reused from `unified_test.py`) | `8916` (new) |
| `BOT_TOKEN_ENV` | `"BOT_TOKEN"` (reused — same convention `demo`/`friendly_forces`/`unified_test` already share) | `"FIREFIGHTING_BOT_TOKEN"` (new, dedicated — see §8 rationale: keeps Option B of §7.4 possible without forcing a shared token) |
| `DB_PATH` dir | `data/standby_squad/` (renamed from `data/unified_test/`) | `data/firefighting/` (new) |
| Agents | `StandbySquadSurveillanceAgent`, `StandbySquadTeamStatusAgent` — **kept as two separate sub-agents**, per instruction — plus `StandbySquadForcesAgent` (the merged-in friendly-forces specialist) | `FirefightingSurveillanceAgent`, `FirefightingCrewStatusAgent`, `FirefightingExternalForcesAgent` — 3 sub-agents, justified in §4.2 |
| Simulations | `sec001_phase1/2/3` (unchanged content, offsets renumbered from 0) | `fire002_phase1/2/3` (unchanged content, offsets renumbered from 0) |

Reasoning for reusing port 8905/8915 for Standby Squad: `unified_test.py` already *is*, architecturally, a
three-agent unified profile whose SEC_001 simulations are the ones Standby Squad needs — the lowest-risk path is
to treat this as a **rename-and-trim** of `unified_test.py` (drop FIRE_002 + the pilot scenario + all existing
protocols, rebuild protocols per §5, rename the `Unified*Agent` classes), not a from-scratch merge of
`friendly_forces.py` + the two `sub_agent_*.py` files. This preserves nearly all of `unified_test.py`'s already-
working persistence wiring, seed data, and role/security test coverage (see §8).

### 3.2 File layout after the split

```
profiles/
  __init__.py            (unchanged)
  contracts.py            (unchanged)
  loader.py                (unchanged, or 1-line default-profile update — see §8)
  simulation.py            (unchanged)
  simulation_provisioning.py (unchanged)
  template.py              (unchanged)
  standby_squad.py         (new — replaces unified_test.py's role)
  firefighting.py          (new)
# deleted: demo.py, friendly_forces.py, sub_agent_surveillance.py,
#          sub_agent_team_status.py, unified_test.py
```

Only two profile modules remain discoverable by `discover_profiles()` after the split.

---

## 4. Profile-by-profile plan

### 4.1 Standby Squad (`profiles/standby_squad.py`)

**Files removed**: `profiles/friendly_forces.py`, `profiles/sub_agent_surveillance.py`,
`profiles/sub_agent_team_status.py`, `profiles/unified_test.py` (all four; `unified_test.py`'s content is
carried forward into the new file, not literally deleted-and-lost).

**Agents** (`AGENTS` list, all `tier="sub"`, mirroring today's `unified_test.py` exactly in shape):

| Agent class (new name) | Base class | Justification |
|---|---|---|
| `StandbySquadSurveillanceAgent` | `agents.SurveillanceAgent` | SEC_001's cameras/drones content across all 3 phases (camera faults, a cut comms cable, a tactical mobile camera spotting a suspect) needs camera status + observation-recording + drone-recon tools. Renamed from `UnifiedSurveillanceAgent`; binds to `standby_squad.py`'s own `SURVEILLANCE_DB_PATH`. |
| `StandbySquadTeamStatusAgent` | `agents.TeamStatusAgent` | SEC_001's response-team members reporting availability/unavailability (reserve duty, fever) and the commander's roster queries need attendance + roster tools. Renamed from `UnifiedTeamStatusAgent`; binds to its own `TEAM_STATUS_DB_PATH`. |
| `StandbySquadForcesAgent` | `agents.FriendlyForcesAgent` | The merged-in `friendly_forces.py` specialist — SEC_001 phase 2/3 need ambulance/police/military dispatch (gunshot casualty, YASAM/YAMAG units). Renamed from `UnifiedFriendlyForcesAgent` (which is itself already exactly this). |

**Kept as two separate sub-agents, per instruction**: `StandbySquadSurveillanceAgent` and
`StandbySquadTeamStatusAgent` are declared as two independent `AgentSpec` entries, never merged into one class —
exactly as `unified_test.py` already does today.

**Where the two sub-agent classes live after `sub_agent_surveillance.py`/`sub_agent_team_status.py` are deleted,
and the move-before-delete sequencing (decision 3)**: `SubAgentSurveillanceAgent` and `SubAgentTeamStatusAgent`
are, today, each a thin subclass of `agents.SurveillanceAgent`/`agents.TeamStatusAgent` that only rebinds a DB
path (`profiles/sub_agent_surveillance.py:26-29`, `profiles/sub_agent_team_status.py:26-33`). `unified_test.py`'s
own `UnifiedSurveillanceAgent`/`UnifiedTeamStatusAgent` are independent instances of that exact same "rebind +
localize text" pattern, against the exact same two base classes. The move is: `standby_squad.py` (created in
implementation Step 1, §9) carries forward `unified_test.py`'s `UnifiedSurveillanceAgent`/`UnifiedTeamStatusAgent`
bodies, renamed to `StandbySquadSurveillanceAgent`/`StandbySquadTeamStatusAgent` — this **is** "the sub-agent
classes from `sub_agent_surveillance.py`/`sub_agent_team_status.py`, moved," not a separate, third copy, since
all three files' versions are the same base-class-rebind pattern with only the DB path/text differing. Concretely:
after Step 1, `profiles/standby_squad.py` contains both classes; `profiles/sub_agent_surveillance.py` and
`profiles/sub_agent_team_status.py` are **only deleted in Step 3**, strictly after Step 1 has been created and its
own load/test verification (below) has passed — so the classes exist in their new home before their old
standalone-profile home is removed, never the other way round.

**Only the `friendly_forces` profile is deleted (decision 1)** — concretely this means:
- No file named `profiles/friendly_forces.py`.
- No `PROFILE_NAME = "Friendly Forces"` string.
- No `module_path = "profiles.friendly_forces"` reference anywhere (see §8 for every current reference).
- **`agents/friendly_forces_agent.py` (the `FriendlyForcesAgent` class, registry key `"friendly_forces_agent"`)
  is explicitly kept unchanged** and reused by both `StandbySquadForcesAgent` (this profile) and
  `FirefightingExternalForcesAgent` (§4.2) — resolved per decision 1, no rename.

**Event types / areas**: see §5.1 (derived from the protocol set, kept minimal).

### 4.2 Firefighting (`profiles/firefighting.py`)

**Built from `profiles/template.py`.**

**Agents** — I chose **3** sub-agents (the max of the suggested 2–3 range), because FIRE_002's three phases
genuinely exercise three distinct capability domains, exactly mirroring the domains Standby Squad needs (just
fire-themed) — collapsing any two of them would force one agent to own two unrelated tool surfaces, which
`docs/agent_authoring.md` implicitly discourages (one `role`, one coherent set of tools per agent):

| Agent class | Base class | Justification (traced to FIRE_002 step content) |
|---|---|---|
| `FirefightingSurveillanceAgent` | `agents.SurveillanceAgent` (rebind, no new tools) | Fire cameras + a thermal sensor (phase 1 step 3), a paused camera for lens cleaning (phase 1 step 5), smoke/fire detection on camera (phase 2 step 1), a camera blinded by smoke/glare (phase 2 step 4), a tactical drone-mounted camera showing the fire reaching a gas tank (phase 3 step 6) — this is the same "camera status + observation + drone recon" tool surface `SurveillanceAgent` already provides, just rebound to this profile's own DB. |
| `FirefightingCrewStatusAgent` | `agents.TeamStatusAgent` (rebind, no new tools) | Opening-shift roster declaration (phase 1 step 1: "6 firefighters in team A, engines Ashed-3 and Carmel-1 fully operational"), a crew member's planned absence for a medical check (phase 1 step 2, recalled again in phase 2 step 3) — the same attendance/roster tool surface `TeamStatusAgent` already provides. |
| `FirefightingExternalForcesAgent` | `agents.FriendlyForcesAgent`, **extended with two new tools** | Phase 2 step 6 ("dispatching our 2 bulldozers, need sector coordination") and phase 3 step 7 ("sending 4 tanker trucks from a neighboring station + 2 firefighting aircraft") describe dispatch actions `FriendlyForcesAgent`'s existing four tools (`dispatch_ambulance`/`dispatch_police`/`dispatch_firefighters`/`dispatch_military`) do not cover — a water-tanker/aircraft mutual-aid dispatch is a materially different action, not a relabeling of "dispatch firefighters." Two new tools are added: `dispatch_water_tankers` and `dispatch_aircraft` (same shape as the existing four: `location`, a count/quantity field, `note`). `dispatch_police`/`dispatch_ambulance` are still used as-is for the phase 3 police-cordon and casualty-adjacent needs. |

**This is the one place Firefighting's build genuinely extends agent capability rather than only reusing it** —
called out explicitly per the task's instruction to flag and justify any such change, and kept minimal (two new
tool *methods* on an existing, already-battle-tested agent class — not a new base class, not a new persistence
store).

**Tool contracts (decision 6)** — same shape as `FriendlyForcesAgent`'s existing four dispatch tools
(`dispatch_ambulance` etc., `profiles/unified_test.py:720-774`): a `@tool(..., side_effecting=True, idempotent=False)`
method that records the dispatch and returns a confirmation string; `location`/`note` free text, a numeric
resource count, both required (`docs/agent_authoring.md` step 3):

| Tool | Triggering step | Parameters | Output | Criticality / approval (via `dispatch_mutual_aid`, §5.2) |
|---|---|---|---|---|
| `dispatch_water_tankers(location: str, tanker_count: int = 1, source_station: str = "", note: str = "")` | Phase 3, step 7 — *"sending 4 tanker trucks (מיכליות מים) from a neighboring station"* | `location` (target sector), `tanker_count` (how many trucks, default 1), `source_station` (which station they're coming from — this scenario names it explicitly), `note` | Confirmation string naming the count, source station, and target location, mirroring `dispatch_ambulance`'s `unified.friendly_forces.confirm_ambulance`-style catalog text | HIGH, `approval_flag=True`, `commander_only=True` (via `dispatch_mutual_aid`) |
| `dispatch_aircraft(location: str, aircraft_count: int = 1, aircraft_type: str = "firefighting", note: str = "")` | Phase 3, step 7 — *"+ 2 firefighting aircraft (טייסת כיבוי)"* (same step as the tankers, a second resource in the same commander decision) | `location`, `aircraft_count` (default 1), `aircraft_type` (free text, defaults to `"firefighting"` — leaves room for a future non-firefighting aircraft use without a signature change), `note` | Confirmation string naming the count, type, and target location | HIGH, `approval_flag=True`, `commander_only=True` (via `dispatch_mutual_aid`) |

Both tools log to the same `dispatches_recorded` list `FriendlyForcesAgent` already maintains (no new persistence
store) and are declared once, on `FirefightingExternalForcesAgent`, not duplicated per profile.

**Event types / areas**: see §5.2.

---

## 5. Protocols: per-profile traceability

Both profiles' protocol sets were built by reading every step's Hebrew text (`messages/he.py`, the exact source
text the bot receives) across all 3 phases and classifying each one. **Caveat, stated up front and repeated in
§11**: which protocol an inbound free-text message actually triggers is ultimately decided at runtime by the Main
Agent's own classification (an LLM call), not by static analysis — the classification below is the *author's
intended* mapping used to design a protocol set that can cover every step that plausibly needs one; it must be
verified empirically by actually running all six simulations end-to-end (§10), and protocol `description` text
should be written (implementation step, not this plan) to steer the model toward exactly this mapping. Steps
marked "no protocol" are routine chatter, corrections, or purely informational relays that a reasonable system
should acknowledge conversationally without invoking a tool — forcing every single message into a protocol would
produce noisy, over-triggered tool calls.

### 5.0 Every existing protocol is deleted first (decision 2)

No `Protocol` object from any of the five retired/merged profiles is carried over, referenced, or imported by
either new profile — every entry in §5.1/§5.2's tables below is authored fresh, independently justified only
against that profile's own 3 simulations' steps (the traceability tables ARE that justification). Where a new
entry happens to share a name with an old one (e.g. `update_camera_observation`, `overall_situational_picture`),
that is because the same real-world need recurs across profiles, not because the object was reused — it is a
distinct `Protocol(...)` declaration in the new file, written and justified independently. For the audit trail,
here is everything being deleted, by source file:

| Source file (deleted per §8) | Protocols deleted (all of them) |
|---|---|
| `profiles/unified_test.py` | `overall_situational_picture`, `query_surveillance_overview`, `query_drone_fleet_status`, `query_active_drone_missions`, `query_camera_status`, `dispatch_drone_to_incident`, `recall_drone_to_base`, `report_team_availability`, `record_attendance_response`, `dispatch_emergency_forces`, `query_historical_incidents` (11) |
| `profiles/friendly_forces.py` | `dispatch_ambulance`, `dispatch_police`, `dispatch_firefighters`, `dispatch_military` (4) |
| `profiles/demo.py` | `status_check`, `dispatch_response`, `minor_incident_review`, `routine_check` (4) |
| `profiles/sub_agent_surveillance.py` | `query_camera_status`, `query_drone_fleet_status`, `query_active_drone_missions`, `query_surveillance_overview`, `dispatch_drone_to_incident`, `return_drone_to_base`, `surveillance_area_scan`, `update_camera_observation` (8) |
| `profiles/sub_agent_team_status.py` | `report_team_availability` (1) |

**28 protocol declarations deleted in total, across 5 files.** §5.1 and §5.2 below rebuild **7** and **8**
respectively — every one newly authored and traced to a specific step of that profile's own 3 simulations, never
to a step from the other profile's simulations, and never copied from the table above.

### 5.1 Standby Squad — protocols and traceability (SEC_001, 3 phases, 28 steps)

**Rebuilt from nothing but SEC_001's 28 steps (§5.0) — no protocol below is a carry-over from `unified_test.py`,
`friendly_forces.py`, `demo.py`, or `sub_agent_surveillance.py`/`sub_agent_team_status.py`, even where a name
repeats.**

**Protocol set (7, all freshly authored per §5.0):**

| Protocol | Agents | Approved tools | Criticality | Approval |
|---|---|---|---|---|
| `record_attendance_response` | `team_status_agent` | `record_attendance_response` | LOW | No |
| `report_team_availability` | `team_status_agent` | `report_team_availability` | LOW | No |
| `update_camera_observation` | `surveillance_agent` | `update_camera_observation` | MEDIUM | Yes |
| `report_security_incident` **(new)** | `surveillance_agent` | `dispatch_drone_to_area` | HIGH | Yes |
| `dispatch_emergency_forces` | `friendly_forces_agent` | `dispatch_ambulance`, `dispatch_police`, `dispatch_firefighters`, `dispatch_military` | HIGH | Yes (commander-only) |
| `overall_situational_picture` | `surveillance_agent`, `team_status_agent` | `get_surveillance_overview`, `report_team_availability` | LOW | No |
| `query_historical_incidents` | `history_agent` | (none — read-only history lookup) | LOW | No |

`report_security_incident` is new because none of the reused protocols fit "an unconfirmed hostile/security event
that needs a system response, not just a status log" — its approved action is tasking a drone to the reported
location for recon confirmation (`dispatch_drone_to_area`), gated on commander approval given the criticality.

**Step-by-step trace:**

| Phase | Step | Text (summary) | Protocol | Notes |
|---|---|---|---|---|
| 1 | 1 | Member reports reserve duty, unavailable | `record_attendance_response` | |
| 1 | 2 | Camera 08 intermittent interference | `update_camera_observation` | |
| 1 | 3 | Small fire near access road, already extinguished, no risk | *no protocol* | Explicitly resolved/no-risk — logged conversationally |
| 1 | 4 | Member changed phone number | *no protocol* | Contact-info update, not an availability report |
| 1 | 5 | Commander asks: nightly roster gaps + camera status | `overall_situational_picture` | |
| 1 | 6 | Member has a fever, can't join evening patrol | `record_attendance_response` | |
| 1 | 7 | Camera 03 taken offline for scheduled update | `update_camera_observation` | |
| 1 | 8 | ATV stolen overnight, thieves possibly along the fence | `report_security_incident` | MEDIUM-flavored trigger of a HIGH protocol — acceptable per protocol semantics (protocol criticality is fixed; the model decides *whether* to select it) |
| 1 | 9 | Commander asks for updated nightly picture | `overall_situational_picture` | |
| 2 | 1 | Camera 03 still down, camera 04 now frozen too | `update_camera_observation` | |
| 2 | 2 | Broadcast: unmarked white van moving slowly near orchards | `report_security_incident` | |
| 2 | 3 | Member hears heavy equipment near west gate, asks if planned | *no protocol* | Routine query |
| 2 | 4 | Commander asks: anything suspicious east? word on west gate? | `overall_situational_picture` | |
| 2 | 5 | Camera 03's comms cable found physically cut — sabotage | `report_security_incident` | Escalation of step 2/1's fault report |
| 2 | 6 | Van found abandoned in orchard, K9 unit dispatched | `report_security_incident` | Continuation/update |
| 2 | 7 | Commander: "cross-reference everything, activate squad, recommend deployment" | `overall_situational_picture` (then likely `dispatch_emergency_forces` in a follow-up turn) | Two-step commander intent; the model gathers picture first |
| 2 | 8 | Responder: "en route, 4 min out, who else is with me?" | `report_team_availability` | view=members |
| 2 | 9 | Fresh breach in fence found, footprints leading inward | `report_security_incident` | |
| 3 | 1 | Sirens, suspicious figure with a long object in a yard | `report_security_incident` | |
| 3 | 2 | Report of continuous gunfire near west gate | `report_security_incident` | Later corrected (step 5) |
| 3 | 3 | Gunshot casualty reported, ambulance needed + escort | `dispatch_emergency_forces` | |
| 3 | 4 | Commander overwhelmed, needs prioritization/picture | `overall_situational_picture` | |
| 3 | 5 | Correction: gunfire was a warning shot, not an attack | *no protocol* | Informational correction |
| 3 | 6 | On-scene: casualty is a minor glass injury, being treated | *no protocol* | Status update |
| 3 | 7 | Mobile tactical camera: suspect spotted on roof | `update_camera_observation` | |
| 3 | 8 | Special police/YAMAG units entering, taking command | *no protocol* | Informational handoff, no system action |
| 3 | 9 | Suspect arrested, incident contained | *no protocol* | Resolution note |
| 3 | 10 | Commander asks for a full end-to-end incident summary | `query_historical_incidents` | Possibly paired with `overall_situational_picture` for current roster |

**Event types** (`EVENT_TYPES`): `team_availability`, `surveillance_report`, `security_incident` (new),
`emergency_dispatch`, `drone_dispatch`, `historical_query`.
**Areas** (`AREAS`): reuse `unified_test.py`'s existing set — `north_gate`, `south_sector`, `east_fence`,
`west_hill`, `central_hub`, `readiness_team` — already covers the fence/gate/compound sectors SEC_001's text
refers to (a street-address-level mention like "Olive St. 12" is expected to resolve to a coarse `AREA` via the
extraction step, not a literal string match — flagged as an assumption in §11).
**`EVENT_TYPE_REQUIRED_FIELDS`**: `security_incident: ("area",)`, `emergency_dispatch: ("area",)` (mirrors
`unified_test.py`'s existing pattern for its analogous types).

### 5.2 Firefighting — protocols and traceability (FIRE_002, 3 phases, 23 steps)

**Rebuilt from nothing but FIRE_002's 23 steps (§5.0) — no protocol below is a carry-over from any retired
profile, even where a name repeats (e.g. `update_camera_observation`, `overall_situational_picture`).**

**Protocol set (8):**

| Protocol | Agents | Approved tools | Criticality | Approval |
|---|---|---|---|---|
| `record_crew_availability_response` | `crew_status_agent` | `record_attendance_response` | LOW | No |
| `report_crew_status` | `crew_status_agent` | `report_team_availability` | LOW | No |
| `update_camera_observation` | `surveillance_agent` | `update_camera_observation` | MEDIUM | Yes |
| `dispatch_drone_to_incident` | `surveillance_agent` | `dispatch_drone_to_area` | MEDIUM | Yes |
| `report_fire_incident` **(new)** | `surveillance_agent` | `dispatch_drone_to_area` | HIGH | Yes |
| `dispatch_mutual_aid` **(new)** | `external_forces_agent` | `dispatch_water_tankers` (new, §4.2), `dispatch_aircraft` (new, §4.2), `dispatch_police` | HIGH | Yes (commander-only) |
| `overall_situational_picture` | `surveillance_agent`, `crew_status_agent` | `get_surveillance_overview`, `report_team_availability` | LOW | No |
| `query_historical_incidents` | `history_agent` | (none) | LOW | No |

`report_fire_incident` (fire spread/escalation reports) is kept separate from `dispatch_mutual_aid` (the actual
resource-dispatch decision) for the same reason Standby Squad separates "report" from "dispatch" — a report
should be logged/recon'd immediately (drone confirmation, MEDIUM/HIGH, one agent), while committing tanker
trucks/aircraft/mutual aid is a bigger, commander-only decision (HIGH, multi-resource).

**Step-by-step trace:**

| Phase | Step | Text (summary) | Protocol | Notes |
|---|---|---|---|---|
| 1 | 1 | Opening shift roster: 6 crew, 2 engines operational | `report_crew_status` | |
| 1 | 2 | Crew member leaving for medical check, back at 15:00 | `record_crew_availability_response` | |
| 1 | 3 | Thermal sensor: low heat alert from heatwave/wind | `update_camera_observation` | |
| 1 | 4 | Broadcast: open-fire ban during heatwave, rangers patrolling | *no protocol* | Policy broadcast, informational |
| 1 | 5 | Camera 02 paused for lens cleaning | `update_camera_observation` | |
| 1 | 6 | Small roadside stubble fire, handled, no risk to structures | *no protocol* | Explicitly resolved/no-risk |
| 1 | 7 | Commander asks for midday force/vehicle availability picture | `overall_situational_picture` | |
| 2 | 1 | Smoke detected on camera, small fire spreading east | `report_fire_incident` | |
| 2 | 2 | Dozens of citizen reports of smoke from the highway | *no protocol* | Aggregated informational reports |
| 2 | 3 | Engine dispatched, 4 min out; crew short (member at checkup) | `report_crew_status` | |
| 2 | 4 | Camera blinded by smoke/glare, lens frozen | `update_camera_observation` | |
| 2 | 5 | Fire jumped a trail into the tree line, risk to industrial park | `report_fire_incident` | |
| 2 | 6 | Rapid spread in forest; 2 bulldozers dispatched, need coordination | `dispatch_mutual_aid` | |
| 2 | 7 | Commander: urgent picture + recommendation on activating standby | `overall_situational_picture` | Possibly followed by `dispatch_mutual_aid` |
| 3 | 1 | Flames reached a chemical plant fence — gas/ammonia tanks on site | `report_fire_incident` | Critical escalation |
| 3 | 2 | Immediate evacuation of first row of houses, toxic smoke | `dispatch_mutual_aid` | Police cordon/evacuation coordination |
| 3 | 3 | Two children reportedly trapped on a roof | `report_fire_incident` | |
| 3 | 4 | Commander: two simultaneous critical hotspots, needs prioritization | `overall_situational_picture` | |
| 3 | 5 | Correction: house was empty, children already evacuated by parents | *no protocol* | False-alarm correction |
| 3 | 6 | Drone camera: fire nearing external gas tank, needs water curtain | `dispatch_drone_to_incident` (already active) / `report_fire_incident` | Recon confirms critical threat |
| 3 | 7 | 4 tanker trucks + 2 aircraft dispatched from neighboring station | `dispatch_mutual_aid` | |
| 3 | 8 | District aid arrived, water curtain up, flames contained, no leak | *no protocol* | Status update |
| 3 | 9 | Commander asks for an initial debrief (timeline, resources, false reports) | `query_historical_incidents` | |

**Event types** (`EVENT_TYPES`): `crew_availability`, `surveillance_report`, `fire_incident` (new),
`mutual_aid_dispatch` (new), `drone_dispatch`, `historical_query`.

**Areas (`AREAS`) — PENDING OWNER REVIEW (decision 4).** Unlike Standby Squad (which reuses `unified_test.py`'s
already-proven area set), Firefighting has no existing precedent — this list is derived solely from FIRE_002's
own text, one area per distinct place named across the 3 phases, quoted below for approval/edit:

| Proposed `AREAS` value | Justifying scenario text (Hebrew, with translation) |
|---|---|
| `pine_ridge` | Phase 2 step 1: *"זיהוי עשן ראשוני **במצלמה 05 (רכס אורנים)**!"* — "initial smoke spotted on camera 05 (**Pine Ridge**)" |
| `quarry_junction` | Phase 1 step 5: *"**מצלמה 02 (צומת המחצבה)** הופסקה יזומית"* — "camera 02 (**Quarry Junction**) paused" |
| `industrial_park` | Phase 2 step 5: *"סיכון ממשי להתפשטות לכיוון **פארק התעשייה**"* — "real risk of spreading toward the **industrial park**" |
| `ornim_street` | Phase 3 step 2: *"פינוי קו הבתים הראשון **ברחוב אורנים**"* — "evacuating the first row of houses **on Ornim St.**" (also step 3: *"רחוב אורנים 14"*) |
| `chemical_plant` | Phase 3 step 1: *"הגיעו לגדר של **מפעל 'כימי-קל'**. יש שם צובר גז ומיכלי אמוניה"* — "reached the fence of **'Chemi-Kal' plant** — gas accumulator and ammonia tanks on site" |
| `fire_station` | Phase 1 step 1: *"מעדכן סד\"כ פותח: 6 כבאים בצוות א', רכב אשד 3 וכרמל 1 **במבצעיות מלאה**"* — the crew's own home base/roster area, never named explicitly but implied by every roster-status step (phase 1 steps 1/2/7, phase 2 step 3) |

**This table is the open item to approve or edit — everything else in §5.2 is otherwise final.**

**`EVENT_TYPE_REQUIRED_FIELDS`**: `fire_incident: ("area",)`, `mutual_aid_dispatch: ("area",)`.

---

## 6. Simulations field design

**Headline decision: reuse everything, build nothing new.** §2.2 already established that the field
(`SIMULATIONS`/`SIMULATION_USERS`/`SIMULATION_GROUPS`/`SIMULATION_ROSTERS` on `LoadedProfile`), its schema
(`SimulationScenario.raw` = the canonical `{scenario, chats, steps}` admin-simulator JSON, persona/group *keys*
instead of concrete IDs), its loader validation (`profiles/loader.py:_validate_simulation_declarations`), its
server endpoints (`GET /Simulations`, `GET /Simulations/<key>`), and its admin frontend wiring (the
"סימולציות הפרופיל" dropdown) are **already fully built and working** for `unified_test.py` today. There is no
gap to fill — only a data migration.

- **Where the data lives**: inline in each profile module (`standby_squad.py`/`firefighting.py`), exactly as
  today — `SimulationScenario.raw` is a Python dict literal per scenario, text pulled from the message catalog
  (`_catalog_text(...)`) to satisfy `tests/test_hebrew_leakage.py`'s "no Hebrew literal outside the catalog" rule.
  **Recommendation**: keep this pattern (no move to external JSON files). The scenario JSON is already
  machine-generated-looking (long, repetitive, one dict per step) and lives fine as Python; splitting it into a
  separate `.json` file per scenario would add an indirection layer (a new "where does this profile load its
  scenario JSON from" question) for no benefit, since nothing else currently reads scenario JSON from disk at
  profile-load time.
- **Loader validation**: no change needed to `profiles/loader.py` — `_validate_simulation_declarations` already
  checks unique keys/offsets and that every `chats`/`steps` reference resolves to a declared persona/group. The
  only thing to re-verify after the split is that **offsets are unique within each profile** (they do not need
  to be globally unique across profiles — each profile is a separate process/DB/reserved-ID space) — recommend
  restarting both profiles' persona/group offsets from 0, not carrying over `unified_test.py`'s combined 0–26
  numbering.
- **`profile_file_hash`**: `hash_profile_file()` hashes the profile module's source file — moving/renaming the
  scenario declarations into new files naturally produces a new hash; no special handling needed, this is exactly
  what the field is for (detecting the profile changed).
- **Server endpoint**: no change — `GET /Simulations` / `GET /Simulations/<key>` already read
  `ctx.loaded_profile.simulations`/`.simulation_users`/`.simulation_groups`, so once `standby_squad.py` declares
  its 3 scenarios and `firefighting.py` declares its 3, each running profile's endpoint automatically serves only
  its own 3 — no profile-selection parameter needed on the endpoint itself (see §7.1 for *which process* answers
  the request). Behavior when a profile declares no simulations is already handled: `simulation_catalog_payload`
  returns `[]`, and the admin JS already disables the dropdown with a "no profile simulations" message
  (`setProfileSimAvailability(false, ...)`, `api/admin_simulator.py:997`).
- **Migration of the six simulations**: mechanical cut-and-paste plus renumbering —
  1. Copy `sec001_phase1/2/3`'s `SimulationScenario` entries, their referenced `SimulationPersona`/`SimulationGroup`
     entries, and the `team_status` `SimulationRoster` from `unified_test.py` into `standby_squad.py`, renumbering
     persona offsets to 0–14 and group offsets to 0–2 (was 2–16 / 0,1,2).
  2. Copy `fire002_phase1/2/3` and their personas/groups into `firefighting.py`, renumbering persona offsets to
     0–9 and group offsets to 0–2 (was 17–26 / 3,4,5); this profile needs its own `SimulationRoster` for
     `crew_status_agent`'s roster store (same shape as `unified_test.py`'s `team_status` roster, pointed at
     `firefighting.py`'s own DB).
  3. Drop the pilot `overall_picture_query` scenario entirely (not one of the six; see §11 open question — it
     could instead become an 8th, un-requested Standby Squad scenario if the reviewer wants to keep a minimal
     "quick demo" entry, but the task asks for exactly 3 per profile).
  4. Move the corresponding `unified.simulation.sec001.*`/`unified.simulation.fire002.*` catalog keys in
     `messages/he.py`/`messages/en.py` to new `standby_squad.simulation.*`/`firefighting.simulation.*` keys
     (mechanical rename, preserving every Hebrew/English string verbatim — no retyping, same "transcribed
     programmatically" discipline the original SEC_001/FIRE_002 migration used).
- **Related consumers**:
  - **Bot simulation mode (`simulator_port`)**: both new profiles must declare `SIMULATOR_PORT` (8915/8916, §3.1)
    so `run_stack.py` starts a `bot.simulator_app` process for each, otherwise the admin simulator's message-kind
    steps have no proxy target (`api/admin.py`'s proxy route 404s cleanly per
    `docs/bot_simulation_mode_design.md` — not a silent failure, but a real feature gap if omitted).
  - **Provisioning**: `ensure_simulation_entities()` needs no changes — it already reads whatever
    `simulation_users`/`simulation_groups`/`simulation_rosters` the loaded profile declares. Each profile's own
    `api.app` process provisions only its own personas/groups/rosters into its own DB at startup.

---

## 7. Server API and admin frontend changes

### 7.1 How the admin reaches "the other" profile — investigated, two real options

`run_stack.py`'s `StackSupervisor` runs **one profile per process pair** (`api.app` + `bot.app` [+
`bot.simulator_app`]), each with its own `API_PORT`/`SIMULATOR_PORT`. The admin simulator page is served by
`api.app` itself, so it only ever shows **the currently-loaded profile's** simulations — there is no
multi-profile picker inside the simulator page itself, and per §2.3 that page's dropdown is correctly scoped
(it should only ever show the running profile's own scenarios, not the other profile's).

**Option A — switch in place (recommended default).** Already fully built:
`api/admin.py`'s existing profile-switch page (§2.3) → `submit_server_command("switch_profile", ...)` →
`run_stack.py`'s `StackSupervisor.switch()` stops the current profile's processes and restarts with the new
one, on the **same** `API_PORT`. Zero new code. Trade-off: brief service interruption during the switch
(`StackSupervisor.stop()`/`start()`, a few seconds), and only one profile's admin panel/bot is reachable at a
time.

**Option B — run both concurrently.** Since the two profiles use distinct `API_PORT`/`SIMULATOR_PORT` (8905/8915
vs. 8906/8916) and distinct `DB_PATH`s, nothing structurally prevents running two `run_stack.py` supervisor
processes side by side, one per profile, each with its own admin panel URL (`:8905/admin/simulator` vs.
`:8906/admin/simulator`) — the operator just opens two browser tabs. **Caveat found during investigation**:
`config/server_control.py:control_dir()` defaults to one shared `data/server_control/` directory
(overridable via `AGENTSHUB_CONTROL_DIR`) — running two supervisors with the default config would have them
overwrite each other's `status.json`/`command.json`. Option B therefore requires launching the second supervisor
with a distinct `AGENTSHUB_CONTROL_DIR` (e.g. `data/server_control_firefighting/`), which also means each
instance needs its own `.env`/bot token if run with real Telegram tokens (already handled by giving Firefighting
its own `BOT_TOKEN_ENV`, §3.1).

**Recommendation**: document and support Option A as the primary, zero-new-code workflow (this is what the
existing profile-switch admin page is *for*); mention Option B in `docs/operator_guide.md` as an advanced,
opt-in path for anyone who wants both profiles live at once, with the `AGENTSHUB_CONTROL_DIR` caveat spelled out.
No code changes are required for either option — this is a documentation/runbook decision, not an implementation
task.

### 7.2 Everything else

No route, schema, or auth changes: `GET /Simulations`/`GET /Simulations/<key>` keep their existing
`RequestedOperation.VIEW_SIMULATIONS` (commander-only) gate; the admin simulator page's dropdown, load/next-step/
clear buttons, and paste/upload JSON flows are all unchanged — they already work purely off whichever profile's
`ApiContext.loaded_profile` the currently-running `api.app` process holds.

---

## 8. Removal and dependency check

Full-repo search performed for `profiles.demo`/`profiles/demo`, `profiles.unified_test`/`profiles/unified_test`,
`profiles.friendly_forces`/`profiles/friendly_forces`, plus (found along the way, not in the original assumption)
`profiles.sub_agent_surveillance`/`profiles.sub_agent_team_status`. Every hit is listed below with its migration.
**No database or data file deletion is proposed anywhere in this plan** — `data/unified_test/*.db` is renamed/
reused as `data/standby_squad/`'s starting point (see note below); `data/friendly_forces_profile.db`,
`data/demo_profile.db`, and the two `sub_agent_*` temp-dir DBs are simply no longer referenced once their profiles
are deleted — I recommend leaving those specific files untouched on disk (they cost nothing sitting idle) rather
than deleting them, since the task instructs never to propose data/DB deletion without explicitly flagging it —
**flagged here**: if the reviewer wants them actually deleted, that is a separate, explicit decision.

### 8.1 Source code (must change for the split to work)

| File | Reference | Migration |
|---|---|---|
| `config/server_control.py:133` | `load_selected_profile(default: str = "profiles.unified_test")` | Change default to `"profiles.standby_squad"`. |
| `tools/terminal_client_viewer.py:138` | `--profile` default `"profiles.demo"` | Change default to `"profiles.standby_squad"` (or drop the default, requiring an explicit `--profile`). |
| `tools/terminal_client_commander.py:320` | Same as above | Same fix. |
| `api/app.py:275` | argparse help text `"e.g. profiles.demo"` | Cosmetic — update example to `profiles.standby_squad`. |
| `bot/app.py:1150` | Same argparse help text | Same fix. |
| `bot/simulator_app.py:320` | Help text `"e.g. profiles.unified_test"` | Same fix. |
| `cli/group_admin.py:29` | Help text mentioning `'profiles.unified_test'` | Same fix. |
| `agents/friendly_forces_agent.py:1` | Module docstring: `"(profiles/friendly_forces.py)"` | The class itself is **not** renamed (decision 1) — only update the stale cross-reference to point at every profile that now uses it: `profiles/standby_squad.py` and `profiles/firefighting.py` (its `FirefightingExternalForcesAgent` base). |
| `agents/surveillance_agent.py:22,127` | Comments referencing `profiles.unified_test.UnifiedSurveillanceAgent` as the worked example of the "exact result capture" pattern | Update the comment to reference `profiles.standby_squad.StandbySquadSurveillanceAgent`. |
| `messages/en.py:656`, `messages/he.py:653` | Section-header comments `"--- profiles/unified_test.py ---"` above the `unified.*` catalog keys | Update alongside the catalog-key migration in §6. |

### 8.2 Tests (must change — this is the largest real body of work)

**Sequencing principle (decision 5)**: every test file below is rewritten in the same implementation step (§9)
as the source-code change that would otherwise break it — never in a separate, later "test cleanup" pass. The
"Step" column names exactly where each rewrite happens; §9 repeats this so the two sections stay in sync.

| File | What it depends on | Required action | Step (§9) |
|---|---|---|---|
| `tests/test_unified_role_and_security.py` | Entire file — `load_profile`/`build_deps` against `profiles.unified_test`, exercising role/permission behavior across the combined 3-agent profile (7 call sites) | **Delete, replace with `tests/test_standby_squad_role_and_security.py`**, repointed at `profiles.standby_squad`, module path and every protocol-name assertion updated to §5.1's new 7-protocol set. Since Standby Squad is architecturally `unified_test.py`'s direct successor, this coverage migrates wholesale, not from scratch. | **Step 1** (same step `standby_squad.py` is created) |
| `tests/test_profile_simulations.py:476-609` | A dedicated "against the real profile" section loading `profiles.unified_test` and asserting SEC_001-specific persona/roster details (offsets, `pre_approved_rosters` membership) | **Rewrite** that section to target `profiles.standby_squad`, updated offsets/keys per §6's renumbering. The *generic*, fixture-based mechanism tests earlier in the same file are untouched. | **Step 1** |
| — (new file) | Coverage for the two new tools | **Add `tests/test_friendly_forces_agent.py` cases** (or a new `tests/test_firefighting_profile.py`) for `dispatch_water_tankers`/`dispatch_aircraft` — parameter validation, confirmation-string shape, `dispatches_recorded` logging, per §4.2's contracts. | **Step 2** (same step `firefighting.py` and its two new tools are created) |
| `tests/test_demo_profile.py` | Entire file: `load_profile("profiles.demo", ...)` | **Delete the file.** Listed by exact name in `.github/workflows/ci.yml:116` (Mission 4). | **Step 3** (same step `demo.py` is deleted) |
| `tests/test_sub_agent_surveillance_profile.py` | Loads `profiles.sub_agent_surveillance` directly | **Delete the file** — its coverage (camera protocols, DB isolation) now lives in `tests/test_standby_squad_role_and_security.py` (Step 1), created *before* this deletion per decision 3's sequencing. | **Step 3** (same step `sub_agent_surveillance.py` is deleted) |
| `tests/test_sub_agent_team_status_profile.py` | Loads `profiles.sub_agent_team_status` directly | Same treatment — deleted alongside its source profile, coverage already absorbed in Step 1. | **Step 3** |
| `tests/test_server_control.py` | Uses `profiles.demo` **and** `profiles.friendly_forces` as two real, distinct, loadable profiles to exercise `StackSupervisor.switch()` end-to-end (lines 14, 17, 24, 25, 29, 36, 43, 45, 46, 47) | **Rewrite**, not just rename: swap in `profiles.standby_squad`/`profiles.firefighting` — both must already exist (Steps 1–2) before this rewrite, and `demo.py`/`friendly_forces.py` must be gone (this step) for the old assertions to be meaningfully replaced, not left dangling. Update the expected `starts`/`last_error` assertions accordingly. | **Step 3** (same step `demo.py`/`friendly_forces.py` are deleted) |
| `tests/test_profile_loading.py:578-585` | One test loads the **real** `profiles.demo` module (not a `SimpleNamespace` double) to prove `load_profile()` behaves correctly against a genuine file | Repoint at `profiles.standby_squad`. | **Step 3** |
| `tests/test_api_admin.py:317` | `assert b"profiles.demo" in page.data` (asserts the profile-switch page lists `profiles.demo`) | Update to assert `b"profiles.standby_squad"` and `b"profiles.firefighting"` are listed instead. | **Step 3** |
| `tests/sanity_check_real_model_call.py:214` | `from profiles.demo import PROTOCOLS` | Manual/ad-hoc script (not in `ci.yml`'s pytest lists) — update the import to a real profile's `PROTOCOLS`. | **Step 4** (general source-reference cleanup) |
| `tests/test_team_status_persistence.py:136` | Docstring comment citing `unified_test.py`'s `as_of_iso` handling as an example | Cosmetic — update the comment to cite `standby_squad.py`. | **Step 4** |

**CI file** (`.github/workflows/ci.yml`): update the hardcoded test lists at lines 116, 119, 120 (remove
`test_demo_profile.py`, `test_sub_agent_surveillance_profile.py`, `test_sub_agent_team_status_profile.py`, add
`test_standby_squad_role_and_security.py` and any new Firefighting test file) — this is a flat file list, not a
glob, so a forgotten update here means CI fails on a "file not found" error, not a silent skip. Done in **Step 3**
(same step the referenced files are deleted/added), not deferred to a separate CI-only pass.

### 8.3 Documentation (should change, not blocking)

`docs/file_catalog.md` (lines 142/143/147/148/150) lists all five profile files being removed — update its
"Production" file table. `docs/work_plan.md`, `docs/profile_spec.md`, `docs/api_spec.md`,
`docs/how_to_connect_telegram.md`, `docs/operator_guide.md`, `docs/Next_Plan.md`, `docs/server_report.md`,
`README.md`, and several files under `docs/IMPROVES/` mention `profiles.demo` as the canonical "how to run a
profile" example — update the example to `profiles.standby_squad`. None of these block implementation; they are
a documentation-accuracy pass, listed here as the task requires "every reference and its migration," not treated
as required implementation work.

### 8.4 Environment / config

`.env.example` already has no `SURVEILLANCE_BOT_TOKEN`/`SURVEILLANCE_CHAT_ID_ENV` entries (only
`TEAM_STATUS_BOT_TOKEN`/`TEAM_STATUS_CHAT_ID` exist today, for `sub_agent_team_status.py`'s standalone use) — add
`FIREFIGHTING_BOT_TOKEN` (new, per §3.1), and remove the now-unused `TEAM_STATUS_BOT_TOKEN`/`TEAM_STATUS_CHAT_ID`
example entries once `sub_agent_team_status.py` is deleted (they were that profile's own dedicated token, not
shared with `unified_test.py`, which already uses plain `BOT_TOKEN`). No CI workflow env vars or `.vscode` launch
configs reference any of the three retired profiles (checked — `.vscode/` only has `extensions.json`, no
`launch.json`).

### 8.5 Ports

Current: `demo`=8902, `friendly_forces`=8903, `sub_agent_team_status`=8903 *(collides with `friendly_forces`,
harmless only because they never run together)*, `sub_agent_surveillance`=8904, `unified_test`=8905/8915,
`template`=9999 (non-runnable). After the split: `standby_squad`=8905/8915 (reused), `firefighting`=8906/8916
(new, no collision with anything). Ports 8902/8903/8904 become free.

---

## 9. Ordered implementation steps

Each step is independently verifiable (a passing full `pytest` run, not just the new/changed files) before moving
to the next — per decision 5, no step is allowed to leave the suite red for a later step to fix. Steps 1–3 are
strictly ordered for a second reason beyond testing: decision 3 requires the two sub-agent classes to exist in
`standby_squad.py` (Step 1) *before* their original standalone-profile files are deleted (Step 3).

1. **Create `profiles/standby_squad.py`**, carrying forward `SubAgentSurveillanceAgent`/`SubAgentTeamStatusAgent`
   (from the still-present `sub_agent_surveillance.py`/`sub_agent_team_status.py`) as `StandbySquadSurveillanceAgent`/
   `StandbySquadTeamStatusAgent`, and `unified_test.py`'s `UnifiedFriendlyForcesAgent` (built on the unchanged,
   shared `agents.FriendlyForcesAgent`, decision 1) as `StandbySquadForcesAgent`. Delete the `overall_picture_query`
   pilot scenario and every `fire002_*` declaration (scenarios, personas, groups, `FIRE002_CHATS`); renumber the
   remaining SEC_001 personas/groups to start at offset 0. **Delete every existing `PROTOCOLS` entry inherited from
   `unified_test.py`** and replace with §5.1's 7-protocol set, built only from SEC_001's steps (§5.0/§5.1). Update
   `EVENT_TYPES`/`AREAS`/`EVENT_TYPE_REQUIRED_FIELDS` per §5.1; rename `PROFILE_NAME`/catalog keys per §3.1/§6.
   **Same step**: delete `tests/test_unified_role_and_security.py`, add `tests/test_standby_squad_role_and_security.py`
   (§8.2); rewrite `tests/test_profile_simulations.py`'s real-profile section to target `profiles.standby_squad`
   (§8.2) — `profiles.unified_test` still exists untouched on disk at this point, so this is a pure addition, not
   yet a removal.
   *Verify*: `load_profile("profiles.standby_squad", ...)` succeeds with no `ProfileValidationError`; full `pytest`
   green (old profiles still present and unmodified, nothing else can have broken).
2. **Create `profiles/firefighting.py`** from `profiles/template.py`: add the 3 agents from §4.2, including
   `FirefightingExternalForcesAgent`'s two new tools (`dispatch_water_tankers`/`dispatch_aircraft`, full contracts
   in §4.2); add §5.2's 8-protocol set, built only from FIRE_002's steps (§5.0/§5.2); add `EVENT_TYPES`/`AREAS`
   (§5.2's areas table — implement using the PENDING OWNER REVIEW list as-is, revisit if it changes before this
   step runs); copy the `fire002_*` scenarios/personas/groups/roster from `unified_test.py` (still present, not yet
   deleted), renumbered from 0. **Same step**: add the new-tool test coverage (§8.2).
   *Verify*: `load_profile("profiles.firefighting", ...)` succeeds with no `ProfileValidationError`; full `pytest`
   green.
3. **Delete** `profiles/demo.py`, `profiles/friendly_forces.py`, `profiles/sub_agent_surveillance.py`,
   `profiles/sub_agent_team_status.py`, `profiles/unified_test.py` — only now, since Steps 1–2 already carried
   forward everything either new profile needs from them (decision 3's sequencing satisfied: the sub-agent classes
   have lived in `standby_squad.py` since Step 1). **Same step**: delete `tests/test_demo_profile.py`,
   `tests/test_sub_agent_surveillance_profile.py`, `tests/test_sub_agent_team_status_profile.py`; rewrite
   `tests/test_server_control.py` to use `profiles.standby_squad`/`profiles.firefighting` as its two concrete
   profiles; fix `tests/test_profile_loading.py` and `tests/test_api_admin.py`'s `profiles.demo` references; update
   `.github/workflows/ci.yml`'s hardcoded test lists (§8.2).
   *Verify*: `discover_profiles()` returns exactly two entries; full `pytest` green; CI green on a throwaway
   branch/PR.
4. **Fix every remaining cosmetic source reference from §8.1** (defaults in `config/server_control.py`,
   `tools/terminal_client_*.py`; argparse help text in `api/app.py`/`bot/app.py`/`bot/simulator_app.py`/
   `cli/group_admin.py`; doc-comments in `agents/friendly_forces_agent.py`/`agents/surveillance_agent.py`;
   catalog section-header comments in `messages/en.py`/`messages/he.py`); update
   `tests/sanity_check_real_model_call.py` and `tests/test_team_status_persistence.py`'s comment (§8.2).
   *Verify*: repo-wide re-grep for the five retired module paths returns only §8.3's doc files.
5. **Update `.env.example`** (§8.4: add `FIREFIGHTING_BOT_TOKEN`, remove now-unused `TEAM_STATUS_BOT_TOKEN`/
   `TEAM_STATUS_CHAT_ID`) and `docs/operator_guide.md` with the two profiles' run instructions and the Option A/B
   guidance from §7.1.
6. **Manual end-to-end pass** (§10): run each profile via `run_stack.py`, open `/admin/simulator`, confirm the
   dropdown isolation (decision 7 — each profile shows only its own 3 simulations), run all 3 simulations per
   profile to completion, verify protocol selection against §5.1/§5.2's tables.
7. **Documentation sweep** (§8.3): update `file_catalog.md` and the remaining doc mentions.

---

## 10. Test and verification plan

- **Unit/contract level**: `pytest` full suite green after every step in §9 (not just the end — decision 5),
  including every migrated/rewritten file from §8.2.
- **Profile load**: for each of the two new modules, `load_profile(module_path, core_model, sub_model)` succeeds
  with no `ProfileValidationError`, and `validate_profile`'s protocol/agent/tool cross-checks pass (every
  `participating_agents` name resolves to a constructed agent, every `approved_tools` entry is actually exposed by
  one of its participants — `profiles/loader.py:_validate_protocol`).
- **Simulation declarations**: `_validate_simulation_declarations` passes for both profiles (unique keys/offsets,
  every `chats`/`steps` reference resolves) — this is exercised automatically by step above, but add an explicit
  assertion in the migrated `tests/test_profile_simulations.py` that both profiles' `GET /Simulations` payload has
  exactly 3 entries with the exact 6 titles from the task.
- **Admin dropdown isolation, both profiles running (decision 7 — new, explicit verification step)**:
  1. Start both profiles concurrently (§7.1 Option B — two `run_stack.py` supervisors, distinct
     `AGENTSHUB_CONTROL_DIR`, ports 8905/8915 and 8906/8916).
  2. Open Standby Squad's `/admin/simulator` (`:8905`) — confirm the "סימולציות הפרופיל" dropdown lists **exactly**
     the 3 SEC_001 titles and **none** of the 3 FIRE_002 titles.
  3. Open Firefighting's `/admin/simulator` (`:8906`) in a second tab — confirm the reverse: exactly the 3 FIRE_002
     titles, none of SEC_001's.
  4. This is a `GET /Simulations` response check per process (§2.2/§6 — each process's payload is computed purely
     from its own `ctx.loaded_profile.simulations`, so this also doubles as a regression check against any future
     change accidentally sharing state between the two profiles' in-memory objects).
- **End-to-end, all 6 simulations** (the task's explicit ask): for each profile,
  1. With that profile running (either Option A or B, §7.1), open `/admin/simulator`.
  2. Load each of its 3 scenarios in turn, click "send next step" through to completion for all 9/9/10 (Standby
     Squad) or 7/7/9 (Firefighting) steps, with no `4xx`/`5xx` from `/admin/simulator/bot-msg` or `/Event`.
  3. For each step this plan marked with a specific protocol (§5.1/§5.2 tables), confirm (via the admin
     event/protocol view, or `GET /Events`) that the Main Agent actually selected that protocol — this is the
     empirical check that closes the "LLM classification isn't guaranteed by static analysis" caveat from §5's
     header. Any mismatch is either a protocol `description` wording fix or a genuine re-classification of that
     step in this plan — not a blocking failure by itself, but must be reconciled before calling the split done.
  4. Confirm every `approval_flag=True` protocol actually surfaces a pending approval in the commander flow,
     including the two new `dispatch_mutual_aid` triggers (§4.2's tools), and that approving/declining it behaves
     as expected.
- **Isolation check**: confirm Standby Squad's DB writes never appear in Firefighting's DB and vice versa (separate
  `DB_PATH`/`data/` directories per §3.1 — a straightforward file-existence + row-count check).
- **Port/process check**: confirm `run_stack.py` starts exactly 3 subprocesses per profile (`api.app`, `bot.app`,
  `bot.simulator_app`) on the expected ports, and that the profile-switch admin page (Option A, §7.1) correctly
  stops/restarts when switching between the two.

---

## 11. Risks, assumptions and open questions

**Resolved, no longer open** (see "Decisions log" at the top): the `friendly_forces_agent` rename question
(decision 1 — not renamed), the rename-and-trim approach (decision 2 — approved, with the all-protocols-deleted
condition), the sub-agent-class destination/sequencing (decision 3), test-rewrite sequencing (decision 5), the
two new Firefighting tools (decision 6), and admin-dropdown isolation verification (decision 7).

**Still open (need your input before/while implementing):**

1. **§5.2 areas (decision 4)**: the `AREAS` table in §5.2 is marked **PENDING OWNER REVIEW** with each value's
   justifying scenario text quoted — approve as-is or edit before Step 2 (§9) implements it.
2. **§6**: the pilot `overall_picture_query` scenario is still proposed to be dropped entirely, to keep exactly
   3-per-profile as specified — not resolved by the decisions above. Confirm that's correct rather than keeping it
   as an unrequested 4th Standby Squad scenario.
3. **§7.1**: Option A vs. Option B as the *primary, documented* operating model is still unresolved — decision 7
   confirms Option B (concurrent) must work correctly (it's now a required verification step, §10), but that alone
   doesn't settle which mode `docs/operator_guide.md` should present as the default day-to-day workflow. Confirm.

**Risks:**

- **Protocol-selection drift** (§5, §10): the step→protocol traceability tables are an informed design, not a
  guarantee of runtime behavior — the Main Agent's classification is model-driven. Budget real iteration time on
  protocol `description` wording during §10's end-to-end pass, not just a one-shot implementation.
- **`test_server_control.py` rewrite risk** (§8.2): this file's assertions are fairly specific about start/switch/
  rollback ordering; because it needs two *real* profiles, it is coupled to whichever two profiles exist — it will
  need another edit if a third profile is ever added later. Low risk today, worth noting for future maintainers.
- **CI list drift** (§8.2/§9 Step 3): the hardcoded test file lists in `ci.yml` are a recurring maintenance
  footgun independent of this task — forgetting to update them silently breaks CI with a file-not-found rather
  than a clear "profile removed" message. Not this task's problem to fix generally, but directly relevant to
  getting Step 3 right.
- **Firefighting's two new tools** (`dispatch_water_tankers`/`dispatch_aircraft`, §4.2): this is real new agent
  code, not pure reuse — it needs its own unit tests (mirroring `tests/test_friendly_forces_agent.py`'s existing
  coverage of the other four dispatch tools) in addition to the profile-level tests in §8.2.

**Assumptions made without asking (recorded per the task's own instruction):**

- Both new profiles set `requires_confirmation`/`commander_only` explicitly on every protocol (§2.4), following
  `unified_test.py`'s fuller style rather than `friendly_forces.py`/`demo.py`'s (which leave them at their
  `False` defaults).
- `data/demo_profile.db`, `data/friendly_forces_profile.db`, and the two `sub_agent_*` temp-dir DBs are left on
  disk, unreferenced, rather than deleted (§8, explicitly flagged per the task's instruction never to propose
  data deletion silently).
- Firefighting's new test coverage (§8.2, Step 2) is added as new cases rather than a prescribed file name — I
  suggested `tests/test_friendly_forces_agent.py` (extended) or a new `tests/test_firefighting_profile.py`; either
  satisfies "mirrors `test_friendly_forces_agent.py`'s existing coverage" (§11 risk below) and can be decided at
  implementation time without changing this plan.

---

## 12. Manual verification checklist

Implementation is complete and the automated suite is green (1470 `pytest` tests; see §13 for the
full verification record). The following still needs a human running the real stack — deliberately
**not** done during implementation, since it needs a real model call, a real or simulated Telegram
surface, and a browser session, none of which the implementation step was allowed to use.

### 1. Admin dropdown isolation (decision 7)

1. Start Standby Squad: `python -m run_stack` with `profiles.standby_squad` selected (the default),
   or `python -m api.app profiles.standby_squad` directly for a quicker check.
2. Open `http://127.0.0.1:8905/admin/simulator`. Confirm the "סימולציות הפרופיל" dropdown lists
   **exactly** these 3 titles and no others:
   - אירוע כיתת כוננות - שלב מכין: שגרה ותקלות קלות
   - אירוע כיתת כוננות - שלב עיקרי: התחממות והצטברות אירועים
   - אירוע כיתת כוננות - שלב קיצון: חדירה פעילה, בלבול וסגר מלא
3. Start Firefighting in a second process — `python -m api.app profiles.firefighting` (or a second
   `run_stack.py` with `AGENTSHUB_CONTROL_DIR` set differently, per `docs/operator_guide.md`).
4. Open `http://127.0.0.1:8906/admin/simulator` (a second browser tab, both running at once).
   Confirm its dropdown lists **exactly** the 3 Firefighting titles and none of Standby Squad's:
   - אירוע כיבוי והצלה - שלב מכין: שגרה, עומס חום ותחזוקת ציוד
   - אירוע כיבוי והצלה - שלב עיקרי: התפשטות שריפה ועומס מוקד
   - אירוע כיבוי והצלה - שלב קיצון: איום חומ"ס, פינוי תושבים ועומס קריטי
5. Confirm neither dropdown ever shows the other profile's entries, and neither shows the retired
   pilot scenario ("overall picture" / תמונת מצב כללית).

### 2. Run all six simulations end to end

For each of the six scenarios above (three per profile, loaded from its own admin panel):

1. Click "load", then "send next step" repeatedly until the queue is empty — Standby Squad's three
   phases have 9, 9, and 10 steps; Firefighting's have 7, 7, and 9. Confirm no step produces an
   HTTP error from `/admin/simulator/bot-msg` or `/Event`, and every persona's message appears in
   its chat bubble.
2. Confirm the "sending…" placeholder bubble always resolves to either an inline reply or (for an
   async job) a follow-up bubble delivered by `pollSimulatorChat()` within a reasonable time.

### 3. Confirm the intended protocol is selected at each step

This is the empirical check the traceability tables in §5.1/§5.2 call for (protocol selection is
model-driven, not guaranteed by static analysis — see §5's header caveat). For each step marked
with a specific protocol in those tables:

1. After sending the step, check the admin events page (or `GET /Events` with a commander identity)
   for that event's `selected_protocol`/`protocol_reason`.
2. Confirm it matches the table's entry. A mismatch is not necessarily a bug — it may mean a
   protocol `description` needs sharper wording (the model is choosing a plausible-but-different
   protocol), or that this plan's classification for that specific step should be revised. Either
   way, reconcile before considering a phase "done" — do not treat a mismatch as blocking on its own
   the first time you see it, but do not ignore a *pattern* of the same step consistently picking
   the wrong protocol.
3. For the steps marked "*no protocol*" in the tables, confirm the Main Agent responds
   conversationally (or logs the report) without invoking a tool — not a hard requirement, but worth
   spot-checking on a few of them (e.g. SEC_001 phase 1 step 3's "small fire, already out, no risk"
   should not trigger a HIGH-criticality `report_security_incident`).

### 4. Approval flow for every `approval_flag=True` protocol

Standby Squad: `update_camera_observation`, `report_security_incident`, `dispatch_emergency_forces`.
Firefighting: `update_camera_observation`, `dispatch_drone_to_incident`, `report_fire_incident`,
`dispatch_mutual_aid`.

For at least one triggering step per protocol:

1. Confirm a pending approval hold appears in the commander's queue (`/admin` approvals page, or the
   commander Telegram/terminal-client flow) rather than the action executing immediately.
2. Approve it — confirm the underlying tool call actually runs (e.g. a drone dispatch confirmation
   appears, or a dispatch is recorded) and the hold clears.
3. For a second instance of the same protocol, decline it — confirm the tool call does **not** run
   and the hold clears as declined.
4. Specifically for `dispatch_emergency_forces` and `dispatch_mutual_aid` (`commander_only=True`,
   `requires_confirmation=True`): confirm even a **commander-originated** request for these still
   holds for explicit confirmation, not just a viewer-originated one (`tests/test_standby_squad_role_and_security.py::test_commander_side_effects_trigger_confirmation_flow`
   covers this at the unit level already — this step confirms it end-to-end through the real bot).

---

## 13. Implementation deviations

Recorded per the implementation prompt's instruction: "where the plan and the actual code disagree
(names, line numbers, tool names), the code wins... record each such deviation." All seven decisions
from the top-of-file "Decisions log" were followed as given; nothing below contradicts them. This
section covers places the *implementation* diverged from what §§1–11 specifically described, plus
one genuine gap the plan never anticipated.

1. **`bot/interactions.py:format_job_result` had a hardcoded protocol-name allowlist the plan never
   mentioned.** A `surveillance_protocols` set (controlling compact vs. verbose job-result
   formatting) was hardcoded to `unified_test.py`'s old protocol names (`query_camera_status`,
   `dispatch_drone_to_incident`, `recall_drone_to_base`, …). Discovered when
   `test_compact_formatting_for_unified_protocols` failed after Step 1's protocol rebuild — none of
   the new protocol names matched the old allowlist, so results silently fell back to verbose
   formatting. Fixed by replacing the set with the union of both new profiles' actual protocol names
   (12 entries), with a comment pointing at this plan. This is exactly the class of pre-existing
   coupling the plan's own "verify every agent/tool name against real code" instruction was meant to
   catch, just one level removed from the profile files themselves.
2. **Protocol `description`/`expected_success_output` text is plain English, not catalog-driven.**
   `unified_test.py`'s protocols routed every string through `_catalog_text(...)` (Hebrew, since
   `DEFAULT_LANGUAGE="he"`); `demo.py`/`friendly_forces.py`/`sub_agent_surveillance.py`/
   `sub_agent_team_status.py` all used plain English literals regardless of their own
   `DEFAULT_LANGUAGE`. The plan (§5) didn't specify which style to follow for the rebuilt protocols;
   implementation followed the majority precedent (plain English) for both new profiles' 15
   protocols — these strings are Main-Agent-facing classification instructions, not end-user text,
   so `tests/test_hebrew_leakage.py`'s catalog rule doesn't apply either way.
3. **Firefighting's two rebind-only agents carry no localized tool-text overrides.**
   `FirefightingSurveillanceAgent`/`FirefightingCrewStatusAgent` are thin `surveillance_db_path`/
   `status_db_path` rebinds with zero overridden methods — matching `sub_agent_surveillance.py`/
   `sub_agent_team_status.py`'s original (simpler) pattern, not `unified_test.py`'s fuller
   Hebrew-localized-override pattern §3.1/§4.1 used for Standby Squad's agents. The plan's §4.2 named
   the three agents and justified them by domain but didn't mandate a localization depth; this
   keeps Firefighting's new code minimal, consistent with §4.2's own "kept minimal" framing for the
   one place it *does* add real capability (the two new tools).
4. **All 253 `unified.*` catalog keys were removed from `messages/en.py`/`messages/he.py`, not just
   the simulation-specific ones §6 named.** After confirming (by repo-wide grep) that nothing
   referenced them once `profiles/unified_test.py` was deleted in Step 3, every `unified.*` entry —
   role/system_prompt/tool-description/output text, not only `unified.simulation.*` — was removed
   from both files (full en/he parity re-verified after). Goes further than §6's literal migration
   scope, in the direction of "no `unified_test` anywhere," with no functional risk (verified via the
   full suite, not just a spot check).
5. **Documentation sweep (Step 7) scoped to living reference docs, not historical/dated records.**
   Updated: `README.md`, `docs/operator_guide.md`, `docs/unified_command_guide.md` (module-path
   references only — see the gap noted below), `docs/file_catalog.md`. Deliberately **not**
   rewritten: `docs/work_process.md`, `docs/progress.md`, `docs/BOT_EVALUATION_REPORT_2026-09-08.md`,
   `docs/TELEGRAM_E2E_REPORT_2026-09-08.md`, `invest.md`, `SPEED.MD`,
   `docs/profile_simulations_design.md`, `docs/bot_simulation_mode_design.md`, `docs/IMPROVES/*` —
   these are point-in-time investigation/design logs describing already-completed past work;
   rewriting them to erase every `unified_test`/`demo`/`friendly_forces` mention would be revisionist
   editing of a historical record, not a documentation fix. `docs/api_spec.md`'s one match
   (`fixtures.profiles.demo_profile`) is an unrelated illustrative example value, not a reference to
   the deleted `profiles/demo.py` — confirmed via search, left untouched.
6. **Known remaining gap: `docs/unified_command_guide.md`'s content still describes the old,
   11-protocol `unified_test.py` protocol/feature set.** Only its `profiles.unified_test` →
   `profiles.standby_squad` module-path references were mechanically updated (5 occurrences) — a full
   rewrite of its protocol-by-protocol Hebrew walkthrough to match §5.1's new 7-protocol set was out
   of scope for this pass and is flagged here rather than left silently stale.
7. **Pre-existing CI gap, not caused by this work.** 12 test files that predate this task
   (`tests/test_server_control.py`, `tests/test_profile_simulations.py`, `tests/test_api_groups.py`,
   and others) were already absent from `.github/workflows/ci.yml`'s hardcoded lists before
   implementation started. Only entries for files this task actually added/removed/renamed were
   fixed (§9 Steps 1–3); the broader pre-existing gap was left as-is rather than expanding this
   task's scope to a general CI-completeness fix.
8. **`.env` (the real, git-ignored secrets file) was not modified** — only `.env.example` gained
   `FIREFIGHTING_BOT_TOKEN`. The real `.env` needs that variable added with a real token value before
   `profiles.firefighting` can start for real; this is a manual action item, not a code change.
9. **Both profiles' `SimulationRoster` key is literally `"team_status"`.** Not a collision (each
   profile's `SIMULATION_ROSTERS` list is independent, resolved only within that profile's own load),
   but worth noting: Firefighting's roster key was not renamed to something crew-status-specific.
   Purely cosmetic — no functional effect, confirmed by both profiles' passing test suites.
