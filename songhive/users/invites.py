"""
Invite code service.

Provides helpers to create, validate, consume, and revoke invitation codes
used for invite-only registration.
"""

import secrets
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.invite import Invite

__all__ = [
    "InviteError",
    "create_invite",
    "get_invite",
    "is_invite_valid",
    "validate_invite",
    "consume_invite",
    "revoke_invite",
    "list_invites",
    "count_invites",
]


class InviteError(ValueError):
    """Raised when an invite code operation cannot be completed."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def _as_utc(value: datetime) -> datetime:
    """
    Return ``value`` as a timezone-aware UTC datetime.

    Naive datetimes are treated as UTC, which matches how SQLite and
    PostgreSQL store UTC values for ``DateTime(timezone=True)`` columns.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


async def create_invite(
    session: AsyncSession,
    created_by: str,
    max_uses: Optional[int] = None,
    expires_at: Optional[datetime] = None,
) -> Invite:
    """
    Create a new invite code.

    :param session: Database session.
    :param created_by: User id that created the invite.
    :param max_uses: Optional maximum number of uses.
    :param expires_at: Optional expiration datetime.
    :returns: The created invite.
    :raises InviteError: If the parameters are invalid or a unique code cannot
        be generated.
    """
    if max_uses is not None and max_uses <= 0:
        raise InviteError("max_uses must be a positive integer")

    now = datetime.now(timezone.utc)
    if expires_at is not None and _as_utc(expires_at) <= now:
        raise InviteError("expires_at must be in the future")

    for _ in range(10):
        code = secrets.token_urlsafe(32)
        existing = await get_invite(session, code)
        if existing is None:
            break
    else:
        raise InviteError("Could not generate a unique invite code")

    invite = Invite(
        code=code,
        created_by=created_by,
        max_uses=max_uses,
        uses=0,
        expires_at=expires_at,
    )
    session.add(invite)
    await session.flush()
    return invite


async def get_invite(session: AsyncSession, code: str) -> Optional[Invite]:
    """Fetch an invite by its raw code."""
    result = await session.execute(select(Invite).where(Invite.code == code))
    return result.scalar_one_or_none()


def is_invite_valid(invite: Optional[Invite]) -> bool:
    """
    Return ``True`` if the invite exists, is not expired, and is not exhausted.
    """
    if invite is None:
        return False

    if invite.expires_at is not None and _as_utc(invite.expires_at) <= datetime.now(timezone.utc):
        return False

    uses = invite.uses or 0
    if invite.max_uses is not None and uses >= invite.max_uses:
        return False

    return True


async def validate_invite(session: AsyncSession, code: str) -> bool:
    """Return ``True`` if the invite code is valid and usable."""
    return is_invite_valid(await get_invite(session, code))


async def consume_invite(session: AsyncSession, code: str) -> Optional[Invite]:
    """
    Validate and consume an invite code, incrementing its ``uses`` count.

    :returns: The consumed invite if valid, otherwise ``None``.
    """
    invite = await get_invite(session, code)
    if invite is None or not is_invite_valid(invite):
        return None

    invite.uses += 1
    await session.flush()
    return invite


async def revoke_invite(session: AsyncSession, code: str) -> bool:
    """
    Revoke an invite code by deleting it from the database.

    :returns: ``True`` if an invite was deleted, ``False`` if it did not exist.
    """
    invite = await get_invite(session, code)
    if invite is None:
        return False

    await session.delete(invite)
    await session.flush()
    return True


async def list_invites(
    session: AsyncSession,
    *,
    limit: int = 100,
    offset: int = 0,
) -> List[Invite]:
    """List invite codes ordered by creation date (newest first)."""
    result = await session.execute(select(Invite).order_by(Invite.created_at.desc()).offset(offset).limit(limit))
    return list(result.scalars().all())


async def count_invites(session: AsyncSession) -> int:
    """Return the total number of invite codes."""
    result = await session.execute(select(func.count(Invite.id)))
    return result.scalar() or 0
