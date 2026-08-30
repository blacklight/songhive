"""
Tests for the system stats service.
"""

import asyncio
import json

import pytest

from songhive.config.schema import SonghiveConfig
from songhive.models._enums import Visibility
from songhive.models.artist import Artist
from songhive.models.library import Library
from songhive.models.playlist import Playlist
from songhive.models.stored_file import StoredFile
from songhive.models.track import Track
from songhive.models.user import User
from songhive.services import stats as stats_service
from songhive.tasks.celery import celery_app


@pytest.mark.asyncio
async def test_get_user_stats(db_session):
    """_get_user_stats returns correct aggregates."""
    for i in range(3):
        user = User(
            username=f"u{i}",
            email=f"u{i}@example.com",
            password_hash="x",
            is_active=True,
            role="user",
        )
        db_session.add(user)
    admin = User(username="admin", email="admin@example.com", password_hash="x", role="admin")
    db_session.add(admin)
    await db_session.flush()

    stats = await stats_service._get_user_stats(db_session)
    assert stats["total_users"] == 4
    assert stats["active_users"] == 4
    assert stats["users_by_role"] == {"user": 3, "admin": 1}
    assert stats["recent_registrations"] == 4


@pytest.mark.asyncio
async def test_get_content_stats(db_session, regular_user):
    """_get_content_stats counts tracks, albums, playlists and libraries."""
    artist = Artist(name="Stats Artist")
    library = Library(name="Stats Library", owner_id=regular_user.id, visibility=Visibility.PUBLIC.value)
    playlist = Playlist(name="Stats Playlist", owner_id=regular_user.id, visibility=Visibility.PUBLIC.value)
    db_session.add_all([artist, library, playlist])
    await db_session.flush()

    track = Track(
        title="Stats Track",
        artist_id=artist.id,
        owner_id=regular_user.id,
        visibility=Visibility.PUBLIC.value,
    )
    db_session.add(track)
    await db_session.flush()

    stats = await stats_service._get_content_stats(db_session)
    assert stats["total_tracks"] == 1
    assert stats["total_albums"] == 0
    assert stats["total_playlists"] == 1
    assert stats["total_libraries"] == 1


@pytest.mark.asyncio
async def test_get_storage_stats(db_session):
    """_get_storage_stats aggregates stored files."""
    file1 = StoredFile(
        storage_path="files/a/b/c1",
        storage_backend="local",
        content_type="audio/mpeg",
        size=100,
        sha256="a" * 64,
    )
    file2 = StoredFile(
        storage_path="files/a/b/c2",
        storage_backend="s3",
        content_type="audio/mpeg",
        size=250,
        sha256="b" * 64,
    )
    db_session.add_all([file1, file2])
    await db_session.flush()

    stats = await stats_service._get_storage_stats(db_session)
    assert stats["total_files"] == 2
    assert stats["total_size_bytes"] == 350
    assert isinstance(stats["total_size_bytes"], int)
    assert json.dumps(stats)  # ensure the result is JSON-serializable
    by_backend = {b["backend"]: b for b in stats["files_by_backend"]}
    assert by_backend["local"]["count"] == 1
    assert by_backend["local"]["size"] == 100
    assert by_backend["s3"]["count"] == 1
    assert by_backend["s3"]["size"] == 250


@pytest.mark.asyncio
async def test_get_federation_stats_enabled():
    """_get_federation_stats returns details when federation is enabled."""
    config = SonghiveConfig(
        auth={"secret_key": "a" * 32},
        federation={
            "enabled": True,
            "instance_domain": "example.com",
            "instance_name": "Test",
            "instance_description": "A test",
        },
    )
    stats = await stats_service._get_federation_stats(config)
    assert stats["enabled"] is True
    assert stats["instance_domain"] == "example.com"
    assert stats["instance_name"] == "Test"


class _FakeCeleryInspect:
    """Stand-in for celery.app.control.Inspect."""

    def __init__(self, workers, active, scheduled, reserved, registered, stats):
        self._workers = workers
        self._active = active
        self._scheduled = scheduled
        self._reserved = reserved
        self._registered = registered
        self._stats = stats

    def ping(self):
        return self._workers

    def active(self):
        return self._active

    def scheduled(self):
        return self._scheduled

    def reserved(self):
        return self._reserved

    def registered(self):
        return self._registered

    def stats(self):
        return self._stats


