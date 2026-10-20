"""
Artist response builders — extracted from ``artists.py`` routes.
"""

from datetime import datetime
from typing import List, Optional, Set

from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from ...models.user import User
from ...services.storage import StorageService
from .._include import IncludeQuery
from ..responses import (
    AlbumSummary,
    TrackSummary,
    _is_loaded,
    build_album_summary,
    build_track_summary,
)


class ArtistResponse(BaseModel):
    """Public artist response."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    musicbrainz_id: Optional[str] = None
    bio: Optional[str] = None
    image_file_id: Optional[str] = None
    image_url: Optional[str] = None
    cover_url: Optional[str] = None
    albums: Optional[List[AlbumSummary]] = None
    tracks: Optional[List[TrackSummary]] = None
    in_collection: bool = False
    tags: List[str] = []
    # Exposed so list consumers can interleave remote entities under the
    # ``created_at``/``updated_at`` sort fields.
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    # Provider-declared list of locally-editable fields; ``None`` for local
    # or unrestricted artists.
    editable_fields: Optional[List[str]] = None
    # Provider type backing the artist (e.g. ``"tidal"``); ``None`` when local.
    external_provider_type: Optional[str] = None


async def _image_url(artist, storage: StorageService) -> Optional[str]:
    """Resolve an artist image URL from a stored file or remote URL."""
    if artist.image_file_id and artist.image_file:
        return await storage.get_url(artist.image_file)
    return artist.image_url


async def _cover_url(artist, storage: StorageService) -> Optional[str]:
    """Resolve an artist cover art URL from a stored file."""
    if artist.cover_file_id and artist.cover_file:
        return await storage.get_url(artist.cover_file)
    return None


def _artist_album_sort_key(album):
    """Return a sort key for an artist's albums."""
    return (album.release_year or 0, album.created_at)


def _artist_track_sort_key(track):
    """Return a sort key for an artist's tracks."""
    return (track.album_id or "", track.disc_number or 0, track.track_number or 0, track.created_at)


def _artist_tags(artist) -> List[str]:
    """Return loaded tag names, avoiding a lazy load."""
    if not _is_loaded(artist, "tags"):
        return []
    return [h.name for h in artist.tags]


async def _artist_album_summaries(
    artist, storage: StorageService, include: IncludeQuery
) -> Optional[List[AlbumSummary]]:
    if "albums" not in include:
        return None
    artist_albums = getattr(artist, "albums", None)
    if artist_albums is None:
        return None
    album_list: List[AlbumSummary] = []
    for a in sorted(artist_albums, key=_artist_album_sort_key):
        album_summary = await build_album_summary(a, storage)
        if album_summary is not None:
            album_list.append(album_summary)
    return album_list


async def _artist_track_summaries(
    artist,
    storage: StorageService,
    include: IncludeQuery,
    user: Optional[User],
    db: Optional[AsyncSession],
) -> Optional[List[TrackSummary]]:
    if "tracks" not in include:
        return None
    artist_tracks = getattr(artist, "tracks", None)
    if artist_tracks is None:
        return None
    track_list: List[TrackSummary] = []
    policy_cache: dict = {}
    for t in sorted(artist_tracks, key=_artist_track_sort_key):
        track_summary = await build_track_summary(t, storage, user=user, session=db, policy_cache=policy_cache)
        if track_summary is not None:
            track_list.append(track_summary)
    return track_list


async def _artist_external_fields(db: Optional[AsyncSession], artist):
    if db is None:
        return None, None
    from ...services.provider_catalog import (
        editable_fields_for_entity,
        provider_type_for_entity,
    )

    editable_fields = await editable_fields_for_entity(db, "artist", str(artist.id))
    external_provider_type = None
    if editable_fields is not None:
        external_provider_type = await provider_type_for_entity(db, "artist", str(artist.id))
    return editable_fields, external_provider_type


async def _build_artist_response(
    artist,
    storage: StorageService,
    include: IncludeQuery,
    saved_ids: Optional[Set[str]] = None,
    user: Optional[User] = None,
    db: Optional[AsyncSession] = None,
) -> ArtistResponse:
    """Build an ArtistResponse with optional nested summaries."""
    albums = await _artist_album_summaries(artist, storage, include)
    tracks = await _artist_track_summaries(artist, storage, include, user, db)
    editable_fields, external_provider_type = await _artist_external_fields(db, artist)

    return ArtistResponse(
        id=str(artist.id),
        name=artist.name,
        musicbrainz_id=artist.musicbrainz_id,
        bio=artist.bio,
        image_file_id=artist.image_file_id,
        image_url=await _image_url(artist, storage),
        cover_url=await _cover_url(artist, storage),
        albums=albums,
        tracks=tracks,
        in_collection=saved_ids is not None and str(artist.id) in saved_ids,
        tags=_artist_tags(artist),
        created_at=artist.created_at,
        updated_at=artist.updated_at,
        editable_fields=editable_fields,
        external_provider_type=external_provider_type,
    )
