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
- **Test suite**: 1420 tests passing (full `tests/` run) — up from 1411 by
  9 new tests covering `SimulationRoster`. Dedicated files:
  `tests/test_profile_simulations.py`, `tests/test_api_simulations.py`,
  `tests/test_integration_profile_simulations.py`, `tests/test_run_stack.py`
  (replacing `tests/test_admin_scenarios.py`), plus additions to
  `tests/test_persistence_conformance.py`, `tests/test_group_routing.py`,
  `tests/test_api_admin.py`, and `tests/api_fakes.py`.

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

Full suite: 1420 passing (1411 + 9 new).