def _fake_inspect_factory(workers, active, scheduled, reserved, registered, stats):
    def _make(**_):
        return _FakeCeleryInspect(workers, active, scheduled, reserved, registered, stats)

    return _make


@pytest.mark.asyncio
async def test_inspect_celery_with_workers(db_session, monkeypatch):
    """_inspect_celery aggregates worker statistics when workers are present."""
    workers = {"worker1@host": {"ok": "pong"}}
    active = {"worker1@host": [{"name": "a"}]}
    scheduled = {"worker1@host": [{"name": "b"}]}
    reserved = {"worker1@host": [{"name": "c"}]}
    registered = {"worker1@host": ["task.one", "task.two"]}
    worker_stats = {"worker1@host": {"total": {"tasks": 42}}}

    monkeypatch.setattr(
        celery_app.control,
        "inspect",
        _fake_inspect_factory(workers, active, scheduled, reserved, registered, worker_stats),
    )

    result = await asyncio.to_thread(stats_service._inspect_celery)
    assert result["available"] is True
    assert result["worker_count"] == 1
    assert result["workers"] == ["worker1@host"]
    assert result["active_tasks"] == 1
    assert result["scheduled_tasks"] == 1
    assert result["reserved_tasks"] == 1
    assert result["registered_task_count"] == 2
    assert result["registered_tasks"] == ["task.one", "task.two"]
    assert result["total_tasks_processed"] == 42


@pytest.mark.asyncio
async def test_get_celery_stats_exception(db_session, monkeypatch):
    """_get_celery_stats returns an unavailable marker on errors."""

    def _boom():
        raise RuntimeError("boom")

    monkeypatch.setattr(stats_service, "_inspect_celery", _boom)
    result = await stats_service._get_celery_stats()
    assert result["available"] is False
    assert "boom" in result["error"]


@pytest.mark.asyncio
async def test_get_all_stats_uses_redis_cache(db_session, fake_redis, config, monkeypatch):
    """get_all_stats returns cached stats when Redis has a valid payload."""
    cached = {"cached": True}
    await fake_redis.set(stats_service.STATS_CACHE_KEY, json.dumps(cached))

    # Ensure the real computation would not be reached.
    async def _no_compute(*args, **kwargs):
        raise AssertionError("should not run")

    monkeypatch.setattr(stats_service, "_compute_all_stats", _no_compute)

    result = await stats_service.get_all_stats(db_session, config, fake_redis)
    assert result == cached


@pytest.mark.asyncio
async def test_get_all_stats_ignores_bad_cache(db_session, fake_redis, config, monkeypatch):
    """A malformed cache value is ignored and recomputed."""
    await fake_redis.set(stats_service.STATS_CACHE_KEY, "not-json")

    monkeypatch.setattr(
        celery_app.control,
        "inspect",
        _fake_inspect_factory({}, {}, {}, {}, {}, {}),
    )

    result = await stats_service.get_all_stats(db_session, config, fake_redis)
    assert "users" in result
    assert result["federation"]["enabled"] is False


@pytest.mark.asyncio
async def test_get_all_stats_serializes_decimal(db_session, fake_redis, config, monkeypatch):
    """Decimal values in the stats payload must be JSON-serializable for Redis caching."""
    from decimal import Decimal

    async def _storage_with_decimal(_session):
        return {
            "total_files": 1,
            "total_size_bytes": Decimal(1024),
            "files_by_backend": [
                {"backend": "local", "count": Decimal(1), "size": Decimal(1024)},
            ],
        }

    async def _no_celery():
        return {"available": False}

    monkeypatch.setattr(stats_service, "_get_storage_stats", _storage_with_decimal)
    monkeypatch.setattr(stats_service, "_get_celery_stats", _no_celery)

    result = await stats_service.get_all_stats(db_session, config, fake_redis)

    assert result["storage"]["total_size_bytes"] == 1024
    assert result["storage"]["files_by_backend"][0]["size"] == 1024

    cached = await fake_redis.get(stats_service.STATS_CACHE_KEY)
    assert cached is not None
    parsed = json.loads(cached)
    assert parsed["storage"]["total_size_bytes"] == 1024
    assert isinstance(parsed["storage"]["total_size_bytes"], int)
