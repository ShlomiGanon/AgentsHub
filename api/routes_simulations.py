"""Discovery and materialization of a profile's declared simulations."""

from __future__ import annotations

from typing import TYPE_CHECKING

from flask import Blueprint, jsonify, request

from api.request_boundary import NotFoundError, authenticate, require
from api.simulations import find_simulation_scenario, materialize_simulation, simulation_catalog_payload
from auth.permissions import RequestedOperation

if TYPE_CHECKING:
    from api.app import ApiContext

def build_simulations_blueprint(ctx: "ApiContext") -> Blueprint:
    """Discovery and materialization for a profile's declared simulations
    (docs/profile_simulations_design.md) — the server-side JSON adapter the admin
    panel's simulator page queries instead of maintaining its own knowledge of
    simulation user/group IDs. `GET /Simulations/<key>` returns the exact
    existing admin-simulator scenario JSON shape, ready to feed straight into
    the same `loadScenario()` a manually pasted/uploaded scenario already uses —
    execution from there goes through `POST /Msg`/`POST /Event` exactly as today,
    unchanged."""

    blueprint = Blueprint("simulations", __name__)
    messages = ctx.loaded_profile.message_catalog

    @blueprint.route("/Simulations", methods=["GET"])
    def list_simulations():
        level = authenticate(ctx.deps.persistence, request.headers.get("X-Identity"))
        require(level, RequestedOperation.VIEW_SIMULATIONS)
        return jsonify({"simulations": simulation_catalog_payload(ctx.loaded_profile)})

    @blueprint.route("/Simulations/<key>", methods=["GET"])
    def get_simulation(key):
        level = authenticate(ctx.deps.persistence, request.headers.get("X-Identity"))
        require(level, RequestedOperation.VIEW_SIMULATIONS)
        scenario = find_simulation_scenario(ctx.loaded_profile, key)
        if scenario is None:
            raise NotFoundError(messages.text("api.simulation_not_found", simulation_key=key))
        return jsonify(
            materialize_simulation(scenario, ctx.loaded_profile.simulation_users, ctx.loaded_profile.simulation_groups)
        )

    return blueprint

    return blueprint
