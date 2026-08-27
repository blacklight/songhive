"""
Celery tasks for API token maintenance.
"""

import logging

from ..config import load_config
from ..models.base import get_session, init_db
from ..services.api_token_tracker import flush_all_api_token_usage
from ..services.redis import close_redis_client, get_redis_client
from .celery import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="songhive.tasks.api_tokens.flush_usage_timestamps")
def flush_usage_timestamps():
    """
    Flush all buffered API token usage timestamps from Redis to the database.

    This task is scheduled to run every 5 minutes to persist the last_used_at
    timestamps without blocking the hot path.
    """
    import asyncio

    async def _flush():
        config = load_config([])
        init_db(config.database.url)
        redis = get_redis_client(config)
        try:
            async with get_session() as db:
                count = await flush_all_api_token_usage(db, redis)
                await db.commit()
                logger.info("Flushed %d API token usage timestamps", count)
        finally:
            await close_redis_client()

    asyncio.run(_flush())
