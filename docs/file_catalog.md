# File Catalog

This English catalog describes every tracked or pending first-party file in the post-refactor tree. Generated caches, local databases, virtual environments, and `.env` secrets are excluded.

| Path | Category | Visibility | Purpose |
|---|---|---|---|
| `$null` | Project | Internal | Preserves a historical captured smoke-test logging artifact. |
| `.env.example` | Project | Internal | Lists deployment environment variables without storing secret values. |
| `.github/workflows/ci.yml` | Automation | Internal | Runs the repository's continuous-integration checks. |
| `.gitignore` | Project | Internal | Excludes generated, local, secret, and runtime artifacts. |
| `.vscode/extensions.json` | Project | Internal | Recommends editor extensions for this workspace. |
| `IMPROVE.MD` | Documentation | Internal | Reports evidence-backed improvements for Main Agent response latency and quality. |
| `README.md` | Project | Internal | Introduces the system and its primary startup commands. |
| `agents/__init__.py` | Production | Public facade | Exposes the public agent facade and compatibility module aliases. |
| `agents/contracts.py` | Production | Private implementation | Defines agent results, descriptors, tool metadata, parsing, and typed errors. |
| `agents/runtime.py` | Production | Private implementation | Constructs and invokes agents, enforces tools, adapts CrewAI, and owns the runtime registry. |
| `agents/standard_agents.py` | Production | Private implementation | Implements the standard History and Reference agents. |
| `api/__init__.py` | Production | Public facade | Exposes the API facade and compatibility module aliases. |
| `api/app.py` | Production | Public entry point | Builds API dependencies, owns ApiContext, and starts Flask. |
| `api/request_boundary.py` | Production | Private implementation | Authenticates requests and translates API and HTTP failures into responses. |
| `api/routes.py` | Production | Private implementation | Defines ingestion, management, hold, job, system, and notification routes. |
| `auth/__init__.py` | Production | Public facade | Exposes authorization contracts. |
| `auth/permissions.py` | Production | Private implementation | Maps actions to permission levels and evaluates authorization. |
| `bot/__init__.py` | Production | Public facade | Exposes the bot facade and compatibility module aliases. |
| `bot/app.py` | Production | Public entry point | Builds bot dependencies, routes Telegram updates, and starts polling. |
| `bot/background_services.py` | Production | Private implementation | Polls and dispatches notifications, persists cursors, and manages single-instance startup. |
| `bot/contracts.py` | Production | Private implementation | Defines bot DTOs, client interfaces, dependency contracts, and errors. |
| `bot/interactions.py` | Production | Private implementation | Formats messages and handles commands, holds, settings, and profile interactions. |
| `bot/transports.py` | Production | Private implementation | Implements HTTP API access and Telegram transport adapters. |
| `cli/__init__.py` | Production | Public facade | Marks the command-line package. |
| `cli/user_admin.py` | Production | Public entry point | Provides the user-administration command-line entry point. |
| `config/__init__.py` | Production | Public facade | Exposes environment and live-settings configuration facades. |
| `config/environment.py` | Production | Private implementation | Resolves model tiers and process flags from environment values. |
| `config/live_settings.py` | Production | Private implementation | Persists retry, risk, and lookback settings atomically. |
| `conftest.py` | Project | Internal | Defines repository-wide pytest fixtures, model-tier configuration, and trace isolation. |
| `docs/DEMO_READY.md` | Documentation | Internal | Documents DEMO READY. |
| `docs/GT critial agents.pptx.pdf` | Documentation | Internal | Stores the GT critial agents.pptx reference artifact. |
| `docs/PRODUCTION_READY.md` | Documentation | Internal | Documents PRODUCTION READY. |
| `docs/agent_authoring.md` | Documentation | Internal | Documents agent authoring. |
| `docs/allowed_calls.md` | Documentation | Internal | Documents allowed calls. |
| `docs/api_spec.md` | Documentation | Internal | Documents api spec. |
| `docs/code_example.py` | Documentation | Internal | Documents code example. |
| `docs/cost_latency_review.md` | Documentation | Internal | Documents cost latency review. |
| `docs/file_catalog.md` | Documentation | Internal | Documents file catalog. |
| `docs/how_to_connect_telegram.md` | Documentation | Internal | Documents how to connect telegram. |
| `docs/investigation_summary.md` | Documentation | Internal | Documents investigation summary. |
| `docs/links.txt` | Documentation | Internal | Documents links. |
| `docs/operator_guide.md` | Documentation | Internal | Documents operator guide. |
| `docs/profile_spec.md` | Documentation | Internal | Documents profile spec. |
| `docs/progress.md` | Documentation | Internal | Documents progress. |
| `docs/questions.txt` | Documentation | Internal | Documents questions. |
| `docs/server_report.md` | Documentation | Internal | Documents server report. |
| `docs/vocabulary.md` | Documentation | Internal | Documents vocabulary. |
| `docs/work_plan.md` | Documentation | Internal | Documents work plan. |
| `docs/ארכיטקטוררה.pptx` | Documentation | Internal | Stores the ארכיטקטוררה reference artifact. |
| `docs/מצגת ארכיטקטורה.pptx` | Documentation | Internal | Stores the מצגת ארכיטקטורה reference artifact. |
| `docs/תיאור מבנה מערכת.pdf` | Documentation | Internal | Stores the תיאור מבנה מערכת reference artifact. |
| `docs/תיאור משימות שבועיות.pdf` | Documentation | Internal | Stores the תיאור משימות שבועיות reference artifact. |
| `fixtures/__init__.py` | Fixture | Internal | Marks reusable fixtures as a package. |
| `fixtures/profiles/__init__.py` | Fixture | Internal | Marks fixture deployment profiles as a package. |
| `fixtures/profiles/minimal_profile.py` | Fixture | Internal | Defines the minimal valid profile used by loading and integration tests. |
| `fixtures/seed_events.py` | Fixture | Internal | Provides deterministic historical event fixtures. |
| `history/__init__.py` | Production | Public facade | Exposes the history facade and compatibility module aliases. |
| `history/contracts.py` | Production | Private implementation | Defines history extraction, query, summary, and persistence-transfer contracts. |
| `history/event_pipeline.py` | Production | Private implementation | Extracts events, normalizes timestamps, and writes durable history state. |
| `history/query.py` | Production | Private implementation | Retrieves raw and summarized history and searches precedents. |
| `history/summaries.py` | Production | Private implementation | Generates and reconciles daily, monthly, and yearly summaries. |
| `instructions.md` | Project | Internal | Defines repository-specific development and architecture rules. |
| `load-env.ps1` | Project | Internal | Loads local development environment variables into PowerShell. |
| `orchestrator/__init__.py` | Production | Public facade | Exposes orchestration capabilities and compatibility module aliases. |
| `orchestrator/event_queue.py` | Production | Private implementation | Serializes event processing on a dedicated worker. |
| `orchestrator/flows.py` | Production | Private implementation | Coordinates report, request, hold-resume, protocol, and outcome workflows. |
| `orchestrator/holds.py` | Production | Private implementation | Creates and resolves clarification and approval holds. |
| `orchestrator/reasoning.py` | Production | Private implementation | Prompts and parses Main/Insights decisions, questions, selection, formulation, and judgment. |
| `persistence/__init__.py` | Production | Public facade | Exposes persistence contracts, constructors, and compatibility aliases. |
| `persistence/contracts.py` | Production | Private implementation | Defines persistence interfaces and domain errors. |
| `persistence/schema.py` | Production | Private implementation | Owns immutable migration DDL and the current SQLite schema. |
| `persistence/sqlite_store.py` | Production | Private implementation | Implements serialized SQLite persistence, transactions, and row conversion. |
| `profiles/__init__.py` | Production | Public facade | Exposes profile contracts, loading, registries, and compatibility aliases. |
| `profiles/contracts.py` | Production | Private implementation | Defines profile declarations, loaded-profile state, and area/event-type registries. |
| `profiles/demo.py` | Production | Private implementation | Defines the runnable demonstration deployment profile. |
| `profiles/loader.py` | Production | Private implementation | Imports, validates, hashes, and constructs deployment profiles and registries. |
| `profiles/template.py` | Production | Private implementation | Provides a reference template for authoring deployment profiles. |
| `protocols/__init__.py` | Production | Public facade | Exposes protocol contracts, execution, repository operations, and aliases. |
| `protocols/contracts.py` | Production | Private implementation | Defines protocols, steps, criticality, results, and edit errors. |
| `protocols/executor.py` | Production | Private implementation | Executes protocol steps with retry and idempotency enforcement. |
| `protocols/repository.py` | Production | Private implementation | Loads protocols and atomically edits declarations in profile source. |
| `pytest.ini` | Project | Internal | Configures pytest discovery and execution. |
| `refactor.md` | Project | Internal | Records the behavior-preserving refactor design and implementation outcomes. |
| `requirements-dev.txt` | Project | Internal | Pins development and test dependencies. |
| `requirements.txt` | Project | Internal | Pins production runtime dependencies. |
| `tests/__init__.py` | Test | Internal | Marks the automated test suite as a package. |
| `tests/api_fakes.py` | Test | Internal | Provides reusable API contexts, clients, and server fakes for tests. |
| `tests/bot_fakes.py` | Test | Internal | Provides reusable bot API and Telegram fakes for tests. |
| `tests/helpers.py` | Test | Internal | Provides shared test builders and persistence helpers. |
| `tests/sanity_check_real_model_call.py` | Test | Internal | Runs an opt-in billed real-model smoke check outside pytest discovery. |
| `tests/test_agent_permission_enforcement.py` | Test | Internal | Verifies agent permission enforcement behavior and edge cases. |
| `tests/test_agent_registry.py` | Test | Internal | Verifies agent registry behavior and edge cases. |
| `tests/test_agent_runtime.py` | Test | Internal | Verifies agent construction, invocation, CrewAI adaptation, and output handling. |
| `tests/test_api_app.py` | Test | Internal | Verifies api app behavior and edge cases. |
| `tests/test_api_holds.py` | Test | Internal | Verifies api holds behavior and edge cases. |
| `tests/test_api_jobs.py` | Test | Internal | Verifies api jobs behavior and edge cases. |
| `tests/test_api_messages.py` | Test | Internal | Verifies api messages behavior and edge cases. |
| `tests/test_api_notifications.py` | Test | Internal | Verifies api notifications behavior and edge cases. |
| `tests/test_api_protocols.py` | Test | Internal | Verifies api protocols behavior and edge cases. |
| `tests/test_api_request_boundary.py` | Test | Internal | Verifies authentication and structured API error translation. |
| `tests/test_api_system.py` | Test | Internal | Verifies api system behavior and edge cases. |
| `tests/test_api_unified_ingestion.py` | Test | Internal | Verifies api unified ingestion behavior and edge cases. |
| `tests/test_architecture.py` | Test | Internal | Enforces package boundaries and prevents recreation of the registries package. |
| `tests/test_bot_app.py` | Test | Internal | Verifies bot dependency wiring, entry-point behavior, and update routing. |
| `tests/test_bot_background_services.py` | Test | Internal | Verifies notification polling, delivery, failures, results, and startup services. |
| `tests/test_bot_holds.py` | Test | Internal | Verifies clarification and approval interaction lifecycles. |
| `tests/test_bot_interactions.py` | Test | Internal | Verifies profile, settings, user, formatting, and command interactions. |
| `tests/test_bot_transports.py` | Test | Internal | Verifies bot HTTP clients, abstract client behavior, and Telegram transports. |
| `tests/test_demo_profile.py` | Test | Internal | Verifies demo profile behavior and edge cases. |
| `tests/test_environment_config.py` | Test | Internal | Verifies environment-backed model and runtime configuration. |
| `tests/test_file_catalog.py` | Test | Internal | Ensures this catalog exactly matches the first-party repository tree. |
| `tests/test_history_agent.py` | Test | Internal | Verifies history agent behavior and edge cases. |
| `tests/test_history_event_pipeline.py` | Test | Internal | Verifies extraction, time normalization, and durable history writes. |
| `tests/test_history_logging.py` | Test | Internal | Verifies history logging behavior and edge cases. |
| `tests/test_history_precedent.py` | Test | Internal | Verifies history precedent behavior and edge cases. |
| `tests/test_history_query.py` | Test | Internal | Verifies history query behavior and edge cases. |
| `tests/test_integration_cost_and_latency_review.py` | Test | Internal | Verifies the cost and latency review scenario across real subsystem boundaries. |
| `tests/test_integration_deployment.py` | Test | Internal | Verifies the deployment scenario across real subsystem boundaries. |
| `tests/test_integration_end_to_end_flow.py` | Test | Internal | Verifies the end to end flow scenario across real subsystem boundaries. |
| `tests/test_integration_history_accuracy.py` | Test | Internal | Verifies the history accuracy scenario across real subsystem boundaries. |
| `tests/test_integration_hold_restart_and_flow.py` | Test | Internal | Verifies the hold restart and flow scenario across real subsystem boundaries. |
| `tests/test_integration_ingestion_parity.py` | Test | Internal | Verifies the ingestion parity scenario across real subsystem boundaries. |
| `tests/test_integration_log_sink.py` | Test | Internal | Verifies the log sink scenario across real subsystem boundaries. |
| `tests/test_integration_profile_editing_and_settings.py` | Test | Internal | Verifies the profile editing and settings scenario across real subsystem boundaries. |
| `tests/test_integration_profile_isolation.py` | Test | Internal | Verifies the profile isolation scenario across real subsystem boundaries. |
| `tests/test_integration_profile_loading.py` | Test | Internal | Verifies the profile loading scenario across real subsystem boundaries. |
| `tests/test_integration_retry_exhaustion.py` | Test | Internal | Verifies the retry exhaustion scenario across real subsystem boundaries. |
| `tests/test_integration_serial_processing_under_load.py` | Test | Internal | Verifies the serial processing under load scenario across real subsystem boundaries. |
| `tests/test_integration_user_administration.py` | Test | Internal | Verifies the user administration scenario across real subsystem boundaries. |
| `tests/test_legacy_imports.py` | Test | Internal | Verifies supported implementation-path aliases resolve to canonical modules. |
| `tests/test_migrations.py` | Test | Internal | Verifies migrations behavior and edge cases. |
| `tests/test_observability.py` | Test | Internal | Verifies tracing and structured logging behavior. |
| `tests/test_orchestrator_flows.py` | Test | Internal | Verifies orchestrator flows behavior and edge cases. |
| `tests/test_orchestrator_holds.py` | Test | Internal | Verifies orchestrator holds behavior and edge cases. |
| `tests/test_orchestrator_insights.py` | Test | Internal | Verifies orchestrator insights behavior and edge cases. |
| `tests/test_orchestrator_judgment.py` | Test | Internal | Verifies orchestrator judgment behavior and edge cases. |
| `tests/test_orchestrator_reasoning.py` | Test | Internal | Verifies Main Agent reasoning, parsing, and decisions. |
| `tests/test_orchestrator_selection.py` | Test | Internal | Verifies orchestrator selection behavior and edge cases. |
| `tests/test_permissions.py` | Test | Internal | Verifies permissions behavior and edge cases. |
| `tests/test_persistence_conformance.py` | Test | Internal | Verifies persistence conformance behavior and edge cases. |
| `tests/test_persistence_events.py` | Test | Internal | Verifies persistence events behavior and edge cases. |
| `tests/test_profile_loading.py` | Test | Internal | Verifies profile imports, validation, construction, and registry configuration. |
| `tests/test_protocol_repository.py` | Test | Internal | Verifies protocol loading, validation, rendering, and atomic editing. |
| `tests/test_protocol_retry.py` | Test | Internal | Verifies protocol retry behavior and edge cases. |
| `tests/test_question_answering.py` | Test | Internal | Verifies question routing and read-only specialist/history answers. |
| `tests/test_reference_agent.py` | Test | Internal | Verifies reference agent behavior and edge cases. |
| `tests/test_sqlite_store.py` | Test | Internal | Verifies SQLite serialization, concurrency, and user persistence. |
| `tests/test_user_admin.py` | Test | Internal | Verifies user admin behavior and edge cases. |
| `tools/__init__.py` | Production | Public facade | Exposes shared observability helpers and lazy terminal compatibility aliases. |
| `tools/observability.py` | Production | Private implementation | Provides trace contexts, structured logging, and human/JSON output. |
| `tools/simulator.py` | Production | Public entry point | Provides the event-simulator executable entry point. |
| `tools/terminal_client_commander.py` | Production | Public entry point | Provides the commander terminal-client executable workflow. |
| `tools/terminal_client_viewer.py` | Production | Public entry point | Provides the viewer terminal-client executable workflow. |
| `tools/terminal_support.py` | Production | Private implementation | Shares terminal HTTP, notification, identity, and interactive-mode helpers. |
