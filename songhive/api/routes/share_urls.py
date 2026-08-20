"""
Share-URL token routes: revocable short links for unauthenticated access.
"""

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from ...config.schema import SonghiveConfig
from ...models.share_token import ShareToken
from ...models.user import User
from ...services import acl, sharing
from ..deps import get_config, get_current_user, get_db
from ..middleware.rate_limit import rate_limit_account
from ._common import load_and_authorize, validate_item_type

router = APIRouter(prefix="/share-urls")


class ShareTokenCreate(BaseModel):
    """Payload for creating a share URL token."""

    item_type: str
    item_id: str
    expires_at: Optional[datetime] = None

    @field_validator("item_type")
    @classmethod
    def _check_item_type(cls, value: str) -> str:
        return validate_item_type(value)


class ShareTokenCreated(BaseModel):
    """Response when a share URL token is created (raw token included once)."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    url: str
    token: str
    expires_at: Optional[datetime] = None


class ShareTokenResponse(BaseModel):
    """Public share-token response (raw token never included)."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    expires_at: Optional[datetime] = None
    revoked_at: Optional[datetime] = None
    created_at: datetime


def _build_share_url(request: Request, config: SonghiveConfig, raw_token: str) -> str:
    """Return the public short URL for a raw token."""
    domain = config.federation.instance_domain
    if domain:
        return f"https://{domain}/api/v1/share/{raw_token}"
    base = str(request.base_url).rstrip("/")
    return f"{base}/api/v1/share/{raw_token}"


@router.post(
    "/",
    response_model=ShareTokenCreated,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit_account)],
)
async def create_share_url(
    body: ShareTokenCreate,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    config: SonghiveConfig = Depends(get_config),
):
    """Create a revocable share URL for an item."""
    await load_and_authorize(db, current_user, body.item_type, body.item_id)

    token, raw = await sharing.create_share_token(
        db,
        body.item_type,
        body.item_id,
        current_user.id,
        expires_at=body.expires_at,
    )
    await db.commit()

    return ShareTokenCreated(
        id=token.id,
        url=_build_share_url(request, config, raw),
        token=raw,
        expires_at=token.expires_at,
    )


@router.get("/", response_model=List[ShareTokenResponse])
async def list_share_urls(
    item_type: str = Query(...),
    item_id: str = Query(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List share URL tokens for an item."""
    await load_and_authorize(db, current_user, item_type, item_id)

    tokens = await sharing.list_share_tokens(db, item_type, item_id)
    return [ShareTokenResponse.model_validate(t) for t in tokens]


@router.delete("/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_share_url(
    token_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Revoke a share URL token by id.

    Missing and unauthorized requests both return 404 to avoid ID enumeration.
    """
    token = await db.get(ShareToken, token_id)
    if token is None or not await acl.can_manage(db, current_user, token.item_type, token.item_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Not found",
        )

    await sharing.revoke_share_token(db, token_id)
    await db.commit()
    return None
