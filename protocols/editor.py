"""Profile protocol editing (work_plan.md §4.3).

A read is just the currently-loaded `ProtocolSet` — reads describe what's
running, not what's on disk, so there's nothing to fetch. A write modifies
the profile *file*, leaving the loaded set untouched; the running system
only picks it up on the next start, and every write result says so
explicitly — the dangerous mistake this exists to prevent is a person
believing a protocol is live when it isn't.

A profile's `PROTOCOLS = [...]` is Python source, not data. Rather than
surgically editing one element in place (fragile — comma/whitespace
bookkeeping around arbitrary hand-formatted entries), a write regenerates
the *entire* `PROTOCOLS` assignment from the currently-loaded `Protocol`
objects plus the one being added/replaced/removed, and splices that in
place of the original assignment's exact line span. Everything else in
the file is untouched. This relies on one invariant: the file being
edited already validated successfully (§4.2's precondition — it's the
file the running system loaded), so it already imports `Protocol` and
`CriticalityLevel`; the regenerated block references them unqualified.
"""

import ast
import importlib.util
import os
from dataclasses import dataclass
from pathlib import Path

from profiles.loader import validate_single_protocol
from protocols.model import Protocol


class ProtocolEditError(Exception):
    """A protocol write was rejected, or the profile file could not be edited."""


@dataclass(frozen=True)
class ProtocolEditResult:
    message: str = "The running system is unchanged. This edit applies from the next start."


def read_protocols(protocol_set) -> tuple[Protocol, ...]:
    """The currently loaded protocols — deliberately just a pass-through,
    so a caller never wonders whether reading touched disk."""

    return protocol_set.all()


def add_protocol(module_path: str, current_protocols: tuple[Protocol, ...], agents_by_name: dict, new_protocol: Protocol) -> ProtocolEditResult:
    if any(p.name == new_protocol.name for p in current_protocols):
        raise ProtocolEditError(f"a protocol named '{new_protocol.name}' already exists — use replace, not add")

    _validate_or_raise(new_protocol, agents_by_name)
    _write_protocols(module_path, (*current_protocols, new_protocol))
    return ProtocolEditResult()


def replace_protocol(module_path: str, current_protocols: tuple[Protocol, ...], agents_by_name: dict, updated_protocol: Protocol) -> ProtocolEditResult:
    if not any(p.name == updated_protocol.name for p in current_protocols):
        raise ProtocolEditError(f"no protocol named '{updated_protocol.name}' exists — use add, not replace")

    _validate_or_raise(updated_protocol, agents_by_name)
    updated = tuple(updated_protocol if p.name == updated_protocol.name else p for p in current_protocols)
    _write_protocols(module_path, updated)
    return ProtocolEditResult()


def remove_protocol(module_path: str, current_protocols: tuple[Protocol, ...], name: str) -> ProtocolEditResult:
    if not any(p.name == name for p in current_protocols):
        raise ProtocolEditError(f"no protocol named '{name}' exists")

    updated = tuple(p for p in current_protocols if p.name != name)
    _write_protocols(module_path, updated)
    return ProtocolEditResult()


def _validate_or_raise(protocol: Protocol, agents_by_name: dict) -> None:
    # The approval flag having no default is enforced by Protocol's own
    # constructor (a required field); this call still catches a
    # deliberately-passed None, matching the startup rule that an absent
    # flag is a failure, never defaulted.
    failures = validate_single_protocol(protocol, agents_by_name)
    if failures:
        raise ProtocolEditError("; ".join(failures))


def _render_protocol(protocol: Protocol) -> str:
    return (
        "    Protocol(\n"
        f"        name={protocol.name!r},\n"
        f"        description={protocol.description!r},\n"
        f"        participating_agents={tuple(protocol.participating_agents)!r},\n"
        f"        approved_tools={tuple(protocol.approved_tools)!r},\n"
        f"        expected_success_output={protocol.expected_success_output!r},\n"
        f"        criticality=CriticalityLevel.{protocol.criticality.name},\n"
        f"        approval_flag={protocol.approval_flag!r},\n"
        "    ),"
    )


def _render_protocols_assignment(protocols: tuple[Protocol, ...]) -> str:
    if not protocols:
        return "PROTOCOLS = []"

    body = "\n".join(_render_protocol(p) for p in protocols)
    return f"PROTOCOLS = [\n{body}\n]"


def _find_protocols_assignment_span(source: str) -> tuple[int, int]:
    """1-indexed, inclusive (start_line, end_line) of the module-level
    `PROTOCOLS = ...` assignment."""

    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "PROTOCOLS" for t in node.targets):
            return node.lineno, node.end_lineno

    raise ProtocolEditError("profile has no top-level PROTOCOLS assignment to edit")


def _resolve_profile_file(module_path: str) -> Path:
    spec = importlib.util.find_spec(module_path)
    if spec is None or spec.origin is None:
        raise ProtocolEditError(f"cannot locate source file for profile module '{module_path}'")

    return Path(spec.origin)


def _write_protocols(module_path: str, protocols: tuple[Protocol, ...]) -> None:
    file_path = _resolve_profile_file(module_path)
    source = file_path.read_text(encoding="utf-8")

    start_line, end_line = _find_protocols_assignment_span(source)
    lines = source.splitlines(keepends=True)
    new_block = _render_protocols_assignment(protocols) + "\n"

    new_source = "".join(lines[: start_line - 1] + [new_block] + lines[end_line:])

    # Write to a temp file and replace, so an interrupted edit can never
    # leave a profile that fails to import (§4.3).
    tmp_path = Path(str(file_path) + ".tmp")
    tmp_path.write_text(new_source, encoding="utf-8")
    os.replace(tmp_path, file_path)
