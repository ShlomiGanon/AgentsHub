"""Small file-based control channel shared by the admin API and ``run_stack``.

The files deliberately live outside every profile database.  Only profile module names
discovered from the local ``profiles`` package may be submitted; the supervisor repeats that
check before acting, so editing an HTTP form cannot turn this channel into an arbitrary import.
"""

from __future__ import annotations

import importlib
import json
import os
import secrets
import time
from dataclasses import dataclass
from pathlib import Path

from profiles import REQUIRED_PROFILE_ATTRS


@dataclass(frozen=True)
class ProfileInfo:
    module_path: str
    profile_name: str
    api_port: int


def control_dir() -> Path:
    configured = os.environ.get("AGENTSHUB_CONTROL_DIR", "").strip()
    return Path(configured) if configured else Path(__file__).resolve().parent.parent / "data" / "server_control"


def _atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(6)}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def discover_profiles(directory: Path | None = None) -> tuple[ProfileInfo, ...]:
    profile_dir = directory or Path(__file__).resolve().parent.parent / "profiles"
    discovered: list[ProfileInfo] = []
    for source in sorted(profile_dir.glob("*.py")):
        stem = source.stem
        if stem.startswith("_") or stem in {"contracts", "loader", "template"}:
            continue
        module_path = f"profiles.{stem}"
        try:
            module = importlib.import_module(module_path)
            profile_name = getattr(module, "PROFILE_NAME")
            api_port = getattr(module, "API_PORT")
            db_path = getattr(module, "DB_PATH")
            if any(not hasattr(module, attribute) for attribute in REQUIRED_PROFILE_ATTRS):
                continue
            resettable = getattr(module, "RESETTABLE_DATABASES", None)
            if not isinstance(profile_name, str) or not profile_name.strip():
                continue
            if not isinstance(api_port, int) or not 1 <= api_port <= 65535:
                continue
            if not isinstance(db_path, str) or not db_path:
                continue
            if not isinstance(resettable, tuple) or not resettable or db_path not in resettable:
                continue
            if any(not isinstance(path, str) or not path.strip() for path in resettable):
                continue
        except Exception:
            continue
        discovered.append(ProfileInfo(module_path, profile_name.strip(), api_port))
    return tuple(discovered)


def available_profile(module_path: str) -> ProfileInfo | None:
    return next((item for item in discover_profiles() if item.module_path == module_path), None)


def write_status(**values: object) -> None:
    _atomic_json(control_dir() / "status.json", {"updated_at": time.time(), **values})


def read_status() -> dict:
    try:
        value = json.loads((control_dir() / "status.json").read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def supervisor_available() -> bool:
    status = read_status()
    return bool(os.environ.get("AGENTSHUB_SUPERVISOR") == "1" and status.get("supervisor_pid"))


def submit_command(action: str, *, profile_module: str | None = None) -> str:
    if action not in {"reset", "switch_profile"}:
        raise ValueError("unsupported server-control action")
    if not supervisor_available():
        raise RuntimeError("stack supervisor is not available")
    if action == "switch_profile" and available_profile(profile_module or "") is None:
        raise ValueError("unknown profile module")
    command_id = secrets.token_urlsafe(18)
    command = {"id": command_id, "action": action, "created_at": time.time()}
    if profile_module is not None:
        command["profile_module"] = profile_module
    _atomic_json(control_dir() / "command.json", command)
    return command_id


def consume_command() -> dict | None:
    path = control_dir() / "command.json"
    try:
        command = json.loads(path.read_text(encoding="utf-8"))
        path.unlink()
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return None
    return command if isinstance(command, dict) else None


def save_selected_profile(module_path: str) -> None:
    if available_profile(module_path) is None:
        raise ValueError("unknown profile module")
    _atomic_json(control_dir() / "selected_profile.json", {"module_path": module_path})


def load_selected_profile(default: str = "profiles.unified_test") -> str:
    try:
        value = json.loads((control_dir() / "selected_profile.json").read_text(encoding="utf-8"))
        module_path = value.get("module_path")
    except (OSError, ValueError, AttributeError):
        module_path = default
    return module_path if available_profile(module_path) is not None else default
