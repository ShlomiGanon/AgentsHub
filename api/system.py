"""GET /SYSTEM and PUT /SYSTEM (work_plan.md §7.7, §7.8).

`GET /SYSTEM` reports the profile hash pending-restart check by
recomputing `profiles.loader.hash_profile_file` against the file on disk
right now and comparing it to the hash `LoadedProfile` captured at load
time — the one function both moments call (see that module's own note),
so the two hashes can never be computed two different ways.

`PUT /SYSTEM` accepts only the three live settings (§1.7) and rejects
every other field by name rather than silently ignoring it — a silent
ignore here would look exactly like a successful change. Each accepted
value is written to the settings store before this response is sent, so
a confirmation can never outlive the change it confirms.
"""

from typing import TYPE_CHECKING

from flask import Blueprint, jsonify, request

from api.auth import authenticate, require
from api.errors import InvalidInputError
from profiles.loader import hash_profile_file

if TYPE_CHECKING:
    from api.app import ApiContext

_SETTINGS_FIELDS = {"retry_count", "risk_threshold", "lookback_window_days"}


def build_system_blueprint(ctx: "ApiContext") -> Blueprint:
    blueprint = Blueprint("system", __name__)

    @blueprint.route("/SYSTEM", methods=["GET"])
    def get_system():
        level = authenticate(ctx.deps.persistence, request.headers.get("X-Identity"))
        require(level, "view_history")

        loaded = ctx.loaded_profile
        current_hash = hash_profile_file(loaded.module_path)

        return jsonify({
            "profile": loaded.module_path,
            "agents": [agent.name for agent in ctx.deps.registry.all()],
            "protocols": [{"name": p.name, "approval_flag": p.approval_flag} for p in ctx.deps.protocol_set.all()],
            "event_types": list(ctx.deps.event_type_registry.types),
            "areas": list(ctx.deps.area_registry.areas),
            "queued_events": ctx.queue.qsize(),
            "held_events": {
                "clarification": len(ctx.deps.persistence.list_held_events("clarification")),
                "approval": len(ctx.deps.persistence.list_held_events("approval")),
            },
            "scheduler": ctx.scheduler.last_run_status(),
            "settings": {
                "retry_count": ctx.deps.settings_store.get_retry_count(),
                "risk_threshold": ctx.deps.settings_store.get_risk_threshold(),
                "lookback_window_days": ctx.deps.settings_store.get_lookback_window_days(),
            },
            "profile_file_changed": current_hash != loaded.profile_file_hash,
        })

    @blueprint.route("/SYSTEM", methods=["PUT"])
    def put_system():
        level = authenticate(ctx.deps.persistence, request.headers.get("X-Identity"))
        require(level, "change_settings")

        body = request.get_json(silent=True) or {}

        unknown = sorted(set(body) - _SETTINGS_FIELDS)
        if unknown:
            field = unknown[0]
            raise InvalidInputError(f"'{field}' belongs to the profile and takes effect only on a restart", field=field)

        if "retry_count" in body:
            value = body["retry_count"]
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise InvalidInputError("'retry_count' must be a non-negative integer", field="retry_count")
            ctx.deps.settings_store.set_retry_count(value)

        if "risk_threshold" in body:
            value = body["risk_threshold"]
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not (0.0 <= value <= 1.0):
                raise InvalidInputError("'risk_threshold' must be a number between 0.0 and 1.0", field="risk_threshold")
            ctx.deps.settings_store.set_risk_threshold(value)

        if "lookback_window_days" in body:
            value = body["lookback_window_days"]
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise InvalidInputError("'lookback_window_days' must be a positive integer", field="lookback_window_days")
            ctx.deps.settings_store.set_lookback_window_days(value)

        return jsonify({
            "retry_count": ctx.deps.settings_store.get_retry_count(),
            "risk_threshold": ctx.deps.settings_store.get_risk_threshold(),
            "lookback_window_days": ctx.deps.settings_store.get_lookback_window_days(),
        })

    return blueprint
