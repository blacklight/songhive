"""
Public short-URL resolver for share tokens.

``GET /api/v1/share/{token}`` maps a raw token to the matching item and
302-redirects to the item's public endpoint, setting a short-lived
``share_token`` cookie for ``require_access`` to validate.
"""

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ...services import acl, sharing
from ..deps import get_db
from ..middleware.rate_limit import rate_limit

router = APIRouter(prefix="/share")

_not_found = HTTPException(
    status_code=status.HTTP_404_NOT_FOUND,
    detail="Not found",
)


@router.get("/{token}", dependencies=[Depends(rate_limit)])
async def resolve_share_url(token: str, request: Request, db: AsyncSession = Depends(get_db)):
    """
    Resolve a raw share token to the item it grants access to.

    The raw token is exchanged for a short-lived ``share_token`` cookie so the
    long-lived secret is not persisted in browser state or access logs.
    """
    row = await sharing.get_valid_share_token(db, token)

    if row is None:
        raise _not_found

    plural = acl.get_item_plural(row.item_type)
    if plural is None:
        raise _not_found

    target = f"/api/v1/{plural}/{row.item_id}"
    response = RedirectResponse(target, status_code=status.HTTP_302_FOUND)
    response.set_cookie(
        key="share_token",
        value=token,
        max_age=300,
        httponly=True,
        secure=request.url.scheme == "https",
        samesite="lax",
        path="/",
    )
    return response
