# AgentsHub

AgentsHub is a profile-driven, multi-agent operations system. It receives reports, questions, and requests through HTTP or Telegram; classifies and reasons about them; selects and executes approved protocols; pauses for human clarification or approval when required; and stores events, outcomes, notifications, summaries, and structured logs in SQLite.

Each deployment is defined by a Python profile. A profile selects its agents, protocols, event types, areas, database path, API port, model tiers, and initial live settings. Separate profiles can run from the same codebase without sharing runtime state or databases.

## Main capabilities

- HTTP API and Telegram frontend with identity-based authorization.
- Main and specialist agents backed by CrewAI-compatible model providers.
- Per-call tool allowlists and side-effect/idempotency enforcement.
- Protocol selection, task formulation, retries, and final judgment.
- Commander clarification and approval holds that survive restarts.
- Indexed historical questions by time, event type, area, outcome, protocol, or event ID, plus precedent matching and scheduled summaries.
- Serialized SQLite writes, notification cursors, and structured trace logs.
- Profile editing with atomic source writes and three immediately persisted live settings.

## Requirements

- Python 3.11 or newer.
- A model-provider API key supported by CrewAI.
- A Telegram bot token when running the Telegram frontend.

From the repository root, create and activate a virtual environment, then install the runtime dependencies:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

For development and testing, also install the test dependencies:

```powershell
python -m pip install -r requirements-dev.txt
```

## Quick start

The commands below are PowerShell commands and must be run from the repository root. The included `profiles.demo` profile listens on `127.0.0.1:8902` and stores its database in the operating system's temporary directory.

### 1. Configure the environment

Copy `.env.example` to `.env`, replace every fake token/key, and choose the model provider and model names for both tiers:

```powershell
Copy-Item .env.example .env
```

Load the file into the current PowerShell process:

```powershell
.\load-env.ps1
```

AgentsHub does not read `.env` automatically. Run `.\load-env.ps1` in every new terminal before starting the API, bot, or an administration command. On another shell, export the same variables using that shell's normal mechanism.

The important variables are:

- `BOT_TOKEN`
- `BOT_SERVICE_KEY` — the secret the bot sends alongside its `bot-service` identity; see step 2 below. Generate one with `python -c "import secrets; print(secrets.token_urlsafe(32))"`.
- `CORE_MODEL_PROVIDER`, `CORE_MODEL_NAME`, and `CORE_MODEL_API_KEY_ENV`
- `SUB_MODEL_PROVIDER`, `SUB_MODEL_NAME`, and `SUB_MODEL_API_KEY_ENV`
- The key variables named by `CORE_MODEL_API_KEY_ENV` and `SUB_MODEL_API_KEY_ENV`
- `DEEP_DEBUG=false` for normal operation; enable it only for a bounded commander diagnostic session.

Profiles contain only environment-variable names, never secret values.

### 2. Register users

A new deployment has no users. Register the first human commander and the bot's service identity before starting the Telegram frontend:

```powershell
python -m cli.user_admin --profile profiles.demo add --telegram-id <your-telegram-id> --level commander
python -m cli.user_admin --profile profiles.demo add --telegram-id bot-service --level commander
```

Use the human user's numeric Telegram ID for `<your-telegram-id>`. The `bot-service` row is an internal service identity and must remain at commander level. If you only use a terminal client, it provisions its own temporary test identity and ensures `bot-service` exists automatically.

This registration is not sufficient by itself — `bot-service` is a fixed, public string, so `BOT_SERVICE_KEY` (see step 1) must also be set, identically, for both the API and the bot process. Without it, calls the bot makes as its own service identity (notifications, the commander roster, profile-change checks) are rejected; a human's own messages are unaffected either way.

User administration is intentionally available only from the host CLI:

```powershell
python -m cli.user_admin --profile profiles.demo list
python -m cli.user_admin --profile profiles.demo update --telegram-id <id> --level viewer
python -m cli.user_admin --profile profiles.demo remove --telegram-id <id>
```

### 3. Start the API

```powershell
python -m api.app profiles.demo
```

