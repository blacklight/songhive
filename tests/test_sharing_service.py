"""
Sharing service unit tests.
"""

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from songhive.models.share_token import ShareToken
from songhive.services import sharing


@pytest.mark.asyncio
async def test_create_share_grant(db_session, regular_user, make_user):
    """create_share_grant persists and returns a grant."""
    other_user = await make_user("other", email_verified=True)
    grant = await sharing.create_share_grant(db_session, "track", "track-1", other_user.id, created_by=regular_user.id)

    assert grant.item_type == "track"
    assert grant.item_id == "track-1"
    assert grant.user_id == other_user.id
    assert grant.created_by == regular_user.id


@pytest.mark.asyncio
async def test_create_share_grant_duplicate_returns_existing(db_session, regular_user, make_user):
    """Creating the same grant twice returns the existing row."""
    other_user = await make_user("other", email_verified=True)

    grant1 = await sharing.create_share_grant(db_session, "track", "track-1", other_user.id, created_by=regular_user.id)
    grant2 = await sharing.create_share_grant(db_session, "track", "track-1", other_user.id, created_by=regular_user.id)

    assert grant1 is grant2


@pytest.mark.asyncio
async def test_revoke_share_grant(db_session, regular_user, make_user):
    """revoke_share_grant deletes an existing grant."""
    other_user = await make_user("other", email_verified=True)

    await sharing.create_share_grant(db_session, "track", "track-1", other_user.id, created_by=regular_user.id)

    assert await sharing.revoke_share_grant(db_session, "track", "track-1", other_user.id) is True
    assert await sharing.revoke_share_grant(db_session, "track", "track-1", other_user.id) is False


@pytest.mark.asyncio
async def test_list_share_grants(db_session, regular_user, make_user):
    """list_share_grants returns all grants for an item."""
    other_user = await make_user("other", email_verified=True)
    third_user = await make_user("third", email_verified=True)

    await sharing.create_share_grant(db_session, "track", "track-1", other_user.id, created_by=regular_user.id)
    await sharing.create_share_grant(db_session, "track", "track-1", third_user.id, created_by=regular_user.id)

    grants = await sharing.list_share_grants(db_session, "track", "track-1")
    assert len(grants) == 2
    assert {g.user_id for g in grants} == {other_user.id, third_user.id}


@pytest.mark.asyncio
async def test_create_share_token_returns_raw_once(db_session, regular_user):
    """create_share_token returns a raw token and stores only its hash."""
    token, raw = await sharing.create_share_token(db_session, "track", "track-1", created_by=regular_user.id)

    expected_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    assert token.token_hash == expected_hash
    assert token.item_type == "track"
    assert token.item_id == "track-1"
    assert token.created_by == regular_user.id

    # The raw token is not persisted anywhere in the table.
    result = await db_session.execute(select(ShareToken))
    rows = list(result.scalars().all())
    assert len(rows) == 1
    assert raw not in {row.token_hash for row in rows}
    assert raw != rows[0].token_hash


@pytest.mark.asyncio
async def test_revoke_share_token(db_session, regular_user):
    """revoke_share_token sets revoked_at."""
    token, _ = await sharing.create_share_token(db_session, "track", "track-1", created_by=regular_user.id)
    assert token.revoked_at is None

    assert await sharing.revoke_share_token(db_session, token.id) is True
    assert token.revoked_at is not None


@pytest.mark.asyncio
async def test_revoke_share_token_missing(db_session):
    """revoking a missing token returns False."""
    assert await sharing.revoke_share_token(db_session, "missing-id") is False


@pytest.mark.asyncio
async def test_validate_share_token(db_session, regular_user):
    """validate_share_token accepts a valid raw token."""
    _, raw = await sharing.create_share_token(db_session, "track", "track-1", created_by=regular_user.id)

    assert await sharing.validate_share_token(db_session, "track", "track-1", raw) is True
    assert await sharing.validate_share_token(db_session, "track", "track-1", "wrong") is False
    assert await sharing.validate_share_token(db_session, "track", "other-track", raw) is False


