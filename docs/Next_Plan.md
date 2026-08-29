# Next Plan: Role-Aware Capabilities and Natural Event Conversations

## 1. Purpose

This plan defines the next implementation sequence for AgentsHub. It combines the following requirements:

- A commander has no authorization restrictions. Commander access must not depend on the viewer allowlist.
- A viewer may perform only actions explicitly present in a viewer-specific `Enum`.
- An action absent from that `Enum` must be denied to a viewer.
- A viewer asking "what can you do for me?" receives only the capabilities that the viewer is currently allowed to use.
- A viewer must not receive protocol, sub-agent, tool, administrative-action, or other protected metadata unless a future, explicitly approved viewer action permits that category.
- Commander-facing identity and capability answers may describe all runtime capabilities, protocols, sub-agents, and tools, but only when relevant to the question.
- Identity and capability responses must be written naturally by the model from runtime metadata. Final answers must not be hardcoded.
- Every profile must define a non-empty `PROFILE_NAME`, and the Main Agent must identify itself as the manager of that named service.
- Stored-event answers must be natural, readable, factually grounded, and able to explain the meaning of persisted fields.
- Follow-up questions about an event must use the conversation memory that already exists. No new conversation table, event-reference table, or replacement memory subsystem will be added.
- All source-code identifiers, comments, docstrings, prompts, profile descriptions, and technical documentation must be written in English. Runtime answers may use the user's language.

This document is an implementation plan only. It does not authorize the implementation until the user approves the plan and the initial viewer action list.

## 2. Locked Design Decisions

### 2.1 Commander authorization

`PermissionLevel.COMMANDER` is unrestricted by the viewer allowlist. Authorization checks must return allowed for a commander before checking `ViewerAllowedAction` membership.

"Unrestricted" applies to authorization. Input validation, schema validation, concurrency controls, idempotency rules, persistence guarantees, and tool safety constraints remain active because they are correctness boundaries rather than user permissions.

### 2.2 Viewer authorization

The system will define a `ViewerAllowedAction` `Enum`. Its members are the complete viewer authorization policy. There will be no separate viewer allowlist hidden in API routes, bot handlers, prompts, or capability formatting code.

The implementation must satisfy this invariant:

```text
commander -> authorization allowed
viewer + action in ViewerAllowedAction -> authorization allowed
viewer + action not in ViewerAllowedAction -> authorization denied
```

The exact initial members of `ViewerAllowedAction` are a user-owned decision. Existing behavior must not be treated as approval merely because the current code permits it. Before Stage 1 begins, the user must approve the complete initial member list and the mapping from every API/message operation to an action.

The user has already established one behavior requirement: a viewer may ask for their own capability list. That answer must be derived from the viewer action policy and must not reveal inaccessible capabilities.

### 2.3 Disclosure is derived from authorization

Capability disclosure is not a second manually maintained policy. Each capability descriptor must identify the action that authorizes it. The viewer capability response is produced by filtering descriptors through `ViewerAllowedAction`.

Removing an action from `ViewerAllowedAction` must automatically:

- deny execution of the corresponding viewer operation;
- remove it from the viewer's "what can you do for me?" response;
- remove protected metadata needed only by that operation from viewer-facing model context;
- keep commander behavior unchanged.

### 2.4 Conversation memory

The existing `conversation_messages` table and the existing `conversation_id`, TTL, pruning, append, and fetch logic remain the only conversation-memory mechanism.

Conversation messages may be used to resolve conversational references such as "that event", "the first one", or "what happened after that?" They are not authoritative for event facts, permissions, protocols, approvals, or outcomes. Once an event is identified, its current facts must be read again from persistence under the caller's current authorization.

No migration is planned for this feature.

### 2.5 Natural language is model-written; facts and access are application-controlled

The application controls:

- which capabilities and metadata may enter a prompt;
- which stored fields may be shown;
- field meanings;
- event ordering and source identifiers;
- authorization and validation;
- provenance.

The model controls only the natural wording of the final response. It must not decide what the caller is permitted to know or do.

## 3. Current-State Findings

The implementation must be based on these existing behaviors:

- `auth/permissions.py` currently uses `ACTION_REQUIREMENTS` and numeric permission thresholds. Viewer actions and commander actions are mixed in one mapping.
- `orchestrator/reasoning.py` currently contains static `SYSTEM_CAPABILITIES` and builds a full context containing protocols, sub-agents, and tools.
- `api/routes.py` currently builds that full system context once when the messages blueprint is created, before a request is authenticated.
- `GET /Protocol` and `GET /SYSTEM` currently use the broad `view_history` permission, which allows a viewer to receive protocols, agent names, and system details.
- `/Msg` authenticates the caller but uses a system context that is not caller-specific.
- `history/query.py` currently formats `latest` and `list` operations as technical lines through `_event_line`, including output such as `outcome=...`.
- Natural history operations already pass database-filtered records to the History Agent, but there is no single explicit field glossary defining what every persisted event field means for response generation.
- `conversation_messages` already stores user and assistant messages by `conversation_id`, with TTL and turn-count pruning.
- `/Msg` already fetches prior messages and stores new turns.
- The merged planner already accepts conversation messages. The legacy question path does not currently receive them.
- `PROFILE_NAME` is already required and validated. This requirement must be preserved and covered by identity-response tests rather than reimplemented.

## 4. Target Components

### 4.1 Requested operation catalog

Introduce an internal `RequestedOperation` `Enum` covering every externally requestable operation. This gives routes, messages, bot commands, and tests stable identifiers instead of unrelated string literals.

`RequestedOperation` is not itself an allowlist. It is the complete operation vocabulary.

### 4.2 Viewer allowlist

Introduce `ViewerAllowedAction`, containing only the operations the user has approved for viewers. A validation helper will ensure every member maps to a real `RequestedOperation` and has one capability descriptor where appropriate.

### 4.3 Caller access policy

Introduce a small immutable `CallerAccessPolicy` built from the authenticated `PermissionLevel`. It will expose:

- `allows(operation)`;
- `require(operation)`;
- `visible_capabilities()`;
- disclosure flags derived from allowed actions, not manually set by callers.

The policy must not contain profile data or model logic.

### 4.4 Capability descriptors

Replace the static final capability list with descriptors. Each descriptor will contain English technical metadata such as:

- stable capability name;
- natural-language description;
- associated `RequestedOperation`;
- whether it uses event history, current-state specialists, protocols, side effects, or human review;
- which runtime metadata categories are necessary to describe it.

The descriptors provide facts to the model. They do not contain final response sentences.

### 4.5 Role-aware system context

Build system context after authentication and for each request. The builder will receive the caller access policy and return only permitted metadata.

For a viewer, protected arrays must be absent rather than present as empty hints when disclosure is forbidden. This reduces prompt-injection and accidental-disclosure risk.

For a commander, the builder may include all runtime protocols, sub-agents, tools, event types, areas, and administrative capabilities.

### 4.6 Semantic event view

Introduce a response-only semantic representation of a stored event. It will be constructed from a persistence record and a centralized English field catalog. It will not change the SQLite schema.

The view will distinguish:

- stable event identity;
- received time versus occurred time;
- original report text versus extracted description;
- classification, area, entities, and severity;
- risk assessment and its reason;
- selected protocol and protocol reason;
- clarification and approval state;
- precedent relationships;
- executed steps and their results;
- final outcome, failure reason, and insight.

Only fields allowed for the caller and relevant to the question will enter the history response prompt.

## 5. Stage 0 - Freeze the Authorization Contract

**Status:** ✅ Complete (2026-08-29)

### Objective

Obtain explicit approval for the initial viewer action members and create a complete action-to-entry-point matrix before changing runtime behavior.

### Files and required work

