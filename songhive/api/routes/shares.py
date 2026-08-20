"""
Share-grant routes: owner/admin CRUD for giving specific users access to items.
"""

from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from ...models.share_grant import ShareGrant
from ...models.user import User
from ...services import sharing
from ..deps import get_current_user, get_db
from ._common import load_and_authorize, validate_item_type

router = APIRouter(prefix="/shares")


class ShareGrantCreate(BaseModel):
    """Payload for creating a share grant."""

    item_type: str
    item_id: str
    user_id: str

    @field_validator("item_type")
    @classmethod
    def _check_item_type(cls, value: str) -> str:
        return validate_item_type(value)


class ShareGrantResponse(BaseModel):
    """Public share-grant response."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    item_type: str
    item_id: str
    user_id: str
    created_at: datetime


@router.post("/", response_model=ShareGrantResponse, status_code=status.HTTP_201_CREATED)
async def create_share_grant(
    body: ShareGrantCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Grant a specific user access to an item."""
    await load_and_authorize(db, current_user, body.item_type, body.item_id)

    grant = await sharing.create_share_grant(
        db,
        body.item_type,
        body.item_id,
        body.user_id,
        created_by=current_user.id,
    )
    await db.commit()

    return ShareGrantResponse.model_validate(grant)


@router.get("/", response_model=List[ShareGrantResponse])
async def list_share_grants(
    item_type: str = Query(...),
    item_id: str = Query(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all share grants for an item."""
    await load_and_authorize(db, current_user, item_type, item_id)

    grants = await sharing.list_share_grants(db, item_type, item_id)
    return [ShareGrantResponse.model_validate(g) for g in grants]


@router.delete("/{share_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_share_grant(
    share_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Revoke a share grant by id."""
    grant = await db.get(ShareGrant, share_id)
    if grant is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Not found",
        )

    await load_and_authorize(db, current_user, grant.item_type, grant.item_id)
    await sharing.revoke_share_grant_by_id(db, share_id)
    await db.commit()
    return None
