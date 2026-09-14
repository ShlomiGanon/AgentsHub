# Simulation Mechanism — Architecture & Feasibility Plan

Status: design/feasibility study only — no implementation yet. This document
captures the research findings, the decisions made while scoping the work,
and the proposed design for letting each profile declare and expose its own
simulations, provisioned with reserved (never-real) Telegram IDs, executed
through the existing `/Msg` / `/Event` ingestion path, and surfaced in the
admin panel via a server-side JSON adapter.

---

## 1. Research findings (current codebase)

### Profiles

`profiles/contracts.py` and `profiles/loader.py` (see also `docs/profile_spec.md`):
a profile is a plain Python module that **declares** config as module-level
constants; `profiles.loader.load_profile()` is the only place anything
actually gets constructed, returning a frozen `LoadedProfile`. Optional
attributes (`TIMEZONE`, `CONVERSATION_HISTORY_TURNS`,
`EVENT_TYPE_REQUIRED_FIELDS`, …) all follow one consistent idiom:

- `getattr(profile_module, "NAME", default)` in `profiles/loader.py`'s
  `load_profile()`,
- a matching frozen-dataclass field with the same default on `LoadedProfile`
  (`profiles/contracts.py`),
- validation checks added to `validate_profile()`, which collects every
  failure before raising rather than stopping at the first one.

`profiles/unified_test.py` is the richest existing profile (viewer/commander
role separation, Hebrew message catalog, real agents/protocols) and is
already the one the bundled Hebrew scenario fixtures target — a natural
pilot profile for the new mechanism.

### The existing admin test/simulation mechanism

`api/admin_simulator.py` (rendered by `api/admin.py`'s `/admin/simulator`
route) plus `api/admin_scenarios.py`:

- The admin page loads a scenario JSON (paste, drag-and-drop upload, or a
  bundled example) and draws one card per declared chat/sensor.
- Releasing a step sends it from the **browser itself** — not the server —
  to the real `POST /Msg` / `POST /Event` on the same origin, with
  `X-Identity` set to that step's own `sender_identity`. This is deliberate:
  the module's own docstring calls it "the exact request the bot makes," so
  the same registration, permission, and group-scoping rules apply as in
  production, with no bypass of the ingestion logic.

The canonical JSON contract (confirmed here — **must stay unchanged**):

```json
{
  "scenario": {
    "id": "...",
    "title": "...",
    "description": "...",
    "tags": ["..."],
    "expected_agent_actions": [
      { "trigger_step": 1, "description": "...", "action_type": "..." }
    ]
  },
  "chats": [
    { "key": "response_team", "kind": "message", "label": "...",
      "telegram_chat_id": "-1001234567890", "telegram_chat_type": "supergroup" },
    { "key": "commander_dm", "kind": "message", "label": "...",
      "telegram_chat_type": "private" },
    { "key": "fence_sensors", "kind": "event", "label": "..." }
  ],
  "steps": [
    { "step": 1, "chat": "response_team", "sender_identity": "1002003",
      "sender_name": "...", "text": "...", "timestamp": "2026-09-06T07:30:00Z" },
    { "step": 2, "chat": "fence_sensors", "sender_identity": "sensor-north-1",
      "text": "..." },
    { "step": 3, "chat": "commander_dm", "sender_identity": "5551",
      "text": "...", "protocol_hint": "overall_situational_picture",
      "source_message_id": "4821" }
  ]
}
```

`kind: "message"` steps go to `POST /Msg`; `kind: "event"` steps go to
`POST /Event`. `timestamp`, `sender_name`, `label`, `title`, `description`,
and `tags` are display-only. The browser-side `validateScenario()` function
enforces:

- `sender_identity` matches `/^\d+$/` and is a positive integer,
- a group's `telegram_chat_id` matches `/^-\d+$/` and `Number(id) < 0`.

