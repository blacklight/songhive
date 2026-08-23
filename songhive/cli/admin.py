"""
Admin CLI commands.

Usage:
    songhive admin init-db
    songhive admin create-user --username <name> --email <email> --password <pw> [--admin | --role <role>]
    songhive admin promote-user --username <name>
    songhive admin demote-user --username <name>
    songhive admin approve-user --username <name>
    songhive admin disable-user --username <name>
    songhive admin reset-password --username <name> --password <pw>
    songhive admin import-dir --path <dir> --library-id <uuid> [--owner <username>]
    songhive admin create-invite --created-by <username> [--max-uses <n>] [--expires-at <iso>]
    songhive admin list-invites
"""

import argparse
import asyncio
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from kombu.exceptions import OperationalError as KombuOperationalError

from ..config import load_config
from ..models.base import create_all_tables, get_session, init_db
from ..models.user import User, UserRole
from ..services.auth import create_user, get_user_by_username
from ..services.federation import ensure_user_actor
from ..tasks.import_ import _AUDIO_EXTENSIONS, scan_directory
from ..users import manager as user_manager
from ..users.invites import InviteError, create_invite, list_invites


def _create_admin_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="songhive admin")
    subparsers = parser.add_subparsers(dest="command")

    # init-db
    subparsers.add_parser("init-db", help="Create database tables if they do not exist")

    # create-user
    create_user_parser = subparsers.add_parser("create-user", help="Create a new user")
    create_user_parser.add_argument("--username", required=True)
    create_user_parser.add_argument("--email", required=True)
    create_user_parser.add_argument("--password", required=True)
    role_group = create_user_parser.add_mutually_exclusive_group()
    role_group.add_argument("--admin", action="store_true", default=False, help="Create the user as an admin")
    role_group.add_argument(
        "--role",
        choices=[r.value for r in UserRole],
        default=None,
        help="Role for the new user (default: user)",
    )

    # promote-user
    promote_parser = subparsers.add_parser("promote-user", help="Promote a user to admin")
    promote_parser.add_argument("--username", required=True)

    # demote-user
    demote_parser = subparsers.add_parser("demote-user", help="Demote a user to the user role")
    demote_parser.add_argument("--username", required=True)

    # approve-user
    approve_parser = subparsers.add_parser("approve-user", help="Approve a user by activating their account")
    approve_parser.add_argument("--username", required=True)

    # disable-user
    disable_parser = subparsers.add_parser("disable-user", help="Deactivate a user account")
    disable_parser.add_argument("--username", required=True)

    # reset-password
    reset_parser = subparsers.add_parser("reset-password", help="Reset a user's password")
    reset_parser.add_argument("--username", required=True)
    reset_parser.add_argument("--password", required=True)

    # import-dir
    import_parser = subparsers.add_parser("import-dir", help="Import audio files from a directory")
    import_parser.add_argument("--path", required=True, help="Path to the directory to import")
    import_parser.add_argument("--library-id", required=True, help="Library UUID to import into")
    import_parser.add_argument("--owner", default=None, help="Username to set as the owner")

    # create-invite
    create_invite_parser = subparsers.add_parser("create-invite", help="Create an invite code")
    create_invite_parser.add_argument("--created-by", required=True, help="Username of the admin creating the invite")
    create_invite_parser.add_argument("--max-uses", type=int, default=None, help="Maximum number of uses")
    create_invite_parser.add_argument(
        "--expires-at", default=None, help="ISO 8601 expiration datetime (e.g. 2026-01-01T00:00:00Z)"
    )

    # list-invites
    subparsers.add_parser("list-invites", help="List invite codes")

    # provision-federation-keys
    subparsers.add_parser(
        "provision-federation-keys",
        help="Provision ActivityPub actor keys for users that are missing them",
    )

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


async def _handle_init_db(*_, **__):
    await create_all_tables()
    print("Database tables initialized successfully")


async def _handle_create_user(args):
    config = load_config([])
    async with get_session() as session:
        role = "admin" if args.admin else (args.role or "user")
        user = await create_user(
            session,
            username=args.username,
            email=args.email,
            password=args.password,
            role=role,
            config=config,
        )
        print(f"User '{user.username}' created successfully (id={user.id}, role={user.role})")


async def _handle_promote_user(args):
    async with get_session() as session:
        user = await get_user_by_username(session, args.username)
        if not user:
            print(f"Error: user '{args.username}' not found", file=sys.stderr)
            sys.exit(1)
        try:
            await user_manager.promote_user(session, user.id)
        except user_manager.UserManagementError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(1)
        print(f"User '{args.username}' promoted to admin")


async def _handle_demote_user(args):
    async with get_session() as session:
        user = await get_user_by_username(session, args.username)
        if not user:
            print(f"Error: user '{args.username}' not found", file=sys.stderr)
            sys.exit(1)
        try:
            await user_manager.demote_user(session, user.id)
        except user_manager.UserManagementError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(1)
        print(f"User '{args.username}' demoted to user")


async def _handle_approve_user(args):
    async with get_session() as session:
        user = await get_user_by_username(session, args.username)
        if not user:
            print(f"Error: user '{args.username}' not found", file=sys.stderr)
            sys.exit(1)
        try:
            await user_manager.approve_user(session, user.id)
        except user_manager.UserManagementError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(1)
        print(f"User '{args.username}' approved and activated")


