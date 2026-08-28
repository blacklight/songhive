"""
User lifecycle management.
"""

import hashlib
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple, cast

from pydantic import EmailStr, TypeAdapter, ValidationError
from redis.asyncio import Redis
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..config.schema import RegistrationMode, SonghiveConfig
from ..models.album import Album
from ..models.api_token import ApiToken
from ..models.audit_log import AuditLog
from ..models.favorite import Favorite
from ..models.history import ListeningHistory
from ..models.invite import Invite
from ..models.library import Library
from ..models.library_track import LibraryTrack
from ..models.oauth_client import OAuth2Client
from ..models.playlist import Playlist
from ..models.radio import Radio
from ..models.report import Report
from ..models.setting import Setting
from ..models.share_grant import ShareGrant
from ..models.share_token import ShareToken
from ..models.stored_file import StoredFile
from ..models.track import Track
from ..models.user import User, UserRole
from ..models.user_link import UserLink
from ..services import deletion
from ..services.auth import (
    get_user_by_email_verification_token,
    get_user_by_id,
    get_user_by_password_reset_token,
    get_user_by_username_or_email,
    hash_password,
    verify_password,
)
from ..services.federation import ensure_user_actor
from ..services.storage import StorageService
from ..users.invites import get_invite, is_invite_valid
from ..users.tokens import revoke_all_user_refresh_tokens

MAX_USERNAME_LENGTH = 64
MAX_EMAIL_LENGTH = 254
# bcrypt silently truncates passwords at 72 bytes; reject longer inputs.
MAX_PASSWORD_BYTES = 72
USERNAME_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")

_EMAIL_VALIDATOR = TypeAdapter(EmailStr)


class RegistrationError(ValueError):
    """Raised when a registration request cannot be fulfilled."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


class UserManagementError(ValueError):
    """Raised when an administrative user-management action cannot be fulfilled."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


class PasswordChangeError(ValueError):
    """Raised when a password change request cannot be fulfilled."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def _normalize_username(username: str) -> str:
    """Strip and lower-case a username."""
    return username.strip().lower()


def _normalize_and_validate_email(email: str) -> str:
    """Validate, strip and lower-case an email address."""
    try:
        return cast(str, _EMAIL_VALIDATOR.validate_python(email)).lower()
    except ValidationError as exc:
        raise RegistrationError("Invalid email address") from exc


def _validate_registration_input(
    username: str,
    email: str,
    password: str,
    display_name: Optional[str],
) -> None:
    """Validate the raw registration input, raising RegistrationError on failure."""
    if not username:
        raise RegistrationError("Username is required")
    if len(username) > MAX_USERNAME_LENGTH:
        raise RegistrationError("Username is too long")
    if not USERNAME_PATTERN.match(username):
        raise RegistrationError("Username contains invalid characters")

    if not email:
        raise RegistrationError("Email is required")
    if len(email) > MAX_EMAIL_LENGTH:
        raise RegistrationError("Email is too long")

    if not password:
        raise RegistrationError("Password is required")
    if len(password.encode("utf-8")) > MAX_PASSWORD_BYTES:
        raise RegistrationError("Password is too long")

    if display_name is not None and len(display_name.strip()) > 128:
        raise RegistrationError("Display name is too long")


def _is_unique_constraint_error(exc: IntegrityError) -> bool:
    """Return True if an IntegrityError is a uniqueness constraint violation."""
    cause = getattr(exc, "orig", None)
    message = str(cause) if cause is not None else str(exc)
    return "unique" in message.lower()


async def _check_for_duplicates(session: AsyncSession, username: str, email: str) -> None:
    """Ensure the requested username and email are not already taken."""
    existing_username = (
        await session.execute(select(User).where(func.lower(User.username) == username.lower()))
    ).scalar_one_or_none()
    if existing_username is not None:
        raise RegistrationError("Username already taken", status_code=409)

    existing_email = (
        await session.execute(select(User).where(func.lower(User.email) == email.lower()))
    ).scalar_one_or_none()
    if existing_email is not None:
        raise RegistrationError("Email already taken", status_code=409)


async def register_user(
    session: AsyncSession,
    config: SonghiveConfig,
    username: str,
    email: str,
    password: str,
    display_name: Optional[str] = None,
    invite_code: Optional[str] = None,
) -> User:
    """
    Register a new user according to ``config.auth.registration_mode``.

    :raises RegistrationError: If the registration mode is closed, the input is
        invalid, the username/email is already taken, or an invite code is
        required but missing/invalid.
    """
    if config.auth.registration_mode == RegistrationMode.CLOSED:
        raise RegistrationError("Registration is closed", status_code=403)

    invite = None
    if config.auth.registration_mode == RegistrationMode.INVITE_ONLY:
        if not invite_code:
            raise RegistrationError("Invalid or missing invite code")
        invite = await get_invite(session, invite_code)
        if invite is None or not is_invite_valid(invite):
            raise RegistrationError("Invalid or missing invite code")

    username = _normalize_username(username)
    email = _normalize_and_validate_email(email)
    if display_name is not None:
        display_name = display_name.strip() or None

    _validate_registration_input(username, email, password, display_name)
    await _check_for_duplicates(session, username, email)

    if invite is not None:
        invite.uses += 1

    # Approval-required users start inactive; every other mode is active.
    is_active = config.auth.registration_mode != RegistrationMode.APPROVAL_REQUIRED
    email_verified = not config.auth.require_email_verification
    email_verification_token: Optional[str] = None
    email_verification_token_raw: Optional[str] = None

    if config.auth.require_email_verification:
        email_verified = False
        email_verification_token_raw = secrets.token_urlsafe(32)
        email_verification_token = hashlib.sha256(email_verification_token_raw.encode("utf-8")).hexdigest()

    user = User(
        username=username,
        email=email,
        password_hash=hash_password(password),
        display_name=display_name,
        role=UserRole.USER,
        is_active=is_active,
        email_verified=email_verified,
        email_verification_token=email_verification_token,
    )
    ensure_user_actor(user, config)
    session.add(user)
    try:
        await session.flush()
    except IntegrityError as exc:
        if _is_unique_constraint_error(exc):
            await session.rollback()
            raise RegistrationError("Username or email already taken", status_code=409) from exc
        raise

    if config.auth.require_email_verification:
        user.email_verification_token_raw = email_verification_token_raw

    return user


async def change_password(session: AsyncSession, user: User, new_password: str) -> None:
    """Change a user's password."""
    user.password_hash = hash_password(new_password)
    await session.flush()


