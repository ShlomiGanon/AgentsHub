"""Loading protocols from the profile (work_plan.md §4.2)."""

import ast
import importlib.util
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from profiles.loader import validate_single_protocol
from protocols.contracts import Protocol, ProtocolEditError

if TYPE_CHECKING:
    from profiles.loader import LoadedProfile


@dataclass(frozen=True)
class ProtocolSet:
    protocols: tuple[Protocol, ...]

    def all(self) -> tuple[Protocol, ...]:
        return self.protocols

    def get(self, name: str) -> Protocol | None:
        for protocol in self.protocols:
            if protocol.name == name:
                return protocol
        return None


def load_protocols(loaded_profile: "LoadedProfile") -> ProtocolSet:
    return ProtocolSet(protocols=loaded_profile.protocols)


EDIT_SUCCESS_MESSAGE = "The running system is unchanged. This edit applies from the next start."


def read_protocols(protocol_set) -> tuple[Protocol, ...]:
    return protocol_set.all()


def add_protocol(module_path: str, current_protocols: tuple[Protocol, ...], agents_by_name: dict, new_protocol: Protocol) -> str:
    if any(protocol.name == new_protocol.name for protocol in current_protocols):
        raise ProtocolEditError(f"a protocol named '{new_protocol.name}' already exists — use replace, not add")

    _validate_or_raise(new_protocol, agents_by_name)
    _write_protocols(module_path, (*current_protocols, new_protocol))
    return EDIT_SUCCESS_MESSAGE


def replace_protocol(module_path: str, current_protocols: tuple[Protocol, ...], agents_by_name: dict, updated_protocol: Protocol) -> str:
    if not any(protocol.name == updated_protocol.name for protocol in current_protocols):
        raise ProtocolEditError(f"no protocol named '{updated_protocol.name}' exists — use add, not replace")

    _validate_or_raise(updated_protocol, agents_by_name)
    updated = tuple(updated_protocol if protocol.name == updated_protocol.name else protocol for protocol in current_protocols)
    _write_protocols(module_path, updated)
    return EDIT_SUCCESS_MESSAGE


def remove_protocol(module_path: str, current_protocols: tuple[Protocol, ...], name: str) -> str:
    if not any(protocol.name == name for protocol in current_protocols):
        raise ProtocolEditError(f"no protocol named '{name}' exists")

    _write_protocols(module_path, tuple(protocol for protocol in current_protocols if protocol.name != name))
    return EDIT_SUCCESS_MESSAGE


def _validate_or_raise(protocol: Protocol, agents_by_name: dict) -> None:
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

    body = "\n".join(_render_protocol(protocol) for protocol in protocols)
    return f"PROTOCOLS = [\n{body}\n]"


def _find_protocols_assignment_span(source: str) -> tuple[int, int]:
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "PROTOCOLS" for target in node.targets):
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

    tmp_path = Path(str(file_path) + ".tmp")
    tmp_path.write_text(new_source, encoding="utf-8")
    os.replace(tmp_path, file_path)
