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
from sqlalchemy import delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..config.schema import RegistrationMode, SonghiveConfig
from ..models.user import User, UserRole
from ..models.user_link import UserLink
from ..services.auth import (
    get_user_by_email_verification_token,
    get_user_by_id,
    get_user_by_password_reset_token,
    get_user_by_username_or_email,
    hash_password,
)
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


async def delete_user(session: AsyncSession, user_id: str) -> None:
    """Delete a user account and all dependent data."""
    user = await _get_user_or_raise(session, user_id)
    if user.role == UserRole.ADMIN and user.is_active and await _active_admin_count(session) <= 1:
        raise UserManagementError("Cannot delete the last active admin", 400)
    await session.delete(user)
    await session.flush()


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
