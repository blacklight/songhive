"""
Tests for the Celery admin service and API endpoints.
"""

import json
from unittest.mock import MagicMock

import pytest
from fastapi import status
from sqlalchemy import select

from songhive.models.audit_log import AuditLog
from songhive.services import celery_admin


def _make_fake_celery_app(workers=None, active=None):
    """Build a stand-in Celery app for service tests."""
    app = MagicMock()
    app.conf = MagicMock()
    app.conf.get.return_value = None

    inspect = MagicMock()
    inspect.ping.return_value = workers or {}
    inspect.active.return_value = active or {}
    app.control.inspect.return_value = inspect

    return app


def _configure_queue_stats_app(
    app,
    workers=None,
    active=None,
    reserved=None,
    scheduled=None,
    broker_depth=0,
    result_states=None,
):
    """Configure a fake Celery app for ``get_celery_queue_stats`` tests."""
    inspect = app.control.inspect.return_value
    inspect.ping.return_value = workers or {}
    inspect.active.return_value = active or {}
    inspect.reserved.return_value = reserved or {}
    inspect.scheduled.return_value = scheduled or {}

    queue = MagicMock()
    queue.name = "celery"
    app.amqp.queues = {"celery": queue}
    channel = app.connection.return_value.__enter__.return_value.default_channel
    channel.queue_declare.return_value = ("celery", broker_depth, 0)

    backend = app.backend
    if result_states is not None:
        backend.task_keyprefix = "celery-task-meta-"
        backend.client.scan_iter.return_value = [f"celery-task-meta-{i}" for i in range(len(result_states))]
        backend.client.mget.return_value = [json.dumps({"status": state}) for state in result_states]
        backend.decode.side_effect = json.loads
    else:
        backend.task_keyprefix = None

    return app


@pytest.fixture
def fake_celery_app(monkeypatch):
    """Patch the module-level Celery app with a controllable fake."""
    app = _make_fake_celery_app()
    monkeypatch.setattr(celery_admin, "celery_app", app)
    return app


@pytest.mark.asyncio
async def test_list_active_celery_tasks_with_active_workers(fake_celery_app):
    """Active tasks are flattened and enriched with runtime information."""
    active = {
        "worker1@host": [
            {
                "id": "task-1",
                "name": "songhive.tasks.storage.cleanup_orphaned_files",
                "args": ["arg1"],
                "kwargs": {"dry_run": True},
                "hostname": "worker1@host",
                "acknowledged": True,
                "delivery_info": {"exchange": "", "routing_key": "celery"},
                "time_start": 1_700_000_000.0,
            }
        ]
    }
    workers = {"worker1@host": {"ok": "pong"}}
    fake_celery_app.control.inspect.return_value.ping.return_value = workers
    fake_celery_app.control.inspect.return_value.active.return_value = active

    tasks = await celery_admin.list_active_celery_tasks()

    assert len(tasks) == 1
    assert tasks[0]["task_id"] == "task-1"
    assert tasks[0]["worker"] == "worker1@host"
    assert tasks[0]["runtime"] is not None
    assert isinstance(tasks[0]["runtime"], float)


@pytest.mark.asyncio
async def test_list_active_celery_tasks_no_workers(fake_celery_app):
    """An empty worker list results in an empty task list."""
    fake_celery_app.control.inspect.return_value.ping.return_value = {}

    tasks = await celery_admin.list_active_celery_tasks()

    assert tasks == []


@pytest.mark.asyncio
async def test_list_active_celery_tasks_handles_inspect_errors(fake_celery_app):
    """Inspect failures are wrapped in CeleryAdminError."""
    fake_celery_app.control.inspect.side_effect = RuntimeError("broker down")

    with pytest.raises(celery_admin.CeleryAdminError):
        await celery_admin.list_active_celery_tasks()


@pytest.mark.asyncio
async def test_terminate_celery_tasks(fake_celery_app):
    """Terminating tasks issues a bulk revoke with terminate=True."""
    task_ids = ["task-1", "task-2"]

    terminated = await celery_admin.terminate_celery_tasks(task_ids)

    assert terminated == 2
    fake_celery_app.control.revoke.assert_called_once_with(
        task_ids,
        terminate=True,
        signal="SIGTERM",
        reply=False,
        timeout=1.0,
    )


@pytest.mark.asyncio
async def test_terminate_celery_tasks_propagates_errors(fake_celery_app):
    """Revoke failures are wrapped in CeleryAdminError."""
    fake_celery_app.control.revoke.side_effect = RuntimeError("broker down")

    with pytest.raises(celery_admin.CeleryAdminError):
        await celery_admin.terminate_celery_tasks(["task-1"])


