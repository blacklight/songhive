"""
Tests for the content moderation reports service.
"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi import status
from sqlalchemy import select

from songhive.federation.fetch import FetchError
from songhive.models.notification import Notification, NotificationType
from songhive.models.report import Report
from songhive.models.user import User
from songhive.services import federation as federation_service
from songhive.services import remote_content
from songhive.services import reports as report_service
from songhive.services.remote_content import RemoteActorResult

BOB_ACTOR = "https://remote.example/users/bob"
BOB_INBOX = "https://remote.example/users/bob/inbox"


@pytest.fixture
def fed_config(config):
    fed = config.model_copy(deep=True)
    fed.federation.enabled = True
    fed.federation.instance_domain = "music.example.com"
    fed.federation.private_key_path = Path(fed.storage.local_path).parent / "actor.pem"
    return fed


@pytest.fixture
def deliver_mock(monkeypatch):
    """Capture activities enqueued on ``deliver_activity``."""
    mock = MagicMock()
    monkeypatch.setattr("songhive.tasks.federation.deliver_activity", mock)
    return mock


def _remote_actor(actor_url: str = BOB_ACTOR, inbox: str = BOB_INBOX) -> RemoteActorResult:
    return RemoteActorResult(
        actor_url=actor_url,
        username="bob",
        domain="remote.example",
        display_name="Bob Remote",
        inbox_url=inbox,
    )


def _patch_remote_lookup(monkeypatch, actor: RemoteActorResult):
    async def _lookup(session, config, target, **kwargs):
        return actor

    monkeypatch.setattr(remote_content, "lookup_remote_actor", _lookup)


async def _provision(db_session, fed_config, *users):
    """Provision actor keys for ``users`` (see ``test_follows._provision``)."""
    for user in users:
        federation_service.ensure_user_actor(user, fed_config)
    await db_session.commit()
    for user in users:
        await db_session.execute(select(User).where(User.id == user.id).execution_options(populate_existing=True))


@pytest.mark.asyncio
async def test_create_report(db_session, config, regular_user):
    """create_report persists a new pending report."""
    report = await report_service.create_report(
        db_session,
        config,
        reporter=regular_user,
        target_type="track",
        target_id="track-1",
        reason="spam",
        description="bad track",
    )
    assert report.reporter_id == regular_user.id
    assert report.target_type == "track"
    assert report.target_id == "track-1"
    assert report.reason == "spam"
    assert report.description == "bad track"
    assert report.status == "pending"


@pytest.mark.asyncio
async def test_create_report_invalid_target_type(db_session, config, regular_user):
    """create_report rejects an unknown target_type."""
    with pytest.raises(report_service.ReportError) as exc_info:
        await report_service.create_report(
            db_session,
            config,
            reporter=regular_user,
            target_type="bad",
            target_id="x",
            reason="spam",
        )
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_create_report_invalid_reason(db_session, config, regular_user):
    """create_report rejects an unknown reason."""
    with pytest.raises(report_service.ReportError) as exc_info:
        await report_service.create_report(
            db_session,
            config,
            reporter=regular_user,
            target_type="track",
            target_id="x",
            reason="bad",
        )
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_list_reports_with_filters(db_session, config, regular_user):
    """list_reports filters by status and target_type."""
    r1 = await report_service.create_report(
        db_session,
        config,
        reporter=regular_user,
        target_type="track",
        target_id="t1",
        reason="spam",
    )
    await report_service.create_report(
        db_session,
        config,
        reporter=regular_user,
        target_type="album",
        target_id="a1",
        reason="copyright",
    )
    r3 = await report_service.create_report(
        db_session,
        config,
        reporter=regular_user,
        target_type="track",
        target_id="t2",
        reason="harassment",
    )

    # Update statuses so filters are meaningful.
    r1.status = "resolved"
    r3.status = "resolved"
    await db_session.flush()

    all_reports, total = await report_service.list_reports(db_session)
    assert total == 3

    by_status, _ = await report_service.list_reports(db_session, status="resolved")
    assert len(by_status) == 2

    by_type, _ = await report_service.list_reports(db_session, target_type="track")
    assert len(by_type) == 2

    both, _ = await report_service.list_reports(
        db_session,
        status="resolved",
        target_type="track",
    )
    assert len(both) == 2


@pytest.mark.asyncio
async def test_update_report(db_session, config, regular_user, make_user):
    """update_report sets the status and returns the previous status."""
    admin = await make_user("admin_report", role="admin", email_verified=True)
    report = await report_service.create_report(
        db_session,
        config,
        reporter=regular_user,
        target_type="track",
        target_id="t1",
        reason="spam",
    )

    updated, old = await report_service.update_report(
        db_session,
        report.id,
        reviewed_by=admin.id,
        status="resolved",
        resolution_notes="fixed",
    )
    assert updated is report
    assert old == "pending"
    assert updated.status == "resolved"
    assert updated.reviewed_by == admin.id
    assert updated.resolution_notes == "fixed"
    assert updated.reviewed_at is not None


@pytest.mark.asyncio
async def test_update_report_invalid_status(db_session):
    """update_report rejects an unknown status."""
    with pytest.raises(report_service.ReportError) as exc_info:
        await report_service.update_report(
            db_session,
            "missing-id",
            reviewed_by="admin",
            status="bad",
        )
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_update_report_missing(db_session, regular_user):
    """update_report raises 404 for a missing report."""
    with pytest.raises(report_service.ReportError) as exc_info:
        await report_service.update_report(
            db_session,
            "missing-id",
            reviewed_by=regular_user.id,
            status="resolved",
        )
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_create_report_local_actor_by_username(db_session, config, regular_user, other_user):
    """Actor reports resolve a bare local username to the canonical actor URL."""
    report = await report_service.create_report(
        db_session,
        config,
        reporter=regular_user,
        target_type="actor",
        target_id="other",
        reason="harassment",
    )
    assert report.target_type == "user"
    assert report.target_id == str(other_user.id)
    assert report.target_actor_url == "urn:songhive:user:other"
    assert report.forwarded is False


@pytest.mark.asyncio
async def test_create_report_local_actor_legacy_id(db_session, config, regular_user, other_user):
    """Legacy ``target_type="user"`` callers may still pass the user's id."""
    report = await report_service.create_report(
        db_session,
        config,
        reporter=regular_user,
        target_type="user",
        target_id=str(other_user.id),
        reason="spam",
    )
    assert report.target_type == "user"
    assert report.target_id == str(other_user.id)
    assert report.target_actor_url == "urn:songhive:user:other"


