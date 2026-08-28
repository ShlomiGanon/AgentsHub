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

Install the runtime dependencies:

```powershell
python -m pip install -r requirements.txt
```

For development and testing, also install:

```powershell
python -m pip install -r requirements-dev.txt
```

## Quick start

The included `profiles.demo` profile listens on port `8902` and stores its database in the operating system's temporary directory.

### 1. Configure the environment

Copy `.env.example` to `.env`, replace every fake token/key, and choose the model provider and model names for both tiers:

```powershell
Copy-Item .env.example .env
```

Load the file into the current PowerShell process:

```powershell
.\load-env.ps1
```

The important variables are:

- `BOT_TOKEN`
- `CORE_MODEL_PROVIDER`, `CORE_MODEL_NAME`, and `CORE_MODEL_API_KEY_ENV`
- `SUB_MODEL_PROVIDER`, `SUB_MODEL_NAME`, and `SUB_MODEL_API_KEY_ENV`
- The key variables named by `CORE_MODEL_API_KEY_ENV` and `SUB_MODEL_API_KEY_ENV`

Profiles contain only environment-variable names, never secret values.

### 2. Register users

A new deployment has no users. Register the first human commander and the bot's service identity before starting the Telegram frontend:

```powershell
python -m cli.user_admin --profile profiles.demo add --telegram-id <your-telegram-id> --level commander
python -m cli.user_admin --profile profiles.demo add --telegram-id bot-service --level commander
```

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

The API binds to `127.0.0.1` by default. Use `--host` only when the deployment has appropriate network and TLS controls.

### 4. Start a client

For the Telegram frontend, open another terminal, load the same environment, and run:

```powershell
python -m bot.app profiles.demo
```

For local end-to-end testing without Telegram, use one of the terminal clients against the running API:

```powershell
python -m tools.terminal_client_commander --profile profiles.demo
python -m tools.terminal_client_viewer --profile profiles.demo
```

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
python -m bot.app <profile_module>
python -m cli.user_admin --profile <profile_module> ...
python -m tools.simulator --port <port> --identity <identity> ...
python -m tools.terminal_client_commander --profile <profile_module>
python -m tools.terminal_client_viewer --profile <profile_module>
```

Run any entry point with `--help` for its complete options.

## Profiles and live settings

Start with `profiles/template.py` when creating a deployment. A profile defines agents, protocols, event types, areas, storage and API settings, the IANA `TIMEZONE` used to resolve relative history periods, and the names of required secret variables. `TIMEZONE` defaults to `UTC` for older profiles. The complete contract is documented in `docs/profile_spec.md`.

Free-form messages are classified as questions, reports, requests, or conversation. A message whose action or referent cannot be determined safely receives a synchronous clarification question and creates no job. History questions remain in the read-only question path: the Main Agent emits a constrained query description, SQLite performs parameterized filtering/counting, and the History Agent sees only the bounded records needed for narrative answers.

Most profile edits take effect after a restart. These three settings are different: they take effect immediately and are saved beside the deployment database:

- `retry_count`
- `risk_threshold`
- `lookback_window_days`

## Logging

Normal operation emits structured records with trace IDs and stores them in the deployment database. Human-readable log lines are written separately for terminal use.

- `LOG_CONSOLE_JSON=false` disables only the JSON console stream.
- `DEBUG_VERBOSE_LOGGING=true` enables model prompts/responses and other sensitive diagnostic detail. Do not leave it enabled in normal operation.

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

The current suite collects 879 tests. `tests/sanity_check_real_model_call.py` is an opt-in, billed smoke check and is not collected by pytest.

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

The default setup is intended for a single localhost deployment. Before exposing it beyond localhost, complete the hardening work covering secrets, TLS, process supervision, backups, resource limits, monitoring, and release management in `docs/PRODUCTION_READY.md`.