A separate, older mechanism (`api/admin_scenarios.py`'s
`map_legacy_scenario`) converts six bundled Hebrew fixtures
(`fixtures/admin_scenarios/*.json`, a different legacy "event stream" shape)
into this canonical shape, substituting admin-typed persona/group IDs for
symbolic placeholders. **This is the closest existing precedent** to what is
being asked for here — "template scenario + placeholder substitution →
canonical JSON" — just done manually today, for hardcoded legacy fixtures.

### The bot process

`bot/app.py` and `bot/transports.py`: the bot is a `python-telegram-bot`
long-polling **client** with no HTTP server of its own. Its only outbound
calls are HTTP requests made by `HttpApiClient` to the API process's
endpoints — `/Msg`, `/Event`, `/Telegram/Admission`, `/Groups`, `/User`, and
so on. The bot process holds essentially no business logic of its own;
permission checks, protocol selection, and orchestration all live behind the
API's routes (`api/routes.py`).

**Conclusion**: "the bot's own endpoint," for simulation purposes, is
`/Msg` / `/Event` — these already are the exact calls the bot process makes
for a real Telegram message, and they are already what the existing
simulator uses for that same reason. There is no separate bot-side HTTP
surface to add.

### Users and groups are both already modeled

`persistence/schema.py`:

- `users` — `telegram_identity` (TEXT PRIMARY KEY), `permission_level`,
  `full_name`, `auto_register`.
- `telegram_groups` — `chat_id` (TEXT PRIMARY KEY), `agent_name`, `label`,
  `created_at`, `auto_register`.

`auto_register = 1` is the **safe-mode pending-approval** flag
(`api/routes.py`'s `/Telegram/Admission` handler) — it must never be set on
simulation entities, or safe mode would block them from being used.

`orchestrator/group_routing.py`'s `GroupRoutingTable` is the in-memory,
write-through cache backing `telegram_groups`, exposing `upsert`,
`register_telegram_group_if_missing`, `approve`, and `remove`. A group
binding is simply `chat_id → agent_name`, where `agent_name` is either a
specialist agent name or the literal `main_agent` (unscoped routing).

### Provisioning today

- `cli/user_admin.py` / `cli/group_admin.py` write directly to
  `persistence` via `write_user` / `write_group` (upsert semantics) or
  `register_telegram_user_if_missing` / `register_telegram_group_if_missing`
  (atomic create-if-absent, but hardcoded to `viewer` / `main_agent` and
  `auto_register = 1`, tied specifically to the safe-mode workflow — not
  directly reusable for simulation provisioning).
- `api/admin.py`'s `/admin/bot-service/provision` route is a precedent for
  "admin-triggered, idempotent, calls persistence directly" provisioning of
  a special identity, reusing the same write path `cli.user_admin` uses.

### Profile load → running process

`api/app.py`'s `build_context()` is the one place a profile actually becomes
a running server: it opens `persistence`, builds `settings_store`,
`registry`, `group_routing`, and so on. This is the only sensible hook for
"on every profile load, ensure simulation entities exist" — `bot.app.build_deps()`
never opens its own database (it is a pure HTTP client to the API), and
`cli.user_admin` / `cli.group_admin` are on-demand tools, not "load."

### Admin UI conventions

`api/admin.py` and `api/admin_api_pages.py`:

- Bootstrap 5.3.3 via cdnjs (the RTL build for an `he` profile, LTR
  otherwise), plus a shared `_DASHBOARD_STYLE` CSS-variable theme
  (`--panel`, `--commander`, `--viewer`, `.block-console`, `.btn-console` /
  `.btn-console-primary`, `.api-card`, `.api-form-grid`, …).
- A recurring pattern for admin pages that need to call the real API: an
  **API-identity selector** (`IDENTITY_BAR` in `api/admin_api_pages.py`)
  that lets the operator pick a registered identity from a dropdown, stored
  in the admin session, and used as `X-Identity` on `fetch()` calls the
  browser makes straight to the public JSON API.
- Admin **CRUD forms** (group/user write, remove, approve; bot-service
  provisioning) go straight from the Flask route to `ctx.deps.persistence` /
  `ctx.group_routing` in-process — no self-HTTP call.
- Only the simulator's **execution** path deliberately goes over real HTTP,
  to exercise the full ingestion stack end to end.

### Telegram ID shape constraints already enforced

- `POST /Telegram/Admission` requires `telegram_identity.isdigit()` and
  `int(telegram_identity) > 0`.
- A group `chat_id` must be a negative digit string.
- Telegram's own documentation caps a real ID at 52 significant bits
  (≈ 4.5 × 10¹⁵).
- The browser's `Number()` arithmetic (used by the simulator's own
  validation) is only safe below `Number.MAX_SAFE_INTEGER`, i.e.
  2⁵³ − 1 ≈ 9.007 × 10¹⁵.

