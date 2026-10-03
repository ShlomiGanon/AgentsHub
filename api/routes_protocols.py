"""CRUD routes for profile protocols."""

from __future__ import annotations

from typing import TYPE_CHECKING

from flask import Blueprint, jsonify, request

from api._route_deps import protocol_from_body, protocol_to_dict
from api.request_boundary import InvalidInputError, authenticate, require
from auth.permissions import RequestedOperation
from protocols import ProtocolEditError, add_protocol, remove_protocol, replace_protocol

if TYPE_CHECKING:
    from api.app import ApiContext

def build_protocols_blueprint(ctx: "ApiContext") -> Blueprint:
    """JSON routes that list and edit the loaded profile's protocols."""

    blueprint = Blueprint("protocols", __name__)

    def _agents_by_name() -> dict:
        """Index registered agents by name for protocol edit validation."""

        return {agent.name: agent for agent in ctx.deps.registry.all()}

    @blueprint.route("/Protocol", methods=["GET"])
    def list_protocols():
        """Return every protocol declared on the loaded profile."""

        level = authenticate(ctx.deps.persistence, request.headers.get("X-Identity"))
        require(level, RequestedOperation.LIST_PROTOCOLS)

        return jsonify({"protocols": [protocol_to_dict(p) for p in ctx.deps.protocol_set.all()]})

    @blueprint.route("/Protocol", methods=["POST"])
    def create_protocol():
        """Add one protocol to the loaded profile."""

        level = authenticate(ctx.deps.persistence, request.headers.get("X-Identity"))
        require(level, RequestedOperation.CREATE_PROTOCOL)

        request_payload = request.get_json(silent=True) or {}
        new_protocol = protocol_from_body(request_payload)

        try:
            protocol_edit_message = add_protocol(ctx.loaded_profile.module_path, ctx.deps.protocol_set.all(), _agents_by_name(), new_protocol)
        except ProtocolEditError as exc:
            raise InvalidInputError(str(exc)) from exc

        return jsonify({"message": protocol_edit_message})

    @blueprint.route("/Protocol/<name>", methods=["PUT"])
    def update_protocol(name):
        """Replace one named protocol on the loaded profile."""

        level = authenticate(ctx.deps.persistence, request.headers.get("X-Identity"))
        require(level, RequestedOperation.UPDATE_PROTOCOL)

        request_payload = request.get_json(silent=True) or {}
        updated_protocol = protocol_from_body(request_payload, name_override=name)

        try:
            protocol_edit_message = replace_protocol(ctx.loaded_profile.module_path, ctx.deps.protocol_set.all(), _agents_by_name(), updated_protocol)
        except ProtocolEditError as exc:
            raise InvalidInputError(str(exc)) from exc

        return jsonify({"message": protocol_edit_message})

    @blueprint.route("/Protocol/<name>", methods=["DELETE"])
    def delete_protocol(name):
        """Remove one named protocol from the loaded profile."""

        level = authenticate(ctx.deps.persistence, request.headers.get("X-Identity"))
        require(level, RequestedOperation.DELETE_PROTOCOL)

        try:
            protocol_edit_message = remove_protocol(ctx.loaded_profile.module_path, ctx.deps.protocol_set.all(), name)
        except ProtocolEditError as exc:
            raise InvalidInputError(str(exc)) from exc

        return jsonify({"message": protocol_edit_message})

    return blueprint
