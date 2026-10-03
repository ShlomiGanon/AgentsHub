# AgentsHub

AgentsHub is a profile-driven multi-agent operations system. It takes reports, questions, and requests over HTTP or Telegram, decides what they mean, runs approved protocols, pauses for a commander when needed, and stores the result in SQLite.

Each deployment is one Python profile: agents, protocols, event types, areas, database path, API port, and model tiers. Two profiles can share this codebase and still keep separate databases and runtime state.

## Architecture

A client talks to the **bot** (Telegram) or the **API** (HTTP). The bot never imports API internals and never opens the API database; it calls the same public HTTP routes.

```text
Telegram / HTTP / terminal
        │
        ▼
   bot  or  api.app
        │
        ▼
      auth          (viewer vs commander)
        │
        ▼
 orchestrator.flows
        │
        ├── agents      (Main Agent + specialists)
        ├── protocols   (approved steps and tools)
        ├── history     (indexed questions and precedents)
        └── persistence (SQLite behind a package facade)
```

Hard rules:

- Storage goes through the `persistence` package only. No raw SQL above that layer.
- Cross-package imports must use the public entry points in `tests/test_architecture.py` (`ENTRY_POINTS`). The API may import `orchestrator.flows` only.
- Profiles are immutable at runtime. Live settings that take effect immediately are `retry_count`, `risk_threshold`, `lookback_window_days`, and `safe_mode`.
- Hebrew user-facing text lives only in `messages/he.py`. English catalog keys stay in `messages/en.py`. Comments and identifiers are English.

### Packages

| Path | Role |
|---|---|
| `agents/` | Agent contracts, CrewAI runtime, tool allowlists |
| `api/` | Flask app, JSON routes, admin panel |
| `auth/` | Permission levels and `BOT_SERVICE_IDENTITY` |
| `bot/` | Telegram polling, HTTP client, notifications |
| `cli/` | Host-only user and group admin |
| `config/` | Environment models and live settings |
| `history/` | Event extraction, queries, summaries |
| `orchestrator/` | Intent, holds, queue, protocol runs |
| `persistence/` | Schema and SQLite stores |
| `profiles/` | Shipped deployments and the authoring template |
| `protocols/` | Protocol model and step execution |
| `tools/` | Logs, traces, terminal clients, simulator |
| `messages/` | English and Hebrew catalogs |
| `tests/` | Unit, architecture, and integration tests |

## How to run

Python 3.11 or newer. From the repository root:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
.\load-env.ps1
```

AgentsHub does not load `.env` by itself. Run `.\load-env.ps1` in every new terminal. Set `BOT_TOKEN`, `BOT_SERVICE_KEY`, model provider keys, and optionally `ADMIN_USERNAME`, `ADMIN_PASSWORD`, and `ADMIN_SESSION_SECRET`.

Register a commander and the bot service identity (same profile you will start):

```powershell
python -m cli.user_admin --profile profiles.response_team add --telegram-id <your-telegram-id> --level commander
python -m cli.user_admin --profile profiles.response_team add --telegram-id bot-service --level commander
```

Start the API, then the bot:

```powershell
python -m api.app profiles.response_team
python -m bot.app profiles.response_team
```

Or start both together:

```powershell
python -m run_stack
```

`profiles.response_team` listens on `127.0.0.1:8907`. `profiles.firefighting` uses port `8906`. Only one profile runs per process pair unless you start two pairs.

Admin panel (optional): set the admin env vars, then open `http://127.0.0.1:8907/admin/login`. Use HTTPS before exposing this beyond localhost.

Without Telegram, use the terminal clients against a running API:

```powershell
python -m tools.terminal_client_commander --profile profiles.response_team
python -m tools.terminal_client_viewer --profile profiles.response_team
```

Check the API:

```powershell
Invoke-RestMethod -Uri http://127.0.0.1:8907/SYSTEM -Headers @{ "X-Identity" = "<your-telegram-id>" }
```

Production-style local API:

```powershell
python -m api.app profiles.response_team --server waitress --threads 16
```

## How to test

Install test extras and set `TEST_*` placeholders (the suite does not read `.env`):

```powershell
python -m pip install -r requirements-dev.txt
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

Normal pytest mocks warmup and the network. Live billed checks (`tests/sanity_check_real_model_call.py`, `tools/evaluate_response_pipeline.py --live`) are opt-in and not collected by default.

## Contributor rules

- One short English sentence on every module, class, function, and test. Explain *why* in inline comments, not the obvious *what*.
- No Hebrew in comments, identifiers, or non-catalog source.
- Do not import across packages except through the public entry points in `tests/test_architecture.py`.
- Do not merge the firefighting and response_team profile twins or their stores.
- Admin HTML/JS/CSS stay as Python string modules that concatenate to the same page.
