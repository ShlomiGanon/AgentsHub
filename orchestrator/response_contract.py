"""Typed authority boundary for user-facing responses.

Model output is presentation only.  Claims about capabilities, permissions,
state, events, approvals, and execution must carry an application-owned,
verified source reference before they are delivered.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


ResponseKind = Literal[
    "refusal",
    "conversational",
    "informational",
    "permission",
    "approval",
    "state",
    "action_status",
    "error",
]

ClaimCategory = Literal[
    "capability",
    "permission",
    "approval",
    "action_status",
    "tool_execution",
    "persisted_state",
    "event_status",
    "informational",
]


class ResponseAuthorityError(ValueError):
    """A response contains a claim without an authoritative source."""


@dataclass(frozen=True)
class ResponseClaim:
    category: ClaimCategory
    value: str
    source_ref: str | None
    verified: bool = True


@dataclass(frozen=True)
class ResponseEnvelope:
    """The only shape accepted by the shared response guard.

    ``claims`` are ephemeral metadata.  They are not persisted and never
    contain model reasoning or raw provider output.
    """

    response_text: str
    response_kind: ResponseKind
    claims: tuple[ResponseClaim, ...] = ()
    authorized_capabilities: frozenset[str] = frozenset()
    authenticated_permission: str | None = None


_SOURCE_PREFIXES: dict[ClaimCategory, tuple[str, ...]] = {
    "capability": ("capability:",),
    "permission": ("auth:",),
    "approval": ("approval:",),
    "action_status": ("event:", "state:", "tool:"),
    "tool_execution": ("tool:", "event:", "state:"),
    "persisted_state": ("state:", "event:"),
    "event_status": ("event:",),
    "informational": ("agent:", "state:", "event:", "capability:"),
}

_TERMINAL_EXECUTION_VALUES = frozenset({"executed", "completed", "dispatched"})


def validate_response(envelope: ResponseEnvelope) -> None:
    """Validate claim provenance without inspecting text or filtering words."""

    if not isinstance(envelope.response_text, str) or not envelope.response_text.strip():
        raise ResponseAuthorityError("response text is empty")

    for claim in envelope.claims:
        if not claim.verified or not claim.source_ref:
            raise ResponseAuthorityError(f"unverified {claim.category} claim")
        allowed_prefixes = _SOURCE_PREFIXES.get(claim.category)
        if allowed_prefixes is None or not isinstance(claim.value, str):
            raise ResponseAuthorityError(f"unknown or malformed claim category: {claim.category}")
        if not any(claim.source_ref.startswith(prefix) for prefix in allowed_prefixes):
            raise ResponseAuthorityError(
                f"{claim.category} claim has an invalid source reference"
            )

        if claim.category == "capability" and claim.value.strip() == "":
            raise ResponseAuthorityError("capability claim is empty")

        if (
            claim.category == "capability"
            and envelope.authorized_capabilities
            and claim.value not in envelope.authorized_capabilities
        ):
            raise ResponseAuthorityError("capability claim is not authorized for this caller")

        if (
            claim.category == "permission"
            and envelope.authenticated_permission is not None
            and claim.value != envelope.authenticated_permission
        ):
            raise ResponseAuthorityError("permission claim does not match authenticated state")

        if (
            claim.category == "action_status"
            and claim.value.casefold() in _TERMINAL_EXECUTION_VALUES
            and not claim.source_ref.startswith(("tool:", "state:"))
        ):
            raise ResponseAuthorityError(
                "terminal execution claim requires verified tool or state evidence"
            )

    if envelope.response_kind == "permission" and not any(
        claim.category == "permission" for claim in envelope.claims
    ):
        raise ResponseAuthorityError("permission response has no authenticated source")

    if envelope.response_kind in {"informational", "state"} and not any(
        claim.category in {"capability", "informational", "persisted_state", "event_status"}
        for claim in envelope.claims
    ):
        raise ResponseAuthorityError("informational response has no typed source")

    if envelope.response_kind == "approval" and not any(
        claim.category == "approval" for claim in envelope.claims
    ):
        raise ResponseAuthorityError("approval response has no approval record")

    if envelope.response_kind == "action_status":
        terminal_claims = [
            claim
            for claim in envelope.claims
            if claim.category == "action_status"
            and claim.value.casefold() in _TERMINAL_EXECUTION_VALUES
        ]
        if terminal_claims and not any(
            claim.category == "tool_execution" and claim.verified
            for claim in envelope.claims
        ):
            raise ResponseAuthorityError(
                "terminal action response has no verified execution evidence"
            )


def render_response(envelope: ResponseEnvelope, *, fallback: str) -> str:
    """Return user-facing text only after the authority contract passes."""

    try:
        validate_response(envelope)
    except ResponseAuthorityError:
        return fallback
    return envelope.response_text.strip()


def refusal_response(text: str) -> ResponseEnvelope:
    return ResponseEnvelope(text, "refusal")


def capability_response(
    response_text: str,
    capability_name: str,
    authorized_capabilities: frozenset[str] | set[str] | tuple[str, ...],
) -> ResponseEnvelope:
    """Build a capability-grounded response or a refusal envelope.

    The caller supplies the already-resolved capability identifier; this
    helper performs no text or keyword matching.
    """

    allowed = frozenset(authorized_capabilities)
    if capability_name not in allowed:
        return refusal_response("This is outside the system's available capabilities.")
    return ResponseEnvelope(
        response_text,
        "informational",
        claims=(ResponseClaim("capability", capability_name, f"capability:{capability_name}"),),
        authorized_capabilities=allowed,
    )


def permission_response(response_text: str, permission_level: str | None) -> ResponseEnvelope:
    """Build a response grounded in the authenticated permission snapshot."""

    if not permission_level:
        return refusal_response("Permission state is unavailable.")
    return ResponseEnvelope(
        response_text,
        "permission",
        claims=(ResponseClaim("permission", permission_level, f"auth:{permission_level}"),),
        authenticated_permission=permission_level,
    )


def informational_response(response_text: str, source_refs: tuple[str, ...]) -> ResponseEnvelope:
    """Wrap an answer whose facts came from typed agent/state sources."""

    claims = tuple(
        ResponseClaim("informational", "answer", source_ref)
        for source_ref in source_refs
    )
    return ResponseEnvelope(response_text, "informational", claims=claims)