---

## 2. Decisions made while scoping this (resolved, and driving the design below)

| Question | Decision |
|---|---|
| "Dedicated simulation endpoint on the bot" | Use the API's existing `POST /Msg` / `POST /Event` — these already are the bot's own code path (`bot/transports.py`), and the existing simulator already uses them for that reason. No new HTTP surface on the bot process. |
| Shape of "one simulation" | One canonical scenario JSON (`{scenario, chats, steps}`, unchanged shape) per simulation, declared by a profile with persona/group **placeholder keys** in place of concrete IDs, resolved to that profile's reserved IDs server-side — extending the existing `map_legacy_scenario` idea, profile-declared instead of hand-typed. |
| Manual vs. profile-driven input | Both coexist. Manual paste/upload/drag-drop keeps working exactly as today, unchanged. Profile-driven is a new, additive path that requires **no ID entry** at all. |
| UI look | Native Bootstrap 5.3.3 + the existing `_DASHBOARD_STYLE` / `.block-console` / `.btn-console` system — no new styling approach introduced. |
| Visibility: simulation users | Ordinary rows in the existing Users list/page — no flag, no filtering. The only thing distinguishing them from a real user is that their Telegram ID is deliberately out of the real range. |
| Visibility: simulation groups | Visible in the existing Groups page; their placeholder `chat_id` must be **editable** in place, since an operator will later swap in a real Telegram group ID once a real group exists. |
| Where the ID-edit control lives | A generic "change chat ID" action added to the existing `/admin/groups` page — useful for any group, not simulation-specific. The new Simulations screen just links there. |
| Auth for the new simulation-discovery calls | Reuse the existing API-identity selector (`IDENTITY_BAR`) already used on the Profiles/Protocols/Events admin pages; gate the new endpoint(s) commander-only. |
| Provisioning timing | Automatically at API server startup only, inside `api/app.py`'s `build_context()`. No separate on-demand "re-provision" button in this first version. |

---

## 3. Data model

New, purely-additive types, mirroring the existing "declare, don't
construct" style already used by `profiles.spec.AgentSpec`, plus the
reserved-ID scheme:

```python
# profiles/simulation.py  (new)

from dataclasses import dataclass
from typing import Mapping

# Chosen so a simulation ID is always:
#   - well above Telegram's documented real-ID ceiling (2^52 ~= 4.5e15), and
#   - well below the browser's Number.MAX_SAFE_INTEGER (2^53-1 ~= 9.007e15),
# so it can never collide with a real Telegram ID and the admin page's own
# JS validation (which uses Number() arithmetic) stays exact.
SIMULATION_USER_ID_BASE = 9_000_000_000_000_000
SIMULATION_GROUP_ID_BASE = -9_000_000_000_000_000


def simulation_user_telegram_id(offset: int) -> str:
    """Deterministic reserved Telegram user ID for persona `offset`."""
    return str(SIMULATION_USER_ID_BASE + offset)


def simulation_group_chat_id(offset: int) -> str:
    """Deterministic reserved Telegram group chat ID for group `offset`."""
    return str(SIMULATION_GROUP_ID_BASE - offset)


@dataclass(frozen=True)
class SimulationPersona:
    key: str                       # placeholder used inside a scenario's sender_identity field
    offset: int                    # stable, never-reused ordinal within the reserved block
    permission_level: str = "viewer"
    full_name: str = ""


@dataclass(frozen=True)
class SimulationGroup:
    key: str                       # placeholder used inside a scenario's telegram_chat_id field
    offset: int
    agent_name: str = "main_agent"
    label: str = ""


@dataclass(frozen=True)
class SimulationScenario:
    key: str                       # unique key exposed via GET /Simulations
    title: str
    description: str = ""
    tags: tuple[str, ...] = ()
    raw: Mapping = None            # canonical {"scenario": ..., "chats": [...], "steps": [...]},
                                    # but sender_identity / telegram_chat_id hold *persona/group
                                    # keys* (strings), not concrete Telegram IDs
```

