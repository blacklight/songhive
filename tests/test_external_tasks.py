"""
Celery task tests for the external-library subsystem.
"""

import asyncio
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from songhive.config import SonghiveConfig
from songhive.config.schema import SonghiveConfig as SonghiveConfigClass
from songhive.external._fake import FakeExternalAdapter
from songhive.external.registry import register_external_adapter
from songhive.models.base import Base
from songhive.models.external_library import ExternalLibrary
from songhive.models.external_sync_run import ExternalSyncRun
from songhive.models.external_track import ExternalTrack
from songhive.models.library import Library
from songhive.models.track import Track
from songhive.models.user import User
from songhive.services import secrets
from songhive.tasks.external_libraries import (
    scan_scheduled_syncs_task,
    sync_external_library_task,
    write_back_metadata_task,
)


@pytest.fixture(autouse=True)
def _register_fake_adapter():
    """Register the fake adapter for every test."""
    register_external_adapter("fake", FakeExternalAdapter)


def _make_config(db_url: str) -> SonghiveConfigClass:
    """Return a test configuration backed by the given database URL."""
    return SonghiveConfig(
        server={
            "host": "127.0.0.1",
            "port": 8000,
            "debug": True,
            "cors_origins": ["http://localhost:8080"],
        },
        database={"url": db_url},
        federation={"enabled": False},
        auth={"secret_key": "a" * 32},
        storage={
            "local_path": "/tmp/media",
            "backend": "local",
        },
    )


@pytest.fixture
def _patched_config(monkeypatch, tmp_path):
    """Patch load_config to use a fresh SQLite database for each test."""
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'tasks.db'}"
    config = _make_config(db_url)
    for target in (
        "songhive.config.load_config",
        "songhive.config.loader.load_config",
        "songhive.tasks.external_libraries.load_config",
        "songhive.external.sync.load_config",
    ):
        monkeypatch.setattr(target, lambda *args, **kwargs: config)
    return db_url


@pytest.fixture
def _patched_redis(monkeypatch, fake_redis_server):
    """Patch Redis client creation so every task uses a fresh fake Redis."""
    from fakeredis.aioredis import FakeRedis

    def _make_redis(_config=None):
        return FakeRedis(server=fake_redis_server, decode_responses=True)

    monkeypatch.setattr("songhive.services.redis._redis_client", None)
    monkeypatch.setattr("songhive.services.redis.get_redis_client", _make_redis)
    monkeypatch.setattr("songhive.tasks.external_libraries.get_redis_client", _make_redis)


def _create_tables(db_url: str):
    engine = create_async_engine(db_url, poolclass=NullPool)

    async def _run():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await engine.dispose()

    asyncio.run(_run())


def test_sync_external_library_task_creates_run(_patched_config, _patched_redis, monkeypatch):
    """sync_external_library_task.apply() creates a sync run and returns its status."""
    _create_tables(_patched_config)
    db_url = _patched_config

    async def _setup():
        engine = create_async_engine(db_url, poolclass=NullPool)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            user = User(
                username="elib",
                email="elib@example.com",
                password_hash="x" * 60,
                role="user",
                is_active=True,
            )
            session.add(user)
            await session.flush()

            library = Library(name="External Library", owner_id=str(user.id), visibility="private")
            session.add(library)
            await session.flush()

            items = {
                "track1.flac": {
                    "data": list(b"audio1"),
                    "metadata": {
                        "title": "Track 1",
                        "artist": "Artist A",
                        "album": "Album A",
                        "duration": 180.0,
                    },
                },
            }
            encrypted = secrets.encrypt_json({"items": items})
            external_library = ExternalLibrary(
                library_id=str(library.id),
                provider_type="fake",
                config=encrypted,
                enabled=True,
                created_by_id=str(user.id),
            )
            session.add(external_library)
            await session.commit()
            return str(external_library.id), str(user.id)

    external_library_id, user_id = asyncio.run(_setup())

    result = sync_external_library_task.apply(
        args=(external_library_id, "manual", user_id),
    )

    assert result.successful()
    payload = result.result
    assert payload["status"] == "success"
    assert payload["sync_run_id"] is not None


