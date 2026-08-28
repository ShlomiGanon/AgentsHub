"""API startup wiring — the package's declared entry point."""

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

from flask import Flask

from agents import build_agent_registry
from config import ModelTierError, SettingsStore, TierModel, load_base_config, resolve_tier_model_from_env
from history import SummaryScheduler
from history.query import HistoryQueryService
from orchestrator.flows import FlowDeps, SerialEventQueue, assemble_core_agents
from persistence import open_persistence
from profiles import build_area_registry, build_event_type_registry
from profiles.loader import load_profile
from protocols import load_protocols
from tools import configure_logging, set_trace_id

if TYPE_CHECKING:
    from agents import Agent
    from history import SummaryScheduler
    from orchestrator.flows import FlowDeps, SerialEventQueue
    from profiles.loader import LoadedProfile


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

    persistence = open_persistence(loaded_profile.db_path)
    configure_logging(loaded_profile.module_path, persistence=persistence)
    base_config = load_base_config(core_model=core_model)

    settings_store = SettingsStore(
        loaded_profile.db_path,
        loaded_profile.retry_count,
        loaded_profile.risk_threshold,
        loaded_profile.lookback_window_days,
    )

    core_agents = assemble_core_agents(loaded_profile, base_config)
    registry = build_agent_registry(core_agents, list(loaded_profile.agents))

    history_agent = registry.get("history_agent")
    history_query_service = HistoryQueryService(persistence, history_agent, settings_store)

    deps = FlowDeps(
        persistence=persistence,
        settings_store=settings_store,
        registry=registry,
        protocol_set=load_protocols(loaded_profile),
        event_type_registry=build_event_type_registry(loaded_profile),
        area_registry=build_area_registry(loaded_profile),
        history_query_service=history_query_service,
    )

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
        set_trace_id("")

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
    args = parser.parse_args(argv)

    try:
        core_model = _tier_model_from_environ("CORE")
        sub_model = _tier_model_from_environ("SUB")
    except ModelTierError as exc:
        raise SystemExit(f"failed to start API: {exc}") from exc

    ctx = build_context(args.profile_module, core_model=core_model, sub_model=sub_model)
    app = build_app(ctx)
    app.run(host=args.host, port=ctx.loaded_profile.api_port)


if __name__ == "__main__":
    main()