| File | Planned change |
|---|---|
| `docs/Next_Plan.md` | Record the approved `ViewerAllowedAction` members in an append-only decision section after user approval. |
| `docs/allowed_calls.md` | Replace level-threshold descriptions with a table mapping every API route, bot command, and message intent to `RequestedOperation` and viewer availability. |
| `docs/vocabulary.md` | Define `RequestedOperation`, `ViewerAllowedAction`, `CallerAccessPolicy`, `CapabilityDescriptor`, and `SemanticEventView`. |
| `auth/permissions.py` | No runtime edit in this stage; enumerate all current action strings and identify collisions where one broad action currently protects unrelated information. |
| `api/routes.py` | No runtime edit in this stage; inventory every `require(level, ...)` call and assign its future operation. |
| `bot/app.py` and `bot/interactions.py` | No runtime edit in this stage; inventory every command and callback that requires an operation mapping. |

### Required user decision

The user approves:

1. every initial member of `ViewerAllowedAction`;
2. whether each viewer action may expose event details, current-state results, job status, or other data;
3. the operation mapping for ambiguous surfaces such as `GET /User/<identity>`, `GET /Job/<event_id>`, `/profile view`, and `/settings view`;
4. whether viewer access to a resource is limited to the viewer's own records where applicable.

No implementer may infer these decisions from the current code.

### Decision record (approved 2026-08-29)

The user approved the following, resolving the four required decisions above. This section is an append-only record; it does not modify the requirements stated earlier in this stage.

1. **`RequestedOperation` catalog** (complete operation vocabulary) and the **entry-point mapping** are published in docs/allowed_calls.md's "Operation matrix" section. Definitions are published in docs/vocabulary.md.
2. **Initial `ViewerAllowedAction` members**: `submit_event`, `submit_message`, `converse`, `ask_question`, `report_event`, `request_action`, `view_profile_overview`, `view_user_registration`, `view_job_status`. Every other `RequestedOperation` (`list_protocols`, `create_protocol`, `update_protocol`, `delete_protocol`, `view_system_internals`, `view_settings`, `change_settings`, `view_commander_roster`, `resolve_clarification`, `approve_run`, `poll_notifications`) is commander-only.
3. **Data exposure per viewer action**: `ask_question` and `view_job_status` may expose event details/current-state results/job status only for events the viewer themselves submitted (matched by `sender_identity`). No viewer action exposes protocol bodies, sub-agent names, tool names, or settings.
4. **Ambiguous surfaces resolved**:
   - `GET /User/<identity>` → `view_user_registration`, viewer-eligible but restricted to the viewer's own identity.
   - `GET /Job/<event_id>` → `view_job_status`, viewer-eligible but restricted to events the viewer themselves submitted.
   - `GET /SYSTEM` (today one merged payload gated by one action) splits into three operations: `view_profile_overview` (identity + `event_types` + `areas`, viewer-eligible), `view_system_internals` (agent names, protocol bodies, scheduler, queue/held counts, commander-only), `view_settings` (settings block, commander-only). The handler must build a role-filtered payload per operation rather than gating the whole endpoint at once — this is Stage 2/3 implementation work, not yet done.
   - Bot `/profile view` maps to `view_profile_overview`; bot `/settings view` maps to `view_settings` (commander-only) — these were previously the same server call and are now distinct.
5. **Ownership scoping**: this is new restriction behavior — no ownership filter exists in the code today. `ask_question`, `view_job_status`, and `view_user_registration` are the only operations scoped to the caller's own records; every other operation is unscoped (subject to level).

### Success criteria

- Every API route, message route, bot command, and callback has exactly one documented `RequestedOperation`.
- The initial `ViewerAllowedAction` member list is explicitly approved by the user.
- No broad action such as `view_history` protects unrelated protocol, profile, user, or system metadata in the target matrix.
- There are no unresolved authorization decisions before Stage 1 starts.

## 6. Stage 1 - Implement Commander-Bypass and Viewer-Enum Authorization

**Status:** ✅ Complete (2026-08-29)

### Objective

Replace threshold-based viewer authorization with the approved enum policy while preserving commander-wide access.

### Files and required work

| File | Planned change |
|---|---|
| `auth/permissions.py` | Add `RequestedOperation` and `ViewerAllowedAction`. Refactor `is_permitted` so commander authorization succeeds independently of viewer enum membership and viewer authorization succeeds only for approved enum members. Remove `ACTION_REQUIREMENTS` as the source of viewer policy. |
| `auth/__init__.py` | Export the new enums and authorization helpers as the package's public permission contract. |
| `api/request_boundary.py` | Change `require` to accept `RequestedOperation`, return the existing uniform 403 behavior, and avoid raw action strings. Authentication remains unchanged. |
| `bot/interactions.py` | Change `check_permission` to accept `RequestedOperation` and use the same shared authorization function as the API. Do not duplicate viewer policy in the bot. |
| `cli/user_admin.py` | Preserve only role assignment (`viewer` or `commander`). Do not add per-user permission lists. |
| `tests/test_permissions.py` | Replace threshold assertions with exhaustive enum tests. Verify every commander operation is allowed and every viewer operation absent from `ViewerAllowedAction` is denied. |
| `tests/test_api_app.py` | Update boundary tests to use the operation enum and preserve 401/403 response contracts. |
| `tests/test_bot_interactions.py` | Verify bot permission checks match API permission checks for every operation. |

### Implementation constraints

- Do not maintain a second viewer set outside `ViewerAllowedAction`.
- Do not compare `PermissionLevel` values with `>=` to decide viewer actions.
- Do not let prompts or models perform authorization.
- Do not change user persistence; users continue to store only `viewer` or `commander`.

### Success criteria

- Exhaustive tests prove that a commander is allowed for every `RequestedOperation`.
- Exhaustive tests prove that a viewer is allowed exactly the approved `ViewerAllowedAction` members and nothing else.
- Adding or removing one viewer enum member changes the authorization test matrix without editing route-specific allowlists.
- Unknown or unmapped operations fail startup/test validation rather than silently acquiring viewer access.
- Existing authentication behavior and refusal wording remain compatible unless the approved policy requires a clearer action name.

### Implementation note (added on completion, 2026-08-29)

`RequestedOperation` and `ViewerAllowedAction` are implemented in `auth/permissions.py` exactly per §4.2/§4.3, and `is_permitted` implements the invariant in §2.2 unconditionally for any `RequestedOperation` value: a commander is authorized before any enum membership check, and a viewer is authorized exactly for `ViewerAllowedAction` members. `bot/interactions.py`'s `check_permission` now takes `RequestedOperation` and calls the same shared `is_permitted` — no viewer policy is duplicated in the bot, and `tests/test_bot_interactions.py::test_check_permission_matches_is_permitted_for_every_operation_and_level` proves the two never disagree.

One deliberate, transitional deviation: `api/routes.py` is Stage 2's file, not Stage 1's, and still passes the old raw action strings (`"send_message"`, `"view_history"`, etc.) to `require`. Changing `is_permitted`/`require` to accept only `RequestedOperation` would have broken every API route before Stage 2 lands. Instead, `auth/permissions.py` keeps the old `ACTION_REQUIREMENTS` table and a legacy string branch in `is_permitted`/`require`, clearly marked as transitional and scoped only to strings — it is never consulted for a `RequestedOperation` call, so it does not participate in the new enum-based viewer policy at all. This legacy table and branch must be deleted as part of Stage 2, once every `api/routes.py` call site is migrated to `RequestedOperation`.

Full offline suite: 920 passed, 0 failed (199.64s), 6 pre-existing third-party CrewAI deprecation warnings, no new failures introduced.

## 7. Stage 2 - Enforce the Policy at Every External Boundary

**Status:** ✅ Complete (2026-08-29)

### Objective

Apply the operation catalog consistently to API routes, free-text messages, Telegram commands, callbacks, and terminal clients.

### Files and required work

