"""
Admin-facing helpers for inspecting and controlling Celery workers.
"""

import asyncio
import logging
import time
from contextlib import contextmanager
from typing import Any, Iterator

from celery import Celery

from ..tasks.celery import celery_app

logger = logging.getLogger(__name__)


class CeleryAdminError(Exception):
    """Raised when a Celery admin operation cannot talk to the broker or workers."""


@contextmanager
def _inspect_context(app: Celery) -> Iterator[None]:
    """Shorten broker connection timeouts for the duration of inspect calls."""
    original_timeout = app.conf.get("broker_connection_timeout")
    original_retry = app.conf.get("broker_connection_retry_on_startup")
    app.conf.broker_connection_timeout = 1.0
    app.conf.broker_connection_retry_on_startup = False
    try:
        yield
    finally:
        app.conf.broker_connection_timeout = original_timeout
        app.conf.broker_connection_retry_on_startup = original_retry


def _list_active_tasks_sync(app: Celery) -> list[dict[str, Any]]:
    """Return a flattened list of currently running Celery tasks across workers."""
    with _inspect_context(app):
        inspect = app.control.inspect(timeout=1.0)
        workers = inspect.ping() or {}
        if not workers:
            return []

        active = inspect.active() or {}

    now = time.time()
    tasks: list[dict[str, Any]] = []

    for worker, task_list in active.items():
        for task in task_list or []:
            if not isinstance(task, dict):
                continue

            time_start = task.get("time_start")
            runtime: float | None = None
            if isinstance(time_start, (int, float)):
                runtime = round(now - float(time_start), 3)

            tasks.append(
                {
                    "task_id": str(task.get("id", "")),
                    "name": str(task.get("name", "")),
                    "worker": worker,
                    "args": task.get("args", []),
                    "kwargs": task.get("kwargs", {}),
                    "hostname": task.get("hostname"),
                    "acknowledged": task.get("acknowledged"),
                    "delivery_info": task.get("delivery_info"),
                    "time_start": time_start,
                    "runtime": runtime,
                }
            )

    return tasks


def _terminate_tasks_sync(app: Celery, task_ids: list[str], signal: str = "SIGTERM") -> int:
    """Request termination of the given task ids on all workers."""
    with _inspect_context(app):
        app.control.revoke(
            task_ids,
            terminate=True,
            signal=signal,
            reply=False,
            timeout=1.0,
        )
    return len(task_ids)


async def list_active_celery_tasks() -> list[dict[str, Any]]:
    """Return a flattened list of currently running Celery tasks across workers."""
    try:
        return await asyncio.to_thread(_list_active_tasks_sync, celery_app)
    except Exception as exc:
        logger.warning("Failed to inspect active Celery tasks: %s", exc)
        raise CeleryAdminError(f"Could not inspect Celery workers: {exc}") from exc


async def terminate_celery_tasks(task_ids: list[str], signal: str = "SIGTERM") -> int:
    """Request that the given task ids be terminated on all workers."""
    try:
        await asyncio.to_thread(_terminate_tasks_sync, celery_app, task_ids, signal)
    except Exception as exc:
        logger.warning("Failed to terminate Celery tasks %s: %s", task_ids, exc)
        raise CeleryAdminError(f"Could not revoke Celery tasks: {exc}") from exc

    return len(task_ids)
