"""Scheduled external-library sync scanner tests."""

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from songhive.models.external_library import ExternalLibrary
from songhive.models.external_sync_run import ExternalSyncRun
from songhive.models.library import Library
from songhive.models.user import User
from songhive.tasks.external_libraries import scan_scheduled_syncs_task


async def _create_library(
    db_session,
    admin_user: User,
    *,
    sync_interval_seconds: int,
    sync_enabled: bool = True,
    enabled: bool = True,
    last_sync_completed_at: datetime | None = None,
) -> ExternalLibrary:
    """Create a library directly in the DB for scheduled-sync testing."""
    library = Library(
        name="Scheduled Sync Library",
        owner_id=str(admin_user.id),
        visibility="private",
    )
    db_session.add(library)
    await db_session.flush()

    external_library = ExternalLibrary(
        library_id=str(library.id),
        provider_type="fake",
        scope="admin",
        name="Scheduled Sync Library",
        enabled=enabled,
        sync_enabled=sync_enabled,
        sync_interval_seconds=sync_interval_seconds,
        last_sync_completed_at=last_sync_completed_at,
        config={"items": {}, "secret_key": "x"},
        created_by_id=str(admin_user.id),
    )
    db_session.add(external_library)
    await db_session.commit()
    return external_library


def _patch_task_env(monkeypatch, config):
    """Patch load_config and the cleanup helpers used by Celery tasks."""
    monkeypatch.setattr(
        "songhive.tasks.external_libraries.load_config",
        lambda *_, **__: config,
    )
    monkeypatch.setattr(
        "songhive.tasks.external_libraries.dispose_and_reset",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "songhive.tasks.external_libraries.close_redis_client",
        AsyncMock(),
    )


@pytest.mark.asyncio
async def test_scan_scheduled_syncs_enqueues_due_libraries(
    app,
    config,
    db_session,
    admin_user,
    fake_adapter,
    monkeypatch,
):
    """A due library is enqueued with triggered_by='scheduled'."""
    delay_mock = MagicMock()
    monkeypatch.setattr(
        "songhive.tasks.external_libraries.sync_external_library_task.delay",
        delay_mock,
    )
    _patch_task_env(monkeypatch, config)

    external_library = await _create_library(
        db_session,
        admin_user,
        sync_interval_seconds=1,
    )

    enqueued = await asyncio.to_thread(scan_scheduled_syncs_task)

    assert enqueued == 1
    delay_mock.assert_called_once()
    call = delay_mock.call_args
    assert call.args[0] == str(external_library.id)
    assert call.kwargs.get("triggered_by") == "scheduled"


@pytest.mark.asyncio
async def test_scan_scheduled_syncs_skips_not_due(
    app,
    config,
    db_session,
    admin_user,
    fake_adapter,
    monkeypatch,
):
    """A library synced recently is skipped."""
    delay_mock = MagicMock()
    monkeypatch.setattr(
        "songhive.tasks.external_libraries.sync_external_library_task.delay",
        delay_mock,
    )
    _patch_task_env(monkeypatch, config)

    await _create_library(
        db_session,
        admin_user,
        sync_interval_seconds=1,
        last_sync_completed_at=datetime.now(timezone.utc) - timedelta(seconds=5),
    )

    enqueued = await asyncio.to_thread(scan_scheduled_syncs_task)

    assert enqueued == 0
    delay_mock.assert_not_called()


@pytest.mark.asyncio
async def test_scan_scheduled_syncs_skips_sync_disabled(
    app,
    config,
    db_session,
    admin_user,
    fake_adapter,
    monkeypatch,
):
    """A library with sync_enabled=False is skipped."""
    delay_mock = MagicMock()
    monkeypatch.setattr(
        "songhive.tasks.external_libraries.sync_external_library_task.delay",
        delay_mock,
    )
    _patch_task_env(monkeypatch, config)

    await _create_library(
        db_session,
        admin_user,
        sync_interval_seconds=1,
        sync_enabled=False,
    )

    enqueued = await asyncio.to_thread(scan_scheduled_syncs_task)

    assert enqueued == 0
    delay_mock.assert_not_called()


@pytest.mark.asyncio
async def test_scan_scheduled_syncs_skips_active_run(
    app,
    config,
    db_session,
    admin_user,
    fake_adapter,
    monkeypatch,
):
    """A library with a queued/running sync run is skipped."""
    delay_mock = MagicMock()
    monkeypatch.setattr(
        "songhive.tasks.external_libraries.sync_external_library_task.delay",
        delay_mock,
    )
    _patch_task_env(monkeypatch, config)

    external_library = await _create_library(
        db_session,
        admin_user,
        sync_interval_seconds=1,
    )

    run = ExternalSyncRun(
        external_library_id=str(external_library.id),
        triggered_by="manual",
        status="running",
    )
    db_session.add(run)
    await db_session.commit()

    enqueued = await asyncio.to_thread(scan_scheduled_syncs_task)

    assert enqueued == 0
    delay_mock.assert_not_called()


@pytest.mark.asyncio
async def test_scan_scheduled_syncs_respects_max_concurrent(
    app,
    config,
    db_session,
    admin_user,
    fake_adapter,
    monkeypatch,
):
    """The scanner stops after reaching max_concurrent_syncs."""
    delay_mock = MagicMock()
    monkeypatch.setattr(
        "songhive.tasks.external_libraries.sync_external_library_task.delay",
        delay_mock,
    )
    _patch_task_env(monkeypatch, config)
    config.external_libraries.max_concurrent_syncs = 2

    for _ in range(3):
        await _create_library(
            db_session,
            admin_user,
            sync_interval_seconds=1,
        )

    enqueued = await asyncio.to_thread(scan_scheduled_syncs_task)

    assert enqueued == 2
    assert delay_mock.call_count == 2
