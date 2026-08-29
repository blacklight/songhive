"""
Genre service: validation, association management, and visibility-aware
listing queries.
"""

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple, Type, cast

from sqlalchemy import (
    and_,
    asc,
    delete,
    desc,
    func,
    literal,
    select,
    union_all,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.sql.elements import ColumnElement

from ..models.album import Album
from ..models.genre import Genre, GenreAlbum, GenreTrack
from ..models.track import Track
from ..models.user import User
from ..services.hashtags import validate_hashtag_name
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
class GenreSummary:
    """Summary of a genre and its visible usage."""

    name: str
    item_count: int
    first_used: Optional[datetime]
    last_used: Optional[datetime]


@dataclass
class GenreItem:
    """A single entity associated with a genre."""

    type: str
    id: str


# Registry mapping entity type to (model, association class, id column, acl item type).
_EntityInfo = Tuple[Type, Type, str, str]

_GENRE_ENTITY_REGISTRY: dict[str, _EntityInfo] = {
    "track": (Track, GenreTrack, "track_id", "track"),
    "album": (Album, GenreAlbum, "album_id", "album"),
}

_GENRE_SPLIT_RE = re.compile(r"[;,|\t\\]")

_GENRE_RE = re.compile(r"^(?=.*[a-z])[a-z0-9&/\-_ ]+$")


class InvalidGenreName(ValueError):
    """Raised when a genre name fails normalisation or validation."""


_MAX_GENRE_LEN = 128


def validate_genre_name(name: str) -> str:
    """
    Strip a leading ``#`` (if any), lowercase, trim and validate the allowed
    charset.

    Genre names allow lowercase letters, digits, underscores and spaces. They
    must contain at least one letter and be no longer than 128 characters.
    """
    if not name or not name.strip():
        raise InvalidGenreName("Genre name cannot be empty")

    cleaned = name.strip().lstrip("#").strip().lower()
    if not cleaned:
        raise InvalidGenreName("Genre name cannot be empty")

    if len(cleaned) > _MAX_GENRE_LEN:
        raise InvalidGenreName(f"Genre name too long: {name!r}")

    if not _GENRE_RE.match(cleaned):
        raise InvalidGenreName(f"The genre name: {name!r} contains invalid characters")

    return cleaned


def split_genre_string(value: Optional[str]) -> List[str]:
    """Split a free-form genre string and return valid, unique genre names."""
    if not value:
        return []

    results: List[str] = []
    seen: set[str] = set()
    for part in _GENRE_SPLIT_RE.split(value):
        part = part.strip().lstrip("#").strip()
        if not part:
            continue
        try:
            name = validate_genre_name(part)
        except InvalidGenreName:
            continue
        if name not in seen:
            seen.add(name)
            results.append(name)
    return results


def extract_genres_from_metadata(metadata: AudioMetadata) -> List[str]:
    """Derive normalised genre names from audio metadata."""
    return split_genre_string(metadata.genre)


def extract_genres_from_track(track: Track) -> List[str]:
    """Derive normalised genre names from a track's ``genre`` column."""
    return split_genre_string(track.genre)


def _entity_access_predicate(model: Type, user: Optional[User], item_type: str) -> ColumnElement:
    """Return an access predicate for the given entity model."""
    return _list_access_predicate(model, user, item_type)


def _accessible_associations_cte(
    user: Optional[User],
    target_user_id: Optional[str] = None,
) -> Any:
    """
    Build a CTE of ``(genre_id, created_at)`` rows for all visible
    associations.

    When ``target_user_id`` is provided, only associations on entities owned by
    that user are included.
    """
    subqueries: List[Any] = []
    for model, assoc_class, entity_col, acl_type in _GENRE_ENTITY_REGISTRY.values():
        if target_user_id is not None and not hasattr(model, "owner_id"):
            continue

        pred = _entity_access_predicate(model, user, acl_type)
        if target_user_id is not None and hasattr(model, "owner_id"):
            pred = and_(pred, model.owner_id == target_user_id)

        subq = (
            select(assoc_class.genre_id, assoc_class.created_at)
            .select_from(assoc_class)
            .join(model, getattr(assoc_class, entity_col) == model.id)
            .where(pred)
        )
        subqueries.append(subq)

    if not subqueries:
        return None
    return union_all(*subqueries).cte("accessible_genre_associations")


def _accessible_items_cte(
    genre_id: str,
    user: Optional[User],
    target_user_id: Optional[str] = None,
) -> Any:
    """
    Build a CTE of ``(item_type, item_id, created_at)`` rows for a single
    genre, filtered by visibility.
    """
    subqueries: List[Any] = []
    for entity_type, (model, assoc_class, entity_col, acl_type) in _GENRE_ENTITY_REGISTRY.items():
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
            .where(assoc_class.genre_id == genre_id, pred)
        )
        subqueries.append(subq)

    if not subqueries:
        return None
    return union_all(*subqueries).cte("genre_items")


