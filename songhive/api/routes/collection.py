"""
Collection routes: save and remove items in the current user's collection.
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ...models.user import User
from ...services import acl, collection, remote_content
from .._common import Pagination, get_pagination
from ..deps import get_current_user, get_db

router = APIRouter(prefix="/collection")


class CollectionItemResponse(BaseModel):
    """Public collection item response."""

    id: str
    item_type: str
    item_id: str
    created_at: str


def _serialize(entry) -> CollectionItemResponse:
    """Serialize a CollectionItem row."""
    return CollectionItemResponse(
        id=str(entry.id),
        item_type=entry.item_type,
        item_id=str(entry.item_id),
        created_at=entry.created_at.isoformat(),
    )


def _check_item_type(item_type: str) -> None:
    """Reject item types that cannot be saved to a collection."""
    if item_type not in collection.COLLECTION_ITEM_TYPES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Invalid item type",
        )


@router.get("/", response_model=List[CollectionItemResponse])
async def list_collection(
    response: Response,
    item_type: Optional[str] = Query(None, description="Filter by item type"),
    current_user: User = Depends(get_current_user),
    pagination: Pagination = Depends(get_pagination),
    db: AsyncSession = Depends(get_db),
):
    """List the items saved to the current user's collection."""
    if item_type is not None:
        _check_item_type(item_type)

    total = await collection.count_items(db, current_user, item_type=item_type)
    rows = await collection.list_items(
        db,
        current_user,
        item_type=item_type,
        limit=pagination.limit,
        offset=pagination.offset,
    )
    pagination.set_total(response, total)
    return [_serialize(entry) for entry in rows]


@router.post("/{item_type}/{item_id}", response_model=CollectionItemResponse, status_code=status.HTTP_201_CREATED)
async def add_to_collection(
    item_type: str,
    item_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Add an item to the current user's collection.

    ``remote`` items reference a cached ``remote_objects`` row — federated
    music resources are bookmarked by id without being copied into local
    music tables. Only rows classified as a resource (track, album, artist,
    library, …) are collectable; bare remote posts are not.
    """
    _check_item_type(item_type)

    if item_type == "remote":
        row = await remote_content.get_cached_remote_object(db, item_id)
        if row is None or row.resource_type is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    else:
        item = await acl.get_item(db, item_type, item_id)
        if item is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    if not await acl.can_access(db, current_user, item_type, item_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    entry = await collection.save_item(db, current_user, item_type, item_id)
    await db.commit()
    return _serialize(entry)


@router.delete("/{item_type}/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_from_collection(
    item_type: str,
    item_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Remove an item from the current user's collection."""
    _check_item_type(item_type)

    removed = await collection.remove_item(db, current_user, item_type, item_id)
    if removed:
        await db.commit()

    return Response(status_code=status.HTTP_204_NO_CONTENT)
