# Admin panel: generic editable tables for Drones, Standby-squad attendance, Friendly forces

Status: planning only, no code written. Builds on two prior research passes (`api/admin.py`,
`api/admin_api_pages.py`, `profiles/response_team.py`, `profiles/firefighting.py`,
`agents/surveillance_agent.py`, `agents/team_status_agent.py`, `agents/friendly_forces_agent.py`,
`persistence/surveillance_store.py`, `persistence/team_status_store.py`,
`persistence/response_team_store.py`, `orchestrator/holds.py`, `profiles/contracts.py`,
`profiles/loader.py`) plus a third pass specifically on drone-recall sharing and
neighboring-forces reusability, prompted by your three decisions below.

## Your three decisions, and what each actually requires

1. **Drones: every field freely editable, including `status`, with no action-button gate.**
   Whatever cascade is needed (aborting a linked active mission) happens transparently inside
   the same write, not behind a separate UI control. Applied as a general principle to the
   other two tables too, for consistency — no table gets special button-only handling.
2. **`firefighting` friendly forces get the same real, persisted mechanism `response_team` has**
   (dispatch log + computed remaining capacity), not just an admin page bolted onto the current
   in-memory lists.
3. **`firefighting` uses the same drone code as `response_team`.**

Decision 3 turns out to be small — the recall logic is *already* shared. Decision 2 turns out
to be the real architectural work in this plan; it requires extracting a shared, parameterized
agent base class, not just adding a table. Both are detailed below with exact citations.

---

## 0. The "no restart, takes effect immediately" guarantee — unchanged, still holds

Every store involved (`persistence/surveillance_store.py`, `persistence/team_status_store.py`,
`persistence/response_team_store.py`) opens a fresh `sqlite3.connect()` per operation, no
in-memory caching anywhere in the agent layer. Any admin write through these stores (or a new
method added beside them) is guaranteed visible to the very next tool call, on either profile,
with zero extra invalidation step. This still holds after the changes below — decision 2 adds a
store *for* firefighting (reusing the existing generic class), it doesn't add any caching.

---

## 1. Drones — decision 3 is nearly free, because the sharing is already halfway done

**Corrected finding**: `return_drone_to_base` (recall) is **already on the shared base class**,
not response-team-specific. `agents/surveillance_agent.py:289-297`'s `SurveillanceAgent` already
declares `@tool("return_drone_to_base", ...)` calling `self._recall(...)` (also on the base,
line 124). `profiles/response_team.py:340-348`'s `recall_drone` is just a same-behavior alias:
`return self.return_drone_to_base(...)`.

**What's actually different between the two profiles today**: which *store* each one's
`__init__` opens.
- `SurveillanceAgent.__init__` (base, used unmodified by `FirefightingSurveillanceAgent` —
  `profiles/firefighting.py:45-49` overrides only `surveillance_db_path`) opens
  `open_surveillance_persistence(self.surveillance_db_path)`
  (`agents/surveillance_agent.py:111-114`) — whose recall SQL hardcodes the literal
  `'central_hub'` as the home area (`persistence/surveillance_store.py:301,349,388`).
- `ResponseTeamSurveillanceAgent.__init__` (`profiles/response_team.py`) instead opens
  `open_response_team_surveillance_store(db_path, eta_fn=eta_seconds, home_area=DRONES_WAREHOUSE)`
  — a **separate, already-generic** class, `ResponseTeamSurveillanceStore`
  (`persistence/response_team_store.py:465-483`), explicitly documented in its own docstring as
  "same contract... with... a pluggable `eta_fn`/`home_area`." It is already reusable as-is,
  by any profile, with that profile's own home-area value.

**So decision 3 is exactly**: give `FirefightingSurveillanceAgent` its own `__init__` override,
identical in shape to `ResponseTeamSurveillanceAgent`'s, opening the same
`open_response_team_surveillance_store` function with `firefighting`'s own DB path and its own
home-area constant (a new `FIREFIGHTING_DRONE_HOME` or similar — the actual area name is an
open question below, not invented here). Once that's done, `return_drone_to_base`/recall works
identically for both profiles with zero changes to `agents/surveillance_agent.py` itself.

