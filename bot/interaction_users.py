"""Resolve Telegram callers and check whether an operation is permitted."""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from auth.permissions import PermissionLevel, RequestedOperation, is_permitted
from messages import MessageCatalog

from bot.interaction_format import _catalog

if TYPE_CHECKING:
    from bot.contracts import BotApiClient


_LEVEL_BY_NAME: dict[str, PermissionLevel] = {"viewer": PermissionLevel.VIEWER, "commander": PermissionLevel.COMMANDER}


@dataclass(frozen=True)
class CallerContext:
    """Resolved Telegram identity and permission level for one caller."""

    telegram_identity: str
    level: PermissionLevel


@dataclass(frozen=True)
class UserResolutionResult:
    """Outcome of looking up a Telegram identity against registered users."""

    status: Literal["ok", "unregistered"]
    caller: CallerContext | None = None
    refusal_message: str = ""
    full_name: str = ""



def _unregistered_message(telegram_identity: str, catalog: MessageCatalog | None = None) -> str:
    """Refusal text naming an identity that is not registered."""

    return _catalog(catalog).text("auth.unregistered", identity=telegram_identity)



async def resolve_caller(
    api_client: "BotApiClient", telegram_identity: str, catalog: MessageCatalog | None = None
) -> UserResolutionResult:
    """Look up the Telegram identity via the API. Returns ok with a caller, or unregistered with a refusal."""

    lookup = await api_client.resolve_user(telegram_identity)

    if not lookup.registered or lookup.permission_level is None:
        return UserResolutionResult(
            status="unregistered",
            refusal_message=_unregistered_message(telegram_identity, catalog),
        )

    level = _LEVEL_BY_NAME[lookup.permission_level]
    return UserResolutionResult(
        status="ok",
        caller=CallerContext(telegram_identity=telegram_identity, level=level),
        full_name=(lookup.full_name or "").strip(),
    )



def check_permission(
    caller: CallerContext, operation: RequestedOperation, catalog: MessageCatalog | None = None
) -> str | None:
    """None when `caller` may perform `operation`; otherwise a message naming the refused operation — never a silent no-op (§8.2: "A silent no-op leaves a commander believing they approved s..."""

    if is_permitted(caller.level, operation):
        return None

    return _catalog(catalog).text(
        "auth.operation_refused",
        operation=operation.value,
        identity=caller.telegram_identity,
        level=caller.level.name.lower(),
    )