async def get_or_create_genre(session: AsyncSession, name: str) -> Genre:
    """Return an existing genre by name or create a new one."""
    result = await session.execute(select(Genre).where(Genre.name == name).limit(1))
    existing = cast(Optional[Genre], result.scalar_one_or_none())
    if existing is not None:
        return existing

    try:
        async with session.begin_nested():
            genre = Genre(name=name)
            session.add(genre)
            await session.flush()
    except IntegrityError as exc:
        if not is_unique_constraint_error(exc):
            raise
        result = await session.execute(select(Genre).where(Genre.name == name).limit(1))
        existing = cast(Optional[Genre], result.scalar_one_or_none())
        if existing is None:
            raise
        return existing

    return genre


async def _get_genre_by_name(session: AsyncSession, name: str) -> Optional[Genre]:
    """Return a genre by its normalised name, or ``None`` for invalid input."""
    try:
        normalised = validate_genre_name(name)
    except InvalidGenreName:
        return None
    result = await session.execute(select(Genre).where(Genre.name == normalised).limit(1))
    return result.scalar_one_or_none()


async def add_genres_to_entity(
    session: AsyncSession,
    entity_type: str,
    entity_id: str,
    names: List[str],
) -> List[Genre]:
    """Validate, create and associate one or more genres with an entity."""
    if entity_type not in _GENRE_ENTITY_REGISTRY:
        raise ValueError(f"Unknown entity type: {entity_type!r}")

    model, assoc_class, entity_col, _ = _GENRE_ENTITY_REGISTRY[entity_type]
    entity = await session.get(model, entity_id)
    if entity is None:
        raise ValueError(f"{entity_type} not found")

    # Normalise and deduplicate while preserving order.
    normalised: List[str] = []
    seen: set[str] = set()
    for raw in names:
        name = validate_genre_name(raw)
        if name not in seen:
            seen.add(name)
            normalised.append(name)

    added: List[Genre] = []
    for name in normalised:
        genre = await get_or_create_genre(session, name)

        try:
            async with session.begin_nested():
                assoc = assoc_class(
                    genre_id=genre.id,
                    **{entity_col: entity_id},
                )
                session.add(assoc)
                await session.flush()
        except IntegrityError as exc:
            if not is_unique_constraint_error(exc):
                raise

        added.append(genre)

    return added


async def remove_genre_from_entity(
    session: AsyncSession,
    entity_type: str,
    entity_id: str,
    genre_name: str,
) -> None:
    """Remove an association between an entity and a genre by name."""
    if entity_type not in _GENRE_ENTITY_REGISTRY:
        raise ValueError(f"Unknown entity type: {entity_type!r}")

    genre = await _get_genre_by_name(session, genre_name)
    if genre is None:
        raise ValueError("Genre not found")

    _, assoc_class, entity_col, _ = _GENRE_ENTITY_REGISTRY[entity_type]
    result = await session.execute(
        select(assoc_class)
        .where(
            assoc_class.genre_id == genre.id,
            getattr(assoc_class, entity_col) == entity_id,
        )
        .limit(1)
    )
    assoc = result.scalar_one_or_none()
    if assoc is None:
        raise ValueError("Genre is not associated with this entity")

    await session.delete(assoc)
    await session.flush()


async def set_genres_for_entity(
    session: AsyncSession,
    entity_type: str,
    entity_id: str,
    names: List[str],
) -> List[Genre]:
    """
    Replace all genre associations for an entity.

    This does not update the entity's own ``genre`` free-text column; callers
    must keep the string and the association table in sync.
    """
    if entity_type not in _GENRE_ENTITY_REGISTRY:
        raise ValueError(f"Unknown entity type: {entity_type!r}")

    model, assoc_class, entity_col, _ = _GENRE_ENTITY_REGISTRY[entity_type]
    entity = await session.get(model, entity_id)
    if entity is None:
        raise ValueError(f"{entity_type} not found")

    # Normalise and deduplicate while preserving order.
    normalised: List[str] = []
    seen: set[str] = set()
    for raw in names:
        name = validate_genre_name(raw)
        if name not in seen:
            seen.add(name)
            normalised.append(name)

    await session.execute(delete(assoc_class).where(getattr(assoc_class, entity_col) == entity_id))

    added: List[Genre] = []
    for name in normalised:
        genre = await get_or_create_genre(session, name)

        try:
            async with session.begin_nested():
                assoc = assoc_class(
                    genre_id=genre.id,
                    **{entity_col: entity_id},
                )
                session.add(assoc)
                await session.flush()
        except IntegrityError as exc:
            if not is_unique_constraint_error(exc):
                raise

        added.append(genre)

    return added


