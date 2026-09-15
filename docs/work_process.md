# Work Process Log — Per-Profile Simulation Mechanism

A running, chronological record of everything done on this feature: the
architecture research, the implementation, the bugs found and fixed, the
diagnoses performed, and the legacy-fixture migration. Append a new dated
entry here after each significant piece of work — this file is never
rewritten from scratch, only added to, so it stays an accurate history of
how the feature actually evolved (including the mistakes and corrections),
not just its current shape.

For the design itself (data model, API, provisioning flow), see
`docs/profile_simulations_design.md`. This file is about *what was done and
in what order*, not the design's own content.

---

## Current status (updated as of the latest entry below)

- **Core mechanism**: designed, implemented, and verified. A profile
  declares `SIMULATION_USERS`/`SIMULATION_GROUPS`/`SIMULATIONS`
  (`profiles/simulation.py`); `ensure_simulation_entities` provisions
  reserved-ID users/groups on every profile load
  (`profiles/simulation_provisioning.py`, wired into `api/app.py`);
  `GET /Simulations`/`GET /Simulations/<key>` expose them
  (`api/routes.py`, `api/simulations.py`); the admin panel's existing
  simulator page discovers and loads them with zero manual ID entry
  (`api/admin_simulator.py`).
- **Pilot profile**: `profiles/unified_test.py` declares 2 base personas
  (`commander`, `viewer`), 1 base group (`response_team`), and one
  worked-example simulation (`overall_picture_query`).
- **UI bugs found via live diagnosis, fixed**: stale mapping-panel bleed
  between the legacy and profile-driven paths (a); identity-selection
  dependency not visually clear (b); manual pasted/uploaded JSON missing
  IDs had no fill-in prompt, only a hard error (c, now built generically).
- **Bundled-fixture migration — both series done**: SEC_001 (readiness-team,
  3 phases, 15 personas, 2 extra groups) and FIRE_002 (firefighting, 3
  phases, 10 personas, 3 extra groups) are both migrated into
  `profiles/unified_test.py`'s `SIMULATIONS` — **additively**; the legacy
  "Bundled examples" dropdown still serves all six original fixtures
  unchanged, both series included (a mid-process mix-up briefly removed
  SEC_001 from there; corrected — see the dated entry below). The two
  series keep fully independent persona/group rosters despite the raw
  fixture format reusing identical channel-name strings across both.
- **Legacy "Bundled examples" path removed entirely** (§10 below) — once
  both series were fully migrated, the manual-mapping dropdown, its route,
  and `api/admin_scenarios.py` became redundant and were deleted outright.
  The shared mapping-panel infrastructure the generic manual-JSON
  missing-ID feature (§6, item c) also depends on was carefully kept, not
  deleted along with it.
- **`TeamStatusAgent` roster gap, root-caused (§11) and fixed (§12)**:
  running a SEC_001 simulation used to fail once a step reached
  `record_attendance_response`, because `TeamStatusAgent` keeps its own
  separate approved-roster store that simulation provisioning never
  populated. Fixed generically via a new declarative
  `SimulationRoster`/`SimulationPersona.pre_approved_rosters` mechanism
  (`profiles/simulation.py`, `profiles/simulation_provisioning.py`) that
  never names `TeamStatusAgent` specifically — any current or future
  agent with a similarly-shaped roster store can be targeted the same way.
  Wired up for both SEC_001 (6 personas) and FIRE_002 (3 personas), the
  two series that share that agent's roster.
- **Group membership: investigated, not a gap.** No Telegram group
  membership concept exists anywhere in this codebase, for real or
  simulated users — nothing to fix (§13, item 1).
- **Attendance-cycle gap (§11): root cause confirmed (§13), designed
  (§14), and now implemented (§15).** The admin simulator's message-kind
  steps flow through a dedicated simulation-mode bot process
  (`bot/simulator_app.py`) that reuses `bot/app.py`'s real handlers and
  both background loops unmodified — including the real, unforced
  `run_attendance_check_loop` — with only the Telegram network legs
  stubbed (`bot/simulator_transport.py`). `docs/bot_simulation_mode_design.md`
  is the design; §15 is the implementation record. `/Event` (sensor)
  steps are untouched, exactly as designed.
- **Test suite**: 1456 tests passing (full `tests/` run) — up from 1420 by
  36 new tests: `tests/test_bot_simulator_transport.py` (16),
  `tests/test_bot_simulator_app.py` (14, including one true end-to-end
  test against a real running `api.app` server), and 6 new
  `tests/test_api_admin.py` tests for the proxy route, plus one existing
  test there updated to match the new page (§15).

---

## 1. Architecture and feasibility research → `docs/profile_simulations_design.md`

Starting point: a request for a per-profile simulation mechanism — each
profile declares its own simulation users/groups/simulations, provisioned
with Telegram IDs clearly outside the real range, executed through the
bot's normal ingestion path (no internal shortcuts), and discoverable by
the admin panel via a server-side JSON adapter that preserves the existing
admin-simulator scenario contract unchanged.

Research covered: how profiles are defined/loaded (`profiles/loader.py`,
`profiles/contracts.py`), the *existing* admin test/simulation mechanism
(`api/admin_simulator.py`'s scenario JSON contract, `api/admin_scenarios.py`'s
legacy bundled-fixture mapping), the bot's actual architecture (a
Telegram-polling *client* with no HTTP server of its own — confirmed the
bot's real "entry point" is the API's `/Msg`/`/Event`, which is exactly what
the existing simulator already calls), the users/groups persistence schema,
and the admin panel's Flask/Bootstrap conventions.

**Key decisions**, each confirmed with the user before writing the plan:

- "Goes through the bot" = `POST /Msg`/`POST /Event` — the exact calls
  `bot/transports.py` makes for a real Telegram message. No new HTTP server
  added to the bot process.
- One simulation = one canonical scenario JSON (unchanged shape), with
  persona/group *keys* in place of concrete IDs, resolved server-side.
- Manual JSON entry and profile-driven discovery coexist as two separate,
  additive paths.
- Reserved Telegram ID scheme: users at `9_000_000_000_000_000 + offset`,
  groups at `-9_000_000_000_000_000 - offset` — chosen to sit above
  Telegram's documented 52-bit real-ID ceiling and below the browser's
  `Number.MAX_SAFE_INTEGER`, since the admin simulator's own JS does
  `Number()` arithmetic on these values.
- Simulation users appear in the ordinary Users list like any other user
  (no flag); simulation groups need an *editable* placeholder ID, since an
  operator will eventually swap in a real Telegram group ID.

Output: the full design doc, plus three clarifying-question rounds resolved
along the way (bot endpoint semantics, simulation granularity, admin
visibility rules, ID-edit location, simulation-discovery auth, provisioning
timing).

## 2. Full implementation of the design

Implemented section by section: new files first, then modifications, then
the pilot profile, then tests.

**New files**: `profiles/simulation.py` (types + reserved-ID helpers),
`profiles/simulation_provisioning.py` (`ensure_simulation_entities`),
`api/simulations.py` (the JSON adapter — `materialize_simulation`,
`simulation_catalog_payload`).

**Modified**: `profiles/contracts.py`/`loader.py` (three new optional
`LoadedProfile` fields + validation), `persistence/contracts.py`/`sqlite_store.py`
(`ensure_user_exists`, `ensure_group_exists`, `rename_group`),
`orchestrator/group_routing.py` (`rename()`), `auth/permissions.py`
(`VIEW_SIMULATIONS`), `api/app.py` (provisioning call + blueprint
registration), `api/routes.py` (`build_simulations_blueprint`), `api/admin.py`
(group-rename route, `IDENTITY_BAR` on the simulator page), `api/admin_simulator.py`
(profile-simulations dropdown + JS), `messages/en.py`/`he.py`.

