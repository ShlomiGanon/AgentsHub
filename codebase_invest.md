# Codebase Investigation Report — AgentsHub

**Scope:** Full-repository standards-compliance and integration audit.
**Method:** Static review against the repo's own governance documents (`instructions.md`, `docs/allowed_calls.md`, `docs/file_catalog.md`, `docs/IMPROVES/REQUIRED_FIELDS_AND_CLOSED_DECISIONS.md`), AST-level scans for structural defects, targeted greps for known anti-patterns, and a full local test run (`python -m pytest`, 1206 tests, all green, ~5.5 min).
**Baseline:** branch `test-experiments`, HEAD `bad9be6`.

This report intentionally does not repeat `docs/SECURITY_AND_QA_AUDIT.md` in full — that document already covers a prior security/QA pass. Where this investigation re-verified one of its findings against current `HEAD`, it is listed under "Carried-forward findings" with a current-status note. Everything else below is new.

---

## Executive Summary

The codebase is **functionally healthy** — the full suite (1206 tests) passes cleanly, package boundaries are enforced by `tests/test_architecture.py`, and the project has unusually thorough self-documentation (`docs/file_catalog.md`, `docs/allowed_calls.md`, per-module docstrings). That green test suite, however, is not proof of architectural integrity: several of the defects found below are invisible to it by construction, because they are dead code, silently-dropped parameters, or drift between two independently-maintained data tables — categories no assertion in the current suite is positioned to catch.

The single largest risk area is **localization discipline**. The project has a documented "HARD RULE" — *no Hebrew string outside the message catalog* — with a dedicated regression test (`tests/test_hebrew_leakage.py`) created specifically because two earlier live bugs came from hardcoded Hebrew. That rule is currently violated in **7 production files, 138 occurrences**, via a gap in the enforcement test itself (it greps for literal Hebrew characters, not the `\uXXXX` escapes that all 138 occurrences use). This is not a hypothetical: it is the exact failure mode the rule was written to prevent, already reoccurring, currently undetected by CI.

The second largest risk area is **layering drift between `api` and `bot`**. `docs/allowed_calls.md` states "Bot reaches the application only through HTTP and never imports API internals" and "Raw SQL remains confined to persistence implementation modules." Both are currently violated: the bot opens a raw `sqlite3` connection directly against the API's database file, and the API layer independently re-implements Hebrew keyword/button matching that has already drifted out of sync with the equivalent table maintained in the bot layer.

A contributing systemic cause: **there is no linter or type checker anywhere in the toolchain** (`requirements-dev.txt` pins only `pytest`; CI runs no `ruff`/`flake8`/`mypy` step). This is very likely why a duplicate top-level function definition sat undetected in `orchestrator/reasoning.py` for however long it's been there — a two-second `ruff --select F811` run would have caught it; pytest cannot, because the second definition simply overrides the first and the code still runs.

None of these are exotic — each is demonstrated below with exact file/line references and, where relevant, the command used to verify it.

**Risk ranking:** 🔴 Critical (breaks a documented hard rule or an authentication boundary, currently undetected) · 🟠 High (real behavioral bug or architecture-boundary violation) · 🟡 Medium (drift/maintainability risk, not yet user-visible) · ⚪ Informational (governance/process gap).

---

## Standards Violations

### 🔴 1. The Hebrew-leakage "HARD RULE" is bypassed in 7 files via a gap in its own enforcement test

`docs/IMPROVES/REQUIRED_FIELDS_AND_CLOSED_DECISIONS.md` (lines 17–31) states, verbatim: *"No Hebrew string, anywhere, in any source file that is not a translation/message-catalog file... Every piece of Hebrew text the user ever sees must go through the existing message-catalog mechanism."* It further explains (lines 33–35) that this rule exists **because two separate live bugs already came from exactly this pattern**, and mandates (lines 252–255) "a grep-based regression test scanning new source files for Hebrew Unicode range characters... this makes the hard rule mechanically enforced."