A profile then declares (all optional, defaulting to `()`, so every existing
profile is unaffected):

```python
SIMULATION_USERS = [
    SimulationPersona(key="commander_a", offset=0, permission_level="commander", full_name="Sim Commander"),
    SimulationPersona(key="viewer_a", offset=1, permission_level="viewer", full_name="Sim Viewer"),
]

SIMULATION_GROUPS = [
    SimulationGroup(key="response_team", offset=0, agent_name="team_status_agent", label="Simulated response team"),
]

SIMULATIONS = [
    SimulationScenario(key="basic_drone_dispatch", title="Basic drone dispatch", raw={...}),
]
```

`LoadedProfile` (`profiles/contracts.py`) gains three matching fields —
`simulation_users`, `simulation_groups`, `simulations` — each defaulting to
`()`, read the same `getattr(profile_module, "NAME", ())` way every other
optional attribute already is. `validate_profile()` gets new checks (fail
loud, collected together with every other failure):

- unique `key` and unique `offset` within each of the three lists,
- every persona/group key a scenario's `chats` / `steps` references must
  resolve to a declared `SimulationPersona` / `SimulationGroup` key,
- each `raw` must contain the three required top-level keys
  (`scenario`, `chats`, `steps`).

**Offsets are authoring-stable identifiers.** A profile must never renumber
or reuse one — doing so would silently turn a previously-provisioned
simulation user into a different persona on the next restart. This is a
documented authoring rule, not something validation alone can catch.

### Why one shared provisioning routine, not one shared table

Users and groups are genuinely different tables with different columns, so
true SQL-level unification does not fit the existing schema — the
codebase's own precedent (`register_telegram_user_if_missing` /
`register_telegram_group_if_missing`) is already "two parallel methods, same
idiom," not one merged method. The practical, architecture-respecting
version of "unified" is one shared Python-level routine applying the same
idempotent control flow to both entity kinds:

```python
# profiles/simulation_provisioning.py  (new)

from dataclasses import dataclass

from profiles.simulation import simulation_group_chat_id, simulation_user_telegram_id


@dataclass(frozen=True)
class ProvisioningResult:
    created_users: tuple[str, ...]
    created_groups: tuple[str, ...]


def ensure_simulation_entities(persistence, loaded_profile) -> ProvisioningResult:
    """Idempotent: creates any declared simulation user/group that doesn't exist yet,
    and never touches one that already does (so admin edits survive a restart)."""

    created_users = []
    for persona in loaded_profile.simulation_users:
        telegram_id = simulation_user_telegram_id(persona.offset)
        if persistence.ensure_user_exists(telegram_id, persona.permission_level, persona.full_name):
            created_users.append(telegram_id)

    created_groups = []
    for group in loaded_profile.simulation_groups:
        chat_id = simulation_group_chat_id(group.offset)
        if persistence.ensure_group_exists(chat_id, group.agent_name, group.label):
            created_groups.append(chat_id)

    return ProvisioningResult(created_users=tuple(created_users), created_groups=tuple(created_groups))
```