**Recommended small cleanup, not required for correctness**: `open_response_team_surveillance_store`
now serves two profiles from a module literally named `response_team_store.py`, which is
misleading. Renaming/moving it (e.g. into `persistence/surveillance_store.py` itself, alongside
the base opener, as `open_configurable_surveillance_store`) would remove that naming
smell. Similarly, `profiles/response_team.py`'s `recall_drone` alias becomes redundant once
firefighting can just call `return_drone_to_base` directly — worth removing for consistency,
or leaving for backward compatibility with anything already calling it by that name.

**Decision 1's consequence for the admin design**: since `status` is now plain-editable (not
gated behind an action), the write path itself must handle the cascade. Design:

```python
# on ResponseTeamSurveillanceStore (already shared -- one implementation, both profiles)
def admin_update_drone(self, drone_id: str, **fields) -> None:
    with self._connect() as conn:
        current = conn.execute("SELECT * FROM drones WHERE drone_id = ?", (drone_id,)).fetchone()
        if current is None:
            raise PersistenceError(f"drone {drone_id!r} not found")
        new_status = fields.get("status", current["status"])
        leaving_active_mission = (
            current["assigned_mission_id"] is not None
            and new_status != "in_flight"
            and fields.get("assigned_mission_id", current["assigned_mission_id"]) != current["assigned_mission_id"] or new_status != "in_flight"
        )
        if leaving_active_mission and current["assigned_mission_id"] is not None:
            conn.execute(
                "UPDATE drone_missions SET status = 'aborted', "
                "notes = 'Admin edit: status changed away from in_flight.', updated_at = ? "
                "WHERE mission_id = ? AND status IN ('dispatched','en_route','on_station')",
                (_now(), current["assigned_mission_id"]),
            )
            fields.setdefault("assigned_mission_id", None)
        if fields.get("status") == "in_flight" and fields.get("assigned_mission_id"):
            mission = conn.execute(
                "SELECT 1 FROM drone_missions WHERE mission_id = ?", (fields["assigned_mission_id"],)
            ).fetchone()
            if mission is None:
                raise PersistenceError("assigned_mission_id does not reference an existing mission")
        # ... apply every submitted field to the drones row in one UPDATE, same statement shape
        # dispatch_drone/recall_drone already use elsewhere in this file.
```

This is one new method on the *already-shared* store class — written once, used identically by
both profiles' `ADMIN_TABLES` declarations. Every column (`callsign`, `model`, `status`,
`battery_percent`, `current_area`, `assigned_mission_id`) is a normal editable
`AdminColumn`; only `drone_id` (primary key) and `last_updated` (stamped by the write itself)
stay `editable=False` — the same structural rule already applied to PKs/timestamps on the other
two tables, not a drone-specific exception.

---

## 2. Standby-squad attendance — unchanged from the previous plan version, simplified

Same shared `persistence/team_status_store.py` tables on both profiles (confirmed in the
previous pass). Per decision 1's "no special button-only handling" applied generally: the
existing dormant `review_late_response(response_id, decision, reviewed_by)` method
(`persistence/team_status_store.py:253-279`) becomes the *body* of a plain field write when
`approval_status` is one of the submitted fields — not a separate action button. A new
`admin_update_attendance_fields(response_id, **fields)` on the same shared store class:
if `approval_status` is among the submitted fields, call `review_late_response`'s exact
`UPDATE` (stamping `reviewed_by`/`reviewed_at` automatically, same as it already does); for
`reason`/`unavailable_until`, a plain `UPDATE`. One method, one `write_fn`, every field
editable, cascade (the `reviewed_by`/`reviewed_at` stamp) happens transparently. Effect on
`report_team_availability` is immediate (its `availability_snapshot` query already filters
`WHERE approval_status = 'accepted'` fresh each call).

This table is already usable by `firefighting` unchanged — it uses the same shared store today
(`FirefightingCrewStatusAgent(TeamStatusAgent)`), so its `ADMIN_TABLES` entry is the same
builder call as `response_team`'s, just pointed at firefighting's own DB path. No new work
needed here beyond declaring it.

---

## 3. Friendly forces — decision 2 is the real work in this plan

**Corrected finding, more precise than the previous pass**: `NeighboringForceStore`
(`persistence/response_team_store.py:889`, opened via `open_neighboring_force_store(db_path)`,
lines 959-960) is **already fully generic** — its constructor takes only a `db_path`. No
force-kind, home-area, or pool-size is baked into it anywhere.