Keep this process running. The API binds to `127.0.0.1` by default and initializes or migrates the profile's SQLite database during startup. Use `--host` only when the deployment has appropriate network and TLS controls.

Before opening the HTTP listener, startup imports CrewAI and makes one real,
minimal provider call for every unique Provider/Model configured by the active
profile. This can be billed and fails startup if any model, credential,
provider, response, or 30-second timeout check fails. Queue workers and
scheduled summaries do not start before all warmup checks succeed.

From another terminal with the environment loaded, verify the running deployment with a registered identity:

```powershell
Invoke-RestMethod `
  -Uri http://127.0.0.1:8902/SYSTEM `
  -Headers @{ "X-Identity" = "<your-telegram-id>" }
```

For a production-style local process, use one Waitress process and at least 16 threads:

```powershell
python -m api.app profiles.demo --server waitress --threads 16
```

### 4. Start a client

For the Telegram frontend, open another terminal, activate the virtual environment, load `.env`, and run:

```powershell
.\.venv\Scripts\Activate.ps1
.\load-env.ps1
python -m bot.app profiles.demo
```

The API must already be running. The bot connects to the profile's `API_PORT`, validates `BOT_TOKEN`, and exits if another bot process already owns the same deployment lock.

For local end-to-end testing without Telegram, use one of the terminal clients against the running API. They create the required test identity when they start and remove that identity when they exit normally:

```powershell
python -m tools.terminal_client_commander --profile profiles.demo
python -m tools.terminal_client_viewer --profile profiles.demo
```

The terminal clients are the one-to-one test simulator for Telegram. Both
surfaces immediately show the profile-language "model is thinking" status and
replace it once with the final response, localized error, or queued ACK and
Task ID. Later asynchronous results remain separate notifications. With
`DEEP_DEBUG=true`, commander clients additionally receive ordered trace
messages; viewer clients never request or display them.

Stop any foreground process with `Ctrl+C`. The SQLite database remains on disk, so restarting the same profile resumes its existing deployment state; durable bot notification cursors also prevent already-delivered notifications from being replayed after a normal restart.

### Common startup failures

- `required environment variable ... is not set`: activate the intended terminal environment and run `.\load-env.ps1` again.
- `could not import profile module`: run the command from the repository root and pass a dotted module name such as `profiles.demo`, not a file path.
- Connection refused from the bot or a terminal client: start `api.app` first and confirm that the client and API use the same profile and port.
- HTTP `401`: register that exact identity with `cli.user_admin` against the same profile.
- Telegram token validation failure: replace the example `BOT_TOKEN` in `.env` with a real token and reload the environment.

## Runtime flow

1. The API authenticates the caller and accepts a message or sensor event.
2. The Main Agent produces a validated intent decision; ambiguous messages ask for clarification instead of falling through to an action.
3. Questions use read-only specialists or a structured, indexed history query. Reports and requests enter orchestration.
4. Orchestration assesses risk, checks precedents, and selects a protocol.
5. Missing classification or ambiguous/flagged execution creates a durable hold for a commander.
6. Approved protocol steps run serially with explicit tool permissions and retry rules.
7. The Insights Agent judges the result; persistence records the outcome and notification in the same transaction.
8. The bot or terminal client delivers holds, notices, failures, and final results through the cursor-backed notification feed.

## Project structure

| Path | Responsibility |
|---|---|
| `agents/` | Agent contracts, CrewAI runtime, tool enforcement, registry, and standard agents. |
| `api/` | Flask composition, request boundary, and HTTP routes. |
| `auth/` | Permission levels and action authorization. |
| `bot/` | Telegram/HTTP transports, interactions, and background notification services. |
| `cli/` | Host-only user administration. |
| `config/` | Environment-backed model configuration and persisted live settings. |
| `history/` | Event extraction, history queries, precedents, and summaries. |
| `orchestrator/` | Reasoning, holds, event queue, and end-to-end business flows. |
| `persistence/` | Persistence contracts, schema/migrations, and serialized SQLite storage. |
| `profiles/` | Profile contracts, loading/validation, registries, demo, and authoring template. |
| `protocols/` | Protocol contracts, repository editing, and step execution. |
| `tools/` | Observability, simulator, terminal clients, and shared terminal support. |
| `fixtures/` | Deterministic profiles and event data used by tests. |
| `tests/` | Unit, architecture, compatibility, persistence, and integration coverage. |
| `docs/` | Operational, API, architecture, profile, and historical documentation. |

The removed implementation paths remain package-level compatibility aliases where supported. The former `registries` package is intentionally not preserved; area and event-type registries now belong to `profiles`.

## Public entry points

```text
python -m api.app <profile_module>
python -m api.app <profile_module> --server waitress --threads 16
python -m bot.app <profile_module>
python -m cli.user_admin --profile <profile_module> ...
python -m tools.simulator --port <port> --identity <identity> ...
python -m tools.terminal_client_commander --profile <profile_module>
python -m tools.terminal_client_viewer --profile <profile_module>
```

Run any entry point with `--help` for its complete options.

## Profiles and live settings

Start with `profiles/template.py` when creating a deployment. Every profile must define a human-facing `PROFILE_NAME`, `DEFAULT_LANGUAGE` (`"en"` or `"he"`), `MAX_ITER`, and `MODEL_TIMEOUT_SECONDS`; shipped profiles use 8 iterations and 30 seconds. The Main Agent uses the profile name when introducing the service, and every fixed client/API message uses the selected language for the full server process. A profile also defines agents, protocols, event types, areas, storage and API settings, the IANA `TIMEZONE` used to resolve relative history periods, conversation retention, optimization policy, and the names of required secret variables. The complete contract is documented in `docs/profile_spec.md`.

Free-form messages are classified as questions, reports, requests, or conversation. A message whose action or referent cannot be determined safely receives a synchronous clarification question and creates no job. History questions remain in the read-only question path: the Main Agent emits a constrained query description, SQLite performs parameterized filtering/counting, and the History Agent sees only the bounded records needed for narrative answers.

System self-description is generated naturally rather than returned from a fixed response. For questions about identity, capabilities, protocols, or sub-agents, the model is grounded with the active `PROFILE_NAME` and a runtime catalog filtered to what the *asking caller* is authorized to see (see "Roles and capability disclosure" below). Adding an agent, tool, or protocol changes a commander's catalog after restart; a viewer's catalog only ever changes when `ViewerAllowedAction` itself changes.

Most profile edits take effect after a restart. These three settings are different: they take effect immediately and are saved beside the deployment database:

- `retry_count`
- `risk_threshold`
- `lookback_window_days`

## Roles and capability disclosure

Every registered user is either a `viewer` or a `commander` (`cli.user_admin`). A commander is unrestricted: every API route, bot command, and Main Agent capability is available. A viewer is authorized for exactly the operations listed in `auth.permissions.ViewerAllowedAction` — currently submitting events/messages, conversing, asking questions, reporting, and requesting an action, plus viewing the profile overview, their own user registration, and their own job status. An operation absent from that enum is denied to a viewer at every entry point (HTTP, Telegram, terminal client) with no separate allowlist to fall out of sync.

Disclosure follows the same rule, not a second policy: a viewer's "what can you do for me?" answer, and the Main Agent's system context more generally, only ever contains the capabilities and runtime metadata (protocols, sub-agent names, tool names) a viewer is authorized for. Protocol and sub-agent data is commander-only — a viewer's `GET /SYSTEM` response and Main Agent prompt omit those fields entirely rather than returning them empty. `GET /Job/<event_id>` and history questions (`ask_question`) are scoped to events a viewer themselves submitted; asking about someone else's event answers as if it does not exist. `GET /User/<identity>` is scoped to a viewer's own identity. See `docs/allowed_calls.md`'s "Operation matrix" for the full entry-point-to-operation mapping, and `docs/vocabulary.md` for the `RequestedOperation`/`ViewerAllowedAction`/`CapabilityDescriptor` terms.

## Conversation memory and event follow-ups

When a profile sets `CONVERSATION_HISTORY_TURNS` above zero, `/Msg` (and the equivalent Telegram/terminal flows) remembers recent turns per `conversation_id` — a stable per-chat/thread key in Telegram, a stable per-session key in the terminal clients. A follow-up question can then refer back to something already discussed without repeating its Event ID:

```text
> smoke reported near gate 3
Queued report. Job ID: evt-2f9a...

