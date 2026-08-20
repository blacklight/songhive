"""
Library routes.
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from ...models._enums import Visibility
from ...models.library import Library
from ...models.user import User
from ...services import music
from ..deps import get_current_user, get_current_user_optional, get_db, require_access
from ._common import redact_owner

router = APIRouter(prefix="/libraries")


class LibraryResponse(BaseModel):
    """Public library response."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    owner_id: Optional[str] = None
    description: Optional[str] = None
    visibility: str = Visibility.PRIVATE.value


class LibraryCreate(BaseModel):
    """Library creation payload."""

    name: str
    description: Optional[str] = None


@router.get("/", response_model=List[LibraryResponse])
async def list_libraries(
    user: Optional[User] = Depends(get_current_user_optional),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """List libraries visible to the requester."""
    rows = await music.list_libraries(db, user=user, limit=limit, offset=offset)
    return [
        LibraryResponse(
            id=str(lib.id),
            name=lib.name,
            owner_id=redact_owner(lib, user),
            description=lib.description,
            visibility=lib.visibility,
        )
        for lib in rows
    ]


@router.post("/", response_model=LibraryResponse, status_code=201)
async def create_library(
    body: LibraryCreate,
    visibility: Visibility = Query(Visibility.PRIVATE),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a new library owned by the current user."""
    library = Library(
        name=body.name,
        owner_id=current_user.id,
        description=body.description,
        visibility=visibility.value,
    )
    db.add(library)
    await db.commit()

    return LibraryResponse(
        id=str(library.id),
        name=library.name,
        owner_id=library.owner_id,
        description=library.description,
        visibility=library.visibility,
    )


@router.get(
    "/{library_id}",
    response_model=LibraryResponse,
    dependencies=[Depends(require_access("library"))],
)
async def get_library(
    library_id: str,
    user: Optional[User] = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    """Get a library by ID."""
    library = await music.get_library(db, library_id)
    # ``require_access`` already loads the row and raises 404 when missing.
    assert library is not None

    return LibraryResponse(
        id=str(library.id),
        name=library.name,
        owner_id=redact_owner(library, user),
        description=library.description,
        visibility=library.visibility,
    )