@pytest.mark.asyncio
async def test_create_report_self_rejected(db_session, config, regular_user):
    """Users cannot report their own account."""
    with pytest.raises(report_service.ReportError) as exc_info:
        await report_service.create_report(
            db_session,
            config,
            reporter=regular_user,
            target_type="user",
            target_id="regular",
            reason="spam",
        )
    assert "yourself" in str(exc_info.value)


@pytest.mark.asyncio
async def test_create_report_remote_actor(db_session, config, regular_user, monkeypatch):
    """An undereferenceable remote actor URL is still a valid report target."""

    # A blocked/unreachable actor fails resolution — the canonical URL is
    # still kept as the report target.
    async def _blocked(session, config, target, **kwargs):
        raise FetchError("Domain is not allowed", status_code=403)

    monkeypatch.setattr(remote_content, "lookup_remote_actor", _blocked)
    report = await report_service.create_report(
        db_session,
        config,
        reporter=regular_user,
        target_type="actor",
        target_id="https://blocked.example/users/bob",
        reason="spam",
        forward=True,
    )
    assert report.target_type == "user"
    assert report.target_id == "https://blocked.example/users/bob"
    assert report.target_actor_url == "https://blocked.example/users/bob"
    # No inbox could be resolved, so nothing was forwarded.
    assert report.forwarded is False


