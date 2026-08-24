"""CRUD /Protocol (work_plan.md §7.6).

Reads serve the currently-loaded `ProtocolSet` directly — nothing to
fetch, matching `protocols.editor`'s own module docstring ("reads
describe what's running, not what's on disk"). Every write goes through
`protocols.editor`, which edits the profile *file* and never the running
system: every write response carries that same fixed message regardless
of which write it was — never a body resembling a successful state
change (§7.6's explicit rule) — and the loaded `ProtocolSet` this process
holds stays untouched until a restart picks up the file.
"""

from typing import TYPE_CHECKING

from flask import Blueprint, jsonify, request

from api.auth import authenticate, require
from api.errors import InvalidInputError
from protocols.editor import ProtocolEditError, add_protocol, remove_protocol, replace_protocol
from protocols.model import CriticalityLevel, Protocol

if TYPE_CHECKING:
    from api.app import ApiContext


def protocol_to_dict(protocol: Protocol) -> dict:
    return {
        "name": protocol.name,
        "description": protocol.description,
        "participating_agents": list(protocol.participating_agents),
        "approved_tools": list(protocol.approved_tools),
        "expected_success_output": protocol.expected_success_output,
        "criticality": protocol.criticality.name.lower(),
        "approval_flag": protocol.approval_flag,
    }


def _protocol_from_body(body: dict, name_override: str | None = None) -> Protocol:
    try:
        return Protocol(
            name=name_override if name_override is not None else body["name"],
            description=body["description"],
            participating_agents=tuple(body["participating_agents"]),
            approved_tools=tuple(body["approved_tools"]),
            expected_success_output=body["expected_success_output"],
            criticality=CriticalityLevel[str(body["criticality"]).upper()],
            approval_flag=body["approval_flag"],
        )
    except KeyError as exc:
        raise InvalidInputError(f"missing required field: {exc.args[0]}", field=str(exc.args[0])) from exc
    except (TypeError, AttributeError) as exc:
        raise InvalidInputError(f"malformed protocol body: {exc}") from exc


def build_protocols_blueprint(ctx: "ApiContext") -> Blueprint:
    blueprint = Blueprint("protocols", __name__)

    def _agents_by_name() -> dict:
        return {agent.name: agent for agent in ctx.deps.registry.all()}

    @blueprint.route("/Protocol", methods=["GET"])
    def list_protocols():
        level = authenticate(ctx.deps.persistence, request.headers.get("X-Identity"))
        require(level, "view_history")

        return jsonify({"protocols": [protocol_to_dict(p) for p in ctx.deps.protocol_set.all()]})

    @blueprint.route("/Protocol", methods=["POST"])
    def create_protocol():
        level = authenticate(ctx.deps.persistence, request.headers.get("X-Identity"))
        require(level, "edit_profile")

        body = request.get_json(silent=True) or {}
        new_protocol = _protocol_from_body(body)

        try:
            result = add_protocol(ctx.loaded_profile.module_path, ctx.deps.protocol_set.all(), _agents_by_name(), new_protocol)
        except ProtocolEditError as exc:
            raise InvalidInputError(str(exc)) from exc

        return jsonify({"message": result.message})

    @blueprint.route("/Protocol/<name>", methods=["PUT"])
    def update_protocol(name):
        level = authenticate(ctx.deps.persistence, request.headers.get("X-Identity"))
        require(level, "edit_profile")

        body = request.get_json(silent=True) or {}
        updated_protocol = _protocol_from_body(body, name_override=name)

        try:
            result = replace_protocol(ctx.loaded_profile.module_path, ctx.deps.protocol_set.all(), _agents_by_name(), updated_protocol)
        except ProtocolEditError as exc:
            raise InvalidInputError(str(exc)) from exc

        return jsonify({"message": result.message})

    @blueprint.route("/Protocol/<name>", methods=["DELETE"])
    def delete_protocol(name):
        level = authenticate(ctx.deps.persistence, request.headers.get("X-Identity"))
        require(level, "edit_profile")

        try:
            result = remove_protocol(ctx.loaded_profile.module_path, ctx.deps.protocol_set.all(), name)
        except ProtocolEditError as exc:
            raise InvalidInputError(str(exc)) from exc

        return jsonify({"message": result.message})

    return blueprint