| File | Planned change |
|---|---|
| `api/routes.py` | Replace each string permission check with its approved `RequestedOperation`. Split broad checks so history, protocols, system metadata, user lookup, job status, holds, settings, and notifications do not share an unrelated permission. |
| `api/routes.py` | In `/Msg`, authenticate first, classify the requested operation, enforce it before executing a tool, querying protected data, creating an event, or queuing work. Preserve the existing API refusal contract. |
| `api/routes.py` | Protect `GET /Protocol` and `GET /SYSTEM` with their own operations. A viewer receives 403 unless the exact operation is later added to `ViewerAllowedAction`. |
| `api/routes.py` | Apply the approved ownership rule to `GET /User/<identity>` and `GET /Job/<event_id>` without inferring it during implementation. |
| `orchestrator/reasoning.py` | Extend the structured message plan with a validated requested-operation value when needed. Validate model output against `RequestedOperation`; a model cannot invent or authorize an operation. |
| `orchestrator/flows.py` | Keep existing safety, approval, and side-effect rules. Accept an already-authorized caller context only where flow behavior genuinely depends on caller role. |
| `bot/app.py` | Check the correct operation before `/profile`, `/settings`, and callback actions. The API remains authoritative even after a bot-side refusal. |
| `bot/interactions.py` | Generate refusals from the operation descriptor and caller role without listing hidden alternatives. Restrict `view_profile` and `view_settings` according to the approved matrix. |
| `bot/transports.py` | Preserve and normalize API 401/403 responses so the bot cannot accidentally transform a denial into a success message. |
| `bot/contracts.py` | Type operation-related outcomes where needed; do not copy the viewer allowlist into transport contracts. |
| `tools/terminal_client_viewer.py` | Continue sending the viewer identity through the API. Do not implement local permission shortcuts. |
| `tools/terminal_client_commander.py` | Continue sending commander identity; verify every supported surface remains available. |

### Test files

| File | Planned coverage |
|---|---|
| `tests/test_api_messages.py` | One allowed and one denied viewer case for each message operation; commander success for all message operations. |
| `tests/test_api_protocols.py` | Viewer cannot list or mutate protocols unless explicitly approved; commander can list and mutate. |
| `tests/test_api_system.py` | Viewer cannot receive agents, protocols, settings, or runtime system metadata unless explicitly approved; commander behavior remains complete. |
| `tests/test_api_jobs.py` | Apply the approved viewer job-visibility and ownership rules. |
| `tests/test_api_holds.py` | Viewer cannot resolve or approve holds; commander can. |
| `tests/test_api_notifications.py` | Viewer cannot poll commander notifications unless explicitly approved. |
| `tests/test_bot_app.py` | Commands and callback paths enforce the same operation as their API endpoint. |
| `tests/test_bot_transports.py` | 401/403 mapping is stable and contains no protected metadata. |

### Success criteria

- Every external entry point uses a typed operation and the shared authorization policy.
- A viewer cannot retrieve protocol definitions, sub-agent names, tool names, settings, commander rosters, hold data, or notification data unless the approved enum explicitly permits the corresponding operation.
- A denied request causes no event creation, queue reservation, tool call, database query for protected content, or side effect.
- A commander receives no authorization denial for any defined operation.
- API, Telegram, and terminal behavior agree for the same identity and operation.

### Implementation note (added on completion, 2026-08-29)

Every `require(level, "string")` call site in `api/routes.py` and `orchestrator/holds.py` (a second authorization point the Stage 0 inventory missed — it re-validates the answering caller's level at the moment a hold is resolved, independent of the route-level check) is now migrated to `RequestedOperation`. Because the migration is now complete everywhere in production code, the transitional legacy string path added in Stage 1 (`ACTION_REQUIREMENTS`, the string branch in `is_permitted`/`require`) was deleted, exactly as that stage's note said it would be — `is_permitted`/`require` now accept only `RequestedOperation`.

**`GET /SYSTEM` split**: implemented as one endpoint (not three), gated by `VIEW_PROFILE_OVERVIEW` (the least-privileged of the three) as the entry check, then building the JSON response field-group by field-group: `profile`/`event_types`/`areas`/`profile_file_changed` are always present once the entry gate passes; `agents`/`protocols`/`queued_events`/`held_events`/`scheduler` are included only if the caller also passes `VIEW_SYSTEM_INTERNALS`; `settings` only if the caller passes `VIEW_SETTINGS`. A viewer's response is therefore a strict subset with protected keys absent, not empty — matching §4.5's "absent rather than present as empty hints." This was chosen over splitting into three URLs to avoid an API/bot compatibility break; `bot/contracts.py::ProfileView`/`bot/transports.py::get_profile_view` now default `agents`/`protocols` to empty tuples when absent, and `bot/interactions.py::format_profile_view` omits those sections entirely for a viewer rather than printing an empty heading. `get_settings_view` raises a clean 403 `ApiRequestError` if `settings` is absent (defense in depth — `bot/app.py` already refuses `/settings view` client-side for a non-commander before ever calling it).

**Ownership scoping**: `GET /User/<identity>` denies a viewer with 403 for any identity other than their own. `GET /Job/<event_id>` denies a viewer with **404** (not 403) for an event they did not submit — deliberately indistinguishable from "unknown job ID," so a viewer cannot use this endpoint to learn that some other sender's job exists. A commander is unrestricted in both cases (e.g. `bot-service`, which resolves arbitrary callers' registrations, is always commander-level per README).

**Deferred to Stage 4**: `ask_question`'s "own submitted events only" scoping (§5 decision record) is not yet enforced — the `require(level, RequestedOperation.ASK_QUESTION)` gate now exists, but the underlying `HistoryQueryService`/`EventSearchCriteria` path it calls has no caller-identity filter yet. Enforcing it means touching `history/query.py`, which is explicitly Stage 4/5 scope, not Stage 2's. Until Stage 4 lands, a viewer's natural-language questions can still surface any event's history, same as before this plan. This gap closes in the same continuous work session, immediately next.

