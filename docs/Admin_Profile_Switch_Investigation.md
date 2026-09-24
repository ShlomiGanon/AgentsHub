# Admin Profile-Switch Investigation

Status: **investigation only — read-only**. No source code was changed to produce this document.
No process was started with real credentials for this investigation; a real stack was already
running on this machine (started by the operator before this investigation began — see §0), and
was only observed (read-only `curl` GETs, log/file reads), never driven through another switch.

---

## 0. What was actually on this machine when the investigation started

Before reading any code, `data/server_control/status.json` showed a **live, currently-running**
stack, started by the operator, not by this investigation:

```json
{
  "supervisor_pid": 10132, "state": "running",
  "profile_module": "profiles.firefighting", "api_port": 8906,
  "api_pid": 17132, "bot_pid": 3252, "bot_sim_pid": 11756, "last_error": ""
}
```

All four PIDs were confirmed alive (`Get-Process`). Log file timestamps reconstruct the operator's
actual session:

| Time | Event (from log file mtimes / content) |
|---|---|
| ~04:20–04:41 | `api-unified_test.stderr.log`/`bot-unified_test.stderr.log` actively growing — a **pre-existing `run_stack.py` process was still running `profiles.unified_test`**, imported into memory before that file was deleted in the prior implementation session. Deleting a `.py` file does not stop a process that already has the module loaded (`sys.modules`) — this process kept serving normally the whole time, unaffected by the file's removal on disk. |
| 04:41:34 | Supervisor (pid 10132) — same process, or a fresh one picking up `load_selected_profile()`'s new default (`profiles.standby_squad`, `config/server_control.py:133`) — starts `profiles.standby_squad` (`api-standby_squad.*`/`bot-standby_squad.*` logs begin). |
| 04:46:14–18 | Switch **standby_squad → firefighting**. `api-firefighting`/`bot-firefighting`/`bot-sim-firefighting` processes start (pids 17132/3252/11756). |
| 04:52 (status.json `updated_at`) → now | `state: "running"`, `last_error: ""` — the supervisor believes the switch succeeded cleanly. |

**Live, read-only checks performed just now** (plain `curl -s -o /dev/null -w ... `, no auth, no
mutation):

```
GET http://127.0.0.1:8906/admin/login  → HTTP 200   (0.03s)
GET http://127.0.0.1:8905/admin/login  → connection failed (exit 7 / "couldn't connect")
```

**The admin panel for the *currently* active profile (firefighting, port 8906) is actually up and
serving correctly right now.** The old profile's port (8905) correctly refuses connections — the
old process was properly stopped and did not leak a listener. This rules out "the new process
never starts" as the explanation for *this specific* observed run, and points the investigation at
the **client-side redirect**, not server-side startup — confirmed in §1.

---

## 1. Confirmed root cause: a race condition in the wait-page's redirect probe

### 1.1 The mechanism (file/line evidence)

`api/admin.py:1407-1412`:

```python
def _restart_page(port: int):
    host = request.host.split(":", 1)[0]
    target_url = f"{request.scheme}://{host}:{port}/admin/server"
    fallback_url = f"{request.scheme}://{host}:{ctx.loaded_profile.api_port}/admin/server"
    candidates = list(dict.fromkeys((target_url, fallback_url)))
    return _render(_SERVER_WAIT_TEMPLATE, target_url=target_url, candidate_urls=candidates)
```

