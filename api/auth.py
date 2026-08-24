"""Authentication and authorization (work_plan.md §7.9).

Two functions, called by every route in `api/` before its own logic —
never an inline level comparison anywhere in this package. `authenticate`
looks the caller up in the user table and returns their level, rejecting
a missing or unregistered identity outright rather than defaulting to
viewer. `require` checks one action against that level through the exact
same `auth.permissions.is_permitted` every other part of this system
uses — the sensor path (`POST /Event`) authenticates through this same
function too, as a pre-registered identity, never a bypass.
"""

from typing import TYPE_CHECKING

from auth.permissions import PermissionLevel, is_permitted

from api.errors import AuthenticationError, AuthorizationError

if TYPE_CHECKING:
    from persistence.interface import PersistenceInterface

IDENTITY_HEADER = "X-Identity"


def authenticate(persistence: "PersistenceInterface", identity: str | None) -> PermissionLevel:
    if not identity:
        raise AuthenticationError("no identity supplied")

    user = persistence.read_user(identity)
    if user is None:
        raise AuthenticationError(f"'{identity}' is not a registered identity")

    return PermissionLevel[user["permission_level"].upper()]


def require(level: PermissionLevel, action: str) -> None:
    if not is_permitted(level, action):
        raise AuthorizationError(f"level {level.name} may not {action.replace('_', ' ')}")
