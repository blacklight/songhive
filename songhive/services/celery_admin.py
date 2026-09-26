"""
Admin-facing helpers for inspecting and controlling Celery workers.
"""

import asyncio
import logging
import time
from collections import Counter
from contextlib import contextmanager
from typing import Any, Iterator, Optional, cast

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


def _task_names(task_list: Any) -> Iterator[str]:
    """Yield task names from an ``inspect.active()``/``reserved()`` task list."""
    for task in task_list or []:
        if isinstance(task, dict) and task.get("name"):
            yield str(task["name"])


def _scheduled_task_names(task_list: Any) -> Iterator[str]:
    """Yield task names from an ``inspect.scheduled()`` task list."""
    for entry in task_list or []:
        if not isinstance(entry, dict):
            continue
        request = entry.get("request")
        name = request.get("name") if isinstance(request, dict) else entry.get("name")
        if name:
            yield str(name)


def _broker_queue_depth(app: Celery) -> Optional[int]:
    """
    Best-effort count of messages still sitting in the broker queues.

    Uses the transport's ``queue_declare`` response, which reports the queue
    length on both AMQP and virtual (Redis/SQS-style) transports. Returns
    ``None`` when the depth cannot be determined.
    """
    try:
        queues = list(app.amqp.queues.values())
        with app.connection() as connection:
            channel = cast(Any, connection.default_channel)
            depth = 0
            for queue in queues:
                try:
                    _, message_count, _ = channel.queue_declare(queue.name, passive=True)
                except Exception:
                    # Virtual transports reject passive declares for queues not
                    # declared on this channel yet; a regular declare is a
                    # channel-local no-op that still reports the queue length.
                    _, message_count, _ = channel.queue_declare(queue.name)
                depth += int(message_count or 0)
        return depth
    except Exception as exc:
        logger.debug("Could not read Celery broker queue depth: %s", exc)
        return None


def _result_backend_state_counts(app: Celery) -> Optional[dict[str, int]]:
    """
    Count stored task results grouped by state, e.g. ``{"SUCCESS": 3}``.

    Only backends exposing a Redis-style client (``scan_iter``/``mget``) and a
    ``task_keyprefix`` support this. Returns ``None`` when unavailable.
    """
    try:
        backend = app.backend
        keyprefix = getattr(backend, "task_keyprefix", None)
        if not isinstance(keyprefix, str):
            return None
        client = getattr(backend, "client", None)
        scan_iter = getattr(client, "scan_iter", None)
        mget = getattr(client, "mget", None)
        if scan_iter is None or mget is None:
            return None

        counts: dict[str, int] = {}
        pending: list[Any] = []

        def _flush() -> None:
            for meta in mget(pending):
                if not meta:
                    continue
                try:
                    decoded = backend.decode(meta)
                except Exception:
                    continue
                state = decoded.get("status") if isinstance(decoded, dict) else None
                if isinstance(state, str):
                    counts[state] = counts.get(state, 0) + 1
            pending.clear()

        for key in scan_iter(match=f"*{keyprefix}*", count=1000):
            pending.append(key)
            if len(pending) >= 1000:
                _flush()
        _flush()
        return counts
    except Exception as exc:
        logger.debug("Could not count Celery task results: %s", exc)
        return None


def _queue_stats_sync(app: Celery) -> dict[str, Any]:
    """Collect Celery queue statistics from workers, the broker, and the backend."""
    active: dict[str, Any] = {}
    reserved: dict[str, Any] = {}
    scheduled: dict[str, Any] = {}
    with _inspect_context(app):
        inspect = app.control.inspect(timeout=1.0)
        workers = inspect.ping() or {}
        if workers:
            active = inspect.active() or {}
            reserved = inspect.reserved() or {}
            scheduled = inspect.scheduled() or {}

    by_name: Counter[str] = Counter()
    for task_list in active.values():
        by_name.update(_task_names(task_list))
    for task_list in reserved.values():
        by_name.update(_task_names(task_list))
    for task_list in scheduled.values():
        by_name.update(_scheduled_task_names(task_list))

    processing = sum(len(tasks) for tasks in active.values() if tasks)
    queued = (
        sum(len(tasks) for tasks in reserved.values() if tasks)
        + sum(len(tasks) for tasks in scheduled.values() if tasks)
        + (_broker_queue_depth(app) or 0)
    )

    state_counts = _result_backend_state_counts(app)

    return {
        "total": processing + queued,
        "processing": processing,
        "queued": queued,
        "failed": state_counts.get("FAILURE", 0) if state_counts is not None else None,
        "completed": state_counts.get("SUCCESS", 0) if state_counts is not None else None,
        "by_name": [
            {"name": name, "count": count}
            for name, count in sorted(by_name.items(), key=lambda item: (-item[1], item[0]))
        ],
    }


async def list_active_celery_tasks() -> list[dict[str, Any]]:
    """Return a flattened list of currently running Celery tasks across workers."""
    try:
        return await asyncio.to_thread(_list_active_tasks_sync, celery_app)
    except Exception as exc:
        logger.warning("Failed to inspect active Celery tasks: %s", exc)
        raise CeleryAdminError(f"Could not inspect Celery workers: {exc}") from exc


async def get_celery_queue_stats() -> dict[str, Any]:
    """Return aggregated Celery queue statistics (queued/processing/completed)."""
    try:
        return await asyncio.to_thread(_queue_stats_sync, celery_app)
    except Exception as exc:
        logger.warning("Failed to inspect Celery queue stats: %s", exc)
        raise CeleryAdminError(f"Could not inspect Celery workers: {exc}") from exc


async def terminate_celery_tasks(task_ids: list[str], signal: str = "SIGTERM") -> int:
    """Request that the given task ids be terminated on all workers."""
    try:
        await asyncio.to_thread(_terminate_tasks_sync, celery_app, task_ids, signal)
    except Exception as exc:
        logger.warning("Failed to terminate Celery tasks %s: %s", task_ids, exc)
        raise CeleryAdminError(f"Could not revoke Celery tasks: {exc}") from exc

    return len(task_ids)
