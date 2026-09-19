"""
Content moderation reports service.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config.schema import SonghiveConfig
from ..models.notification import NotificationType
from ..models.report import Report
from ..models.user import User, UserRole
from . import moderation as moderation_service
from .acl import ITEM_TYPES

logger = logging.getLogger(__name__)

VALID_REASONS = {"spam", "copyright", "harassment", "illegal", "other"}
VALID_STATUSES = {"pending", "reviewing", "resolved", "dismissed"}
ACTOR_TARGET_TYPES = {"user", "actor"}
VALID_TARGET_TYPES = ITEM_TYPES | ACTOR_TARGET_TYPES


class ReportError(ValueError):
    """Raised when a report operation cannot be completed."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def _flag_activity(config: SonghiveConfig, report: Report, reporter: User) -> dict:
    """Build the ActivityPub ``Flag`` activity forwarded to remote instances."""
    domain = config.federation.instance_domain or ""
    content = report.reason
    if report.description:
        content = f"{report.reason}: {report.description}"
    return {
        "@context": [
            "https://www.w3.org/ns/activitystreams",
            {"toot": "http://joinmastodon.org/ns#"},
        ],
        "id": f"https://{domain}/reports/{report.id}#flag",
        "type": "Flag",
        "actor": reporter.actor_url,
        "content": content,
        "object": [report.target_actor_url],
        "published": datetime.now(timezone.utc).isoformat(),
    }


def _enqueue_forward(config: SonghiveConfig, report: Report, reporter: User, inbox_url: str) -> bool:
    """Queue the ``Flag`` delivery; returns whether it was enqueued."""
    if not (reporter.actor_url and reporter.private_key_pem):
        return False
    from ..tasks.federation import deliver_activity

    deliver_activity.delay(  # type: ignore[attr-defined]
        _flag_activity(config, report, reporter),
        inbox_url,
        f"{reporter.actor_url}#main-key",
        reporter.private_key_pem,
    )
    return True


async def notify_admins_of_report(session: AsyncSession, report: Report, reporter: User) -> None:
    """Send an in-app ``report`` notification to every active admin."""
    from . import notifications as notifications_service

    admins = (
        (
            await session.execute(
                select(User).where(
                    User.role == UserRole.ADMIN,
                    User.is_active.is_(True),
                    User.id != reporter.id,
                )
            )
        )
        .scalars()
        .all()
    )
    for admin in admins:
        await notifications_service.create_notification(
            session,
            user_id=str(admin.id),
            type=NotificationType.REPORT,
            actor_url=moderation_service.local_actor_url(reporter),
            payload={
                "report_id": str(report.id),
                "target_type": report.target_type,
                "target_actor_url": report.target_actor_url,
                "reason": report.reason,
            },
        )


async def create_report(
    session: AsyncSession,
    config: SonghiveConfig,
    *,
    reporter: User,
    target_type: str,
    target_id: str,
    reason: str,
    description: Optional[str] = None,
    forward: bool = False,
) -> Report:
    """
    Create and return a new content report.

    Actor reports (``target_type="user"``/``"actor"``) accept local
    usernames, ``@user@domain`` handles and actor URLs, and record the
    reported account's canonical actor URL so admins can moderate it
    directly. ``forward`` additionally delivers an ActivityPub ``Flag``
    to the reported actor's home instance (remote targets only).
    """
    if target_type not in VALID_TARGET_TYPES:
        raise ReportError(f"Invalid target_type: {target_type}")
    if reason not in VALID_REASONS:
        raise ReportError(f"Invalid reason: {reason}")

    canonical: Optional[str] = None
    inbox_url: Optional[str] = None
    if target_type in ACTOR_TARGET_TYPES:
        local_user: Optional[User] = None
        actor_data: Optional[dict] = None
        if target_type == "user":
            # Legacy callers pass the reported user's ``id`` directly.
            local_user = (await session.execute(select(User).where(User.id == target_id))).scalar_one_or_none()
        if local_user is not None:
            canonical = moderation_service.local_actor_url(local_user)
        else:
            canonical, local_user, actor_data = await moderation_service.resolve_moderation_target(
                session, config, target_id
            )
        if local_user is not None and str(local_user.id) == str(reporter.id):
            raise ReportError("You cannot report yourself")
        target_id = str(local_user.id) if local_user is not None else canonical
        if actor_data:
            inbox = actor_data.get("inbox")
            inbox_url = inbox if isinstance(inbox, str) else None

    report = Report(
        reporter_id=reporter.id,
        target_type="user" if target_type in ACTOR_TARGET_TYPES else target_type,
        target_id=target_id,
        target_actor_url=canonical,
        reason=reason,
        description=description,
        status="pending",
    )
    session.add(report)
    await session.flush()

    await notify_admins_of_report(session, report, reporter)

    if forward and canonical and inbox_url:
        report.forwarded = _enqueue_forward(config, report, reporter, inbox_url)
        if not report.forwarded:
            logger.warning("Report %s could not be forwarded: reporter has no federation keys", report.id)
        await session.flush()

    return report


def _apply_filters(
    stmt: Select[Any],
    *,
    status: Optional[str] = None,
    target_type: Optional[str] = None,
) -> Select[Any]:
    """Apply report filters to a statement."""
    if status:
        stmt = stmt.where(Report.status == status)
    if target_type:
        stmt = stmt.where(Report.target_type == target_type)
    return stmt


def _build_list_stmt(
    status: Optional[str] = None,
    target_type: Optional[str] = None,
) -> Select[Any]:
    """Build a filtered, ordered statement for reports."""
    stmt = select(Report).order_by(Report.created_at.desc())
    return _apply_filters(stmt, status=status, target_type=target_type)


async def list_reports(
    session: AsyncSession,
    *,
    status: Optional[str] = None,
    target_type: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[Report], int]:
    """Return a paginated list of reports and the total matching count."""
    stmt = _build_list_stmt(status=status, target_type=target_type)
    total_stmt = _apply_filters(
        select(func.count(Report.id)),
        status=status,
        target_type=target_type,
    )
    total = (await session.execute(total_stmt)).scalar() or 0

    stmt = stmt.offset(offset).limit(limit)
    result = await session.execute(stmt)
    return list(result.scalars().all()), total


async def get_report(session: AsyncSession, report_id: str) -> Optional[Report]:
    """Fetch a report by id."""
    result = await session.execute(select(Report).where(Report.id == report_id))
    return result.scalar_one_or_none()


async def update_report(
    session: AsyncSession,
    report_id: str,
    *,
    reviewed_by: str,
    status: str,
    resolution_notes: Optional[str] = None,
) -> tuple[Report, str]:
    """
    Update a report's review status.

    :returns: A tuple of ``(updated_report, previous_status)``.
    """
    if status not in VALID_STATUSES:
        raise ReportError(f"Invalid status: {status}")

    report = await get_report(session, report_id)
    if report is None:
        raise ReportError("Report not found", status_code=404)

    old_status = report.status
    report.status = status
    report.reviewed_by = reviewed_by
    report.reviewed_at = datetime.now(timezone.utc)
    report.resolution_notes = resolution_notes
    await session.flush()
    return report, old_status