**Pilot profile**: `profiles/unified_test.py` gained `SIMULATION_USERS`
(commander + viewer), `SIMULATION_GROUPS` (response_team), and one
`SimulationScenario` (`overall_picture_query`).

**Real bugs the test suite caught while building this** (fixed immediately,
not deferred):

1. `MessageCatalog.text(self, key, **values)` names its own first
   positional parameter `key` — calling it as
   `messages.text("api.simulation_not_found", key=key)` collided with that.
   Renamed the catalog placeholder to `{simulation_key}`.
2. `_validate_simulation_declarations` first accessed
   `loaded.simulation_users` etc. directly, breaking every existing
   `validate_profile()` test using a `SimpleNamespace` stand-in that
   predates the field. Fixed to read via `getattr(loaded, "...", ())`, like
   every other optional `LoadedProfile` field already does.
3. `tests/test_architecture.py`'s package-boundary rule: `api/app.py` and
   `api/simulations.py` were reaching into `profiles.simulation`/
   `profiles.simulation_provisioning` directly, which aren't declared
   cross-package entry points for `profiles` (only `profiles` and
   `profiles.loader` are). Fixed by re-exporting the needed names through
   `profiles/__init__.py`, the same way `AgentSpec`/`OptimizationPolicy`
   already are.

Also extended `docs/allowed_calls.md`'s operation matrix and
`docs/file_catalog.md` for every new file. Full suite: 1412 passing at the
end of this phase (1408 pre-existing + 4 new, before the later integration
tests were added).

## 3. "Test the server" — end-to-end integration tests

Added `tests/test_integration_profile_simulations.py`: four tests driving a
materialized simulation through the *real* `/Msg`/`/Event` endpoints (real
`SQLitePersistence`, real Flask app, only the LLM call itself scripted) —
proving provisioning, materialization, group-scoped routing, and event
ingestion all work together, not just in isolation.

**Three more bugs caught by actually running these** (not guessed around —
each assertion was corrected against real server behavior):

1. Assumed `/Msg`'s `taken_as` would be `"request"` for a protocol-hint
   fast path; it's actually `"question"`. Removed the incorrect assertion
   (matching what the pre-existing `test_a_protocol_hint_inside_the_group_scope_takes_the_fast_path`
   already only checks).
2. A sensor's `sender_identity` still needs to be a *registered* identity
   for `/Event` (401 otherwise) — same rule as everywhere else. Registered
   it explicitly in the test instead of assuming sensors are exempt.
3. `/Event` returns `202 Accepted` with an `event_id`, not `200` — fixed
   the assertion and confirmed the event actually landed in persistence.

Full suite: 1412 passing.

## 4. Diagnosis: "step 2 doesn't advance" after releasing step 1

The user reported the admin simulator UI appearing stuck after releasing
step 1 of a scenario. Investigated methodically per the user's own
checklist (reproduce, inspect the JS state machine, inspect the actual HTTP
response, check legacy-vs-new, check for swallowed errors) rather than
guessing at a fix.

**Root cause, confirmed by reproduction**: not a bug. The scenario used
(identified by the group label `TELEGRAM_GROUP_RESPONSE_TEAM`, from the
*legacy* bundled fixtures, not the new mechanism) numbers steps globally
across four simulated chats, interleaved chronologically. `nextChatKey()`
picks the single globally-lowest pending step across every chat card, so
after step 1 (response-team), the truly-next step belonged to a
*different* card (`TELEGRAM_GROUP_CAMERAS`). Reproduced deterministically
by mirroring the JS state machine in Python against a real Flask app using
the actual bundled fixture, draining all 9 steps successfully with no
errors — confirming the mechanism itself was never stuck, and that this
predates and is unrelated to the new per-profile mechanism.

## 5. Diagnosis: Telegram ID auto-fill not behaving as intended

The user clarified the real concern: for the profile-driven path, the
admin should never see/type a raw Telegram ID; for manual JSON missing
IDs, the UI should prompt for them. Traced both paths against the actual
current source (not from memory) before proposing anything.

**Findings**:

