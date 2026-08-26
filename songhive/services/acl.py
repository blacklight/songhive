"""
Access control service for shareable media items.

The ACL implements a three-level visibility model (private / local / public)
augmented with per-user share grants and revocable share URL tokens.  It is
designed to be used by the FastAPI route layer and by federation serializers.
"""

import logging
from typing import Any, Dict, NamedTuple, Optional, Type

from sqlalchemy import exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models._enums import Visibility
from ..models.album import Album
from ..models.artist import Artist
from ..models.library import Library
from ..models.playlist import Playlist
from ..models.radio import Radio
from ..models.share_grant import ShareGrant
from ..models.stored_file import StoredFile
from ..models.track import Track
from ..models.user import User
from . import sharing

logger = logging.getLogger(__name__)


class _ItemType(NamedTuple):
    """Registry entry pairing an item type with its model, id key, and API plural."""

    model: Type[Any]
    id_key: str
    plural: str


_ITEM_REGISTRY: Dict[str, _ItemType] = {
    "track": _ItemType(Track, "track_id", "tracks"),
    "album": _ItemType(Album, "album_id", "albums"),
    "artist": _ItemType(Artist, "artist_id", "artists"),
    "playlist": _ItemType(Playlist, "playlist_id", "playlists"),
    "library": _ItemType(Library, "library_id", "libraries"),
    "radio": _ItemType(Radio, "radio_id", "radios"),
    "file": _ItemType(StoredFile, "file_id", "files"),
}

ITEM_TYPES = set(_ITEM_REGISTRY)
ITEM_ID_KEYS = {item_type: entry.id_key for item_type, entry in _ITEM_REGISTRY.items()}


def get_item_plural(item_type: str) -> Optional[str]:
    """Return the public API plural path segment for ``item_type`` or ``None`` if unknown."""
    entry = _ITEM_REGISTRY.get(item_type)
    if entry is None:
        return None
    return entry.plural


_MAX_DERIVED_DEPTH = 1


def _list_access_predicate(model, user: Optional[User], item_type: str):
    """
    Return a SQL WHERE clause for the ACL rules used by list queries.

    This predicate intentionally covers only visibility, ownership, and
    explicit share grants.  Share-token and derived-file access are handled
    by `can_access` for single-item lookups and are not needed for lists.
    """
    if user is None:
        return model.visibility == Visibility.PUBLIC.value
    return or_(
        model.owner_id == user.id,
        model.visibility == Visibility.PUBLIC.value,
        model.visibility == Visibility.LOCAL.value,
        exists().where(
            ShareGrant.item_type == item_type,
            ShareGrant.item_id == model.id,
            ShareGrant.user_id == user.id,
        ),
    )


def apply_access_filter(
    stmt,
    model,
    user: Optional[User],
    item_type: str,
):
    """
    Add ACL filtering to ``stmt`` for the given ``model`` and ``user``.

    Admins bypass the filter.  The predicate is applied before ``offset`` and
    ``limit`` so list pagination returns the expected number of rows.
    """
    if user is not None and user.is_admin:
        return stmt
    return stmt.where(_list_access_predicate(model, user, item_type))


async def get_item(
    session: AsyncSession,
    item_type: str,
    item_id: str,
) -> Optional[Any]:
    """Return the model instance for ``(item_type, item_id)`` or ``None`` if missing."""
    entry = _ITEM_REGISTRY.get(item_type)
    if entry is None:
        raise ValueError(f"Unknown item type: {item_type!r}")
    return await session.get(entry.model, item_id)