@pytest.mark.asyncio
async def test_admin_list_celery_tasks(client, make_user, auth_headers, monkeypatch):
    """Admins can list currently running Celery tasks."""
    admin = await make_user("admin", role="admin")

    app = _make_fake_celery_app(
        workers={"worker1@host": {"ok": "pong"}},
        active={
            "worker1@host": [
                {
                    "id": "task-1",
                    "name": "songhive.tasks.storage.cleanup_orphaned_files",
                    "args": [],
                    "kwargs": {},
                    "time_start": 1_700_000_000.0,
                    "hostname": "worker1@host",
                    "acknowledged": True,
                    "delivery_info": {"exchange": "", "routing_key": "celery"},
                }
            ]
        },
    )
    monkeypatch.setattr(celery_admin, "celery_app", app)

    response = client.get("/api/v1/admin/celery/tasks", headers=auth_headers(admin))
    assert response.status_code == status.HTTP_200_OK

    data = response.json()
    assert len(data) == 1
    assert data[0]["task_id"] == "task-1"
    assert data[0]["worker"] == "worker1@host"
    assert data[0]["name"] == "songhive.tasks.storage.cleanup_orphaned_files"


@pytest.mark.asyncio
async def test_admin_list_celery_tasks_broker_error(client, make_user, auth_headers, monkeypatch):
    """A broker failure when listing tasks returns 503."""
    admin = await make_user("admin", role="admin")

    app = _make_fake_celery_app()
    app.control.inspect.side_effect = RuntimeError("broker down")
    monkeypatch.setattr(celery_admin, "celery_app", app)

    response = client.get("/api/v1/admin/celery/tasks", headers=auth_headers(admin))
    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE


@pytest.mark.asyncio
async def test_admin_terminate_celery_tasks(client, db_session, make_user, auth_headers, monkeypatch):
    """Admins can terminate running Celery tasks and the action is audited."""
    admin = await make_user("admin", role="admin")

    app = _make_fake_celery_app()
    monkeypatch.setattr(celery_admin, "celery_app", app)

    response = client.post(
        "/api/v1/admin/celery/terminate",
        headers=auth_headers(admin),
        json={"task_ids": ["task-1", "task-2"]},
    )
    assert response.status_code == status.HTTP_200_OK

    data = response.json()
    assert data["terminated"] == 2

    app.control.revoke.assert_called_once_with(
        ["task-1", "task-2"],
        terminate=True,
        signal="SIGTERM",
        reply=False,
        timeout=1.0,
    )

    result = await db_session.execute(select(AuditLog).where(AuditLog.action == "celery.terminate"))
    log = result.scalar_one_or_none()
    assert log is not None
    assert log.actor_id == str(admin.id)
    assert log.details["count"] == 2
    assert log.details["task_ids"] == ["task-1", "task-2"]


@pytest.mark.asyncio
async def test_admin_terminate_celery_tasks_requires_admin(client, make_user, auth_headers):
    """Non-admins cannot terminate Celery tasks."""
    user = await make_user("regular", email_verified=True)

    response = client.post(
        "/api/v1/admin/celery/terminate",
        headers=auth_headers(user),
        json={"task_ids": ["task-1"]},
    )
    assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.asyncio
async def test_admin_terminate_celery_tasks_rejects_empty_list(client, make_user, auth_headers):
    """The terminate endpoint requires at least one task id."""
    admin = await make_user("admin", role="admin")

    response = client.post(
        "/api/v1/admin/celery/terminate",
        headers=auth_headers(admin),
        json={"task_ids": []},
    )
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


@pytest.mark.asyncio
async def test_get_celery_queue_stats_aggregates_counts(monkeypatch):
    """Queue stats combine inspect data, broker depth, and backend results."""
    app = _configure_queue_stats_app(
        _make_fake_celery_app(),
        workers={"worker1@host": {"ok": "pong"}},
        active={
            "worker1@host": [
                {"id": "task-1", "name": "songhive.task.a"},
                {"id": "task-2", "name": "songhive.task.b"},
            ]
        },
        reserved={"worker1@host": [{"id": "task-3", "name": "songhive.task.a"}]},
        scheduled={
            "worker1@host": [{"eta": "2024-01-01T00:00:00Z", "request": {"id": "task-4", "name": "songhive.task.c"}}]
        },
        broker_depth=5,
        result_states=["SUCCESS", "FAILURE", "FAILURE"],
    )
    monkeypatch.setattr(celery_admin, "celery_app", app)

    stats = await celery_admin.get_celery_queue_stats()

    assert stats["processing"] == 2
    assert stats["queued"] == 7
    assert stats["total"] == 9
    assert stats["failed"] == 2
    assert stats["completed"] == 1
    assert stats["by_name"] == [
        {"name": "songhive.task.a", "count": 2},
        {"name": "songhive.task.b", "count": 1},
        {"name": "songhive.task.c", "count": 1},
    ]