Everything response-team-*specific* lives one layer up, in
`profiles/response_team.py`'s own `NeighboringForcesAgent` class — which its own docstring
already describes as "wholly new, profile-only... not the shared `agents/friendly_forces_agent.py`
`FriendlyForcesAgent`." That's the actual gap: `agents/friendly_forces_agent.py`'s shared
`FriendlyForcesAgent` (which `firefighting.py`'s `FirefightingExternalForcesAgent` currently
subclasses) is a **different, older, in-memory family** — confirmed by direct quote, its
inherited `dispatch_police`/`dispatch_ambulance` are just `self.dispatches_recorded.append(...)`,
plain strings, same as firefighting's own `dispatch_water_tankers`/`dispatch_aircraft`. There is
nothing to reuse from that base class — it's the wrong base entirely for what decision 2 asks
for.

**What decision 2 actually requires**: extract `response_team.py`'s `NeighboringForcesAgent`
into a new shared base class, `agents/neighboring_forces_agent.py`, parameterized the same way
`SurveillanceAgent`/`TeamStatusAgent` already are — constructor takes `db_path`,
`force_bases: dict[str, str]` (kind → home area), `pool_size: int`, `busy_seconds: int` — using
the already-generic `NeighboringForceStore` underneath, and exposing the same
`dispatch_neighboring_force(kind, area, unit_count)` tool and the same busy-window remaining-
capacity calculation (`_force_remaining_capacity`, currently `profiles/response_team.py:513-524`)
generically.

**Both open questions from the previous version are now resolved by you — decisions below,
not left as options.**

### 3.1 Home areas — derived from each profile's own simulation text, one per kind, cited

Per your instruction, these are chosen by reading each profile's actual simulation scenario
steps and using the area that appears where each force kind is actually mentioned — not a
uniform default. Evidence quality varies by kind and is stated honestly below rather than
smoothed over.

#### `response_team` (SEC_001) — two values corrected, per your decision

Checked already-shipped `FORCE_BASES` (`profiles/response_team.py:167-172`) against the actual
simulation text. Two of the four didn't match; you've confirmed both should be corrected to
match the simulation, as part of this plan's scope (a small, isolated fix, not deferred):

| Kind | Shipped today | Simulation text says | Action |
|---|---|---|---|
| `ambulance` | `expansion_neighborhood` | Matches — `sec001_phase3` step 3, `mda_dispatch`: *"קיבלנו דיווח על פצוע ירי בכניסה לשכונת ההרחבה! אמבולנס בדרך..."* | No change. |
| `k9` | `east_orchards` | Matches — `sec001_phase2` step 6, `patrol_unit_40`: *"מזהים את המסחרית הלבנה נטושה במטע הזיתים המזרחי... מקפיצים יחידת כלבנים."* | No change. |
| `police` | `east_gate` | Didn't match — both police-sourced messages say `east_orchards`: `sec001_phase2` step 2, `police_duty_officer`: *"...על רכב מסחרי לבן... באזור המטעים המזרחיים שלכם"*; `sec001_phase3` step 5, `police_patrol`: *"הירי שדווח הוא ירי אזהרה של הניידת שלנו באזור המטעים המזרחיים."* | **Fix: `east_gate` → `east_orchards`.** |
| `yasam` | `east_gate` | Didn't match — `yasam_commander`'s own messages (steps 8-9) never repeat an area name; `east_gate` traced back to a *different* persona's initial report, not yasam's own text. The place yasam actually converges on, per the chase narrative (steps 6-7), is `old_public_building`. | **Fix: `east_gate` → `old_public_building`.** |

```python
# profiles/response_team.py:167-172 -- the only lines this fix touches
FORCE_BASES = {
    "ambulance": "expansion_neighborhood",
    "police": "east_orchards",        # was "east_gate"
    "k9": "east_orchards",
    "yasam": "old_public_building",   # was "east_gate"
}
```

**Test impact, checked**: `tests/test_response_team_resource_unavailable.py` exercises police
capacity/exhaustion logic with its own hardcoded `target_area="east_gate"`/`origin_area="east_gate"`
values, but those are the *dispatch target* and hand-built fixture rows for capacity math, not
values read from `FORCE_BASES` — the capacity calculation only keys on `force_kind`, never on
the area. **No test changes needed** for this fix; confirmed by reading the file, not assumed.

#### `firefighting` (FIRE_002) — new, chosen from its own three simulation scenarios