async def change_user_password(
    session: AsyncSession,
    user: User,
    current_password: str,
    new_password: str,
) -> None:
    """Change a user's password after verifying their current password."""
    if not current_password or not new_password:
        raise PasswordChangeError("Current and new password are required")
    if len(new_password.encode("utf-8")) > MAX_PASSWORD_BYTES:
        raise PasswordChangeError("Password is too long")
    if not verify_password(current_password, user.password_hash):
        raise PasswordChangeError("Current password is incorrect")

    user.password_hash = hash_password(new_password)
    await session.flush()


async def deactivate_user(session: AsyncSession, user: User) -> None:
    """Deactivate a user account."""
    user.is_active = False
    await session.flush()


async def update_profile(session: AsyncSession, user: User, updates: Dict[str, Any]) -> User:
    """
    Update a user's profile and replace their links if supplied.

    ``updates`` should only contain keys the caller explicitly intends to
    change. A missing key leaves the corresponding field untouched. A value of
    ``None`` or an empty/whitespace-only string clears the field.
    """
    if "display_name" in updates:
        display_name = updates["display_name"]
        user.display_name = (display_name or "").strip() or None

    if "bio" in updates:
        bio = updates["bio"]
        user.bio = (bio or "").strip() or None

    if "avatar_url" in updates:
        avatar_url = updates["avatar_url"]
        user.avatar_url = (avatar_url or "").strip() or None

    if "links" in updates:
        links = updates["links"] or []
        await session.execute(delete(UserLink).where(UserLink.user_id == user.id))
        for link in links:
            link.user_id = user.id
            session.add(link)
        await session.refresh(user, attribute_names=["links"])

    await session.flush()
    return user


async def _get_user_or_raise(session: AsyncSession, user_id: str) -> User:
    """Fetch a user by id or raise a ``UserManagementError``."""
    user = await get_user_by_id(session, user_id)
    if user is None:
        raise UserManagementError("User not found", 404)
    return user


async def _active_admin_count(session: AsyncSession) -> int:
    """Return the number of currently active admin users."""
    result = await session.execute(
        select(func.count(User.id)).where(
            User.is_active.is_(True),
            User.role == UserRole.ADMIN,
        )
    )
    return result.scalar() or 0


async def count_users(session: AsyncSession) -> int:
    """Return the total number of users."""
    result = await session.execute(select(func.count(User.id)))
    return result.scalar() or 0


async def list_users(session: AsyncSession, limit: int = 100, offset: int = 0) -> List[User]:
    """List users ordered by creation date (newest first)."""
    result = await session.execute(select(User).order_by(User.created_at.desc()).offset(offset).limit(limit))
    return list(result.scalars().all())


