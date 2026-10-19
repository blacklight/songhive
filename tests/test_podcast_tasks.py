"""
Tests for the podcast Celery tasks (tasks/podcasts.py).

The tasks run their DB work inside ``asyncio.run``, so tests invoke them
through ``asyncio.to_thread`` to keep a running loop out of the task body.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from celery.exceptions import Retry

from songhive.config.schema import PodcastsConfig
from songhive.models.base import init_db
from songhive.models.podcast import Podcast
from songhive.services import gpodder as gpodder_service
from songhive.services import podcasts as podcasts_service
from songhive.services.gpodder import GPodderError
from songhive.services.podcasts import FeedFetchError
from songhive.tasks import podcasts as podcast_tasks


def _task_self(retries: int = 0):
    """Stand-in for the bound Celery task object."""
    self = MagicMock()
    self.request.retries = retries
    self.max_retries = 3
    self.retry.side_effect = Retry()
    return self


def _podcasts_config(config, **overrides):
    params = {"enabled": True}
    params.update(overrides)
    return config.model_copy(update={"podcasts": PodcastsConfig(**params)})


def _patch_config(monkeypatch, config):
    monkeypatch.setattr("songhive.tasks.podcasts.load_config", lambda *_: config)


async def _run_task(fn, *args):
    """Invoke a Celery task body without a running loop on the task's side."""
    return await asyncio.to_thread(fn, *args)


class TestRefreshPodcast:
    async def test_missing_podcast_retries(self, db_session, engine, config, monkeypatch):
        """A missing podcast row retries — the enqueue races the commit."""
        init_db(engine=engine, force=True)
        _patch_config(monkeypatch, _podcasts_config(config))

        self = _task_self(retries=0)
        with pytest.raises(Retry):
            await _run_task(podcast_tasks.refresh_podcast.run.__func__, self, "missing-id")
        self.retry.assert_called_once()
        assert self.retry.call_args.kwargs["countdown"] == 5

    async def test_missing_podcast_drops_after_max_retries(self, db_session, engine, config, monkeypatch):
        init_db(engine=engine, force=True)
        _patch_config(monkeypatch, _podcasts_config(config))

        self = _task_self(retries=3)
        result = await _run_task(podcast_tasks.refresh_podcast.run.__func__, self, "missing-id")
        assert result is None
        self.retry.assert_not_called()

    async def test_refresh_success(self, db_session, engine, config, monkeypatch):
        init_db(engine=engine, force=True)
        _patch_config(monkeypatch, _podcasts_config(config))

        podcast = Podcast(feed_url="https://pod.example/feed.xml", title="Show")
        db_session.add(podcast)
        await db_session.commit()

        refresh = AsyncMock(return_value=2)
        monkeypatch.setattr(podcasts_service, "refresh_podcast", refresh)

        result = await _run_task(podcast_tasks.refresh_podcast.run.__func__, _task_self(), podcast.id)
        assert result == "done"
        refresh.assert_awaited_once()
        assert refresh.await_args.kwargs.get("force") is True

    async def test_refresh_feed_error_returns_error(self, db_session, engine, config, monkeypatch):
        init_db(engine=engine, force=True)
        _patch_config(monkeypatch, _podcasts_config(config))

        podcast = Podcast(feed_url="https://pod.example/broken.xml", title="Broken")
        db_session.add(podcast)
        await db_session.commit()

        refresh = AsyncMock(side_effect=FeedFetchError("404"))
        monkeypatch.setattr(podcasts_service, "refresh_podcast", refresh)

        result = await _run_task(podcast_tasks.refresh_podcast.run.__func__, _task_self(), podcast.id)
        assert result == "error"


class TestScanDuePodcasts:
    async def test_disabled_returns_zero(self, db_session, engine, config, monkeypatch):
        _patch_config(monkeypatch, _podcasts_config(config, enabled=False))
        assert await _run_task(podcast_tasks.scan_due_podcasts) == 0

    async def test_enqueues_due_feeds(self, db_session, engine, config, monkeypatch, _no_real_celery_broker):
        init_db(engine=engine, force=True)
        _patch_config(monkeypatch, _podcasts_config(config))

        due = AsyncMock(return_value=["pod-1", "pod-2"])
        monkeypatch.setattr(podcasts_service, "due_podcast_ids", due)

        assert await _run_task(podcast_tasks.scan_due_podcasts) == 2
        sent = [
            call.kwargs["args"][0] if call.kwargs.get("args") else call.args[1][0]
            for call in _no_real_celery_broker.call_args_list
        ]
        assert sorted(sent) == ["pod-1", "pod-2"]

    async def test_enqueue_failure_is_tolerated(self, db_session, engine, config, monkeypatch):
        init_db(engine=engine, force=True)
        _patch_config(monkeypatch, _podcasts_config(config))

        monkeypatch.setattr(podcasts_service, "due_podcast_ids", AsyncMock(return_value=["p1", "p2"]))

        calls = []

        def _flaky_delay(podcast_id):
            calls.append(podcast_id)
            if podcast_id == "p1":
                raise RuntimeError("broker down")

        monkeypatch.setattr(podcast_tasks.refresh_podcast, "delay", _flaky_delay)
        assert await _run_task(podcast_tasks.scan_due_podcasts) == 2
        assert calls == ["p1", "p2"]