async def get_genres_for_entity(
    session: AsyncSession,
    entity_type: str,
    entity_id: str,
) -> List[str]:
    """Return the genre names associated with a specific entity."""
    if entity_type not in _GENRE_ENTITY_REGISTRY:
        raise ValueError(f"Unknown entity type: {entity_type!r}")

    _, assoc_class, entity_col, _ = _GENRE_ENTITY_REGISTRY[entity_type]
    result = await session.execute(
        select(Genre)
        .join(assoc_class, Genre.id == assoc_class.genre_id)
        .where(getattr(assoc_class, entity_col) == entity_id)
        .order_by(Genre.name)
    )
    return [g.name for g in result.scalars().all()]


async def list_genres(
    session: AsyncSession,
    user: Optional[User] = None,
    query: Optional[str] = None,
    target_user_id: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
    sort_by: str = "name",
    sort_dir: str = "asc",
) -> Tuple[List[GenreSummary], int]:
    """
    List genres visible to ``user``, optionally scoped to a single owner.

    Returns ``(summaries, total_count)``.
    """
    cte = _accessible_associations_cte(user, target_user_id)
    if cte is None:
        return [], 0

    item_count = func.count(cte.c.genre_id).label("item_count")
    first_used = func.min(cte.c.created_at).label("first_used")
    last_used = func.max(cte.c.created_at).label("last_used")

    stmt = select(Genre, item_count, first_used, last_used).join(cte, Genre.id == cte.c.genre_id).group_by(Genre.id)

    if query:
        stmt = stmt.where(Genre.name.ilike(f"%{query}%"))

    if sort_by == "name":
        order = _order_clause(Genre.name, sort_dir)
    elif sort_by == "item_count":
        order = _order_clause(item_count, sort_dir)
    elif sort_by == "first_used":
        order = _order_clause(first_used, sort_dir, nulls_last=True)
    elif sort_by == "last_used":
        order = _order_clause(last_used, sort_dir, nulls_last=True)
    else:
        raise ValueError(f"Unsupported sort_by: {sort_by!r}")

    stmt = stmt.order_by(order, Genre.name)
    count_stmt = select(func.count()).select_from(stmt.subquery())
    stmt = stmt.offset(offset).limit(limit)

    total = (await session.execute(count_stmt)).scalar() or 0
    rows = (await session.execute(stmt)).all()

    return [
        GenreSummary(
            name=row.Genre.name,
            item_count=row._mapping["item_count"],
            first_used=row._mapping["first_used"],
            last_used=row._mapping["last_used"],
        )
        for row in rows
    ], total


async def get_items_for_genre(
    session: AsyncSession,
    genre_name: str,
    user: Optional[User] = None,
    target_user_id: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
    sort_by: str = "created_at",
    sort_dir: str = "desc",
) -> Tuple[List[GenreItem], int]:
    """
    Return the visible items for a genre, optionally scoped to an owner.

    Returns ``(items, total_count)``.
    """
    genre = await _get_genre_by_name(session, genre_name)
    if genre is None:
        return [], 0

    cte = _accessible_items_cte(genre.id, user, target_user_id)
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

    return [GenreItem(type=row.item_type, id=row.item_id) for row in rows], total


async def delete_genre_globally(session: AsyncSession, genre_name: str) -> Optional[Genre]:
    """Delete a genre and all its associations (admin only)."""
    genre = await _get_genre_by_name(session, genre_name)
    if genre is None:
        return None

    await session.delete(genre)
    await session.flush()
    return genre


def genres_to_hashtags(genre_names: List[str]) -> List[str]:
    """
    Convert normalised genre names to valid hashtag names.

    Spaces are replaced with underscores and names that do not form valid
    hashtags are skipped.
    """
    hashtags: List[str] = []
    seen: set[str] = set()
    for name in genre_names:
        tag = re.sub(r"[^a-z0-9_]+", "_", name.lower())
        try:
            tag = validate_hashtag_name(tag)
        except ValueError:
            continue
        if tag not in seen:
            seen.add(tag)
            hashtags.append(tag)
    return hashtags


