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

from dataclasses import dataclass
from typing import TYPE_CHECKING

from flask import Flask

from agents.registry import build_agent_registry
from config.base import load_base_config
from config.settings_store import SettingsStore
from history.interface import SummaryScheduler
from history.query import HistoryQueryService
from orchestrator.flows import FlowDeps, SerialEventQueue, assemble_core_agents
from persistence.interface import open_persistence
from profiles.loader import load_profile
from protocols.loader import load_protocols
from registries.areas import build_area_registry
from registries.event_types import build_event_type_registry

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
    is an `(event_id, work_fn)` pair. `job_status` (api/jobs.py) reads
    that same convention back through `queue.currently_processing()`.
    """

    _event_id, work_fn = item
    work_fn()


def build_context(module_path: str) -> ApiContext:
    loaded_profile = load_profile(module_path)
    base_config = load_base_config()

    persistence = open_persistence(loaded_profile.db_path)
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

    from api.errors import register_error_handlers
    from api.events import build_events_blueprint
    from api.holds import build_holds_blueprint
    from api.jobs import build_jobs_blueprint
    from api.messages import build_messages_blueprint
    from api.notifications import build_notifications_blueprint
    from api.protocols import build_protocols_blueprint
    from api.system import build_system_blueprint
    from api.users import build_users_blueprint

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


def create_app(module_path: str) -> Flask:
    return build_app(build_context(module_path))