def _sync_result(**overrides):
    result = MagicMock()
    result.subscribed = overrides.get("subscribed", 1)
    result.unsubscribed = overrides.get("unsubscribed", 0)
    result.pushed_adds = overrides.get("pushed_adds", 0)
    result.pushed_removes = overrides.get("pushed_removes", 0)
    return result


class TestSyncGpodderAccount:
    async def test_missing_user_retries(self, db_session, engine, config, monkeypatch):
        init_db(engine=engine, force=True)
        _patch_config(monkeypatch, _podcasts_config(config))

        self = _task_self(retries=0)
        with pytest.raises(Retry):
            await _run_task(podcast_tasks.sync_gpodder_account.run.__func__, self, "missing-user")
        self.retry.assert_called_once()

    async def test_missing_user_drops_after_max_retries(self, db_session, engine, config, monkeypatch):
        init_db(engine=engine, force=True)
        _patch_config(monkeypatch, _podcasts_config(config))

        self = _task_self(retries=3)
        result = await _run_task(podcast_tasks.sync_gpodder_account.run.__func__, self, "missing-user")
        assert result is None
        self.retry.assert_not_called()

    async def test_no_sync_config_returns_disabled(self, db_session, engine, config, monkeypatch, regular_user):
        init_db(engine=engine, force=True)
        _patch_config(monkeypatch, _podcasts_config(config))
        monkeypatch.setattr(gpodder_service, "get_sync_config", AsyncMock(return_value=None))

        result = await _run_task(podcast_tasks.sync_gpodder_account.run.__func__, _task_self(), regular_user.id)
        assert result == "disabled"

    async def test_disabled_config_returns_disabled(self, db_session, engine, config, monkeypatch, regular_user):
        init_db(engine=engine, force=True)
        _patch_config(monkeypatch, _podcasts_config(config))
        row = MagicMock(enabled=False)
        monkeypatch.setattr(gpodder_service, "get_sync_config", AsyncMock(return_value=row))

        result = await _run_task(podcast_tasks.sync_gpodder_account.run.__func__, _task_self(), regular_user.id)
        assert result == "disabled"

    async def test_sync_error_returns_error(self, db_session, engine, config, monkeypatch, regular_user):
        init_db(engine=engine, force=True)
        _patch_config(monkeypatch, _podcasts_config(config))
        row = MagicMock(enabled=True)
        monkeypatch.setattr(gpodder_service, "get_sync_config", AsyncMock(return_value=row))
        monkeypatch.setattr(gpodder_service, "run_sync", AsyncMock(side_effect=GPodderError("unreachable")))

        result = await _run_task(podcast_tasks.sync_gpodder_account.run.__func__, _task_self(), regular_user.id)
        assert result == "error"

    async def test_sync_success(self, db_session, engine, config, monkeypatch, regular_user):
        init_db(engine=engine, force=True)
        _patch_config(monkeypatch, _podcasts_config(config))
        row = MagicMock(enabled=True)
        monkeypatch.setattr(gpodder_service, "get_sync_config", AsyncMock(return_value=row))
        run_sync = AsyncMock(return_value=_sync_result(subscribed=2, unsubscribed=1))
        monkeypatch.setattr(gpodder_service, "run_sync", run_sync)

        result = await _run_task(podcast_tasks.sync_gpodder_account.run.__func__, _task_self(), regular_user.id)
        assert result == "done"
        run_sync.assert_awaited_once()


class TestScanDueGpodderSyncs:
    async def test_disabled_returns_zero(self, db_session, engine, config, monkeypatch):
        _patch_config(monkeypatch, _podcasts_config(config, enabled=False))
        assert await _run_task(podcast_tasks.scan_due_gpodder_syncs) == 0

    async def test_enqueues_due_users(self, db_session, engine, config, monkeypatch, _no_real_celery_broker):
        init_db(engine=engine, force=True)
        _patch_config(monkeypatch, _podcasts_config(config))
        monkeypatch.setattr(gpodder_service, "due_sync_user_ids", AsyncMock(return_value=["u1", "u2"]))

        assert await _run_task(podcast_tasks.scan_due_gpodder_syncs) == 2
        assert _no_real_celery_broker.call_count == 2

    async def test_enqueue_failure_is_tolerated(self, db_session, engine, config, monkeypatch):
        init_db(engine=engine, force=True)
        _patch_config(monkeypatch, _podcasts_config(config))
        monkeypatch.setattr(gpodder_service, "due_sync_user_ids", AsyncMock(return_value=["u1", "u2"]))

        def _flaky_delay(user_id):
            if user_id == "u2":
                raise RuntimeError("broker down")

        monkeypatch.setattr(podcast_tasks.sync_gpodder_account, "delay", _flaky_delay)
        assert await _run_task(podcast_tasks.scan_due_gpodder_syncs) == 2
