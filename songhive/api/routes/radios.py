"""
Radio routes - dynamic radio generation.
"""

from typing import List, Optional, cast

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from ...models._enums import Visibility
from ...models.radio import Radio
from ...models.user import User
from ...services import music
from .._common import Pagination, get_pagination
from ..deps import get_current_user, get_current_user_optional, get_db, require_access
from ._common import HasOwnerId, redact_owner

router = APIRouter(prefix="/radios")


class RadioResponse(BaseModel):
    """Public radio response."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    description: Optional[str] = None
    owner_id: Optional[str] = None
    visibility: str = Visibility.PRIVATE.value


class RadioCreate(BaseModel):
    """Radio creation payload."""

    name: str
    description: Optional[str] = None
    config: Optional[str] = None


@router.get("/", response_model=List[RadioResponse])
async def list_radios(
    response: Response,
    user: Optional[User] = Depends(get_current_user_optional),
    pagination: Pagination = Depends(get_pagination),
    db: AsyncSession = Depends(get_db),
):
    """List radios visible to the requester."""
    total = await music.count_radios(db, user=user)
    rows = await music.list_radios(db, user=user, limit=pagination.limit, offset=pagination.offset)
    pagination.set_total(response, total)
    return [
        RadioResponse(
            id=str(r.id),
            name=r.name,
            description=r.description,
            owner_id=redact_owner(cast(HasOwnerId, r), user),
            visibility=r.visibility,
        )
        for r in rows
    ]


@router.post("/", response_model=RadioResponse, status_code=201)
async def create_radio(
    body: RadioCreate,
    visibility: Visibility = Query(Visibility.PRIVATE),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a new radio owned by the current user."""
    radio = Radio(
        name=body.name,
        owner_id=current_user.id,
        description=body.description,
        visibility=visibility.value,
        config=body.config,
    )
    db.add(radio)
    await db.commit()

    return RadioResponse(
        id=str(radio.id),
        name=radio.name,
        description=radio.description,
        owner_id=radio.owner_id,
        visibility=radio.visibility,
    )


@router.get(
    "/{radio_id}",
    response_model=RadioResponse,
    dependencies=[Depends(require_access("radio"))],
)
async def get_radio(
    radio_id: str,
    user: Optional[User] = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    """Get a radio by ID."""
    radio = await music.get_radio(db, radio_id)
    # ``require_access`` already loads the row and raises 404 when missing.
    assert radio is not None

    return RadioResponse(
        id=str(radio.id),
        name=radio.name,
        description=radio.description,
        owner_id=redact_owner(cast(HasOwnerId, radio), user),
        visibility=radio.visibility,
    )


@router.get(
    "/{radio_id}/tracks",
    dependencies=[Depends(require_access("radio"))],
)
async def get_radio_tracks(
    radio_id: str,
    count: int = Query(10, ge=1, le=50),
):
    """Get the next batch of tracks for a radio."""
    # Radio track generation is not yet implemented; the endpoint is protected
    # by the ACL so only viewers of the radio can call it.
    return []