That test exists: `tests/test_hebrew_leakage.py`, whose pattern is:

```python
_HEBREW_PATTERN = re.compile("[֐-׿]")   # tests/test_hebrew_leakage.py:23
```

This matches literal Hebrew *characters* in the source text. It does **not** match `\u05XX`-style Unicode escape sequences — those are plain ASCII in the `.py` file itself; the Hebrew character only materializes when Python parses the string literal at import time. Every one of the following files writes its Hebrew user-facing strings as escapes rather than literal characters, which silences the test while still producing Hebrew text at runtime:

| File | Escaped-Hebrew occurrences |
|---|---|
| `bot/app.py` | 65 |
| `api/routes.py` | 35 |
| `orchestrator/reasoning.py` | 27 |
| `history/query.py` | 4 |
| `agents/surveillance_agent.py` | 3 |
| `orchestrator/flows.py` | 3 |
| `bot/interactions.py` | 1 |

**138 occurrences total**, none of them in `messages/he.py`, `messages/en.py`, or `messages/catalog.py` — the single sanctioned location the project's own docs and `docs/file_catalog.md` name as authoritative ("Contains every fixed Hebrew user-interface string"). Concretely, e.g. `orchestrator/reasoning.py:1481`:

```python
if is_hebrew:
    return QuestionAnswer(f"נדרשים פרטים נוספים...")
return QuestionAnswer(f"I need a little more detail before I can answer. {selection.reason}")
```

**Why this matters beyond style:** these strings never pass through `messages/catalog.py`'s "strict catalog lookup, formatting, key parity, and placeholder validation" (per `docs/file_catalog.md`), so none of the guarantees the catalog exists to provide — key parity between languages, consistent phrasing, a single edit point for a wording change — apply to them. This is precisely the "two separate live bugs" class the HARD RULE was written to close off, and it has reopened at 138 sites without CI noticing.

**Fix:** extend `_HEBREW_PATTERN`'s scan to also flag `\u05[0-9a-fA-F]{2}` (and the Hebrew presentation-forms block) as raw text patterns, not just literal characters — or simpler, run the check against `ast.literal_eval`'d string constants so escapes are normalized before scanning. Then migrate all 138 occurrences into `messages/en.py`/`messages/he.py` keys.

---

### 🔴 2. Duplicate top-level function definitions — dead code shadowing live code, undetected by any tooling

An AST scan across every production package found two exact duplicate top-level definitions in the same file:

- **`orchestrator/reasoning.py`**: `answer_question_from_plan` is defined twice — lines **1461–1519** and **1572–1671**. Python silently keeps only the second; the first 59 lines are unreachable dead code.
- **`api/routes.py`**: `_now()` is defined twice — lines **60–61** and **121–122** (with a duplicated `if TYPE_CHECKING:` import guard immediately above the second copy, at lines 56–57 and 115–116). Harmless here since both bodies are identical, but it's the same symptom: two blocks of route-module content were concatenated without dedup.

The shadowed, dead copy of `answer_question_from_plan` (lines 1461–1519) is not just inert — it contains a latent bug that would resurface the moment anyone deletes what looks like "the duplicate": when `selection.status` reaches neither `"none"`, `"clarification"`, nor `"history"`, and `unknown_names` is empty, the function falls off the end with **no return statement**, implicitly returning `None` instead of continuing to run the parallel specialists. The live (second) copy fixes this by continuing into `_run_task`/`run_parallel_specialists`. A future edit that "cleans up the duplicate" by deleting the second definition and keeping the first would silently reintroduce this bug.

