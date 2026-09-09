# Systems Integration Audit — `profiles/unified_test.py`

**Question:** Is `profiles/unified_test.py` fully wired into every cross-cutting mechanism this codebase has, the same way the other profiles are (or should be)?

**Method:** Read the mechanism implementations directly (not just `profiles/unified_test.py`), compared them against the other four real deployment profiles (`profiles/demo.py`, `profiles/friendly_forces.py`, `profiles/sub_agent_surveillance.py`, `profiles/sub_agent_team_status.py`), and against `docs/profile_spec.md` where a written contract exists. `profiles/template.py` is a fill-in-the-blank scaffold, not a deployed profile, and is used only as a reference, not a matrix column.

**Bottom line up front:** `unified_test.py` is the *most* thoroughly wired profile in the repo for almost every mechanism that has a real per-profile integration point — RBAC (`commander_only`/`requires_confirmation`), the `action.{protocol_name}` label convention, the optional Telegram role-keyboards, and the exact-tool-result-capture anti-paraphrase pattern are all used more completely here than in any sibling profile, and are backed by dedicated tests (`tests/test_unified_role_and_security.py`). It has exactly one real, structural gap, and it is a significant one: it is the *only* first-party source file in the repo explicitly exempted from the "all Hebrew text must go through the message catalog" architectural rule. It also has one behavioral deviation from convention (import-time database seeding / auto-provisioning) that every other profile avoids.

---

## Step 1 — Mechanisms found

