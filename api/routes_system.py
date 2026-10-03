"""SYSTEM overview, settings writes, and live Trace polling."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from flask import Blueprint, jsonify, request

from api._route_deps import protocol_to_dict
from api.request_boundary import InvalidInputError, NotFoundError, authenticate, require
from auth.permissions import RequestedOperation, is_permitted
from config import environment as base_config
from profiles.loader import hash_profile_file
from tools import get_trace_id, is_valid_trace_id, render_deep_debug_entry

if TYPE_CHECKING:
    from api.app import ApiContext

logger = logging.getLogger(__name__)

_SETTINGS_FIELDS = {
    "retry_count", "risk_threshold", "lookback_window_days", "safe_mode", "rich_reports_enabled",
    "hold_reminder_minutes", "hold_escalation_minutes", "hold_expiry_hours",
}

def build_system_blueprint(ctx: "ApiContext") -> Blueprint:
    """JSON routes for profile overview, settings, and live traces."""

    blueprint = Blueprint("system", __name__)
    messages = ctx.loaded_profile.message_catalog

    @blueprint.route("/SYSTEM", methods=["GET"])
    def get_system():
        """Return profile overview, internals, and settings the caller may see."""

        level = authenticate(ctx.deps.persistence, request.headers.get("X-Identity"))
        # VIEW_PROFILE_OVERVIEW is the least-privileged of the three operations this
        # single endpoint now serves — it is the entry gate. The response payload
        # below is then built field-group by field-group, each gated by its own
        # operation, so a viewer's response is a strict subset (protected arrays
        # absent, never present-but-empty) rather than the full commander payload
        # with fields simply omitted after the fact.
        require(level, RequestedOperation.VIEW_PROFILE_OVERVIEW)

        loaded = ctx.loaded_profile
        current_hash = hash_profile_file(loaded.module_path)

        response_payload = {
            "profile": loaded.module_path,
            "event_types": list(ctx.deps.event_type_registry.types),
            "areas": list(ctx.deps.area_registry.areas),
            "profile_file_changed": current_hash != loaded.profile_file_hash,
        }

        if is_permitted(level, RequestedOperation.VIEW_SYSTEM_INTERNALS):
            response_payload["agents"] = [agent.name for agent in ctx.deps.registry.all()]
            response_payload["protocols"] = [protocol_to_dict(p) for p in ctx.deps.protocol_set.all()]
            response_payload["queued_events"] = ctx.queue.qsize()
            response_payload["held_events"] = {
                "clarification": len(ctx.deps.persistence.list_held_events("clarification")),
                "approval": len(ctx.deps.persistence.list_held_events("approval")),
            }
            response_payload["scheduler"] = ctx.scheduler.last_run_status()
            if ctx.hold_sweep_scheduler is not None:
                response_payload["hold_sweep_scheduler"] = ctx.hold_sweep_scheduler.last_run_status()

        if is_permitted(level, RequestedOperation.VIEW_SETTINGS):
            response_payload["settings"] = {
                "retry_count": ctx.deps.settings_store.get_retry_count(),
                "risk_threshold": ctx.deps.settings_store.get_risk_threshold(),
                "lookback_window_days": ctx.deps.settings_store.get_lookback_window_days(),
                "safe_mode": ctx.deps.settings_store.get_safe_mode(),
                "rich_reports_enabled": ctx.deps.settings_store.get_rich_reports_enabled(),
                "hold_reminder_minutes": ctx.deps.settings_store.get_hold_reminder_minutes(),
                "hold_escalation_minutes": ctx.deps.settings_store.get_hold_escalation_minutes(),
                "hold_expiry_hours": ctx.deps.settings_store.get_hold_expiry_hours(),
            }

        return jsonify(response_payload)

    @blueprint.route("/SYSTEM", methods=["PUT"])
    def put_system():
        """Write live settings that do not require a process restart."""

        level = authenticate(ctx.deps.persistence, request.headers.get("X-Identity"))
        require(level, RequestedOperation.CHANGE_SETTINGS)

        request_payload = request.get_json(silent=True)
        if request_payload is None:
            request_payload = {}
        if not isinstance(request_payload, dict):
            raise InvalidInputError(messages.text("api.telegram_admission_invalid"))

        unknown = sorted(set(request_payload) - _SETTINGS_FIELDS)
        if unknown:
            field = unknown[0]
            raise InvalidInputError(messages.text("api.profile_field_restart", field=field), field=field)

        validated: dict[str, object] = {}
        if "retry_count" in request_payload:
            setting_value = request_payload["retry_count"]
            if not isinstance(setting_value, int) or isinstance(setting_value, bool) or setting_value < 0:
                raise InvalidInputError(messages.text("api.retry_nonnegative_integer"), field="retry_count")
            validated["retry_count"] = setting_value

        if "risk_threshold" in request_payload:
            setting_value = request_payload["risk_threshold"]
            if not isinstance(setting_value, (int, float)) or isinstance(setting_value, bool) or not (0.0 <= setting_value <= 1.0):
                raise InvalidInputError(messages.text("api.risk_threshold_range"), field="risk_threshold")
            validated["risk_threshold"] = setting_value

        if "lookback_window_days" in request_payload:
            setting_value = request_payload["lookback_window_days"]
            if not isinstance(setting_value, int) or isinstance(setting_value, bool) or setting_value < 1:
                raise InvalidInputError(messages.text("api.lookback_positive_integer"), field="lookback_window_days")
            validated["lookback_window_days"] = setting_value

        if "safe_mode" in request_payload:
            setting_value = request_payload["safe_mode"]
            if not isinstance(setting_value, bool):
                raise InvalidInputError(messages.text("api.safe_mode_boolean"), field="safe_mode")
            validated["safe_mode"] = setting_value

        if "rich_reports_enabled" in request_payload:
            setting_value = request_payload["rich_reports_enabled"]
            if not isinstance(setting_value, bool):
                raise InvalidInputError(messages.text("api.rich_reports_enabled_boolean"), field="rich_reports_enabled")
            validated["rich_reports_enabled"] = setting_value

        if "hold_reminder_minutes" in request_payload:
            setting_value = request_payload["hold_reminder_minutes"]
            if not isinstance(setting_value, (int, float)) or isinstance(setting_value, bool) or setting_value <= 0:
                raise InvalidInputError(messages.text("api.hold_reminder_minutes_positive"), field="hold_reminder_minutes")
            validated["hold_reminder_minutes"] = setting_value

        if "hold_escalation_minutes" in request_payload:
            setting_value = request_payload["hold_escalation_minutes"]
            if not isinstance(setting_value, (int, float)) or isinstance(setting_value, bool) or setting_value <= 0:
                raise InvalidInputError(messages.text("api.hold_escalation_minutes_positive"), field="hold_escalation_minutes")
            validated["hold_escalation_minutes"] = setting_value

        if "hold_expiry_hours" in request_payload:
            setting_value = request_payload["hold_expiry_hours"]
            if not isinstance(setting_value, (int, float)) or isinstance(setting_value, bool) or setting_value <= 0:
                raise InvalidInputError(messages.text("api.hold_expiry_hours_positive"), field="hold_expiry_hours")
            validated["hold_expiry_hours"] = setting_value

        previous_safe_mode = ctx.deps.settings_store.get_safe_mode()
        if "retry_count" in validated:
            ctx.deps.settings_store.set_retry_count(validated["retry_count"])
        if "risk_threshold" in validated:
            ctx.deps.settings_store.set_risk_threshold(validated["risk_threshold"])
        if "lookback_window_days" in validated:
            ctx.deps.settings_store.set_lookback_window_days(validated["lookback_window_days"])
        if "safe_mode" in validated:
            ctx.deps.settings_store.set_safe_mode(validated["safe_mode"])
            logger.info(
                "safe mode changed",
                extra={
                    "event": "safe_mode_changed",
                    "previous_safe_mode": previous_safe_mode,
                    "safe_mode": validated["safe_mode"],
                    "changed_by": request.headers.get("X-Identity"),
                    "trace_id": get_trace_id(),
                },
            )
        if "rich_reports_enabled" in validated:
            ctx.deps.settings_store.set_rich_reports_enabled(validated["rich_reports_enabled"])
        if "hold_reminder_minutes" in validated:
            ctx.deps.settings_store.set_hold_reminder_minutes(validated["hold_reminder_minutes"])
        if "hold_escalation_minutes" in validated:
            ctx.deps.settings_store.set_hold_escalation_minutes(validated["hold_escalation_minutes"])
        if "hold_expiry_hours" in validated:
            ctx.deps.settings_store.set_hold_expiry_hours(validated["hold_expiry_hours"])

        return jsonify({
            "retry_count": ctx.deps.settings_store.get_retry_count(),
            "risk_threshold": ctx.deps.settings_store.get_risk_threshold(),
            "lookback_window_days": ctx.deps.settings_store.get_lookback_window_days(),
            "safe_mode": ctx.deps.settings_store.get_safe_mode(),
            "rich_reports_enabled": ctx.deps.settings_store.get_rich_reports_enabled(),
        })

    @blueprint.route("/Trace/<trace_id>", methods=["GET"])
    def get_trace(trace_id: str):
        """Poll deep-debug log entries for one live trace id."""

        level = authenticate(ctx.deps.persistence, request.headers.get("X-Identity"))
        require(level, RequestedOperation.VIEW_LIVE_TRACE)
        if not base_config.DEEP_DEBUG:
            raise NotFoundError(messages.text("api.deep_debug_disabled"))
        if not is_valid_trace_id(trace_id):
            raise InvalidInputError(messages.text("api.trace_id_invalid"), field="trace_id")

        try:
            since = int(request.args.get("since", "0"))
        except (TypeError, ValueError) as exc:
            raise InvalidInputError(messages.text("api.cursor_invalid"), field="since") from exc
        if since < 0:
            raise InvalidInputError(messages.text("api.cursor_invalid"), field="since")

        try:
            wait_seconds = int(request.args.get("wait_seconds", "0"))
        except (TypeError, ValueError) as exc:
            raise InvalidInputError(messages.text("api.wait_invalid"), field="wait_seconds") from exc
        if not 0 <= wait_seconds <= 30:
            raise InvalidInputError(messages.text("api.wait_invalid"), field="wait_seconds")

        entries = ctx.deps.persistence.wait_for_log_entries_since(
            trace_id,
            since,
            wait_seconds,
        )
        rendered = []
        for entry in entries:
            rendered_text = render_deep_debug_entry(entry, messages)
            if rendered_text is not None:
                rendered.append({"id": entry["id"], "text": rendered_text})

        next_cursor = max((entry["id"] for entry in entries), default=since)
        terminal = any(entry.get("event") == "event_outcome" for entry in entries)
        return jsonify({
            "entries": rendered,
            "next_cursor": next_cursor,
            "terminal": terminal,
        })

    return blueprint