async def propagate_album_genres(session: AsyncSession, album: Album) -> List[str]:
    """
    Derive an album's genre from the intersection of its tracks' genres.

    A genre is propagated to the album only when **all** of the album's tracks
    are associated with that genre. The album's ``genre`` column is updated to
    the ``; ``-joined list of unanimous genres, and the album's genre
    associations are replaced.
    """
    total_result = await session.execute(select(func.count(Track.id)).where(Track.album_id == album.id))
    total_tracks = total_result.scalar() or 0

    if total_tracks == 0:
        album.genre = None
        await set_genres_for_entity(session, "album", album.id, [])
        return []

    counts_stmt = (
        select(GenreTrack.genre_id, func.count(GenreTrack.track_id).label("track_count"))
        .join(Track, GenreTrack.track_id == Track.id)
        .where(Track.album_id == album.id)
        .group_by(GenreTrack.genre_id)
    )
    counts = (await session.execute(counts_stmt)).all()
    unanimous_ids = [row.genre_id for row in counts if row.track_count == total_tracks]

    if not unanimous_ids:
        album.genre = None
        await set_genres_for_entity(session, "album", album.id, [])
        return []

    genres_result = await session.execute(select(Genre).where(Genre.id.in_(unanimous_ids)).order_by(Genre.name))
    genres = list(genres_result.scalars().all())
    genre_names = [g.name for g in genres]
    album.genre = "; ".join(genre_names)
    await set_genres_for_entity(session, "album", album.id, genre_names)
    return genre_names


def _track_has_explicit_genre(track: Track) -> bool:
    """Return ``True`` when a track has its own track-level genre override."""
    if track.genre_associations:
        return any(not a.inherited for a in track.genre_associations)
    # No associations yet: a non-empty, parseable ``track.genre`` is treated as
    # explicit track-level metadata (e.g. from a tag sync or import) rather than
    # an inherited album value.
    return bool(track.genre and split_genre_string(track.genre))


async def _load_track_genre_associations(session: AsyncSession, track: Track) -> None:
    """Ensure ``track.genre_associations`` reflects the current database state."""
    await session.refresh(track, attribute_names=["genre_associations"])


async def set_track_inherited_genres(
    session: AsyncSession,
    track: Track,
    album_genre_names: List[str],
    genre_map: Optional[Dict[str, Genre]] = None,
) -> bool:
    """
    Replace a track's inherited genre associations with ``album_genre_names``.

    If the track has an explicit track-level genre override, the track is left
    untouched and ``False`` is returned.  Otherwise the track's ``genre`` column
    and its ``GenreTrack`` rows are updated to mirror the album, with the
    ``inherited`` flag set to ``True``.
    """
    await _load_track_genre_associations(session, track)
    if _track_has_explicit_genre(track):
        return False

    await session.execute(
        delete(GenreTrack).where(
            GenreTrack.track_id == track.id,
            GenreTrack.inherited.is_(True),
        )
    )

    track.genre = "; ".join(album_genre_names) if album_genre_names else None

    for name in album_genre_names:
        genre = None
        if genre_map is not None:
            genre = genre_map.get(name)
        if genre is None:
            genre = await get_or_create_genre(session, name)
            if genre_map is not None:
                genre_map[name] = genre

        session.add(
            GenreTrack(
                genre_id=genre.id,
                track_id=track.id,
                inherited=True,
            )
        )

    await session.flush()
    return True


async def propagate_track_genres(session: AsyncSession, album: Album) -> List[str]:
    """
    Copy an album's genres to any track in the album without an explicit
    track-level genre override.

    Tracks that have their own ``GenreTrack`` associations keep their current
    genres.  For each inheriting track, the ``track.genre`` column and its
    ``GenreTrack`` rows are replaced with the album's genres.

    Returns the IDs of the tracks that were updated.
    """
    album_genre_names = await get_genres_for_entity(session, "album", album.id)

    genre_map: Dict[str, Genre] = {}
    for name in album_genre_names:
        genre_map[name] = await get_or_create_genre(session, name)

    result = await session.execute(
        select(Track).where(Track.album_id == album.id).options(selectinload(Track.genre_associations))
    )
    tracks = result.scalars().unique().all()

    updated: List[str] = []
    for track in tracks:
        if await set_track_inherited_genres(session, track, album_genre_names, genre_map):
            updated.append(str(track.id))

    return updated


async def sync_album_genres(session: AsyncSession, album: Album) -> List[str]:
    """
    Synchronise an album's genre with its tracks in both directions.

    Inherited album genres are pushed to tracks that do not have an explicit
    override, the album is then re-derived from the intersection of all tracks,
    and the final album state is pushed back to inheriting tracks.  This
    converges on the stable state where the album is the intersection of track
    genres and tracks without explicit genres inherit from the album.
    """
    await propagate_track_genres(session, album)
    album_genres = await propagate_album_genres(session, album)
    await propagate_track_genres(session, album)
    return album_genres
