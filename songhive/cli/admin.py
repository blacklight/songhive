"""
Admin CLI commands.

Usage:
    songhive admin create-user --username <name> --email <email> --password <pw> [--admin]
    songhive admin promote-user --username <name>
    songhive admin create-invite --created-by <username> [--max-uses <n>] [--expires-at <iso>]
    songhive admin list-invites
"""

import argparse
import asyncio
import sys
from datetime import datetime, timezone

from ..config import load_config
from ..models.base import get_session, init_db
from ..services.auth import create_user, get_user_by_username
from ..users.invites import InviteError, create_invite, list_invites


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

    # create-invite
    create_invite_parser = subparsers.add_parser("create-invite", help="Create an invite code")
    create_invite_parser.add_argument("--created-by", required=True, help="Username of the admin creating the invite")
    create_invite_parser.add_argument("--max-uses", type=int, default=None, help="Maximum number of uses")
    create_invite_parser.add_argument(
        "--expires-at", default=None, help="ISO 8601 expiration datetime (e.g. 2026-01-01T00:00:00Z)"
    )

    # list-invites
    subparsers.add_parser("list-invites", help="List invite codes")

    return parser


def _parse_iso_datetime(value: str) -> datetime:
    """Parse an ISO 8601 datetime string into a timezone-aware UTC datetime."""
    value = value.strip()
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


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


async def _handle_create_invite(args):
    async with get_session() as session:
        user = await get_user_by_username(session, args.created_by)
        if not user:
            print(f"Error: user '{args.created_by}' not found", file=sys.stderr)
            sys.exit(1)

        expires_at = None
        if args.expires_at:
            try:
                expires_at = _parse_iso_datetime(args.expires_at)
            except ValueError as exc:
                print(f"Error: invalid expires_at: {exc}", file=sys.stderr)
                sys.exit(1)

        try:
            invite = await create_invite(
                session,
                created_by=user.id,
                max_uses=args.max_uses,
                expires_at=expires_at,
            )
        except InviteError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(1)

        print(f"Invite code: {invite.code}")
        print(f"  max_uses: {invite.max_uses}")
        print(f"  uses: {invite.uses}")
        if invite.expires_at:
            print(f"  expires_at: {invite.expires_at.isoformat()}")


async def _handle_list_invites(*_):
    async with get_session() as session:
        invites = await list_invites(session, limit=1000, offset=0)
        if not invites:
            print("No invite codes found.")
            return
        for invite in invites:
            expires = invite.expires_at.isoformat() if invite.expires_at else "never"
            print(f"{invite.code}  max_uses={invite.max_uses}  uses={invite.uses}  expires_at={expires}")


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
        "create-invite": _handle_create_invite,
        "list-invites": _handle_list_invites,
    }

    handler = handlers.get(args.command)
    if handler:
        asyncio.run(handler(args))
    else:
        parser.print_help()
        sys.exit(1)
