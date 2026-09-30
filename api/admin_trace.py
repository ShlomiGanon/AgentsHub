"""Admin routes for the Behind-the-Scenes simulator trace panel."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Callable

from flask import Blueprint, jsonify, request

from tools import aggregate_trace_data, is_valid_trace_id

if TYPE_CHECKING:
    from api.app import ApiContext

logger = logging.getLogger(__name__)


def register_trace_routes(
    blueprint: Blueprint,
    ctx: "ApiContext",
    require_session: Callable[[], Any],
) -> None:
    """Register simulator trace diagnostic endpoints on the admin blueprint."""

    @blueprint.route("/simulator/trace/<trace_id>", methods=["GET"])
    def simulator_trace(trace_id: str):
        redirect_response = require_session()
        if redirect_response is not None:
            return jsonify({"error": {"message": "session expired"}}), 401
        if not is_valid_trace_id(trace_id):
            return jsonify({"error": {"message": "invalid trace_id"}}), 400

        try:
            since = max(0, int(request.args.get("since", "0")))
        except (ValueError, TypeError):
            since = 0

        try:
            wait_seconds = max(0.0, min(float(request.args.get("wait_seconds", "0")), 15.0))
        except (ValueError, TypeError):
            wait_seconds = 0.0

        raw_entries = ctx.deps.persistence.wait_for_log_entries_since(trace_id, since, wait_seconds)
        next_cursor = max((entry["id"] for entry in raw_entries), default=since)

        payload = aggregate_trace_data(
            raw_entries,
            profile_name=ctx.loaded_profile.module_path,
            trace_id=trace_id,
        )
        payload["next_cursor"] = next_cursor
        return jsonify(payload)

    @blueprint.route("/simulator/traces/recent", methods=["GET"])
    def simulator_recent_traces():
        redirect_response = require_session()
        if redirect_response is not None:
            return jsonify({"error": {"message": "session expired"}}), 401

        try:
            limit = max(1, min(int(request.args.get("limit", "15")), 30))
        except (ValueError, TypeError):
            limit = 15

        recent = []
        connection = ctx.deps.persistence._read_connection()
        try:
            rows = connection.execute(
                "SELECT event_id, trace_id, received_at, sender_identity, raw_text, classification, outcome, source "
                "FROM events WHERE trace_id IS NOT NULL AND trace_id != '' "
                "ORDER BY received_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
            for row in rows:
                raw_txt = str(row["raw_text"] or "")
                outcome = row["outcome"] or ""
                recent.append({
                    "event_id": row["event_id"],
                    "trace_id": row["trace_id"],
                    "received_at": row["received_at"],
                    "sender_identity": row["sender_identity"],
                    "text": raw_txt[:80] + ("…" if len(raw_txt) > 80 else ""),
                    "classification": row["classification"] or "—",
                    "status": outcome if outcome else "running",
                    "source": row["source"] or "unknown",
                })
        except Exception as exc:
            logger.warning("could not read recent traces: %s", exc)
        finally:
            connection.close()

        terminal_set = {"succeeded", "failed", "uncertain", "closed_on_precedent", "declined"}
        active_count = sum(1 for r in recent if r["status"] not in terminal_set)

        return jsonify({"items": recent, "active_count": active_count})

    @blueprint.route("/simulator/behind-the-scenes", methods=["GET"])
    def simulator_behind_the_scenes():
        redirect_response = require_session()
        if redirect_response is not None:
            return redirect_response
        trace_id = request.args.get("trace_id", "")
        from api.admin_bts_page import render_behind_the_scenes_html
        return render_behind_the_scenes_html(
            trace_id=trace_id,
            profile_name=ctx.loaded_profile.module_path,
        )