async def _handle_disable_user(args):
    async with get_session() as session:
        user = await get_user_by_username(session, args.username)
        if not user:
            print(f"Error: user '{args.username}' not found", file=sys.stderr)
            sys.exit(1)
        try:
            await user_manager.deactivate_user_by_id(session, user.id)
        except user_manager.UserManagementError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(1)
        print(f"User '{args.username}' deactivated")


async def _handle_reset_password(args):
    async with get_session() as session:
        user = await get_user_by_username(session, args.username)
        if not user:
            print(f"Error: user '{args.username}' not found", file=sys.stderr)
            sys.exit(1)
        try:
            await user_manager.change_password(session, user, args.password)
        except user_manager.UserManagementError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(1)
        print(f"Password reset for user '{args.username}'")


def _normalize_scan_roots(roots: Iterable[str]) -> list[Path]:
    """Resolve configured scan roots to absolute paths."""
    normalized = []
    for root in roots or []:
        path = Path(root).expanduser().resolve()
        if path.exists() and path.is_dir():
            normalized.append(path)
    return normalized


def _validate_import_path(config, path: Path) -> Path:
    """Validate and resolve an import path against configured scan roots."""
    resolved = path.expanduser().resolve()
    if not resolved.exists():
        raise ValueError(f"Path does not exist: {resolved}")
    if not resolved.is_dir():
        raise ValueError(f"Path is not a directory: {resolved}")

    scan_roots = _normalize_scan_roots(config.imports.scan_roots)
    if not scan_roots:
        raise ValueError("No configured scan roots; configure one before importing")

    for root in scan_roots:
        try:
            resolved.relative_to(root)
            return resolved
        except ValueError:
            continue
    raise ValueError(f"Path is not within any configured scan root: {resolved}")


def _count_audio_files(path: Path) -> int:
    """Count audio files under the given path."""
    return sum(
        1 for file_path in path.rglob("*") if file_path.is_file() and file_path.suffix.lower() in _AUDIO_EXTENSIONS
    )


async def _handle_import_dir(args):
    config = load_config([])
    try:
        resolved = _validate_import_path(config, Path(args.path))
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    owner_id = None
    if args.owner:
        async with get_session() as session:
            user = await get_user_by_username(session, args.owner)
            if not user:
                print(f"Error: owner '{args.owner}' not found", file=sys.stderr)
                sys.exit(1)
            owner_id = user.id

    # Try to enqueue the task; fall back to a synchronous scan if the broker is
    # unavailable, giving the admin an immediate count and a clear next step.
    try:
        result = scan_directory.delay(str(resolved), args.library_id, owner_id)  # type: ignore
        print(f"Import queued with task id: {result.id}")
    except (KombuOperationalError, OSError):
        try:
            count = scan_directory(str(resolved), args.library_id, owner_id)
            print(f"Enqueued {count} file(s) for import")
        except ValueError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(1)
        except (KombuOperationalError, OSError):
            count = _count_audio_files(resolved)
            print(f"Found {count} file(s) to import")
            print(
                "Celery broker is not available; start the worker to process the queue.",
                file=sys.stderr,
            )


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


async def _handle_provision_federation_keys(*_):
    """Back-fill ActivityPub actor URLs and keypairs for existing users."""
    from sqlalchemy import or_, select

    config = load_config([])
    if not config.federation.enabled or not config.federation.instance_domain:
        print("Federation is not enabled or no instance domain is configured; nothing to do.")
        return

    domain = config.federation.instance_domain
    batch_size = 50
    total = 0

    async with get_session() as session:
        stmt = (
            select(User)
            .where(
                or_(
                    User.actor_url.is_(None),
                    User.private_key_pem.is_(None),
                    User.public_key_pem.is_(None),
                )
            )
            .order_by(User.created_at)
        )
        result = await session.execute(stmt)
        users = result.scalars().all()

        for user in users:
            if ensure_user_actor(user, config):
                total += 1
                if total % batch_size == 0:
                    await session.flush()

        await session.commit()

    print(f"Provisioned federation keys for {total} user(s) on {domain}")


def admin_main(argv=None):
    """Entry point for admin CLI commands."""
    parser = _create_admin_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        sys.exit(1)

    # If no auth secret is configured, try to load the one persisted by the
    # Docker entrypoint so that `docker compose exec ... songhive admin ...`
    # works without an explicit SONGHIVE_AUTH__SECRET_KEY variable.
    if not os.environ.get("SONGHIVE_AUTH__SECRET_KEY"):
        secret_file = Path(os.environ.get("SONGHIVE_SECRET_FILE", "/data/secret_key"))
        if secret_file.exists():
            os.environ["SONGHIVE_AUTH__SECRET_KEY"] = secret_file.read_text().strip()

    # Load config and init DB
    config = load_config([])
    init_db(config.database.url)

    handlers = {
        "init-db": _handle_init_db,
        "create-user": _handle_create_user,
        "promote-user": _handle_promote_user,
        "demote-user": _handle_demote_user,
        "approve-user": _handle_approve_user,
        "disable-user": _handle_disable_user,
        "reset-password": _handle_reset_password,
        "import-dir": _handle_import_dir,
        "create-invite": _handle_create_invite,
        "list-invites": _handle_list_invites,
        "provision-federation-keys": _handle_provision_federation_keys,
    }

    handler = handlers.get(args.command)
    if handler:
        asyncio.run(handler(args))
    else:
        parser.print_help()
        sys.exit(1)