@pytest.mark.asyncio
async def test_create_report_notifies_admins(db_session, config, regular_user, other_user, make_user):
    """Every active admin except the reporter gets a ``report`` notification."""
    admin = await make_user("admin_notify", role="admin", email_verified=True)
    inactive_admin = await make_user("admin_inactive", role="admin", is_active=False)

    report = await report_service.create_report(
        db_session,
        config,
        reporter=regular_user,
        target_type="actor",
        target_id="other",
        reason="spam",
    )

    rows = (
        (await db_session.execute(select(Notification).where(Notification.type == NotificationType.REPORT)))
        .scalars()
        .all()
    )
    assert [row.user_id for row in rows] == [str(admin.id)]
    assert str(inactive_admin.id) not in [row.user_id for row in rows]
    assert rows[0].payload["report_id"] == str(report.id)
    assert rows[0].payload["target_actor_url"] == "urn:songhive:user:other"
    assert rows[0].actor_url == "urn:songhive:user:regular"


@pytest.mark.asyncio
async def test_forward_remote_report_delivers_flag(db_session, fed_config, regular_user, deliver_mock, monkeypatch):
    """``forward=True`` on a remote actor report enqueues an AP ``Flag``."""
    _patch_remote_lookup(monkeypatch, _remote_actor())
    await _provision(db_session, fed_config, regular_user)

    report = await report_service.create_report(
        db_session,
        fed_config,
        reporter=regular_user,
        target_type="actor",
        target_id=BOB_ACTOR,
        reason="harassment",
        description="abusive",
        forward=True,
    )

    assert report.forwarded is True
    deliver_mock.delay.assert_called_once()
    activity, inbox_url, key_id, _key = deliver_mock.delay.call_args[0]
    assert activity["type"] == "Flag"
    assert activity["actor"] == regular_user.actor_url
    assert activity["object"] == [BOB_ACTOR]
    assert "harassment" in activity["content"]
    assert "abusive" in activity["content"]
    assert inbox_url == BOB_INBOX
    assert key_id == f"{regular_user.actor_url}#main-key"


@pytest.mark.asyncio
async def test_forward_remote_report_without_keys(db_session, fed_config, regular_user, deliver_mock, monkeypatch):
    """Without federation keys the report is saved but ``forwarded`` stays false."""
    _patch_remote_lookup(monkeypatch, _remote_actor())

    report = await report_service.create_report(
        db_session,
        fed_config,
        reporter=regular_user,
        target_type="actor",
        target_id=BOB_ACTOR,
        reason="spam",
        forward=True,
    )

    assert report.forwarded is False
    deliver_mock.delay.assert_not_called()


@pytest.mark.asyncio
async def test_admin_list_reports_includes_reporter_username(client, db_session, make_user, auth_headers):
    """The admin report list exposes the reporter's username."""
    admin = await make_user("admin", role="admin")
    reporter = await make_user("reporter_user", email_verified=True)
    target = await make_user("reported_user", email_verified=True)
    db_session.add(Report(reporter_id=reporter.id, target_type="user", target_id=target.id, reason="spam"))
    await db_session.commit()

    response = client.get("/api/v1/admin/reports", headers=auth_headers(admin))
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data[0]["reporter_username"] == "reporter_user"
    assert data[0]["forwarded"] is False


@pytest.mark.asyncio
async def test_create_report_api_actor_target(client, make_user, auth_headers):
    """The public endpoint accepts actor reports by username."""
    reporter = await make_user("reporter_user", email_verified=True)
    await make_user("reported_user", email_verified=True)

    response = client.post(
        "/api/v1/reports",
        headers=auth_headers(reporter),
        json={
            "target_type": "actor",
            "target_id": "reported_user",
            "reason": "spam",
        },
    )
    assert response.status_code == status.HTTP_201_CREATED
    data = response.json()
    assert data["target_type"] == "user"
    assert data["target_actor_url"] == "urn:songhive:user:reported_user"
    assert data["forwarded"] is False