Two new, small, non-destructive persistence primitives back this
(`INSERT ... ON CONFLICT DO NOTHING`, always `auto_register = 0` so safe
mode never blocks a simulation entity — unlike the existing
`register_*_if_missing` methods, which hardcode `auto_register = 1`):

```python
# persistence/contracts.py  (new abstract methods)
def ensure_user_exists(self, telegram_identity: str, permission_level: str, full_name: str) -> bool:
    """Create the user with auto_register=0 if absent; return True iff it was created."""

def ensure_group_exists(self, chat_id: str, agent_name: str, label: str) -> bool:
    """Create the group with auto_register=0 if absent; return True iff it was created."""
```

**Hook point**: `api/app.py`'s `build_context()`, one new call right after
`persistence = open_persistence(loaded_profile.db_path)` and *before*
`group_routing = build_group_routing(persistence, registry)` — so the
routing table's own first `.load()` already picks up any newly-created
groups, with no extra reload call required.

### The group-ID-edit primitive

```python
# persistence/contracts.py + persistence/sqlite_store.py  (new)
def rename_group(self, old_chat_id: str, new_chat_id: str) -> dict:
    """Change a group's chat_id (its primary key) in place.
    Raises NotFoundError if old_chat_id doesn't exist, and a conflict error
    if new_chat_id is already taken by another group."""

# orchestrator/group_routing.py  (new)
class GroupRoutingTable:
    def rename(self, old_chat_id: str, new_chat_id: str) -> GroupBinding:
        """Write-through rename, mirroring the existing upsert()/remove() methods."""

# api/admin.py  (new route)
# POST /admin/groups/<chat_id>/rename
```

**Design note** (a stated assumption, not re-asked as a question): once an
operator renames a simulation group's placeholder to a real Telegram group
ID, that row simply becomes an ordinary group, decoupled from the
simulation system. The *next* server start's provisioning pass recreates a
fresh placeholder at the original reserved `chat_id` (since that key is
free again), so automated/repeatable simulation runs keep a deterministic
target, while the promoted row goes on serving real Telegram traffic
independently. IDs are always computed as a pure function of
`(base, offset)` — the JSON adapter never needs to consult the database to
do its job; only the admin UI's optional "already promoted?" hint would.

---

## 4. Server-side JSON adapter and API endpoints

```python
# api/simulations.py  (new — pure logic, no Flask, mirrors the separation
# already used by api/admin_scenarios.py)

def simulation_catalog_payload(loaded_profile) -> list[dict]:
    """One {key, title, description, tags} entry per declared SimulationScenario
    — metadata only, used to populate a picker."""

def materialize_simulation(scenario, simulation_users, simulation_groups) -> dict:
    """Walks scenario.raw's chats[]/steps[], replacing each persona/group *key*
    with its deterministic simulation_user_telegram_id()/simulation_group_chat_id().
    Same {scenario, chats, steps} shape in and out — only the IDs are swapped."""
```

```python
# api/routes.py  (new build_simulations_blueprint(ctx), registered in
# api/app.py's build_app(), alongside the other build_*_blueprint calls)

GET /Simulations          # commander-only; simulation_catalog_payload(ctx.loaded_profile)
GET /Simulations/<key>    # commander-only; materialize_simulation(...) for one declared scenario
```

Both are cheap and in-memory-only — no persistence read is needed, since the
profile's own declarations are already loaded into `ApiContext.loaded_profile`.

**Admin page integration** (`api/admin_simulator.py` + `api/admin.py`,
additive only): add the existing `IDENTITY_BAR` component (already generic,
imported from `api/admin_api_pages.py`) to the simulator page, plus one new
"Profile simulations" dropdown next to the existing "Examples" dropdown.
Selecting an entry calls `GET /Simulations/<key>` with
`X-Identity: {api_identity}` and feeds the response **directly** into the
page's existing `loadScenario()` JS function — no mapping panel, because
there is nothing left to map. The manual paste/drop/bundled-example paths
are untouched.

