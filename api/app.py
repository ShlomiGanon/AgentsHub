"""API startup wiring — the package's declared entry point.

Assembles everything a running API needs from a loaded profile: the
agent registry (Mission 6's `orchestrator.flows.assemble_core_agents`
merges core agents, but nothing before this module builds the registry,
the protocol/area/event-type registries, the history query service, the
settings store, the serial queue, or the summary scheduler from a
`LoadedProfile` all at once) — and the Flask app itself. `api/` is the
first package that needs all of it together to serve a single request,
so this is where that wiring has to live.

`build_context` and `build_app` are split from `create_app` so tests can
construct an `ApiContext` by hand (a fake registry, a temp-file
persistence, no real profile module) and exercise routes against it
without going through `load_profile`.
"""

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

from flask import Flask

from agents.registry import build_agent_registry
from config.base import ModelTierError, TierModel, build_tier_model, load_base_config
from config.settings_store import SettingsStore
from history.interface import SummaryScheduler
from history.query import HistoryQueryService
from orchestrator.flows import FlowDeps, SerialEventQueue, assemble_core_agents
from persistence.interface import open_persistence
from profiles.loader import load_profile
from protocols.loader import load_protocols
from registries.areas import build_area_registry
from registries.event_types import build_event_type_registry
from tools.logging_config import configure_logging
from tools.tracing import set_trace_id

if TYPE_CHECKING:
    from agents.base import Agent
    from profiles.loader import LoadedProfile


@dataclass(frozen=True)
class ApiContext:
    deps: FlowDeps
    main_agent: "Agent"
    insights_agent: "Agent"
    loaded_profile: "LoadedProfile"
    queue: SerialEventQueue
    scheduler: SummaryScheduler


def _dispatch_queue_item(item: object) -> None:
    """The one `process_fn` every queued item runs through.

    `orchestrator.queue.SerialEventQueue` stays fully generic over what
    an "item" is (work_plan.md §6.15's own design) — this function is the
    api/-only convention that gives items meaning: every one api/ submits
    is an `(event_id, work_fn)` pair. `job_status` (api/operations.py) reads
    that same convention back through `queue.currently_processing()`.
    """

    _event_id, work_fn = item
    work_fn()


def build_context(module_path: str, core_model: TierModel, sub_model: TierModel) -> ApiContext:
    """`core_model`/`sub_model` are the two already-resolved `TierModel`s
    — required, no default, no environment access anywhere in this
    function (`config.base` never reaches into `os.environ` either; see
    config/base.py). `core_model` feeds both `load_profile` (the History
    Agent) and `load_base_config` (Main/Insights agents); `sub_model`
    feeds `load_profile` alone, for whatever a profile's own `AGENTS`
    declares on that tier. `main`, below, is the one place in this module
    that decides where these values come from.
    """

    loaded_profile = load_profile(module_path, core_model=core_model, sub_model=sub_model)

    # Persistence opens before logging configures — deliberately reordered
    # from this function's original sequence — specifically so the
    # DB-backed log sink (work_plan.md §1.8 follow-up) has a handle to
    # attach to. Nothing between the old and new position depended on the
    # old order: `open_persistence` reads nothing `configure_logging`/
    # `load_base_config` produce, and neither of those logs anything
    # during its own setup that would be lost by running after this.
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

    # Isolates each request's trace ID on the thread that serves it, before
    # anything else runs — see tools.tracing.set_trace_id's own docstring
    # for why a route sets one this way instead of scoping it with
    # trace_context. Without this reset, a route that never mints a trace
    # ID at all (a plain read like GET /SYSTEM) would inherit whatever
    # value a *previous*, unrelated request left set on this same
    # request-serving thread — a false correlation, worse than none.
    @app.before_request
    def _reset_trace_id_for_this_request() -> None:
        set_trace_id("")

    from api.errors import register_error_handlers
    from api.ingestion import build_events_blueprint, build_messages_blueprint
    from api.management import build_protocols_blueprint, build_system_blueprint, build_users_blueprint
    from api.operations import build_holds_blueprint, build_jobs_blueprint, build_notifications_blueprint

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


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if value is None:
        raise ModelTierError(f"required environment variable '{name}' is not set")
    return value


def _tier_model_from_environ(prefix: str) -> TierModel:
    """Read one tier's provider/model name/API key straight from the real
    process environment — `main`'s own job, the one place in this module
    `os.environ` is read for model-tier config (see config/base.py's
    module docstring). `prefix` is `"CORE"` or `"SUB"`.
    """

    provider = _require_env(f"{prefix}_MODEL_PROVIDER")
    model_name = _require_env(f"{prefix}_MODEL_NAME")
    api_key_env_name = _require_env(f"{prefix}_MODEL_API_KEY_ENV")
    api_key = _require_env(api_key_env_name)
    return build_tier_model(provider, model_name, api_key)


def main(argv: list[str] | None = None) -> None:
    """Run the API layer for one deployment (work_plan.md §9.21).

    The localhost-demo launch path this subtask asks for — every host,
    port, and path the running server uses comes from the named profile
    (§1.4/§7.1), never a flag, so the identical build runs unmodified for
    any profile. `--host` is the one exception, deliberately: which
    network interface to bind is a deployment concern the profile itself
    has no opinion on, not a per-deployment identity value like the port
    is. Production process supervision, TLS, and everything else
    `docs/NEXT_STAGE.md` covers stays out of scope here — this is
    `app.run()`, Flask's own development server, exactly matching what
    "package... to run on localhost for the demonstration" asks for.

    This is one of the three real entry points (with `bot.app.main`,
    `cli.user_admin.main`) that reads `os.environ` for model-tier config —
    everything below it takes already-resolved `TierModel` values instead.
    """

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
