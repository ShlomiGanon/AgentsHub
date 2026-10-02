"""JSON route assembler: domain blueprints plus re-exports for callers."""

from api._route_deps import (
    _failed_step_agent_name,
    _now,
    _steps_completed,
    _work_concurrency_keys,
    failed_step_agent_name,
    job_status,
    protocol_from_body,
    protocol_to_dict,
    steps_completed,
    utc_now_storage,
    work_concurrency_keys,
)
from api.routes_events import build_events_blueprint
from api.routes_groups import build_groups_blueprint
from api.routes_holds import build_holds_blueprint
from api.routes_jobs import build_jobs_blueprint
from api.routes_messages import (
    KNOWN_BUTTON_PROTOCOLS,
    SITUATIONAL_PICTURE_PROTOCOL,
    build_messages_blueprint,
)
from api.routes_notifications import build_notifications_blueprint
from api.routes_protocols import build_protocols_blueprint
from api.routes_simulations import build_simulations_blueprint
from api.routes_system import build_system_blueprint
from api.routes_telegram import build_telegram_blueprint
from api.routes_users import build_users_blueprint

__all__ = [
    "KNOWN_BUTTON_PROTOCOLS",
    "SITUATIONAL_PICTURE_PROTOCOL",
    "build_events_blueprint",
    "build_groups_blueprint",
    "build_holds_blueprint",
    "build_jobs_blueprint",
    "build_messages_blueprint",
    "build_notifications_blueprint",
    "build_protocols_blueprint",
    "build_simulations_blueprint",
    "build_system_blueprint",
    "build_telegram_blueprint",
    "build_users_blueprint",
    "failed_step_agent_name",
    "job_status",
    "protocol_from_body",
    "protocol_to_dict",
    "steps_completed",
    "utc_now_storage",
    "work_concurrency_keys",
    "_failed_step_agent_name",
    "_now",
    "_steps_completed",
    "_work_concurrency_keys",
]
