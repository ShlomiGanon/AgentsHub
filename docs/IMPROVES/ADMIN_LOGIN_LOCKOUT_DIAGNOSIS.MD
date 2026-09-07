> **Resolved.** After reviewing this diagnosis, the user made a deliberate design decision:
> per-IP lockout behaved identically to a global lock in every situation that actually mattered
> for this single-shared-credential deployment (see Step 2's reproduction), so **global lockout
> is now the intended design**, not a bug to route around. This is implemented in `api/admin.py`
> (`LoginRateLimiter` is now global-scoped, timestamp-only storage), along with fixes for both
> Step 3 message-mismatch bugs, the Post/Redirect/Get fix for symptom 1, and the minutes/hours
> remaining-time display from Step 5 — see `docs/progress.md` for the implementation entry and
> `tests/test_api_admin.py`'s "LoginRateLimiter: global lockout mechanism" section for coverage.
> Everything below is kept as the historical record of the investigation that led to this
> decision; the per-IP scoping it describes no longer reflects the code.

# AgentsHub — Diagnose: Admin Login Shows "Wrong Username or Password" With No Action Taken

**Headline finding: neither hypothesis (a) nor (b) as literally stated —
a third, code-confirmed explanation for each symptom, found by reading
`api/admin.py` and then empirically reproducing both flows against the
real Flask app via its test client (no code changed; see Method).**

- The lockout mechanism is **per-source-IP**, not global-with-no-key and
  not per-session. This is a *deliberate, already-documented* design
  choice (`api/admin.py:126-137`), not an oversight.
- **The "you are locked out" message with zero actions taken (symptom 2)
  is real and reproduced.** It happens whenever two different visitors
  share what the server sees as the same source IP — one visitor's
  failed attempts lock out the *next*, completely unrelated visitor's
  very first, correct-credentials attempt. Reproduced directly below
  (two independent Flask test-client sessions, same process → same
  observed IP, exactly the scenario a shared office network, campus
  Wi-Fi, mobile carrier CGNAT, or — most likely in this deployment — an
  unconfigured reverse proxy in front of Waitress would produce for real
  users). This is a genuine defect in the *practical* scoping, even
  though the code technically has a key (IP) and isn't literally global.
- **The "Wrong username or password" message with zero actions taken
  (symptom 1) is architecturally different and NOT explained by the
  lockout mechanism at all.** That message is rendered from Flask's
  `flash()`, which is stored in the *browser's own signed session
  cookie* — it is private per-browser and cannot leak from one visitor's
  browser into another's. Confirmed by reproduction: a brand-new session
  with zero cookies never sees this message on GET, regardless of what
  other sessions on the same IP have done. The most likely real-world
  explanation is client-side: this app renders the failed-login result
  directly from the POST handler instead of redirecting after POST (no
  Post/Redirect/Get), so a browser "refresh" on that result page is
  actually **re-submitting the same failed POST**, not issuing a fresh
  GET — see Step 2 for the exact code path and why the user's own
  description ("clean URL, no `?error=`") does not rule this out for an
  app that never used a query-string error flag to begin with.
- **Step 3's message-mismatch bug is also confirmed, and is worse than
  "the generic message can show while locked out": there are actually
  two separate display gaps**, both reproduced below:
  1. The exact POST attempt that *crosses* the lockout threshold shows
     the **generic** "Wrong username or password" message, not the
     lockout message — the code decides which message to flash before
     it knows that this failure just triggered a lockout.
  2. A plain **GET** reload of `/admin/login` while genuinely locked out
     shows **no message at all** (not the lockout message, not the
     generic one) — the GET handler never consults the rate limiter.
- **Step 5's minutes/hours display was not added — this is a read-only
  diagnosis pass, no code was changed.** The current message is
  described in Step 5 below; it already avoids raw seconds by luck of
  its rounding, but never scales to hours and is a hardcoded English
  literal outside the project's message catalog.

**Method:** static code reading (`api/admin.py`, `api/app.py`) plus a
throwaway reproduction script run against the real Flask app through its
existing Werkzeug test-client harness (same pattern as
`tests/test_api_admin.py`, built from `tests/api_fakes.build_context`) —
an in-process, isolated app instance, nothing production-sensitive
touched, no server actually run on a port, no lasting local files
written outside the OS temp dir. The script was written to this
session's scratchpad directory, run, and its output captured verbatim
below; it was not added to the repository and no repository file was
modified in this pass.

---

## Step 1 — The actual lockout/attempt-tracking mechanism

**Location:** `api/admin.py`, class `LoginRateLimiter` (lines 126–172),
instantiated once per admin blueprint at `api/admin.py:612`
(`rate_limiter = LoginRateLimiter(config.login_max_attempts,
config.login_lockout_minutes)`) — one instance per running process,
long-lived for the process's lifetime (module-level closure state inside
`build_admin_blueprint`, not per-request).

