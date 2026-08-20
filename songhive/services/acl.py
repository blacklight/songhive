"""
Access control service for shareable media items.

The ACL implements a three-level visibility model (private / local / public)
augmented with per-user share grants and revocable share URL tokens.  It is
designed to be used by the FastAPI route layer and by federation serializers.
"""

from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models._enums import Visibility
from ..models.album import Album
from ..models.library import Library
from ..models.playlist import Playlist
from ..models.radio import Radio
from ..models.share_grant import ShareGrant
from ..models.stored_file import StoredFile
from ..models.track import Track
from ..models.user import User
from . import sharing

ITEM_TYPES = {"track", "album", "playlist", "library", "radio", "file"}
_MODEL_MAP = {
    "track": Track,
    "album": Album,
    "playlist": Playlist,
    "library": Library,
    "radio": Radio,
    "file": StoredFile,
}
_MAX_DERIVED_DEPTH = 1


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
    if item_type not in ITEM_TYPES:
        raise ValueError(f"Unknown item type: {item_type!r}")

    model_class = _MODEL_MAP[item_type]
    item = await session.get(model_class, item_id)
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

    # Rule 4: ownerless items are treated as local-equivalent for legacy data.
    if (
        getattr(item, "owner_id", None) is None
        and user is not None
        and getattr(item, "visibility", None) == Visibility.PRIVATE.value
    ):
        return True

    # Rule 5: local items are visible to any authenticated user.
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

    # Rule 8: derived file access through owning tracks or albums.
    if item_type == "file" and depth < _MAX_DERIVED_DEPTH:
        track_ids = (await session.execute(select(Track.id).where(Track.audio_file_id == item_id))).scalars().all()
        album_ids = (await session.execute(select(Album.id).where(Album.cover_file_id == item_id))).scalars().all()

        for track_id in track_ids:
            if await _can_access(
                session,
                user,
                "track",
                str(track_id),
                share_token=share_token,
                depth=depth + 1,
            ):
                return True

        for album_id in album_ids:
            if await _can_access(
                session,
                user,
                "album",
                str(album_id),
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

    if item_type not in ITEM_TYPES:
        raise ValueError(f"Unknown item type: {item_type!r}")

    model_class = _MODEL_MAP[item_type]
    item = await session.get(model_class, item_id)
    if item is None:
        return False
    if user.is_admin:
        return True

    owner_id = getattr(item, "owner_id", None)
    if owner_id is None:
        return False

    return owner_id == user.id


async def filter_accessible_ids(
    session: AsyncSession,
    user: Optional[User],
    item_type: str,
    item_ids: List[str],
) -> List[str]:
    """
    Return the subset of ``item_ids`` that ``user`` is permitted to access.

    The current implementation evaluates the access rules per row.  This is
    acceptable for list-page sizes (<= 100); a future SQL-level rewrite can
    push more of the logic into a single query.
    """
    if not item_ids:
        return []

    if item_type not in ITEM_TYPES:
        raise ValueError(f"Unknown item type: {item_type!r}")

    model_class = _MODEL_MAP[item_type]
    result = await session.execute(select(model_class).where(model_class.id.in_(item_ids)))
    items = list(result.scalars().all())

    accessible: List[str] = []
    for item in items:
        if await _can_access(session, user, item_type, str(item.id)):
            accessible.append(str(item.id))

    return accessible
