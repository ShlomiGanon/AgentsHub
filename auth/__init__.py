"""Permission model — public re-export surface (work_plan.md §1.9; docs/Next_Plan.md §6).

`auth.permissions` remains directly importable for existing call sites; this
module re-exports the same objects as the package's forward-looking public
contract.
"""

from auth.permissions import PermissionLevel, RequestedOperation, ViewerAllowedAction, is_permitted

__all__ = ["PermissionLevel", "RequestedOperation", "ViewerAllowedAction", "is_permitted"]
