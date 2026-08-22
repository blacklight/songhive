"""
Audit log service.

Provides helpers to create and query ``AuditLog`` rows from the rest of the
application.
"""

from typing import Any, Optional

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.audit_log import AuditLog


async def log_action(
    session: AsyncSession,
    *,
    actor_id: Optional[str],
    action: str,
    target_type: Optional[str] = None,
    target_id: Optional[str] = None,
    details: Optional[dict] = None,
    ip_address: Optional[str] = None,
) -> AuditLog:
    """Create and flush a single audit log entry."""
    log = AuditLog(
        actor_id=actor_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        details=details,
        ip_address=ip_address,
    )
    session.add(log)
    await session.flush()
    return log


def _apply_filters(
    stmt: Select[Any],
    *,
    action: Optional[str] = None,
    actor_id: Optional[str] = None,
    target_type: Optional[str] = None,
    target_id: Optional[str] = None,
) -> Select[Any]:
    """Apply audit log filters to a statement."""
    if action:
        stmt = stmt.where(AuditLog.action == action)
    if actor_id:
        stmt = stmt.where(AuditLog.actor_id == actor_id)
    if target_type:
        stmt = stmt.where(AuditLog.target_type == target_type)
    if target_id:
        stmt = stmt.where(AuditLog.target_id == target_id)
    return stmt


def _build_list_stmt(
    action: Optional[str] = None,
    actor_id: Optional[str] = None,
    target_type: Optional[str] = None,
    target_id: Optional[str] = None,
) -> Select[Any]:
    """Build a filtered, ordered statement for audit log rows."""
    stmt = select(AuditLog).order_by(AuditLog.created_at.desc())
    return _apply_filters(
        stmt,
        action=action,
        actor_id=actor_id,
        target_type=target_type,
        target_id=target_id,
    )


async def list_audit_logs(
    session: AsyncSession,
    *,
    action: Optional[str] = None,
    actor_id: Optional[str] = None,
    target_type: Optional[str] = None,
    target_id: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[AuditLog], int]:
    """Return a paginated list of audit logs and the total matching count."""
    stmt = _build_list_stmt(
        action=action,
        actor_id=actor_id,
        target_type=target_type,
        target_id=target_id,
    )
    total = await count_audit_logs(
        session,
        action=action,
        actor_id=actor_id,
        target_type=target_type,
        target_id=target_id,
    )
    stmt = stmt.offset(offset).limit(limit)
    result = await session.execute(stmt)
    return list(result.scalars().all()), total


async def count_audit_logs(
    session: AsyncSession,
    *,
    action: Optional[str] = None,
    actor_id: Optional[str] = None,
    target_type: Optional[str] = None,
    target_id: Optional[str] = None,
) -> int:
    """Return the total number of audit logs matching the filters."""
    stmt = _apply_filters(
        select(func.count(AuditLog.id)),
        action=action,
        actor_id=actor_id,
        target_type=target_type,
        target_id=target_id,
    )
    result = await session.execute(stmt)
    return result.scalar() or 0
