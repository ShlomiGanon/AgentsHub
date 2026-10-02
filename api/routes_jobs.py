"""GET /Job/<event_id> — public job status, optionally long-polled."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from flask import Blueprint, jsonify, request

from api._route_deps import job_status
from api.request_boundary import InvalidInputError, NotFoundError, authenticate, require
from auth.permissions import PermissionLevel, RequestedOperation

if TYPE_CHECKING:
    from api.app import ApiContext

def build_jobs_blueprint(ctx: "ApiContext") -> Blueprint:
    """JSON routes that report one event's job status."""

    blueprint = Blueprint("jobs", __name__)
    messages = ctx.loaded_profile.message_catalog

    @blueprint.route("/Job/<event_id>", methods=["GET"])
    def get_job(event_id):
        caller_identity = request.headers.get("X-Identity")
        level = authenticate(ctx.deps.persistence, caller_identity)
        require(level, RequestedOperation.VIEW_JOB_STATUS)

        # Ownership scoping (docs/Next_Plan.md §5 decision record): a viewer may
        # only check the status of an event they themselves submitted. A 404
        # (not 403) is returned for someone else's job, matching the "no such
        # job" response for a genuinely unknown ID — it does not confirm that a
        # job belonging to another sender exists. A commander is unrestricted.
        if level is PermissionLevel.VIEWER:
            event = ctx.deps.persistence.fetch_event(event_id)
            if event is None or event.get("sender_identity") != caller_identity:
                raise NotFoundError(messages.text("api.job_not_found", task_id=event_id))

        status = job_status(ctx, event_id)
        if status is None:
            raise NotFoundError(messages.text("api.job_not_found", task_id=event_id))

        try:
            wait_seconds = int(request.args.get("wait_seconds", "0"))
        except (TypeError, ValueError) as exc:
            raise InvalidInputError(messages.text("api.wait_invalid"), field="wait_seconds") from exc
        if not 0 <= wait_seconds <= 30:
            raise InvalidInputError(messages.text("api.wait_invalid"), field="wait_seconds")

        waiter = getattr(ctx.deps.persistence, "wait_for_notifications_since", None)
        if wait_seconds and waiter is not None and status.get("status") in {"queued", "running"}:
            deadline = time.monotonic() + wait_seconds
            existing = ctx.deps.persistence.fetch_notifications_since(0)
            since = existing[-1]["sequence_id"] if existing else 0
            while status.get("status") in {"queued", "running"}:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                waiter(since, remaining)
                status = job_status(ctx, event_id)
                if status is None:
                    raise NotFoundError(messages.text("api.job_not_found", task_id=event_id))
                rows = ctx.deps.persistence.fetch_notifications_since(since)
                if rows:
                    since = rows[-1]["sequence_id"]

        return jsonify(status)

    return blueprint