| Kind | Chosen area | Evidence |
|---|---|---|
| `police` | `ornim_street` | **Explicit, twice.** `fire002_phase3` step 2, sender `police_hub_agam`: *"מתחילים פינוי מיידי של קו הבתים הראשון ברחוב אורנים עקב עשן רעיל..."* ("beginning immediate evacuation of the first row of houses on Ornim Street due to toxic smoke..."); step 5, sender `fire_police_patrol`: *"בדיקה ברחוב אורנים 14: הבית ריק!"* ("checked Ornim Street 14: the house is empty!"). Both explicit, both `ornim_street`. |
| `water_tankers` | `chemical_plant` | **Explicit but shared with aircraft (see below).** `fire002_phase3` step 7, sender `district_fire_commander`: *"נשלח אליכם סיוע: 4 רכבי אלון (מיכליות מים)... ו-2 מטוסי כיבוי."* ("sending you aid: 4 tanker trucks... and 2 firefighting aircraft.") — this response is to the fire reaching the chemical plant (step 1: *"...הגיעו לגדר של מפעל 'כימי-קל'"*). |
| `aircraft` | `pine_ridge` | **Not textually distinct from water_tankers — reasoned choice, not a direct citation.** Both are requested in the exact same sentence, for the same incident. To honor "each kind gets its own area" honestly rather than assign the same one twice, `pine_ridge` is used instead — it's where this same fire was *first detected from height* (`fire002_phase1` step 3: *"מגדל תצפית אורנים"*, the Ornim lookout tower; `fire002_phase2` step 1: *"רכס אורנים"*, the Ornim ridge — note: this is the *ridge/lookout*, textually distinct from `ornim_street`, the residential street used for `police` above, even though both derive from the same Hebrew root). Aerial units staging near the elevated observation point is a reasonable real-world inference, but flagged plainly as inference, not a quoted dispatch location the way `police`/`water_tankers` are. |
| `ambulance` | *(no citation exists)* | **No ambulance/EMS persona or message appears anywhere in FIRE_002's three scenarios at all** — confirmed by listing every sender identity across all three phases. The closest thematically-related content is the trapped-persons report on `ornim_street` (`fire002_phase3` steps 3-5), which turns out to be a false report needing no medical response. There is no honest textual basis to pick a distinct area for this kind. Recommended: also `ornim_street` (closest thematic proximity, same as `police`), stated explicitly as *not* simulation-evidenced, pending either a real answer from you or a future scenario update that actually mentions an ambulance dispatch. |

```python
# profiles/firefighting.py -- proposed, not yet written
FORCE_BASES = {
    "police": "ornim_street",
    "water_tankers": "chemical_plant",
    "aircraft": "pine_ridge",
    "ambulance": "ornim_street",   # placeholder -- no simulation text supports any choice here
}
FORCE_POOL_SIZE = 2   # mirrors response_team.py's own default; no firefighting-specific evidence either way
```

### 3.2 Firefighting uses response_team's exact single-tool shape — the 4 named tools are replaced, not kept alongside

Per your decision, `firefighting` gets the *same* `dispatch_neighboring_force(kind, target_area,
unit_count=1, note="")` tool response_team has, with `kind` selecting among
`police|ambulance|water_tankers|aircraft` — not 4 separate tool methods each internally calling
into the shared store. This **replaces** `dispatch_police`, `dispatch_ambulance`,
`dispatch_water_tankers`, `dispatch_aircraft` entirely (removing the two firefighting-specific
ones and no longer inheriting the two from the old in-memory base). This is a real,
compatibility-breaking API-surface change, not additive — it requires:
- Updating every `Protocol` in `profiles/firefighting.py` whose `approved_tools` currently names
  one of the 4 old tools, to name `dispatch_neighboring_force` instead.
- Updating every existing test that calls `dispatch_police`/`dispatch_ambulance`/
  `dispatch_water_tankers`/`dispatch_aircraft` by name.
- Confirming no simulation script (`FIRE_002` series) scripts a message expecting one of the
  old tool names to appear verbatim anywhere user-visible (tool names aren't normally
  user-visible, so this is a low-risk check, not a likely blocker).

### 3.3 Keeping response_team's own squad special-case out of the shared base class

