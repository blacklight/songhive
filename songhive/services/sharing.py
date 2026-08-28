"""
Sharing service: per-user share grants and revocable share URL tokens.

Share grants allow an owner to give a specific user access to a private item.
Share tokens generate a short URL that grants unauthenticated access until the
token is revoked or expires.  Only the SHA-256 hash of a raw token is stored.
"""

import hashlib
import secrets
from datetime import datetime, timezone
from typing import List, Optional, Tuple

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.share_grant import ShareGrant
from ..models.share_token import ShareToken
from ..storage.exc import is_unique_constraint_error


def _now_utc() -> datetime:
    """Return the current UTC time with an explicit timezone."""
    return datetime.now(timezone.utc)


def make_aware(dt: Optional[datetime]) -> Optional[datetime]:
    """Treat a timezone-naive datetime as UTC for comparisons."""
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _hash_token(raw_token: str) -> str:
    """Return the SHA-256 hex digest of ``raw_token``."""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


async def create_share_grant(
    session: AsyncSession,
    item_type: str,
    item_id: str,
    user_id: str,
    created_by: str,
) -> ShareGrant:
    """
    Create a share grant for ``user_id`` on ``(item_type, item_id)``.

    If an identical grant already exists, the existing row is returned instead
    of raising a uniqueness error.
    """
    grant = ShareGrant(
        item_type=item_type,
        item_id=item_id,
        user_id=user_id,
        created_by=created_by,
    )

    try:
        async with session.begin_nested():
            session.add(grant)
            await session.flush()
    except IntegrityError as exc:
        if not is_unique_constraint_error(exc):
            raise

        # Duplicate grant; return the existing row.  The nested savepoint has
        # already been rolled back, leaving the outer transaction intact.
        existing = await _get_share_grant(session, item_type, item_id, user_id)
        if existing is not None:
            return existing
        raise

    return grant


async def _get_share_grant(
    session: AsyncSession,
    item_type: str,
    item_id: str,
    user_id: str,
) -> Optional[ShareGrant]:
    """Fetch an existing share grant by its natural key."""
    result = await session.execute(
        select(ShareGrant).where(
            ShareGrant.item_type == item_type,
            ShareGrant.item_id == item_id,
            ShareGrant.user_id == user_id,
        )
    )
    return result.scalar_one_or_none()


async def revoke_share_grant(
    session: AsyncSession,
    item_type: str,
    item_id: str,
    user_id: str,
) -> bool:
    """Revoke a share grant.  Returns ``True`` if a row was deleted."""
    result = await session.execute(
        select(ShareGrant).where(
            ShareGrant.item_type == item_type,
            ShareGrant.item_id == item_id,
            ShareGrant.user_id == user_id,
        )
    )
    grant = result.scalar_one_or_none()
    if grant is None:
        return False

    await session.delete(grant)
    await session.flush()
    return True


async def revoke_share_grant_by_id(session: AsyncSession, grant_id: str) -> bool:
    """Revoke a share grant by id.  Returns ``True`` if the grant existed."""
    grant = await session.get(ShareGrant, grant_id)
    if grant is None:
        return False

    await session.delete(grant)
    await session.flush()
    return True


async def list_share_grants(
    session: AsyncSession,
    item_type: str,
    item_id: str,
) -> List[ShareGrant]:
    """List all share grants for ``(item_type, item_id)``."""
    result = await session.execute(
        select(ShareGrant).where(
            ShareGrant.item_type == item_type,
            ShareGrant.item_id == item_id,
        )
    )
    return list(result.scalars().all())


async def count_share_grants(
    session: AsyncSession,
    item_type: str,
    item_id: str,
) -> int:
    """Return the total number of share grants for ``(item_type, item_id)``."""
    result = await session.execute(
        select(func.count(ShareGrant.id)).where(
            ShareGrant.item_type == item_type,
            ShareGrant.item_id == item_id,
        )
    )
    return result.scalar() or 0


async def create_share_token(
    session: AsyncSession,
    item_type: str,
    item_id: str,
    created_by: str,
    expires_at: Optional[datetime] = None,
) -> Tuple[ShareToken, str]:
    """
    Create a revocable share token for ``(item_type, item_id)``.

    Returns the persisted ``ShareToken`` and the raw token.  The raw token is
    returned exactly once; callers must communicate it to the user immediately.
    """
    raw_token = secrets.token_urlsafe(32)
    token_hash = _hash_token(raw_token)

    token = ShareToken(
        item_type=item_type,
        item_id=item_id,
        token_hash=token_hash,
        created_by=created_by,
        expires_at=expires_at,
        revoked_at=None,
    )
    session.add(token)
    await session.flush()
    return token, raw_token


async def revoke_share_token(session: AsyncSession, token_id: str) -> bool:
    """Revoke a share token by id.  Returns ``True`` if the token existed."""
    token = await session.get(ShareToken, token_id)
    if token is None:
        return False

    token.revoked_at = _now_utc()
    await session.flush()
    return True


def _share_tokens_where(item_type: str, item_id: str, now: datetime):
    """Build the common WHERE clause for share-token list/count queries."""
    return (
        ShareToken.item_type == item_type,
        ShareToken.item_id == item_id,
        ShareToken.revoked_at.is_(None),
        or_(
            ShareToken.expires_at.is_(None),
            ShareToken.expires_at > now,
        ),
    )


async def list_share_tokens(
    session: AsyncSession,
    item_type: str,
    item_id: str,
) -> List[ShareToken]:
    """List non-revoked, non-expired share tokens for ``(item_type, item_id)``."""
    now = _now_utc()
    result = await session.execute(select(ShareToken).where(*_share_tokens_where(item_type, item_id, now)))
    return list(result.scalars().all())


async def count_share_tokens(
    session: AsyncSession,
    item_type: str,
    item_id: str,
) -> int:
    """Return the total number of non-revoked, non-expired share tokens."""
    now = _now_utc()
    result = await session.execute(
        select(func.count(ShareToken.id)).where(*_share_tokens_where(item_type, item_id, now))
    )
    return result.scalar() or 0


async def get_valid_share_token(
    session: AsyncSession,
    raw_token: str,
) -> Optional[ShareToken]:
    """Return the matching ``ShareToken`` row if ``raw_token`` is valid.

    A missing, revoked, or expired token returns ``None``.
    """
    token_hash = _hash_token(raw_token)

    result = await session.execute(select(ShareToken).where(ShareToken.token_hash == token_hash))
    token = result.scalar_one_or_none()
    if token is None:
        return None

    if token.revoked_at is not None:
        return None

    if token.expires_at is not None:
        expires_at = make_aware(token.expires_at)
        assert expires_at is not None
        if _now_utc() > expires_at:
            return None

    return token


async def validate_share_token(
    session: AsyncSession,
    item_type: str,
    item_id: str,
    raw_token: str,
) -> bool:
    """Return whether ``raw_token`` is valid for ``(item_type, item_id)``."""
    token = await get_valid_share_token(session, raw_token)
    if token is None:
        return False

    return token.item_type == item_type and token.item_id == item_id
