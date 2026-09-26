"""
Celery application factory.
"""

import logging
from typing import Optional

from celery import Celery
from celery.schedules import crontab
from kombu import Queue

logger = logging.getLogger(__name__)


def _parse_crontab(expr: str) -> crontab:
    """Parse a 5-field cron expression into a Celery crontab schedule."""
    parts = expr.split()
    if len(parts) != 5:
        raise ValueError(
            f"Invalid crontab expression {expr!r}; expected 5 fields: " "minute hour day_of_month month day_of_week"
        )
    minute, hour, day_of_month, month, day_of_week = parts
    return crontab(
        minute=minute,
        hour=hour,
        day_of_month=day_of_month,
        month_of_year=month,
        day_of_week=day_of_week,
    )


def _load_celery_config() -> tuple[str, str, str, int, int, Optional[str]]:
    """Load Celery-relevant configuration, falling back to sensible defaults."""
    try:
        from ..config import load_config

        config = load_config([])
        return (
            config.celery.broker_url,
            config.celery.result_backend,
            config.celery.cleanup_orphaned_files_schedule,
            config.notifications.digest_hour,
            config.notifications.purge_hour,
            config.federation.remote_activity_prune_schedule or None,
        )
    except Exception as exc:
        logger.info("Could not load Songhive config for Celery, using defaults: %s", type(exc).__name__)
        return (
            "redis://localhost:6379/1",
            "redis://localhost:6379/2",
            "0 3 * * *",
            8,
            3,
            None,
        )


def make_celery(
    broker_url: str = "redis://localhost:6379/1",
    result_backend: str = "redis://localhost:6379/2",
    cleanup_orphaned_files_schedule: Optional[str] = None,
    notification_digest_hour: int = 8,
    notification_purge_hour: int = 3,
    remote_activity_prune_schedule: Optional[str] = None,
) -> Celery:
    """Create and configure a Celery application."""
    if cleanup_orphaned_files_schedule is None:
        cleanup_orphaned_files_schedule = "0 3 * * *"

    app = Celery("songhive")
    app.conf.update(
        broker_url=broker_url,
        result_backend=result_backend,
        task_serializer="json",
        accept_content=["json"],
        result_serializer="json",
        timezone="UTC",
        enable_utc=True,
        task_default_queue="celery",
        # Queue layout — workers must subscribe to all three:
        #   celery -A songhive.tasks worker -Q celery,scrobbles,bulk
        #
        # ``scrobbles``: latency-sensitive Last.fm/Libre.fm submissions. A
        # now-playing delivered minutes late is worthless, so these must
        # never wait behind bulk work.
        # ``bulk``: high-volume per-item jobs (external library sync fan-out,
        # MusicBrainz enrichment, image/tag rewrites, imports, transcodes).
        # A fresh library sync can enqueue tens of thousands of these;
        # without the split they starve everything else on ``celery``.
        # ``celery``: the default — low-volume and interactive tasks.
        task_queues=(
            Queue("celery"),
            Queue("scrobbles"),
            Queue("bulk"),
        ),
        task_routes={
            "songhive.tasks.scrobbling.*": {"queue": "scrobbles"},
            "songhive.tasks.musicbrainz.*": {"queue": "bulk"},
            "songhive.tasks.images.*": {"queue": "bulk"},
            "songhive.tasks.tags.*": {"queue": "bulk"},
            "songhive.tasks.import_.*": {"queue": "bulk"},
            "songhive.tasks.transcoding.*": {"queue": "bulk"},
            "songhive.tasks.external_libraries.sync_external_library": {"queue": "bulk"},
            "songhive.tasks.external_libraries.write_back_metadata": {"queue": "bulk"},
            "songhive.tasks.podcasts.refresh_podcast": {"queue": "bulk"},
            "songhive.tasks.storage.rehash_audio_files": {"queue": "bulk"},
        },
        beat_schedule={
            "cleanup-orphaned-files": {
                "task": "songhive.tasks.storage.cleanup_orphaned_files",
                "schedule": _parse_crontab(cleanup_orphaned_files_schedule),
            },
            "flush-api-token-usage": {
                "task": "songhive.tasks.api_tokens.flush_usage_timestamps",
                "schedule": 300.0,  # Every 5 minutes
            },
            "external-scheduled-sync-scan": {
                "task": "songhive.tasks.external_libraries.scan_scheduled_syncs",
                "schedule": crontab(minute="*/5"),
            },
            "send-notification-digests": {
                "task": "songhive.tasks.notifications.send_notification_digests",
                "schedule": _parse_crontab(f"0 {notification_digest_hour} * * *"),
            },
            "purge-old-notifications": {
                "task": "songhive.tasks.notifications.purge_old_notifications",
                "schedule": _parse_crontab(f"0 {notification_purge_hour} * * *"),
            },
            # The scan itself is cheap; the task decides per feed whether a
            # refresh is due based on podcasts.refresh_interval_minutes.
            "scan-due-podcasts": {
                "task": "songhive.tasks.podcasts.scan_due_podcasts",
                "schedule": crontab(minute="*/15"),
            },
            "scan-due-gpodder-syncs": {
                "task": "songhive.tasks.podcasts.scan_due_gpodder_syncs",
                "schedule": crontab(minute="*/15"),
            },
            "cleanup-completed-downloads": {
                "task": "songhive.tasks.downloads.cleanup_completed_downloads",
                "schedule": crontab(minute=35),
            },
        },
    )
    if remote_activity_prune_schedule:
        app.conf.beat_schedule["prune-remote-activities"] = {
            "task": "songhive.tasks.federation.prune_remote_activities",
            "schedule": _parse_crontab(remote_activity_prune_schedule),
        }
    app.autodiscover_tasks(["songhive.tasks"])
    return app


(
    _broker_url,
    _result_backend,
    _cleanup_schedule,
    _digest_hour,
    _purge_hour,
    _remote_prune_schedule,
) = _load_celery_config()
celery_app = make_celery(
    broker_url=_broker_url,
    result_backend=_result_backend,
    cleanup_orphaned_files_schedule=_cleanup_schedule,
    notification_digest_hour=_digest_hour,
    notification_purge_hour=_purge_hour,
    remote_activity_prune_schedule=_remote_prune_schedule,
)
