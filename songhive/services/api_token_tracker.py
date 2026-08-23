"""
API token usage tracking service.

Buffers last_used_at updates in Redis to reduce DB write load on the hot path.
Updates are flushed to the database periodically or on token revocation.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from redis.asyncio import Redis
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.api_token import ApiToken

logger = logging.getLogger(__name__)

# Redis key prefix for buffered last_used_at timestamps
LAST_USED_KEY_PREFIX = "api_token:last_used:"

# TTL for buffered timestamps (5 minutes)
BUFFER_TTL = 300


async def track_api_token_usage(redis: Redis, jti: str) -> None:
    """
    Record that an API token was used at the current time.

    The timestamp is buffered in Redis with a 5-minute TTL. A background task
    or explicit flush call will write it to the database.
    """
    key = f"{LAST_USED_KEY_PREFIX}{jti}"
    now = datetime.now(timezone.utc).isoformat()
    try:
        await redis.set(key, now, ex=BUFFER_TTL)
    except Exception:
        # Best-effort: a failure here must not fail the request.
        logger.exception("Failed to buffer API token usage for jti=%s", jti)


async def flush_api_token_usage(db: AsyncSession, redis: Redis, jti: str) -> None:
    """
    Flush the buffered last_used_at timestamp for a single API token to the database.

    This is called explicitly on token revocation to ensure the final usage time
    is persisted before the token becomes inactive.
    """
    key = f"{LAST_USED_KEY_PREFIX}{jti}"
    try:
        timestamp_str = await redis.get(key)
        if timestamp_str is None:
            return

        timestamp = datetime.fromisoformat(str(timestamp_str))
        stmt = update(ApiToken).where(ApiToken.jti == jti).values(last_used_at=timestamp)
        await db.execute(stmt)
        await db.flush()
        await redis.delete(key)
    except Exception:
        # Best-effort: log but don't fail the operation.
        logger.exception("Failed to flush API token usage for jti=%s", jti)


async def flush_all_api_token_usage(db: AsyncSession, redis: Redis) -> int:
    """
    Flush all buffered last_used_at timestamps to the database.

    This is intended to be called by a periodic background task (e.g., every 5 minutes).
    Returns the number of tokens updated.
    """
    count = 0
    try:
        # Scan for all buffered timestamps
        cursor = 0
        pattern = f"{LAST_USED_KEY_PREFIX}*"
        while True:
            cursor, keys = await redis.scan(cursor, match=pattern, count=100)
            for key in keys:
                jti = str(key).replace(LAST_USED_KEY_PREFIX, "", 1)
                await flush_api_token_usage(db, redis, jti)
                count += 1
            if cursor == 0:
                break
    except Exception:
        logger.exception("Failed to flush all API token usage")

    return count


async def get_buffered_last_used_at(redis: Redis, jti: str) -> Optional[datetime]:
    """
    Return the buffered last_used_at timestamp for an API token, if any.

    This is useful for displaying the most recent usage time without waiting
    for the next flush.
    """
    key = f"{LAST_USED_KEY_PREFIX}{jti}"
    try:
        timestamp_str = await redis.get(key)
        if timestamp_str is None:
            return None
        return datetime.fromisoformat(str(timestamp_str))
    except Exception:
        logger.exception("Failed to get buffered last_used_at for jti=%s", jti)
        return None
