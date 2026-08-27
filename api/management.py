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

        return jsonify({"message": result})

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

        return jsonify({"message": result})

    @blueprint.route("/Protocol/<name>", methods=["DELETE"])
    def delete_protocol(name):
        level = authenticate(ctx.deps.persistence, request.headers.get("X-Identity"))
        require(level, "edit_profile")

        try:
            result = remove_protocol(ctx.loaded_profile.module_path, ctx.deps.protocol_set.all(), name)
        except ProtocolEditError as exc:
            raise InvalidInputError(str(exc)) from exc

        return jsonify({"message": result})

    return blueprint

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

§7.12: `GET /SYSTEM`'s protocol summary reuses `api.management
.protocol_to_dict` (the same rendering `GET /Protocol` uses) rather than
a separate, narrower `{name, approval_flag}` shape — found in the Mission
8 deep audit that `bot.api_client.ProfileView`/`ProtocolView` need
`description`/`criticality` too, which the old narrower shape omitted.
One coherent surface for both callers, not two shapes to keep in sync.
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
            "protocols": [protocol_to_dict(p) for p in ctx.deps.protocol_set.all()],
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

"""`GET /User/<identity>` and `GET /Commanders` (work_plan.md §8.13, §8.14).

Both close gaps the Mission 8 deep audit found: `bot.api_client
.BotApiClient.resolve_user`/`.list_commander_chat_ids` had no
corresponding endpoint anywhere in the API Layer. Numbered under §8, not
§7, for the same reason as `api/operations.py` — see that module's
docstring.

`GET /User/<identity>` is deliberately low-privilege (§7.9's shared check
at `view_history`'s VIEWER minimum): reporting whether *some other*
identity is registered, and at what level, is the same shape of
information `resolve_user`'s own docstring in `bot/api_client.py` already
describes as needing only "the same restraint every other endpoint
already exercises" — not elevated privilege.

`GET /Commanders` is COMMANDER-level, unlike most reads in this system:
it returns the full commander roster, the same shape of information
§8.2's "no command that reports the user list" refuses to expose to a
bot user directly. The only real caller is the bot's own service
identity (`docs/allowed_calls.md`: "bot calls only api"), itself
provisioned at commander level — see `docs/api_spec.md`'s
"Service identity" section.
"""

from typing import TYPE_CHECKING

from flask import Blueprint, jsonify, request

from api.auth import authenticate, require

if TYPE_CHECKING:
    from api.app import ApiContext


def build_users_blueprint(ctx: "ApiContext") -> Blueprint:
    blueprint = Blueprint("users", __name__)

    @blueprint.route("/User/<identity>", methods=["GET"])
    def get_user(identity):
        level = authenticate(ctx.deps.persistence, request.headers.get("X-Identity"))
        require(level, "view_history")

        user = ctx.deps.persistence.read_user(identity)
        if user is None:
            return jsonify({"registered": False, "permission_level": None})

        return jsonify({"registered": True, "permission_level": user["permission_level"]})

    @blueprint.route("/Commanders", methods=["GET"])
    def get_commanders():
        level = authenticate(ctx.deps.persistence, request.headers.get("X-Identity"))
        require(level, "view_commander_roster")

        commanders = [u for u in ctx.deps.persistence.list_users() if u["permission_level"] == "commander"]
        return jsonify({"commanders": [{"telegram_identity": u["telegram_identity"]} for u in commanders]})

    return blueprint



