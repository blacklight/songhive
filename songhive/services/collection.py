"""
Collection service: save and remove content in a user's collection.

A user's collection is the set of items they own plus the items they have
explicitly saved (``CollectionItem`` rows).  List endpoints accept a
``collection`` filter that restricts results to that set; tracks also count
as collected when they are present in the user's ``favorites``.
"""

from typing import Any, Iterable, List, Optional, Set

from sqlalchemy import Select, exists, false, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.collection_item import CollectionItem
from ..models.favorite import Favorite
from ..models.user import User

# Item types that can be saved to a user's collection.  ``file`` rows are
# storage internals rather than browsable content and are excluded.
COLLECTION_ITEM_TYPES = frozenset(
    {
        "album",
        "artist",
        "library",
        "playlist",
        "radio",
        "track",
    }
)


def in_collection_clause(model: Any, item_type: str, user: User) -> Any:
    """Return a WHERE clause matching ``model`` rows in ``user``'s collection.

    A row is collected when the user owns it or has saved it through a
    ``CollectionItem`` row.  Tracks additionally match when favorited.
    """
    conditions: List[Any] = [
        exists().where(
            CollectionItem.item_type == item_type,
            CollectionItem.item_id == model.id,
            CollectionItem.user_id == user.id,
        )
    ]
    owner_id = getattr(model, "owner_id", None)
    if owner_id is not None:
        conditions.append(owner_id == user.id)
    if item_type == "track":
        conditions.append(
            exists().where(
                Favorite.track_id == model.id,
                Favorite.user_id == user.id,
            )
        )
    return or_(*conditions)


def apply_collection_filter(
    stmt: Select[Any],
    model: Any,
    user: Optional[User],
    item_type: str,
    collection: Optional[bool],
) -> Select[Any]:
    """Restrict ``stmt`` to items in ``user``'s collection when ``collection`` is set.

    Anonymous requesters match nothing — a collection is a per-user concept.
    """
    if not collection:
        return stmt
    if user is None:
        return stmt.where(false())
    return stmt.where(in_collection_clause(model, item_type, user))


async def save_item(
    session: AsyncSession,
    user: User,
    item_type: str,
    item_id: str,
) -> CollectionItem:
    """Add ``(item_type, item_id)`` to ``user``'s collection, idempotently."""
    result = await session.execute(
        select(CollectionItem).where(
            CollectionItem.user_id == user.id,
            CollectionItem.item_type == item_type,
            CollectionItem.item_id == item_id,
        )
    )
    entry = result.scalar_one_or_none()
    if entry is None:
        entry = CollectionItem(user_id=user.id, item_type=item_type, item_id=item_id)
        session.add(entry)
        await session.flush()
    return entry


async def remove_item(
    session: AsyncSession,
    user: User,
    item_type: str,
    item_id: str,
) -> bool:
    """Remove ``(item_type, item_id)`` from ``user``'s collection. Returns whether a row existed."""
    result = await session.execute(
        select(CollectionItem).where(
            CollectionItem.user_id == user.id,
            CollectionItem.item_type == item_type,
            CollectionItem.item_id == item_id,
        )
    )
    entry = result.scalar_one_or_none()
    if entry is None:
        return False
    await session.delete(entry)
    return True


async def saved_item_ids(
    session: AsyncSession,
    user: Optional[User],
    item_type: str,
    item_ids: Iterable[str],
) -> Set[str]:
    """Return the subset of ``item_ids`` that ``user`` has saved to their collection."""
    ids = {str(i) for i in item_ids}
    if user is None or not ids:
        return set()
    result = await session.execute(
        select(CollectionItem.item_id).where(
            CollectionItem.user_id == user.id,
            CollectionItem.item_type == item_type,
            CollectionItem.item_id.in_(ids),
        )
    )
    return {str(row) for row in result.scalars().all()}


async def list_items(
    session: AsyncSession,
    user: User,
    item_type: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
) -> List[CollectionItem]:
    """List the items in ``user``'s collection, newest first."""
    stmt = select(CollectionItem).where(CollectionItem.user_id == user.id)
    if item_type:
        stmt = stmt.where(CollectionItem.item_type == item_type)
    stmt = stmt.order_by(CollectionItem.created_at.desc(), CollectionItem.id.desc())
    stmt = stmt.offset(offset).limit(limit)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def count_items(
    session: AsyncSession,
    user: User,
    item_type: Optional[str] = None,
) -> int:
    """Return the number of items in ``user``'s collection."""
    stmt = select(func.count(CollectionItem.id)).where(CollectionItem.user_id == user.id)
    if item_type:
        stmt = stmt.where(CollectionItem.item_type == item_type)
    result = await session.execute(stmt)
    return result.scalar() or 0