---

## 5. Execution path (confirmed unchanged)

Both manual and profile-driven scenarios end up as the identical canonical
JSON, loaded into the page's identical `state` object, sent by the
identical `sendNext()` / `buildRequest()` JS to the identical
`POST /Msg` / `POST /Event`, with `X-Identity` set to each step's
`sender_identity` — now simply one of the profile's reserved simulation IDs
instead of an admin-typed one. This is precisely "goes through the bot" as
this codebase already defines it: the exact HTTP calls `bot/transports.py`
makes for a real Telegram message, hitting the same `/Msg` / `/Event`
ingestion Flask routes, with no internal shortcut and no new bypass.

---

## 6. File impact

### New files

- `profiles/simulation.py` — `SimulationPersona`, `SimulationGroup`,
  `SimulationScenario`, the reserved-ID constants, and the ID helper
  functions.
- `profiles/simulation_provisioning.py` — `ensure_simulation_entities()`
  and `ProvisioningResult`.
- `api/simulations.py` — `simulation_catalog_payload()` and
  `materialize_simulation()`.

### Modified files

- `profiles/contracts.py` — three new optional `LoadedProfile` fields
  (`simulation_users`, `simulation_groups`, `simulations`, each defaulting
  to `()`).
- `profiles/loader.py` — read and validate the three new optional profile
  attributes.
- `persistence/contracts.py` / `persistence/sqlite_store.py` — new
  `ensure_user_exists`, `ensure_group_exists`, `rename_group`.
- `orchestrator/group_routing.py` — new `GroupRoutingTable.rename()`.
- `api/app.py` — one new call to `ensure_simulation_entities()` inside
  `build_context()`.
- `api/routes.py` — new `build_simulations_blueprint()`, registered in
  `build_app()`.
- `api/admin.py` — new `/admin/groups/<chat_id>/rename` route; the existing
  `IDENTITY_BAR` added to the simulator page render.
- `api/admin_simulator.py` — new "Profile simulations" dropdown plus the
  fetch/`loadScenario()` wiring for it (additive JS/HTML only).
- `messages/en.py` / `messages/he.py` — new `admin.simulator.*` and
  group-rename catalog keys (bilingual parity is required by
  `messages.catalog.validate_catalogs`).
- `profiles/unified_test.py` (or another pilot profile) — first real
  `SIMULATION_USERS` / `SIMULATION_GROUPS` / `SIMULATIONS` declaration, as a
  worked example.
- Tests: `tests/test_profile_loading.py`, the persistence conformance
  tests / `tests/test_sqlite_store.py`, `tests/test_api_admin.py`,
  `tests/test_admin_scenarios.py`, `tests/test_api_groups.py`,
  `tests/test_group_routing.py` — new cases added, no restructuring of
  existing ones.

### Explicitly untouched by this design

- `api/admin_scenarios.py`'s legacy bundled-fixture flow.
- The simulator's manual paste/drop path and its JSON contract.
- The entire `/Msg` / `/Event` ingestion stack.
- The admin frontend's existing JSON schema/contract.

---

## 7. Risks / edge cases to verify during implementation

- **Safe mode interaction with `/Msg`.** `api/app.py`'s `before_request`
  calls `enforce_safe_mode_for_bot_request` on every request. Confirm this
  does not also gate a plain `X-Identity`-only call to `/Msg` / `/Event`
  (as the existing simulator already makes) beyond what safe mode already
  tolerates today. Simulation users are pre-approved (`auto_register = 0`)
  either way, but this interaction should be exercised directly, not
  assumed.
- **Offset stability.** This is an authoring discipline, not something
  validation alone can fully enforce beyond uniqueness — it should be
  documented clearly wherever profiles are authored (e.g. alongside
  `docs/profile_spec.md`).