@pytest.mark.asyncio
async def test_validate_share_token_revoked(db_session, regular_user):
    """A revoked token is invalid."""
    token, raw = await sharing.create_share_token(db_session, "track", "track-1", created_by=regular_user.id)
    await sharing.revoke_share_token(db_session, token.id)

    assert await sharing.validate_share_token(db_session, "track", "track-1", raw) is False


@pytest.mark.asyncio
async def test_validate_share_token_expired(db_session, regular_user):
    """An expired token is invalid."""
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    _, raw = await sharing.create_share_token(
        db_session,
        "track",
        "track-1",
        created_by=regular_user.id,
        expires_at=past,
    )

    assert await sharing.validate_share_token(db_session, "track", "track-1", raw) is False


@pytest.mark.asyncio
async def test_validate_share_token_missing(db_session):
    """A token that was never created is invalid."""
    assert await sharing.validate_share_token(db_session, "track", "track-1", secrets.token_urlsafe(32)) is False


@pytest.mark.asyncio
async def test_list_share_tokens(db_session, regular_user):
    """list_share_tokens returns tokens for an item."""
    token1, _ = await sharing.create_share_token(db_session, "track", "track-1", created_by=regular_user.id)
    token2, _ = await sharing.create_share_token(db_session, "track", "track-1", created_by=regular_user.id)

    tokens = await sharing.list_share_tokens(db_session, "track", "track-1")
    assert len(tokens) == 2
    assert {t.id for t in tokens} == {token1.id, token2.id}


@pytest.mark.asyncio
async def test_count_share_grants_and_tokens(db_session, regular_user, make_user):
    """count_share_grants and count_share_tokens return totals."""
    other = await make_user("other", email_verified=True)

    await sharing.create_share_grant(db_session, "track", "track-1", other.id, created_by=regular_user.id)
    assert await sharing.count_share_grants(db_session, "track", "track-1") == 1

    await sharing.create_share_token(db_session, "track", "track-1", created_by=regular_user.id)
    assert await sharing.count_share_tokens(db_session, "track", "track-1") == 1


@pytest.mark.asyncio
async def test_revoke_share_grant_by_id(db_session, regular_user, make_user):
    """revoke_share_grant_by_id deletes an existing grant and returns False for missing."""
    other = await make_user("other", email_verified=True)
    grant = await sharing.create_share_grant(
        db_session,
        "track",
        "track-1",
        other.id,
        created_by=regular_user.id,
    )
    assert await sharing.revoke_share_grant_by_id(db_session, grant.id) is True
    assert await sharing.revoke_share_grant_by_id(db_session, grant.id) is False
    assert await sharing.revoke_share_grant_by_id(db_session, "missing-id") is False


@pytest.mark.asyncio
async def test_create_share_grant_non_unique_integrity_error(db_session, regular_user, make_user):
    """A non-unique IntegrityError is re-raised."""
    other = await make_user("other", email_verified=True)
    with pytest.raises(IntegrityError):
        await sharing.create_share_grant(
            db_session,
            "track",
            "track-1",
            other.id,
            created_by=None,  # violates NOT NULL constraint, not a unique violation
        )


@pytest.mark.asyncio
async def test_create_share_grant_duplicate_not_found(db_session, regular_user, make_user, monkeypatch):
    """A duplicate grant with no retrievable row re-raises the IntegrityError."""
    other = await make_user("other", email_verified=True)
    await sharing.create_share_grant(
        db_session,
        "track",
        "track-1",
        other.id,
        created_by=regular_user.id,
    )

    monkeypatch.setattr(sharing, "_get_share_grant", AsyncMock(return_value=None))
    with pytest.raises(IntegrityError):
        await sharing.create_share_grant(
            db_session,
            "track",
            "track-1",
            other.id,
            created_by=regular_user.id,
        )


@pytest.mark.asyncio
async def test_make_aware_preserves_aware_and_utc_naive(db_session):
    """make_aware treats naive datetimes as UTC and leaves aware datetimes alone."""
    from datetime import datetime, timezone

    from songhive.services.sharing import make_aware

    naive = datetime(2024, 1, 1, 12)
    aware = make_aware(naive)
    assert aware is not None
    assert aware.tzinfo is timezone.utc

    already = datetime(2024, 1, 1, 12, tzinfo=timezone.utc)
    assert make_aware(already) is already