> what's the status of that event now?
Event evt-2f9a... is still queued.
```

The Main Agent resolves a reference like "that event" or "the first one" from the remembered turns to a stable Event ID, then always re-reads the current record from the database — a remembered assistant reply is never treated as current fact, and the caller's authorization is re-evaluated fresh on every turn (a role change between two turns of the same conversation takes effect starting on the very next one). An ambiguous reference (more than one plausible prior event) gets a short clarification question instead of a guess.

Protocol steps may declare event fields they cannot execute without. If one of
those fields is absent, the step is persisted as `waiting_for_event_data`
without consuming an execution attempt, while the event remains non-terminal.
The Main Agent asks the original reporter for all currently blocking details in
the reporter's language. A reply in the same conversation updates the existing
event and resumes the saved plan; completed steps are not repeated. Messages
that do not answer the data request continue through normal intent routing.

## Logging

Normal operation emits structured records with trace IDs and stores them in the deployment database. Human-readable log lines are written separately for terminal use.

- `LOG_CONSOLE_JSON=false` disables only the JSON console stream.
- `DEBUG_VERBOSE_LOGGING=true` lowers the console/log threshold to DEBUG for local diagnostics; it does not enable raw model-I/O persistence.
- `DEEP_DEBUG=true` enables commander-only live traces and persistence of raw model prompts/responses. It is read once at startup, adds no model calls, and may retain sensitive data indefinitely in SQLite; leave it false during normal operation.
- `OBSERVABILITY_MODE=log|otlp` selects local structured spans or OTLP export. `OTEL_EXPORTER_OTLP_ENDPOINT` is mandatory in `otlp` mode.

See `docs/operator_guide.md` for log fields, trace flow, and operational guidance.

## Testing

The suite reads model-tier placeholders from `TEST_`-prefixed environment variables. It does not read `.env` automatically and normal automated tests do not require real billed model calls.

Example PowerShell setup:

```powershell
$env:TEST_CORE_MODEL_PROVIDER = "openai"
$env:TEST_CORE_MODEL_NAME = "test-core-model"
$env:TEST_CORE_MODEL_API_KEY_ENV = "CORE_TEST_KEY"
$env:TEST_CORE_TEST_KEY = "test-key"

