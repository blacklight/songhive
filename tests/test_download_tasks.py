"""
Tests for the download archive Celery tasks (tasks/downloads.py).
"""

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from celery.exceptions import MaxRetriesExceededError, Retry
from sqlalchemy import select, update

from songhive.models.base import init_db
from songhive.models.download import DownloadArchive
from songhive.models.notification import Notification
from songhive.services.storage import StorageService
from songhive.storage import get_storage
from songhive.tasks import downloads as download_tasks


def _task_self(retries: int = 0, retry_side_effect=None):
    """Stand-in for the bound Celery task object."""
    self = MagicMock()
    self.request.retries = retries
    self.max_retries = download_tasks._SLOT_MAX_RETRIES
    self.retry.side_effect = retry_side_effect or Retry()
    return self


def _patch_config(monkeypatch, config):
    # ``tasks.downloads`` imports ``load_config`` lazily inside the task bodies.
    monkeypatch.setattr("songhive.config.load_config", lambda *_: config)


def _run(task_fn, *args):
    """Invoke a bound task body. ``run`` is a bound method; ``__func__`` takes self."""
    task = getattr(download_tasks, task_fn) if isinstance(task_fn, str) else task_fn
    run = task.run
    fn = run.__func__ if hasattr(run, "__func__") else run
    return fn


class TestAcquireSlot:
    def test_acquires_below_cap(self, config, monkeypatch):
        client = MagicMock()
        client.eval.return_value = 1
        monkeypatch.setattr("songhive.services.redis.get_sync_redis_client", lambda *_: client)
        assert download_tasks._acquire_slot("a1", config) is client

    def test_returns_none_at_cap(self, config, monkeypatch):
        client = MagicMock()
        client.eval.return_value = 0
        monkeypatch.setattr("songhive.services.redis.get_sync_redis_client", lambda *_: client)
        assert download_tasks._acquire_slot("a1", config) is None

    def test_fails_open_on_redis_error(self, config, monkeypatch):
        monkeypatch.setattr(
            "songhive.services.redis.get_sync_redis_client",
            MagicMock(side_effect=RuntimeError("redis down")),
        )
        client = download_tasks._acquire_slot("a1", config)
        assert isinstance(client, download_tasks._FailOpenClient)
        # Releasing a fail-open handle is a no-op.
        download_tasks._release_slot(client, "a1")

    def test_eval_error_fails_open(self, config, monkeypatch):
        client = MagicMock()
        client.eval.side_effect = RuntimeError("no eval")
        monkeypatch.setattr("songhive.services.redis.get_sync_redis_client", lambda *_: client)
        assert isinstance(download_tasks._acquire_slot("a1", config), download_tasks._FailOpenClient)


class TestReleaseSlot:
    def test_none_is_noop(self):
        download_tasks._release_slot(None, "a1")

    def test_releases_via_zrem(self):
        client = MagicMock()
        download_tasks._release_slot(client, "a1")
        client.zrem.assert_called_once_with(download_tasks._SLOT_KEY, "a1")

    def test_release_error_is_swallowed(self):
        client = MagicMock()
        client.zrem.side_effect = RuntimeError("down")
        download_tasks._release_slot(client, "a1")


class TestMarkFailed:
    async def test_marks_pending_archive_failed(self, db_session, engine, regular_user):
        init_db(engine=engine, force=True)
        archive = DownloadArchive(user_id=regular_user.id, label="x", items=[], status="pending")
        db_session.add(archive)
        await db_session.commit()

        await asyncio.to_thread(lambda: asyncio.run(download_tasks._mark_failed(archive.id, "boom")))
        await db_session.refresh(archive)
        assert archive.status == "failed"
        assert archive.error == "boom"
        assert archive.completed_at is not None

    async def test_ignores_completed_archive(self, db_session, engine, regular_user):
        init_db(engine=engine, force=True)
        archive = DownloadArchive(
            user_id=regular_user.id,
            label="x",
            items=[],
            status="ready",
            completed_at=datetime.now(timezone.utc),
        )
        db_session.add(archive)
        await db_session.commit()

        await asyncio.to_thread(lambda: asyncio.run(download_tasks._mark_failed(archive.id, "boom")))
        await db_session.refresh(archive)
        assert archive.status == "ready"


