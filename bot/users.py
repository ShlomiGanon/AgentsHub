"""Resolve users against the user table (work_plan.md §8.2).

Looks up every Telegram identity on every interaction and turns the
result into exactly the two refusal messages §8.2 requires — "not
registered" for an identity the table doesn't know, and a named-action
refusal for a registered user acting above their level. The permission
*comparison* itself is `auth.permissions.is_permitted` (work_plan.md
§1.9's shared function), imported directly — `auth` is a low-level
package callable by anything (docs/allowed_calls.md), and §1.9 itself
names the bot as one of the function's two callers. Only the identity ->
level *lookup* goes through the API seam (`bot.api_client`), since that
requires reading the user table (§2.4), which lives behind persistence,
which the bot has no path to except through the API.

Deliberately absent from this module, and from every command this
package registers (§8.2's own explicit prohibition): anything that adds,
changes, removes, or lists users. `cli.user_admin` (work_plan.md §1.10)
is the only path that manages users; see `tests/test_bot_users.py`'s
`test_no_user_management_capability_exists` for the check that keeps it
that way.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from auth.permissions import PermissionLevel, is_permitted

if TYPE_CHECKING:
    from bot.api_client import BotApiClient

_LEVEL_BY_NAME: dict[str, PermissionLevel] = {"viewer": PermissionLevel.VIEWER, "commander": PermissionLevel.COMMANDER}


@dataclass(frozen=True)
class CallerContext:
    telegram_identity: str
    level: PermissionLevel


@dataclass(frozen=True)
class UserResolutionResult:
    status: Literal["ok", "unregistered"]
    caller: CallerContext | None = None
    refusal_message: str = ""


def _unregistered_message(telegram_identity: str) -> str:
    return (
        f"You are not a registered user of this system (identity: {telegram_identity}). "
        f"An administrator must add you before you can use this bot."
    )


async def resolve_caller(api_client: "BotApiClient", telegram_identity: str) -> UserResolutionResult:
    lookup = await api_client.resolve_user(telegram_identity)

    if not lookup.registered or lookup.permission_level is None:
        return UserResolutionResult(status="unregistered", refusal_message=_unregistered_message(telegram_identity))

    level = _LEVEL_BY_NAME[lookup.permission_level]
    return UserResolutionResult(status="ok", caller=CallerContext(telegram_identity=telegram_identity, level=level))


def check_permission(caller: CallerContext, action: str) -> str | None:
    """None when `caller` may perform `action`; otherwise a message naming
    the refused action — never a silent no-op (§8.2: "A silent no-op
    leaves a commander believing they approved something").
    """

    if is_permitted(caller.level, action):
        return None

    return (
        f"Refused: '{action}' requires commander level; your account "
        f"({caller.telegram_identity}) is registered as {caller.level.name.lower()}."
    )