`response_team.py`'s current `NeighboringForcesAgent` also handles `kind="squad"` — not a real
external force, response_team's *own* roster, routed through the same tool as a special case
(`SQUAD_KIND`/`SQUAD_ORIGIN_AREA`, checked against the roster store's live availability instead
of `FORCE_POOL_SIZE`). `firefighting` has no equivalent concept. To keep the extracted
`agents/neighboring_forces_agent.py` base genuinely generic (not response_team's squad logic
wearing a shared-class costume), the base class should expose a small override hook — e.g. a
protected `_resolve_kind(self, kind) -> ForceOrigin | None` returning the home area and the
capacity-check function to use for that kind — with a default implementation driven purely by
`FORCE_BASES`/`FORCE_POOL_SIZE`. `response_team.py`'s subclass overrides `_resolve_kind` to add
the squad branch on top; `firefighting.py`'s subclass uses the base default as-is, no override
needed.

Then:
- `profiles/response_team.py`'s `NeighboringForcesAgent` becomes a thin subclass: its own
  `FORCE_BASES`/`FORCE_POOL_SIZE`/`FORCE_BUSY_SECONDS`, plus the `_resolve_kind` override for
  `"squad"` — net *shrinks* from what it is today.
- `profiles/firefighting.py` gets a new subclass of the same shared base with the constants
  from 3.1 and no override at all — `FirefightingExternalForcesAgent` stops subclassing the old
  in-memory `agents/friendly_forces_agent.py::FriendlyForcesAgent` entirely.
- The admin `admin_update_dispatch(request_id, **fields)` method goes on the already-generic
  `NeighboringForceStore` itself (not per-profile) — one implementation, both profiles, exactly
  like drones' `admin_update_drone` in section 1.

This is materially bigger than "add a table" — it's a real extraction-and-share refactor,
sized honestly in section 5.

---

## 4. Generic admin-panel design (simplified — no action-button primitive needed)

```python
@dataclass(frozen=True)
class AdminColumn:
    name: str
    label: str
    kind: Literal["text", "number", "select", "checkbox", "readonly", "datetime"] = "text"
    choices: tuple[str, ...] = ()   # mirrors the store's own CHECK constraint
    editable: bool = True           # False only for PKs and system-stamped timestamps
    required: bool = False

@dataclass(frozen=True)
class AdminTable:
    key: str
    label: str
    primary_key: str
    columns: tuple[AdminColumn, ...]
    list_fn: Callable[[FlowDeps], list[dict]]
    get_fn: Callable[[FlowDeps, str], dict | None]
    write_fn: Callable[[FlowDeps, dict], None]   # every field; cascades happen inside it
    delete_fn: Callable[[FlowDeps, str], None] | None = None
    allow_create: bool = False
```

No `AdminAction`/action route — dropped per decision 1's general principle. Every table is
rendered and edited the same way: `GET /admin/tables/<key>` lists, `GET/POST
/admin/tables/<key>/edit/<row_id>` edits every editable column in one form, `write_fn` does
whatever cascade its own store method needs internally. `LoadedProfile.admin_tables` +
`profiles/loader.py`'s `getattr(...)` wiring, and the dashboard nav loop, are unchanged from the
previous plan version.

Because all three admin-update methods (`admin_update_drone`, `admin_update_attendance_fields`,
`admin_update_dispatch`) live on stores that are **already shared** between the two profiles,
each profile's `ADMIN_TABLES` declaration is just: open my own store instance, wrap its
list/get/write in three one-line functions, done. No per-profile duplication of validation
logic anywhere.

---

## 5. Files touched, with size estimates (revised)