- **Reserved-ID magnitude.** The chosen bases (±9 × 10¹⁵) sit safely above
  Telegram's documented 52-bit real-ID ceiling and below the
  `Number.MAX_SAFE_INTEGER` boundary the existing browser-side validation
  relies on — verified against both constraints, not just one.
- **Group rename races.** `rename_group` should fail cleanly (an existing
  `NotFoundError` / conflict-style error) if the target `chat_id` is
  already taken. `GroupRoutingTable`'s TTL-cached copy in `bot/app.py`
  picks up a rename within its existing 60-second refresh window, same as
  any other out-of-band group change today.
- **Startup-only provisioning.** A profile's simulation declarations added
  after a server is already running only take effect on the next restart —
  consistent with how every other profile-level constant already behaves,
  but worth calling out explicitly in operator-facing docs.

---

## 8. Implementation notes (added post-implementation)

This design has been implemented in full, section by section, with the full
test suite green (1408 tests). A few details the plan left open, plus two
real bugs the implementation caught, are recorded here so this document
stays accurate:

- **`RequestedOperation.VIEW_SIMULATIONS`** was added to `auth/permissions.py`
  (commander-only — not added to `ViewerAllowedAction`) as the permission
  gate for `GET /Simulations`/`GET /Simulations/<key>`, matching the existing
  one-dedicated-operation-per-endpoint-group convention (`LIST_GROUPS`,
  `MANAGE_GROUPS`, …). Recorded in `docs/allowed_calls.md`'s operation matrix.
- **`SimulationScenario.raw`** is a required field (no default), placed right
  after `title` — a scenario without a JSON template is meaningless, and
  Python dataclasses require non-default fields before default ones anyway.
- **Package-boundary rule** (`tests/test_architecture.py`): the `profiles`
  package only exposes `profiles` and `profiles.loader` as cross-package
  entry points. `profiles/simulation.py` and
  `profiles/simulation_provisioning.py` are internal submodules — their
  public names (`SimulationPersona`, `SimulationGroup`, `SimulationScenario`,
  `simulation_user_telegram_id`, `simulation_group_chat_id`,
  `ensure_simulation_entities`, `ProvisioningResult`) are re-exported through
  `profiles/__init__.py`, the same way `AgentSpec`/`OptimizationPolicy`
  already are. `api/app.py` and `api/simulations.py` import from `profiles`
  (the package), not the submodules directly.
- **Bug caught by the test suite**: `MessageCatalog.text(self, key, **values)`
  names its own first positional parameter `key` — calling it as
  `messages.text("api.simulation_not_found", key=key)` collided with that.
  Fixed by naming the catalog placeholder `{simulation_key}` instead.
- **Bug caught by the test suite**: `_validate_simulation_declarations` first
  accessed `loaded.simulation_users`/etc. directly, which broke every
  existing `validate_profile()` test using a `SimpleNamespace` stand-in that
  predates this field. Fixed to read via `getattr(loaded, "...", ())`, like
  every other optional `LoadedProfile` field `validate_profile` already
  checks (e.g. `event_type_required_fields`).
- **`tests/api_fakes.py`**'s shared `_FakeLoadedProfile`/`build_context` test
  double gained optional `simulation_users`/`simulation_groups`/`simulations`
  parameters (defaulting to empty, so every existing caller is unaffected) —
  needed so `GET /Simulations` could be tested through the same established
  fixture every other API blueprint test uses.
- Test files: `tests/test_profile_simulations.py` (ID scheme, profile
  validation, provisioning, JSON materialization) and
  `tests/test_api_simulations.py` (the HTTP endpoints) are new, dedicated
  files, matching this codebase's "one file per feature" convention (e.g.
  `tests/test_group_routing.py`); the group-rename primitive and the
  Groups-page rename action gained cases in the existing
  `tests/test_persistence_conformance.py`, `tests/test_group_routing.py`,
  and `tests/test_api_admin.py` instead, since they extend existing
  mechanisms rather than introduce a new one.