**Storage mechanism:** plain in-memory Python dicts, guarded by a
`threading.Lock` — not a database table, not a cache service:

```python
# api/admin.py:139-144
def __init__(self, max_attempts: int, lockout_minutes: int):
    self._max_attempts = max_attempts
    self._lockout_seconds = lockout_minutes * 60
    self._lock = threading.Lock()
    self._failure_counts: dict[str, int] = {}
    self._locked_until: dict[str, float] = {}
```

**The key:** `_client_source()`, `api/admin.py:598-599`:

```python
def _client_source() -> str:
    return request.remote_addr or "unknown"
```

This is passed as `source` into `locked_out_for(source)`,
`record_failure(source)`, and `record_success(source)` (all called from
the `login()` view, `api/admin.py:656-690`). So the answer to the
scoping question:

- **Not per-username** — correct per the user's framing; there's only
  one shared identity, so this was never a meaningful axis here.
- **Not per-session/cookie** — the rate limiter never reads `session` at
  all.
- **Not a single global counter/flag with no key** — there **is** a key,
  and it's `request.remote_addr` (Flask/Werkzeug's view of the TCP peer
  address, i.e. whatever the WSGI server reports as
  `environ["REMOTE_ADDR"]`).
- **It is per-IP.** This is documented, deliberately, in the class's own
  docstring (`api/admin.py:126-137`):

  > "Tracked per source IP, not per username: ADMIN_USERNAME is one
  > shared credential for every commander using this panel (there's no
  > per-person admin account), so a per-username lockout would let a
  > single hostile — or even accidental — bad login from anywhere lock
  > out every legitimate commander at once. Per-IP contains a lockout to
  > whoever is actually failing, at the cost of not stopping a
  > distributed attempt from many source IPs — an accepted trade-off for
  > a small deployment's admin panel, not a public-internet-scale
  > defense."

  Note what this documented trade-off does and doesn't cover: it names
  the risk of an *attacker* spreading attempts across many IPs to evade
  lockout. It does **not** name the opposite and, for this bug report,
  more relevant risk: many **different legitimate people** sharing what
  the server sees as *one* IP (NAT, corporate/campus network, carrier
  CGNAT, or a reverse-proxy hop that collapses every client to the
  proxy's own address) getting bundled into a single lockout bucket.
  That gap is exactly what Step 2 reproduces below.

Reset behavior: `locked_out_for()` (lines 146-158) lazily evicts expired
entries on read; there is no separate cleanup thread. All state is lost
on process restart (in-memory only, consistent with this module's
already-stated single-process assumption, same as `SingleInstanceLock`
elsewhere in the codebase per the docstring).

---

## Step 2 — Why a "fresh" page load shows an error with nothing submitted

This required separating the **two symptoms** the user reported, because
they turn out to have different mechanisms — one really is a server-side
scoping defect, the other most likely isn't a server bug at all.

### Reproduction setup

A scratch script (not committed) built two independent `test_client()`
instances from the *same* running `Flask` app object — i.e. two
completely separate cookie jars/sessions (simulating two different
browsers/visitors), sharing the same process and therefore, like every
Werkzeug test client, the same reported `remote_addr` (this is also
exactly what a shared office network, campus Wi-Fi, carrier-grade NAT,
or an unconfigured reverse proxy produces for two *real, physically
different* visitors in production). `ADMIN_LOGIN_MAX_ATTEMPTS=3`,
`ADMIN_LOGIN_LOCKOUT_MINUTES=15`.

### Captured output (verbatim)

```
=== Client A (its own cookie jar) ===
A: fresh GET /admin/login: status=200 -> NO error message shown
A: failed POST #1 (below threshold): status=200 -> GENERIC wrong-password message
A: failed POST #2 (below threshold): status=200 -> GENERIC wrong-password message
A: failed POST #3 (crosses lockout threshold on this very call): status=200 -> GENERIC wrong-password message
A: GET /admin/login while locked out (plain reload): status=200 -> NO error message shown
A: POST with CORRECT password while still locked out: status=200 -> LOCKOUT message

=== Client B: brand-new cookie jar, zero prior attempts of its own ===
B: fresh GET /admin/login (never touched this app before): status=200 -> NO error message shown
B: POST with CORRECT credentials, first attempt ever from this session: status=200 -> LOCKOUT message
```

### What this proves, symptom by symptom

**Symptom 2 — "you are locked out, try again in X" with zero actions
taken: CONFIRMED, and reproduced exactly.** Client B never submitted a
single failed attempt — its *very first* POST, with the fully correct
username and password, was rejected with the lockout message, purely
because Client A (a different browser/session, sharing the same
observed IP) had already tripped the counter. This is symptom (a)'s
*practical effect* even though the code isn't literally a no-key global
flag — per-IP scoping degenerates into the same user-visible behavior
the instant more than one real visitor shares an apparent source
address. In this codebase's actual deployment shape (Waitress behind
whatever sits in front of it — `docs/PRODUCTION_READY.md` lines 183-191
still lists "is there a reverse proxy in front" as an open decision, and
`api/admin.py`'s own module docstring, lines 22-32, recommends adding
one for TLS), the most likely trigger is exactly that: if a reverse
proxy terminates TLS and forwards to Waitress without the proxy's own
address being distinguished from real clients (no `X-Forwarded-For`
trust, no `ProxyFix`-equivalent — confirmed absent: `grep` for
`ProxyFix`/`X-Forwarded-For`/`remote_addr` across the codebase turns up
only the one line at `api/admin.py:599` reading `request.remote_addr`
directly), then **every single visitor, from anywhere, is seen by this
code as one IP: the proxy's.** At that point per-IP scoping *is*
functionally the no-key global flag the user hypothesized — same
symptom, different mechanical route to get there. This is worth
confirming directly against the actual deployment (see Step 4).