`ctx.loaded_profile.api_port` here is **the port of the process handling *this* request** — i.e.
the *old* profile's port, captured before the switch has even been submitted. `switch_profile()`
(`api/admin.py:1414-1433`) calls this with `selected.api_port` (the *new* profile's port), so:

- `candidates[0]` (`target_url`) = new profile's port (correct final destination)
- `candidates[1]` (`fallback_url`) = **the current, about-to-be-killed process's own port**

This already correctly anticipates that switching changes the port (the Profile Split Plan's §7.1
assumption that ports stay the same was wrong about the *plan*, but the *code* — written after —
already accounts for it). The bug is in how the two candidates are raced against each other.

`api/admin.py:978`, the wait page's own script:

```js
(function(){ const candidates={{ candidate_urls|tojson }};
  async function probe(){
    for(const url of candidates){
      try{ await fetch(url, {mode:'no-cors', credentials:'include', cache:'no-store'});
           window.location.href=url; return; }
      catch(error){}
    }
    setTimeout(probe, 1500);
  }
  setTimeout(probe, 3000);
})();
```

With `mode: 'no-cors'`, `fetch()` **resolves** (does not throw) for *any* completed HTTP exchange,
regardless of status code, because the response is cross-origin (different port = different
origin) and therefore opaque to JavaScript — the code cannot see or check the status. It only
**rejects** on an actual network-level failure (connection refused, DNS failure, timeout). The
loop tries `candidates` **in order** and navigates to the **first one that answers at all**,
correct destination or not.

### 1.2 Why this is a real race, not a theoretical one

Actual measured timings, from the code the operator's own run just exercised:

- **Supervisor command latency**: `run_stack.py:186-214`, the main loop calls `consume_command()`
  once per iteration and sleeps `time.sleep(1)` between iterations — up to ~1s before a submitted
  `switch_profile` command is even picked up.
- **`stop()`** (`run_stack.py:136-157`): `terminate()` on up to 3 processes, then `wait(timeout=10)`
  each, sequentially. Normally fast (well under a second per process for a cooperative Python
  process), but not instant and not bounded from below.
- **`start()`** (`run_stack.py:94-134`): unconditional `time.sleep(3)` after launching `api.app`,
  then `time.sleep(1)` after `bot.app`, then (both profiles declare `SIMULATOR_PORT`) another
  `time.sleep(1)` after `bot.simulator_app` — **minimum 5 seconds** of sleep alone, before
  `api.app` has necessarily finished binding Flask, and *before* accounting for `api/app.py`'s own
  startup work (profile import, DB init, `ensure_simulation_entities`, and — per `README.md:99-103`
  — one real minimum provider warmup call per configured model, which is real network I/O with no
  fixed upper bound).

So end-to-end, "old process fully down" happens well before "new process fully up" — there is a
guaranteed window where **both** candidate URLs fail (fine, the retry loop handles that), but
there is *also* a window, before the old process's `stop()` has actually completed, where the
**old port is still alive** while the switch is already in flight. The wait page's **first probe
fires at a fixed `t=3000ms`**, independent of the server-side state machine — squarely inside the
same few-second window the supervisor itself needs. If the probe's `for` loop reaches
`fallback_url` (the dying old port) while it is still momentarily accepting connections, `fetch()`
resolves, and the browser is redirected **back to the port that is seconds away from being
killed**. The wait page's retry script only exists on the wait page itself — once
`window.location.href` navigates away to `.../admin/server` on the old port, that page is the
**plain** `_SERVER_TEMPLATE` (`api/admin.py:1383-1405`), which has no such script. When the old
process is then actually terminated, the browser is left on a dead port with nothing left to bring
it forward — matching "the admin panel does not come up."

### 1.3 A second way the same mechanism produces the same symptom

The `fallback_url` design is not accidental — read literally, it is clearly meant to handle
`switch()`'s own rollback path (`run_stack.py:159-177`): if the *new* profile's `start()` raises,
`switch()` catches it, restores `self.profile_module = previous`, and calls `self.start()` again
for the **old** profile — i.e. the supervisor really can end up back on the old port after a
"switch." In *that* scenario, `fallback_url` is the genuinely correct destination. But the wait
page has no way to distinguish "old port answering because a rollback stabilized it" (correct,
should redirect there) from "old port answering because it just hasn't died yet" (wrong, about to
be a dead end) — both look identical to a `no-cors` probe. So the same code path is both the
intended safety net for a real rollback *and* the mechanism that can strand the browser mid-switch;
whichever candidate happens to answer first wins, with no coordination against the supervisor's
actual state (`status.json`, which the wait page never reads).

**This is the confirmed root cause of "admin panel does not come up" after a switch**: not a
server-side failure to start (§0's live evidence rules that out for the run that was inspected),
but a client-side redirect race that can strand the browser on a port that is about to go, or has
already gone, dead — with no further automatic recovery once it happens.

---

## 2. Hypotheses investigated and ruled out (with evidence)

| # | Hypothesis (from the task's scope list) | Verdict | Evidence |
|---|---|---|---|
| 3 | `load_profile`/`validate_profile` fails for one of the profiles in the real `run_stack` path (env vars, `DB_PATH` creation, `ensure_simulation_entities`, `AREAS`/`EVENT_TYPES`, catalog keys) | **Ruled out for the observed run.** | Both `data/standby_squad/` and `data/firefighting/` exist with real, current `.db`/`.db-wal`/`.settings.json` files (`ls -la` timestamps 04:41–04:56). `api-standby_squad.stdout.log` and `api-firefighting.stdout.log` both show `Serving Flask app 'app'` / `Debug mode: off` — Flask bound successfully both times. `ADMIN_USERNAME`/`ADMIN_PASSWORD`/`ADMIN_SESSION_SECRET` are all set in `.env`, so the `/admin` blueprint registers in every spawned process (inherited identically via `env = os.environ.copy()`, `run_stack.py:98`). Cannot fully rule out a *slower or failing* model-provider warmup call under different network conditions (§3) — that's a residual risk, not a ruled-out one. |
| 4 | Telegram `409 Conflict` from two bot processes sharing one token, or a bot crash loop blocking the API/admin | **Ruled out as currently occurring; the underlying risk is real.** `.env`'s `BOT_TOKEN` and `FIREFIGHTING_BOT_TOKEN` are confirmed **byte-identical** (same 46-char value) — both profiles really do share one Telegram bot. But `run_stack.py:159-177`'s `switch()` is strictly sequential (`self.stop()` *then* `self.start()`), so the old bot's long-poll connection is torn down before the new one opens one for the same token — no two `getUpdates` pollers for the same token ever run concurrently. `bot-standby_squad.stderr.log`/`bot-firefighting.stderr.log` show clean, periodic `getUpdates` calls with no `409`/`Conflict` text anywhere. Also: the bot process is architecturally separate from the API process that serves `/admin` — even a genuinely crash-looping bot would not, by itself, prevent Flask from serving `/admin` routes on the API process. |
| — | (found independently, not on the task's list, but directly relevant) The bot's own service calls (`/Notifications`, `/TeamStatus/AttendanceCheck`) are failing with `401 invalid_input: הזהות 'bot-service' אינה רשומה במערכת.` for `profiles.firefighting`, continuously, right now | **Real, but a separate, already-known issue — not the admin-panel cause.** This is the same `BOT_SERVICE_KEY` process-environment-staleness class of bug diagnosed and reported in an earlier session for `profiles.standby_squad`; it is now visible again because the current supervisor (pid 10132, started 04:41:34) inherited whatever `BOT_SERVICE_KEY` was in its own launching shell's environment, and every subsequent switch propagates that same (possibly stale) value to each new `api`/`bot` pair via `env.copy()`. It degrades the bot's *background* functions (notifications, attendance checks) but has no code path into `/admin` — the admin panel authenticates via session login (`ADMIN_USERNAME`/`ADMIN_PASSWORD`/`ADMIN_SESSION_SECRET`), never `BOT_SERVICE_KEY`. Flagged for awareness; not re-investigated in depth here since it was already diagnosed. |
| 5 | Admin session/cookie broken across the restart (`ADMIN_SESSION_SECRET`, cookie bound to a port, rate limiting/lockout) | **Ruled out.** `app.secret_key = admin_config.session_secret` (`api/app.py:250`) is the same `ADMIN_SESSION_SECRET` value in every spawned process (same inherited env), so a session cookie signed by the old process verifies fine on the new one. HTTP cookies are never port-scoped (RFC 6265 — `Domain`+`Path` only), and no `SESSION_COOKIE_DOMAIN` is set (`api/app.py:250-252` sets only `HTTPONLY`/`SAMESITE=Lax`), so the browser sends the same cookie to the new port automatically. `SameSite=Lax` does not block this — the wait page's `window.location.href` navigation is a top-level GET, and same-host-different-port is not "cross-site" for `SameSite` purposes at all. `_require_session()` (`api/admin.py:1106-1117`) checks only a session flag and an inactivity timer, nothing port/IP-specific. `LoginRateLimiter`'s lockout state is in-memory per process (confirmed by reading its own docstring, `api/admin.py:1119` area) and simply resets on every restart — not a "doesn't come up" cause, just a note that a pre-restart lockout doesn't carry over (arguably desirable, not a bug). |
| 6 | Leftover references to a removed profile (`demo`, `unified_test`, `friendly_forces`, `sub_agent_*`) in the switch path, or stale `data/server_control` state | **Ruled out for the current repo state.** `grep` of `run_stack.py`, `config/server_control.py`, `api/admin.py` for the five retired module paths returns nothing. `data/server_control/selected_profile.json` currently holds `{"module_path": "profiles.firefighting"}` — a real, loadable profile. (The *stale, still-running unified_test process* from §0 was real, but it predates this repo's current deletions and is a leftover **running process**, not a leftover **reference in the switch code** — `discover_profiles()` itself would already refuse to switch *to* `profiles.unified_test` today, since it no longer appears in its scan of `profiles/*.py`.) |
| 2 (ports) | "Plan §7.1 assumed same-port switch" | **Confirmed wrong, exactly as flagged** — but the *implementation* (unlike the plan) already accounts for the port change via `_restart_page`/`target_url`/`fallback_url`. The bug is not "the code assumes the port doesn't change" — it's that the mechanism built to *handle* the port change has a race in it (§1). |

---

## 3. What could not be verified, and what to run or paste

1. **Whether the race in §1 is what the operator actually saw.** The task's symptom section was
   left as the literal placeholder (`[Add here: which direction I switched, what I see...]`) — no
   specific symptom text, screenshot, or browser console output was provided. Everything in §1 is
   reconstructed from code inspection plus the real timing evidence on this machine (log
   timestamps, live port probes), not from a direct report of what the browser showed. **Please
   paste**: the exact URL the browser was sitting on when it appeared stuck, and whether it showed
   a "can't connect" error, a blank page, or the wait page frozen with its spinner/link still
   visible.
2. **Whether a slow or failing model-provider warmup call (§2, hypothesis 3's residual risk) has
   ever actually been the trigger.** `api/app.py`'s startup makes one real, billed, minimum
   provider call per configured model before opening the listener (per `README.md:99-103`); this
   investigation did not have a way to safely simulate "provider is slow/down right now" without
   using real model keys, which the task's rules exclude. **Please paste**: `api-firefighting.stderr.log`
   / `api-standby_squad.stderr.log` from around the *specific* switch attempt where the panel didn't
   come up (if different from the one inspected here), specifically any lines between the process
   start and the first `Serving Flask app` line.
3. **Whether the browser's own console/network tab shows the probe's `fetch()` calls actually
   landing on the old port and resolving.** This would be the most direct confirmation of §1's
   mechanism. Not something this read-only, no-browser investigation could observe. **If you can
   reproduce it again**: open the browser dev tools' Network tab before clicking "switch", and
   capture what the wait page's two `fetch()` calls actually hit and in what order.
4. **Whether the currently-running stale `profiles.unified_test` process (§0) is still active on
   this machine right now**, and if so, what if anything is still pointed at it. This is a
   leftover-process hygiene question, not a code bug, but worth confirming/cleaning up separately
   from this investigation (a stale process from a deleted profile could confuse a future switch
   attempt by occupying port 8905 unexpectedly). **Please confirm**: whether you still have an old
   terminal window open running the original `run_stack.py`/`api.app profiles.unified_test`
   invocation from before the profile split, and close it if so.

---

## 4. Proposed fix plan (for approval — not implemented)

### 4.1 Fix the redirect race itself

**File**: `api/admin.py` (`_restart_page`, `_SERVER_WAIT_TEMPLATE`, `switch_profile`).

- Stop racing `target_url` against `fallback_url` as equally-valid "whichever answers first"
  candidates. Only ever navigate to `fallback_url` when there is positive evidence of an actual
  rollback (e.g. the wait page instead polls `GET /admin/status`-style JSON — reusing
  `read_server_status()`/`config/server_control.py:87-93`, already exposed data — and checks
  `profile_module`/`state` in the response body to decide whether the *target* profile is the one
  that ended up running, versus the *previous* one). This makes the decision based on the
  supervisor's actual recorded state, not "which port happened to answer a probe first."
- Concretely: poll `status.json`'s content (via a small JSON endpoint, not an opaque `no-cors`
  fetch) until `profile_module == module_path` (the one just requested) and `state == "running"`,
  *then* redirect to that profile's `api_port` — and only fall back to the previous profile's port
  if `status.json` reports `last_error` naming a rollback to it. This removes the opaque-response
  ambiguity entirely (a same-origin-capable status check, e.g. proxied through whichever port is
  still up, can read the actual JSON body and state, not just "did something answer").
- Needs care: at the moment the wait page loads, *both* the old and new profiles are candidate
  hosts for that status JSON, so the status check itself needs the same "don't trust a stale
  responder" property — likely by including `profile_file_hash`/`updated_at` in the comparison, or
  simply by only trusting a response that explicitly names the *requested* profile module in its
  body.

### 4.2 Make the wait page not silently strand the browser

- Add a hard cap (e.g. 60s) after which the wait page shows an explicit "still not reachable, retry
  manually" message with both candidate links, instead of retrying `setTimeout(probe, 1500)`
  forever with no visible feedback if every attempt keeps failing.
- If §4.1 lands, a stranding is structurally much harder (the redirect only fires once the state
  check confirms the *right* profile is actually running) — this is a defense-in-depth addition,
  not a substitute for §4.1.

### 4.3 Tests to add

- **New**: a route-level test for `POST /admin/server/profile` (mirroring existing `test_api_admin.py`
  fixtures) asserting `_restart_page`'s rendered `candidate_urls` and — after §4.1's rework — that
  the page's status-polling logic only ever settles on the *requested* profile's port, never a
  stale responder, using a fake/mocked status source the test controls (no real subprocess).
- **New**: a `run_stack.py`-level integration test that actually spawns two lightweight real
  subprocesses (or reuses the existing fake-profile pattern in `tests/test_server_control.py`, but
  with real `start()`/`stop()` instead of monkeypatched no-ops) and asserts there is never a moment
  where a `GET` to the *old* port succeeds after `switch()` has begun — closing the exact gap
  identified in §5 below. This is the test that would have caught this bug: today nothing exercises
  real process start/stop timing at all.
- Cover the `BOT_SERVICE_KEY` propagation issue (§2's independent finding) separately, if this
  investigation's flag prompts a fix — out of scope for *this* plan, noted only for completeness.

### 4.4 Operational note (not a code fix, flag only)

- `BOT_TOKEN` and `FIREFIGHTING_BOT_TOKEN` are currently the same real token in `.env`. This
  doesn't cause a `409` today only because `switch()` is strictly sequential — it is fragile
  (depends on `stop()` always fully completing before `start()` begins, which is currently true by
  construction but would break silently if that ordering ever changed). Recommend either accepting
  this as a documented constraint (`docs/operator_guide.md` already documents the two separate env
  vars; could add an explicit "must be different bots for concurrent/Option-B use" note) or getting
  a second real bot token for `FIREFIGHTING_BOT_TOKEN`. Your call — not proposing a code change for
  this.

---

## 5. Answer to item 7: why the existing test passes while the real flow fails

`tests/test_server_control.py::test_failed_profile_switch_rolls_back_and_keeps_error_for_admin`
monkeypatches `StackSupervisor.start`/`.stop`/`._status` to no-op fakes (`run_stack.py`'s real
`subprocess.Popen`, `time.sleep(3)`/`sleep(1)`, and real port binding never execute). It correctly
proves the **Python-level rollback bookkeeping** (`self.profile_module` restored, `last_error` set,
`save_selected_profile` not called on failure) — genuinely useful coverage, but of a different layer
entirely. A repo-wide search (`grep -rln "switch_profile\|_restart_page\|SERVER_WAIT\|server/profile" tests/`)
found **zero** other references — nothing in `tests/test_api_admin.py` or anywhere else calls the
real `POST /admin/server/profile` route, renders `_SERVER_WAIT_TEMPLATE`, or exercises `_restart_page`'s
`target_url`/`fallback_url` construction at all. The gap is exactly the layer §1's bug lives in: the
HTTP route + the browser-facing redirect page + the *timing* between the supervisor's real
subprocess lifecycle and the wait page's fixed-interval polling — none of which a mocked-`start()`
unit test can ever observe, since the whole bug is about what happens during the seconds a *real*
`start()`/`stop()` takes.

---

## 6. Fix applied

Implemented as requested, with one deliberate departure from §4.1's own proposal.

### 6.1 What changed, and why the simpler sequence beats this doc's original §4.1 proposal

§4.1 originally proposed a new JSON status-polling endpoint (`read_server_status()` exposed to the
browser, checked for `profile_module`/`state` matching the request) to remove the ambiguity of an
opaque `no-cors` response. The approach actually implemented is simpler and was specified directly:
**never race the two candidate URLs; instead wait for the old port to stop answering, then wait
for the new port to start answering, then navigate only there.** This is strictly better than the
original proposal for two reasons found while implementing it:

- It needs no new backend endpoint, route, or JSON contract — the existing opaque `no-cors` probe
  is already sufficient once it's used to detect a state *transition* (down → up) instead of "did
  anything answer."
- It also correctly covers `reset_server()` (`api/admin.py:1435-1452`), where `old_url == target_url`
  (a reset restarts the *same* profile on the *same* port) — a scenario §4.1's original proposal
  didn't explicitly address. Requiring an observed down-then-up transition means reset can never
  declare success by observing the pre-restart process still answering; the JSON-status approach
  would have needed extra handling (e.g. an `updated_at`/hash comparison) to achieve the same
  guarantee for the same-port case.

### 6.2 Files changed

- **`api/admin.py`**
  - `_restart_page()` (was ~1407-1412): now builds `old_url` (the port serving the current
    request) and `target_url` (the destination port) and passes both, plus `timeout_ms`/`poll_ms`,
    to the template — no more `candidates` list.
  - `_SERVER_WAIT_TEMPLATE`: rewritten. The page now shows a normal "restarting" message plus a
    hidden `#wait-timeout` block; the script runs a strict two-phase `tick()` loop — phase
    `'old-down'` polls `oldUrl` until a probe *rejects* (confirms the old process actually
    stopped), then phase `'new-up'` polls `targetUrl` until a probe *resolves*, and only then sets
    `window.location.href = targetUrl`. A `deadline` (`Date.now() + timeout_ms`) checked on every
    tick reveals `#wait-timeout` — two plain links to `target_url` and `old_url` — instead of
    retrying forever.
  - New module constants `_RESTART_POLL_MS = 1500`, `_RESTART_TIMEOUT_MS = 60000` (60s, per this
    doc's own §4.2 recommendation).
- **`messages/en.py` / `messages/he.py`**: added `admin.server_restart_timeout_title`,
  `_help`, `_target_link`, `_previous_link` (English/Hebrew parity verified); removed the now-unused
  `admin.server_retry_link` (the old single-link wording no longer matches the two-link timeout UI).
- **`tests/test_api_admin.py`**: five new tests — the rendered page targets only the new profile's
  port for auto-redirect; the old race-both-candidates shape (`candidate_urls`, the `for` loop over
  `candidates`) is gone and the sequential `'old-down'`/`'new-up'` markers are present, in that
  order; the timeout section (`#wait-timeout`, `deadline`, both links) is present; and `reset_server()`'s
  same-port case still goes through the same two-phase wait. A new `_make_supervisor_available()`
  helper wires up `AGENTSHUB_SUPERVISOR`/`AGENTSHUB_CONTROL_DIR` so these tests reach
  `_restart_page()` at all — previously nothing did (§5).
- **`tests/test_server_control.py`**: unchanged — it tests `StackSupervisor`'s own rollback
  bookkeeping with `start`/`stop` mocked out, a different layer than this fix, and none of the
  fix's changes affect what it asserts.
- **`docs/operator_guide.md`**: corrected the "switch in place" paragraph, which repeated the same
  wrong same-port assumption this investigation flagged (§2, row "ports"); documented the new
  wait/timeout behavior; added a note that switch-in-place tolerates a shared bot token (sequential
  stop-then-start) but the advanced concurrent mode does not (§4.4's operational note).
- **Not changed, per the task's constraints**: `run_stack.py` (switch semantics untouched — `stop()`
  then `start()`, same as before), no profile files touched.

### 6.3 Verification

Full `pytest` suite: **1474 passed**, 0 failed, after all of the above (including the file-catalog
entry for this document itself, added retroactively since it was created in the prior,
investigation-only turn). No process was started with real credentials to verify this fix — the
new tests exercise `_restart_page()`/`switch_profile()`/`reset_server()` through Flask's in-process
test client only, asserting on the rendered page's structure and content, not a real browser or a
real subprocess switch.