| File | Change | Est. lines |
|---|---|---|
| `profiles/admin_tables.py` | **new** — `AdminColumn`, `AdminTable` | 35-50 |
| `profiles/contracts.py` | `LoadedProfile.admin_tables` field | 2-3 |
| `profiles/loader.py` | one `getattr(...)` line | 2-3 |
| `api/admin_tables.py` | **new** — generic blueprint (list/edit/(new)/(delete) only, no action route), 2 Jinja templates | 220-300 |
| `api/admin.py` | dashboard nav loop; blueprint registration | 15-25 |
| `persistence/response_team_store.py` (or renamed/relocated per section 1's cleanup) | `admin_update_drone` (with the transparent mission-cascade) on `ResponseTeamSurveillanceStore`; `admin_update_dispatch` on `NeighboringForceStore` | 90-140 |
| `persistence/team_status_store.py` | `admin_update_attendance_fields` (wraps `review_late_response` for `approval_status`) | 25-35 |
| `agents/neighboring_forces_agent.py` | **new** — shared, parameterized base extracted from `response_team.py`'s current class | 90-140 |
| `profiles/response_team.py` | `FORCE_BASES` fix (`police`→`east_orchards`, `yasam`→`old_public_building`, section 3.1) — 2 lines, independent of everything else in this plan; `NeighboringForcesAgent` shrinks to a thin subclass + `_resolve_kind` override for `"squad"`; `+__init__` override unaffected (already correct); `ADMIN_TABLES` — 3 entries | net +30-60 |
| `profiles/firefighting.py` | `FirefightingSurveillanceAgent.__init__` override (drone home-area); remove `dispatch_police`/`dispatch_ambulance`/`dispatch_water_tankers`/`dispatch_aircraft`, replace with `FORCE_BASES`/`FORCE_POOL_SIZE` (section 3.1) + one new subclass of the shared base (no override needed); update every `Protocol.approved_tools` referencing an old tool name; `ADMIN_TABLES` — 3 entries | 90-150 |
| `tests/test_firefighting_external_forces_agent.py` (existing file, per `docs/file_catalog.md`) | update every test calling the 4 removed tool names to call `dispatch_neighboring_force(kind=...)` instead | 40-80 (diff, not net new) |
| `docs/file_catalog.md` | register 2 new files | 2 |
| `tests/test_admin_tables.py` | **new** — generic mechanism against a fake `AdminTable` | 110-160 |
| `tests/test_response_team_admin_tables.py` | **new** — 3 real wirings, incl. a status-edit-while-active-mission test and an approval-status-edit test | 150-230 |
| `tests/test_firefighting_admin_tables.py` | **new** — same 3, against firefighting's own DBs, proving the shared base class actually works for a second profile | 120-190 |
| `tests/test_neighboring_forces_agent.py` | **new** — the extracted shared base class's own unit tests, independent of either profile (mirrors how `SurveillanceAgent`/`TeamStatusAgent` are tested at the base-class level already, if they are — worth confirming that convention exists before assuming it) | 80-130 |

Total: roughly **1100-1700 lines**. The increase from the previous version is almost entirely
the neighboring-forces extraction and the firefighting tool-unification (decisions 2's
follow-ups) — the drone work (decision 3) turned out smaller than expected once the existing
sharing was found.

---

## 6. Phases (each independently testable, in dependency order)

**Phase 0 — `response_team`'s `FORCE_BASES` fix (section 3.1).** Two-line constant change,
independent of everything else in this plan — can land before, during, or separately from the
admin-panel work. **Testable**: run the existing `dispatch_neighboring_force` test suite
unchanged (confirmed above it doesn't hardcode the old values) plus one new assertion that a
real (non-fixture) police/yasam dispatch now records `origin_area="east_orchards"`/
`"old_public_building"` respectively.

**Phase 1 — generic mechanism.** Same as before, no real table wired. Tested against a fake
`AdminTable`.

**Phase 2 — Drones, `response_team`.** `admin_update_drone` (with the transparent cascade) on
the already-shared store. **Testable**: edit every field including `status` while a mission is
active, confirm the mission row is aborted and `get_drone_fleet_status` reflects the new state
immediately; confirm setting `status="in_flight"` with a bogus `assigned_mission_id` is
rejected.

**Phase 3 — Drone code sharing, `firefighting`.** The `__init__` override + home-area constant
(decision 3). **Testable**: dispatch a firefighting drone via the real tool, then recall it via
`return_drone_to_base` directly (proving the shared base class now works end-to-end for this
profile) — this alone is valuable independent of the admin panel, since firefighting currently
has no way to recall a drone at all, by any means.

**Phase 4 — Drones admin page, `firefighting`.** Reuses phase 2's store method against
firefighting's own DB, once phase 3 gives it a correctly-configured store. **Testable**: same
assertions as phase 2, against firefighting's DB.

**Phase 5 — Standby-squad attendance, both profiles.** `admin_update_attendance_fields`;
`ADMIN_TABLES` entries for both profiles (this table needed no extraction work, so both land
together). **Testable**: edit reason/unavailable_until; separately, flip `approval_status` and
confirm `report_team_availability`'s snapshot reflects it immediately, on each profile's own DB.

**Phase 6 — Extract the shared `NeighboringForcesAgent` base class.** Pure refactor:
`response_team.py`'s existing class moves to `agents/neighboring_forces_agent.py`,
parameterized; `response_team.py` becomes a thin subclass. **Testable in isolation**: this
phase changes zero externally-visible behavior for `response_team` — its own existing test
suite for `dispatch_neighboring_force`/capacity/resource-unavailable must still pass unchanged,
which is the test for this phase (a regression check, not new tests).

**Phase 7 — Friendly forces, `firefighting`, using the extracted base.** Home areas/pool size
from section 3.1; new subclass (no `_resolve_kind` override needed); remove the 4 old tool
methods, update every `Protocol.approved_tools` and every existing test referencing them by
name (section 5); `admin_update_dispatch` reused from the already-shared store, no new store
work. **Testable**: dispatch each of the 4 kinds via `dispatch_neighboring_force(kind=...)` in
chat, confirm a real `neighboring_force_dispatches`-equivalent row appears in firefighting's own
DB and the busy-window capacity calculation works identically to `response_team`'s; confirm the
existing `FIRE_002` simulation flows that used to call the old tool names still resolve to the
same protocol via `approved_tools`'s updated entry.

**Phase 8 — Friendly forces admin page, both profiles.** `ADMIN_TABLES` entries once phase 7
lands. **Testable**: edit a dispatch row's status/eta on each profile, confirm
`list_neighboring_force_dispatches`-equivalent output and capacity calculation reflect it
immediately.

---

## 7. Risks and open questions

1. **Two of firefighting's four home areas (section 3.1) are not directly evidenced — kept as
   chosen, per your decision, but the evidence gap doesn't go away just because the values are
   final.** `police` (`ornim_street`) and `water_tankers` (`chemical_plant`) are explicit,
   quoted dispatch locations. `aircraft` (`pine_ridge`) is a reasoned inference, not a citation
   — the text never distinguishes it from `water_tankers`. `ambulance` has **no supporting text
   at all** anywhere in FIRE_002 — the placeholder value (`ornim_street`) is a guess, and is the
   single most likely of the four to need revisiting once real operational input exists or a
   future scenario update actually mentions an ambulance dispatch.

2. **The tool-unification in section 3.2 is a compatibility-breaking change for `firefighting`**,
   not additive: 4 named tools disappear, every protocol/test referencing them by name must be
   updated in the same phase. Sized in section 5, but worth flagging plainly that phase 7 cannot
   land partially — the old tools and the new one can't coexist without confusing the model
   about which to call for the same action.

3. **The drone status/mission-cascade write is now a data-integrity rule enforced in the store
   method itself** (reject `status="in_flight"` with a nonexistent `assigned_mission_id`; auto-
   abort an active mission when status changes away from `in_flight`), not a UI restriction —
   confirm this specific behavior (rather than, say, silently allowing an inconsistent state) is
   what "really updates the server state, same as everything else" is meant to include.

4. **Neighboring-force capacity is computed, not stored** — an admin edit to
   `dispatched_at`/`status` retroactively changes the busy-window calculation everywhere it's
   read. Intended, per the immediate-effect requirement, not a bug — restated here since it now
   applies to firefighting too once phase 7 lands.

5. **No generic validator drift-checks `AdminColumn.choices` against the DB's own
   `CHECK(...)`.** Low risk, few tables, worth a code comment cross-reference at each
   declaration site.

6. **Concurrency** — unchanged from the previous plan version: real agent tool calls and admin
   edits both write these tables; no optimistic locking proposed (last-write-wins, matching this
   codebase's existing pattern); an accepted v1 gap, restated rather than re-litigated.

7. **Security posture unchanged, not improved** — same single-shared-credential, HTTP-only,
   localhost-only admin auth now covers real-world-effecting writes (drone recall, force
   dispatch status) on two profiles instead of one. A decision being made explicitly, not
   inherited silently.

8. **`allow_create`/delete semantics per table still need explicit per-table answers** — can an
   admin add a new drone, delete a dispatch row, etc.? One-line decisions in each
   `ADMIN_TABLES` declaration, not defaulted uniformly.

9. **Confirm base-class-level test coverage convention exists before phase 6's extraction** —
   the estimate for `tests/test_neighboring_forces_agent.py` assumes `SurveillanceAgent`/
   `TeamStatusAgent` (the two existing shared bases) already have their own base-class-level
   tests, mirrored for the new shared `NeighboringForcesAgent`. Not verified in this pass —
   check before sizing phase 6's test work precisely.
