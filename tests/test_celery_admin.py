"""
Tests for the Celery admin service and API endpoints.
"""

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
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