async def search_users(
    session: AsyncSession,
    query: str,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[User], int]:
    """Search users by username or email (case-insensitive partial match)."""
    pattern = f"%{query.strip().lower()}%"
    where_clause = or_(
        func.lower(User.username).like(pattern),
        func.lower(User.email).like(pattern),
    )
    stmt = select(User).where(where_clause).order_by(User.created_at.desc())
    total = await session.execute(select(func.count(User.id)).where(where_clause))
    total_count = total.scalar() or 0
    stmt = stmt.offset(offset).limit(limit)
    result = await session.execute(stmt)
    return list(result.scalars().all()), total_count


async def promote_user(session: AsyncSession, user_id: str) -> User:
    """Promote a user to the admin role."""
    user = await _get_user_or_raise(session, user_id)
    user.role = UserRole.ADMIN
    await session.flush()
    return user


async def demote_user(session: AsyncSession, user_id: str) -> User:
    """Demote a user to the user role, guarding the last active admin."""
    user = await _get_user_or_raise(session, user_id)
    if user.role == UserRole.ADMIN and await _active_admin_count(session) <= 1:
        raise UserManagementError("Cannot demote the last active admin", 400)
    user.role = UserRole.USER
    await session.flush()
    return user


async def approve_user(session: AsyncSession, user_id: str) -> User:
    """Approve a user by activating their account."""
    user = await _get_user_or_raise(session, user_id)
    user.is_active = True
    await session.flush()
    return user


async def activate_user(session: AsyncSession, user_id: str) -> User:
    """Activate (re-enable) a user account."""
    user = await _get_user_or_raise(session, user_id)
    user.is_active = True
    await session.flush()
    return user


async def deactivate_user_by_id(
    session: AsyncSession,
    user_id: str,
    redis: Optional[Redis] = None,
) -> User:
    """Deactivate a user account, guarding the last active admin."""
    user = await _get_user_or_raise(session, user_id)
    if user.role == UserRole.ADMIN and user.is_active and await _active_admin_count(session) <= 1:
        raise UserManagementError("Cannot deactivate the last active admin", 400)
    user.is_active = False
    await session.flush()
    if redis is not None:
        await revoke_all_user_refresh_tokens(redis, user.id)
    return user


DELETE_ACCOUNT_CONFIRMATION = "Yes, I really want to delete my account"


async def _remove_user_references(session: AsyncSession, user: User) -> None:
    """Remove or nullify all database references to a user before deletion.

    This ensures the user row can be deleted even on databases (such as
    SQLite without foreign-key enforcement) that do not automatically apply
    ``ON DELETE`` actions.
    """
    # Remove rows that cannot exist without a referencing user.
    await session.execute(delete(ApiToken).where(ApiToken.user_id == user.id))
    await session.execute(delete(Favorite).where(Favorite.user_id == user.id))
    await session.execute(delete(ListeningHistory).where(ListeningHistory.user_id == user.id))
    await session.execute(delete(Invite).where(Invite.created_by == user.id))
    await session.execute(delete(Report).where(Report.reporter_id == user.id))
    await session.execute(delete(ShareGrant).where(ShareGrant.user_id == user.id))
    await session.execute(delete(ShareGrant).where(ShareGrant.created_by == user.id))
    await session.execute(delete(ShareToken).where(ShareToken.created_by == user.id))
    await session.execute(delete(UserLink).where(UserLink.user_id == user.id))

    # Nullify optional owner/reviewer fields on user-created content.
    await session.execute(update(Album).where(Album.owner_id == user.id).values(owner_id=None))
    await session.execute(update(AuditLog).where(AuditLog.actor_id == user.id).values(actor_id=None))
    await session.execute(update(Library).where(Library.owner_id == user.id).values(owner_id=None))
    await session.execute(update(LibraryTrack).where(LibraryTrack.added_by_id == user.id).values(added_by_id=None))
    await session.execute(update(OAuth2Client).where(OAuth2Client.owner_id == user.id).values(owner_id=None))
    await session.execute(update(Playlist).where(Playlist.owner_id == user.id).values(owner_id=None))
    await session.execute(update(Radio).where(Radio.owner_id == user.id).values(owner_id=None))
    await session.execute(update(Report).where(Report.reviewed_by == user.id).values(reviewed_by=None))
    await session.execute(update(Setting).where(Setting.updated_by == user.id).values(updated_by=None))
    await session.execute(update(StoredFile).where(StoredFile.owner_id == user.id).values(owner_id=None))
    await session.execute(update(Track).where(Track.owner_id == user.id).values(owner_id=None))


