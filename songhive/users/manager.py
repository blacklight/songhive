"""
User lifecycle management.
"""

import re
import secrets
from typing import Any, Dict, List, Optional, cast

from pydantic import EmailStr, TypeAdapter, ValidationError
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..config.schema import RegistrationMode, SonghiveConfig
from ..models.user import User, UserRole
from ..models.user_link import UserLink
from ..services.auth import get_user_by_id, hash_password

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


async def _validate_invite_code(_session: AsyncSession, _invite_code: Optional[str]) -> bool:
    """
    Placeholder invite-code validation.

    Section 12 (Invite model and invite service) will replace this with a real
    check against persisted invite codes. Until then, no invite code is valid.
    """
    return False


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
        raise RegistrationError("Registration is closed")

    if config.auth.registration_mode == RegistrationMode.INVITE_ONLY and (
        not invite_code or not await _validate_invite_code(session, invite_code)
    ):
        raise RegistrationError("Invalid or missing invite code")

    username = _normalize_username(username)
    email = _normalize_and_validate_email(email)
    if display_name is not None:
        display_name = display_name.strip() or None

    _validate_registration_input(username, email, password, display_name)
    await _check_for_duplicates(session, username, email)

    is_active = True
    email_verified = True
    email_verification_token: Optional[str] = None

    if config.auth.registration_mode == RegistrationMode.APPROVAL_REQUIRED:
        is_active = False

    if config.auth.require_email_verification:
        is_active = False
        email_verified = False
        email_verification_token = secrets.token_urlsafe(32)

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
    """Update a user's profile and replace their links if supplied.

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


async def list_users(session: AsyncSession, limit: int = 100, offset: int = 0) -> List[User]:
    """List users ordered by creation date (newest first)."""
    result = await session.execute(select(User).order_by(User.created_at.desc()).offset(offset).limit(limit))
    return list(result.scalars().all())


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


async def deactivate_user_by_id(session: AsyncSession, user_id: str) -> User:
    """Deactivate a user account, guarding the last active admin."""
    user = await _get_user_or_raise(session, user_id)
    if user.role == UserRole.ADMIN and user.is_active and await _active_admin_count(session) <= 1:
        raise UserManagementError("Cannot deactivate the last active admin", 400)
    user.is_active = False
    await session.flush()
    return user
