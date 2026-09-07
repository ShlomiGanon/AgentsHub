"""API startup wiring — the package's declared entry point."""

import os
import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from flask import Flask, g, request

from agents import (
    AgentInvocationError,
    build_agent_registry,
    configure_provider_concurrency,
    configure_structured_output_mode,
    configure_invocation_limits,
    initialize_agent_runtime,
    install_crewai_provider_telemetry,
    set_invocation_deadline,
)
from config import ModelTierError, SettingsStore, TierModel, load_base_config, resolve_tier_model_from_env
from history import SummaryScheduler
from messages import set_current_catalog
from history.query import HistoryQueryService
from orchestrator.flows import FlowDeps, PolicyAwareEventQueue, SerialEventQueue, assemble_core_agents
from persistence import open_persistence
from profiles import build_area_registry, build_event_type_registry
from profiles.loader import load_profile
from protocols import load_protocols
from tools import configure_logging, configure_telemetry, get_trace_id, normalize_trace_id, set_trace_id

if TYPE_CHECKING:
    from agents import Agent
    from history import SummaryScheduler
    from orchestrator.flows import FlowDeps, SerialEventQueue
    from profiles.loader import LoadedProfile

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ApiContext:
    deps: "FlowDeps"
    main_agent: "Agent"
    insights_agent: "Agent"
    loaded_profile: "LoadedProfile"
    queue: "SerialEventQueue"
    scheduler: "SummaryScheduler"


def _dispatch_queue_item(item: object) -> None:
    """The one `process_fn` every queued item runs through."""

    _event_id, work_fn = item
    work_fn()


def build_context(module_path: str, core_model: TierModel, sub_model: TierModel) -> ApiContext:
    """`core_model`/`sub_model` are the two already-resolved `TierModel`s — required, no default, no environment access anywhere in this function (`config.base` never reaches into `os...."""

    loaded_profile = load_profile(module_path, core_model=core_model, sub_model=sub_model)
    configure_provider_concurrency(loaded_profile.optimization_policy.provider_concurrency)
    configure_structured_output_mode(loaded_profile.optimization_policy.structured_output_mode)
    configure_invocation_limits(loaded_profile.max_iter, loaded_profile.model_timeout_seconds)

    persistence = open_persistence(loaded_profile.db_path)
    configure_logging(loaded_profile.module_path, persistence=persistence)
    install_crewai_provider_telemetry()
    base_config = load_base_config(core_model=core_model)

    settings_store = SettingsStore(
        loaded_profile.db_path,
        loaded_profile.retry_count,
        loaded_profile.risk_threshold,
        loaded_profile.lookback_window_days,
    )

    core_agents = assemble_core_agents(loaded_profile, base_config)
    registry = build_agent_registry(core_agents, list(loaded_profile.agents))

    try:
        initialize_agent_runtime(list(registry.all()))
    except Exception:
        persistence.close()
        raise

    history_agent = registry.get("history_agent")
    history_query_service = HistoryQueryService(
        persistence,
        history_agent,
        settings_store,
        classifications=loaded_profile.event_types,
        areas=loaded_profile.areas,
        protocol_names=tuple(protocol.name for protocol in loaded_profile.protocols),
        timezone_name=loaded_profile.timezone_name,
    )

    deps = FlowDeps(
        persistence=persistence,
        settings_store=settings_store,
        registry=registry,
        protocol_set=load_protocols(loaded_profile),
        event_type_registry=build_event_type_registry(loaded_profile),
        area_registry=build_area_registry(loaded_profile),
        history_query_service=history_query_service,
        optimization_policy=loaded_profile.optimization_policy,
        conversation_history_turns=loaded_profile.conversation_history_turns,
        conversation_history_ttl_hours=loaded_profile.conversation_history_ttl_hours,
    )

    queue_policy = loaded_profile.optimization_policy
    if queue_policy.event_queue_mode == "policy":
        queue = PolicyAwareEventQueue(
            _dispatch_queue_item,
            workers=queue_policy.event_workers,
            max_size=queue_policy.event_queue_size,
            reserved_continuation_percent=queue_policy.reserved_continuation_percent,
        )
    else:
        queue = SerialEventQueue(_dispatch_queue_item)
    queue.start()

    scheduler = SummaryScheduler(persistence, history_agent)
    scheduler.start()

    return ApiContext(
        deps=deps,
        main_agent=registry.get("main_agent"),
        insights_agent=registry.get("insights_agent"),
        loaded_profile=loaded_profile,
        queue=queue,
        scheduler=scheduler,
    )


