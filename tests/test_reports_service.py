"""
Tests for the content moderation reports service.
"""

import pytest

from songhive.services import reports as report_service


@pytest.mark.asyncio
async def test_create_report(db_session, regular_user):
    """create_report persists a new pending report."""
    report = await report_service.create_report(
        db_session,
        reporter_id=regular_user.id,
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
async def test_create_report_invalid_target_type(db_session, regular_user):
    """create_report rejects an unknown target_type."""
    with pytest.raises(report_service.ReportError) as exc_info:
        await report_service.create_report(
            db_session,
            reporter_id=regular_user.id,
            target_type="bad",
            target_id="x",
            reason="spam",
        )
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_create_report_invalid_reason(db_session, regular_user):
    """create_report rejects an unknown reason."""
    with pytest.raises(report_service.ReportError) as exc_info:
        await report_service.create_report(
            db_session,
            reporter_id=regular_user.id,
            target_type="track",
            target_id="x",
            reason="bad",
        )
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_list_reports_with_filters(db_session, regular_user):
    """list_reports filters by status and target_type."""
    r1 = await report_service.create_report(
        db_session,
        reporter_id=regular_user.id,
        target_type="track",
        target_id="t1",
        reason="spam",
    )
    await report_service.create_report(
        db_session,
        reporter_id=regular_user.id,
        target_type="album",
        target_id="a1",
        reason="copyright",
    )
    r3 = await report_service.create_report(
        db_session,
        reporter_id=regular_user.id,
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
async def test_update_report(db_session, regular_user, make_user):
    """update_report sets the status and returns the previous status."""
    admin = await make_user("admin_report", role="admin", email_verified=True)
    report = await report_service.create_report(
        db_session,
        reporter_id=regular_user.id,
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
