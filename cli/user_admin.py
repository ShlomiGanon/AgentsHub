"""User administration command (work_plan.md §1.10).

Run from the shell on the machine hosting a deployment. This is the only
way into the user table — no equivalent exists through the API or the bot,
deliberately: user management stays outside the running system. Works
against a database with no users at all, since a fresh deployment has no
commander and no in-system path could ever create the first one.
"""

import argparse
import os
import sys

from auth.permissions import PermissionLevel
from config.base import ModelTierError, TierModel, build_tier_model
from persistence.exceptions import NotFoundError, PersistenceError
from persistence.interface import PersistenceInterface, open_persistence
from profiles.loader import ProfileLoadError, ProfileValidationError, load_profile


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m cli.user_admin",
        description="Add, change, and remove users for one deployment.",
    )
    parser.add_argument(
        "--profile",
        required=True,
        help="profile module path, e.g. 'fixtures.profiles.minimal_profile' — "
        "resolved through the same loader the running system uses, so the "
        "command writes to that deployment's database and no other",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    add_parser = subparsers.add_parser("add", help="add or replace a user")
    add_parser.add_argument("--telegram-id", required=True)
    add_parser.add_argument("--level", required=True, choices=[level.name.lower() for level in PermissionLevel])

    update_parser = subparsers.add_parser("update", help="change a user's permission level")
    update_parser.add_argument("--telegram-id", required=True)
    update_parser.add_argument("--level", required=True, choices=[level.name.lower() for level in PermissionLevel])

    remove_parser = subparsers.add_parser("remove", help="remove a user")
    remove_parser.add_argument("--telegram-id", required=True)

    subparsers.add_parser("list", help="list every registered user")

    return parser


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if value is None:
        raise ModelTierError(f"required environment variable '{name}' is not set")
    return value


def _tier_model_from_environ(prefix: str) -> TierModel:
    """Read one tier's provider/model name/API key straight from the real
    process environment — `main`'s own job, the one place in this module
    `os.environ` is read for model-tier config (see config/base.py's
    module docstring). `prefix` is `"CORE"` or `"SUB"`.
    """

    provider = _require_env(f"{prefix}_MODEL_PROVIDER")
    model_name = _require_env(f"{prefix}_MODEL_NAME")
    api_key_env_name = _require_env(f"{prefix}_MODEL_API_KEY_ENV")
    api_key = _require_env(api_key_env_name)
    return build_tier_model(provider, model_name, api_key)


def main(argv: list[str] | None = None) -> int:
    """One of the three real entry points (with `api.app.main`,
    `bot.app.main`) that reads `os.environ` for model-tier config —
    everything below it takes already-resolved `TierModel` values instead.
    """

    args = _build_parser().parse_args(argv)

    try:
        core_model = _tier_model_from_environ("CORE")
        sub_model = _tier_model_from_environ("SUB")
    except ModelTierError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    try:
        loaded_profile = load_profile(args.profile, core_model=core_model, sub_model=sub_model)
    except (ProfileLoadError, ProfileValidationError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    store = open_persistence(loaded_profile.db_path)

    try:
        return _run_command(args, store)
    finally:
        store.close()


def _run_command(args: argparse.Namespace, store: PersistenceInterface) -> int:
    if args.command in ("add", "update"):
        store.write_user(args.telegram_id, args.level)
        print(f"{args.command}: '{args.telegram_id}' is now '{args.level}'")
        return 0

    if args.command == "remove":
        try:
            store.delete_user(args.telegram_id)
        except NotFoundError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        print(f"remove: '{args.telegram_id}' removed")
        return 0

    if args.command == "list":
        for user in store.list_users():
            print(f"{user['telegram_identity']}\t{user['permission_level']}")
        return 0

    raise AssertionError(f"unreachable: unknown command '{args.command}'")


if __name__ == "__main__":
    try:
        sys.exit(main())
    except PersistenceError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
