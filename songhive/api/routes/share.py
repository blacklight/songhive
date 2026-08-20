"""
Public short-URL resolver for share tokens.

``GET /api/v1/share/{token}`` maps a raw token to the matching item and
302-redirects to the item's public endpoint, carrying the token as a query
parameter for ``require_access`` to validate.
"""

from fastapi import APIRouter, Depends, HTTPException, status
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
async def resolve_share_url(token: str, db: AsyncSession = Depends(get_db)):
    """Resolve a raw share token to the item it grants access to."""
    row = await sharing.get_valid_share_token(db, token)

    if row is None:
        raise _not_found

    plural = acl.get_item_plural(row.item_type)
    if plural is None:
        raise _not_found

    target = f"/api/v1/{plural}/{row.item_id}?token={token}"
    return RedirectResponse(target, status_code=status.HTTP_302_FOUND)
