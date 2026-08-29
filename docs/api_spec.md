# API Spec (work_plan.md §7.1)

The request and response shape of every endpoint the API layer serves.
Every field name and meaning is drawn from `docs/vocabulary.md`
(work_plan.md §1.2) — this document doesn't restate what a field *means*,
only how it's carried over HTTP. All bodies are JSON. All timestamps are
ISO-8601 strings. Every response includes `X-Trace-ID`. A caller may supply a
valid `X-Trace-ID` and `X-Client-Request-ID`; otherwise the server creates a
trace identifier. The trace follows queued work through its final notification.

Every endpoint authenticates the caller first (§7.9) — see
**Authentication** below — and every error response uses the one shape
in **Errors** (§7.10), regardless of which endpoint produced it.

## Authorization (docs/Next_Plan.md)

After authentication, every endpoint checks one `auth.permissions.RequestedOperation`
via `auth.permissions.is_permitted`/`api.request_boundary.require`. A commander
is authorized for every operation unconditionally. A viewer is authorized
exactly for the operations listed in `auth.permissions.ViewerAllowedAction` —
currently `submit_event`, `submit_message`, `converse`, `ask_question`,
`report_event`, `request_action`, `view_profile_overview`,
`view_user_registration`, and `view_job_status`. A denied operation returns
`403` (the errors shape, `invalid_input`, `message` naming the operation) with
no partial effect — no event created, no queue reservation, no protected read.
`docs/allowed_calls.md`'s "Operation matrix" is the authoritative mapping from
every route below to its operation and viewer availability; this document
notes the operation and any ownership scoping per endpoint but does not repeat
that full matrix.

Three operations carry ownership scoping beyond plain membership, for a
viewer only: `ask_question` and `view_job_status` are restricted to events
the viewer themselves submitted (an unrelated event answers as if it does not
exist — `404` for `GET /Job/<event_id>`, an empty/negative result for a
history question); `view_user_registration` is restricted to the viewer's
own identity (`403` for any other). A commander is unrestricted in every
case.

`GET /SYSTEM`'s response is **role-filtered**, not a fixed shape: fields a
viewer is not authorized for (`agents`, `protocols`, `queued_events`,
`held_events`, `scheduler`, `settings`) are absent from the JSON body
entirely, never present as an empty value — see **`GET /SYSTEM`** below for
both shapes.

## Authentication