def test_scan_scheduled_syncs_enqueues_due_library(_patched_config, _patched_redis, monkeypatch):
    """scan_scheduled_syncs_task enqueues a sync for a due library and skips one with an active run."""
    _create_tables(_patched_config)
    db_url = _patched_config

    async def _setup():
        engine = create_async_engine(db_url, poolclass=NullPool)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            user = User(
                username="elib",
                email="elib@example.com",
                password_hash="x" * 60,
                role="user",
                is_active=True,
            )
            session.add(user)
            await session.flush()

            due_songhive_library = Library(name="Due External Library", owner_id=str(user.id), visibility="private")
            session.add(due_songhive_library)
            skipped_songhive_library = Library(
                name="Skipped External Library", owner_id=str(user.id), visibility="private"
            )
            session.add(skipped_songhive_library)
            await session.flush()

            items = {
                "track1.flac": {
                    "data": list(b"audio1"),
                    "metadata": {
                        "title": "Track 1",
                        "artist": "Artist A",
                        "album": "Album A",
                        "duration": 180.0,
                    },
                },
            }
            encrypted = secrets.encrypt_json({"items": items})

            due_library = ExternalLibrary(
                library_id=str(due_songhive_library.id),
                provider_type="fake",
                config=encrypted,
                enabled=True,
                sync_enabled=True,
                sync_interval_seconds=60,
                created_by_id=str(user.id),
            )
            session.add(due_library)
            await session.flush()

            skipped_library = ExternalLibrary(
                library_id=str(skipped_songhive_library.id),
                provider_type="fake",
                config=encrypted,
                enabled=True,
                sync_enabled=True,
                sync_interval_seconds=60,
                created_by_id=str(user.id),
            )
            session.add(skipped_library)
            await session.flush()

            active_run = ExternalSyncRun(
                external_library_id=str(skipped_library.id),
                triggered_by="manual",
                status="running",
                started_at=datetime.now(timezone.utc),
            )
            session.add(active_run)
            await session.commit()
            return str(due_library.id), str(skipped_library.id)

    due_id, skipped_id = asyncio.run(_setup())

    mock_delay = MagicMock()
    monkeypatch.setattr(
        "songhive.tasks.external_libraries.sync_external_library_task",
        MagicMock(delay=mock_delay),
    )

    result = scan_scheduled_syncs_task.apply()

    assert result.successful()
    assert result.result == 1
    mock_delay.assert_called_once_with(due_id, triggered_by="scheduled")


def test_write_back_metadata_task_updates_provider_and_clears_flag(_patched_config, _patched_redis):
    """write_back_metadata_task applies Songhive edits to the provider and clears the pending flag."""
    _create_tables(_patched_config)
    db_url = _patched_config

    async def _setup():
        engine = create_async_engine(db_url, poolclass=NullPool)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            user = User(
                username="elib",
                email="elib@example.com",
                password_hash="x" * 60,
                role="user",
                is_active=True,
            )
            session.add(user)
            await session.flush()

            library = Library(name="External Library", owner_id=str(user.id), visibility="private")
            session.add(library)
            await session.flush()

            items = {
                "track1.flac": {
                    "data": list(b"audio1"),
                    "metadata": {
                        "title": "Track 1",
                        "artist": "Artist A",
                        "album": "Album A",
                        "duration": 180.0,
                    },
                },
            }
            encrypted = secrets.encrypt_json({"items": items})
            external_library = ExternalLibrary(
                library_id=str(library.id),
                provider_type="fake",
                config=encrypted,
                enabled=True,
                created_by_id=str(user.id),
            )
            session.add(external_library)
            await session.commit()
            return str(external_library.id), str(user.id)

    external_library_id, user_id = asyncio.run(_setup())

    sync_result = sync_external_library_task.apply(
        args=(external_library_id, "manual", user_id),
    )
    assert sync_result.successful()

    async def _prepare_writeback():
        engine = create_async_engine(db_url, poolclass=NullPool)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            ext_track = (
                (
                    await session.execute(
                        select(ExternalTrack).where(ExternalTrack.external_library_id == external_library_id)
                    )
                )
                .scalars()
                .one()
            )
            track = await session.get(Track, ext_track.track_id)
            track.title = "Local Title"
            track.metadata_updated_at = datetime.now(timezone.utc)
            ext_track.write_back_pending = True
            await session.commit()
            return str(ext_track.id)

    ext_track_id = asyncio.run(_prepare_writeback())

    result = write_back_metadata_task.apply(args=(ext_track_id,))

    assert result.successful()
    assert result.result is True

    async def _verify():
        engine = create_async_engine(db_url, poolclass=NullPool)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            ext_track = await session.get(ExternalTrack, ext_track_id)
            track = await session.get(Track, ext_track.track_id)
            assert ext_track.write_back_pending is False
            assert track.external_metadata_synced_at is not None

    asyncio.run(_verify())


