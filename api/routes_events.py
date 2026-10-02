"""POST /Event — sensor-style report ingestion."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import time
from typing import TYPE_CHECKING

from flask import Blueprint, jsonify, request

from api._route_deps import work_concurrency_keys, utc_now_storage
from api.request_boundary import AuthorizationError, InvalidInputError, ServiceUnavailableError, authenticate, require
from auth.permissions import RequestedOperation
from history import parse_timestamp, storage_timestamp
from orchestrator.flows import WorkItem, begin_report, run_report_extraction
from profiles import OptimizationPolicy
from tools import get_trace_id, new_trace_id, set_trace_id, trace_context

if TYPE_CHECKING:
    from api.app import ApiContext

def build_events_blueprint(ctx: "ApiContext") -> Blueprint:
    """JSON routes that accept a new sensor-style Event."""

    blueprint = Blueprint("events", __name__)
    messages = ctx.loaded_profile.message_catalog

    @blueprint.route("/Event", methods=["POST"])
    def post_event():
        optimization_policy = getattr(ctx.loaded_profile, "optimization_policy", OptimizationPolicy())
        caller_identity = request.headers.get("X-Identity")
        level = authenticate(ctx.deps.persistence, caller_identity)
        require(level, RequestedOperation.SUBMIT_EVENT)

        request_payload = request.get_json(silent=True) or {}
        text = request_payload.get("text")
        sender_identity = request_payload.get("sender_identity")

        if not text:
            raise InvalidInputError(messages.text("api.field_required", field="text"), field="text")
        if not sender_identity:
            raise InvalidInputError(
                messages.text("api.field_required", field="sender_identity"), field="sender_identity"
            )
        if sender_identity != caller_identity:
            raise AuthorizationError(messages.text("api.sender_identity_mismatch"))
        reservation = ctx.queue.reserve(False)
        if reservation is None:
            raise ServiceUnavailableError(messages.text("api.queue_full"))

        received_at = utc_now_storage()
        raw_timestamp = request_payload.get("timestamp")
        if raw_timestamp is not None and raw_timestamp != "":
            if not isinstance(raw_timestamp, str):
                raise InvalidInputError(
                    messages.text("api.invalid_timestamp", field="timestamp"), field="timestamp"
                )
            try:
                received_at = storage_timestamp(parse_timestamp(raw_timestamp))
            except (ValueError, TypeError):
                raise InvalidInputError(
                    messages.text("api.invalid_timestamp", field="timestamp"), field="timestamp"
                )

        trace_id = get_trace_id() or new_trace_id()
        set_trace_id(trace_id)
        deadline_at = storage_timestamp(datetime.now(timezone.utc) + timedelta(seconds=optimization_policy.job_deadline_seconds))
        try:
            event_id = begin_report(
                ctx.deps,
                text,
                "sensor",
                received_at,
                sender_identity,
                deadline_at=deadline_at,
                sender_permission_level=level.name.lower(),
            )
        except Exception:
            ctx.queue.release_reservation(reservation)
            raise

        def _work() -> None:
            with trace_context(trace_id):
                run_report_extraction(ctx.deps, event_id, ctx.main_agent, ctx.insights_agent)

        ctx.queue.submit(
            WorkItem(
                (event_id, _work), trace_id=trace_id,
                deadline_monotonic=time.monotonic() + optimization_policy.job_deadline_seconds,
                concurrency_keys=work_concurrency_keys(sender_identity, "surveillance_agent"),
            ),
            reservation,
        )

        return jsonify({"event_id": event_id, "status": "queued", "trace_id": trace_id}), 202

    return blueprint