$env:TEST_SUB_MODEL_PROVIDER = "openai"
$env:TEST_SUB_MODEL_NAME = "test-sub-model"
$env:TEST_SUB_MODEL_API_KEY_ENV = "SUB_TEST_KEY"
$env:TEST_SUB_TEST_KEY = "test-key"

python -m pytest -q
```

The exact test count is intentionally not hard-coded here. `tests/sanity_check_real_model_call.py` and `tools/evaluate_response_pipeline.py --live` are opt-in, billed checks and are not collected by pytest.

The normal pytest suite mocks warmup and all network boundaries. It never uses
real model or Telegram credentials and makes no paid provider call.

## Documentation

- `docs/operator_guide.md` — deployment and operations walkthrough.
- `docs/how_to_connect_telegram.md` — Telegram setup and verification.
- `docs/profile_spec.md` — authoritative profile contract.
- `docs/agent_authoring.md` — adding agents and tools.
- `docs/api_spec.md` — HTTP endpoints, authentication, and errors.
- `docs/allowed_calls.md` — package boundaries and supported public surfaces.
- `docs/file_catalog.md` — English description of every first-party file.
- `docs/PRODUCTION_READY.md` — hardening work required beyond a localhost deployment.

## Deployment note

Localhost keeps Flask as the default server. Production instructions use one API process under Waitress with at least 16 threads so long-polling requests cannot starve ordinary API traffic. Before exposing it beyond localhost, complete the hardening work covering secrets, TLS, process supervision, backups, resource limits, monitoring, and release management in `docs/PRODUCTION_READY.md`.
