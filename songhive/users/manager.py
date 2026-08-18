"""
User lifecycle management.
"""

import re
import secrets
from typing import Optional

from pydantic import EmailStr, TypeAdapter, ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..config.schema import RegistrationMode, SonghiveConfig
from ..models.user import User, UserRole
from ..services.auth import hash_password

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
        return _EMAIL_VALIDATOR.validate_python(email).lower()
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
