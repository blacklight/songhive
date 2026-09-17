"""
Access control service for shareable media items.

The ACL implements a three-level visibility model (private / local / public)
augmented with per-user share grants and revocable share URL tokens.  It is
designed to be used by the FastAPI route layer and by federation serializers.
"""

import logging
from typing import Any, Dict, Iterable, List, NamedTuple, Optional, Sequence, Set, Tuple, Type

from sqlalchemy import and_, exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models._enums import Visibility
from ..models.activity import Activity, ActivityMention
from ..models.album import Album
from ..models.artist import Artist
from ..models.library import Library
from ..models.library_track import LibraryTrack
from ..models.playlist import Playlist, PlaylistTrack
from ..models.radio import Radio
from ..models.remote_object import RemoteObject
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


_MAX_DERIVED_DEPTH = 2


def _list_access_predicate(model, user: Optional[User], item_type: str):
    """
    Return a SQL WHERE clause for the ACL rules used by list queries.

    This predicate intentionally covers only visibility, ownership, and
    explicit share grants.  Share-token and derived-file access are handled
    by `can_access` for single-item lookups and are not needed for lists.
    """
    if user is None:
        return model.visibility == Visibility.PUBLIC.value
    predicate = or_(
        model.owner_id == user.id,
        model.visibility == Visibility.PUBLIC.value,
        model.visibility == Visibility.LOCAL.value,
        exists().where(
            ShareGrant.item_type == item_type,
            ShareGrant.item_id == model.id,
            ShareGrant.user_id == user.id,
        ),
    )
    # Tracks in a shared album or playlist are also accessible to the share recipient.
    if item_type == "track":
        album_share = exists().where(
            ShareGrant.item_type == "album",
            ShareGrant.item_id == model.album_id,
            ShareGrant.user_id == user.id,
        )
        playlist_share = exists().where(
            PlaylistTrack.track_id == model.id,
            PlaylistTrack.playlist_id == ShareGrant.item_id,
            ShareGrant.item_type == "playlist",
            ShareGrant.user_id == user.id,
        )
        library_share = exists().where(
            LibraryTrack.track_id == model.id,
            LibraryTrack.library_id == ShareGrant.item_id,
            ShareGrant.item_type == "library",
            ShareGrant.user_id == user.id,
        )
        predicate = or_(predicate, album_share, playlist_share, library_share)
    return predicate


def _activity_visibility_filter(user: Optional[User]):
    """
    Return a WHERE clause applying per-activity visibility for list queries.

    Mirrors ``can_view_activity`` minus the entity-access check — the caller
    authorizes the containing entity once for the whole page: ``public``
    activities are visible to everyone, ``local`` and ``followers`` to
    authenticated users, ``mentioned`` to the users they name, and every
    visibility to the activity's owner. Admins get no extra reach —
    ``mentioned`` and ``private`` replies stay confined to their audience.
    """
    conditions = [Activity.visibility == Visibility.PUBLIC.value]
    if user is not None:
        conditions.append(Activity.owner_user_id == user.id)
        conditions.append(Activity.visibility.in_([Visibility.LOCAL.value, Visibility.FOLLOWERS.value]))
        conditions.append(
            and_(
                Activity.visibility == Visibility.MENTIONED.value,
                exists().where(
                    ActivityMention.activity_id == Activity.id,
                    ActivityMention.user_id == user.id,
                ),
            )
        )
    return or_(*conditions)


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


def get_item_title(item: Any) -> Optional[str]:
    """Return a human-readable title for a shareable item, or ``None``."""
    return getattr(item, "title", None) or getattr(item, "name", None) or getattr(item, "original_filename", None)