**Verified, no change needed**: `orchestrator/reasoning.py` — the model only ever emits one of five fixed `Literal["question","report","request","conversational","needs_clarification"]` intents, explicitly re-validated against that closed set (`valid_intents` check, raises `OrchestrationParseError` otherwise); `api/routes.py` — not the model — deterministically maps each intent to its `RequestedOperation` and enforces it. This already satisfies "a model cannot invent or authorize an operation" without further changes. `orchestrator/flows.py` — its one caller-dependent decision (`continue_from_risk_assessment`'s `originated_from_commander` flag, feeding `determine_approval_hold`) already existed and needed no change. `tools/terminal_client_viewer.py`/`tools/terminal_client_commander.py` — grepped for any local permission logic; none exists, both still simply forward identity through the API.

Full offline suite: 923 passed, 0 failed (203.03s), 6 pre-existing third-party CrewAI deprecation warnings, no new failures.

## 8. Stage 3 - Build Role-Aware, Modular Capability Context

**Status:** ✅ Complete (2026-08-29)

### Objective

Make identity and capability answers natural, profile-aware, modular, and safe for the caller's role.

### Files and required work

| File | Planned change |
|---|---|
| `orchestrator/capabilities.py` | New module defining `CapabilityDescriptor`, descriptor validation, and role-aware context construction. Descriptors use English facts and map to `RequestedOperation`; they do not contain final answers. |
| `orchestrator/reasoning.py` | Move or replace `SYSTEM_CAPABILITIES` and `build_system_capability_context` with the new builder. Keep `answer_conversationally` model-written and grounded only in the filtered context. |
| `orchestrator/reasoning.py` | Update prompts so the Main Agent identifies itself as the main agent managing the named profile's services, answers in the user's language, stays concise, and never reveals absent context. |
| `api/routes.py` | Stop constructing one global full system context at blueprint creation. Build a caller-specific context after authentication for each message request. |
| `profiles/contracts.py` | Preserve `profile_name` as a required loaded value. Add no response wording to the profile contract. |
| `profiles/loader.py` | Preserve strict non-empty `PROFILE_NAME` validation and ensure there is no module-path or generic-name fallback. Validate capability descriptor coverage at startup if descriptors depend on profile/runtime features. |
| `profiles/demo.py` | Keep `PROFILE_NAME = "For Tests"`; add no hardcoded identity response. |
| `profiles/template.py` | Keep `PROFILE_NAME` mandatory and document that it is the human-facing service name used by the Main Agent. |
| `agents/runtime.py` | Reuse existing runtime descriptors and exposed-tool metadata. Do not create a second manually maintained sub-agent/tool inventory. |
| `protocols/contracts.py` and `protocols/repository.py` | Reuse loaded protocol metadata for commander context. Do not create a duplicate protocol description registry. |

### Context rules

For a viewer:

- include `PROFILE_NAME` and the Main Agent identity;
- include only capability descriptors whose operations are viewer-allowed;
- omit protected protocol, sub-agent, tool, administrative-action, and unrelated configuration metadata;
- when asked about hidden categories, answer naturally that those details are not available to the caller without naming the hidden contents.

For a commander:

- include all capability descriptors;
- include loaded protocols, registered sub-agents, and exposed tools when the current question asks about them;
- derive all lists from runtime registries so additions become visible after restart without response-code changes.

### Test files

| File | Planned coverage |
|---|---|
| `tests/test_orchestrator_holds.py` | Prompt grounding, concise natural identity, profile-name use, same-language behavior, and no invented capability. |
| `tests/test_api_messages.py` | Separate viewer and commander identity/capability prompts and responses. Viewer prompt must not contain protected names. |
| `tests/test_agent_registry.py` | Adding a sub-agent changes commander context automatically and does not change viewer context unless an approved descriptor requires it. |
| `tests/test_protocol_repository.py` | Adding a protocol changes commander protocol context automatically and remains absent from viewer context. |
| `tests/test_profile_loading.py` | Missing, empty, whitespace-only, or control-character `PROFILE_NAME` fails startup. |
| `tests/test_demo_profile.py` | The demo profile exposes `For Tests` as the service name without storing a final answer. |

### Success criteria

- "Who are you?" identifies the Main Agent as manager of the active profile's service, never as a generic AI assistant.
- "What can you do for me?" is model-written and lists only caller-authorized capabilities.
- Adding or removing a viewer enum action automatically changes the viewer capability context and answer content without editing a final-response template.
- Adding a runtime protocol, sub-agent, or tool automatically changes the commander's relevant answer after restart.
- Viewer-facing prompts and outputs contain zero protected protocol names, sub-agent names, tool names, or commander-only operation names.
- No final identity or capability answer is hardcoded.

### Implementation note (added on completion, 2026-08-29)

New `orchestrator/capabilities.py`: `CapabilityDescriptor` (12 descriptors, each naming its authorizing `RequestedOperation`), a startup-time validator, `visible_capabilities(level)`, and `build_role_aware_system_context(level, ...)` — the role-aware replacement for `SYSTEM_CAPABILITIES`/`build_system_capability_context`, both deleted from `orchestrator/reasoning.py`. The 5 original capabilities (report_event, request_action, ask_current_state, ask_event_history, handle_human_review) all map to viewer-allowed operations, so a viewer sees the same 5; 7 new descriptors covering protocol management, settings, hold resolution, roster, notifications, and runtime description are commander-only, giving a commander a complete, grounded answer to "what can you do" that previously only existed informally. `orchestrator/flows.py` re-exports the new builder (its canonical-facade role per `docs/allowed_calls.md`) so `api/routes.py` keeps importing from `orchestrator.flows`, not the new module directly — `test_architecture.py`'s entry-point rule (`orchestrator` → `orchestrator.flows` only) stays satisfied. Registered in `docs/file_catalog.md` and `docs/allowed_calls.md`'s implementation-module list.

`api/routes.py`'s `build_messages_blueprint` no longer builds one global `system_context` at blueprint-creation time; `post_msg` now builds it fresh, per authenticated request, from the caller's `PermissionLevel`. A viewer's context has `protocols`/`sub_agents` absent entirely (not empty lists); `capabilities` is always exactly `visible_capabilities(level)`.

Strengthened `_build_conversational_prompt` to explicitly instruct: when a category is absent from the context, say plainly it isn't available to this caller without naming, counting, or hinting at what's missing.

**One disclosure channel found and mitigated beyond the plan's named files**: the merged planner's `_build_message_plan_prompt` (`orchestrator/reasoning.py`) builds its own `Protocols JSON`/`Agents JSON` blocks unconditionally from the full loaded registry — separate from `system_context` — because routing a report/request to the right protocol/agent genuinely requires the model to see real candidate names, regardless of caller role. This is *not* the same channel `system_context` filters, so Stage 3's role-aware context alone does not close it. Rather than restrict routing fidelity for a viewer (a bigger, unresolved design question the plan doesn't address), the prompt now explicitly forbids using `Protocols JSON`/`Agents JSON` as a source for `conversational_reply` under any circumstance, direct or indirect, keeping routing quality intact while adding an instruction-level guard against disclosure through this second channel. Prompt instructions are not a hard boundary — Stage 6's adversarial corpus is the closing verification for this specific channel, and its "viewer asks indirectly which protocol would be selected" scenario should probe it directly.

Verified, no change needed: `agents/runtime.py`, `protocols/contracts.py`/`repository.py` (the new builder reuses `registry.all()`/the loaded `protocols` tuple directly — no duplicate inventory); `profiles/contracts.py`, `profiles/loader.py`, `profiles/demo.py`, `profiles/template.py` (`PROFILE_NAME` validation already correct from prior work; capability descriptors are static, not profile-derived, so no startup coverage validation against profile data was needed).

Test coverage added/updated: `tests/test_api_messages.py` now has matched viewer/commander conversational-prompt tests proving a viewer's prompt contains neither protocol nor sub-agent names while a commander's does.

**Deviation from §4.3's original design, recorded for accuracy (docs/vocabulary.md updated to match, Stage 7):** no `CallerAccessPolicy` class was introduced. Its three planned methods were implemented as plain functions taking `PermissionLevel` directly — `is_permitted(level, operation)`, `require(level, operation)`, `visible_capabilities(level)` — which already satisfy §4.3's actual requirement (disclosure flags derived from `is_permitted`, never set manually; no profile data or model logic) without an added wrapping type. Similarly, `CapabilityDescriptor` does not carry a per-descriptor "metadata categories" field as §4.4 originally described — protocol/sub-agent disclosure is one coarser gate (`view_system_internals`) shared by both arrays together, not decided descriptor by descriptor.

Full offline suite: 924 passed, 0 failed (207.94s), 6 pre-existing third-party CrewAI deprecation warnings, no new failures.

## 9. Stage 4 - Produce Natural, Explainable Event-History Answers

**Status:** ✅ Complete (2026-08-29)

### Objective

Replace technical event listings with natural responses while preserving exact database facts, provenance, and caller filtering.

### Files and required work