def test_write_back_metadata_task_noop_when_not_pending(_patched_config, _patched_redis):
    """write_back_metadata_task is a no-op when write_back_pending is false."""
    _create_tables(_patched_config)
    db_url = _patched_config

    async def _setup():
        engine = create_async_engine(db_url, poolclass=NullPool)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            user = User(
                username="elib",
                email="elib@example.com",
                password_hash="x" * 60,
                role="user",
                is_active=True,
            )
            session.add(user)
            await session.flush()

            library = Library(name="External Library", owner_id=str(user.id), visibility="private")
            session.add(library)
            await session.flush()

            items = {
                "track1.flac": {
                    "data": list(b"audio1"),
                    "metadata": {
                        "title": "Track 1",
                        "artist": "Artist A",
                        "album": "Album A",
                        "duration": 180.0,
                    },
                },
            }
            encrypted = secrets.encrypt_json({"items": items})
            external_library = ExternalLibrary(
                library_id=str(library.id),
                provider_type="fake",
                config=encrypted,
                enabled=True,
                created_by_id=str(user.id),
            )
            session.add(external_library)
            await session.commit()
            return str(external_library.id), str(user.id)

    external_library_id, user_id = asyncio.run(_setup())

    sync_result = sync_external_library_task.apply(
        args=(external_library_id, "manual", user_id),
    )
    assert sync_result.successful()

    async def _get_ext_track_id():
        engine = create_async_engine(db_url, poolclass=NullPool)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            ext_track = (
                (
                    await session.execute(
                        select(ExternalTrack).where(ExternalTrack.external_library_id == external_library_id)
                    )
                )
                .scalars()
                .one()
            )
            return str(ext_track.id)

    ext_track_id = asyncio.run(_get_ext_track_id())

    result = write_back_metadata_task.apply(args=(ext_track_id,))

    assert result.successful()
    assert result.result is True


def test_write_back_metadata_task_noop_when_write_tags_unsupported(_patched_config, _patched_redis):
    """write_back_metadata_task returns True without calling the adapter when write_tags is false."""
    _create_tables(_patched_config)
    db_url = _patched_config

    async def _setup():
        engine = create_async_engine(db_url, poolclass=NullPool)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            user = User(
                username="elib",
                email="elib@example.com",
                password_hash="x" * 60,
                role="user",
                is_active=True,
            )
            session.add(user)
            await session.flush()

            library = Library(name="External Library", owner_id=str(user.id), visibility="private")
            session.add(library)
            await session.flush()

            items = {
                "track1.flac": {
                    "data": list(b"audio1"),
                    "metadata": {
                        "title": "Track 1",
                        "artist": "Artist A",
                        "album": "Album A",
                        "duration": 180.0,
                    },
                },
            }
            encrypted = secrets.encrypt_json({"items": items})
            external_library = ExternalLibrary(
                library_id=str(library.id),
                provider_type="fake",
                config=encrypted,
                enabled=True,
                capabilities={
                    "write_tags": False,
                    "list_items": True,
                    "compute_hash": True,
                },
                created_by_id=str(user.id),
            )
            session.add(external_library)
            await session.commit()
            return str(external_library.id), str(user.id)

    external_library_id, user_id = asyncio.run(_setup())

    sync_result = sync_external_library_task.apply(
        args=(external_library_id, "manual", user_id),
    )
    assert sync_result.successful()

    async def _prepare():
        engine = create_async_engine(db_url, poolclass=NullPool)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            ext_track = (
                (
                    await session.execute(
                        select(ExternalTrack).where(ExternalTrack.external_library_id == external_library_id)
                    )
                )
                .scalars()
                .one()
            )
            ext_track.write_back_pending = True
            await session.commit()
            return str(ext_track.id)

    ext_track_id = asyncio.run(_prepare())

    result = write_back_metadata_task.apply(args=(ext_track_id,))

    assert result.successful()
    assert result.result is True