Every request carries the caller's identity in an `X-Identity` header —
the same Telegram identity string the user table keys on (§2.4), or a
sensor's own pre-registered system identity for `POST /Event`. There is
no session, no token issuance endpoint, and no login flow: the identity
table (§2.4, provisioned only by `cli/user_admin`, §1.10) is the entire
authentication mechanism. A missing or unregistered identity is rejected
outright — never treated as a viewer (§7.9's own rule).

### Service identity (`bot.api_client.BOT_SERVICE_IDENTITY`)

A third kind of pre-registered, non-human identity, alongside the human
commander/viewer case and the sensor case above: the bot process itself.
`docs/allowed_calls.md`'s "bot calls only api" means a real `HttpApiClient`
is the only thing that ever makes these calls, and every one of them needs
*some* `X-Identity` — for two genuinely different reasons, so it uses two
genuinely different identities depending on which:

- **Calls with no specific Telegram user's identity to forward** —
  `resolve_user`, `list_commander_chat_ids`/`GET /Commanders`,
  `poll_pending_notifications`/`GET /Notifications`, and
  `get_profile_diff_status` — present the bot's own fixed identity,
  `bot.api_client.BOT_SERVICE_IDENTITY` (currently the literal string
  `"bot-service"`). Provisioned once per deployment via `cli/user_admin`,
  at **commander** level (it needs to see commander-only information —
  the full roster, every notification kind — to do its own fan-out
  correctly), exactly like the first human commander:
  ```
  python -m cli.user_admin --profile <profile> add --telegram-id bot-service --level commander
  ```
  Without this row, every one of these calls fails authentication — the
  same "unregistered identity rejected outright" rule above applies to the
  bot itself, no exception. `get_profile_diff_status` stays here
  deliberately: it reads only `GET /SYSTEM`'s `profile_file_changed` field,
  gated by `view_profile_overview` — viewer-eligible, the least-restrictive
  of the operations this endpoint serves — it was never one of the methods
  the gap below applied to.
- **Calls that already carry a specific person's identity as a parameter**
  — `answer_clarification_hold`, `answer_approval_hold`, `submit_message`,
  and (since the server-side-enforcement gap noted below was closed)
  `get_profile_view`, `get_settings_view`, `get_job_result`,
  `write_protocol`, `write_setting` — use *that* identity as `X-Identity`,
  never the service identity. This is required, not a style choice: §7.9's
  permission check is what decides whether a hold-answer, a message
  submission, or one of these five is allowed at all, and it checks
  whoever `X-Identity` names. If the bot always presented its own
  commander-level service identity here, the API's own check would always
  pass regardless of the real Telegram user's actual level — silently
  turning the API's authorization into a rubber stamp for the bot's client-
  side check alone, rather than genuine defense-in-depth. The bot's own
  pre-check (`bot.users.check_permission`, already in place and tested)
  stays the first gate a request meets; the API's check, driven by the
  real identity, stays the second, independent one.

**A structural gap that used to exist here, closed:** `get_profile_view`,
`get_settings_view`, `get_job_result`, `write_protocol`, and
`write_setting` originally carried no per-call identity parameter at all
in `BotApiClient`'s Mission-8-era abstract signatures, even though the
last two are commander-only writes — meaning the API-side check could
only ever validate the bot's own blanket service-level access for these
five, never the real caller's. `bot/contracts.py`, `bot/transports.py`,
`bot/interactions.py` and `bot/app.py`
were all updated to thread the real caller's identity through to every
one of these five — the same shape of fix as the Mission 8 deep audit's
own §8.2 profile/settings unauthenticated-read fix, this time closing a
server-side-enforcement gap rather than a missing check entirely. Verified
with a dedicated test per affected method confirming the real API now
genuinely refuses an unauthorized identity for that specific call — not
just that the bot's own client-side check would have caught it
(`tests/test_bot_transports.py`, `tests/test_bot_app.py`).

## The acknowledgment shape

Every endpoint that submits work returns this shape immediately, before
any model call runs:

```json
{
  "event_id": "e3f1...",
  "status": "queued"
}
```

`event_id` **is** the job ID (§7.2) — there is no separate job
identifier. `status` is always `"queued"` at submission time; poll
`GET /Job/<event_id>` for how it progresses.

## `POST /Event`

Sensor ingestion (§7.3). Authenticates as a system identity, same as any
other caller — never bypassed.

Request:
```json
{ "text": "smoke observed at gate 3", "sender_identity": "sensor-north-1" }
```

Response `202 Accepted`: the acknowledgment shape. `occurred_at` is set
equal to the receipt time server-side; nothing here can override it.

## `POST /Msg`

Human ingestion (§7.4) — reports, requests, questions, and conversation all arrive
here; intent classification (§6.13) decides which. `submit_message` gates entry;
the resolved intent is then checked against its own operation
(`converse`/`ask_question`/`report_event`/`request_action`) before that branch
runs — all four are viewer-eligible today, but `ask_question` is further
ownership-scoped to the viewer's own submitted events (see **Authorization**
above).

Request:
```json
{ "text": "any status update on gate 3?", "sender_identity": "1002003", "source_message_id": "4821", "conversation_id": "telegram:1002003:main" }
```
`source_message_id` — the originating Telegram message's own ID — is
optional in the request body (a caller with no message to reference, or
testing directly against this endpoint, may omit it) but is what a much
later `job_finished`/`job_failed` or `event_data_hold` entry in `GET /Notifications` (§8.12)
needs to carry a real `reply_to_message_id`, per work_plan.md §2.3's
`events.source_message_id` column: written once here, read back there.

`conversation_id` is optional. When absent, behavior is compatible with old
clients and no conversation memory is read or written. Conversation is a
reference aid only: permissions, protocols, event facts and outcomes are
always read from their authoritative stores.

Response, when the message was a **question** — answered inline, no job:
```json
{
  "taken_as": "question",
  "answer": "Gate 3 is currently nominal.",
  "provenance": {
    "timezone": "Asia/Jerusalem", "time_start": null, "time_end": null,
    "filters": {}, "matched_count": 1, "truncated": false, "source_ids": ["e3f1..."]
  }
}
```
`provenance` is optional and does not replace the stable `answer` field.

Conversation is also answered inline. When the intent cannot be chosen safely, the same synchronous shape asks for clarification and creates no event:

```json
{ "taken_as": "clarification", "answer": "Which location do you mean?" }
```