async def resolve_item_titles(
    session: AsyncSession,
    items: Iterable[Tuple[str, str]],
) -> Dict[Tuple[str, str], Optional[str]]:
    """Batch-resolve display titles for ``(item_type, item_id)`` pairs.

    Returns a mapping keyed by ``(item_type, item_id)``; pairs whose item no
    longer exists are absent from the result so callers can distinguish a
    missing resource from one without a title.
    """
    ids_by_type: Dict[str, Set[str]] = {}
    for item_type, item_id in items:
        ids_by_type.setdefault(item_type, set()).add(item_id)

    titles: Dict[Tuple[str, str], Optional[str]] = {}
    for item_type, ids in ids_by_type.items():
        entry = _ITEM_REGISTRY.get(item_type)
        if entry is None:
            continue
        result = await session.execute(select(entry.model).where(entry.model.id.in_(ids)))
        for row in result.scalars().all():
            titles[(item_type, row.id)] = get_item_title(row)
    return titles


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

    # Rule 8: tracks inherit access from the album they belong to.
    if item_type == "track" and depth < _MAX_DERIVED_DEPTH:
        album_id = getattr(item, "album_id", None)
        if album_id is not None and await _can_access(
            session,
            user,
            "album",
            str(album_id),
            share_token=share_token,
            depth=depth + 1,
        ):
            return True

    # Rule 10: tracks inherit access from playlists they belong to.
    if item_type == "track" and depth < _MAX_DERIVED_DEPTH:
        playlist_ids = (
            (await session.execute(select(PlaylistTrack.playlist_id).where(PlaylistTrack.track_id == item.id)))
            .scalars()
            .all()
        )
        for playlist_id in playlist_ids:
            if await _can_access(
                session,
                user,
                "playlist",
                str(playlist_id),
                share_token=share_token,
                depth=depth + 1,
            ):
                return True

    # Rule 11: tracks inherit access from libraries they belong to.
    if item_type == "track" and depth < _MAX_DERIVED_DEPTH:
        library_ids = (
            (await session.execute(select(LibraryTrack.library_id).where(LibraryTrack.track_id == item.id)))
            .scalars()
            .all()
        )
        for library_id in library_ids:
            if await _can_access(
                session,
                user,
                "library",
                str(library_id),
                share_token=share_token,
                depth=depth + 1,
            ):
                return True

    # Rule 9: derived file access through owning tracks, albums, artists,
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

        # Images attached to an album or artist are also surfaced through that
        # entity's content: sharing a track reveals its album cover and artist
        # image, and sharing an album reveals the album artist's images.
        album_track_ids: Sequence[str] = ()
        if album_ids:
            album_track_ids = (
                (await session.execute(select(Track.id).where(Track.album_id.in_(album_ids)))).scalars().all()
            )
        artist_ids = set(artist_image_ids) | set(artist_cover_ids)
        artist_track_ids: Sequence[str] = ()
        artist_album_ids: Sequence[str] = ()
        if artist_ids:
            artist_track_ids = (
                (await session.execute(select(Track.id).where(Track.artist_id.in_(artist_ids)))).scalars().all()
            )
            artist_album_ids = (
                (await session.execute(select(Album.id).where(Album.artist_id.in_(artist_ids)))).scalars().all()
            )

        derived_item_ids = (
            [
                ("track", str(i))
                for i in set(track_ids) | set(track_image_ids) | set(album_track_ids) | set(artist_track_ids)
            ]
            + [("album", str(i)) for i in set(album_ids) | set(artist_album_ids)]
            + [("artist", str(i)) for i in artist_ids]
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


async def _can_access_user(session: AsyncSession, user: Optional[User], item_id: str) -> bool:
    """Return whether ``user`` may access the ``user`` entity ``item_id``.

    User profiles are the containing entity for standalone statuses: an
    active profile is visible to everyone (including anonymous viewers),
    while an inactive one is only reachable by its owner or an admin.
    """
    item = await session.get(User, item_id)
    if item is None:
        return False
    if item.is_active:
        return True
    return user is not None and (user.is_admin or str(item.id) == str(user.id))


async def _can_access_remote(session: AsyncSession, user: Optional[User], item_id: str) -> bool:
    """Return whether ``user`` may access the ``remote`` entity ``item_id``.

    Cached remote objects have no owner or share grants: public objects are
    visible to everyone, non-public ones (inbox-delivered) to authenticated
    users only. Activity-level visibility still applies on top through
    ``can_view_activity``.
    """
    item = await session.get(RemoteObject, item_id)
    if item is None:
        return False
    if item.visibility == "public":
        return True
    return user is not None


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
    if item_type == "user":
        return await _can_access_user(session, user, item_id)
    if item_type == "remote":
        return await _can_access_remote(session, user, item_id)
    return await _can_access(session, user, item_type, item_id, share_token=share_token)


_ACCESS_BATCH_SIZE = 1000


async def filter_accessible_track_ids(
    session: AsyncSession,
    user: Optional[User],
    track_ids: List[str],
    batch_size: int = _ACCESS_BATCH_SIZE,
) -> Set[str]:
    """
    Return the subset of ``track_ids`` that ``user`` may access.

    The check is pushed into the database using ``apply_access_filter`` and
    batched to avoid exceeding database parameter limits for very large inputs.
    """
    if not track_ids:
        return set()

    accessible: Set[str] = set()
    for i in range(0, len(track_ids), batch_size):
        chunk = track_ids[i : i + batch_size]
        stmt = apply_access_filter(
            select(Track.id).where(Track.id.in_(chunk)),
            Track,
            user,
            "track",
        )
        result = await session.execute(stmt)
        accessible.update(str(row) for row in result.scalars().all())
    return accessible


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

    if item_type == "remote":
        # Remote cache rows are read-only — nobody manages them locally.
        return False

    if item_type == "user":
        item = await session.get(User, item_id)
        return item is not None and (user.is_admin or str(item.id) == str(user.id))

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
