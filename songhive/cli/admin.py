"""
Admin CLI commands.

Usage:
    songhive admin create-user --username <name> --email <email> --password <pw> [--admin]
    songhive admin promote-user --username <name>
"""

import argparse
import asyncio
import sys

from ..config import load_config
from ..models.base import get_session, init_db
from ..services.auth import create_user, get_user_by_username


def _create_admin_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="songhive admin")
    subparsers = parser.add_subparsers(dest="command")

    # create-user
    create_user_parser = subparsers.add_parser("create-user", help="Create a new user")
    create_user_parser.add_argument("--username", required=True)
    create_user_parser.add_argument("--email", required=True)
    create_user_parser.add_argument("--password", required=True)
    create_user_parser.add_argument("--admin", action="store_true", default=False)

    # promote-user
    promote_parser = subparsers.add_parser("promote-user", help="Promote a user to admin")
    promote_parser.add_argument("--username", required=True)

    return parser


async def _handle_create_user(args):
    async with get_session() as session:
        role = "admin" if args.admin else "user"
        user = await create_user(
            session,
            username=args.username,
            email=args.email,
            password=args.password,
            role=role,
        )
        print(f"User '{user.username}' created successfully (id={user.id})")


async def _handle_promote_user(args):
    async with get_session() as session:
        user = await get_user_by_username(session, args.username)
        if not user:
            print(f"Error: user '{args.username}' not found", file=sys.stderr)
            sys.exit(1)
        user.role = "admin"
        await session.flush()
        print(f"User '{user.username}' promoted to admin")


def admin_main(argv=None):
    """Entry point for admin CLI commands."""
    parser = _create_admin_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        sys.exit(1)

    # Load config and init DB
    config = load_config([])
    init_db(config.database.url)

    handlers = {
        "create-user": _handle_create_user,
        "promote-user": _handle_promote_user,
    }

    handler = handlers.get(args.command)
    if handler:
        asyncio.run(handler(args))
    else:
        parser.print_help()
        sys.exit(1)
