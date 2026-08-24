# API Spec (work_plan.md §7.1)

The request and response shape of every endpoint the API layer serves.
Every field name and meaning is drawn from `docs/vocabulary.md`
(work_plan.md §1.2) — this document doesn't restate what a field *means*,
only how it's carried over HTTP. All bodies are JSON. All timestamps are
ISO-8601 strings.

Every endpoint authenticates the caller first (§7.9) — see
**Authentication** below — and every error response uses the one shape
in **Errors** (§7.10), regardless of which endpoint produced it.

## Authentication

Every request carries the caller's identity in an `X-Identity` header —
the same Telegram identity string the user table keys on (§2.4), or a
sensor's own pre-registered system identity for `POST /Event`. There is
no session, no token issuance endpoint, and no login flow: the identity
table (§2.4, provisioned only by `cli/user_admin`, §1.10) is the entire
authentication mechanism. A missing or unregistered identity is rejected
outright — never treated as a viewer (§7.9's own rule).

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

Human ingestion (§7.4) — reports, requests, and questions all arrive
here; intent classification (§6.13) decides which.

Request:
```json
{ "text": "any status update on gate 3?", "sender_identity": "1002003" }
```

Response, when the message was a **question** — answered inline, no job:
```json
{ "taken_as": "question", "answer": "Gate 3 is currently nominal." }
```

Response, when the message became a **report** or **request** —
`202 Accepted`, the acknowledgment shape plus which it was taken as:
```json
{ "taken_as": "report", "event_id": "e3f1...", "status": "queued" }
```

`taken_as` is one of `"question" | "report" | "request"`.

## `GET /Job/<event_id>`

Result / status retrieval (§7.2).

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

`200 OK`:
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

## `PUT /SYSTEM` (§7.8)

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

## Mapping to `BotApiClient` (bot/api_client.py) — §7.12

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
`bot.errors` type, not yet defined) rather than force one of these into
the DTO. `500` `internal_error` is the same.

**`write_protocol` → `ProtocolWriteResult`.** `accepted: bool` already
covers a validation failure at `POST`/`PUT`/`DELETE /Protocol` (`400`
`invalid_input` → `accepted=False`, `message` from the API). `401`/`403`
have no slot here either — same as `submit_message`, these must raise.

**`write_setting` → `SettingsWriteResult`.** Same shape and same mapping
as `write_protocol` — `accepted=False` for a `400` from `PUT /SYSTEM`;
`401`/`403` raise.

**`get_profile_view` / `get_profile_diff_status` / `get_settings_view` /
`get_job_result`.** These are read-only GETs; the only realistic error
responses are `401`/`403` (raise) and `404` for `get_job_result` (no such
event — return `None`, matching this method's own declared `JobResult |
None` return type, no raise needed).
