"""
Admin CLI commands.

Usage:
    songhive admin init-db
    songhive admin migrate
    songhive admin create-user --username <name> --email <email> --password <pw> [--admin | --role <role>]
    songhive admin promote-user --username <name>
    songhive admin demote-user --username <name>
    songhive admin approve-user --username <name>
    songhive admin disable-user --username <name>
    songhive admin reset-password --username <name> --password <pw>
    songhive admin import-dir --path <dir> --library-id <uuid> [--owner <username>]
    songhive admin create-invite --created-by <username> [--max-uses <n>] [--expires-at <iso>]
    songhive admin list-invites
    songhive admin provision-federation-keys
    songhive admin rehash-audio [--dry-run]
    songhive admin sync-tags \
        (--track-id <id> | --album-id <id> | --artist-id <id> | --library-id <id> | --all) [--dry-run]
"""

import argparse
import asyncio
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import aiofiles
import aiofiles.os
from kombu.exceptions import OperationalError as KombuOperationalError
from sqlalchemy import select, update

from ..config import load_config
from ..migrations import ensure_migrated
from ..models.base import get_session, init_db
from ..models.stored_file import StoredFile
from ..models.track import Track
from ..models.transcoded_file import TranscodedFile
from ..models.upload import Upload
from ..models.user import User, UserRole
from ..services import music
from ..services.auth import create_user, get_user_by_username
from ..services.federation import ensure_user_actor
from ..services.storage import StorageService, audio_hash
from ..storage import get_storage
from ..storage.s3 import S3Storage
from ..tasks.import_ import _AUDIO_EXTENSIONS, scan_directory
from ..users import manager as user_manager
from ..users.invites import InviteError, create_invite, list_invites


def _create_admin_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="songhive admin")
    subparsers = parser.add_subparsers(dest="command")

    # init-db
    subparsers.add_parser("init-db", help="Create database tables if they do not exist")

    # migrate
    subparsers.add_parser("migrate", help="Run Alembic database migrations")

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

    # rehash-audio
    rehash_parser = subparsers.add_parser(
        "rehash-audio",
        help="Migrate audio StoredFile rows to audio-only SHA-256 hashes",
    )
    rehash_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report files that would migrate without making changes",
    )

    # sync-tags
    sync_tags_parser = subparsers.add_parser(
        "sync-tags",
        help="Enqueue tag sync for one or more tracks",
    )
    sync_tags_group = sync_tags_parser.add_mutually_exclusive_group(required=True)
    sync_tags_group.add_argument("--track-id", help="Sync a single track by ID")
    sync_tags_group.add_argument("--album-id", help="Sync all tracks in an album")
    sync_tags_group.add_argument("--artist-id", help="Sync all tracks by an artist")
    sync_tags_group.add_argument("--library-id", help="Sync all tracks in a library")
    sync_tags_group.add_argument("--all", action="store_true", help="Sync all tracks")
    sync_tags_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the number of tracks that would be queued without enqueuing",
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
    config = load_config([])
    ensure_migrated(config.database.url)
    print("Database tables initialized successfully")


async def _handle_migrate(*_, **__):
    config = load_config([])
    ensure_migrated(config.database.url)
    print("Migrations applied successfully")


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


async def _merge_stored_file(session, storage, duplicate: StoredFile, survivor: StoredFile) -> None:
    """Point all known references to ``survivor`` and remove ``duplicate``."""
    duplicate_id = str(duplicate.id)
    survivor_id = str(survivor.id)

    await session.execute(update(Track).where(Track.audio_file_id == duplicate_id).values(audio_file_id=survivor_id))
    await session.execute(
        update(Upload).where(Upload.stored_file_id == duplicate_id).values(stored_file_id=survivor_id)
    )
    await session.execute(
        update(TranscodedFile).where(TranscodedFile.stored_file_id == duplicate_id).values(stored_file_id=survivor_id)
    )

    old_path = duplicate.storage_path
    await session.delete(duplicate)
    await session.flush()
    try:
        await storage.delete(old_path)
    except Exception:
        pass