| # | Mechanism | Defined in | Profile integration point |
|---|---|---|---|
| 1 | Trace ID / correlation ID | `tools/observability.py` (`get_trace_id`, `set_trace_id`, `trace_context`, `new_trace_id`) | Automatic — every `Agent.process()` call, tool wrapper, protocol step, and queue item is trace-scoped by the framework. A profile only touches it if it needs exact-output capture (see #19). |
| 2 | Structured (JSON) + human-readable logging | `tools/observability.py` (`configure_logging`, `_JsonFormatter`, `_HumanReadableFormatter`, `_PersistenceLogHandler`) | Automatic, no profile hook. |
| 3 | OpenTelemetry tracing/metrics | `tools/observability.py` (`stage_context`, `telemetry_span`, `configure_telemetry`), `agents/provider_telemetry.py` | Automatic, env-gated (`OBSERVABILITY_MODE`), no profile hook. |
| 4 | Agent-layer error hierarchy | `agents/contracts.py` (`AgentInvocationError`, `AgentTimeoutError`, `AgentModelError`, …) | Automatic — raised by `agents/runtime.py`, carries `trace_id`. |
| 5 | API-layer error hierarchy + handlers | `api/request_boundary.py` (`ApiError` family, `register_error_handlers`) | Automatic, no profile hook. |
| 6 | Env-var/credential indirection | `config/environment.py` (`TierModel`, `resolve_tier_model_from_env`), profile's `BOT_TOKEN_ENV`/`MODEL_CREDENTIAL_ENVS`, `AgentSpec` tier system | Profile declares env-var *names* only, never secret values; documented in full in `docs/profile_spec.md`. |
| 7 | RBAC — `commander_only` / `requires_confirmation` | `protocols/contracts.py` (`Protocol` fields, default `False`), enforced in `orchestrator/holds.py`, `orchestrator/flows.py`, `api/routes.py` | Optional per-protocol opt-in. |
| 8 | RBAC — `approval_flag` (generic human-in-the-loop gate) | `protocols/contracts.py`, enforced in `orchestrator/holds.py` | Required (non-optional) field on every protocol. |
| 9 | Retry + idempotency gate | `protocols/executor.py` (`execute_step_with_retry`, `_can_retry`), `agents/contracts.py` (`tool(... idempotent=...)`) | Structural — a side-effecting tool without an explicit `idempotent` value fails to import at all (`agents/contracts.py:48-53`). |
| 10 | Side-effect concurrency locks | `protocols/executor.py` (`_locks_for_step`, keyed `agent:tool` locks) | Automatic, derived from each tool's `side_effecting` flag. |
| 11 | Event queue mode + `OptimizationPolicy` | `orchestrator/event_queue.py` (`SerialEventQueue`/`PolicyAwareEventQueue`), `profiles/contracts.py` (`OptimizationPolicy`) | Optional module-level `OPTIMIZATION_POLICY`; defaults to the legacy serial queue. |
| 12 | LLM client cache | `agents/runtime.py` (`_llm_cache`, gated by `provider_capabilities(...).thread_safe_client`) | Automatic, provider-keyed, no profile hook. |
| 13 | Persistence open + schema/migrations | `persistence/contracts.py`, `persistence/surveillance_contracts.py`, `persistence/team_status_contracts.py`, `persistence/schema.py` | Profile supplies `DB_PATH` (+ any domain-specific `*_DB_PATH` it defines itself); schema creation/migration is automatic on open. |
| 14 | Mutable settings store | `config/live_settings.py` (`SettingsStore`, `<DB_PATH>.settings.json`) | Profile supplies starting `RETRY_COUNT`/`RISK_THRESHOLD`/`LOOKBACK_WINDOW_DAYS`; the store owns them after first run. |
| 15 | `/SYSTEM` health/status + profile-file-hash drift detection | `api/routes.py:900-938`, `profiles/loader.hash_profile_file` | Fully automatic — reflects whatever the loader built, no profile hook. |
| 16 | Message catalog / "Hebrew only through the catalog" HARD RULE | `messages/catalog.py`, `messages/en.py`, `messages/he.py`; enforced by `tests/test_hebrew_leakage.py` | Profile picks `DEFAULT_LANGUAGE`; all user-visible fixed text is required to live in `messages/en.py`/`messages/he.py`, never as a literal in application source. |
| 17 | `action.{protocol_name}` dynamic label convention | `bot/interactions.py:760-765` (`_friendly_action_type`) | Optional — falls back to `action.generic` if a profile's protocol name has no matching catalog key in both languages. |
| 18 | Optional Telegram role-keyboards | `bot/app.py:167-171` (`getattr(profile_mod, "COMMANDER_KEYBOARD"/"VIEWER_KEYBOARD", None)`) | Fully optional module-level constants. |
| 19 | Exact-tool-result-capture (anti-paraphrase) pattern | First introduced in `agents/surveillance_agent.py` (`_capture_recall_result`, trace-id-keyed `ContextVar` + `process()` override) | Not a formally documented mechanism — an ad hoc pattern some agents use to force the LLM to return a tool's exact output instead of an LLM paraphrase of it. |
| 20 | `bot-service` / operator user-provisioning bootstrap | `cli/user_admin.py`, `api/admin.py` (`/admin/bot-service/provision`), documented operator flow in `README.md` / `docs/operator_guide.md` | The **standard** mechanism is operator-run, out-of-band, after profile load — a profile module is not supposed to write users itself. |
| 21 | Profile structural validation | `profiles/loader.py` (`validate_profile`, `REQUIRED_PROFILE_ATTRS`, `PROTOCOL_REQUIRED_ATTRS`) | Automatic at load time. |
| 22 | Package-boundary / import-graph rule | `tests/test_architecture.py` | Automatic, governs every file under the governed packages. |
| 23 | Event-type required-fields gate | `profiles/contracts.py` (`EventTypeRegistry`), profile's optional `EVENT_TYPE_REQUIRED_FIELDS` | Optional per-event-type field-completeness gate, `protocols.EVENT_DATA_FIELDS` vocabulary only. |

Two "strong `getattr` signal" integration points from the prompt's own checklist turned out to matter most: `bot/app.py`'s `COMMANDER_KEYBOARD`/`VIEWER_KEYBOARD` lookup (#18), and `bot/interactions.py`'s dynamically-built `f"action.{protocol_name}"` catalog key (#17). Both are exercised *more* by `unified_test.py` than by any other profile — see the matrix.

---

## Step 2 — Integration Matrix

Legend: ✅ fully wired · ⚠️ partially wired · ❌ missing · N/A mechanism doesn't apply to this profile's domain

| # | Mechanism | `demo` | `friendly_forces` | `sub_agent_surveillance` | `sub_agent_team_status` | `unified_test` |
|---|---|:---:|:---:|:---:|:---:|:---:|
| 1 | Trace ID propagation | ✅ | ✅ | ✅ | ✅ | ✅ |
| 2 | Structured/human logging | ✅ | ✅ | ✅ | ✅ | ✅ |
| 3 | OpenTelemetry tracing/metrics | ✅ | ✅ | ✅ | ✅ | ✅ |
| 4 | Agent-layer error hierarchy | ✅ | ✅ | ✅ | ✅ | ✅ |
| 5 | API-layer error hierarchy | ✅ | ✅ | ✅ | ✅ | ✅ |
| 6 | Env-var/credential indirection | ✅ | ✅ | ✅ | ✅ | ✅ |
| 7 | RBAC — `commander_only`/`requires_confirmation` | N/A | N/A | N/A | N/A | ✅ |
| 8 | RBAC — `approval_flag` | ✅ (2/4 protocols) | ✅ (4/4) | ✅ (3/8) | N/A (0 side-effects) | ✅ (3/10) |
| 9 | Retry + idempotency gate | ✅ | ✅ | ✅ | ✅ | ✅ |
| 10 | Side-effect concurrency locks | ✅ | ✅ | ✅ | ✅ | ✅ |
| 11 | Event queue mode / `OptimizationPolicy` | ✅ (default) | ✅ (default) | ✅ (default) | ✅ (default) | ✅ (default) |
| 12 | LLM client cache | ✅ | ✅ | ✅ | ✅ | ✅ |
| 13 | Persistence open + schema | ✅ | ✅ | ✅ | ✅ | ⚠️ (see 3a) |
| 14 | Mutable settings store | ✅ | ✅ | ✅ | ✅ | ✅ |
| 15 | `/SYSTEM` health + file-hash drift | ✅ | ✅ | ✅ | ✅ | ✅ |
| 16 | Message catalog / Hebrew HARD RULE | ✅ (N/A, `en`) | ✅ (N/A, `en`) | ✅ (`he`, zero literals) | ✅ (`he`, zero literals) | ❌ (see 3b) |
| 17 | `action.{protocol_name}` label convention | ⚠️ (0/1 matched) | ⚠️ (0/4 matched) | ⚠️ (1/3 matched) | N/A | ✅ (3/3 matched) |
| 18 | Optional Telegram role-keyboards | N/A | N/A | N/A | N/A | ✅ |
| 19 | Exact-result-capture pattern | N/A (no exact-ID output) | ⚠️ (base class doesn't use it; see 6b) | ✅ (inherited) | ⚠️ (base class doesn't use it; see 6b) | ✅ (all 3 agents) |
| 20 | `bot-service` provisioning bootstrap | ✅ (standard/operator-driven) | ✅ (standard/operator-driven) | ✅ (standard/operator-driven) | ✅ (standard/operator-driven) | ⚠️ (see 3c) |
| 21 | Profile structural validation | ✅ | ✅ | ✅ | ✅ | ✅ |
| 22 | Package-boundary import rule | ✅ | ✅ | ✅ | ✅ | ✅ |
| 23 | Event-type required-fields gate | ✅ (2/2 types) | N/A (not declared, not required) | N/A (not declared, not required) | N/A (not declared, not required) | ✅ (3/8 types, correctly scoped) |

---

## Step 3 — Deep dive on `unified_test.py`'s ⚠️/❌ cells

### 3a. Persistence bootstrap (⚠️, row 13) — see 3c below

The persistence *mechanism itself* (`open_persistence`/`open_surveillance_persistence`/`open_team_status_persistence`, schema creation) is used correctly and the three databases are properly isolated (`unified_history.db`, `unified_surveillance.db`, `unified_team_status.db` — `profiles/unified_test.py:31-33`). The ⚠️ is entirely about *when* and *how* `unified_test.py` writes to them — folded into 3c so it isn't counted twice.

### 3b. Message catalog / Hebrew HARD RULE (❌, row 16) — the main finding

`tests/test_hebrew_leakage.py` states the rule in its own module docstring:

> "no Hebrew string, anywhere, in any first-party source file that is not a translation/message-catalog file. Every piece of Hebrew text a user ever sees must go through `messages/he.py`"

and then carves out a literal, named exception:

```python
_ALLOWED_HEBREW_FILES = {
    "messages/en.py",
    "messages/he.py",
    "profiles/unified_test.py",   # <-- tests/test_hebrew_leakage.py:28
}
```

`profiles/unified_test.py` hardcodes Hebrew text directly in Python source in at least three distinct ways, none of which go through `messages/catalog.py`:

- **System prompts** — e.g. `UnifiedSurveillanceAgent.system_prompt` (`profiles/unified_test.py:84-95`), all in Hebrew.
- **Tool descriptions** passed to `@tool(...)` — e.g. `"מחזיר סטטוס תפעולי, רמות סוללה ומיקומים של צי הרחפנים בעברית."` (`profiles/unified_test.py:120`).
- **Tool *return values*** — the actual strings shown to the end user, e.g. `"לא נמצאו רחפנים במערך."` (`profiles/unified_test.py:132`), `"החזרת הרחפנים הושלמה בהצלחה ✅..."` (`profiles/unified_test.py:242`), the entire `COMMANDER_KEYBOARD`/`VIEWER_KEYBOARD` button-label tuples (`profiles/unified_test.py:873-885`).

**Why this happened (root-cause context for Step 4):** every *other* `he`-language profile (`sub_agent_surveillance.py`, `sub_agent_team_status.py`) reuses its base agent class's tools unmodified. Those base classes return **English** text and rely on the LLM's `"Answer in Hebrew when the request is in Hebrew"` instruction (`agents/surveillance_agent.py:56`) to translate it live. `unified_test.py` instead needed byte-exact Hebrew output (drone callsigns, ETAs, mission IDs must never be corrupted by an LLM's live translation/paraphrase), so its authors wrote the exact Hebrew strings directly into the tool bodies and used the exact-result-capture pattern (#19) to force the model to return them verbatim. That is a reasonable *product* goal, but the *implementation* bypassed the catalog mechanism entirely instead of extending it — the catalog already supports named placeholders (`messages/catalog.py:69-85`, used today for `"debug.tokens_breakdown"` etc.), so the same exact-string guarantee was achievable through `messages/he.py`/`messages/en.py`.

**Runtime/production consequence:**
1. **No English parity for this profile's actual output.** `messages/catalog.py`'s `validate_catalogs()` enforces that every key exists, with matching placeholders, in *both* `en` and `he` (`messages/catalog.py:32-59`) — a real safety net against a translator missing a string. `unified_test.py`'s Hebrew tool output has no such enforcement or English counterpart at all; if this profile is ever run with `DEFAULT_LANGUAGE="en"` (nothing prevents it — `DEFAULT_LANGUAGE` is a free profile choice) every tool response stays hardcoded in Hebrew regardless.
2. **No compile-time placeholder safety.** A typo in a literal f-string (`f"רחפן {d['callsign']}..."`) fails only if that exact code path is executed by a test; a catalog-key placeholder mismatch is caught by `validate_catalogs()` at `get_catalog()` time, before any request is served.
3. **The rest of the codebase's single-source-of-truth-for-UI-text guarantee no longer holds** for this profile — an operator (or a future contributor) auditing "everything the user can see" by reading `messages/he.py` will silently miss roughly 40 user-facing strings that only exist in `profiles/unified_test.py`.
4. This is a **known, accepted** gap, not a silent one — the test suite documents it in-line — but it is still a real divergence from how every other profile satisfies this specific mechanism, and it's the one place in this audit where "wired the same way the other profiles are" is unambiguously false.

**Classification:** the code doesn't call the mechanism at all for its tool-output strings (as opposed to calling it with wrong parameters/scope) — this is a "doesn't call it" gap, not a misuse.

### 3c. `bot-service` provisioning bootstrap (⚠️, row 20)

Every other profile relies on the standard, documented, operator-driven flow:

```
python -m cli.user_admin --profile <profile_module> add --telegram-id bot-service --level commander
```

(`README.md:76`, `docs/operator_guide.md:103`, `docs/how_to_connect_telegram.md:76`) — run once, out-of-band, after the operator has reviewed what they're granting.

`profiles/unified_test.py` instead defines `_seed_mock_data()` (`profiles/unified_test.py:665-701`) and calls it **unconditionally at module import time** (`profiles/unified_test.py:706`, top-level `_seed_mock_data()` call with no `if __name__` guard or lazy-init check beyond "is the DB empty"). It:

- Auto-registers `"bot-service"` at **commander** level directly via `hist_store.write_user("bot-service", "commander")` (`profiles/unified_test.py:669-670`) — the exact write `cli.user_admin add` makes, but performed automatically rather than by an operator action.
- Auto-registers and auto-approves a 6-member Hebrew-named readiness-team roster (`profiles/unified_test.py:680-686`) and opens an attendance cycle (`profiles/unified_test.py:688-690`).

**Runtime/production consequence:**
- **Import has a side effect that every other profile in the repo avoids.** `sub_agent_surveillance.py`/`sub_agent_team_status.py` only call `Path.mkdir()` at import time; no other profile opens a SQLite connection or writes a row merely by being imported. Anything that imports `profiles.unified_test` for a reason unrelated to running it as a live deployment — a future doc-generation script, a static-analysis tool, an IDE's "import all profiles to build an index" pass, a test that imports the module to read a constant off it (several already do exactly this: `tests/test_unified_role_and_security.py:159,205,243,281` import `from profiles import unified_test`) — silently creates/mutates `data/unified_test/*.db` and grants a fixed, publicly-known service identity commander-level access, with no log line calling it out as unusual (it logs as an ordinary write, indistinguishable from an operator's deliberate provisioning).
- `_seed_mock_data()`'s `write_user` write is guarded (`if hist_store.read_user("bot-service") is None`), so it's idempotent/non-destructive on re-import — this is not a data-corruption risk — but it is a silent, unauthorized-by-design privilege grant path that bypasses the one piece of operator judgment (`cli.user_admin`) the rest of the system requires before `bot-service` gets commander access. Since `bot-service` is a fixed, public, non-secret identity (`api/request_boundary.py:101-106` — the whole reason `BOT_SERVICE_KEY` exists), the actual security boundary in production is `BOT_SERVICE_KEY`, not this write; still, it means this profile's provisioning story cannot be audited the same way every other profile's can ("check whether an operator ran `cli.user_admin`").

**Classification:** the mechanism (operator-driven provisioning) is not called at all by this profile — it has its own separate, parallel bootstrap path instead.

---

## Step 4 — Root cause categorization

**1. Never implemented**
- 3b (Hebrew strings bypass the message catalog). The catalog mechanism is fully capable of expressing this profile's exact-string requirement (named placeholders, `validate_catalogs()` parity checking already exist and are exercised elsewhere) — it was simply not used for this profile's ~40 user-facing strings. Not a refactor casualty: `tests/test_hebrew_leakage.py`'s exemption for this exact file suggests it was a conscious, from-the-start decision when the file was authored, not something that broke later.

**2. Broken by refactor**
- None found. Nothing in `unified_test.py`'s cross-cutting wiring shows the signature of a mechanism that used to work and was orphaned when the 3-agent architecture landed (no dead imports, no unreachable branches, no stale `getattr` fallbacks specific to this profile). The RBAC fields, keyboards, and result-capture pattern are all live and covered by passing tests today.

**3. Structurally incompatible**
- None found for `unified_test.py` specifically. Every mechanism this profile touches (RBAC, retry/idempotency, event queue, message catalog, `action.*` labels) already supports a 3-specialist-agent profile without modification — `unified_test.py`'s own test suite (`tests/test_unified_role_and_security.py`) is itself the proof, exercising all three agents together through the real orchestrator flow. The one place a case could be made — the exact-result-capture pattern needing a `ContextVar` per agent rather than one shared implementation — is a few lines of near-identical boilerplate repeated three times (`_surv_key`/`_team_key`/`_forces_key` and their capture functions, `profiles/unified_test.py:39-73`), which is a **duplication/simplification** concern, not a structural incompatibility; see 6b.

3c (import-time provisioning) doesn't fit any of the three categories cleanly — it isn't a missing integration into an existing mechanism, it's a self-authored *parallel* mechanism that stands in for the standard one. It's listed under Step 5 as its own remediation item.

---

## Step 5 — Remediation backlog (numbered, approve item-by-item)

### 1. Move `unified_test.py`'s Hebrew tool-output strings into the message catalog
**Priority: Medium.** No functional bug today (the exemption is deliberate and tested), but it's the one real architectural-consistency gap found, it's the most labor-intensive item, and every day it stays this way is another day the "everything visible goes through `messages/he.py`" guarantee doesn't actually hold repo-wide.
**Scope: touches shared infrastructure** — adds ~40 new keys to *both* `messages/en.py` and `messages/he.py` (required for `validate_catalogs()` parity), which is low-risk to other profiles (additive only) but is not a `unified_test.py`-only change, and is large enough to warrant its own review pass rather than folding into other fixes.

Representative diff (one tool, as a template for the rest):

```python
# messages/he.py — add near the other Hebrew-facing message groups
"unified.surveillance.no_drones": "לא נמצאו רחפנים במערך.",
"unified.surveillance.fleet_status_header": "🛸 מצב צי רחפנים ({count} רחפנים):",
"unified.surveillance.fleet_status_line": (
    "• [{drone_id}] {callsign} ({model}): {status} | סוללה: {battery}% | "
    "גזרה: {area}{mission_info}"
),
"unified.surveillance.fleet_status_summary": (
    "סיכום: {ready} מוכנים לשיגור | {in_flight} באוויר | {charging} בטעינה"
),

# messages/en.py — same keys, English text, identical placeholders
"unified.surveillance.no_drones": "No drones found in the fleet.",
"unified.surveillance.fleet_status_header": "Drone Fleet Status ({count} drones):",
"unified.surveillance.fleet_status_line": (
    "- [{drone_id}] {callsign} ({model}): {status} | Batt: {battery}% | "
    "Area: {area}{mission_info}"
),
"unified.surveillance.fleet_status_summary": (
    "Summary: {ready} ready | {in_flight} in-flight | {charging} charging"
),
```

```python
# profiles/unified_test.py — get_drone_fleet_status, rewritten to call the catalog
from messages import get_catalog  # profile already knows its own DEFAULT_LANGUAGE

def get_drone_fleet_status(self, status_filter: str = "") -> str:
    catalog = get_catalog(DEFAULT_LANGUAGE)
    cleaned = status_filter.strip().lower()
    if cleaned in {"all", "*"}:
        cleaned = ""
    drones = self.surveillance_store.list_drones(status=cleaned or None)
    if not drones:
        all_drones = self.surveillance_store.list_drones()
        drones = all_drones if all_drones else []
    if not drones:
        res = catalog.text("unified.surveillance.no_drones")
        _capture_surv_result(res)
        return res
    ...
    res = catalog.text("unified.surveillance.fleet_status_header", count=len(drones)) + "\n" + ...
```

Once every tool body is converted, remove the exemption:

```python
# tests/test_hebrew_leakage.py
_ALLOWED_HEBREW_FILES = {
    "messages/en.py",
    "messages/he.py",
-   "profiles/unified_test.py",
}
```

The system prompts and `@tool(...)` descriptions (never shown to the end user, only to the model) are lower-value to migrate and can reasonably stay as-is or move in a follow-up — flag this explicitly when approving, since the highest-value/lowest-risk slice of this item is the *tool return values* and the `COMMANDER_KEYBOARD`/`VIEWER_KEYBOARD` button labels, not the system prompts.

### 2. Stop auto-provisioning `bot-service` and the readiness roster at import time
**Priority: High.** Not a currently-exploited bug (the write is idempotent and `BOT_SERVICE_KEY` is the real gate), but it's a silent side effect on `import profiles.unified_test` that no other profile has, it grants commander-level access to a fixed public identity without an operator's explicit action, and several existing tests already trigger it as a side effect of an unrelated import (`tests/test_unified_role_and_security.py:159,205,243,281`).
**Scope: `unified_test.py`-only.** No shared-infrastructure change needed — this is purely about *when* this profile calls its own seeding function.

```python
# profiles/unified_test.py

def _seed_mock_data() -> None:
    """Initialize mock readiness-team members and bot-service if DB is empty."""
    ...

- # Run initial seed
- _seed_mock_data()
+ # NOTE: intentionally not called at import time — see docs/profile_spec.md.
+ # Call `profiles.unified_test.ensure_seed_data()` explicitly from the one
+ # real entry point that starts this profile (api.app.main / bot.app.main /
+ # a dedicated `scripts/seed_unified_test.py`), the same way `cli.user_admin`
+ # is the explicit, operator-run step for every other profile's bot-service
+ # registration.
+ def ensure_seed_data() -> None:
+     _seed_mock_data()
```

Then update `run_stack.py` (the one place in the repo that already treats `profiles.unified_test` as a runnable target) to call `unified_test.ensure_seed_data()` explicitly before startup, and update the handful of tests that currently rely on the import-time side effect to call it explicitly too. This keeps today's "works out of the box for a demo" convenience, but makes the data-seeding an explicit, visible step instead of a side effect of `import`.

### 3. De-duplicate the three near-identical exact-result-capture blocks
**Priority: Low.** Pure maintainability/simplification — see Step 6b; not a correctness gap, and explicitly *not* counted against the Integration Matrix. Listed here only because it's a mechanical, low-risk cleanup that falls out of fixing item 1.
**Scope: `unified_test.py`-only** if kept local; **shared infrastructure** if promoted into `agents/runtime.py` as a reusable helper (recommended — see 6b for the proposed shape). Flag for extra care only if the shared-helper route is chosen, since it would touch `agents/surveillance_agent.py` too.

---

## Step 6 — System-level observations

*(These are observations about the codebase as a whole, offered separately for a possible broader cleanup decision. None of them were used to score the Integration Matrix or set remediation priority above — several apply equally, or worse, to profiles other than `unified_test.py`.)*

### 6a. Orphaned/unused mechanisms

- **None of the 23 mechanisms found is fully dead code.** The closest candidate is the `action.{protocol_name}` convention (row 17): across the four non-unified profiles, only 1 of 11 approval-gated protocol names (`sub_agent_surveillance.py`'s `dispatch_drone_to_incident`, which happens to share its name with `unified_test.py`'s protocol) has a matching catalog key — every other approval-gated protocol in `demo.py`, `friendly_forces.py`, and the rest of `sub_agent_surveillance.py` falls back to the generic `"action.generic"`/`"פעולה מבצעית"` label. The mechanism works and degrades gracefully (it's not broken), but it is overwhelmingly under-adopted outside `unified_test.py` — which is the *opposite* of what an integration audit normally expects to find, and worth noting since it means `unified_test.py` is not the outlier here, it's the only profile using this feature as apparently intended.
- The exact-result-capture pattern (row 19) is *not* orphaned, but it is confined to exactly three files in the whole repo (`agents/surveillance_agent.py` and, now, `profiles/unified_test.py`'s three subclasses) despite being a generically useful "don't let the LLM paraphrase this exact tool output" primitive that `agents/team_status_agent.py` and `agents/friendly_forces_agent.py` could equally benefit from (see 6b).

### 6b. Questionable design patterns

- **The exact-result-capture pattern is copy-pasted, not shared.** `agents/surveillance_agent.py`'s `_recall_invocation_key`/`_capture_recall_result`/`process()` override (lines 23-33, 78-97) and `unified_test.py`'s three near-identical `_surv_key`/`_team_key`/`_forces_key` blocks (lines 39-73) plus three near-identical `process()` overrides (lines 97-117, 372-393, 590-609) are the same ~20-line pattern repeated four times with only the `ContextVar` name and dict changed. A cleaner alternative: a small factory in `agents/runtime.py`, e.g. `make_exact_result_capture() -> tuple[Callable[[str], None], Callable[[Agent, str, list[str], InvocationPolicy | None], AgentResult]]`, that any agent (base or profile-defined) can call once at class-definition time instead of hand-rolling the `ContextVar` + lock + `process()` override every time. This would also make the pattern discoverable and consistently applied (see 6a) rather than something each author has to reinvent from reading `surveillance_agent.py`'s source.
- **The `f"action.{protocol_name}"` catalog-key convention is undocumented outside `bot/interactions.py`'s source.** It's a real, working mechanism, but there is nothing in `docs/profile_spec.md` telling a profile author "if your protocol needs commander confirmation, also add an `action.<protocol_name>` key to both catalogs, or it'll silently show the generic label." That silent, graceful fallback is precisely why it went unnoticed in 10 of 11 cases across the non-unified profiles — a louder failure mode (or, better, a startup-time warning listing approval-gated protocols with no matching catalog key) would surface this instead of leaving it to be noticed by an end user seeing "Operational action" instead of "Tactical drone dispatch."
- **Import-time side effects in a profile module are unprecedented but not prohibited.** `docs/profile_spec.md` documents in detail that a profile module must not read `os.environ` or resolve model tiers at import time, but says nothing about whether a profile may perform I/O (DB writes) at import time at all. `unified_test.py`'s `_seed_mock_data()` is the only instance of this in the repo (see 3c/remediation item 2) — worth deciding, and writing down, whether this is disallowed in general or was simply never anticipated.

### 6c. Inconsistent conventions with no clear winner

- **`DB_PATH` location: temp dir vs. permanent dir.** `profiles/template.py`, `profiles/sub_agent_surveillance.py`, and `profiles/sub_agent_team_status.py` all put their databases under `tempfile.gettempdir()`; `profiles/demo.py`, `profiles/friendly_forces.py`, and `profiles/unified_test.py` all put theirs under a permanent `<repo>/data/` directory. `docs/profile_spec.md` says only "No default — two profiles running at once must not collide" — it takes no position on temp-vs-permanent, and both conventions are in active use with no comment anywhere explaining when to pick which (persistence across restarts vs. throwaway-test-profile is the obvious axis, but it's never stated).
- **Whether a profile defines its own `*_DB_PATH` module constant vs. a private `_PROFILE_DATA_DIR`-derived one.** `sub_agent_surveillance.py`/`sub_agent_team_status.py` expose `SURVEILLANCE_DB_PATH`/`TEAM_STATUS_DB_PATH` as public module constants; `unified_test.py` does the same (`UNIFIED_SURVEILLANCE_DB_PATH`, `UNIFIED_TEAM_STATUS_DB_PATH`) but prefixes them `UNIFIED_` rather than reusing the plain names — a reasonable choice given it needs both in one module, but there's no documented naming convention for "a profile with more than one domain-specific DB," so this is currently precedent-of-one.
- **Whether a specialist subclass overrides `process()` at all.** As covered in 6b, it's an implicit "read `surveillance_agent.py`'s source and copy the pattern" convention, not a documented one, so whether a given profile's agent subclass does this is currently a matter of whether its author happened to know the pattern exists.

---

## Backlog summary (for approval)

| Item | Priority | Scope |
|---|---|---|
| 1. Route `unified_test.py`'s Hebrew tool-output strings through the message catalog; drop its `test_hebrew_leakage.py` exemption | Medium | Shared infra (new catalog keys, additive/low-risk) + `unified_test.py` |
| 2. Stop seeding `bot-service`/roster data at import time; make it an explicit call from the real entry point | High | `unified_test.py`-only |
| 3. De-duplicate the three exact-result-capture blocks | Low | `unified_test.py`-only, or shared infra if promoted to `agents/runtime.py` |

Step 6 observations (6a–6c) are informational only and were not used to set any of the above priorities.
