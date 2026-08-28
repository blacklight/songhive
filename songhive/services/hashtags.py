"""
Hashtag service: validation, association management, and visibility-aware
listing queries.
"""

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, List, Optional, Tuple, Type

from sqlalchemy import (
    and_,
    asc,
    desc,
    func,
    literal,
    select,
    true,
    union_all,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from ..models.album import Album
from ..models.artist import Artist
from ..models.hashtag import (
    Hashtag,
    HashtagAlbum,
    HashtagArtist,
    HashtagLibrary,
    HashtagPlaylist,
    HashtagTrack,
)
from ..models.library import Library
from ..models.playlist import Playlist
from ..models.track import Track
from ..models.user import User
from ..services.metadata import AudioMetadata
from ..services.storage import is_unique_constraint_error
from .acl import _list_access_predicate

logger = logging.getLogger(__name__)


def _order_clause(expr: Any, direction: str, nulls_last: bool = False) -> Any:
    """Return an ascending or descending order clause, optionally NULLS LAST."""
    clause = asc(expr) if direction == "asc" else desc(expr)
    if nulls_last:
        clause = clause.nulls_last()
    return clause


@dataclass
class HashtagSummary:
    """Summary of a hashtag and its visible usage."""

    name: str
    item_count: int
    first_used: Optional[datetime]
    last_used: Optional[datetime]


@dataclass
class TaggedItem:
    """A single entity associated with a hashtag."""

    type: str
    id: str


# Registry mapping entity type to (model, association class, id column, acl item type).
_EntityInfo = Tuple[Type, Type, str, str]

_ENTITY_REGISTRY: dict[str, _EntityInfo] = {
    "track": (Track, HashtagTrack, "track_id", "track"),
    "album": (Album, HashtagAlbum, "album_id", "album"),
    "artist": (Artist, HashtagArtist, "artist_id", "artist"),
    "playlist": (Playlist, HashtagPlaylist, "playlist_id", "playlist"),
    "library": (Library, HashtagLibrary, "library_id", "library"),
}

_HASHTAG_TAG_KEYS = {
    "TAGS",
    "KEYWORDS",
    "TXXX:TAGS",
    "TXXX:KEYWORDS",
    "----:com.apple.iTunes:TAGS",
    "----:com.apple.iTunes:KEYWORDS",
}

_TAG_SPLIT_RE = re.compile(r"[;,\/\\]")

_HASHTAG_RE = re.compile(r"^(?=.*[a-z])[a-z0-9_]+$")


def validate_hashtag_name(name: str) -> str:
    """
    Strip a leading ``#``, lowercase, and validate the allowed charset.

    Hashtags must contain only lowercase letters, digits and underscores and
    must include at least one letter.
    """
    if not name or not name.strip():
        raise ValueError("Hashtag name cannot be empty")

    cleaned = name.strip().lstrip("#").strip().lower()
    if not cleaned:
        raise ValueError("Hashtag name cannot be empty")

    if not _HASHTAG_RE.match(cleaned):
        raise ValueError(
            f"Invalid hashtag name: {name!r}. Only letters, digits and underscores are allowed, "
            "and at least one letter is required."
        )

    return cleaned


def _split_and_validate_tags(value: Optional[str]) -> List[str]:
    """Split a free-form tag string and return valid hashtags."""
    if not value:
        return []

    results: List[str] = []
    for part in _TAG_SPLIT_RE.split(value):
        part = part.strip().lstrip("#").strip()
        if not part:
            continue
        try:
            results.append(validate_hashtag_name(part))
        except ValueError:
            continue
    return results


def _collect_tags(genre: Optional[str], raw_tags: Optional[dict]) -> List[str]:
    """Collect valid hashtags from a genre string and raw tag/keyword fields."""
    tags: List[str] = []

    if genre:
        tags.extend(_split_and_validate_tags(genre))

    for key, values in (raw_tags or {}).items():
        if key.upper() not in _HASHTAG_TAG_KEYS and key not in _HASHTAG_TAG_KEYS:
            continue
        for value in values:
            tags.extend(_split_and_validate_tags(value))

    seen: set[str] = set()
    unique: List[str] = []
    for tag in tags:
        if tag not in seen:
            seen.add(tag)
            unique.append(tag)
    return unique


def extract_hashtags_from_metadata(metadata: AudioMetadata) -> List[str]:
    """
    Derive hashtags from genre and tag/keyword metadata fields.

    Preserves the order of discovery while deduplicating.
    """
    return _collect_tags(metadata.genre, metadata.raw_tags)


def extract_hashtags_from_track(track: Track) -> List[str]:
    """Derive hashtags from a track's genre and raw tag fields."""
    return _collect_tags(track.genre, track.raw_metadata)


def _entity_access_predicate(
    model: Type,
    user: Optional[User],
    item_type: str,
) -> ColumnElement:
    """Return an access predicate for the given entity model."""
    if item_type == "artist":
        # Artists have no visibility/owner fields and are treated as public.
        return true()
    return _list_access_predicate(model, user, item_type)


def _accessible_associations_cte(
    user: Optional[User],
    target_user_id: Optional[str] = None,
) -> Any:
    """
    Build a CTE of ``(hashtag_id, created_at)`` rows for all visible
    associations.

    When ``target_user_id`` is provided, only associations on entities owned by
    that user are included.
    """
    subqueries: List[Any] = []
    for model, assoc_class, entity_col, acl_type in _ENTITY_REGISTRY.values():
        if target_user_id is not None and not hasattr(model, "owner_id"):
            continue

        pred = _entity_access_predicate(model, user, acl_type)
        if target_user_id is not None and hasattr(model, "owner_id"):
            pred = and_(pred, model.owner_id == target_user_id)

        subq = (
            select(assoc_class.hashtag_id, assoc_class.created_at)
            .select_from(assoc_class)
            .join(model, getattr(assoc_class, entity_col) == model.id)
            .where(pred)
        )
        subqueries.append(subq)

    if not subqueries:
        return None
    return union_all(*subqueries).cte("accessible_associations")


def _accessible_items_cte(
    hashtag_id: str,
    user: Optional[User],
    target_user_id: Optional[str] = None,
) -> Any:
    """
    Build a CTE of ``(item_type, item_id, created_at)`` rows for a single
    hashtag, filtered by visibility.
    """
    subqueries: List[Any] = []
    for entity_type, (model, assoc_class, entity_col, acl_type) in _ENTITY_REGISTRY.items():
        if target_user_id is not None and not hasattr(model, "owner_id"):
            continue

        pred = _entity_access_predicate(model, user, acl_type)
        if target_user_id is not None and hasattr(model, "owner_id"):
            pred = and_(pred, model.owner_id == target_user_id)

        subq = (
            select(
                literal(entity_type).label("item_type"),
                getattr(assoc_class, entity_col).label("item_id"),
                assoc_class.created_at.label("created_at"),
            )
            .select_from(assoc_class)
            .join(model, getattr(assoc_class, entity_col) == model.id)
            .where(assoc_class.hashtag_id == hashtag_id, pred)
        )
        subqueries.append(subq)

    if not subqueries:
        return None
    return union_all(*subqueries).cte("tagged_items")


async def get_or_create_hashtag(session: AsyncSession, name: str) -> Hashtag:
    """Return an existing hashtag by name or create a new one."""
    result = await session.execute(select(Hashtag).where(Hashtag.name == name).limit(1))
    existing = result.scalar_one_or_none()
    if existing is not None:
        return existing

    try:
        async with session.begin_nested():
            hashtag = Hashtag(name=name)
            session.add(hashtag)
            await session.flush()
    except IntegrityError as exc:
        if not is_unique_constraint_error(exc):
            raise
        result = await session.execute(select(Hashtag).where(Hashtag.name == name).limit(1))
        existing = result.scalar_one_or_none()
        if existing is None:
            raise
        return existing

    return hashtag


async def _get_hashtag_by_name(session: AsyncSession, name: str) -> Optional[Hashtag]:
    """Return a hashtag by its normalised name, or ``None`` for invalid input."""
    try:
        normalised = validate_hashtag_name(name)
    except ValueError:
        return None
    result = await session.execute(select(Hashtag).where(Hashtag.name == normalised).limit(1))
    return result.scalar_one_or_none()


async def add_hashtags_to_entity(
    session: AsyncSession,
    entity_type: str,
    entity_id: str,
    names: List[str],
    user_id: Optional[str] = None,
) -> List[Hashtag]:
    """
    Validate, create and associate one or more hashtags with an entity.

    ``user_id`` may be ``None`` for system-derived (e.g. genre) tags.
    """
    if entity_type not in _ENTITY_REGISTRY:
        raise ValueError(f"Unknown entity type: {entity_type!r}")

    model, assoc_class, entity_col, _ = _ENTITY_REGISTRY[entity_type]
    entity = await session.get(model, entity_id)
    if entity is None:
        raise ValueError(f"{entity_type} not found")

    # Normalise and deduplicate while preserving order.
    normalised: List[str] = []
    seen: set[str] = set()
    for raw in names:
        name = validate_hashtag_name(raw)
        if name not in seen:
            seen.add(name)
            normalised.append(name)

    added: List[Hashtag] = []
    for name in normalised:
        hashtag = await get_or_create_hashtag(session, name)

        try:
            async with session.begin_nested():
                assoc = assoc_class(
                    hashtag_id=hashtag.id,
                    **{entity_col: entity_id},
                    user_id=user_id,
                )
                session.add(assoc)
                await session.flush()
        except IntegrityError as exc:
            if not is_unique_constraint_error(exc):
                raise

        added.append(hashtag)

    return added


async def remove_hashtag_from_entity(
    session: AsyncSession,
    entity_type: str,
    entity_id: str,
    hashtag_name: str,
) -> None:
    """Remove an association between an entity and a hashtag by name."""
    if entity_type not in _ENTITY_REGISTRY:
        raise ValueError(f"Unknown entity type: {entity_type!r}")

    hashtag = await _get_hashtag_by_name(session, hashtag_name)
    if hashtag is None:
        raise ValueError("Hashtag not found")

    _, assoc_class, entity_col, _ = _ENTITY_REGISTRY[entity_type]
    result = await session.execute(
        select(assoc_class)
        .where(
            assoc_class.hashtag_id == hashtag.id,
            getattr(assoc_class, entity_col) == entity_id,
        )
        .limit(1)
    )
    assoc = result.scalar_one_or_none()
    if assoc is None:
        raise ValueError("Hashtag is not associated with this entity")

    await session.delete(assoc)
    await session.flush()


async def get_hashtags_for_entity(
    session: AsyncSession,
    entity_type: str,
    entity_id: str,
) -> List[Hashtag]:
    """Return the hashtags associated with a specific entity."""
    if entity_type not in _ENTITY_REGISTRY:
        raise ValueError(f"Unknown entity type: {entity_type!r}")

    _, assoc_class, entity_col, _ = _ENTITY_REGISTRY[entity_type]
    result = await session.execute(
        select(Hashtag)
        .join(assoc_class, Hashtag.id == assoc_class.hashtag_id)
        .where(getattr(assoc_class, entity_col) == entity_id)
        .order_by(Hashtag.name)
    )
    return list(result.scalars().all())


async def list_hashtags(
    session: AsyncSession,
    user: Optional[User] = None,
    query: Optional[str] = None,
    target_user_id: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
    sort_by: str = "name",
    sort_dir: str = "asc",
) -> Tuple[List[HashtagSummary], int]:
    """
    List hashtags visible to ``user``, optionally scoped to a single owner.

    Returns ``(summaries, total_count)``.
    """
    cte = _accessible_associations_cte(user, target_user_id)
    if cte is None:
        return [], 0

    item_count = func.count(cte.c.hashtag_id).label("item_count")
    first_used = func.min(cte.c.created_at).label("first_used")
    last_used = func.max(cte.c.created_at).label("last_used")

    stmt = (
        select(Hashtag, item_count, first_used, last_used)
        .join(cte, Hashtag.id == cte.c.hashtag_id)
        .group_by(Hashtag.id)
    )

    if query:
        stmt = stmt.where(Hashtag.name.ilike(f"%{query}%"))

    if sort_by == "name":
        order = _order_clause(Hashtag.name, sort_dir)
    elif sort_by == "item_count":
        order = _order_clause(item_count, sort_dir)
    elif sort_by == "first_used":
        order = _order_clause(first_used, sort_dir, nulls_last=True)
    elif sort_by == "last_used":
        order = _order_clause(last_used, sort_dir, nulls_last=True)
    else:
        raise ValueError(f"Unsupported sort_by: {sort_by!r}")

    stmt = stmt.order_by(order, Hashtag.name)
    count_stmt = select(func.count()).select_from(stmt.subquery())
    stmt = stmt.offset(offset).limit(limit)

    total = (await session.execute(count_stmt)).scalar() or 0
    rows = (await session.execute(stmt)).all()

    return [
        HashtagSummary(
            name=row.Hashtag.name,
            item_count=row._mapping["item_count"],
            first_used=row._mapping["first_used"],
            last_used=row._mapping["last_used"],
        )
        for row in rows
    ], total


async def get_items_for_hashtag(
    session: AsyncSession,
    hashtag_name: str,
    user: Optional[User] = None,
    target_user_id: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
    sort_by: str = "created_at",
    sort_dir: str = "desc",
) -> Tuple[List[TaggedItem], int]:
    """
    Return the visible items for a hashtag, optionally scoped to an owner.

    Returns ``(items, total_count)``.
    """
    hashtag = await _get_hashtag_by_name(session, hashtag_name)
    if hashtag is None:
        return [], 0

    cte = _accessible_items_cte(hashtag.id, user, target_user_id)
    if cte is None:
        return [], 0

    if sort_by == "type":
        order = _order_clause(cte.c.item_type, sort_dir)
    elif sort_by == "created_at":
        order = _order_clause(cte.c.created_at, sort_dir, nulls_last=True)
    else:
        raise ValueError(f"Unsupported sort_by: {sort_by!r}")

    stmt = (
        select(cte.c.item_type, cte.c.item_id, cte.c.created_at)
        .select_from(cte)
        .order_by(order, cte.c.item_id)
        .offset(offset)
        .limit(limit)
    )
    count_stmt = select(func.count()).select_from(cte)

    total = (await session.execute(count_stmt)).scalar() or 0
    rows = (await session.execute(stmt)).all()

    return [TaggedItem(type=row.item_type, id=row.item_id) for row in rows], total


async def delete_hashtag_globally(session: AsyncSession, hashtag_name: str) -> Optional[Hashtag]:
    """Delete a hashtag and all its associations (admin only)."""
    hashtag = await _get_hashtag_by_name(session, hashtag_name)
    if hashtag is None:
        return None

    await session.delete(hashtag)
    await session.flush()
    return hashtag