async def _handle_rehash_audio(args):
    """Migrate existing audio StoredFile rows to audio-only hashes."""
    config = load_config([])
    init_db(config.database.url)

    storage = get_storage(config.storage)
    storage_service = StorageService(storage, config.storage)

    migrated = 0
    merged = 0
    skipped = 0
    failed = 0

    async with get_session() as session:
        result = await session.execute(select(StoredFile).where(StoredFile.content_type.ilike("audio/%")))
        rows = list(result.scalars().all())

        for stored_file in rows:
            local_path = await storage_service.backend.retrieve(stored_file.storage_path)
            if local_path is None:
                failed += 1
                continue

            try:
                new_hash = await audio_hash(local_path)
            except RuntimeError:
                failed += 1
                continue

            if new_hash == stored_file.sha256:
                skipped += 1
                continue

            if args.dry_run:
                migrated += 1
                continue

            existing = await session.scalar(select(StoredFile).where(StoredFile.sha256 == new_hash))
            if existing is not None:
                await _merge_stored_file(session, storage, stored_file, existing)
                merged += 1
            else:
                prefix = stored_file.storage_path.split("/")[0] if "/" in stored_file.storage_path else "files"
                new_path = f"{prefix}/{new_hash[:2]}/{new_hash[2:4]}/{new_hash}"

                try:
                    size = (await asyncio.to_thread(os.stat, local_path)).st_size
                    with open(local_path, "rb") as f:
                        await storage_service.backend.store(f, new_path, content_type=stored_file.content_type)
                except Exception:
                    failed += 1
                    continue

                old_path = stored_file.storage_path
                stored_file.storage_path = new_path
                stored_file.sha256 = new_hash
                stored_file.size = size

                try:
                    await storage_service.backend.delete(old_path)
                except Exception:
                    pass

                if isinstance(storage_service.backend, S3Storage):
                    try:
                        await aiofiles.os.remove(local_path)
                    except Exception:
                        pass

                migrated += 1

        await session.commit()

    if args.dry_run:
        print(
            f"Would rehash {migrated} audio file(s), {skipped} already audio-hashed, "
            f"{failed} failed, {merged} would merge."
        )
    else:
        print(
            f"Rehashed {migrated} audio file(s), merged {merged} duplicate(s), "
            f"{skipped} already audio-hashed, {failed} failed."
        )


async def _handle_sync_tags(args):
    """Enqueue tag sync for the requested scope of tracks."""
    from ..tasks.tags import sync_track_tags

    config = load_config([])
    init_db(config.database.url)

    admin_user = User(role="admin")

    async with get_session() as session:
        track_ids = await music.resolve_track_ids_for_sync(
            session,
            track_id=args.track_id,
            album_id=args.album_id,
            artist_id=args.artist_id,
            library_id=args.library_id,
            all_=args.all,
            user=admin_user,
        )

    if not track_ids:
        print("No matching tracks found.")
        return

    if args.dry_run:
        print(f"Would enqueue tag sync for {len(track_ids)} track(s).")
        return

    try:
        for track_id in track_ids:
            sync_track_tags.delay(track_id)  # type: ignore
    except (KombuOperationalError, OSError):
        print(f"Found {len(track_ids)} track(s) to sync.")
        print("Celery broker is not available; start the worker to process the queue.", file=sys.stderr)
        return

    print(f"Enqueued tag sync for {len(track_ids)} track(s).")


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
        "migrate": _handle_migrate,
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
        "rehash-audio": _handle_rehash_audio,
        "sync-tags": _handle_sync_tags,
    }

    handler = handlers.get(args.command)
    if handler:
        asyncio.run(handler(args))
    else:
        parser.print_help()
        sys.exit(1)