Response, when the message became a **report** or **request** —
`202 Accepted`, the acknowledgment shape plus which it was taken as:
```json
{ "taken_as": "report", "event_id": "e3f1...", "status": "queued" }
```

When an existing protocol step is waiting for event data, a message in the
same `conversation_id` from the authenticated original reporter is first
checked as a possible answer to that request. A validated answer updates the
existing event and queues continuation of the same job:

```json
{
  "taken_as": "event_update",
  "event_id": "e3f1...",
  "updated_fields": ["area"],
  "answer": "The location was recorded and the waiting work will resume.",
  "status": "queued"
}
```

The Main Agent writes `answer` in the reporter's language. An unrelated
message is not consumed as event data and continues through ordinary intent
routing. `taken_as` is one of `"question" | "report" | "request" |
"conversational" | "clarification" | "event_update"`. Reports and requests
create jobs; `event_update` resumes an existing one.

## `GET /Job/<event_id>`

Result / status retrieval (§7.2). `view_job_status` — viewer-eligible, but
ownership-scoped: a viewer may only check the status of an event they
themselves submitted; `event_id` naming someone else's event responds
`404 Not Found`, identical to an unknown ID — it does not confirm that
event exists. A commander is unrestricted.

Response `200 OK` while in progress:
```json
{ "event_id": "e3f1...", "status": "queued" }
```
`status` is one of `"queued" | "running"`.

Response `200 OK` while held:
```json
{
  "event_id": "e3f1...",
  "status": "held_for_clarification",
  "unresolved_field": "classification"
}
```
or
```json
{
  "event_id": "e3f1...",
  "status": "held_for_approval",
  "reason": "flagged_protocol"
}
```
`reason` is `"flagged_protocol" | "ambiguous_selection"` (§6.7). This is
how a caller tells "still running" from "waiting on a commander" from
each other — reading `status` alone is enough; the extra field says why.

A protocol action that lacks data is neither failed nor complete. It persists
with zero attempts and reports:

```json
{
  "event_id": "e3f1...",
  "status": "waiting_for_event_data",
  "missing_fields": ["area"],
  "question": "באיזה אזור האירוע התרחש?",
  "steps_completed": []
}
```

The question is model-written from validated field metadata. Supplying the
data through `POST /Msg` updates this same event; completed steps are not run
again.

Response `200 OK` once finished — closed without running:
```json
{
  "event_id": "e3f1...",
  "status": "closed_on_precedent",
  "detail": "closed against resolved precedent 'e19a...'"
}
```

Response `200 OK` once finished — declined:
```json
{ "event_id": "e3f1...", "status": "declined" }
```

