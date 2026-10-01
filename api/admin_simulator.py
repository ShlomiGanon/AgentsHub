"""Scenario simulator page for the admin panel (`api/admin.py` mounts it at `/admin/simulator`).

The page loads a scenario JSON (file, drag-and-drop, or paste), draws one card per chat or
sensor the scenario declares, and lets the operator release the steps one at a time, in order.

`kind: "event"` (sensor) steps are sent by the **browser** directly to the real `POST /Event`
on the same origin, under that step's own `X-Identity` — a sensor has no Telegram identity and
was never bot/Telegram traffic to begin with (docs/bot_simulation_mode_design.md §2 decision 3),
so this path is unchanged. `kind: "message"` (persona chat) steps instead go through
`POST /admin/simulator/bot-msg` (`api/admin.py`) — a same-origin proxy to a dedicated
simulation-mode bot process (`bot/simulator_app.py`) that feeds the step through the real bot's
own handler/dispatch code (`bot/app.py`, unmodified), so the same registration, permission,
group-scoping *and* real bot behavior (its own derivation of conversation_id/protocol_hint, its
background loops) apply exactly as they would for a real Telegram message — see
docs/bot_simulation_mode_design.md for the full design. If the loaded profile hasn't declared
`SIMULATOR_PORT`, or the simulator process isn't running, the proxy route reports that clearly
rather than silently falling back to a shortcut.

This module holds only the page's style, body and script as constants plus the helper that
gathers the page's data; the route, session check and shared admin chrome stay in `api/admin.py`
(which assembles the full template) so there is one place that knows how admin pages are served.
All user-visible text comes from the `admin.simulator.*` catalog keys (`messages/en.py`,
`messages/he.py`) — the server-rendered parts through the usual `t()` helper, the script's parts
as raw templates embedded in the page and formatted client-side with the same `{name}` syntax.

Scenario JSON shape (documented in docs/unified_command_guide.md):

    {
      "scenario": {"id": "...", "title": "...", "description": "...", "tags": ["..."]},
      "chats": [
        {"key": "response_team", "kind": "message", "label": "...",
         "telegram_chat_id": "-1001234567890", "telegram_chat_type": "supergroup"},
        {"key": "commander_dm", "kind": "message", "label": "...", "telegram_chat_type": "private"},
        {"key": "fence_sensors", "kind": "event", "label": "..."}
      ],
      "steps": [
        {"step": 1, "chat": "response_team", "sender_identity": "1002003", "sender_name": "...",
         "text": "..."},
        {"step": 2, "chat": "fence_sensors", "sender_identity": "sensor-north-1", "text": "..."},
        {"step": 3, "chat": "commander_dm", "sender_identity": "5551", "text": "...",
         "protocol_hint": "overall_situational_picture", "source_message_id": "4821"}
      ]
    }

`kind: "message"` steps are proxied with `telegram_chat_id`/`telegram_chat_type` exactly as
declared on the chat — `conversation_id`/`protocol_hint` are no longer caller-supplied for this
path, since the real bot handler now derives them itself, exactly as it would for a real Telegram
message (docs/bot_simulation_mode_design.md §10); `kind: "event"` steps still go straight to
`POST /Event`. An edited pending step updates the in-memory queue and the
payload that is actually sent: `text` and `sender_identity` always, and
`timestamp` when the operator fills the optional datetime field (message-kind
steps carry it to the simulation-mode bot as Telegram `message.date`;
event-kind steps send it to `POST /Event` as `received_at`). Declared
simulations omit `timestamp` so send-time is used. `sender_name`, `label`,
`title`, `description` and `tags` remain display-only.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from api.admin_api_pages import FLASH_MESSAGES, IDENTITY_BAR
from api.simulations import simulation_catalog_payload
from messages import MessageCatalog

if TYPE_CHECKING:
    from api.app import ApiContext

SIMULATOR_STRING_PREFIX = "admin.simulator."


def simulator_page_context(
    ctx: "ApiContext", catalog: MessageCatalog, bot_service_identity: str, api_identity: str = ""
) -> dict:
    """Everything the page's script needs, as one JSON-serialisable dict: the live group
    bindings and registered users (so cards can show how a chat will be routed and warn about an
    unregistered sender before the API refuses it), the routable agents, and the raw
    `admin.simulator.*` message templates of the current catalog (formatted client-side).

    `api_identity` is the admin's currently-selected registered identity (the same
    `IDENTITY_BAR`/`api_identity` mechanism the Profiles/Protocols/Events pages already
    use, `api/admin_api_pages.py`) — it gates the server-rendered simulation catalog and
    authenticates the selected scenario's `GET /Simulations/<key>` materialization request;
    it is unrelated to any scenario step's own `sender_identity`, which is always used for
    that step's own request."""

    groups = [
        {"chat_id": binding.chat_id, "agent_name": binding.agent_name, "label": binding.label}
        for binding in ctx.group_routing.all()
    ]
    users = [
        {
            "telegram_identity": user["telegram_identity"],
            "permission_level": user["permission_level"],
            "full_name": user.get("full_name", ""),
        }
        for user in sorted(ctx.deps.persistence.list_users(), key=lambda user: user["telegram_identity"])
    ]
    selected_user = next((user for user in users if user["telegram_identity"] == api_identity), None)
    can_view_simulations = bool(selected_user and selected_user["permission_level"] == "commander")
    profile_simulations = simulation_catalog_payload(ctx.loaded_profile) if can_view_simulations else []
    if not api_identity:
        profile_simulations_hint_key = "admin.simulator.select_identity_first"
    elif not can_view_simulations:
        profile_simulations_hint_key = "admin.simulator.profile_simulations_commander_required"
    elif not profile_simulations:
        profile_simulations_hint_key = "admin.simulator.no_profile_simulations"
    else:
        profile_simulations_hint_key = ""
    strings = {
        key[len(SIMULATOR_STRING_PREFIX):]: template
        for key, template in catalog.messages.items()
        if key.startswith(SIMULATOR_STRING_PREFIX)
    }
    return {
        "groups": groups,
        "users": users,
        "routable_agents": list(ctx.group_routing.routable_targets),
        "bot_service_identity": bot_service_identity,
        "api_identity": api_identity,
        "profile_simulations": profile_simulations,
        "profile_simulations_hint_key": profile_simulations_hint_key,
        "strings": strings,
        "queued_ack_prefixes": [
            catalog.messages[key] for key in ("api.queued_report", "api.queued_request") if key in catalog.messages
        ],
    }

from api.admin_simulator_assets import SIMULATOR_BODY, SIMULATOR_STYLE
