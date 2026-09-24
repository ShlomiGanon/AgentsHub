# Response Team operational-state plan

Locked design for unifying the Response Team into one profile, persisting
simulation-relevant operational state in that profile's own database, and
treating SEC_001 as a live event. Implementation has not started; this file
is the specification.

## Goal

One Response Team profile. SEC_001 writes to that profile's live SQLite file
and is treated as a real event. Operational state lives in **separate tables
in that same file**. Tools and tables are **unique to this profile**; Fire /
Rescue is unchanged.

Delete during later implementation (not when only writing this document):
`profiles/standby_squad.py`, `profiles/response_team_sim.py`. Keep
`profiles/fire_station.py`, `profiles/firefighting.py`,
`profiles/fire_station_sim.py`.

## Architecture rules

- Single DB: `data/response_team/response_team_history.db` (`DB_PATH` on
  `profiles/response_team.py`).
- Do **not** add domain tables to `persistence/schema.py` (every profile,
  including Fire, would inherit them).
- New persistence module(s) open the **same** `db_path`, run profile-only
  `CREATE TABLE IF NOT EXISTS`, and are imported only by Response Team agents.
- Agents are profile-owned subclasses (or classes declared only in
  `response_team.AGENTS`). Do not add these tools to shared
  `agents/friendly_forces_agent.py`, `agents/surveillance_agent.py`,
  `agents/team_status_agent.py`, or `agents/roster_agent.py`.
- Orchestration never touches SQL; only `@tool` methods on the owning
  sub-agent.
- Profile module text stays English; Hebrew only in `messages/he.py`.
- Documented exception: neighboring-force status becomes `arrived` after ETA
  with no human report (breaks the usual "tool result proves only the tool's
  effect" rule for that later ticker update only). Dispatch tool text still
  says "recorded, en route, ETA=…".

```
build_context
  -> ensure_simulation_entities (users, groups, roster)
  -> create-if-missing cameras / drones
  -> response_team_history.db

SEC_001 via /Msg -> protocol -> sub-agent tool -> same DB
ETA ticker -> neighboring_force_dispatches (en_route -> arrived)
```

## Profile declarations

**`AREAS`:** `west_gate`, `east_gate`, `east_fence`, `east_orchards`,
`expansion_neighborhood`, `old_public_building`, `south_corner`,
`access_road`, `drones_warehouse`.

**Cameras** (create if missing; never update an existing row):

| id | area | Was in SEC_001 |
|---|---|---|
| `CAM-01` | `east_fence` | camera 03 |
| `CAM-02` | `east_fence` | camera 04 |
| `CAM-03` | `south_corner` | camera 08 |

Rewrite SEC_001 catalog strings to these IDs.

**Drones:** `DRONE-01`, `DRONE-02`. Home and recall target: `drones_warehouse`.

**Neighboring force kinds and home bases** (no firefighters; YAMAG folds
into `yasam`):

| kind | base |
|---|---|
| `ambulance` | `expansion_neighborhood` |
| `police` | `east_gate` |
| `k9` | `east_orchards` |
| `yasam` | `east_gate` |

## ETA matrix (seconds, symmetric; same cell = 45)

Order: WG `west_gate`, EG `east_gate`, EF `east_fence`, EO `east_orchards`,
EX `expansion_neighborhood`, OP `old_public_building`, SC `south_corner`,
AR `access_road`, DW `drones_warehouse`.

| from\to | WG | EG | EF | EO | EX | OP | SC | AR | DW |
|---|---|---|---|---|---|---|---|---|---|
| WG | 45 | 180 | 210 | 200 | 180 | 150 | 120 | 90 | 90 |
| EG | 180 | 45 | 60 | 90 | 90 | 100 | 150 | 120 | 90 |
| EF | 210 | 60 | 45 | 75 | 90 | 110 | 150 | 160 | 120 |
| EO | 200 | 90 | 75 | 45 | 90 | 100 | 160 | 150 | 120 |
| EX | 180 | 90 | 90 | 90 | 45 | 60 | 140 | 130 | 100 |
| OP | 150 | 100 | 110 | 100 | 60 | 45 | 130 | 140 | 90 |
| SC | 120 | 150 | 150 | 160 | 140 | 130 | 45 | 90 | 80 |
| AR | 90 | 120 | 160 | 150 | 130 | 140 | 90 | 45 | 100 |
| DW | 90 | 90 | 120 | 120 | 100 | 90 | 80 | 100 | 45 |

## Tables in the same DB

Keep the existing core tables (`users`, `events`, `event_steps`,
`held_events`, `notification_log`, `conversation_messages`, summaries, logs,
`telegram_groups`).

### Roster / attendance

Same shape as `persistence/team_status_store.py`, plus one column:

- `team_members` — add `current_area` (nullable text). Only members who must
  file attendance (the six SEC fighters with `pre_approved_rosters`). Not
  technician, resident, police, MDA, or site security officer.
- `roster_approval`, `attendance_cycles`, `attendance_responses` unchanged in
  meaning.
- Daily cycle (08:00 local, one-hour window). Late replies still need
  commander **response** review — that is not protocol `approval_flag`.

### Surveillance

`cameras`, `drones`, `drone_missions` (same shape as
`persistence/surveillance_store.py`; seed and area names come from the
profile, not hardcoded standby sectors).

### Neighboring forces (new)

```
neighboring_force_dispatches
  request_id, force_kind, unit_count, origin_area, target_area,
  status (en_route | arrived), dispatched_at, eta_seconds,
  arrived_at, note, event_id
```

Force kinds and bases are profile constants, not a standing-units table.

## Profile-load provisioning

Extend the existing hook in `api/app.py` (`ensure_simulation_entities` after
`open_persistence`):

1. Users and groups — already in `profiles/simulation_provisioning.py`.
2. Roster `register_member` for personas with `pre_approved_rosters` —
   already; port the six fighters from `standby_squad`.
3. Cameras and drones — insert if id missing; never overwrite.
4. Open today's attendance cycle if none exists (otherwise SEC_001 absence
   reports fail the daily-cycle rule).