def build_app(ctx: ApiContext) -> Flask:
    app = Flask(__name__)

    @app.before_request
    def _reset_trace_id_for_this_request() -> None:
        set_current_catalog(ctx.loaded_profile.message_catalog)
        set_trace_id(normalize_trace_id(request.headers.get("X-Trace-ID")))
        g.request_started_at = time.monotonic()
        logger.info(
            "API request started",
            extra={
                "event": "api_request_started",
                "route": request.path,
                "method": request.method,
                "trace_id": get_trace_id(),
            },
        )

    @app.after_request
    def _finish_request(response):
        logger.info(
            "API request finished",
            extra={
                "event": "api_request_finished",
                "route": request.path,
                "method": request.method,
                "status_code": response.status_code,
                "duration_seconds": time.monotonic() - g.request_started_at,
                "trace_id": get_trace_id(),
                "telemetry_only": True,
            },
        )
        response.headers["X-Trace-ID"] = get_trace_id()
        response.headers["X-Content-Type-Options"] = "nosniff"
        set_invocation_deadline(None)
        return response

    from api.request_boundary import register_error_handlers
    from api.routes import (
        build_events_blueprint,
        build_holds_blueprint,
        build_jobs_blueprint,
        build_messages_blueprint,
        build_notifications_blueprint,
        build_protocols_blueprint,
        build_system_blueprint,
        build_users_blueprint,
    )

    register_error_handlers(app)
    app.register_blueprint(build_events_blueprint(ctx))
    app.register_blueprint(build_messages_blueprint(ctx))
    app.register_blueprint(build_jobs_blueprint(ctx))
    app.register_blueprint(build_holds_blueprint(ctx))
    app.register_blueprint(build_protocols_blueprint(ctx))
    app.register_blueprint(build_system_blueprint(ctx))
    app.register_blueprint(build_users_blueprint(ctx))
    app.register_blueprint(build_notifications_blueprint(ctx))

    return app


def create_app(module_path: str, core_model: TierModel, sub_model: TierModel) -> Flask:
    return build_app(build_context(module_path, core_model=core_model, sub_model=sub_model))


def _tier_model_from_environ(prefix: str) -> TierModel:
    """Read one tier's provider/model name/API key straight from the real process environment — `main`'s own job, the one place in this module `os.environ` is read for model-tier confi..."""

    return resolve_tier_model_from_env(prefix, error_type=ModelTierError)


def main(argv: list[str] | None = None) -> None:
    """Run the API layer for one deployment (work_plan.md §9.21)."""

    import argparse

    parser = argparse.ArgumentParser(description="Run the API layer for one deployment (work_plan.md §7, §9.21).")
    parser.add_argument("profile_module", help="dotted module path of the profile to run, e.g. profiles.demo")
    parser.add_argument("--host", default="127.0.0.1", help="network interface to bind (default: 127.0.0.1, localhost only)")
    parser.add_argument("--server", choices=("flask", "waitress"), default="flask", help="HTTP server (default: flask for local development)")
    parser.add_argument("--threads", type=int, default=16, help="Waitress worker threads (default: 16, minimum: 4)")
    args = parser.parse_args(argv)

    if args.server == "waitress" and args.threads < 4:
        parser.error("--threads must be at least 4 when --server=waitress")

    try:
        core_model = _tier_model_from_environ("CORE")
        sub_model = _tier_model_from_environ("SUB")
    except ModelTierError as exc:
        raise SystemExit(f"failed to start API: {exc}") from exc

    configure_telemetry()
    try:
        ctx = build_context(args.profile_module, core_model=core_model, sub_model=sub_model)
    except AgentInvocationError as exc:
        raise SystemExit(f"failed to start API: {exc}") from exc
    app = build_app(ctx)
    parser_mode = getattr(args, "server", "flask")
    if parser_mode == "waitress":
        try:
            from waitress import serve
        except ImportError as exc:
            raise SystemExit("failed to start API: waitress is not installed") from exc
        serve(app, host=args.host, port=ctx.loaded_profile.api_port, threads=args.threads)
    else:
        app.run(host=args.host, port=ctx.loaded_profile.api_port, threaded=True)


if __name__ == "__main__":
    main()