class TestBuildArchive:
    async def _make_archive(self, db_session, user, status="pending") -> DownloadArchive:
        archive = DownloadArchive(user_id=user.id, label="My tracks", items=[], status=status)
        db_session.add(archive)
        await db_session.commit()
        return archive

    async def test_slot_denied_retries(self, db_session, engine, config, monkeypatch, regular_user):
        init_db(engine=engine, force=True)
        _patch_config(monkeypatch, config)
        monkeypatch.setattr(download_tasks, "_acquire_slot", lambda *_: None)

        self = _task_self()
        with pytest.raises(Retry):
            await asyncio.to_thread(download_tasks.build_download_archive.run.__func__, self, "a1")
        assert self.retry.call_args.kwargs["countdown"] == download_tasks._SLOT_RETRY_COUNTDOWN

    async def test_slot_retry_exhausted_marks_failed(self, db_session, engine, config, monkeypatch, regular_user):
        init_db(engine=engine, force=True)
        _patch_config(monkeypatch, config)
        archive = await self._make_archive(db_session, regular_user)
        monkeypatch.setattr(download_tasks, "_acquire_slot", lambda *_: None)

        self = _task_self(retries=download_tasks._SLOT_MAX_RETRIES)
        # Celery's retry() raises MaxRetriesExceededError when retries are spent.
        self.retry.side_effect = MaxRetriesExceededError("spent")
        result = await asyncio.to_thread(download_tasks.build_download_archive.run.__func__, self, archive.id)
        assert result is None
        await db_session.refresh(archive)
        assert archive.status == "failed"
        assert "timed out" in archive.error

    async def test_build_success(self, db_session, engine, config, monkeypatch, regular_user, tmp_path):
        init_db(engine=engine, force=True)
        _patch_config(monkeypatch, config)
        archive = await self._make_archive(db_session, regular_user)

        slot_client = MagicMock()
        monkeypatch.setattr(download_tasks, "_acquire_slot", lambda *_: slot_client)
        monkeypatch.setattr("songhive.storage.get_storage", lambda *_: get_storage(config.storage))

        zip_path = tmp_path / "out.zip"
        zip_path.write_bytes(b"PK\x05\x06" + b"\x00" * 18)
        materialize = AsyncMock(return_value=(zip_path, [{"ref": "t1", "error": "gone"}]))
        monkeypatch.setattr("songhive.services.downloads.materialize_archive", materialize)

        result = await asyncio.to_thread(download_tasks.build_download_archive.run.__func__, _task_self(), archive.id)
        assert result is None

        await db_session.refresh(archive)
        assert archive.status == "ready"
        assert archive.archive_file_id is not None
        assert archive.item_errors == [{"ref": "t1", "error": "gone"}]
        assert archive.completed_at is not None
        slot_client.zrem.assert_called_once_with(download_tasks._SLOT_KEY, archive.id)

        notif = (
            await db_session.execute(select(Notification).where(Notification.user_id == regular_user.id))
        ).scalar_one()
        assert notif.payload["archive_id"] == archive.id

    async def test_build_materialize_failure_marks_failed(
        self, db_session, engine, config, monkeypatch, regular_user, tmp_path
    ):
        init_db(engine=engine, force=True)
        _patch_config(monkeypatch, config)
        archive = await self._make_archive(db_session, regular_user)

        monkeypatch.setattr(download_tasks, "_acquire_slot", lambda *_: MagicMock())
        monkeypatch.setattr("songhive.storage.get_storage", lambda *_: get_storage(config.storage))
        monkeypatch.setattr(
            "songhive.services.downloads.materialize_archive",
            AsyncMock(side_effect=RuntimeError("item fetch blew up")),
        )

        await asyncio.to_thread(download_tasks.build_download_archive.run.__func__, _task_self(), archive.id)
        await db_session.refresh(archive)
        assert archive.status == "failed"
        assert "item fetch blew up" in archive.error


class TestCleanupArchives:
    async def test_fails_stale_and_deletes_expired(self, db_session, engine, config, monkeypatch):
        init_db(engine=engine, force=True)
        now = datetime.now(timezone.utc)
        old = now - timedelta(hours=48)

        stale = DownloadArchive(user_id="u1", label="stale", items=[], status="processing")
        expired = DownloadArchive(
            user_id="u1",
            label="expired",
            items=[],
            status="ready",
            completed_at=old,
        )
        fresh = DownloadArchive(
            user_id="u1",
            label="fresh",
            items=[],
            status="ready",
            completed_at=now,
        )
        running = DownloadArchive(user_id="u1", label="running", items=[], status="processing")
        db_session.add_all([stale, expired, fresh, running])
        await db_session.commit()
        # Backdate the stale row past ``stale_run_hours`` (the onupdate default
        # would otherwise rewrite ``updated_at`` on flush).
        await db_session.execute(update(DownloadArchive).where(DownloadArchive.id == stale.id).values(updated_at=old))

        storage_service = StorageService(get_storage(config.storage), config.storage)
        removed = await download_tasks._cleanup_archives(db_session, storage_service, config)
        await db_session.commit()

        assert removed == 1
        await db_session.refresh(stale)
        assert stale.status == "failed"
        assert stale.completed_at is not None
        assert await db_session.get(DownloadArchive, expired.id) is None
        assert await db_session.get(DownloadArchive, fresh.id) is not None
        assert await db_session.get(DownloadArchive, running.id) is not None
        await db_session.refresh(running)
        assert running.status == "processing"

    async def test_cleanup_task(self, db_session, engine, config, monkeypatch, regular_user):
        init_db(engine=engine, force=True)
        _patch_config(monkeypatch, config)
        monkeypatch.setattr("songhive.storage.get_storage", lambda *_: get_storage(config.storage))

        expired = DownloadArchive(
            user_id=regular_user.id,
            label="old",
            items=[],
            status="failed",
            completed_at=datetime.now(timezone.utc) - timedelta(days=7),
        )
        db_session.add(expired)
        await db_session.commit()
        expired_id = expired.id

        removed = await asyncio.to_thread(download_tasks.cleanup_completed_downloads)
        assert removed == 1
        db_session.expire_all()
        assert await db_session.get(DownloadArchive, expired_id) is None
