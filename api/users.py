"""`GET /User/<identity>` and `GET /Commanders` (work_plan.md §8.13, §8.14).

Both close gaps the Mission 8 deep audit found: `bot.api_client
.BotApiClient.resolve_user`/`.list_commander_chat_ids` had no
corresponding endpoint anywhere in the API Layer. Numbered under §8, not
§7, for the same reason as `api/notifications.py` — see that module's
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