5. `approve_roster` only the first time the roster has no approval row.

Same idiom as simulation users: create if missing, never touch if present.

## Agents and tools (Response Team only)

### Roster agent

Attendance tables + `current_area`:

- `record_attendance_response` (write)
- `report_team_availability` (read)
- `start_daily_attendance_check` (write)
- `report_team_movement` (write `current_area`)

### Surveillance agent

Cameras + drones (one agent):

- `update_camera_status` / list-or-overview (write / read)
- `dispatch_drone_to_area`
- `recall_drone` (to `drones_warehouse`)
- `get_drone_fleet_status`

### Neighboring-forces agent

Dispatch table only:

- `dispatch_neighboring_force(kind, target_area, unit_count, note)` — insert
  `en_route`; ETA from matrix(base, target)
- `list_neighboring_force_dispatches` — read; ticker may have flipped
  `arrived`

ETA ticker: follow the attendance-loop pattern in
`bot/background_services.py` / existing `POST /TeamStatus/AttendanceCheck`.
Advance rows where `dispatched_at + eta_seconds` has passed.

### history_agent

No new table; incident summary reads `events`.

## Protocols (authored for SEC_001)

All seven: `approval_flag=False`, `commander_only=False`,
`requires_confirmation=False`.

| Protocol | EVENT_TYPE | SEC_001 steps | Tools |
|---|---|---|---|
| `record_attendance` | `attendance` | P1: 1, 4, 6 | `record_attendance_response` |
| `update_camera_status` | `camera_status` | P1: 2, 7; P2: 1 | `update_camera_status` |
| `report_security_incident` | `security_incident` | P1: 8; P2: 2, 3, 5, 9; P3: 1, 5, 7, 9 | `dispatch_drone_to_area` as needed |
| `dispatch_neighboring_force` | `force_dispatch` | P2: 6; P3: 3, 8 | `dispatch_neighboring_force` |
| `report_team_movement` | `team_movement` | P2: 8; P3: 2, 6 | `report_team_movement` |
| `query_situational_picture` | `situational_query` | P1: 5, 9; P2: 4, 7; P3: 4 | read roster + cameras + dispatches |
| `query_incident_summary` | `incident_summary` | P3: 10 | history |

P1 step 3 (already-handled fire, no firefighter kind): information event, no
dispatch. P3 step 7 (ad-hoc mast camera): description only; no fourth camera
row.

Replace the current light `profiles/response_team.py` protocol/event-type set
with this list.

## Simulations

Move `SIMULATION_USERS` / `GROUPS` / `ROSTERS` / `SIMULATIONS` from
`profiles/standby_squad.py` into `response_team`. Keep
`pre_approved_rosters` only on the six fighters. Update camera numbers in
`messages/he.py` / `messages/en.py`. No `response_team_sim` twin.

## Implementation stages

1. Profile constants: areas, cameras, drones, force bases, ETA matrix,
   personas.
2. Persistence on `DB_PATH` + extra-table DDL (reuse team-status /
   surveillance contracts where possible; new dispatch store).
3. Three profile-only agents + tools; rewrite protocols.
4. Provisioning + daily cycle open-if-missing.
5. ETA ticker.
6. Port SEC_001 + catalog camera IDs.
7. Delete `standby_squad` and `response_team_sim`; retarget tests,
   `.github/workflows/ci.yml`, file catalog, `docs/operator_guide.md`.
8. Leave Fire profiles untouched.

Tests that must change in that later work:
`tests/test_operational_profiles.py` (live/sim isolation),
`tests/test_operational_scenarios.py`, `tests/test_standby_squad_*`,
file-catalog exactness.

## Out of scope for this document

No code, no Fire merge, no global schema migration, no shared-agent tool
additions.