| File | Planned change |
|---|---|
| `history/field_catalog.py` | New response-layer catalog defining the English meaning, display label, sensitivity category, and formatting guidance for each event and step field that may be explained. This is code metadata, not persistence schema. |
| `history/contracts.py` | Add immutable response-layer types such as `EventFieldDefinition` and `SemanticEventView`. Keep `HistoryAnswer` provenance fields. |
| `history/query.py` | Convert database event records into filtered semantic event views before prompt construction. Never send unrestricted raw rows to the History Agent. |
| `history/query.py` | Replace `_event_line` output for `latest` and `list` with natural History Agent formulation. Preserve deterministic event ordering, match count, truncation, and source IDs in application code. |
| `history/query.py` | Require each referenced event to remain identifiable by stable `Event ID`, including numbered multi-event answers suitable for follow-up references. |
| `agents/standard_agents.py` | Update the History Agent's English system prompt to explain semantic fields faithfully, distinguish missing from false values, preserve contradictions, and avoid exposing omitted fields. |
| `orchestrator/reasoning.py` | Keep history routing and `HistoryQuerySpec` validation application-controlled. Return natural text plus existing provenance. |
| `api/routes.py` | Preserve the `answer` field and optional `provenance`. Apply caller filtering before invoking the History Agent. |
| `persistence/contracts.py` | No schema change. Confirm the existing event-read interface supplies the fields needed by the semantic view. Add only read-contract typing if required. |
| `persistence/schema.py` | No change. Explicitly excluded from this stage. |
| `persistence/sqlite_store.py` | No conversation or event schema change. Query changes are allowed only if an existing required event field is not returned by the current read path. |

### Field interpretation requirements

The field catalog must explain at least the fields currently persisted in `events` and `event_steps`. It must explicitly distinguish:

- `received_at`: when AgentsHub received the report;
- `occurred_at`: when the event is believed to have occurred;
- `occurred_at_is_fallback`: whether occurrence time was substituted rather than extracted;
- `raw_text`: the original submitted text;
- `description`: the extracted operational description;
- `classification`, `area`, `entities`, and `severity`: extracted event attributes;
- `risk_level` and `risk_reason`: assessed risk and its evidence;
- `selected_protocol` and `protocol_reason`: the chosen handling protocol and selection reason;
- clarification and approval fields: whether human input was required and how it was resolved;
- precedent fields: which prior events matched and whether one closed the new event;
- `steps`: ordered specialist tasks, allowed tools, attempts, and results;
- `insight_text`, `outcome`, and `outcome_failure_reason`: final synthesis and persisted result;
- `trace_id`, `conversation_id`, `deadline_at`, and ingestion identity: internal metadata that must not be exposed merely because it exists.

### Test files

| File | Planned coverage |
|---|---|
| `tests/test_history_query.py` | Natural `latest`, `list`, `event_details`, and narrative responses; exact ordering, count, truncation, and source IDs. |
| `tests/test_question_answering.py` | History answer text plus provenance, semantic validation, and no unsupported claims. |
| `tests/test_integration_history_accuracy.py` | Every stated fact must be traceable to the selected database records; contradictory records remain contradictory. |
| `tests/test_history_agent.py` | Field meaning, missing-value behavior, Event ID retention, and natural formulation. |
| `tests/test_persistence_events.py` | No schema behavior change; existing fields still round-trip unchanged. |

### Success criteria

- Event lists no longer expose raw technical lines such as `outcome=...`.
- Answers lead with a natural conclusion and explain relevant event facts in the user's language.
- Every event discussed remains identifiable by `Event ID`.
- The model can explain the meaning of a relevant stored field without inventing database semantics.
- All statements are grounded in the filtered records supplied for the query.
- Match count, ordering, truncation, filters, timezone, and source IDs remain accurate.
- Restricted/internal fields never enter viewer-facing prompts or responses.
- No SQLite migration or new table is introduced.

### Implementation note (added on completion, 2026-08-29)

New `history/field_catalog.py`: `EVENT_FIELD_CATALOG` (30 `EventFieldDefinition` entries — type added to `history/contracts.py` alongside new `SemanticEventView`), each with a stable key, English label, plain-English meaning, and `category` of `"narrative"` or `"internal"`. The internal category matches §9's own list exactly (`trace_id`, `conversation_id`, `deadline_at`, and ingestion identity — `source`, `sender_identity`, `source_message_id`) and is excluded from every semantic view **unconditionally, for any caller** — not role-gated, since these are plumbing details with no narrative meaning, not information some caller is more or less trusted with.

`history/query.py::_build_semantic_event_view` converts one raw persistence record into a `SemanticEventView` containing only present, non-null, narrative-category fields; `_history_agent_prompt` serializes one or more views plus the field-meanings glossary into the History Agent's prompt. `latest` and `list` now call the History Agent for a natural formulation instead of `_event_line`'s raw `f"{event_id}: {occurred_at}, ..., outcome=..."` template (deleted); both are instructed to always state each event's Event ID explicitly, and `list`'s truncation note is still appended in application code, guaranteeing exact count wording regardless of what the model produces. `event_details` and the narrative/`similar_cases`/`compare`-adjacent fallback path now feed semantic views instead of raw `json.dumps(events)`. `agents/standard_agents.py`'s `HistoryAgent.system_prompt` was extended: treat an absent field as "not available" never a false negative, never speculate about an omitted field's existence, explain field meanings only from the supplied glossary, always state Event IDs including within a numbered list.

**Closed the Stage 2-deferred `ask_question` ownership-scoping gap.** This required a query-level filter that did not exist: `persistence/contracts.py::EventSearchCriteria` gained an optional `sender_identity: str | None = None` field, and `persistence/sqlite_store.py::_search_where` gained one more `AND sender_identity = ?` clause when it is set — both files are outside §13's explicitly-excluded list, and this is a query-logic addition against an *already-existing* `events` column, not a schema or migration change, so no stop-and-return-to-user gate applied. `HistoryQueryService.query_spec`/`answer_most_recent_event` take an optional `sender_identity_filter`; when set, it flows into every `EventSearchCriteria` the call builds, so `count`/`aggregate`/`compare`/`latest`/`list`/`event_details` are all scoped through the one shared `_criteria`/`_search_where` path — including denying visibility of an explicitly-requested `event_id` that is not the caller's own, since the criteria's `event_id IN (...)` and `sender_identity = ?` clauses combine with `AND`. `HistoryQueryService.query()` (the free-text period-summary path, used for the "ask a history sub-agent a task" fan-out) cannot be scoped the same way: a rolled-up daily/monthly/yearly summary is prose that may describe many senders' events with no way to redact just one sender's share. When `sender_identity_filter` is set, `query()` therefore drops every summary-level source entirely and keeps only raw per-event sources matching that sender — a deliberate, documented trade-off (safe, but a viewer's broader free-text period questions answer from raw events only, never the summary optimization) rather than either riskily including mixed-sender prose or fully disabling this call shape.