- The pilot profile (`profiles/unified_test.py`) declares two simulation
  personas (`commander`, `viewer`), one simulation group (`response_team`,
  bound to `team_status_agent`), and one worked-example simulation
  (`overall_picture_query`) — a viewer privately asking for the overall
  situational picture, a `LOW`-criticality, `approval_flag=False` protocol
  that completes immediately with no commander interaction needed. Its
  display text lives in `messages/en.py`/`messages/he.py` under
  `unified.simulation.*`, per this file's own enforced "no Hebrew literal
  outside the message catalog" rule (`tests/test_hebrew_leakage.py`).

## 9. Bundled-fixture migration (SEC_001 and FIRE_002, both done)

Beyond the pilot scenario, the six legacy bundled fixtures under `fixtures/admin_scenarios/`
(reachable only through the admin panel's "Bundled examples" dropdown + manual ID-mapping panel)
are being migrated into real `profiles.unified_test` `SIMULATIONS` declarations, so they become
available through `GET /Simulations` with reserved IDs already injected — no manual mapping.

**SEC_001 (the readiness-team series, 3 phases) is migrated and done**, as a checkpoint before
doing the same for FIRE_002:

- 15 new `SimulationPersona` entries (offsets 2-16) and 2 new `SimulationGroup` entries
  (`cameras`, `external_forces`, offsets 1-2 — `response_team` at offset 0 is reused from the
  pilot scenario's own declaration, not re-declared).
- 3 new `SimulationScenario` entries (`sec001_phase1`/`phase2`/`phase3`), transcribed verbatim
  from the fixture JSON (Hebrew text copied programmatically into `messages/he.py`, never
  retyped by hand, to guarantee fidelity) with full literal English translations added to
  `messages/en.py`.
- Recurring characters across the 3 phases (the on-call technician, the site security officer)
  share one `SimulationPersona`/offset/reserved ID across every phase they appear in, rather
  than being re-declared per phase — verified in `tests/test_profile_simulations.py`.
- Fully additive, per the actual decision (an earlier write-up of this section briefly said
  the SEC_001 files were removed from `api/admin_scenarios.SCENARIO_FILES` — that was wrong,
  traced to a mismatch between what the `AskUserQuestion` tool reported back as the selection
  and what was actually chosen, caught and corrected). All six bundled fixtures, SEC_001
  included, keep working through the legacy "Bundled examples" dropdown exactly as before;
  the profile-declared versions are a second, additional way to reach the same three phases.

**FIRE_002 (the firefighting series, 3 phases) is migrated too, additively, exactly like
SEC_001**: 10 new `SimulationPersona` entries (offsets 17-26) and 3 new `SimulationGroup`
entries (`fire_response_team`/`fire_cameras`/`fire_external_forces`, offsets 3-5 — its own
independent group set, not reusing SEC_001's `response_team`/`cameras`/`external_forces`,
per the "independent roster per series" decision), 3 new `SimulationScenario` entries
(`fire002_phase1`/`phase2`/`phase3`). One source-data nuance handled: "מוקד משטרה - אגמ"
(phase 1) and "מוקד משטרה - אג\"מ" (phases 2-3) are the same police-operations-hub character
with a spelling variant in the raw fixture — both Hebrew strings map to the one
`police_hub_agam` persona. Both bundled-fixture series remain fully reachable through the
legacy "Bundled examples" dropdown, unchanged, alongside the profile-driven versions.

## 10. Open follow-ups (not blocking this plan)

- Decide, when implementation starts, which profile becomes the first real
  pilot for `SIMULATION_USERS` / `SIMULATION_GROUPS` / `SIMULATIONS`
  (`profiles/unified_test.py` is the natural candidate).
- Decide whether a later iteration should add an on-demand "re-provision"
  admin action (mirroring `/admin/bot-service/provision`) for picking up
  new simulation declarations without a full restart — deliberately left
  out of this first version per the scoping decision in §2.
