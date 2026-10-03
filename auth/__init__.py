"""Permission model — public re-export surface.

`auth.permissions` remains directly importable for existing call sites; this
module re-exports the same objects as the package's forward-looking public
contract.
"""

from auth.permissions import (
    BOT_SERVICE_IDENTITY,
    BOT_SERVICE_KEY_ENV_VAR,
    PermissionLevel,
    RequestedOperation,
    ViewerAllowedAction,
    is_permitted,
)
from auth.user_names import InvalidFullNameError, MAX_FULL_NAME_LENGTH, normalize_full_name

__all__ = [
    "BOT_SERVICE_IDENTITY",
    "BOT_SERVICE_KEY_ENV_VAR",
    "InvalidFullNameError",
    "MAX_FULL_NAME_LENGTH",
    "PermissionLevel",
    "RequestedOperation",
    "ViewerAllowedAction",
    "is_permitted",
    "normalize_full_name",
]
