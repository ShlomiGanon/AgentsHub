"""Admin scenario-simulator page and HTTP proxy to the simulation-mode bot."""

from __future__ import annotations

import logging
import os

import httpx
from flask import Blueprint, jsonify, request, session

from api.admin_chrome import _SIMULATOR_TEMPLATE
from api.admin_simulator import simulator_page_context
from api.admin_support import AdminPanel, catalog_text
from api.request_boundary import BOT_SERVICE_IDENTITY, BOT_SERVICE_KEY_ENV_VAR, SERVICE_KEY_HEADER
from messages import get_current_catalog
from tools import get_trace_id

logger = logging.getLogger(__name__)


def register_simulator_routes(blueprint: Blueprint, panel: AdminPanel) -> None:
    """Mount the simulator page and bot-process proxy routes."""

    ctx = panel.ctx

    def _forward_to_simulator(method: str, path: str, **kwargs):
        """HTTP call to the simulation-mode bot; returns (flask_response, status_code)."""

        simulator_port = ctx.loaded_profile.simulator_port
        if not simulator_port:
            return jsonify({"error": {"message": catalog_text("admin.simulator.bot_mode_unconfigured")}}), 501

        service_key = os.environ.get(BOT_SERVICE_KEY_ENV_VAR) or ""
        forward_headers = {SERVICE_KEY_HEADER: service_key}
        fwd_trace = request.headers.get("X-Trace-ID") or (kwargs.get("json", {}).get("trace_id") if isinstance(kwargs.get("json"), dict) else None)
        if fwd_trace:
            forward_headers["X-Trace-ID"] = str(fwd_trace)
        try:
            response = httpx.request(
                method,
                f"http://localhost:{simulator_port}{path}",
                headers=forward_headers,
                timeout=httpx.Timeout(connect=2.0, pool=2.0, write=5.0, read=75.0),
                **kwargs,
            )
        except httpx.HTTPError:
            logger.warning(
                "simulation-mode bot process unreachable",
                extra={"event": "admin_simulator_bot_unreachable", "trace_id": get_trace_id()},
            )
            return jsonify({"error": {"message": catalog_text("admin.simulator.bot_mode_unreachable")}}), 502

        try:
            body = response.json()
        except ValueError:
            body = {}
        return jsonify(body), response.status_code

    @blueprint.route("/simulator", methods=["GET"])
    def simulator():
        """Serve the scenario simulator page; steps then go through /Msg and /Event."""

        redirect_response = panel.require_session()
        if redirect_response is not None:
            return redirect_response

        api_context = panel.api_page_context("simulator")
        return panel.render(
            _SIMULATOR_TEMPLATE,
            page_data=simulator_page_context(
                ctx, get_current_catalog(), BOT_SERVICE_IDENTITY, api_identity=api_context["api_identity"]
            ),
            csrf_token=session["csrf_token"],
            **api_context,
        )

    @blueprint.route("/simulator/bot-msg", methods=["POST"])
    def simulator_bot_msg():
        """Proxy one message-kind simulation step to the simulation-mode bot."""

        redirect_response = panel.require_session()
        if redirect_response is not None:
            return redirect_response

        payload = request.get_json(silent=True) or {}
        return _forward_to_simulator("POST", "/Simulator-msg", json=payload)

    @blueprint.route("/simulator/bot-poll", methods=["GET"])
    def simulator_bot_poll():
        """Proxy a chat-watermark poll to the simulation-mode bot."""

        redirect_response = panel.require_session()
        if redirect_response is not None:
            return redirect_response

        params = {
            "chat_id": request.args.get("chat_id", ""),
            "status_len": request.args.get("status_len", "0"),
            "sent_len": request.args.get("sent_len", "0"),
        }
        return _forward_to_simulator("GET", "/Simulator-msg/poll", params=params)