async def _can_access(  # pylint: disable=too-many-return-statements,too-many-branches
    session: AsyncSession,
    user: Optional[User],
    item_type: str,
    item_id: str,
    *,
    share_token: Optional[str] = None,
    depth: int = 0,
) -> bool:
    """Internal access check with a depth guard for derived file access."""
    entry = _ITEM_REGISTRY.get(item_type)
    if entry is None:
        raise ValueError(f"Unknown item type: {item_type!r}")

    item = await session.get(entry.model, item_id)
    if item is None:
        return False

    # Rule 1: admins can access anything.
    if user is not None and user.is_admin:
        return True

    # Rule 2: owners can access their own items.
    if user is not None and getattr(item, "owner_id", None) == user.id:
        return True

    # Rule 3: public items are visible to everyone.
    if getattr(item, "visibility", None) == Visibility.PUBLIC.value:
        return True

    # Rule 4: local items are visible to any authenticated user.
    if user is not None and getattr(item, "visibility", None) == Visibility.LOCAL.value:
        return True

    # Rule 6: explicit per-user share grants.
    if user is not None:
        grant_result = await session.execute(
            select(ShareGrant)
            .where(
                ShareGrant.item_type == item_type,
                ShareGrant.item_id == item_id,
                ShareGrant.user_id == user.id,
            )
            .limit(1)
        )
        if grant_result.scalar_one_or_none() is not None:
            return True

    # Rule 7: revocable share URL tokens.
    if share_token is not None and await sharing.validate_share_token(session, item_type, item_id, share_token):
        return True

    # Rule 8: derived file access through owning tracks, albums, artists,
    # libraries, or playlists.
    if item_type == "file" and depth < _MAX_DERIVED_DEPTH:
        track_ids = (await session.execute(select(Track.id).where(Track.audio_file_id == item_id))).scalars().all()
        track_image_ids = (
            (await session.execute(select(Track.id).where(Track.image_file_id == item_id))).scalars().all()
        )
        album_ids = (await session.execute(select(Album.id).where(Album.cover_file_id == item_id))).scalars().all()
        artist_image_ids = (
            (await session.execute(select(Artist.id).where(Artist.image_file_id == item_id))).scalars().all()
        )
        artist_cover_ids = (
            (await session.execute(select(Artist.id).where(Artist.cover_file_id == item_id))).scalars().all()
        )
        library_image_ids = (
            (await session.execute(select(Library.id).where(Library.image_file_id == item_id))).scalars().all()
        )
        library_cover_ids = (
            (await session.execute(select(Library.id).where(Library.cover_file_id == item_id))).scalars().all()
        )
        playlist_image_ids = (
            (await session.execute(select(Playlist.id).where(Playlist.image_file_id == item_id))).scalars().all()
        )
        playlist_cover_ids = (
            (await session.execute(select(Playlist.id).where(Playlist.cover_file_id == item_id))).scalars().all()
        )

        derived_item_ids = (
            [("track", str(i)) for i in set(track_ids) | set(track_image_ids)]
            + [("album", str(i)) for i in album_ids]
            + [("artist", str(i)) for i in set(artist_image_ids) | set(artist_cover_ids)]
            + [("library", str(i)) for i in set(library_image_ids) | set(library_cover_ids)]
            + [("playlist", str(i)) for i in set(playlist_image_ids) | set(playlist_cover_ids)]
        )

        for derived_type, derived_id in derived_item_ids:
            if await _can_access(
                session,
                user,
                derived_type,
                derived_id,
                share_token=share_token,
                depth=depth + 1,
            ):
                return True

    return False


async def can_access(
    session: AsyncSession,
    user: Optional[User],
    item_type: str,
    item_id: str,
    *,
    share_token: Optional[str] = None,
) -> bool:
    """
    Return whether ``user`` may access ``(item_type, item_id)``.

    Anonymous requesters (``user is None``) only match public items, valid
    share tokens, and derived file access through visible tracks/albums.
    """
    return await _can_access(session, user, item_type, item_id, share_token=share_token)


async def can_manage(
    session: AsyncSession,
    user: Optional[User],
    item_type: str,
    item_id: str,
) -> bool:
    """
    Return whether ``user`` may manage (update or share) ``item_id``.

    Only the owner or an admin can manage an item.  Items with no owner are
    not manageable via this helper.
    """
    if user is None:
        return False

    entry = _ITEM_REGISTRY.get(item_type)
    if entry is None:
        raise ValueError(f"Unknown item type: {item_type!r}")

    item = await session.get(entry.model, item_id)
    if item is None:
        return False
    if user.is_admin:
        return True

    owner_id = getattr(item, "owner_id", None)
    if owner_id is None:
        return False

    return owner_id == user.id


async def audit_ownerless_private(session: AsyncSession) -> int:
    """
    Log a warning and return the count of ownerless private items.

    Ownerless private rows are not accessible under the current ACL rules and
    should be migrated to an explicit owner or ``LOCAL`` visibility.
    """
    from sqlalchemy import func

    result = await session.execute(
        select(func.count(StoredFile.id)).where(
            StoredFile.owner_id.is_(None),
            StoredFile.visibility == Visibility.PRIVATE.value,
        )
    )
    count = result.scalar_one()
    if count:
        logger.warning("Found %d ownerless private StoredFile row(s); migrate to an owner or LOCAL visibility", count)
    return count
