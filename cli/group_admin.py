"""Telegram group routing administration command.

Binds a Telegram group chat to the specialist agent its messages belong to
(or to `main_agent` for full routing), for one deployment. Writes go straight
to that deployment's `telegram_groups` table; a running API process picks
them up within its routing table's refresh interval (orchestrator.group_routing),
or immediately if the change is made through the admin panel / `PUT /Groups`
instead.
"""

import argparse
import sys

from config import ModelTierError, TierModel, resolve_tier_model_from_env
from persistence import NotFoundError, PersistenceError, PersistenceInterface, open_persistence
from profiles.loader import ProfileLoadError, ProfileValidationError, load_profile

MAIN_AGENT_TARGET = "main_agent"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m cli.group_admin",
        description="Bind, rebind, and remove Telegram groups for one deployment.",
    )
    parser.add_argument(
        "--profile",
        required=True,
        help="profile module path, e.g. 'profiles.unified_test' — resolved through the same "
        "loader the running system uses, so the command writes to that deployment's database and no other",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    add_parser = subparsers.add_parser("add", help="bind or rebind a group to an agent")
    add_parser.add_argument("--chat-id", required=True, help="Telegram chat ID (negative for groups/supergroups)")
    add_parser.add_argument("--agent", required=True, help="specialist agent name from the profile, or 'main_agent'")
    add_parser.add_argument("--label", default="", help="free-text label shown in the admin panel")

    update_parser = subparsers.add_parser("update", help="change a group's agent or label")
    update_parser.add_argument("--chat-id", required=True)
    update_parser.add_argument("--agent", required=True)
    update_parser.add_argument("--label", default="")

    remove_parser = subparsers.add_parser("remove", help="remove a group binding")
    remove_parser.add_argument("--chat-id", required=True)

    approve_parser = subparsers.add_parser("approve", help="approve an automatically registered group")
    approve_parser.add_argument("--chat-id", required=True)

    subparsers.add_parser("list", help="list every registered group binding")

    return parser


def _tier_model_from_environ(prefix: str) -> TierModel:
    return resolve_tier_model_from_env(prefix, error_type=ModelTierError)


def main(argv: list[str] | None = None) -> int:
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

    routable = (MAIN_AGENT_TARGET, *sorted(agent.name for agent in loaded_profile.agents))
    store = open_persistence(loaded_profile.db_path)

    try:
        return _run_command(args, store, routable)
    finally:
        store.close()


def _run_command(args: argparse.Namespace, store: PersistenceInterface, routable: tuple[str, ...]) -> int:
    if args.command in ("add", "update"):
        chat_id = args.chat_id.strip()
        if not chat_id:
            print("error: --chat-id must not be empty", file=sys.stderr)
            return 1
        if args.agent not in routable:
            print(f"error: '{args.agent}' is not a routable agent; allowed: {', '.join(routable)}", file=sys.stderr)
            return 1
        store.write_group(chat_id, args.agent, args.label)
        print(f"{args.command}: group '{chat_id}' is now routed to '{args.agent}'")
        return 0

    if args.command == "remove":
        try:
            store.delete_group(args.chat_id)
        except NotFoundError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        print(f"remove: group '{args.chat_id}' removed")
        return 0

    if args.command == "approve":
        try:
            store.approve_group(args.chat_id)
        except NotFoundError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        print(f"approve: group '{args.chat_id}' approved")
        return 0

    if args.command == "list":
        for group in store.list_groups():
            source = "automatic" if group.get("auto_register", False) else "approved"
            print(f"{group['chat_id']}\t{group['agent_name']}\t{group['label']}\t{source}")
        return 0

    raise AssertionError(f"unreachable: unknown command '{args.command}'")


if __name__ == "__main__":
    try:
        sys.exit(main())
    except PersistenceError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
