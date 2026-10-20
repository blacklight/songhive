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
from ..federation import doc_cache
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
from ..services.storage import StorageService, is_unique_constraint_error
from ..users.invites import get_invite, is_invite_valid
from ..users.tokens import revoke_all_user_refresh_tokens
from .deletion_helpers import delete_user_content
from .deletion_helpers import delete_user_content as _delete_user_content
from .deletion_helpers import remove_user_references
from .deletion_helpers import remove_user_references as _remove_user_references

# Re-exported for backwards-compatible imports (moved to deletion_helpers).
__all__ = ["_delete_user_content", "_remove_user_references"]

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

    # Approval-required and paid-registration users start inactive; every
    # other mode is active. Paid users also carry ``payments_required`` so the
    # membership sync keeps them inactive until the provider confirms payment.
    paid_mode = config.auth.registration_mode == RegistrationMode.PAID
    is_active = config.auth.registration_mode not in (
        RegistrationMode.APPROVAL_REQUIRED,
        RegistrationMode.PAID,
    )
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
        payments_required=paid_mode,
    )
    ensure_user_actor(user, config)
    session.add(user)
    try:
        await session.flush()
    except IntegrityError as exc:
        if is_unique_constraint_error(exc):
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

    if "status_content_type" in updates:
        status_content_type = updates["status_content_type"]
        if status_content_type:
            user.status_content_type = status_content_type

    if "preview_cards_enabled" in updates:
        preview_cards_enabled = updates["preview_cards_enabled"]
        if preview_cards_enabled is not None:
            user.preview_cards_enabled = bool(preview_cards_enabled)

    if "profile_visibility" in updates:
        profile_visibility = updates["profile_visibility"]
        if profile_visibility:
            user.profile_visibility = profile_visibility

    if "followers_approval" in updates:
        followers_approval = updates["followers_approval"]
        if followers_approval:
            user.followers_approval = followers_approval

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


async def approve_user(
    session: AsyncSession,
    user_id: str,
    config: Optional[SonghiveConfig] = None,
) -> User:
    """
    Approve a user by activating their account.

    The effective flag goes through ``sync_user_active_flag`` so approving a
    ``payments_required`` account without a live subscription leaves it
    inactive — payment must come from the provider, not the approval.
    """
    from ..services.payments import membership

    user = await _get_user_or_raise(session, user_id)
    user.admin_suspended = False
    await membership.sync_user_active_flag(session, user, config=config)
    return user


async def activate_user(
    session: AsyncSession,
    user_id: str,
    config: Optional[SonghiveConfig] = None,
) -> User:
    """Activate (re-enable) a user account, honoring unpaid memberships."""
    return await approve_user(session, user_id, config=config)


async def deactivate_user_by_id(
    session: AsyncSession,
    user_id: str,
    redis: Optional[Redis] = None,
    access_token_ttl: int = 0,
    config: Optional[SonghiveConfig] = None,
) -> User:
    """Deactivate a user account, guarding the last active admin."""
    from ..services.payments import membership

    user = await _get_user_or_raise(session, user_id)
    if user.role == UserRole.ADMIN and user.is_active and await _active_admin_count(session) <= 1:
        raise UserManagementError("Cannot deactivate the last active admin", 400)
    # An explicit deactivation must also stop billing: durable cancel intent is
    # recorded first so a failure leaves a retryable task, not a paid ghost.
    if config is not None:
        await membership.cancel_for_deactivated_user(session, user, config)
    user.admin_suspended = True
    user.is_active = False
    await session.flush()
    if redis is not None:
        await revoke_all_user_refresh_tokens(redis, user.id, access_token_ttl)
    return user


DELETE_ACCOUNT_CONFIRMATION = "Yes, I really want to delete my account"


async def delete_user(
    session: AsyncSession,
    user_id: str,
    *,
    recursive: bool = False,
    storage: Optional[StorageService] = None,
    config: Optional[SonghiveConfig] = None,
) -> List[deletion.UnpublishInfo]:
    """Delete a user account and, optionally, all content created by the user."""
    from ..services.payments import membership

    user = await _get_user_or_raise(session, user_id)
    if user.role == UserRole.ADMIN and user.is_active and await _active_admin_count(session) <= 1:
        raise UserManagementError("Cannot delete the last active admin", 400)

    # Billing must stop before the row goes away. When the provider call
    # cannot be confirmed the intent stays durable (a retry task owns it) and
    # the delete is refused — a repeat call once it lands proceeds normally.
    if config is not None and not await membership.cancel_for_deactivated_user(session, user, config):
        raise UserManagementError(
            "Provider subscription cancellation is pending; retry once it confirms",
            409,
        )

    unpublish: List[deletion.UnpublishInfo] = []

    if recursive:
        if storage is None:
            raise UserManagementError("Storage service is required for recursive deletion", 500)
        unpublish.extend(await delete_user_content(session, storage, user))

    # The deleted user's federated documents must stop resolving at once.
    # Track/activity/entity documents are not keyed by username, so the
    # whole cache is dropped once the delete commits.
    await remove_user_references(session, user)
    await session.delete(user)
    await session.flush()

    doc_cache.clear(session=session)
    return unpublish


async def verify_email(session: AsyncSession, token: str) -> User | None:
    """
    Verify a user's email address using the raw verification token.

    On success the user's ``email_verified`` is set to ``True`` and the
    ``email_verification_token`` is cleared. Approval-required accounts stay
    inactive; ``payments_required`` accounts re-sync ``is_active`` so a
    verified + paid user activates immediately.
    """
    user = await get_user_by_email_verification_token(session, token)
    if user is None:
        return None

    user.email_verified = True
    user.email_verification_token = None
    await session.flush()

    # A payments_required account may activate right here: verification is the
    # last gate once the provider has confirmed payment.
    if user.payments_required:
        from ..services.payments import membership

        await membership.sync_user_active_flag(session, user)
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
    if user is None or user.email_verified:
        return None, None
    # Inactive unpaid members still need the verification email — it is the
    # first step toward paying; suspended users get nothing.
    if not user.is_active and (user.admin_suspended or not user.payments_required):
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
    access_token_ttl: int = 0,
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
    await revoke_all_user_refresh_tokens(redis, user.id, access_token_ttl)
    return True
