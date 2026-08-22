"""
System health and admin dashboard statistics.
"""

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config.schema import SonghiveConfig
from ..models.album import Album
from ..models.library import Library
from ..models.playlist import Playlist
from ..models.stored_file import StoredFile
from ..models.track import Track
from ..models.user import User

logger = logging.getLogger(__name__)

STATS_CACHE_KEY = "stats:dashboard"
STATS_CACHE_TTL = 60


async def _get_user_stats(session: AsyncSession) -> dict:
    """Return user-related statistics."""
    total = (await session.execute(select(func.count(User.id)))).scalar() or 0
    active = (await session.execute(select(func.count(User.id)).where(User.is_active.is_(True)))).scalar() or 0

    by_role = (await session.execute(select(User.role, func.count(User.id)).group_by(User.role))).mappings().all()

    week_ago = datetime.now(timezone.utc) - timedelta(days=7)
    recent = (await session.execute(select(func.count(User.id)).where(User.created_at >= week_ago))).scalar() or 0

    return {
        "total_users": total,
        "active_users": active,
        "users_by_role": {row["role"]: row["count"] for row in by_role},
        "recent_registrations": recent,
    }


async def _get_content_stats(session: AsyncSession) -> dict:
    """Return content-related statistics."""
    total_tracks = (await session.execute(select(func.count(Track.id)))).scalar() or 0
    total_albums = (await session.execute(select(func.count(Album.id)))).scalar() or 0
    total_playlists = (await session.execute(select(func.count(Playlist.id)))).scalar() or 0
    total_libraries = (await session.execute(select(func.count(Library.id)))).scalar() or 0

    return {
        "total_tracks": total_tracks,
        "total_albums": total_albums,
        "total_playlists": total_playlists,
        "total_libraries": total_libraries,
    }


async def _get_storage_stats(session: AsyncSession) -> dict:
    """Return storage-related statistics."""
    total_files = (await session.execute(select(func.count(StoredFile.id)))).scalar() or 0
    total_size = (await session.execute(select(func.coalesce(func.sum(StoredFile.size), 0)))).scalar() or 0

    by_backend = (
        (
            await session.execute(
                select(
                    StoredFile.storage_backend,
                    func.count(StoredFile.id),
                    func.coalesce(func.sum(StoredFile.size), 0),
                ).group_by(StoredFile.storage_backend)
            )
        )
        .mappings()
        .all()
    )

    return {
        "total_files": total_files,
        "total_size_bytes": total_size,
        "files_by_backend": [
            {"backend": row["storage_backend"], "count": row["count"], "size": row["sum"]} for row in by_backend
        ],
    }


async def _get_federation_stats(config: SonghiveConfig) -> dict:
    """Return federation status and best-effort instance metadata."""
    if not config.federation.enabled:
        return {"enabled": False}

    return {
        "enabled": True,
        "instance_domain": config.federation.instance_domain,
        "instance_name": config.federation.instance_name,
    }


def _inspect_celery() -> dict:
    """Query Celery workers and return aggregated task statistics."""
    from ..tasks.celery import celery_app

    # Keep the request fast when the broker is down or no workers are running.
    # These changes only affect this singleton in the web process; workers have
    # their own process-local copy of the Celery app.
    original_timeout = celery_app.conf.get("broker_connection_timeout")
    original_retry = celery_app.conf.get("broker_connection_retry_on_startup")
    celery_app.conf.broker_connection_timeout = 1.0
    celery_app.conf.broker_connection_retry_on_startup = False

    try:
        inspect = celery_app.control.inspect(timeout=1.0)
        workers = inspect.ping() or {}

        if not workers:
            return {
                "available": True,
                "worker_count": 0,
                "workers": [],
                "active_tasks": 0,
                "scheduled_tasks": 0,
                "reserved_tasks": 0,
                "registered_task_count": 0,
                "registered_tasks": [],
                "total_tasks_processed": 0,
            }

        active = inspect.active() or {}
        scheduled = inspect.scheduled() or {}
        reserved = inspect.reserved() or {}
        registered = inspect.registered() or {}
        worker_stats = inspect.stats() or {}
    except Exception as exc:
        logger.warning("Failed to inspect Celery workers: %s", exc)
        return {"available": False, "error": str(exc)}
    finally:
        celery_app.conf.broker_connection_timeout = original_timeout
        celery_app.conf.broker_connection_retry_on_startup = original_retry

    total_tasks = sum(ws.get("total", {}).get("tasks", 0) for ws in worker_stats.values())

    return {
        "available": True,
        "worker_count": len(workers),
        "workers": sorted(workers.keys()),
        "active_tasks": sum(len(t) for t in active.values()),
        "scheduled_tasks": sum(len(t) for t in scheduled.values()),
        "reserved_tasks": sum(len(t) for t in reserved.values()),
        "registered_task_count": sum(len(t) for t in registered.values()),
        "registered_tasks": sorted({task for tasks in registered.values() for task in tasks}),
        "total_tasks_processed": total_tasks,
    }


async def _get_celery_stats() -> dict:
    """Return Celery worker and task statistics, or an unavailable marker."""
    try:
        return await asyncio.to_thread(_inspect_celery)
    except Exception as exc:
        logger.warning("Failed to gather Celery stats: %s", exc)
        return {"available": False, "error": str(exc)}


async def _compute_all_stats(session: AsyncSession, config: SonghiveConfig) -> dict:
    """Compute the full stats dashboard."""
    return {
        "users": await _get_user_stats(session),
        "content": await _get_content_stats(session),
        "storage": await _get_storage_stats(session),
        "federation": await _get_federation_stats(config),
        "celery": await _get_celery_stats(),
    }


async def get_all_stats(
    session: AsyncSession,
    config: SonghiveConfig,
    redis: Optional[Redis] = None,
) -> dict:
    """Return the full stats dashboard, optionally cached in Redis."""
    if redis is not None:
        cached = await redis.get(STATS_CACHE_KEY)
        if cached:
            try:
                return json.loads(cached)
            except json.JSONDecodeError:
                pass

    stats = await _compute_all_stats(session, config)

    if redis is not None:
        await redis.set(STATS_CACHE_KEY, json.dumps(stats), ex=STATS_CACHE_TTL)

    return stats