**Root cause:** no linter runs in CI or locally (see Recommendation #1). `ruff`'s `F811` ("redefinition of unused name") or `pyflakes` would catch both duplicates in under a second.

---

### 🟠 3. `bot/interactions.py` bypasses persistence and opens a raw SQLite connection from the bot process

```python
# bot/interactions.py:540-556
def get_open_approval_holds(db_path: str | None = None) -> list[str]:
    if db_path:
        try:
            import sqlite3
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            try:
                rows = conn.execute(
                    "SELECT event_id FROM held_events WHERE kind = 'approval' AND resolved = 0 ORDER BY created_at"
                ).fetchall()
                ...
            finally:
                conn.close()
        except Exception:
            pass
    return list(_OPEN_APPROVAL_HOLDS)
```

and its caller, `bot/app.py:527-528`:

```python
db_path = getattr(deps.loaded_profile, "db_path", None)
open_holds = interactions.get_open_approval_holds(db_path)
```

This violates three separate, explicit rules at once:

- **`docs/allowed_calls.md`**: "Bot reaches the application only through HTTP and never imports API internals" and "Raw SQL remains confined to persistence implementation modules."
- **`instructions.md` §1.3**, Database Engine Agnosticism: "No subsystem above the persistence layer may write raw SQL strings, use engine-specific exceptions, or reference SQLite directly."
- **`instructions.md` §3.3**, No Silent Failures: the bare `except Exception: pass` swallows every possible failure mode here — a locked file, a missing file, a corrupt database, a schema change — with zero logging, so the bot process has no signal that its fallback DB read is failing.

`tests/test_architecture.py` does not catch this because it only walks the Python import graph (`import`/`from ... import`); a same-process stdlib `sqlite3.connect()` call is invisible to it. This is architecturally significant even though the query is read-only: it means the bot process must have direct filesystem access to the API's database file and know its on-disk schema (`held_events`, `kind`, `resolved`, `created_at`) — a second, undocumented contract with the schema that `persistence/schema.py` owns, which will silently drift the next time that table changes shape, with no test to catch it (the `except: pass` guarantees the drift fails invisibly rather than loudly).

**Fix:** add an HTTP-exposed "list open approval holds" operation on the existing `/SYSTEM` or a dedicated route (the API and persistence layer already have everything needed — `held_events` is a persistence concept), and delete the direct-SQLite fallback path from `bot/interactions.py` entirely.

---

### 🟡 4. Two independently-maintained, already-drifted button→protocol mapping tables

`bot/app.py` (lines ~427–450, `BUTTON_PROTOCOL_HINTS`) and `api/routes.py` (lines 125–136, `KNOWN_BUTTON_PROTOCOLS`) each hardcode a `dict[str, str]` mapping literal Hebrew Telegram-button labels (with emoji prefixes) to protocol names, for the same underlying purpose: recognizing that an incoming message is really a keyboard button press so it can skip general LLM intent classification. They are maintained independently, in two different packages, with no shared constant and no test comparing them — and they have already diverged:

```python
# bot/app.py — BUTTON_PROTOCOL_HINTS (excerpt)
"📊 תמונת מצב כללית": "overall_situational_picture",
"🌐 תמונת מצב גזרתית כוללת": "overall_situational_picture",
"ℹ️ סטטוס גזרה": "overall_situational_picture",
```

```python
# api/routes.py — KNOWN_BUTTON_PROTOCOLS (excerpt)
"📊 תמונת מצב כללית": "overall_situational_picture",
```

`api/routes.py` recognizes only 1 of the 3 label variants that `bot/app.py`'s keyboard can actually send for the same protocol (and similarly for the camera/history-query buttons, where `bot/app.py` has 2–3 variants per protocol and `api/routes.py` has 1). If a Telegram user taps a button whose label is one of the variants only `bot/app.py` knows about, the api-side fast-path silently misses and the request falls through to full LLM intent classification instead — not a crash, but an unintended and unmonitored behavioral difference between two code paths that are supposed to agree, with no test asserting they do.

**Fix:** define the mapping once (e.g. in `profiles/` alongside the protocol declarations it labels, or a new small shared table under `messages/`) and have both `bot/app.py` and `api/routes.py` import it, plus a test asserting every key `bot/app.py` can emit exists in the api-side table.

---

### 🟠 5. Business/NLU logic embedded directly in the API layer, duplicating the Main Agent's canonical intent classification

`api/routes.py` contains multiple hand-written Hebrew keyword classifiers operating directly on raw message text, e.g.:

```python
# api/routes.py:139, 154, 167, 174
def _is_team_roster_query(text: str, prior_messages: tuple[dict, ...]) -> bool: ...
def _team_roster_view(text: str) -> str: ...
def _is_approval_policy_question(text: str) -> bool: ...
def _is_pending_report_cancellation(text: str) -> bool: ...
```

Each does `casefold()` + substring matching against hardcoded Hebrew phrase tuples. This conflicts with the architecture `README.md` itself documents: *"The Main Agent produces a validated intent decision"* — intent classification is supposed to be the orchestrator/Main-Agent's job (`orchestrator/reasoning.py::classify_intent`), not the API transport layer's. `docs/allowed_calls.md`'s dependency-direction rule is explicit that "API translates HTTP into orchestration calls" — it is not supposed to itself contain domain classification logic that can disagree with the orchestrator's own classifier for the same input.

This is also a second, independent source of the HARD RULE violation in Finding 1 (it's part of the 35 occurrences counted for `api/routes.py`), and it is unmaintainable by construction: a Hebrew speaker updating `messages/he.py` for wording consistency has no way to know these five functions in `api/routes.py` need the same update, because they aren't discoverable from the message catalog.

**Fix:** move this keyword-shortcut logic into `orchestrator/` (where `classify_intent` already lives) if it must exist as a fast-path optimization ahead of the LLM call, and route its literal strings through `messages/he.py`/`messages/en.py` like everything else.

---

### ⚪ 6. Governance documents contradict each other on package structure

`instructions.md` §5.1 (the repo's mandatory AI-authoring guidelines) lists `registries/` as one of the "pre-approved subsystem packages" the model is permitted to create files in. But:

- `README.md`: *"The former `registries` package is intentionally not preserved; area and event-type registries now belong to `profiles`."*
- `docs/allowed_calls.md` makes no mention of `registries` as a package.
- `tests/test_architecture.py:108` actively asserts `not (REPO_ROOT / "registries").exists()`.

`instructions.md` was evidently written before (or never updated after) the registries→profiles consolidation and is now actively wrong on this point. This is low-risk today (the test would catch anyone who actually acted on the stale instruction), but it is exactly the kind of contradiction that a future contributor — human or AI — reading `instructions.md` as "the mandatory engineering standards" document could act on in good faith and immediately fail CI on.

**Fix:** remove `registries/` from `instructions.md` §5.1's list, or add a note pointing to the current `profiles`-based design.

---

### 🟡 7. No linter or type checker anywhere in the toolchain

`requirements-dev.txt` (2 lines total) pins only `pytest>=8.0`. `.github/workflows/ci.yml` runs nine "Mission" pytest stages plus a couple of repo-hygiene checks (YAML validation, patch whitespace, working-tree cleanliness) but no static analysis step at all. This is the systemic explanation for Findings 2 and 4: a duplicate function definition (`F811`) and an unused parameter (`conversation_messages`, see Finding 9) are both classic `pyflakes`/`ruff` catches that a 1206-test, otherwise well-disciplined suite still cannot see, because they don't change any *observable* behavior a test happens to assert on.

**Fix:** add `ruff check` (fast, zero-config-friendly, catches F811/F401/F841 among others) as an early CI step. This is cheap and would have caught two of the findings in this report immediately.

---

## Integration & Communication Flaws

### 🟠 8. Documented conversation-memory feature is silently dropped on the "merged planner" question path

`README.md`'s "Conversation memory and event follow-ups" section documents that follow-up questions resolve against recent conversation turns. `api/routes.py:691-709` implements this by threading `prior_messages` through as `conversation_messages` for the merged-planner question path:

```python
# api/routes.py:699-709
if planner_mode == "merged" and message_plan is not None and message_plan.question_selection is not None:
    question_answer = answer_question_from_plan(
        ctx.main_agent, text, message_plan.question_selection,
        ctx.deps.registry, ctx.deps.history_query_service,
        max_fanout=optimization_policy.specialist_fanout,
        caller_sender_identity_filter=caller_sender_identity_filter,
        conversation_messages=prior_messages,
    )
```

But the live `answer_question_from_plan` (`orchestrator/reasoning.py:1572-1671`) accepts `conversation_messages` as a keyword parameter and **never reads it** anywhere in its body — not in the history-query branch, not in `_run_task`, not in `_build_compose_prompt`. Contrast with the sibling legacy path, `answer_question` (`orchestrator/reasoning.py:1693+`), which does thread `conversation_messages` into `_build_direct_lookup_prompt` and `_build_agent_selection_prompt` (lines 1709, 1727).

**Concrete effect:** a follow-up question routed through the merged planner (`planner_mode == "merged"`, which reads as the current/faster path given the naming) loses conversation continuity for sub-agent task text and history-query phrasing — a caller's "what's the status of that event now?" cannot resolve "that event" through this path's own agent-selection prompt the way the README describes, because the very parameter meant to carry that context is discarded.

**Test coverage:** `tests/test_orchestrator_reasoning.py` has zero references to `conversation_messages` — nothing exercises this parameter on `answer_question_from_plan`, so this is not just a live bug, it's an untested one.

**Fix:** either thread `conversation_messages` into the relevant prompt builders inside the live `answer_question_from_plan` (mirroring `answer_question`), or, if it's genuinely unused by design now, remove the parameter and its two call sites — but confirm via `caller_sender_identity_filter`-style tests either way, since right now this is unverified in either direction.

---

### 🟠 9. Bot-layer DB fallback duplicates and drifts from the persistence schema it doesn't own

Restating Finding 3 from an integration angle: `bot/interactions.py::get_open_approval_holds` hardcodes a raw SQL string (`SELECT event_id FROM held_events WHERE kind = 'approval' AND resolved = 0 ORDER BY created_at`) that must stay in lockstep with `persistence/schema.py`'s `held_events` table definition — a contract enforced nowhere. `persistence/schema.py` "owns immutable migration DDL and the current SQLite schema" per `docs/file_catalog.md`; nothing about that ownership model accounts for a second, independent SQL string living in the bot package. Any future migration that renames or restructures `held_events` will not fail loudly here — the bare `except Exception: pass` means it will fail by silently returning zero DB-backed holds and falling back to the process-local `_OPEN_APPROVAL_HOLDS` set, which does not survive a bot restart. Operationally this reads as "approvals occasionally go missing after a bot restart" with nothing in the logs pointing at the cause.

---

### 🟠 10. `X-Identity` authentication has no proof of possession for ordinary identities (carried forward, still current)

Re-verified against current `HEAD` (`api/request_boundary.py:97-118`). The `bot-service` identity is now protected by a keyed comparison against `BOT_SERVICE_KEY` (`_bot_service_key_matches`, lines 88-94) — this specific gap from `docs/SECURITY_AND_QA_AUDIT.md` §1.1 has been narrowed since that document was written. However, for every *other* identity, `authenticate()` still does nothing more than:

```python
user = persistence.read_user(identity)
if user is None:
    raise AuthenticationError(...)
return PermissionLevel[user["permission_level"].upper()]
```

Any caller who can reach the API port and knows (or guesses) a registered Telegram numeric ID — not inherently secret; visible in shared groups, forwarded messages, or via `cli.user_admin list` output leakage — can authenticate as that identity, including a commander, with an `X-Identity` header alone. **Status: still current and unresolved** for non-service identities; only the service identity's specific instance of this class of issue has been fixed.

---

### 🟡 11. Runtime AST rewriting of profile source files (carried forward, still current)

Re-verified: `protocols/repository.py:99-115` still parses a profile's `.py` file with `ast.parse`, locates the `PROTOCOLS` assignment, and rewrites the file on disk (`_write_protocols`) when a commander edits a protocol via `/Protocol`. This remains inconsistent with any read-only-filesystem deployment (containers, some PaaS targets) and keeps "protocol definition" as a hybrid of "immutable profile code" and "runtime-mutable state," which cuts against `instructions.md` §1.4's own "Profiles are immutable during runtime" principle — protocols are profile-declared but not, in fact, immutable. **Status: unresolved**, matches `docs/SECURITY_AND_QA_AUDIT.md` §1.3.

---

## Actionable Recommendations

Ordered by impact-to-effort ratio, not raw severity — several critical items here are also the cheapest to fix.

1. **Add `ruff` (or `flake8`) to `requirements-dev.txt` and as an early CI step.** Lowest effort, would have caught Findings 2 and 8 automatically, and prevents the same class of defect from recurring silently in the future. Do this first — it's a net-new safety net for everything else in this list.

2. **Fix the Hebrew-leakage test's blind spot (Finding 1), then migrate the 138 hardcoded strings.** Split into two steps: (a) extend `tests/test_hebrew_leakage.py`'s pattern to also catch `\uXXXX` escapes (a few lines — normalize via `ast.literal_eval` on string constants, or add a second regex for the escape form) and land that first so the safety net is real; (b) work through the 7 offending files one at a time, moving each string into `messages/en.py`/`messages/he.py` with a catalog key, in order of blast radius: `orchestrator/reasoning.py` and `api/routes.py` first (most occurrences, most-used code paths), `bot/app.py` next, then the remaining three files.

3. **Delete the dead `answer_question_from_plan` definition in `orchestrator/reasoning.py` (lines 1461–1519)** and the duplicate `_now`/`TYPE_CHECKING` block in `api/routes.py` (lines 115–116, 121–122). Zero behavioral risk (the first is unreachable), and removes the exact latent bug described in Finding 2 before anyone trips over it.

4. **Decide and fix `conversation_messages` in `answer_question_from_plan` (Finding 8).** This needs a product decision, not just a code fix: either the merged-planner question path is supposed to honor conversation follow-ups (thread the parameter through, add a regression test asserting a follow-up resolves correctly through this specific path) or it isn't (remove the dead parameter and update `api/routes.py`'s call site and any docs implying otherwise). Left as-is, it's a silent contradiction between what `README.md` promises and what one of two question-answering code paths actually does.

5. **Remove the direct-SQLite fallback from `bot/interactions.py::get_open_approval_holds` (Findings 3, 9).** Add a small API operation (route or extend `/SYSTEM`) that returns open approval-hold event IDs over HTTP, matching every other bot↔API interaction in the codebase, and delete the `sqlite3` import, the raw SQL string, and the bare `except: pass` along with it.

6. **Unify the two button→protocol mapping tables (Finding 4).** Move `BUTTON_PROTOCOL_HINTS`/`KNOWN_BUTTON_PROTOCOLS` into one shared, single-sourced table and add a test asserting `bot/app.py` and `api/routes.py` agree on every key — cheap insurance against the exact drift already observed.

7. **Relocate the Hebrew-keyword NLU helpers out of `api/routes.py` (Finding 5)** into `orchestrator/`, alongside `classify_intent`, if they're meant to stay as a fast-path shortcut; otherwise remove them in favor of letting the Main Agent classify every message, per the architecture `README.md` documents.

8. **Correct `instructions.md` §5.1's stale `registries/` reference (Finding 6).** One-line documentation fix; do it whenever someone is next in that file for another reason.

9. **Longer-term, track the two carried-forward findings already logged in `docs/SECURITY_AND_QA_AUDIT.md`** (`X-Identity` spoofing for non-service identities, runtime profile-file rewriting) — both are still valid per this pass's re-verification and are outside this report's new-findings scope, but neither should be considered "already handled" going forward; only the `bot-service`-specific instance of the auth issue has actually been fixed since that audit was written.

---

*Generated by static review of `HEAD=bad9be6` on `test-experiments`. No source files were modified in the course of this investigation. Line numbers refer to the exact revision above and may drift with subsequent edits.*
