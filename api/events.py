"""POST /Event (work_plan.md §7.3).

Sensor ingestion. Sets the occurrence timestamp equal to the receipt
timestamp by recording `source="sensor"` — `history.extraction
.extract_event`'s own `source == "sensor"` branch (Mission 5) already
sets `occurred_at` to `received_at` and never asks the model to extract
one; this endpoint doesn't re-implement that, only triggers it. This is
half of §7.5's unified ingestion — see `api/messages.py` for the other
half and `tests/test_api_unified_ingestion.py` for the convergence proof.
"""

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from flask import Blueprint, jsonify, request

from api.auth import authenticate, require
from api.errors import InvalidInputError
from orchestrator.flows import begin_report, run_report_extraction

if TYPE_CHECKING:
    from api.app import ApiContext


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_events_blueprint(ctx: "ApiContext") -> Blueprint:
    blueprint = Blueprint("events", __name__)

    @blueprint.route("/Event", methods=["POST"])
    def post_event():
        level = authenticate(ctx.deps.persistence, request.headers.get("X-Identity"))
        require(level, "send_message")

        body = request.get_json(silent=True) or {}
        text = body.get("text")
        sender_identity = body.get("sender_identity")

        if not text:
            raise InvalidInputError("'text' is required", field="text")
        if not sender_identity:
            raise InvalidInputError("'sender_identity' is required", field="sender_identity")

        event_id = begin_report(ctx.deps, text, "sensor", _now(), sender_identity)
        ctx.queue.submit((event_id, lambda: run_report_extraction(ctx.deps, event_id, ctx.main_agent, ctx.insights_agent)))

        return jsonify({"event_id": event_id, "status": "queued"}), 202

    return blueprint