**Symptom 1 — "Wrong username or password" with zero actions taken: NOT
explained by the lockout mechanism, and not explained by a stale
session flash either.** Two things rule those out directly:

- The generic message is only ever produced by `flash()` inside the
  `login()` POST branch (`api/admin.py:689`). Flask's `flash()` writes
  into the *session cookie*, which is private to one browser — it
  physically cannot appear in a different browser's response. Client
  B's fresh GET, and even Client B's fresh session sharing Client A's
  IP, never shows it — only Client A's own session, after Client A's own
  failed POSTs, ever does.
- The literal "stale flash not cleared on GET" bug (hypothesis (b)) does
  **not** reproduce here: Client A's own GET, immediately after its own
  failed POSTs (`"A: GET /admin/login while locked out (plain reload)"`),
  shows **no** message at all — not even a leftover generic one. Flask's
  `get_flashed_messages()` pops the flash out of the session the moment
  it's rendered (it's rendered once, in the same response the POST
  itself returns, at `api/admin.py:690`), so by the time a later GET
  happens the session's flash queue is already empty. This part of the
  code is not buggy.

Given both of those, the most likely real explanation for what the user
experienced is **browser-side POST resubmission, not a server defect**:
this handler violates Post/Redirect/Get — a failed login POST is
answered by rendering `_LOGIN_TEMPLATE` directly (`api/admin.py:690`)
rather than redirecting to a fresh GET. The response the browser is
looking at after a failed attempt is therefore the *result of a POST*,
at the same URL, with no distinguishing query string. A browser refresh
(or a back/forward navigation) on that page is a resubmission of that
same POST with the same submitted credentials — most browsers prompt
("Confirm Form Resubmission") but some navigation paths (back/forward
cache, some corporate browser policies, some in-page reload shortcuts)
replay it without a visible prompt. The user's own ruling-out criterion
— "a clean URL, no `?error=` query parameter" — doesn't actually
distinguish this, because this app **never** used a query-string error
flag to begin with (that pattern belongs to the classic
redirect-after-POST design, which this handler doesn't use); a resubmit
would also carry no query string. This explanation isn't reproduced
here as certainly as symptom 2 (it depends on exact browser navigation
behavior outside this codebase's control), but it is the only path
through the actual code that can produce this message with what looks
like, but isn't actually, "zero action taken."

---

## Step 3 — Message-vs-actual-state mismatch: CONFIRMED, two distinct gaps

Reproduced directly in Client A's sequence above. Walking through what
the code does at each stage (`api/admin.py:649-690`):

| Stage | What's shown | Confirmed |
|---|---|---|
| Before any failed attempts (fresh GET) | No message | ✅ matches expectation |
| Failed POST #1, #2 (below threshold) | Generic "Wrong username or password" | ✅ matches expectation |
| **Failed POST #3 (this call crosses the threshold)** | **Generic "Wrong username or password" — not the lockout message** | ❌ bug |
| **GET reload while genuinely locked out** | **No message at all** | ❌ bug (different from what Step 3 asked to check, but still a real mismatch) |
| Next POST while still locked out | Correct lockout message, with remaining time | ✅ matches expectation |

**Bug 1 — the threshold-crossing POST shows the wrong message.** Root
cause, precisely: `login()` checks `rate_limiter.locked_out_for(source)`
*before* validating credentials (`api/admin.py:656-658`), and only calls
`rate_limiter.record_failure(source)` *after* credentials fail
(`api/admin.py:680`). The failure that pushes the count to
`max_attempts` and actually sets `_locked_until[source]`
(`api/admin.py:163-165`) happens *after* the lockout check for that same
request already returned "not locked." The code then unconditionally
flashes the generic message (`api/admin.py:689`) without re-checking
whether the failure it just recorded pushed the account into lockout. A
real user crossing the threshold is told "wrong password" — indicating
"try again, you probably mistyped it" — on the exact attempt that
actually locked them out, with no indication a lockout just started.

**Bug 2 — GET while locked out shows nothing, not the lockout state.**
The GET branch of `login()` (`api/admin.py:651-654`) only checks
`session.get("admin_authenticated")`; it never calls
`rate_limiter.locked_out_for(source)` at all. So a locked-out visitor
who simply reloads the page (no submission) sees a plain, blank login
form — indistinguishable from having never failed at all. This isn't
"intermittently shows the generic message" as the task's Step 3
description anticipated; it's a related but distinct gap: the lockout
state is **only ever surfaced on POST**, never on GET, so a user who
reloads instead of resubmitting gets no information either way, and
would have to submit again (using up nothing, since a locked-out POST
doesn't call `record_failure` — but still misleading) just to be told
they're locked out.

Both are real: a user could plausibly interpret Bug 1 as "I mistyped it,
let me try once more" immediately after actually crossing into lockout,
and Bug 2 means a page reload gives no warning before that retry.

---

## Step 4 — Describing the fix (not implemented)

### For the per-IP collateral-lockout gap (Step 2, symptom 2)

This is a real defect **in deployment conditions where multiple
legitimate visitors share one observed source IP** — most plausibly here
because of an unconfigured reverse proxy/load balancer in front of
Waitress (open per `docs/PRODUCTION_READY.md`), or any shared/NAT'd
network. It is not a defect in the code's stated design intent (per-IP
was chosen deliberately, see Step 1's quoted docstring) — it's a gap
*in that design's documented trade-offs*, which named the attacker-evasion
cost but not the shared-IP collateral-damage cost.

**This is the one place this diagnosis explicitly flags a choice back to
the user rather than assuming it, per the task's own instruction:**

- **Confirm first whether a reverse proxy sits in front of this
  deployment**, and if so, whether it forwards the real client address
  (e.g. `X-Forwarded-For`) and whether Flask/Werkzeug is configured to
  trust it (`werkzeug.middleware.proxy_fix.ProxyFix` or equivalent —
  confirmed absent from the codebase today). If a proxy is present and
  untrusted, **fixing that alone** (trusting the real client IP) may
  already resolve most of the collateral-lockout risk without changing
  `LoginRateLimiter`'s scoping at all, since each real visitor would then
  get their own IP-keyed bucket again.
- **Separately, and even with the client IP correctly resolved**, IP
  scoping still bundles together any visitors on a genuinely shared
  network (office, campus, CGNAT) — ask the user whether that residual
  risk is acceptable, or whether **per-session** scoping (keying
  `LoginRateLimiter` off a random, unauthenticated per-browser
  identifier set in the session/cookie on first visit, independent of
  `admin_authenticated`) is preferred instead. Trade-off to state
  explicitly, since the task asked for it: per-session is immune to
  shared-IP collateral damage but is trivially evaded by clearing
  cookies or opening a private window, so it protects legitimate
  shared-IP users at the cost of weakening the lockout's actual
  brute-force deterrence. There is no existing documentation choosing
  between these two failure modes — Step 1's docstring only justifies
  *IP over username*, not *IP over session* — so this is a genuinely
  open decision, not something to infer from prior intent.
- Whichever key is chosen, the storage-key fix itself is small and
  local to `LoginRateLimiter`/`_client_source()` (`api/admin.py:126-172,
  598-599`) — no schema or persistence change, since it's already an
  in-memory dict keyed by a string.
- **Regression test to add** (describing, not writing): using two
  separate `test_client()` instances against one `build_app()` (mirroring
  this diagnosis's reproduction), assert that failing the login enough
  times from one client does **not** cause a *different* client — using
  correct credentials, its first attempt — to see the lockout message.
  If the fix is proxy-trust based, the test would need to simulate two
  different forwarded addresses via the relevant header and assert they
  get independent buckets; if the fix is session-based, it would assert
  two clients with distinct session cookies (as already produced by two
  separate `test_client()`s) don't share a bucket even when
  `remote_addr` is identical (which is exactly today's Flask
  test-client default, and exactly what this diagnosis's reproduction
  script already demonstrates fails today).

### For the message-mismatch bugs (Step 3)

- **Bug 1 (threshold-crossing POST shows generic message):** after
  `rate_limiter.record_failure(source)` (`api/admin.py:680`), re-check
  `rate_limiter.locked_out_for(source)` before deciding which message to
  flash — if it now reports a positive remaining time, flash the lockout
  message (with that remaining time) instead of the generic one, for
  that same response. This keeps the check-then-act pattern but adds the
  missing second check right after the state-changing call.
- **Bug 2 (GET shows nothing while locked out):** the GET branch of
  `login()` (`api/admin.py:651-654`) should also call
  `rate_limiter.locked_out_for(source)` and flash/display the lockout
  message (with remaining time) whenever it's positive, exactly as the
  POST branch already does — so a locked-out visitor sees the same
  message on a plain reload as they would on a resubmission.
- **Regression test to add:** drive the exact sequence in Step 3's
  table — fail up to and including the threshold-crossing attempt and
  assert the lockout message (not the generic one) is what's shown on
  *that* response; then issue a plain GET and assert the lockout message
  is shown there too, not a blank form; then submit again and assert the
  lockout message is still shown, not the generic one, until the
  lockout actually expires.

---

## Step 5 — Describing the minutes/hours remaining-time display (not implemented)

**Current code**, `api/admin.py:663`:

```python
flash(f"Too many failed attempts — try again in {int(remaining // 60) + 1} minute(s).", "error")
```

What this already does and doesn't satisfy:

- It **never shows raw seconds** — `remaining` (a float count of
  seconds) is always floor-divided by 60 and has 1 added, so the lowest
  possible display is "1 minute(s)," never "47 seconds." This one
  requirement is already accidentally satisfied by the current rounding,
  not because of any deliberate hours/minutes design.
- It **never scales to hours.** With the default
  `ADMIN_LOGIN_LOCKOUT_MINUTES=15` this never matters (15 minutes is
  always minutes), but a deployment configuring a longer lockout (e.g.
  60+ minutes) would see "try again in 90 minute(s)" rather than the
  more natural "about 1.5 hours" / "about 2 hours" the task asks for.
- It is a **hardcoded English literal**, called directly inside
  `api/admin.py`, entirely outside `messages/en.py` /
  `messages/he.py` / `messages/catalog.py`. Checking those three files
  confirms the admin panel currently has **no integration point at all**
  with the project's bilingual message catalog (`MessageCatalog.text(key,
  **values)`, `messages/catalog.py:69-85`, selected via
  `get_current_catalog()`/`set_current_catalog()`) — the admin panel is,
  today, an English-only surface entirely separate from the bot's
  Telegram-facing bilingual messages. Routing this string through the
  catalog would be **new integration, not a mechanical fix**, and raises
  its own open question worth flagging to the user: *which* language the
  admin panel should render in, since it has no profile-selected
  `DEFAULT_LANGUAGE` concept the way the bot does today — defaulting it
  to `"en"` unconditionally is the obvious minimal choice, but that's an
  assumption, not a discovered requirement.

**Fix to describe:**

- Add two new keys to `messages/en.py` and `messages/he.py` (kept in
  sync per `validate_catalogs()`'s placeholder-matching requirement,
  `messages/catalog.py:32-59`) — one for "less than a minute"/"about N
  minutes," one for "about N hours" (or a single key taking a
  pre-formatted `{unit_phrase}` value computed in `api/admin.py`, which
  keeps the catalog entries simpler and keeps the minutes-vs-hours
  arithmetic in Python where it's easier to test). Suggested computation
  shape: below 60 seconds remaining → "less than a minute"; below 60
  minutes → "about {N} minutes" (rounded to nearest minute, minimum 1);
  60 minutes and over → "about {N} hours" (rounded to the nearest half
  hour or whole hour, using simple judgment on which reads more
  naturally, e.g. round 90 minutes to "about 1.5 hours" or "about 2
  hours" rather than "about 90 minutes").
- Both the POST lockout branch (`api/admin.py:663`) and Step 4's Bug 2
  fix (the new GET-branch lockout display) should call the same
  formatting function, so the phrasing is identical regardless of which
  code path is showing it — this ties directly back to Step 3: the
  remaining-time message must be what's shown consistently whenever
  locked out, never silently replaced by the generic message on some
  paths and shown correctly on others.
- **Regression test to add:** parametrize over a few representative
  remaining-time values (e.g. 30 seconds, 5 minutes, 90 minutes, 3
  hours) and assert the rendered message (a) contains no bare digit
  sequence followed by "second(s)" and no raw seconds count, and (b)
  uses "minute(s)" for sub-hour remainders and "hour(s)" once the
  remainder reaches an hour, in both `en` and `he` catalog entries.

---

## Answering the task's summary questions directly

- **Which hypothesis was confirmed:** neither (a) nor (b) exactly as
  stated. **Symptom 2 (lockout with no action taken)** is a real,
  reproduced defect that behaves like (a) in effect — the mechanism has
  a key (`request.remote_addr`), but that key collapses multiple real
  visitors into one bucket whenever they share an observed IP (shared
  network, or, most likely here, an unconfigured reverse proxy). **Symptom
  1 (generic wrong-password message with no action taken)** is not
  explained by either (a) or (b) — the flash mechanism is correctly
  session-scoped and correctly self-clearing on GET; the most plausible
  explanation is a client-side POST resubmission on refresh, enabled by
  this handler not following Post/Redirect/Get.
- **Distinguishing evidence from the two-session test:** Client B (a
  session with zero attempts of its own) received the lockout message on
  its very first, correct-credentials submission, purely from sharing
  Client A's observed IP — direct proof of the shared-IP collateral
  effect. Client B's *GET*, and Client A's own GET after its failures,
  both showed no message at all, which is what rules out (b) — there is
  no stale flash bleeding across either sessions or requests.
- **Step 3's message-mismatch bug:** confirmed, and broader than
  described — two distinct gaps (threshold-crossing POST shows the wrong
  message; GET while locked out shows no message at all), both
  reproduced above.
- **Step 5's display change:** **not added.** This entire pass is
  read-only diagnosis per the task's instructions; Step 5 above
  describes the design and regression test for a human to implement and
  review, and does not modify `api/admin.py` or the message catalogs.