1. **Core mechanism verified correct** — `materialize_simulation` really
   does inject real reserved IDs (checked directly against the pilot
   scenario's materialized output).
2. **Confirmed bug**: the new "Profile simulations" Load button never
   touched `#mapping-panel` — a panel left visible from a prior "Bundled
   examples" interaction would bleed into the profile-driven view,
   creating the false impression that the new path also asks for IDs.
3. **Confirmed gap**: the profile-driven picker silently stays disabled
   until an identity is chosen elsewhere on the page, with no visible
   explanation of why.
4. **Confirmed pre-existing gap, not a regression**: manually pasted/
   uploaded JSON missing IDs has never had an interactive fill-in prompt —
   only a hard validation error. The only existing "fill in IDs" UI
   (`#mapping-panel`) is hardcoded to the six bundled legacy fixtures
   specifically.

## 6. Fixes (a), (b), (c)

Implemented in `api/admin_simulator.py` only — the core injection mechanism
(`materialize_simulation`, `GET /Simulations`) was explicitly left
untouched throughout, per the user's own instruction.

- **(a)**: centralized the fix rather than patching one button —
  `loadScenario()` itself now calls a new `closeMappingPanel()` as its
  first action, so *every* load path (paste, drop, bundled example,
  profile-driven) closes any stale panel, not just the one reported.
- **(b)**: added `setProfileSimAvailability(enabled, hint)` — an inline
  hint (reusing the page's existing `.subtitle` styling, no new UI
  pattern) plus native `title` tooltips, driven by one function.
- **(c)**: generalized the missing-ID prompt for *any* pasted/uploaded
  JSON, without duplicating the bundled-fixture logic. Refactored the
  bundled-example flow's row-building/collection code into two shared
  functions (`renderMappingRows`, `collectMappingValues`); added
  `collectMissingIdentifiers(raw)` (finds every group needing a real
  `telegram_chat_id`, keyed by the chat's own unique key, and every
  distinct non-numeric `sender_identity` placeholder, deduplicated),
  `applyManualMapping(raw, mapping)` (deep-clones and substitutes, never
  mutates the original), and `loadRawScenario(raw)` (the one shared entry
  point every "load this raw scenario" path now funnels through). One
  `mappingMode` state variable lets one panel serve both flows.
  Verified functionally (not just syntactically) by extracting the two
  pure functions from the actually-rendered page and executing them under
  real Node, not just eyeballing the logic.

Full suite: 1413 passing.

## 7. Bundled-fixture migration — SEC_001 series

New, explicit scope: migrate the six legacy bundled fixtures
(`fixtures/admin_scenarios/*.json`) into real profile-declared
`SimulationScenario` entries, so they become reachable through
`GET /Simulations` with reserved IDs already injected.

Before implementing, gathered concrete evidence (persona overlap across
each series' three phases) and asked three clarifying questions rather
than guessing at scope:

1. Should the legacy "Bundled examples" path keep serving these fixtures
   too, once migrated?
2. How to fill the English catalog side, given the fixtures are
   Hebrew-only?
3. How to scope the first pass, given the size (two independent series,
   6-8 personas each, 6 files total)?

Answers received: retire the legacy path for the migrated ones (**this
answer turned out to be a mismatch — see the correction entry below**),
full literal translation, one full series first as a checkpoint.

**SEC_001 (readiness-team series, 3 phases) migrated**: 15 new
`SimulationPersona` entries (offsets 2-16) and 2 new `SimulationGroup`
entries (`cameras`, `external_forces` — `response_team` reused from the
pilot scenario's own declaration rather than re-declared), 3 new
`SimulationScenario` entries (`sec001_phase1`/`phase2`/`phase3`). Personas
that recur across a series' three phases (the on-call technician, the site
security officer) share one `SimulationPersona`/offset/reserved ID across
every phase they appear in, matching a real continuing roster, rather than
being re-declared per file — the design decision this rested on came from
directly inspecting persona-name overlap across the three source files
first, not assuming it.

Hebrew catalog text was copied programmatically from the source JSON via a
one-off, scratchpad-only generator script (never committed) — guaranteeing
byte-for-byte fidelity with zero manual retyping risk — while English
translations were hand-written. Verified via `load_profile` (real
validation), direct materialization of all three phases (confirmed every
step gets a real digit ID, every group a real negative-digit chat ID, and
the recurring personas resolve to the identical reserved ID in every phase
they appear in), and a new dedicated test
(`test_unified_test_declares_the_migrated_sec001_series`).

Per the (mismatched) "retire" answer, the three SEC_001 files were removed
from `api/admin_scenarios.SCENARIO_FILES` at this point, and
`tests/test_admin_scenarios.py`'s scenario-count test was adjusted from 6
to 3 fixtures. **This was corrected in the next entry.**

One bug caught by the test suite: an actual Hebrew filename had been
written directly into a Python comment in `profiles/unified_test.py` while
documenting the migration — `tests/test_hebrew_leakage.py` correctly
flagged it. Fixed by describing the source generically (scenario IDs)
instead of naming the Hebrew file.

Full suite: 1414 passing (before the correction below; unaffected by it).

## 8. Correction: the legacy-path retirement was a mismatch, not the intended choice

The user had actually selected "keep both, fully additive" for the
legacy-path question in §7 — not "retire," which is what the
`AskUserQuestion` tool reported back as the answer and what was acted on.
Neither side's fault was established; the discrepancy was logged as
product feedback (a queued draft, not sent without the user's approval)
rather than assumed to be a user error.

**Correction applied**: restored all six original filenames to
`api/admin_scenarios.SCENARIO_FILES` (SEC_001 included), reverted
`tests/test_admin_scenarios.py`'s scenario-count test back to asserting
all 6, and corrected `docs/profile_simulations_design.md`'s §9 to describe
the actually-intended, fully-additive state. The profile-declared SEC_001
`SIMULATIONS` entries from §7 were **not** touched — they were correct and
additive regardless of the legacy-path mix-up.

Full suite: 1414 passing.

## 9. FIRE_002 series migration

Same approach as SEC_001 (§7), applied to the firefighting series, per
explicit instruction: additive only (both legacy and profile-driven paths
kept), full literal English translation, shared personas/groups across its
own three phases, independent roster from SEC_001.

**Migrated**: 10 new `SimulationPersona` entries (offsets 17-26, continuing
after SEC_001's last offset) and 3 new `SimulationGroup` entries
(`fire_response_team`/`fire_cameras`/`fire_external_forces`, offsets 3-5 —
deliberately *not* reusing SEC_001's `response_team`/`cameras`/
`external_forces`, since the two series represent different simulated
Telegram groups despite the raw fixture format giving both series'
channels identical generic key names), 3 new `SimulationScenario` entries
(`fire002_phase1`/`phase2`/`phase3`).

Characters recurring across all three FIRE_002 phases (the shift commander,
the surveillance operator, the station commander) share one
`SimulationPersona`/offset/reserved ID per the same continuity pattern as
SEC_001 — confirmed by inspecting the actual persona-name overlap across
the three source files first, the same way as before, not assumed.

One source-data wrinkle handled explicitly: the police-operations-hub
persona appears as two slightly different Hebrew strings across phases
("מוקד משטרה - אגמ" in phase 1, "מוקד משטרה - אג\"מ" in phases 2-3 — a
gershayim-mark typo in the original fixture) — both map to the same
`police_hub_agam` persona/offset rather than becoming two separate
characters, since they're clearly meant to be the same entity.

Hebrew catalog text again copied programmatically from the source JSON
(scratchpad-only generator script, never committed); English translations
hand-written. Verified via `load_profile`, direct materialization of all
three phases (real digit IDs throughout, recurring personas resolving to
the identical reserved ID across every phase they appear in), and a new
dedicated test (`test_unified_test_declares_the_migrated_fire002_series`),
which also explicitly asserts FIRE_002's persona/group keys are disjoint
from SEC_001's and that no two personas anywhere in the profile collide on
key or offset.

One test-authoring mistake caught immediately by running it: an assertion
checked `group_keys.isdisjoint({"cameras", "external_forces"})` against
the *entire* accumulated group list — which is always false once SEC_001's
own same-named groups exist, regardless of FIRE_002. Not a migration bug;
fixed by asserting the actually-meaningful thing (FIRE_002's three group
keys don't collide with SEC_001's).

The legacy "Bundled examples" dropdown was not touched — both series were
already restored/kept there in the previous entry, and this migration
only added to `profiles/unified_test.py`, `messages/en.py`, and
`messages/he.py`.

Full suite: 1415 passing.

## 10. Legacy "Bundled examples" path removed entirely

Once both SEC_001 and FIRE_002 were fully migrated into the profile-driven
mechanism, the legacy dropdown/manual-mapping flow became redundant for
that content — removed outright, at explicit request.

Confirmed first that nothing else depended on `api/admin_scenarios.py`
(grepped for other importers — none) before deleting it. Then:

- Deleted `api/admin_scenarios.py` and `tests/test_admin_scenarios.py`
  entirely; moved its two unrelated `run_stack.reset_profile_databases`
  tests into a new `tests/test_run_stack.py`.
- Removed the `/admin/simulator/example` route and its import from
  `api/admin.py`.
- Removed the "Bundled examples" `<select>`, its population/handler JS,
  and the now-dead `CSRF_TOKEN` from `api/admin_simulator.py`; renamed
  `apply-example` → `apply-mapping`; simplified the click handler to the
  manual-mapping branch only; fixed several stale comments referencing the
  removed path.
- Removed the orphaned catalog keys this route alone owned
  (`admin.simulator.examples`/`choose_example`/`example_invalid`/
  `unregistered_name`) from `messages/en.py`/`he.py`; kept everything the
  shared `#mapping-panel` infrastructure still needs, since that panel also
  serves the generic manual-JSON missing-ID feature (§6, item c).
- Fixed a dangling docstring reference in `api/simulations.py` and a stale
  historical comment in `profiles/unified_test.py` that named the removed
  module.
- Updated `docs/file_catalog.md` and added
  `docs/profile_simulations_design.md` §11 documenting exactly what was
  removed, what was deliberately kept, and what was left alone (the six
  fixture JSON files remain on disk as historical record).

Full suite: 1411 passing (1415 − 4 removed tests; nothing else regressed).

## 11. Investigation: SEC_001 fails at the response-team join/attendance step

Explicit "investigation only, no fixes yet" request, prompted by a real
failure running SEC_001 through the admin UI around the response-team
group-join step.

Read `agents/team_status_agent.py` and grepped `messages/he.py` (no code
changes). Root cause found: `TeamStatusAgent` keeps its own separate
approved-roster persistence (`UNIFIED_TEAM_STATUS_DB_PATH`, via
`register_member()`/`approve_roster()`), entirely distinct from the main
`users` table `ensure_simulation_entities()` provisions. Its
`record_attendance_response` tool checks
`self.status_store.list_members(approved_only=True)` and refuses a caller
not on that list — exactly the failure observed. SEC_001's (and FIRE_002's)
simulation personas were provisioned as ordinary authenticated users, but
never registered+approved on this second, agent-specific roster.
Supporting evidence: `profiles/unified_test.py`'s own `_seed_mock_data()`
already does `register_member()`+`approve_roster()` for its fixed demo
identities — the exact step missing for the new simulation personas. Not
caught by earlier integration tests because those exercised scripted
stand-in agents, never the real `TeamStatusAgent` tool logic.

Reported back to the user with three fix options (extend provisioning to
also populate this roster; add a declarative per-persona/roster flag so
provisioning stays generic; have the scenario's own steps perform the join
in-band). User chose the declarative-flag option — see §12.

## 12. `SimulationRoster` — generic pre-approval declaration, and the SEC_001/FIRE_002 fix

Implements the fix direction chosen in §11: a new declarative flag,
generalized so `ensure_simulation_entities` never has to import or name
`TeamStatusAgent` specifically — see
`docs/profile_simulations_design.md` §12 for the full design writeup.
Design was confirmed with the user (two `AskUserQuestion` checks — the
`SimulationRoster` shape, and the once-only approval-idempotency rule)
before implementing, per the standing "confirm before proceeding" request.

**New**: `SimulationRoster` dataclass (`profiles/simulation.py`) —
`key`, `open` (a store-opening factory, duck-typed to
`register_member`/`approve_roster`/`roster_is_approved`/`list_members`),
`db_path`, `approved_by`. `SimulationPersona` gains
`pre_approved_rosters: tuple[str, ...] = ()`. `LoadedProfile` gains
`simulation_rosters`; `profiles/loader.py` reads `SIMULATION_ROSTERS` and
validates roster-key uniqueness plus every `pre_approved_rosters` reference
resolving to a declared roster, the same style as the existing
persona/group cross-reference checks.

**`ensure_simulation_entities()`** (`profiles/simulation_provisioning.py`)
now also registers every referencing persona onto its declared roster(s)
and approves each roster the first time it has no approval record at all —
deliberately *not* on every restart, since `approve_roster()` marks every
row in that roster's table approved with no per-member scoping, and
re-running it on an already-approved roster would risk silently
auto-approving a real, still-pending member. `ProvisioningResult` gained
`registered_roster_members`/`newly_approved_rosters` for observability.

**Wired into `profiles/unified_test.py`**: one `SIMULATION_ROSTERS` entry
(`key="team_status"`, pointing at the profile's existing
`open_team_status_persistence`/`UNIFIED_TEAM_STATUS_DB_PATH`). Marked
`pre_approved_rosters=("team_status",)` on the six SEC_001 personas that
are actual response-team members (`eli_response_team`,
`danny_response_team`, `michael_response_team`, `yuval_response_team`,
`gil_response_team`, `dan_response_team`) and, extending the same fix to
FIRE_002 for consistency (it shares the exact same `team_status_agent`
group/roster and would otherwise hit the identical bug), the three FIRE_002
personas posting into `fire_response_team` as team members
(`lahav_avi_shift_commander`, `omri_firefighter`,
`yuval_ashed3_commander`). Personas posting into those same channels as
outsiders (`resident_avraham`, `citizen_reports_group`) deliberately do not
get the flag.

**Tests** (`tests/test_profile_simulations.py`, +9 net): loader validation
for `SimulationRoster` (type check, duplicate-key check, unresolved
`pre_approved_rosters` reference, and a fully-valid case); provisioning
behavior against a duck-typed fake roster store (registers referencing
personas, approves once, never re-approves an already-approved roster,
never opens a roster nothing references, reports only newly-registered
members); and one integration test against the real
`profiles.unified_test` profile (using an isolated copy of its declared
roster, not its on-disk DB) proving every `pre_approved_rosters` persona
ends up approved while a bystander persona does not.

## 13. Investigation: group membership, and the attendance-cycle gap's real fix — four rounds, no fixes yet

Two new "investigation only" items, requested together: (1) whether the
simulation mechanism establishes Telegram group *membership* for simulation
users, and (2) the still-open "no attendance cycle is open" failure found
in §11 (root-caused, deliberately left unfixed pending direction).

**Item 1 — group membership**: read `persistence/schema.py`,
`orchestrator/group_routing.py`, and `/Msg`'s actual auth path
(`api/routes.py`'s `post_msg`). Finding: there is no group-membership
concept anywhere in this codebase, for real users or simulated ones —
`users` and `telegram_groups` are two independent tables with no
relationship between them; `/Msg` only checks the caller is a known user,
the chat_id is a registered group, and `sender_identity == caller_identity`.
In production this is fine because Telegram itself is the real membership
authority. `ensure_simulation_entities()` correctly does nothing here
because there is nothing to establish — not a gap, a non-issue, distinct
from the `TeamStatusAgent` roster (a business-domain roster, unrelated to
Telegram group membership).

**Item 2 — attendance cycle, round 1**: traced `bot/background_services.py`'s
`run_attendance_check_loop` (started automatically by the real
`bot/app.py`, but a *separate OS process* from `api/app.py` that the admin
simulator never runs) and confirmed the admin simulator only ever calls
`/Msg`/`/Event` — the real loop that would naturally open a cycle over
real time never fires during a simulation. Proposed three options
(provisioning-time fake-open; manual `force=true` call; leave as an
operator precondition); recommended against baking a fake cycle-open into
`ensure_simulation_entities()`, since a cycle is a dated/time-boxed entity,
unlike the once-only roster-approval fix.

**Round 2**: user reframed around a stated principle — simulation traffic
must flow through the bot, not bypass it. Investigated whether running the
real `bot/app.py` process alongside a simulation would make the loop fire
naturally. Finding: mechanically yes for the loop itself (pure time-driven,
no Telegram-update dependency), but requires real Telegram network
connectivity (`Bot.get_me()` at bootstrap) and real wall-clock time to
cross the daily threshold — impractical for on-demand simulation stepping
— and doesn't generalize to inbound persona messages at all, since reserved
IDs are deliberately unreal and can never produce a real Telegram update.
Recommended `force=true` on `POST /TeamStatus/AttendanceCheck` directly as
the honest alternative (same server-side code path).

**Round 3**: user asked whether a *minimal bot-side endpoint* (skipping
just the polling/token setup) could close the gap without the full
process's cost. Traced `run_attendance_check_once`'s exact two-step body —
found the "open the cycle" half is already 100% the API-side endpoint
(zero bot involvement), and `BotDeps.telegram_client` is eagerly
constructed from a validated token *before* any endpoint could be reached,
so a "minimal" endpoint doesn't actually escape the token/network
dependency. Conclusion: no functional gap left for a bot-side endpoint to
close beyond what the direct API call already closes; also would require
adding a first-ever inbound HTTP surface to `bot/app.py`.

**Round 4 — the precise divergence check**: user pushed on whether
`force=true` could *silently diverge* from real bot behavior if the loop's
own code changed later. Traced every line of
`run_attendance_check_once`/`run_attendance_check_loop`: no business/state
decision logic beyond the bare API call (formatting, retry/backoff, and
per-chat delivery-continue are all presentation/infra, not decision logic)
— *except* one concrete, present-tense fact: the real loop's call is
always unforced (`{}` body → `force=False`), so it never opens a cycle
outside the real due-time window, while `force=true` deliberately bypasses
that. Reported this precisely as a real, acknowledged trade-off rather than
walking back the recommendation — `force=true` is honest about *what* it
does (the same server code) but not equivalent to *when* a real bot would
do it.

**This led directly to round 5** (§14): rather than accept that trade-off,
the user asked whether the bot's *real* handler/dispatch logic (not just
the background loop) could be reused end-to-end, with only the Telegram
network legs stubbed — see §14 for the resulting design.

No code changed in this section — investigation and reporting only, per
explicit instruction each round.

## 14. `bot_simulation_mode_design.md` — planning a dedicated simulation-mode bot process

Final round of the §13 investigation thread: could a second, dedicated bot
process reuse PTB's real `Application.process_update()` dispatcher and the
real handler/background-loop code (`bot/app.py`,
`bot/background_services.py`, unmodified), with only the literal inbound
delivery and outbound send legs stubbed for simulation-reserved identities?

Researched precisely against the installed `python-telegram-bot` 22.8:
`build_deps()`/`PTBTelegramClient.__init__` need no real network access to
construct (only `Application.initialize()`'s `Bot.initialize()` →
`get_me()` does, and PTB's own `BaseRequest` abstraction — 4 abstract
methods — is an already-precedented seam for stubbing exactly that call
locally); `process_update()` requires a real `telegram.Update`
(`Update.de_json(...)`), not the loose `SimpleNamespace` today's
`tests/test_bot_app.py` uses (those tests bypass PTB's dispatcher
entirely, calling handlers directly — a materially different, less
faithful test shape than what round 5 asked for); `FakeTelegramClient`
(`tests/bot_fakes.py`) already proves the outbound stub pattern works.
Confirmed no hard architectural blocker — everything decomposes into
engineering work on top of seams that already exist and are already
tested, with one genuinely new piece of infrastructure (an inbound HTTP
endpoint on a *new* bot-side process — `bot/app.py` itself has never had
one).

Before finalizing, asked 4 `AskUserQuestion` clarifications (all answered):
v1 scope is plain-text messages only (no scenario today uses buttons/
commands); the browser reaches the new endpoint via a same-origin proxy
route on `api/app.py`, not directly (also required by
`tests/test_architecture.py`'s package-boundary rule — `api` may only
import `bot`/`bot.app`, never a new submodule directly, so HTTP is the only
option regardless); `/Event` (sensor) steps stay fully untouched, forever
(sensors were never bot/Telegram traffic); the new process is auto-started
by `run_stack.py` as a third subprocess, not a manual step.

Produced `docs/bot_simulation_mode_design.md` — architecture/feasibility
plan only, same rigor as `docs/profile_simulations_design.md`: new
`bot/simulator_transport.py` (`FakeBotRequest`, `SimulatorTelegramClient`,
synthetic-`Update` construction) and `bot/simulator_app.py` (the entry
point — real `Application`, real handlers, real background loops, no
`run_polling()`, a small Flask-in-a-thread `POST /Simulator-msg` endpoint
bridged into the asyncio loop); a new proxy route on `api/admin.py`;
`api/admin_simulator.py`'s `buildRequest()` retargeted for message-kind
steps only; identity-allowlist gating against the loaded profile's own
declared reserved IDs as the core isolation guarantee; full maintainability
argument (no duplicated logic — the new process imports and calls the real
`bot/app.py`/`bot/background_services.py` functions directly, so future
changes to either stay automatically reflected); file impact and risk
sections, including the one open edge case (`conversation_id`/
`protocol_hint` can no longer be caller-dictated on message-kind steps once
they go through the real handler, since a real Telegram message never
could either).

No implementation yet — planning only, per explicit instruction. Next step
is the user's decision on whether/when to build it.

Full suite: 1420 passing (1411 + 9 new).

## 15. Implementation: the simulation-mode bot process

User: "Go ahead and implement it." Built exactly what
`docs/bot_simulation_mode_design.md` specified, in the order the design's
own File impact list laid it out; no re-scoping, one implementation-time
correction discovered by testing against the real PTB library (below).

**New — `bot/simulator_transport.py`**: `FakeBotRequest(BaseRequest)`,
`SimulatorTelegramClient(TelegramClient)`, `build_synthetic_text_update()`.
Verified directly against installed `python-telegram-bot` 22.8 (not just
inspected) before writing the rest: constructing a real `Application` with
`FakeBotRequest` and calling `process_update()` on a synthetic update
correctly reaches a real `MessageHandler`, and `filters.COMMAND` correctly
never matches it.

**Correction found by that verification, not assumed in the design**:
`register_handlers()`'s `post_init` hook (`bot/app.py`, reused unmodified)
calls `set_my_commands()` — a second real Bot-API call beyond `getMe` that
`FakeBotRequest` also has to satisfy, or reusing `register_handlers()`
as-is breaks. The design's original "refuse everything but getMe" was
narrowed from a hypothesis to a tested fact once this surfaced;
`FakeBotRequest` now succeeds generically for any Bot-API call (documented
why: every real send in this codebase already goes through
`deps.telegram_client`, never `context.bot` directly, so nothing but PTB's
own harmless bootstrap machinery can ever reach this stub) rather than a
narrow allowlist.

**Also found by testing, not assumed**: `Application.start()` does *not*
call `post_init`/`post_shutdown` — those are invoked by `run_polling()`
itself, in a documented order (`initialize → post_init → start`, mirrored
in reverse for shutdown). Since `simulator_app.py` never calls
`run_polling()`, it calls `post_init`/`post_shutdown` by hand, in that same
order — verified directly (both background-loop tasks present and running
in `application.bot_data` after startup, cleanly cancelled after shutdown).

**`SimulatorTelegramClient.reply_since()`** replays the send/edit/delete
status-message lifecycle to its *final* surviving text per message_id,
rather than concatenating a status message with its own "thinking..."
placeholder — an implementation detail the design didn't fully specify;
caught immediately by a test asserting the reply is `"42 events"`, not
`"The model is thinking...\n42 events"`.

**New — `bot/simulator_app.py`**: `SimulatorRuntime` (owns the real
`Application`, the stubs, and the *real* `HttpApiClient`; `startup()`/
`shutdown()` do the by-hand `post_init`/`post_shutdown` sequencing above;
`handle_message()` does identity-allowlist gating against the loaded
profile's own declared `simulation_users`/`simulation_groups`, then
dispatches through `process_update()` and reads back the reply) and
`build_flask_app()` (`POST /Simulator-msg` — `X-Service-Key` auth, JSON
validation, bridges into the runtime's asyncio loop via
`asyncio.run_coroutine_threadsafe`). `main()` mirrors `bot/app.py`'s shape;
a separate `SingleInstanceLock` path (`.bot-simulator.lock`) so a real bot
and a simulation-mode one can run concurrently for the same profile.
`api_client` accepts an optional override (production never passes one —
`run_simulator()` always builds the real `HttpApiClient`) specifically so
tests can inject a `FakeBotApiClient` or a real one against a live test
server, per the design's own test-plan.

**Modified**: `profiles/contracts.py`/`profiles/loader.py` — optional
`simulator_port: int | None = None`, same shape as `api_port`, read via
`getattr(..., None)`. `profiles/unified_test.py` — declares
`SIMULATOR_PORT = 8915`. `api/admin.py` — new `POST /admin/simulator/bot-msg`
proxy route: session-gated like every admin route, no CSRF (a JSON `fetch()`
call, not a form submission — `request.form` is always empty for it, which
is what CSRF checking here actually reads), forwards to
`http://localhost:{simulator_port}/Simulator-msg` with the shared
`X-Service-Key` (read via `api/request_boundary.py`'s already-existing
`BOT_SERVICE_KEY_ENV_VAR`/`SERVICE_KEY_HEADER`, not re-duplicated a third
time), relays the response verbatim. `api/admin_simulator.py` —
`buildRequest()`'s message-kind branch now targets the proxy route instead
of `/Msg` directly, with `conversation_id`/`protocol_hint` dropped from the
request body (the real handler now derives them itself); `sendNext()`'s
message-kind response handling simplified to `payload.reply_text` — the
old `/Msg`-shaped `taken_as`/`duplicate`/inline-job-polling branch is fully
dead now that no message-kind step calls `/Msg` directly, so it (and the
now-orphaned `admin.simulator.taken_as`/`admin.simulator.duplicate` catalog
keys, and the `INLINE_KINDS` JS constant) were removed rather than left
unreachable. Two new catalog keys added
(`admin.simulator.bot_mode_unconfigured`/`bot_mode_unreachable`/
`bot_no_reply`) in both `en`/`he`. `run_stack.py` — a third subprocess
(`python -m bot.simulator_app`), started only when the active profile
declares `SIMULATOR_PORT` (so every profile that hasn't opted in is
completely unaffected); `.bot-simulator.lock` added to the known-sidecar
cleanup list. `config/server_control.py` — `ProfileInfo.simulator_port`
(optional) so `run_stack.py` can make that "started only if declared"
decision; `bot_sim_pid` added to the status file. `tests/api_fakes.py` —
`_FakeLoadedProfile`/`build_context` gained a `simulator_port` parameter.

**One deliberate response-shape simplification, flagged rather than
silently decided**: `/Msg`'s direct response carries a rich shape
(`taken_as`, `event_id`, `status`) that only exists *outside* the real
handler — `present_incoming_message()` never returns it anywhere the
simulator process could observe, only sends it via `deps.telegram_client`.
`/Simulator-msg` therefore returns `{"reply_text": ...}` — the rendered
text a real Telegram user would see — not a reconstruction of that richer
shape. Concretely: an async job's `event_id`/`status: "queued"` is no
longer visible to the admin UI for message-kind steps, so `pollJob()`-style
live status polling no longer applies to that path (it still does for
`/Event`, unchanged) — a job's eventual outcome is still delivered for
real, just via the real bot's own background notification loop, out-of-band
from this synchronous request.

**Tests** (36 new, all passing alongside the existing suite):
`tests/test_bot_simulator_transport.py` (16 — `FakeBotRequest` satisfies
`Application.initialize()` and `set_my_commands()` with no network;
`SimulatorTelegramClient`'s full `TelegramClient` ABC and its send/edit/
delete replay logic, including the "only the final edit, not the
placeholder" case and a "does not leak into the next request" case;
`build_synthetic_text_update()` classified correctly by real PTB filters,
never matches a `CommandHandler`, and reuses the same message_id for a
repeated `source_message_id`). `tests/test_bot_simulator_app.py` (14 —
every identity/chat_type/shape gate on `handle_message()`; a full dispatch
through the real handler with `FakeBotApiClient`, proving the captured
reply; the `/Simulator-msg` HTTP surface itself run on a real background
thread with a real second asyncio loop (the actual thread-bridge, not a
simplification of it) via a Flask test client — auth, bad JSON, refused
identity, successful dispatch, and a handler-exception 500; and one true
end-to-end test using the real `HttpApiClient` against a real, live
`api.app` Flask server (`RunningApiServer`, mirroring
`tests/test_bot_transports.py`'s own pattern) — this one caught that a
persona needs `full_name` set for `_gate_on_full_name()` (real,
unmodified) not to intercept it, exactly as a real freshly-registered
Telegram user would be). `tests/test_api_admin.py` (+6 — session
requirement, "not configured" 501, "unreachable" 502, successful forward
with header/body verified via a real stdlib `http.server` stand-in for the
simulator process, refusal-status passthrough, and that the service key
never reaches the browser); one pre-existing test there
(`test_simulator_page_talks_to_the_real_endpoints_only`) updated to match
— `/Msg` no longer appears in the page, `/admin/simulator/bot-msg` does,
`/Event`/`/Job/`/`X-Identity` still do.

Full suite: 1456 passing (1420 + 36 new). `docs/file_catalog.md` updated
for every new file (including this design doc itself, initially missed and
caught by `tests/test_file_catalog.py`).

## 16. First real run: `bot-service` was never registered — three linked findings, first fix applied

Running a real SEC_001 step through the live `bot.simulator_app` for the
first time surfaced a generic `bot.handler_error` reply. Investigated
across several rounds (no code changes until root cause was fully nailed
down, per explicit instruction each round):

- Traced the exact request via the live `bot-sim-unified_test.stderr.log`:
  `POST /Telegram/Admission "401 UNAUTHORIZED"` → `_guarded()`'s generic
  `except Exception` → `bot.handler_error`. Not `full_name` (already set
  correctly on `eli_response_team`), not a bot-simulation-mode-specific
  bug — the real `bot-unified_test.stderr.log` showed the identical 401 on
  the same class of call, at the same time, from the real unmodified
  `bot.app`.
- Ruled out `run_stack.py`'s env-propagation mechanism precisely (one
  `os.environ.copy()`, passed via `env=` to all three `subprocess.Popen()`
  calls in one `start()`; `BOT_TOKEN` — same file, same mechanism, same
  processes — was working, proving propagation itself wasn't broken) and
  ruled out a stale-process/timing explanation (user stopped and restarted
  the whole stack fresh from a shell with a confirmed-correct
  `BOT_SERVICE_KEY`; identical 401 persisted).
- Root cause, found by direct (read-only) inspection of the live
  database: `bot-service` was never a registered user in this profile's
  `users` table at all — `authenticate()`'s second check
  (`persistence.read_user(identity) is None`) produces the *identical*
  401/message as a key mismatch (by design, so a caller can't
  distinguish "wrong key" from "never registered" — `api/request_boundary.py`'s
  own comment says so), which is exactly why the key/environment
  investigation kept coming up clean.
- This one gap explained two real, general-layer symptoms at once: the
  admission-check crash, *and* `run_notification_poll_loop`'s own
  `GET /Notifications` call (also bot-service-scoped) failing identically
  — meaning async job completions were not reaching **any** real Telegram
  user either, not just the simulator. Confirmed the raw "taken as
  {kind}" / "Task ID: {uuid}" text the operator also found unfriendly
  (`messages/he.py`'s `bot.taken_as`/`status.async_ack`) is genuine,
  unmodified production text from `bot/app.py`'s `_submit_and_format_message`
  — the admin simulator renders it verbatim, no wrapping — so not a
  simulator-only issue either.

User set priority order (1 → 2 → 3), all framed around the same
constraint: fixes must improve real production behavior, not just the
simulator's display.

**Priority 1 — done and verified.** Used the existing
`/admin/bot-service/provision` admin route (no new code — a one-time data
action, exactly as designed) against the live, running `unified_test` API
server: logged in, fetched the CSRF token, submitted the form. Confirmed
directly against the database (`bot-service`, `commander`,
`auto_register=0` — present). Then watched the **already-running** real
`bot.app` and `bot.simulator_app` processes' live logs (no restart needed
— `authenticate()` reads the `users` table fresh on every request) turn
from `401 UNAUTHORIZED` to `200 OK` on `/Notifications`,
`/TeamStatus/AttendanceCheck`, and (same code path) `/Telegram/Admission`,
for both processes, within one poll cycle. This is a genuinely general,
production-layer fix — the real bot's own admission and notification
delivery are now working, independent of anything simulation-related.

**Priority 2 — done and verified.** Two separate wording decisions,
confirmed with the user before editing (`AskUserQuestion`, twice — the
task-ID visibility mechanism, then the `kind` wording) each time:

- The task ID's user-facing visibility (`status.async_ack`) is gated on
  `tools.deep_debug_enabled()` — the codebase's own existing, general
  "verbose diagnostics" flag (`config/environment.py`'s `DEEP_DEBUG`,
  already used elsewhere in `bot/app.py` for live-trace polling), not a
  new mechanism invented for this message. Default: friendly text, no raw
  ID (no bot command lets a caller look a job up by it today, so it had no
  user-facing purpose). Under `DEEP_DEBUG=true`: the same text plus the
  ID, via a second catalog key (`status.async_ack_debug`). The job ID
  itself is completely unaffected either way — only this one reply's text.
- `bot.taken_as`'s raw `{kind}` interpolation (always literally "report"
  or "request", the only two kinds that reach this fallback branch) — was
  showing the raw English enum word even inside the Hebrew sentence.
  Split into two full-sentence catalog keys per kind
  (`bot.taken_as_report`/`bot.taken_as_request`) instead of interpolating
  a word, so each language phrases it naturally rather than force-fitting
  an English token into it (`_TAKEN_AS_CATALOG_KEYS` dict, `bot/app.py`).

Both are genuine `messages/en.py`/`messages/he.py` catalog changes read by
`bot/app.py`'s `_submit_and_format_message()` — real, shared production
code, unmodified by the earlier simulation-mode work — so real Telegram
users see the improved text identically to the simulator, per the user's
standing "no behavioral divergence" principle. Tests updated to match
(`tests/test_bot_app.py` — one test split in two, covering both the
default and `DEEP_DEBUG` paths; `tests/test_messages.py`,
`tests/test_integration_ingestion_parity.py`). Full suite: 1457 passing.

**Priority 3 — done, verified by tests.** Genuinely simulator-specific
(confirmed with the user before designing: no production/simulator
divergence, since the real delivery already happens identically either
way — this is purely the admin page's own missing "did anything new
arrive" check), so unlike Priorities 1–2 this adds simulator-only surface
area, deliberately.

Design confirmed via two `AskUserQuestion` checks first: a late arrival
renders as a **new** bubble (it's a genuinely separate, later message, not
an edit of the original ack — matches how a real Telegram user would see
a second message appear), and the poll window reuses the existing
`pollJob()` constants (`POLL_INTERVAL_MS`/`POLL_TIMEOUT_MS`) rather than
inventing new ones.

Rejected building this on `GET /Job/<id>` (the old `pollJob()`'s target):
`/Simulator-msg`'s response only ever returns `{reply_text}` — the
structured `job_id` never reaches it, since `_submit_and_format_message()`
(production code) returns only the formatted string. Piping `job_id` out
through there would mean changing a production function's return contract
just to serve the simulator — exactly the entanglement being avoided.

**What was built instead** — generalizing `SimulatorTelegramClient`'s
existing capture mechanism (`reply_since()`, from Priority-1-era work)
into a proper watermark-based poll:

- `SimulatorRuntime.handle_message()`'s response now also returns a
  `watermark` (`{status_len, sent_len}` — `SimulatorTelegramClient.mark()`,
  JSON-shaped).
- New `SimulatorRuntime.poll_chat(chat_id, since)` — "what's arrived in
  this chat since `since`", reusing `reply_since()` verbatim; gated by the
  exact same identity-allowlist as `handle_message` (a poll can only ever
  watch a currently-declared simulation chat).
- New `GET /Simulator-msg/poll` on `bot.simulator_app` (same
  `X-Service-Key` auth as the POST route; a shared `_check_service_key()`
  helper factored out of both).
- New `GET /admin/simulator/bot-poll` proxy on `api/admin.py` — session-gated,
  read-only (no CSRF concern). Both `api/admin.py` proxy routes now share
  one `_forward_to_simulator(method, path, **kwargs)` helper instead of
  duplicating the httpx/error-handling logic.
- `api/admin_simulator.py`'s `sendNext()` fires `pollSimulatorChat()`
  after every message-kind step — **not awaited**, deliberately: unlike
  `pollJob()` (which blocks because the operator is explicitly watching
  one sensor event), most message-kind steps resolve inline immediately,
  so blocking the step queue on a multi-minute watch for every single step
  would make working through a scenario painfully slow for no benefit.
  Runs quietly in the background; appends a new system bubble only if and
  when something actually arrives, advancing its own watermark so a
  second background delivery (if any) gets its own bubble too rather than
  repeating the first.

**Tests** (9 new): `tests/test_bot_simulator_app.py` — `poll_chat()`
finding nothing / finding a delivery made the same way
`run_notification_poll_loop` actually makes one (`send_reply` called
independently of any request) / refusing an undeclared chat_id; the same
three shapes again at the HTTP layer via `_RunningSimulator`, plus a
full round-trip proving a *second* poll from the *new* watermark correctly
finds nothing further. `tests/test_api_admin.py` — the proxy route's
session gate, "unconfigured" error, and query-param forwarding (extended
`_FakeSimulatorHandler` with a `do_GET`, shared with the existing POST
tests). Full suite: 1466 passing (1457 + 9).

All three priorities from this round are now complete and verified.

## 17. Architecture check: does the bot decide anything? — real inconsistency found and fixed

User asked for a precise trace, against the project's own standing principle
("the bot relays, only the server decides"): was `_submit_and_format_message`
(`bot/app.py`) — the place `bot.taken_as`/`status.async_ack` text got
assembled — doing any decision-making itself?

**Traced and confirmed**: no business decision happens bot-side.
`kind`/`job_id` are read straight from `/Msg`'s `taken_as`/`event_id` fields
(`bot/transports.py`'s `HttpApiClient.submit_message()`) — the bot never
classifies or decides to queue anything. But a **real, pre-existing
inconsistency** turned up: for `question`/`conversational`/`clarification`,
the server already sends a ready-to-display `"answer"` field (built
server-side via its own `messages.text(...)` calls) — the bot's job for
those is pure relay. For `report`/`request`/the queued-job ack, `/Msg`'s
response carried *no* `"answer"` field at all — just `taken_as`/`event_id`/
`status`. The tell: the server already computes equivalent text
(`api.queued_report`/`api.queued_request`) for its *own* conversation-memory
record (`_remember(...)`), but never puts it in the HTTP response — leaving
the bot to independently reconstruct similar text from its own, separately-
maintained catalog. Not something introduced by §16's wording work — that
work rewrote the bot-side text within an already-inconsistent pattern,
without realizing report/request are *always* queued via the real path
(confirmed by re-reading `api/routes.py`: `ctx.queue.submit(...)` then
`jsonify({..., "status": "queued"}), 202`, unconditionally) — meaning
`bot.taken_as_report`/`bot.taken_as_request` (added in §16) were themselves
unreachable in production; only `status.async_ack`/`_debug` ever fired for
real report/request traffic.

**Fix — `/Msg`'s response contract extended** (`api/routes.py`): all three
report/request queued-response call sites now include `"answer"`, via a new
`_queued_answer_text(messages, kind, task_id)` — DEEP_DEBUG-gated
(`tools.deep_debug_enabled()`, read **server-side** now, not by whichever
bot process happens to relay the reply — the single-source-of-truth
process now owns this decision, same flag, moved to where "decides"
belongs). `api.queued_report`/`api.queued_request` reworded to the friendly,
no-task-id text; new `_debug` variants carry the ID, for both the HTTP
answer and (always, regardless of DEEP_DEBUG — an internal audit record,
not user-facing) the conversation-memory write.

`_submit_and_format_message` (`bot/app.py`) **collapsed to pure relay for
every kind** — no more kind-based branching, no more DEEP_DEBUG check, no
more bot-local catalog lookup for this content:

```python
lines = [submission_result.answer_text or messages.text("bot.no_answer")]
if submission_result.awaiting_approval:
    lines.append(messages.text("bot.waiting_approval"))
return "\n".join(lines), submission_result
```

Removed as dead now that the server is the single source of truth:
`bot.taken_as_report`/`bot.taken_as_request`/`status.async_ack`/
`status.async_ack_debug` (all four catalog keys, both languages) and
`_TAKEN_AS_CATALOG_KEYS`. Also cleaned up, found along the way: `en.py` had
an entire verbatim-duplicate block of ~15 keys (including the original
`api.queued_report`/`api.queued_request`) — pre-existing, unrelated to this
work, harmless (Python dicts just keep the last one), removed while already
editing that exact area rather than left to compound further.

**Tests**: `tests/test_api_messages.py` gained the server-side coverage
this now belongs to (`test_a_report_includes_the_task_id_under_deep_debug`,
plus `answer`-field assertions added to the existing report/request 202
tests). `tests/test_bot_app.py`'s now-obsolete bot-side DEEP_DEBUG tests
(testing behavior the bot no longer performs) replaced with pure-relay
tests (`test_report_reply_is_a_pure_relay_of_the_servers_own_answer`,
`test_request_awaiting_approval_appends_the_waiting_line`) asserting the
bot echoes exactly what a `FakeBotApiClient` stands in for the server
providing. `tests/test_messages.py`/`test_integration_ingestion_parity.py`
updated to the surviving keys/wording. Full suite: 1465 passing.

**Part 2 (the hardcoded `awaiting_approval=False`) — investigated, not yet
fixed; a real finding to flag rather than a straightforward wiring bug.**
See the conversation for the full report: `/Msg`'s response for report/
request is *always* the immediate, synchronous `202 {status: "queued"}` —
`ctx.queue.submit(...)` schedules the actual protocol selection/risk
assessment as background work; the server itself cannot know, at the
moment it answers `/Msg`, whether that will later become held for approval
(`job_status()`'s own `held_for_approval` branch, `api/routes.py`, only
ever fires against `/Job/<id>` polling or the async `approval_hold`
notification — both real, working, and entirely independent of this
field). Confirmed `register_open_approval_hold` is *already* correctly
called from the real, working path (`push_approval_prompt`, triggered by
the async notification) — the `bot/app.py` call site gated on
`submission_result.awaiting_approval` is redundant dead code, never
reachable via the real client. Awaiting the user's direction on how they
want this resolved, given the field cannot be populated from `/Msg`'s
synchronous response as currently modeled.

## 18. `awaiting_approval` removed entirely — dead code, not a wiring fix

User chose option 1 from §17's three options: remove the field and every
call site depending on it, rather than leave a documented always-`False`
stub or invent a synthetic value. Traced every reference across the repo
first (not just the ones already found) to separate this concept
precisely from an unrelated, same-named-sounding one: `api/routes.py`'s
`"user_awaiting_approval"`/`"group_awaiting_approval"` (safe-mode
auto-registration blocking reasons) and `tests/bot_fakes.py`/
`tests/test_unsafe_system.py`'s matching tests are a **completely
different concept** — left untouched, on purpose.

**Removed:**
- `MessageSubmissionResult.awaiting_approval` (`bot/contracts.py`).
- The hardcoded `awaiting_approval=False` kwarg in
  `HttpApiClient.submit_message()` (`bot/transports.py`).
- `bot/app.py`'s dead gate (`if submission_result.awaiting_approval and
  submission_result.job_id: interactions.register_open_approval_hold(...)`)
  and the `bot.waiting_approval` append — `_submit_and_format_message`
  collapses further, to a single-line pure relay.
- `"bot.waiting_approval"` (`messages/en.py`/`he.py`), now unused.

**Confirmed untouched and still working**: `bot/interactions.py`'s
`register_open_approval_hold`/`unregister_open_approval_hold` and their
real call sites — `push_approval_prompt` (the async `approval_hold`
notification path, `bot/interactions.py:575`) and the approval-answer
handler (`:665`) — none of this was ever reached through the field being
removed; it's a fully independent, already-correct mechanism. Verified
directly: `tests/test_unified_role_and_security.py`'s dedicated
`register_open_approval_hold`/`unregister_open_approval_hold`/
`get_open_approval_holds` tests pass unchanged.

**Tests**: `tests/test_bot_transports.py`'s
`test_submit_message_report_never_claims_to_know_awaiting_approval`
renamed to `test_submit_message_report_returns_a_job_id`, dropping only
the now-impossible assertion (kind/job_id coverage kept).
`tests/test_bot_app.py`'s `test_request_awaiting_approval_appends_the_waiting_line`
renamed to `test_request_reply_is_also_a_pure_relay_of_the_servers_own_answer`,
same reasoning — confirms `request`, like `report`, is now a pure relay of
whatever `/Msg` sends, with no bot-side augmentation left for either.
Full suite: 1465 passing (net-zero test count — two renames, no coverage
lost, none of it dead-code testing anymore).

## 19. Duplicate-ack bug: root-caused from real logs, fixed, and item 2 confirmed as a side effect

First real-world use of Priority 3's polling surfaced a UI bug: the same
ack bubble twice with a stray "המודל חושב..." (model thinking) bubble
between them. Investigated with real evidence before touching anything —
checked `bot-sim-unified_test.stderr.log` around the reported timestamp
and found exactly **one** `POST /Simulator-msg` there, ruling out a
duplicate client send immediately. The same log showed the same chat
(`-9000000000000001`) received a step at 22:25:11 and another at
22:27:07 — well inside the first step's own 5-minute poll window.

**Root cause**: `pollSimulatorChat()` starts one independent poll loop per
step sent, scoped only by `chat_id`, not by which step started it.
Multiple steps sent to the same chat within that window mean multiple
loops read the *same* shared, chronological event stream with
*independent, never-synchronized* watermarks. `reply_since()`'s send/edit
collapse (so a status placeholder never shows on its own) only works
*within one poll's own query window* — when an unrelated, earlier step's
stale watermark happens to land between a *later* step's own send and
edit events, that later step's own already-displayed synchronous exchange
gets split across two of the earlier loop's poll ticks and rendered
twice: once as "model thinking..." (the send, discovered alone), once as
the ack again (the edit, discovered on the next tick).

**Fix — `api/admin_simulator.py` (JS only; no server-side change)**: a
per-chat_id "generation" counter (`pollGenerationByChatId`). Starting a
poll loop claims the next generation for that chat *synchronously*, before
any `await`; each loop iteration checks — both before issuing a request
and after receiving its response — that it still holds the current
generation for that chat, and stops immediately (no further requests, no
rendering) the moment a newer step's own loop has superseded it. A newer
step's own loop is never blocked by this — it simply becomes the chat's
sole active watcher.

**Tests**: `test_a_superseded_poll_loop_never_polls_or_renders_anything`
(`tests/test_api_admin.py`) — executed for real under node, not just
syntax-checked. Runs a same-process, self-calibrating single-loop timing
baseline, then two loops started back-to-back for one chat; asserts the
two-loop call count stays close to the baseline rather than roughly
doubling it (a fixed call-count threshold would be fragile across
machines). **Verified the test actually catches the regression**, not
just passes by coincidence: temporarily stripped the generation checks,
confirmed the test fails (26 calls vs. an expected ≤21, against a ~13-14
call baseline — matching the exact "roughly double" signature of the
original bug), then restored the real fix and confirmed it passes again.
Full suite: 1466 passing (1465 + 1).

**Item 2 (surfacing `format_job_result()`'s classification detail),
confirmed resolved as a side effect — not a new mechanism.** This fix is
purely client-side (JS generation guard); it does not touch
`SimulatorTelegramClient`/`poll_chat()`/the notification-delivery path at
all. Those were already proven correct by existing tests
(`test_poll_chat_surfaces_a_background_delivery_that_arrives_after_the_watermark`,
`test_simulator_msg_poll_finds_a_reply_delivered_after_the_original_watermark`,
`tests/test_bot_simulator_app.py`) — a genuine `deliver_job_result()`
delivery (which calls `format_job_result()`, carrying the real
classification/protocol/outcome) was already correctly discoverable by
polling before this fix, and remains so after it; what was broken was
never "can a genuine delivery be found," it was "does an *unrelated*,
stale loop also render something that was never meant for it." §19's new
test additionally confirms the *surviving* (correct) loop keeps polling
and rendering normally even with a second step in play — so the specific
classification detail the user wants to see in the follow-up message now
reaches them cleanly, with no duplicate or stray bubble noise around it.

The live stack from this investigation's own session had already been
stopped by the time this fix was verified, so confirmation here is
test-based (including the deliberate revert-and-confirm above) rather
than a fresh live-log check; offered to verify against a live run once
the user restarts one.