`orchestrator/reasoning.py::answer_question`/`answer_question_from_plan` both gained an optional `caller_sender_identity_filter` parameter, threaded to every `history_query_service` call each makes (including the sub-agent fan-out's per-task `HistoryAgent` calls). `api/routes.py`'s `/Msg` question-intent branch now computes `caller_sender_identity_filter = None if level is PermissionLevel.COMMANDER else caller_identity` (the authenticated `X-Identity`, not the message body's self-reported `sender_identity` field, which is for the event being submitted *now*, not a filter on past history) and passes it through.

Test coverage added: `tests/test_history_query.py` gained direct `HistoryQueryService`-level proofs that `sender_identity_filter` restricts `count`/`list`/`answer_most_recent_event` correctly and leaves an unset filter (commander) unrestricted, plus proof that internal fields (trace_id/conversation_id/deadline_at/source_message_id) never reach the History Agent's prompt while the field-meanings glossary does. `tests/test_api_messages.py` gained one full end-to-end `POST /Msg` test proving a viewer's structured count only covers their own submitted events while a commander's covers all.

Full offline suite: 931 passed, 0 failed (202.63s), 6 pre-existing third-party CrewAI deprecation warnings, no new failures.

## 10. Stage 5 - Use Existing Conversation Memory for Event Follow-Ups

**Status:** ✅ Complete (2026-08-29)

### Objective

Allow the Main Agent to continue a natural conversation about an event already discussed, using the existing stored turns and only small routing/prompt changes.

### Files and required work

| File | Planned change |
|---|---|
| `api/routes.py` | Keep the existing `conversation_id`, fetch, `_remember`, TTL, and pruning behavior. Pass `prior_messages` to every active question-planning path, including the legacy path while it remains selectable. |
| `orchestrator/reasoning.py` | Add optional conversation messages to legacy question routing and direct-lookup classification. Instruct routing to resolve references from recent turns and emit `HistoryQuerySpec(operation="event_details", event_ids=...)` when one event is clear. |
| `orchestrator/reasoning.py` | Keep the existing merged-planner rule that conversation facts are references only and must be retrieved again from history. Apply the same rule to the legacy path. |
| `history/query.py` | Re-fetch a referenced event by validated Event ID and formulate the new answer from the current persisted record and field catalog. |
| `persistence/sqlite_store.py` | No new methods or schema changes are planned. Reuse `fetch_conversation_messages`, `append_conversation_message`, and existing event search/fetch methods. |
| `persistence/schema.py` | No change. Do not add a conversation-reference table, multi-event link table, JSON reference column, or migration. |
| `bot/app.py` | Preserve the stable Telegram conversation key based on chat and thread. |
| `tools/terminal_client_viewer.py` and `tools/terminal_client_commander.py` | Preserve one stable conversation ID per terminal session. |

### Required follow-up behavior

- If the previous answer discussed exactly one event, phrases such as "that event" resolve to it.
- If a numbered answer discussed multiple events, phrases such as "the first event" or "the second one" resolve according to the stored answer's deterministic order.
- If several events remain plausible, the Main Agent asks a short clarification question instead of guessing.
- The remembered answer must include enough visible identifying information, especially Event IDs, for the model to resolve the reference from ordinary conversation text.
- After reference resolution, the current event record is fetched again. The stored assistant message is never accepted as the latest factual state.
- Authorization and field filtering are recalculated from the current caller on every turn. Prior visibility does not grant future visibility.

### Test files

| File | Planned coverage |
|---|---|
| `tests/test_response_improvements.py` | Existing conversation isolation, TTL, pruning, and chronology remain unchanged; add event follow-up scenarios without schema assertions changing. |
| `tests/test_api_messages.py` | End-to-end single-event and multi-event follow-ups using the same `conversation_id`. |
| `tests/test_question_answering.py` | Reference resolution produces validated event IDs and fresh history queries. |
| `tests/test_history_query.py` | `event_details` returns current persisted facts for the referenced ID. |
| `tests/test_bot_app.py` | Telegram chat/thread separation prevents one conversation from resolving another conversation's event. |
| `tests/test_bot_transports.py` | `conversation_id` remains present across consecutive submissions. |

### Success criteria

- A user can ask about an event, then ask a natural follow-up without repeating its ID.
- The Main Agent resolves clear singular and ordinal references from existing conversation turns.
- Ambiguous references produce clarification rather than a guessed event.
- Updating an event between turns causes the follow-up to reflect the updated database value, proving fresh retrieval.
- Two conversation IDs cannot resolve each other's events from memory.
- A role or permission change between turns is enforced immediately.
- No new table, migration, memory service, or large architectural change is introduced.

### Implementation note (added on completion, 2026-08-29)

No schema, migration, or new persistence method — exactly as scoped. `orchestrator/reasoning.py`'s legacy question path (`_build_direct_lookup_prompt`, `_build_agent_selection_prompt`, `answer_question`) gained an optional `conversation_messages` parameter, matching what the merged planner (`plan_message`/`_build_message_plan_prompt`) already had. Both prompt builders now carry explicit instructions: a reference to a previously discussed event ("that event", "the first one", "what happened after that?") must be resolved, using conversation context only to identify the Event ID(s) meant, into a `history` route with `operation="event_details"` and those `event_ids` — never answered from what the conversation text itself claims, always re-fetched fresh through the same `HistoryQueryService.query_spec` path Stage 4 built. An ambiguous reference routes to `clarification` instead of guessing. The merged planner's `_build_message_plan_prompt` got the identical instruction added, since this requirement is planner-mode-independent. `_build_direct_lookup_prompt` also now receives conversation context, with an explicit instruction not to misclassify a backward reference as a "most recent event" direct lookup.

`api/routes.py`'s `/Msg` question branch now passes the already-fetched `prior_messages` into `answer_question` (the legacy path) — the merged path already received it. Re-fetching "fresh, not from memory" was already guaranteed by Stage 4's `query_spec`/`event_details` design; Stage 5 only needed to get conversation context to the routing decision that produces the `event_ids`.

Verified, no change needed: `bot/app.py`'s Telegram `conversation_id` (`f"telegram:{chat_id}:{thread_id or 'main'}"`) was already stable and chat/thread-scoped; `tools/terminal_client_viewer.py`/`_commander.py`'s one-conversation-ID-per-session was already stable (both confirmed in Stage 2); `persistence/sqlite_store.py`'s `fetch_conversation_messages`/`append_conversation_message` needed no changes.

Test coverage added: an orchestrator-level test in `tests/test_question_answering.py` proving a scripted "that event" reference produces a validated `event_details` query with the correct `event_ids`, and that the conversation context actually reached both prompts (not just that the scripted response happened to name the right ID). One full end-to-end `tests/test_api_messages.py` test proving a second `/Msg` call sharing a `conversation_id` carries the first turn's content into the routing prompt (required adding an opt-in `conversation_history_turns` parameter to the shared `tests/api_fakes.py::build_context`/`_FakeLoadedProfile`, defaulting to `0` — conversation memory off — to leave every other existing test's behavior unchanged). One `tests/test_bot_app.py` test proving distinct chats and distinct threads within one chat never share a `conversation_id` (added `message_thread_id` support to the file's `_FakeMessage`/`_fake_update` test helpers). `tests/test_bot_transports.py`'s conversation-id-passthrough in `HttpApiClient.submit_message` was not touched by this stage and was not given new coverage — noted rather than silently skipped.

Full offline suite: 934 passed, 0 failed (262.35s), 6 pre-existing third-party CrewAI deprecation warnings, no new failures.

## 11. Stage 6 - Adversarial Disclosure and Regression Gate

**Status:** ✅ Complete (2026-08-29)

### Objective

Prove that dynamic answers and conversation context cannot bypass the viewer policy.

### Files and required work

| File | Planned change |
|---|---|
| `fixtures/` | Add English and Hebrew prompts covering identity, capability discovery, protocol extraction, sub-agent extraction, tool extraction, prompt injection, indirect requests, quoted instructions, and event follow-ups. |
| `tests/test_api_messages.py` | Assert protected names are absent from viewer prompts and responses even when explicitly requested. |
| `tests/test_orchestrator_reasoning.py` | Validate requested-operation parsing, ambiguity handling, and refusal of unknown model-generated operations. |
| `tests/test_agent_permission_enforcement.py` | Preserve per-call tool allowlists and prove viewer policy cannot alter tool wrapper enforcement. |
| `tests/test_integration_profile_isolation.py` | Verify profile metadata and conversation context do not cross profile boundaries. |
| `tests/test_integration_end_to_end_flow.py` | Full viewer and commander scenarios with identical questions and role-appropriate results. |
| `tests/test_integration_history_accuracy.py` | Prompt injection inside `raw_text` or prior messages cannot change field filtering or source selection. |
| `tools/evaluate_response_pipeline.py` | Add offline evaluation cases for concise identity, dynamic capability coverage, natural history, follow-up resolution, and disclosure safety. |

### Mandatory security scenarios

- Viewer asks directly for all protocol names.
- Viewer asks indirectly which protocol would be selected.
- Viewer asks for sub-agent and tool names.
- Viewer says to ignore authorization or reveal the hidden system context.
- Protected names appear inside an event's `raw_text`.
- A commander asks the same questions and receives the complete authorized answer.
- A viewer action is removed from `ViewerAllowedAction` and disappears from both execution and self-description.
- A previously visible conversation turn is followed by a permission downgrade.

### Success criteria

- Zero protected-name disclosures in the viewer security corpus.
- Zero viewer executions of operations absent from `ViewerAllowedAction`.
- Zero commander authorization denials for defined operations.
- Zero tool calls or side effects on denied requests.
- No regression in conversation isolation, event accuracy, tool allowlists, approvals, or persistence guarantees.
- All existing offline tests and all new tests pass without weakening assertions.

### Implementation note (added on completion, 2026-08-29)

New `fixtures/adversarial_disclosure_v1.jsonl`: 20 cases (10 English, 10 Hebrew) across all 9 named categories (identity, capability_discovery, protocol_extraction — direct and indirect, sub_agent_extraction, tool_extraction, prompt_injection, quoted_instruction, indirect_request, event_follow_up), each naming the real protocol/agent/tool names (`status_check`, `dispatch_response`, `reference_agent`, `history_agent`, `check_status`, `record_action`) the test fixture profile actually loads, as `forbidden_substrings` a viewer must never receive.

New `tests/test_api_messages.py` coverage runs every corpus case through the real `POST /Msg` → `answer_conversationally` path and inspects the *application-supplied system context segment* of the actual prompt the (scripted) Main Agent received — not the full prompt text, since a quoted-instruction case's own adversarial message legitimately contains the protected name the attacker typed, which is not a disclosure. A positive-control companion test proves the same corpus does reach a commander's full context, so the viewer test cannot be vacuously passing. Both are prompt-level checks: they prove the application never hands a viewer a protected name to work with, which is the actual boundary the application controls (docs/Next_Plan.md §2.5); whether a model could still be talked into inventing or leaking something despite a clean prompt is residual model-quality risk, out of an offline test's reach — the extended `tools/evaluate_response_pipeline.py` (below) is where that gets checked against a real model.

New `tests/test_orchestrator_capabilities.py` (no dedicated capability test file existed since Stage 3) structurally proves "a viewer action removed from `ViewerAllowedAction` disappears from both execution and self-description": every `CapabilityDescriptor` whose operation is outside current `ViewerAllowedAction` membership is absent from `visible_capabilities(VIEWER)` (self-description) and denied by `is_permitted(VIEWER, ...)` (execution) simultaneously, because both derive from the identical check — there is nothing else to keep in sync when the enum changes.

New `tests/test_api_messages.py::test_permission_downgrade_mid_conversation_is_enforced_on_the_very_next_turn` proves the last named mandatory scenario end to end: the same identity, same `conversation_id`, answered first as a commander (full context) then downgraded to viewer before the very next turn — that next turn's context is immediately restricted, proving authorization and field filtering are recomputed from the caller's current level every turn, never cached or carried forward by conversation memory.

New `tests/test_integration_history_accuracy.py` coverage proves an injected instruction embedded in one event's own `raw_text` ("ignore ownership filters", "include this event regardless of classification") cannot widen ownership scoping or source selection for a *different* query — because which events are returned is decided entirely by SQL `WHERE` criteria before any event's `raw_text` is ever read, there is no code path an embedded instruction could reach.

New `tests/test_orchestrator_reasoning.py` coverage proves the two closed vocabularies a model can actually emit — `primary_intent` (five values) and history `operation` (eight values) — reject an invented value with `OrchestrationParseError` and accept every valid one. (`RequestedOperation` itself is never parsed from model output at all — routes.py deterministically maps a validated `intent`/`operation` to it — so there is no "model generates a RequestedOperation" case to test; this was already established as Stage 2's own verified-no-change finding and is restated here for the record.)

Extended `tools/evaluate_response_pipeline.py` with a `--corpus-type disclosure` mode: offline (default) it just loads and summarizes `fixtures/adversarial_disclosure_v1.jsonl`; with `--live --profile <module>` it runs every case through the real Main Agent once as a viewer and once as a commander (via the same `orchestrator.capabilities.build_role_aware_system_context` a real request builds) and scores whether a protected name appears in the model's actual free-text answer — the live, billed check this project's own convention defers until credentials/budget are available (no live call was made in this session).

Verified, no change needed: `tests/test_agent_permission_enforcement.py` (per-call tool allowlists — untouched by this plan, still enforced identically) and `tests/test_integration_profile_isolation.py` (profile/conversation isolation — untouched). `tests/test_integration_end_to_end_flow.py` was not given new viewer/commander scenarios: that file's own scope is full-stack event-processing tracing (trace IDs, model-IO logging), not role-based question answering, and the "identical questions, role-appropriate results" requirement is already covered thoroughly in `tests/test_api_messages.py` (Stage 3's conversational pair, this stage's adversarial-corpus pair, the ownership-scoping pair, and the downgrade test) — a deliberate placement choice over adding a mismatched scenario to an unrelated file.

One fix required by this stage's own additions: `tools/evaluate_response_pipeline.py` initially imported `orchestrator.capabilities` directly, which `tests/test_architecture.py` correctly rejected (`orchestrator`'s only cross-package entry point is `orchestrator.flows`) — fixed by importing `build_role_aware_system_context` through `orchestrator.flows`, consistent with every other cross-package caller.

Full offline suite: 951 passed, 0 failed (280.14s), 6 pre-existing third-party CrewAI deprecation warnings. One transient Windows temp-file-rename `PermissionError` in `tests/test_migrations.py` on one run, confirmed unrelated (passed cleanly both in isolation and on the following full run) — not a regression from this stage's changes.

## 12. Stage 7 - Documentation, Rollout, and Completion

**Status:** ✅ Complete (2026-08-29)

### Objective

Make the new policy and behavior understandable to profile authors, API clients, operators, and future developers.

### Files and required work

| File | Planned change |
|---|---|
| `README.md` | Document viewer/commander behavior, profile-name identity, natural capability answers, existing conversation memory, and example follow-up usage. |
| `docs/profile_spec.md` | State that `PROFILE_NAME` is mandatory and explain role-filtered capability disclosure. Remove any claim that every caller receives protocols, agents, and tools. |
| `docs/api_spec.md` | Document operation-specific 403 responses and which endpoints expose protected metadata. Do not publish viewer access until the enum is approved. |
| `docs/allowed_calls.md` | Publish the final operation matrix and identify `ViewerAllowedAction` as the sole viewer allowlist. |
| `docs/agent_authoring.md` | Explain that agent/tool descriptors may be shown to commanders and must contain safe English technical descriptions. |
| `docs/vocabulary.md` | Finalize the new authorization and semantic-history terms. |
| `docs/work_plan.md` | Add references to this approved next plan without rewriting completed historical tasks. |
| `docs/progress.md` | Append one English entry after each completed subtask, including tests, deviations, and gate results. Never rewrite existing entries. |

### Rollout sequence

1. Capture the current offline test baseline without hardcoding the test count in documentation.
2. Implement Stage 1 behind tests; no production rollout yet.
3. Apply Stage 2 boundary enforcement and run the complete permission matrix.
4. Enable role-aware context in a test profile and inspect captured prompts for disclosure.
5. Enable natural history answers and compare factual output against stored fixtures.
6. Enable follow-up routing using existing conversation memory.
7. Run the adversarial corpus and the full offline suite.
8. Run a controlled live-model evaluation for natural wording and reference resolution.
9. Roll out to staging, then one canary profile, then all profiles after the success gates remain green.

### Rollback

- Runtime feature switches may restore the previous planner path if a model-quality gate fails.
- Authorization must not roll back to exposing protected viewer information once the new enum policy is active.
- No database rollback is required because this plan introduces no schema changes.
- Profile changes continue to take effect after restart under the existing profile lifecycle.

### Success criteria

- Documentation and runtime behavior describe the same authorization matrix.
- All technical documentation, prompts, comments, descriptors, and identifiers added by this work are in English.
- Viewer and commander examples demonstrate visibly different capability answers without hardcoded final text.
- Operators can identify the active service through `PROFILE_NAME`.
- The full offline suite passes, the live evaluation meets the approved rubric, and no security scenario fails.
- Every completed subtask has an append-only progress record.

### Implementation note (added on completion, 2026-08-29)

`README.md`: new "Roles and capability disclosure" section (the `ViewerAllowedAction` policy, ownership scoping for `ask_question`/`view_job_status`/`view_user_registration`, `GET /SYSTEM` field omission) and a new "Conversation memory and event follow-ups" section with a worked follow-up example; the existing "System self-description" paragraph updated to state disclosure is role-filtered, not identical for every caller.

`docs/profile_spec.md`: the "Main Agent identity and capability answers" section rewritten to state runtime metadata is role-filtered per caller (`orchestrator/capabilities.py`, `ViewerAllowedAction`) — removed the prior unconditional "the model receives ... loaded protocols, and the registered agents and their exposed tools" claim, which predated this plan and was no longer accurate for a viewer.

`docs/api_spec.md`: new "Authorization" section stating the `RequestedOperation`/`ViewerAllowedAction` model, the three ownership-scoped operations, and that `GET /SYSTEM` is role-filtered; per-endpoint operation names and ownership notes added throughout (`GET /Protocol` family now commander-only, `GET /User/<identity>` and `GET /Job/<event_id>` ownership scoping, `POST /Msg`'s per-intent operations, `PUT /SYSTEM`/`POST /Approve`/`POST /Clarify` operation names); `GET /SYSTEM`'s example section now shows both the commander (full) and viewer (subset) response shapes; one stale `auth.permissions.ACTION_REQUIREMENTS` reference (removed in Stage 2) corrected.

`docs/allowed_calls.md`: the Stage 0 "Operation matrix" is confirmed to already match the completed implementation exactly (no correction needed) and is now labeled as the completed matrix, not a proposal; its `GET /SYSTEM` note updated from "will be split" to the actual implemented mechanism (one endpoint, field-group-by-field-group gating); `orchestrator`/`history` package rows already list `capabilities`/`field_catalog` as implementation modules (added when those files were created in Stages 3–4).

`docs/agent_authoring.md`: new section stating that a sub-agent's `name`/`role` and each tool's `name`/`description` may reach a commander verbatim through the role-aware capability context, so they must be safe, accurate English technical text.

`docs/vocabulary.md`: `RequestedOperation`, `ViewerAllowedAction`, `CapabilityDescriptor`, and `SemanticEventView` entries confirmed accurate against the final implementation; `CallerAccessPolicy`'s entry and `CapabilityDescriptor`'s "metadata categories" mention corrected to describe what was actually built (plain functions taking `PermissionLevel` directly; one coarser protocol/sub-agent disclosure gate) rather than the original Stage 0 design that was simplified during implementation — the same correction is recorded in Stage 3's own implementation note above.

`docs/work_plan.md`: new dated section at the end referencing `docs/Next_Plan.md` as a separate, completed follow-on plan, without rewriting any `work_plan.md` historical section; states that where the two documents disagree (e.g. `auth`'s permission model), `docs/Next_Plan.md` is authoritative going forward.

`docs/progress.md`: append-only entries added after every stage throughout this implementation (Stages 0–6 already recorded before this entry) — no prior entry rewritten.

**Not done in this session, consistent with this project's standing convention** (every prior `IMPROVE-*`/Mission progress entry states the same): no paid live-model evaluation was run. `tools/evaluate_response_pipeline.py --corpus-type disclosure --live` (Stage 6) is ready to run against a real profile and credentials when the user chooses to spend the budget; until then, the "controlled live-model evaluation" rollout step below remains outstanding, and rollout beyond local/offline verification should wait on it.

Full offline suite re-confirmed clean after all documentation edits: 951 passed, 0 failed (251.38s), 6 pre-existing third-party CrewAI deprecation warnings. `git diff --check` reported no whitespace errors (only line-ending normalization notices) across every file this plan touched.

## 13. File Impact Summary

### Files expected to change

- `auth/permissions.py`
- `auth/__init__.py`
- `api/request_boundary.py`
- `api/routes.py`
- `orchestrator/reasoning.py`
- `orchestrator/flows.py` only if caller context is required at a flow boundary
- `orchestrator/capabilities.py` (new)
- `history/contracts.py`
- `history/query.py`
- `history/field_catalog.py` (new)
- `agents/standard_agents.py`
- `profiles/contracts.py` only if capability validation needs a typed field
- `profiles/loader.py`
- `profiles/demo.py`
- `profiles/template.py`
- `bot/app.py`
- `bot/interactions.py`
- `bot/transports.py`
- `bot/contracts.py` only if a typed denial outcome is needed
- `tools/terminal_client_viewer.py` only for verification or stable-session fixes
- `tools/terminal_client_commander.py` only for verification or stable-session fixes
- the test and documentation files listed in Stages 1 through 7

### Files explicitly not expected to change

- `persistence/schema.py`
- SQLite migration definitions
- the shape of `conversation_messages`
- user records beyond their existing viewer/commander role
- event side-effect and idempotency guarantees

If implementation discovers a need to change any explicitly excluded file or introduce a new persistence structure, work must stop and return to the user for approval. It must not be treated as an implementation detail.

## 14. Definition of Done

The complete plan is done only when all of the following are true:

1. The user has approved the exact initial `ViewerAllowedAction` members.
2. Commander authorization is unrestricted for every defined operation.
3. Viewer authorization is exactly equal to enum membership.
4. Viewer capability answers are natural, dynamic, and limited to permitted actions.
5. Viewer prompts and outputs do not expose protected protocols, sub-agents, tools, or commander-only actions.
6. Commander answers dynamically reflect the active profile, protocols, sub-agents, and tools.
7. The Main Agent identifies itself using the mandatory profile name.
8. Event-history answers are natural, semantically explained, ordered, and provenance-backed.
9. Event follow-ups work through the existing conversation memory with fresh database retrieval.
10. No new conversation table, reference table, migration, or replacement memory system exists.
11. Authorization is enforced before protected reads, model disclosure, queueing, tools, and side effects.
12. All existing and new offline tests pass, and the controlled live-model rubric passes.
13. All new technical text in the repository is English.
14. Documentation matches the implemented behavior and progress is recorded append-only.

### Final accounting (added on completion, 2026-08-29)

All 8 stages (0–7) are implemented, tested, and documented. Items 1–11, 13, and 14 above are met: the approved `ViewerAllowedAction` membership is implemented and enforced exactly (item 1–3, §5/§6), viewer answers are natural/dynamic/limited (item 4, Stage 3), viewer prompts/outputs are proven free of protected names by the adversarial corpus (item 5, Stage 6), commander answers reflect the live profile/protocols/sub-agents/tools (item 6, Stage 3), the Main Agent identifies itself via `PROFILE_NAME` (item 7, pre-existing + preserved), event-history answers are natural/semantic/ordered/provenance-backed (item 8, Stage 4), follow-ups work through existing conversation memory with fresh re-fetch (item 9, Stage 5), no new conversation/reference table or migration exists (item 10, verified throughout), authorization gates every protected read/disclosure/queue/tool/side-effect (item 11, Stages 1–2), all new technical text is English (item 13), and documentation now matches the implemented behavior with append-only progress records (item 14, this stage).

**Item 12 is partially outstanding**: the full offline suite passes (951 tests, 0 failures, confirmed repeatedly through every stage). The "controlled live-model rubric" half of item 12 has not run — no paid live-model call was made in this implementation session, consistent with this project's standing convention of deferring billed evaluation to a deliberate, budgeted decision by the user. `tools/evaluate_response_pipeline.py --corpus-type disclosure --live --profile <module>` (Stage 6) is ready to run this the moment the user chooses to spend that budget. Until it runs and passes, this plan should be considered **functionally complete and locally verified, but not yet cleared for the live-model rollout gate** in §12's rollout sequence step 8 onward.