async def delete_user(
    session: AsyncSession,
    user_id: str,
    *,
    recursive: bool = False,
    storage: Optional[StorageService] = None,
) -> List[deletion.UnpublishInfo]:
    """Delete a user account and, optionally, all content created by the user."""
    user = await _get_user_or_raise(session, user_id)
    if user.role == UserRole.ADMIN and user.is_active and await _active_admin_count(session) <= 1:
        raise UserManagementError("Cannot delete the last active admin", 400)

    unpublish: List[deletion.UnpublishInfo] = []

    if recursive:
        if storage is None:
            raise UserManagementError("Storage service is required for recursive deletion", 500)

        # Remove the user's radios first; they reference the user directly.
        await session.execute(delete(Radio).where(Radio.owner_id == user.id))

        # Remove user-owned playlists and libraries without deleting their tracks.
        playlist_ids = list(
            (await session.execute(select(Playlist.id).where(Playlist.owner_id == user.id))).scalars().all()
        )
        for playlist_id in playlist_ids:
            try:
                await deletion.delete_playlist(session, storage, str(playlist_id), recursive=False)
            except deletion.DeletionError as exc:
                if exc.status_code != 404:
                    raise

        library_ids = list(
            (await session.execute(select(Library.id).where(Library.owner_id == user.id))).scalars().all()
        )
        for library_id in library_ids:
            try:
                await deletion.delete_library(session, storage, str(library_id), recursive=False)
            except deletion.DeletionError as exc:
                if exc.status_code != 404:
                    raise

        # Delete all tracks owned by the user.
        track_ids = list((await session.execute(select(Track.id).where(Track.owner_id == user.id))).scalars().all())
        if track_ids:
            track_unpublish, _ = await deletion.delete_tracks_bulk(
                session,
                storage,
                [str(track_id) for track_id in track_ids],
                user=user,
            )
            unpublish.extend(track_unpublish)

        # Delete any remaining albums owned by the user.
        album_ids = list((await session.execute(select(Album.id).where(Album.owner_id == user.id))).scalars().all())
        for album_id in album_ids:
            try:
                await deletion.delete_album(
                    session,
                    storage,
                    str(album_id),
                    recursive=False,
                    user=user,
                    is_admin=False,
                )
            except deletion.DeletionError as exc:
                if exc.status_code != 404:
                    raise

    await _remove_user_references(session, user)
    await session.delete(user)
    await session.flush()
    return unpublish


async def verify_email(session: AsyncSession, token: str) -> User | None:
    """
    Verify a user's email address using the raw verification token.

    On success the user's ``email_verified`` is set to ``True`` and the
    ``email_verification_token`` is cleared. The account ``is_active`` status
    is not modified, so approval-required accounts still require an admin
    approval step.
    """
    user = await get_user_by_email_verification_token(session, token)
    if user is None:
        return None

    user.email_verified = True
    user.email_verification_token = None
    await session.flush()
    return user


async def generate_verification_email_token(
    session: AsyncSession,
    username_or_email: str,
) -> Tuple[Optional[User], Optional[str]]:
    """
    Generate a fresh email-verification token for a user and return the raw token.

    The user must exist, be active, and not already have a verified email
    address. The previous verification token, if any, is replaced.

    If no eligible user is found, ``(None, None)`` is returned so callers can
    return a generic success response without leaking account status.
    """
    user = await get_user_by_username_or_email(session, username_or_email)
    if user is None or not user.is_active or user.email_verified:
        return None, None

    raw_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    user.email_verification_token = token_hash
    await session.flush()
    return user, raw_token


async def request_password_reset(
    session: AsyncSession,
    config: SonghiveConfig,
    username_or_email: str,
) -> Tuple[Optional[User], Optional[str]]:
    """
    Generate a single-use password-reset token for a user if one exists.

    The raw token is returned so a caller can send it to the user; only a
    SHA-256 hash of the token is stored in the database.
    """
    user = await get_user_by_username_or_email(session, username_or_email)
    if user is None:
        return None, None

    raw_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    user.password_reset_token = token_hash
    user.password_reset_expires_at = datetime.now(timezone.utc) + timedelta(
        minutes=config.auth.password_reset_token_expiry_minutes
    )
    await session.flush()
    return user, raw_token


async def confirm_password_reset(
    session: AsyncSession,
    redis: Redis,
    token: str,
    new_password: str,
) -> bool:
    """
    Set a new password using a reset token.

    Returns ``True`` if the token is valid and not expired, otherwise ``False``.
    The token and expiry are cleared on success, and all active refresh tokens
    for the user are revoked as a security hardening measure.
    """
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    user = await get_user_by_password_reset_token(session, token_hash)
    if user is None or user.password_reset_expires_at is None:
        return False

    now = datetime.now(timezone.utc)
    expires_at = user.password_reset_expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    else:
        expires_at = expires_at.astimezone(timezone.utc)

    if now > expires_at:
        return False

    user.password_hash = hash_password(new_password)
    user.password_reset_token = None
    user.password_reset_expires_at = None
    await session.flush()
    await revoke_all_user_refresh_tokens(redis, user.id)
    return True
