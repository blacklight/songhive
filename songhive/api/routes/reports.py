"""
Content moderation report routes.

Public report submission lives at ``/api/v1/reports``; admin review endpoints
are mounted at ``/api/v1/admin/reports``.
"""

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ...models.user import User
from ...services import audit
from ...services import reports as report_service
from .._common import Pagination, client_ip, get_pagination
from ..deps import get_current_user, get_db, require_admin

router = APIRouter()
admin_router = APIRouter(prefix="/admin/reports")


class ReportResponse(BaseModel):
    """Report API response."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    reporter_id: str
    target_type: str
    target_id: str
    reason: str
    description: Optional[str]
    status: str
    reviewed_by: Optional[str]
    reviewed_at: Optional[datetime]
    resolution_notes: Optional[str]
    created_at: datetime


class ReportCreateRequest(BaseModel):
    """Request body for submitting a report."""

    target_type: str = Field(..., min_length=1)
    target_id: str = Field(..., min_length=1)
    reason: str = Field(..., min_length=1)
    description: Optional[str] = None


class ReportUpdateRequest(BaseModel):
    """Request body for resolving a report."""

    status: str = Field(..., min_length=1)
    resolution_notes: Optional[str] = None


@router.post(
    "/reports",
    response_model=ReportResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_report(
    body: ReportCreateRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Submit a content report (authenticated users only)."""
    try:
        report = await report_service.create_report(
            db,
            reporter_id=user.id,
            target_type=body.target_type,
            target_id=body.target_id,
            reason=body.reason,
            description=body.description,
        )
    except report_service.ReportError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    return ReportResponse.model_validate(report)


@admin_router.get(
    "/",
    response_model=List[ReportResponse],
    dependencies=[Depends(require_admin)],
)
async def list_reports(
    response: Response,
    status: Optional[str] = Query(None),
    target_type: Optional[str] = Query(None),
    pagination: Pagination = Depends(get_pagination),
    db: AsyncSession = Depends(get_db),
):
    """List content reports (admin only)."""
    reports, total = await report_service.list_reports(
        db,
        status=status,
        target_type=target_type,
        limit=pagination.limit,
        offset=pagination.offset,
    )
    pagination.set_total(response, total)
    return [ReportResponse.model_validate(report) for report in reports]


@admin_router.put(
    "/{report_id}",
    response_model=ReportResponse,
)
async def update_report(
    report_id: str,
    body: ReportUpdateRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Resolve or dismiss a content report (admin only)."""
    try:
        report, old_status = await report_service.update_report(
            db,
            report_id,
            reviewed_by=admin.id,
            status=body.status,
            resolution_notes=body.resolution_notes,
        )
    except report_service.ReportError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    await audit.log_action(
        db,
        actor_id=admin.id,
        action="report.update",
        target_type="report",
        target_id=report_id,
        details={
            "old_status": old_status,
            "new_status": report.status,
            "resolution_notes": body.resolution_notes,
        },
        ip_address=client_ip(request),
    )

    return ReportResponse.model_validate(report)