Response `200 OK` once finished — ran to a verdict:
```json
{
  "event_id": "e3f1...",
  "status": "succeeded",
  "insight_text": "...",
  "steps_completed": ["reference_agent: gate 3 is nominal"]
}
```
or, on a run that exhausted its retries:
```json
{
  "event_id": "e3f1...",
  "status": "failed",
  "detail": "attempt limit exhausted",
  "steps_completed": ["reference_agent: gate 3 is nominal"],
  "failed_step_agent_name": "dispatch_agent"
}
```
`status` here is one of `"succeeded" | "failed" | "uncertain"`. This is
how "still running" is told from "waiting on a commander" is told from
"closed without running" (§7.1's own required distinction) — three
different `status` values, never inferred from HTTP status code alone
(every one of these responses is `200 OK`; the run's own outcome is data,
not a transport-level failure — §7.10's explicit rule). `steps_completed`
(§7.12) lists every step that actually produced a result, in execution
order — omitted when no step ran (e.g. `closed_on_precedent`, `declined`).
`failed_step_agent_name` (§7.12) is present only when `status: "failed"`
and a step actually ran and failed (as opposed to, say, task formulation
itself failing before any step started).

Response `404 Not Found` (the errors shape, §7.10) when `event_id` names
no event at all.

## `POST /Approve/<event_id>` and `POST /Clarify/<event_id>` (§7.11)

`resolve_clarification` and `approve_run` respectively — both commander-only.

`POST /Clarify/<event_id>`:
```json
{ "classification": "fire" }
```
Response `202 Accepted`, the acknowledgment shape — resolving always
queues a continuation (§7.11's own rule).

`POST /Approve/<event_id>`:
```json
{ "decision": "approved" }
```
or
```json
{ "decision": "rejected" }
```
or, for the ambiguous-selection case (§6.4/§6.7) — a candidate protocol
name in place of `"approved"`/`"rejected"`:
```json
{ "decision": "status_check" }
```

Response on `"approved"` or a candidate protocol name: `202 Accepted`,
the acknowledgment shape — the continuation is queued, same as clarify.

Response on `"rejected"`: `200 OK`, synchronously, no job left running:
```json
{ "event_id": "e3f1...", "status": "declined" }
```

Response `400 Bad Request` (the errors shape, `invalid_input`) when
`decision` is neither `"approved"`, `"rejected"`, nor one of the hold's
own candidate protocol names — naming the real candidates.

Response `409 Conflict` (the errors shape) when the hold named by
`event_id` was already resolved — `message` names who resolved it and
when:
```json
{
  "error_class": "invalid_input",
  "message": "already resolved by 'commander-2' at 2026-08-24T09:12:00"
}
```

## `CRUD /Protocol` (§7.6)

Every operation here — `list_protocols`, `create_protocol`,
`update_protocol`, `delete_protocol` — is commander-only; a viewer receives
`403` for all four, including the read.

`GET /Protocol` — `200 OK`:
```json
{
  "protocols": [
    {
      "name": "status_check",
      "description": "...",
      "participating_agents": ["reference_agent"],
      "approved_tools": ["check_status"],
      "expected_success_output": "...",
      "criticality": "low",
      "approval_flag": false
    }
  ]
}
```

`POST /Protocol` — request body is one protocol object, same shape as
above. `PUT /Protocol/<name>` — same body, replaces the named protocol.
`DELETE /Protocol/<name>` — no body.

Every write's response, `200 OK`:
```json
{ "message": "The running system is unchanged. This edit applies from the next start." }
```
Never a body resembling a successful state change (§7.6's explicit rule)
— this exact message, unconditionally, on every write.

## `GET /SYSTEM` (§7.7)

Gated by `view_profile_overview` (viewer-eligible — the entry check for
this one endpoint). The response body is then built field-group by
field-group, each behind its own operation, so a viewer's response is a
**strict subset** with the protected fields absent, not a full body with
some values blanked out.

`200 OK` for a **commander** (authorized for `view_system_internals` and
`view_settings` too):
```json
{
  "profile": "fixtures.profiles.demo_profile",
  "agents": ["reference_agent", "main_agent", "history_agent", "insights_agent"],
  "protocols": [
    {
      "name": "status_check",
      "description": "applies to a routine status check",
      "participating_agents": ["reference_agent"],
      "approved_tools": ["check_status"],
      "expected_success_output": "a status report",
      "criticality": "low",
      "approval_flag": false
    }
  ],
  "event_types": ["fire", "medical", "human_activation"],
  "areas": ["north_sector", "south_sector"],
  "queued_events": 2,
  "held_events": { "clarification": 1, "approval": 0 },
  "scheduler": { "last_run_at": "2026-08-24T09:00:00", "last_run_ok": true, "last_run_error": null },
  "settings": { "retry_count": 3, "risk_threshold": 0.5, "lookback_window_days": 30 },
  "profile_file_changed": false
}
```
`profile_file_changed` is `true` when the profile file's hash on disk no
longer matches the hash taken at load — a pending edit awaiting restart.
Each entry in `protocols` uses the exact same shape `GET /Protocol` (§7.6)
returns — one rendering, reused, so a caller never needs both endpoints
just to get one protocol's full description and criticality (§7.12).

`200 OK` for a **viewer** — `agents`, `protocols`, `queued_events`,
`held_events`, `scheduler`, and `settings` are absent entirely:
```json
{
  "profile": "fixtures.profiles.demo_profile",
  "event_types": ["fire", "medical", "human_activation"],
  "areas": ["north_sector", "south_sector"],
  "profile_file_changed": false
}
```

## `PUT /SYSTEM` (§7.8)

`change_settings` — commander-only.

Request — only these three keys are ever accepted:
```json
{ "retry_count": 5, "risk_threshold": 0.6, "lookback_window_days": 45 }
```
A partial body is fine — only the keys present are changed. Any other
key is rejected (the errors shape, `invalid_input`, naming the field)
rather than silently ignored.

Response `200 OK`, the new values, already written to the settings store
before this response is sent:
```json
{ "retry_count": 5, "risk_threshold": 0.6, "lookback_window_days": 45 }
```

## `GET /User/<identity>` (§8.14)

`200 OK`, always — an unregistered identity is not an error here, since
asking "is this registered" is the whole point:
```json
{ "registered": true, "permission_level": "commander" }
```
or
```json
{ "registered": false, "permission_level": null }
```
`view_user_registration` — viewer-eligible, but ownership-scoped: a viewer
may only look up their own identity (`403` for any other). A commander is
unrestricted (this is how `bot-service`, at commander level, resolves every
Telegram caller's registration on their behalf — see **Service identity**
above).

## `GET /Commanders` (§8.13)

`200 OK`:
```json
{ "commanders": [{ "telegram_identity": "commander-1" }] }
```
COMMANDER-level (`view_commander_roster`) — see **Service identity**
above for who the real caller is and why this isn't VIEWER-level like
most reads in this system.

## Notification waiting

`GET /Notifications` accepts `since=<cursor>` and optional
`wait_seconds=<0..30>`. The wait defaults to zero for backward-compatible
immediate polling; a positive value waits until a notification commit or
timeout without changing cursor ordering or at-least-once delivery.

## `GET /Notifications` (§8.12)

`GET /Notifications?since=<cursor>` — `since` is an opaque, caller-tracked
integer cursor (omit or `0` for "everything ever recorded"). `200 OK`:
```json
{
  "notifications": [
    {
      "sequence_id": 7,
      "kind": "approval_hold",
      "payload": {
        "hold_id": "...", "event_id": "e3f1...", "reason": "flagged_protocol",
        "risk_level": "high", "risk_reason": "...",
        "selected_protocol_name": "dispatch_response", "candidate_protocol_names": []
      },
      "target_chat_ids": [],
      "reply_to_message_id": null,
      "trace_id": "1b7f..."
    }
  ],
  "next_cursor": 7
}
```
`kind` is one of `bot.api_client.BotNotificationKind`'s eight values;
`payload`'s shape matches that kind's own DTO
(`HeldClarificationNotice`/`HeldApprovalNotice`/`UncertainVerdictNotice`
/`PrecedentClosureNotice`/`EventDataNeededNotice`/`JobResult`-shaped for
`job_finished`/`job_failed`) exactly, field for field. `target_chat_ids` is populated
(one entry, the original sender's own identity — a private chat's
`chat_id` equals its user's identity) only for `job_finished`,
`job_failed`, and `event_data_hold`, since those three are addressed to a
specific person; the commander-facing kinds are addressed to every commander, which a caller
resolves via `GET /Commanders` rather than this endpoint repeating that
list on every row. `reply_to_message_id` (§8.9's "reference the original
message") is likewise populated only for those same three kinds, read from
the originating event's own `source_message_id` column (work_plan.md
§2.3) — `null` for a sensor-sourced event (no Telegram message to
reference) and for every other notification kind (none of them are
replies to anything). Polling again with `since` equal to the previous
response's `next_cursor` returns `{"notifications": [], "next_cursor":
<same value>}` — never the same row twice. COMMANDER-level
(`poll_notifications`) — see **Service identity** above.

An outcome that has two distinct audiences produces two separate entries
with two different `kind`s and two different `sequence_id`s, not one
entry serving both: `"uncertain"` produces a `job_finished` entry (for
whoever submitted the event) and a separate `uncertain_verdict` entry
(for commanders); `"closed_on_precedent"` produces `job_finished` plus a
separate `precedent_closure` entry, the same way.

## `POST /Msg/Stream`

Optional SSE transport for verified final text. It is hidden with `404` unless
the profile enables streaming. Event types are `ack`, `delta`, `final`, and
`error`; structured decisions and unvalidated model output are never emitted.
The provider adapter may emit only `final`/`error` when token streaming is not
available, and persistence stores only the final answer.

## Errors (§7.10)

One shape, every endpoint, every failure:
```json
{
  "error_class": "invalid_input",
  "message": "human-readable, specific",
  "field": "risk_threshold"
}
```
`field` is present only when one specific field or protocol is at fault;
omitted otherwise. `error_class` is one of:

- `"invalid_input"` — `400`. Bad payload, unknown field, a value that
  fails validation, an unregistered identity, an authorization refusal.
- `"run_failure"` — `422`. The Main Agent couldn't produce a usable
  response somewhere with no job to report it against — e.g. intent
  classification or question-answer routing failing synchronously inside
  `POST /Msg`. A protocol run that exhausts its retries is **not** this
  class — that's a normal `GET /Job/<event_id>` response reporting
  `status: "failed"`, `200 OK` (§7.10's explicit rule, restated in
  **`GET /Job/<event_id>`** above).
- `"internal_error"` — `500`. Anything unexpected. Never carries an
  exception message, a stack trace, or an engine-specific detail —
  `message` is a fixed, generic string for this class only.

No response body of any kind, on any endpoint, ever contains a Python
traceback, an exception's `repr`, or a SQLite error string.

## Mapping to `BotApiClient` (`bot/contracts.py`) — §7.12

Every `BotApiClient` method is typed to return a success DTO, several with
their own in-body status field for a condition this API instead reports as
an HTTP error response. This is the mapping a real `HttpApiClient` needs —
written down now, per §7.12, since the DTOs themselves aren't changing
until an `HttpApiClient` is actually built.

**`answer_clarification_hold` / `answer_approval_hold` → `HoldAnswerOutcome`.**
This is the one place almost every error response has a natural home,
since `HoldAnswerStatus` already exists to carry exactly this:

| Response | `HoldAnswerOutcome.status` | Notes |
|---|---|---|
| `401`/`403` `invalid_input` (authentication/authorization) | `"unauthorized"` | `message` carries the API's own message text. |
| `404` `invalid_input` (no such hold) | `"not_found"` | `resolved_by` stays `None` — nothing to name. |
| `409` `invalid_input` (already resolved) | `"not_found"` | `resolved_by`/`resolved_at` must be parsed out of the API's `message` string (`"already resolved by 'X' at T"`) — there is no separate structured field for it in the error body (§7.10's fixed three-field shape has no room for one). A future refinement could add a dedicated field; until then, parsing the message is the only path. |
| `400` `invalid_input`, `field: "classification"` (`POST /Clarify`) | `"invalid_classification"` | |
| `400` `invalid_input`, `field: "decision"` (`POST /Approve`, bad candidate) | `"invalid_candidate"` | |
| `400` `invalid_input`, `field: "decision"` (`POST /Approve`, missing/malformed) | *(no exact match)* | Nearest is `"invalid_candidate"`, though the API's message may not be about a candidate at all (e.g. a missing `decision` field entirely) — a client should render the API's `message` regardless of which status it picks. |
| `202`/`200` success | `"approved"` / `"rejected"` / `"resolved"` | Direct — the response's `status` field (`"queued"` or `"declined"`) plus which endpoint was called determines which. |

**`submit_message` → `MessageSubmissionResult`.** This DTO has no error
slot at all — no `accepted`, no `status` covering failure. A `401`/`403`
(auth) or `422` `run_failure` (intent routing/question-answering failed
synchronously) from `POST /Msg` has nowhere to go inside a
`MessageSubmissionResult`; a real client must raise an exception (a new
`bot.startup.ApiRequestError`) rather than force one of these into
the DTO. `500` `internal_error` is the same.

**`write_protocol` → `WriteResult`.** `accepted: bool` already
covers a validation failure at `POST`/`PUT`/`DELETE /Protocol` (`400`
`invalid_input` → `accepted=False`, `message` from the API). `401`/`403`
have no slot here either — same as `submit_message`, these must raise.

**`write_setting` → `WriteResult`.** Same shape and same mapping
as `write_protocol` — `accepted=False` for a `400` from `PUT /SYSTEM`;
`401`/`403` raise.

**`get_profile_view` / `get_profile_diff_status` / `get_settings_view` /
`get_job_result`.** These are read-only GETs; the only realistic error
responses are `401`/`403` (raise) and `404` for `get_job_result` (no such
event — return `None`, matching this method's own declared `JobResult |
None` return type, no raise needed).

**`resolve_user` → `UserLookupResult`.** `GET /User/<identity>`'s `200`
body maps directly — `registered`/`permission_level` are already exactly
this DTO's own fields, no translation needed. `401`/`403` raise, same as
every other read.

**`list_commander_chat_ids` → `tuple[str, ...]`.** `GET /Commanders`'s
`commanders` array, each entry's `telegram_identity` — a plain tuple of
strings, no DTO wrapper. `401`/`403` raise.

**`poll_pending_notifications` → `tuple[tuple[BotNotification, ...],
int]`.** `GET /Notifications`'s `notifications` array, each entry built
into a `BotNotification` directly — `kind`, `target_chat_ids`, and
`reply_to_message_id` all come straight off the response entry, `payload`
parsed into the DTO that `kind` names (see **`GET /Notifications`**
above) — paired with `next_cursor`. `401`/`403` raise.
