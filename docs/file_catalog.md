# File Catalog

This English catalog describes every tracked or pending first-party file in the post-refactor tree. Generated caches, local databases, virtual environments, and `.env` secrets are excluded.

| Path | Category | Visibility | Purpose |
|---|---|---|---|
| `$null` | Project | Internal | Preserves a historical captured smoke-test logging artifact. |
| `.env.example` | Project | Internal | Lists deployment environment variables without storing secret values. |
| `.github/workflows/ci.yml` | Automation | Internal | Runs the repository's continuous-integration checks. |
| `.gitignore` | Project | Internal | Excludes generated, local, secret, and runtime artifacts. |
| `.vscode/extensions.json` | Project | Internal | Recommends editor extensions for this workspace. |
| `benchmark_baseline.json` | Evaluation | Internal | Stores baseline response-pipeline benchmark measurements. |
| `benchmark_optimized.json` | Evaluation | Internal | Stores optimized response-pipeline benchmark measurements. |
| `codebase_invest.md` | Documentation | Internal | Records a codebase investigation and implementation findings. |
| `IMPROVE.MD` | Documentation | Internal | Reports evidence-backed improvements for Main Agent response latency and quality. |
| `invest.md` | Documentation | Internal | Records repository investigation notes. |
| `README.md` | Project | Internal | Introduces the system and its primary startup commands. |
| `run_stack.py` | Production | Public entry point | Starts and supervises the unified API and Telegram bot processes together. |
| `SPEED.MD` | Documentation | Internal | Records the measured response-latency investigation and evidence. |
| `SPEED_PLAN.MD` | Documentation | Internal | Defines the approved sequential implementation and verification plan for current speed and operator-experience work. |
| `unsafe_system.md` | Documentation | Internal | Defines the approved safe/open Telegram admission design, implementation stages, and verification criteria. |
| `agents/__init__.py` | Production | Public facade | Exposes the public agent facade and compatibility module aliases. |
| `agents/contracts.py` | Production | Private implementation | Defines agent results, descriptors, tool metadata, parsing, and typed errors. |
| `agents/fire_station_agents.py` | Production | Private implementation | Implements the Fire and Rescue dispatch and hazmat-assessment specialists (docs/bar_improves.md). |
| `agents/friendly_forces_agent.py` | Production | Private implementation | Implements the tactical coordination and dispatch specialist for friendly forces. |
| `agents/provider_telemetry.py` | Production | Private implementation | Correlates CrewAI provider-call events with AgentsHub traces, stages, latency, and usage. |
| `agents/response_team_agents.py` | Production | Private implementation | Implements the Response Team security-operations and surveillance-fault specialists (docs/bar_improves.md). |
| `agents/roster_agent.py` | Production | Private implementation | Implements the shared team/crew availability-reporting specialist (docs/bar_improves.md). |
| `agents/runtime.py` | Production | Private implementation | Constructs and invokes agents, enforces tools, adapts CrewAI, and owns the runtime registry. |
| `agents/standard_agents.py` | Production | Private implementation | Implements the standard History and Reference agents. |
| `agents/surveillance_agent.py` | Production | Private implementation | Implements the visual-surveillance, camera-monitoring, and tactical-drone specialist. |
| `agents/team_status_agent.py` | Production | Private implementation | Implements the readiness-team roster, attendance-cycle, response, and availability-report specialist. |
| `api/__init__.py` | Production | Public facade | Exposes the API facade and compatibility module aliases. |
| `api/admin.py` | Production | Private implementation | Serves the login-gated, seven-page admin web panel under `/admin`, in the profile's catalog language. |
| `api/admin_api_pages.py` | Production | Private implementation | Provides the tailored profiles, protocols, and events management UI; live endpoint actions use the selected `X-Identity` and retain normal API authorization. |
| `api/admin_simulator.py` | Production | Private implementation | Style, body and script of the admin scenario simulator page, plus the helper that gathers its embedded data. |
| `api/app.py` | Production | Public entry point | Builds API dependencies, owns ApiContext, and starts Flask. |
| `api/request_boundary.py` | Production | Private implementation | Authenticates requests and translates API and HTTP failures into responses. |
| `api/routes.py` | Production | Private implementation | Defines ingestion, management, hold, job, system, and notification routes. |
| `api/simulations.py` | Production | Private implementation | Converts a profile's declared simulations into the existing admin-simulator scenario JSON, substituting reserved simulation IDs for persona/group keys. |
| `auth/__init__.py` | Production | Public facade | Exposes authorization contracts. |
| `auth/permissions.py` | Production | Private implementation | Maps actions to permission levels and evaluates authorization. |
| `auth/user_names.py` | Production | Private implementation | Normalizes and validates the single full-name field associated with a Telegram identity. |
| `bot/__init__.py` | Production | Public facade | Exposes the bot facade and compatibility module aliases. |
| `bot/app.py` | Production | Public entry point | Builds bot dependencies, routes Telegram updates, and starts polling. |
| `bot/background_services.py` | Production | Private implementation | Polls and dispatches notifications, persists cursors, and manages single-instance startup. |
| `bot/contracts.py` | Production | Private implementation | Defines bot DTOs, client interfaces, dependency contracts, and errors. |
| `bot/interactions.py` | Production | Private implementation | Formats messages and handles commands, holds, settings, and profile interactions. |
| `bot/presentation.py` | Production | Private implementation | Implements the shared Telegram/CLI status replacement and fallback lifecycle. |
| `bot/simulator_app.py` | Production | Public entry point | Runs the simulation-mode bot process — real handlers/background loops, stubbed Telegram network (docs/bot_simulation_mode_design.md). |
| `bot/simulator_transport.py` | Production | Private implementation | Defines the Telegram-network stubs (`FakeBotRequest`, `SimulatorTelegramClient`) and synthetic-Update construction for simulation-mode. |
| `bot/transports.py` | Production | Private implementation | Implements HTTP API access and Telegram transport adapters. |
| `cli/__init__.py` | Production | Public facade | Marks the command-line package. |
| `cli/group_admin.py` | Production | Public entry point | Provides the Telegram group routing administration command-line entry point. |
| `cli/user_admin.py` | Production | Public entry point | Provides the user-administration command-line entry point. |
| `config/__init__.py` | Production | Public facade | Exposes environment and live-settings configuration facades. |
| `config/environment.py` | Production | Private implementation | Resolves model tiers and process flags from environment values. |
| `config/live_settings.py` | Production | Private implementation | Persists retry, risk, and lookback settings atomically. |
| `config/server_control.py` | Production | Private implementation | Discovers safe profiles and exchanges restart, profile-switch, reset, and status messages with the stack supervisor. |
| `conftest.py` | Project | Internal | Defines repository-wide pytest fixtures, model-tier configuration, and trace isolation. |
| `docs/Admin_Profile_Switch_Investigation.md` | Documentation | Internal | Investigates why the admin panel doesn't come up after a profile switch, records the confirmed root cause, and the fix applied. |
| `docs/DEMO_READY.md` | Documentation | Internal | Documents DEMO READY. |
| `docs/GT critial agents.pptx.pdf` | Documentation | Internal | Stores the GT critial agents.pptx reference artifact. |
| `docs/IMPROVES/ADMIN_LOGIN_LOCKOUT_DIAGNOSIS.MD` | Documentation | Internal | Records the admin-login lockout diagnosis. |
| `docs/IMPROVES/AREA_FIELD_REGRESSION_CHECK.MD` | Documentation | Internal | Records the area-field regression investigation. |
| `docs/IMPROVES/CRITICAL_FIXES_PLAN.MD` | Documentation | Internal | Defines the critical-fixes implementation plan. |
| `docs/IMPROVES/DIAGNOSTIC_FINDINGS.MD` | Documentation | Internal | Records diagnostic findings from system review. |
| `docs/IMPROVES/HELP_COMMAND_DESIGN_INPUTS.MD` | Documentation | Internal | Records design inputs for the help command. |
| `docs/IMPROVES/REQUIRED_FIELDS_AND_CLOSED_DECISIONS.md` | Documentation | Internal | Records required fields and closed implementation decisions. |
| `docs/IMPROVES/TELEGRAM_UX_FINDINGS.MD` | Documentation | Internal | Records Telegram user-experience findings. |
| `docs/IMPROVES/TEST_INTEGRITY_CHECK.MD` | Documentation | Internal | Records test-integrity review findings. |
| `docs/IMPROVES/UX_PREDICTABILITY_PLAN.MD` | Documentation | Internal | Defines the user-experience predictability plan. |
| `docs/PRODUCTION_READY.md` | Documentation | Internal | Documents PRODUCTION READY. |
| `docs/SECURITY_AND_QA_AUDIT.md` | Documentation | Internal | Records the security and quality-assurance audit. |
| `docs/agent_authoring.md` | Documentation | Internal | Documents agent authoring. |
| `docs/allowed_calls.md` | Documentation | Internal | Documents allowed calls. |
| `docs/api_spec.md` | Documentation | Internal | Documents api spec. |
| `docs/bar_improves.md` | Documentation | Internal | Specifies the operational-profiles improvement task: availability fields, the Response Team and Fire and Rescue Station profiles, their simulation deployments, and acceptance scenarios. |
| `docs/bot_simulation_mode_design.md` | Documentation | Internal | Documents the simulation-mode bot process's architecture: reusing real handlers/background loops with stubbed Telegram network legs. |
| `docs/code_example.py` | Documentation | Internal | Documents code example. |
| `docs/cost_latency_review.md` | Documentation | Internal | Documents cost latency review. |
| `docs/file_catalog.md` | Documentation | Internal | Documents file catalog. |
| `docs/BOT_EVALUATION_REPORT_2026-09-08.md` | Documentation | Internal | Records the 2026-09-08 bot evaluation results and coverage gaps. |
| `docs/TELEGRAM_E2E_REPORT_2026-09-08.md` | Documentation | Internal | Records the 2026-09-08 live Telegram end-to-end test results. |
| `docs/how_to_connect_telegram.md` | Documentation | Internal | Documents how to connect telegram. |
| `docs/investigation_summary.md` | Documentation | Internal | Documents investigation summary. |
| `docs/links.txt` | Documentation | Internal | Documents links. |
| `docs/Next_Plan.md` | Documentation | Internal | Defines deferred optimizations that remain disabled until the current speed changes pass their gates. |
| `docs/operator_guide.md` | Documentation | Internal | Documents operator guide. |
| `docs/profile_simulations_design.md` | Documentation | Internal | Documents the per-profile simulation mechanism's architecture, data model, reserved ID scheme, and file impact. |
| `docs/Profile_Split_Plan.md` | Documentation | Internal | Plans and records the Standby Squad/Firefighting profile split: architecture, protocol traceability, migration, and implementation deviations. |
| `docs/profile_spec.md` | Documentation | Internal | Documents profile spec. |
| `docs/progress.md` | Documentation | Internal | Documents progress. |
| `docs/questions.txt` | Documentation | Internal | Documents questions. |
| `docs/server_report.md` | Documentation | Internal | Documents server report. |
| `docs/unified_command_guide.md` | Documentation | Internal | Operational and architectural guide for Unified Command Hub profile (Hebrew). |
| `docs/vocabulary.md` | Documentation | Internal | Documents vocabulary. |
| `docs/work_plan.md` | Documentation | Internal | Documents work plan. |
| `docs/work_process.md` | Documentation | Internal | Running chronological log of the per-profile simulation mechanism's design, implementation, diagnoses, and fixes. |
| `docs/ארכיטקטוררה.pptx` | Documentation | Internal | Stores the ארכיטקטוררה reference artifact. |
| `docs/מצגת ארכיטקטורה.pptx` | Documentation | Internal | Stores the מצגת ארכיטקטורה reference artifact. |
| `docs/תיאור מבנה מערכת.pdf` | Documentation | Internal | Stores the תיאור מבנה מערכת reference artifact. |
| `docs/תיאור משימות שבועיות.pdf` | Documentation | Internal | Stores the תיאור משימות שבועיות reference artifact. |
| `fixtures/__init__.py` | Fixture | Internal | Marks reusable fixtures as a package. |
| `fixtures/adversarial_disclosure_v1.jsonl` | Fixture | Internal | Provides a versioned Hebrew and English adversarial disclosure-safety corpus. |
| `fixtures/admin_scenarios/כיתת כוננת - חלק 1.json` | Fixture | Internal | Provides the first readiness-team simulator scenario. |
| `fixtures/admin_scenarios/כיתת כוננת - חלק 2.json` | Fixture | Internal | Provides the second readiness-team simulator scenario. |
| `fixtures/admin_scenarios/כיתת כוננת - חלק 3.json` | Fixture | Internal | Provides the third readiness-team simulator scenario. |
| `fixtures/admin_scenarios/מכבי אש - חלק 1.json` | Fixture | Internal | Provides the first fire-response simulator scenario. |
| `fixtures/admin_scenarios/מכבי אש - חלק 2.json` | Fixture | Internal | Provides the second fire-response simulator scenario. |
| `fixtures/admin_scenarios/מכבי אש - חלק 3.json` | Fixture | Internal | Provides the third fire-response simulator scenario. |
| `fixtures/profiles/__init__.py` | Fixture | Internal | Marks fixture deployment profiles as a package. |
| `fixtures/profiles/minimal_profile.py` | Fixture | Internal | Defines the minimal valid profile used by loading and integration tests. |
| `fixtures/response_eval_v1.jsonl` | Fixture | Internal | Provides a versioned Hebrew and English response-quality corpus. |
| `fixtures/seed_events.py` | Fixture | Internal | Provides deterministic historical event fixtures. |
| `history/__init__.py` | Production | Public facade | Exposes the history facade and compatibility module aliases. |
| `history/contracts.py` | Production | Private implementation | Defines history extraction, query, summary, and persistence-transfer contracts. |
| `history/event_pipeline.py` | Production | Private implementation | Extracts events, normalizes timestamps, and writes durable history state. |
| `history/field_catalog.py` | Production | Private implementation | English meanings and narrative/internal category for every persisted event field. |
| `history/query.py` | Production | Private implementation | Retrieves raw and summarized history and searches precedents. |
| `history/summaries.py` | Production | Private implementation | Generates and reconciles daily, monthly, and yearly summaries. |
| `instructions.md` | Project | Internal | Defines repository-specific development and architecture rules. |
| `load-env.ps1` | Project | Internal | Loads local development environment variables into PowerShell. |
| `messages/__init__.py` | Production | Public facade | Selects and validates the active immutable user-interface message catalog. |
| `messages/catalog.py` | Production | Private implementation | Defines strict catalog lookup, formatting, key parity, and placeholder validation. |
| `messages/en.py` | Production | Private implementation | Contains every fixed English user-interface string. |
| `messages/he.py` | Production | Private implementation | Contains every fixed Hebrew user-interface string. |
| `messages/model_messages.py` | Production | Private implementation | Centralizes prompts used only to formulate natural user-facing model text. |
| `my_fake_bot_test.py` | Test | Internal | Provides a standalone fake-bot test harness outside pytest discovery. |
| `orchestrator/__init__.py` | Production | Public facade | Exposes orchestration capabilities and compatibility module aliases. |
| `orchestrator/capabilities.py` | Production | Private implementation | Builds the role-aware, per-caller Main Agent capability and system context. |
| `orchestrator/event_queue.py` | Production | Private implementation | Serializes event processing on a dedicated worker. |
| `orchestrator/flows.py` | Production | Private implementation | Coordinates report, request, hold-resume, protocol, and outcome workflows. |
| `orchestrator/group_routing.py` | Production | Private implementation | Holds the in-memory, DB-backed Telegram group to agent routing table and scopes flow dependencies per group. |
| `orchestrator/holds.py` | Production | Private implementation | Creates and resolves clarification and approval holds. |
| `orchestrator/reasoning.py` | Production | Private implementation | Prompts and parses Main/Insights decisions, questions, selection, formulation, and judgment. |
| `orchestrator/situational_picture.py` | Production | Private implementation | Builds the multi-domain situational picture at request time: the Main Agent plans one live question per specialist, gathers their answers and the recent event log concurrently, and composes the picture from those findings only. |
| `persistence/__init__.py` | Production | Public facade | Exposes persistence contracts, constructors, and compatibility aliases. |
| `persistence/contracts.py` | Production | Private implementation | Defines persistence interfaces and domain errors. |
| `persistence/schema.py` | Production | Private implementation | Owns immutable migration DDL and the current SQLite schema. |
| `persistence/sqlite_store.py` | Production | Private implementation | Implements serialized SQLite persistence, transactions, and row conversion. |
| `persistence/surveillance_contracts.py` | Production | Private implementation | Defines camera, drone, and surveillance-mission persistence contracts. |
| `persistence/surveillance_store.py` | Production | Private implementation | Implements the isolated SQLite surveillance store. |
| `persistence/team_status_contracts.py` | Production | Private implementation | Defines the database-agnostic readiness-team status persistence contract and constructor. |
| `persistence/team_status_store.py` | Production | Private implementation | Implements the isolated SQLite store for readiness-team roster and attendance state. |
| `profiles/__init__.py` | Production | Public facade | Exposes profile contracts, loading, registries, and compatibility aliases. |
| `profiles/contracts.py` | Production | Private implementation | Defines profile declarations, loaded-profile state, and area/event-type registries. |
| `profiles/fire_station.py` | Production | Private implementation | Defines the Fire and Rescue Station profile (structure/hazmat fire, rescue, mutual-aid dispatch, attendance) (docs/bar_improves.md). |
| `profiles/fire_station_sim.py` | Production | Private implementation | Defines the Fire and Rescue Station simulation deployment, reusing the live profile's declared content (docs/bar_improves.md). |
| `profiles/firefighting.py` | Production | Private implementation | Defines the Firefighting profile (crew status, visual surveillance, mutual-aid dispatch) and the FIRE_002 simulations (docs/Profile_Split_Plan.md). |
| `profiles/loader.py` | Production | Private implementation | Imports, validates, hashes, and constructs deployment profiles and registries. |
| `profiles/response_team.py` | Production | Private implementation | Defines the Response Team profile (perimeter/external-force observation, surveillance faults, team status, attendance) (docs/bar_improves.md). |
| `profiles/response_team_sim.py` | Production | Private implementation | Defines the Response Team simulation deployment, reusing the live profile's declared content (docs/bar_improves.md). |
| `profiles/simulation.py` | Production | Private implementation | Defines simulation persona, group, scenario, and roster declarations and the reserved Telegram ID scheme. |
| `profiles/simulation_provisioning.py` | Production | Private implementation | Ensures a profile's declared simulation users and groups exist, and registers/approves any of them on the agent-owned rosters they declare. |
| `profiles/standby_squad.py` | Production | Private implementation | Defines the Standby Squad profile (readiness-team status, visual surveillance, friendly-forces dispatch) and the SEC_001 simulations (docs/Profile_Split_Plan.md). |
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
| `tests/test_api_admin.py` | Test | Internal | Verifies the admin web panel's login, session, CSRF, rate limiting, and user-management behavior. |
| `tests/test_api_app.py` | Test | Internal | Verifies api app behavior and edge cases. |
| `tests/test_api_groups.py` | Test | Internal | Verifies Telegram group binding routes, group-scoped message handling, and the attendance-check trigger. |
| `tests/test_api_holds.py` | Test | Internal | Verifies api holds behavior and edge cases. |
| `tests/test_api_jobs.py` | Test | Internal | Verifies api jobs behavior and edge cases. |
| `tests/test_api_messages.py` | Test | Internal | Verifies api messages behavior and edge cases. |
| `tests/test_api_notifications.py` | Test | Internal | Verifies api notifications behavior and edge cases. |
| `tests/test_api_protocols.py` | Test | Internal | Verifies api protocols behavior and edge cases. |
| `tests/test_api_request_boundary.py` | Test | Internal | Verifies authentication and structured API error translation. |
| `tests/test_api_simulations.py` | Test | Internal | Verifies `GET /Simulations` and `GET /Simulations/<key>` permission gating and ID materialization. |
| `tests/test_api_situational_picture.py` | Test | Internal | Verifies `/Msg` builds the multi-domain picture from live specialist answers and caller-scoped recent events, by hint or in plain words. |
| `tests/test_api_system.py` | Test | Internal | Verifies api system behavior and edge cases. |
| `tests/test_api_trace.py` | Test | Internal | Verifies commander-only Deep Debug trace polling, authorization, ordering, and rendering. |
| `tests/test_api_unified_ingestion.py` | Test | Internal | Verifies api unified ingestion behavior and edge cases. |
| `tests/test_approvals_queue.py` | Test | Internal | Verifies commander approvals queue API and Telegram interactions. |
| `tests/test_architecture.py` | Test | Internal | Enforces package boundaries and prevents recreation of the registries package. |
| `tests/test_bot_app.py` | Test | Internal | Verifies bot dependency wiring, entry-point behavior, and update routing. |
| `tests/test_bot_background_services.py` | Test | Internal | Verifies notification polling, delivery, failures, results, and startup services. |
| `tests/test_bot_groups.py` | Test | Internal | Verifies bot group handling: binding cache, ignoring unbound groups, chat metadata on messages, attendance prompts and buttons. |
| `tests/test_bot_holds.py` | Test | Internal | Verifies clarification and approval interaction lifecycles. |
| `tests/test_bot_interactions.py` | Test | Internal | Verifies profile, settings, user, formatting, and command interactions. |
| `tests/test_bot_presentation.py` | Test | Internal | Verifies shared status editing, long-message splitting, and fallback behavior. |
| `tests/test_bot_simulator_app.py` | Test | Internal | Verifies the simulation-mode bot process end-to-end: identity gating, dispatch through real handlers, reply capture. |
| `tests/test_bot_simulator_transport.py` | Test | Internal | Verifies `FakeBotRequest`, `SimulatorTelegramClient`, and synthetic-Update construction against real PTB filters. |
| `tests/test_bot_transports.py` | Test | Internal | Verifies bot HTTP clients, abstract client behavior, and Telegram transports. |
| `tests/test_cli_group_admin.py` | Test | Internal | Verifies the Telegram group routing administration command. |
| `tests/test_environment_config.py` | Test | Internal | Verifies environment-backed model and runtime configuration. |
| `tests/test_file_catalog.py` | Test | Internal | Ensures this catalog exactly matches the first-party repository tree. |
| `tests/test_friendly_forces_agent.py` | Test | Internal | Verifies friendly forces agent dispatch tools and coordination records. |
| `tests/test_firefighting_external_forces_agent.py` | Test | Internal | Verifies FirefightingExternalForcesAgent's two new mutual-aid tools (docs/Profile_Split_Plan.md). |
| `tests/test_group_routing.py` | Test | Internal | Verifies the group routing table, staleness refresh, scope resolution, and dependency scoping. |
| `tests/test_history_agent.py` | Test | Internal | Verifies history agent behavior and edge cases. |
| `tests/test_history_event_pipeline.py` | Test | Internal | Verifies extraction, time normalization, and durable history writes. |
| `tests/test_history_logging.py` | Test | Internal | Verifies history logging behavior and edge cases. |
| `tests/test_history_precedent.py` | Test | Internal | Verifies history precedent behavior and edge cases. |
| `tests/test_history_query.py` | Test | Internal | Verifies history query behavior and edge cases. |
| `tests/test_hebrew_leakage.py` | Test | Internal | Verifies Hebrew responses do not leak unintended internal content. |
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
| `tests/test_integration_profile_simulations.py` | Test | Internal | Verifies a materialized simulation runs end to end through the real `/Msg`/`/Event` endpoints, with reserved IDs provisioned by `ensure_simulation_entities` alone. |
| `tests/test_integration_retry_exhaustion.py` | Test | Internal | Verifies the retry exhaustion scenario across real subsystem boundaries. |
| `tests/test_integration_serial_processing_under_load.py` | Test | Internal | Verifies the serial processing under load scenario across real subsystem boundaries. |
| `tests/test_integration_user_administration.py` | Test | Internal | Verifies the user administration scenario across real subsystem boundaries. |
| `tests/test_legacy_imports.py` | Test | Internal | Verifies supported implementation-path aliases resolve to canonical modules. |
| `tests/test_migrations.py` | Test | Internal | Verifies migrations behavior and edge cases. |
| `tests/test_messages.py` | Test | Internal | Verifies language catalogs, key and placeholder parity, strict formatting, and selection. |
| `tests/test_observability.py` | Test | Internal | Verifies tracing and structured logging behavior. |
| `tests/test_operational_profiles.py` | Test | Internal | Verifies the Response Team and Fire and Rescue Station profiles, their simulation deployments, and the tool-result rule (docs/bar_improves.md). |
| `tests/test_operational_scenarios.py` | Test | Internal | Verifies the operational-profile acceptance scenarios offline through the real API with the model boundary faked (docs/bar_improves.md). |
| `tests/test_orchestrator_flows.py` | Test | Internal | Verifies orchestrator flows behavior and edge cases. |
| `tests/test_orchestrator_capabilities.py` | Test | Internal | Verifies role-aware capability descriptor and system-context behavior. |
| `tests/test_orchestrator_holds.py` | Test | Internal | Verifies orchestrator holds behavior and edge cases. |
| `tests/test_orchestrator_insights.py` | Test | Internal | Verifies orchestrator insights behavior and edge cases. |
| `tests/test_orchestrator_judgment.py` | Test | Internal | Verifies orchestrator judgment behavior and edge cases. |
| `tests/test_orchestrator_reasoning.py` | Test | Internal | Verifies Main Agent reasoning, parsing, and decisions. |
| `tests/test_orchestrator_selection.py` | Test | Internal | Verifies orchestrator selection behavior and edge cases. |
| `tests/test_permissions.py` | Test | Internal | Verifies permissions behavior and edge cases. |
| `tests/test_persistence_conformance.py` | Test | Internal | Verifies persistence conformance behavior and edge cases. |
| `tests/test_persistence_events.py` | Test | Internal | Verifies persistence events behavior and edge cases. |
| `tests/test_profile_loading.py` | Test | Internal | Verifies profile imports, validation, construction, and registry configuration. |
| `tests/test_profile_simulations.py` | Test | Internal | Verifies the simulation ID scheme, profile validation, provisioning, and JSON materialization. |
| `tests/test_protocol_repository.py` | Test | Internal | Verifies protocol loading, validation, rendering, and atomic editing. |
| `tests/test_protocol_retry.py` | Test | Internal | Verifies protocol retry behavior and edge cases. |
| `tests/test_provider_telemetry.py` | Test | Internal | Verifies CrewAI provider-event correlation, usage fields, failures, and race recovery. |
| `tests/test_question_answering.py` | Test | Internal | Verifies question routing and read-only specialist/history answers. |
| `tests/test_reference_agent.py` | Test | Internal | Verifies reference agent behavior and edge cases. |
| `tests/test_response_improvements.py` | Test | Internal | Verifies conversation retention, long polling, trace propagation, queue ordering, idempotency, and removal of the obsolete stream route. |
| `tests/test_run_stack.py` | Test | Internal | Verifies profile-database reset removes only declared databases and known sidecars, and refuses a non-database path. |
| `tests/test_server_control.py` | Test | Internal | Verifies safe profile discovery and supervisor command and selection persistence. |
| `tests/test_situational_picture.py` | Test | Internal | Verifies picture planning, per-domain live questioning, recent-events window and scope, unavailable-domain handling, and composition fallbacks. |
| `tests/test_sqlite_store.py` | Test | Internal | Verifies SQLite serialization, concurrency, and user persistence. |
| `tests/test_surveillance_agent.py` | Test | Internal | Verifies camera, drone, dispatch, mission, and overview tools. |
| `tests/test_surveillance_persistence.py` | Test | Internal | Verifies surveillance database initialization, updates, dispatch, and mission state. |
| `tests/test_team_status_agent.py` | Test | Internal | Verifies daily attendance, multi-day unavailability, late approval, and protocol execution. |
| `tests/test_team_status_persistence.py` | Test | Internal | Verifies readiness-team roster approval, message idempotency, late-response isolation, and separate SQLite schemas. |
| `tests/test_standby_squad_role_and_security.py` | Test | Internal | Verifies Standby Squad role-based security, button workflows, and confirmation flows. |
| `tests/test_unsafe_system.py` | Test | Internal | Verifies safe/open Telegram admission, automatic registration, approval, and API isolation. |
| `tests/test_user_admin.py` | Test | Internal | Verifies user admin behavior and edge cases. |
| `tools/__init__.py` | Production | Public facade | Exposes shared observability helpers and lazy terminal compatibility aliases. |
| `tools/observability.py` | Production | Private implementation | Provides trace contexts, structured logging, and human/JSON output. |
| `tools/evaluate_response_pipeline.py` | Production | Public entry point | Runs versioned offline response evals and opt-in billed live evaluation. |
| `tools/simulator.py` | Production | Public entry point | Provides the event-simulator executable entry point. |
| `tools/terminal_client_commander.py` | Production | Public entry point | Provides the commander terminal-client executable workflow. |
| `tools/terminal_client_viewer.py` | Production | Public entry point | Provides the viewer terminal-client executable workflow. |
| `tools/terminal_support.py` | Production | Private implementation | Shares terminal HTTP, notification, identity, and interactive-mode helpers. |
