"""
Admin routes.
"""

from typing import List

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from ..deps import require_admin

router = APIRouter(prefix="/admin")


class AdminUserResponse(BaseModel):
    id: str
    username: str
    email: str
    is_active: bool
    is_admin: bool


@router.get("/users", response_model=List[AdminUserResponse])
async def list_users(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    _admin=Depends(require_admin),
):
    """List all users (admin only)."""
    # TODO: implement
    return []


@router.post("/users/{user_id}/promote", status_code=200)
async def promote_user(user_id: str, _admin=Depends(require_admin)):
    """Promote a user to admin."""
    # TODO: implement


@router.post("/users/{user_id}/deactivate", status_code=200)
async def deactivate_user(user_id: str, _admin=Depends(require_admin)):
    """Deactivate a user account."""
    # TODO: implement
