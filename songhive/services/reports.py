"""
Content moderation reports service.
"""

from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.report import Report
from .acl import ITEM_TYPES

VALID_REASONS = {"spam", "copyright", "harassment", "illegal", "other"}
VALID_STATUSES = {"pending", "reviewing", "resolved", "dismissed"}
VALID_TARGET_TYPES = ITEM_TYPES | {"user"}


class ReportError(ValueError):
    """Raised when a report operation cannot be completed."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


async def create_report(
    session: AsyncSession,
    *,
    reporter_id: str,
    target_type: str,
    target_id: str,
    reason: str,
    description: Optional[str] = None,
) -> Report:
    """Create and return a new content report."""
    if target_type not in VALID_TARGET_TYPES:
        raise ReportError(f"Invalid target_type: {target_type}")
    if reason not in VALID_REASONS:
        raise ReportError(f"Invalid reason: {reason}")

    report = Report(
        reporter_id=reporter_id,
        target_type=target_type,
        target_id=target_id,
        reason=reason,
        description=description,
        status="pending",
    )
    session.add(report)
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