@pytest.mark.asyncio
async def test_get_celery_queue_stats_no_workers(monkeypatch):
    """Without workers, broker backlog is still reported and backend stats are null."""
    app = _configure_queue_stats_app(
        _make_fake_celery_app(),
        workers={},
        broker_depth=3,
        result_states=None,
    )
    monkeypatch.setattr(celery_admin, "celery_app", app)

    stats = await celery_admin.get_celery_queue_stats()

    assert stats["processing"] == 0
    assert stats["queued"] == 3
    assert stats["total"] == 3
    assert stats["failed"] is None
    assert stats["completed"] is None
    assert stats["by_name"] == []


@pytest.mark.asyncio
async def test_get_celery_queue_stats_handles_inspect_errors(monkeypatch):
    """Inspect failures are wrapped in CeleryAdminError."""
    app = _make_fake_celery_app()
    app.control.inspect.side_effect = RuntimeError("broker down")
    monkeypatch.setattr(celery_admin, "celery_app", app)

    with pytest.raises(celery_admin.CeleryAdminError):
        await celery_admin.get_celery_queue_stats()


@pytest.mark.asyncio
async def test_admin_celery_queue_stats_endpoint(client, make_user, auth_headers, monkeypatch):
    """Admins can read aggregated Celery queue statistics."""
    admin = await make_user("admin", role="admin")

    app = _configure_queue_stats_app(
        _make_fake_celery_app(),
        workers={"worker1@host": {"ok": "pong"}},
        active={"worker1@host": [{"id": "task-1", "name": "songhive.task.a"}]},
        reserved={"worker1@host": [{"id": "task-2", "name": "songhive.task.b"}]},
        broker_depth=4,
        result_states=["SUCCESS", "SUCCESS", "FAILURE"],
    )
    monkeypatch.setattr(celery_admin, "celery_app", app)

    response = client.get("/api/v1/admin/celery/queue", headers=auth_headers(admin))
    assert response.status_code == status.HTTP_200_OK

    data = response.json()
    assert data["processing"] == 1
    assert data["queued"] == 5
    assert data["total"] == 6
    assert data["failed"] == 1
    assert data["completed"] == 2
    assert data["by_name"] == [
        {"name": "songhive.task.a", "count": 1},
        {"name": "songhive.task.b", "count": 1},
    ]


@pytest.mark.asyncio
async def test_admin_celery_queue_stats_broker_error(client, make_user, auth_headers, monkeypatch):
    """A broker failure when reading queue stats returns 503."""
    admin = await make_user("admin", role="admin")

    app = _make_fake_celery_app()
    app.control.inspect.side_effect = RuntimeError("broker down")
    monkeypatch.setattr(celery_admin, "celery_app", app)

    response = client.get("/api/v1/admin/celery/queue", headers=auth_headers(admin))
    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE


@pytest.mark.asyncio
async def test_admin_celery_queue_stats_requires_admin(client, make_user, auth_headers):
    """Non-admins cannot read Celery queue statistics."""
    user = await make_user("regular", email_verified=True)

    response = client.get("/api/v1/admin/celery/queue", headers=auth_headers(user))
    assert response.status_code == status.HTTP_403_FORBIDDEN


def test_celery_queue_routing():
    """Scrobbles and bulk fan-out must not share the default queue.

    A fresh external library sync enqueues tens of thousands of enrichment
    jobs; if scrobbling tasks land on the same queue they starve for hours
    (this broke production once already).
    """
    from songhive.tasks.celery import celery_app

    queue_names = {q.name for q in celery_app.conf.task_queues}
    assert queue_names == {"celery", "scrobbles", "bulk"}

    route = celery_app.amqp.router.route
    assert route({}, "songhive.tasks.scrobbling.now_playing")["queue"].name == "scrobbles"
    assert route({}, "songhive.tasks.scrobbling.scrobble")["queue"].name == "scrobbles"
    for bulk_task in (
        "songhive.tasks.musicbrainz.enrich_track",
        "songhive.tasks.images.enrich_images",
        "songhive.tasks.tags.sync_track_tags",
        "songhive.tasks.external_libraries.sync_external_library",
        "songhive.tasks.import_.scan_directory",
        "songhive.tasks.transcoding.transcode_upload",
    ):
        assert route({}, bulk_task)["queue"].name == "bulk", bulk_task
    # Interactive/low-volume tasks stay on the default queue.
    assert route({}, "songhive.tasks.federation.deliver_activity")["queue"].name == "celery"
    assert route({}, "songhive.tasks.email.send_notification_email")["queue"].name == "celery"
