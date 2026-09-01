"""Shared Pydantic response models and summary builders.

These live outside individual route modules so that nested summary objects can
be reused across the API without creating import cycles.
"""

from typing import List, Optional

from pydantic import BaseModel, ConfigDict
from sqlalchemy import inspect

from ..models._enums import Visibility
from ..services.storage import StorageService


def _is_loaded(obj, attr: str) -> bool:
    """Return True when ``attr`` has already been loaded on ``obj``."""
    try:
        state = inspect(obj)
        if not state.unloaded or attr not in state.unloaded:
            return True
        return False
    except Exception:
        return False


class ArtistSummary(BaseModel):
    """Shallow artist object suitable for nesting inside other responses."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    image_url: Optional[str] = None
    cover_url: Optional[str] = None


class UserSummary(BaseModel):
    """Shallow user object suitable for nesting as an owner reference."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    username: str
    display_name: Optional[str] = None
    avatar_url: Optional[str] = None


class AlbumSummary(BaseModel):
    """Shallow album object suitable for nesting inside other responses."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    artist_id: str
    artist: Optional[ArtistSummary] = None
    musicbrainz_id: Optional[str] = None
    release_year: Optional[int] = None
    cover_url: Optional[str] = None
    owner_id: Optional[str] = None
    visibility: str = Visibility.PRIVATE.value


class TrackSummary(BaseModel):
    """Shallow track object suitable for nesting inside other responses."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    artist_id: str
    artist: Optional[ArtistSummary] = None
    album_id: Optional[str] = None
    album: Optional[AlbumSummary] = None
    track_number: Optional[int] = None
    disc_number: Optional[int] = None
    duration: Optional[float] = None
    audio_url: Optional[str] = None
    image_url: Optional[str] = None
    release_year: Optional[int] = None
    owner_id: Optional[str] = None
    visibility: str = Visibility.PRIVATE.value


class TrackResponse(BaseModel):
    """Public track response."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    artist_id: str
    album_id: Optional[str] = None
    track_number: Optional[int] = None
    disc_number: Optional[int] = None
    duration: Optional[float] = None
    genre: Optional[str] = None
    audio_url: Optional[str] = None
    image_url: Optional[str] = None
    release_year: Optional[int] = None
    owner_id: Optional[str] = None
    visibility: str = Visibility.PRIVATE.value
    artist: Optional[ArtistSummary] = None
    album: Optional[AlbumSummary] = None
    owner: Optional[UserSummary] = None
    hashtags: List[str] = []
    genres: List[str] = []
    favorited: Optional[bool] = None
    is_external: bool = False
    external_library_id: Optional[str] = None
    external_provider_type: Optional[str] = None
    external_state: Optional[str] = None
    can_stream: Optional[bool] = None
    can_download: Optional[bool] = None
    can_write_tags: Optional[bool] = None
    can_delete_source: Optional[bool] = None


async def build_artist_summary(
    artist,
    storage: StorageService,
) -> Optional[ArtistSummary]:
    """Build an ArtistSummary, resolving the image and cover URLs when possible."""
    if artist is None:
        return None

    image_url = artist.image_url
    if artist.image_file_id and artist.image_file:
        image_url = await storage.get_url(artist.image_file)

    cover_url = None
    if artist.cover_file_id and artist.cover_file:
        cover_url = await storage.get_url(artist.cover_file)

    return ArtistSummary(
        id=str(artist.id),
        name=artist.name,
        image_url=image_url,
        cover_url=cover_url,
    )


async def build_album_summary(
    album,
    storage: StorageService,
) -> Optional[AlbumSummary]:
    """Build an AlbumSummary, resolving the cover URL and artist when possible."""
    if album is None:
        return None

    cover_url = album.cover_url
    if album.cover_file_id and album.cover_file:
        cover_url = await storage.get_url(album.cover_file)

    return AlbumSummary(
        id=str(album.id),
        title=album.title,
        artist_id=album.artist_id,
        artist=await build_artist_summary(album.artist, storage),
        musicbrainz_id=album.musicbrainz_id,
        release_year=album.release_year,
        cover_url=cover_url,
        owner_id=album.owner_id,
        visibility=album.visibility,
    )


def _track_release_year(track) -> Optional[int]:
    """Return the track's year, falling back to the album's year when loaded."""
    if track.release_year is not None:
        return track.release_year
    if track.album_id and _is_loaded(track, "album") and track.album is not None:
        return track.album.release_year
    return None


async def _track_image_url(track, storage: StorageService) -> Optional[str]:
    """Return the track's image URL, falling back to the album cover when loaded."""
    if track.image_file_id and track.image_file:
        return await storage.get_url(track.image_file)
    if track.album_id and _is_loaded(track, "album") and track.album is not None:
        if track.album.cover_file_id and track.album.cover_file:
            return await storage.get_url(track.album.cover_file)
        return track.album.cover_url
    return None


async def build_track_summary(
    track,
    storage: StorageService,
) -> Optional[TrackSummary]:
    """Build a TrackSummary, resolving the audio URL, cover, and effective year."""
    if track is None:
        return None

    audio_url = None
    if track.audio_file_id and _is_loaded(track, "audio_file") and track.audio_file:
        audio_url = await storage.get_url(track.audio_file)

    artist = None
    if _is_loaded(track, "artist") and track.artist is not None:
        artist = await build_artist_summary(track.artist, storage)

    album = None
    if _is_loaded(track, "album") and track.album is not None:
        album = await build_album_summary(track.album, storage)

    return TrackSummary(
        id=str(track.id),
        title=track.title,
        artist_id=track.artist_id,
        artist=artist,
        album_id=track.album_id,
        album=album,
        track_number=track.track_number,
        disc_number=track.disc_number,
        duration=track.duration,
        audio_url=audio_url,
        image_url=await _track_image_url(track, storage),
        release_year=_track_release_year(track),
        owner_id=track.owner_id,
        visibility=track.visibility,
    )


async def build_user_summary(user) -> Optional[UserSummary]:
    """Build a UserSummary from a User model."""
    if user is None:
        return None

    return UserSummary(
        id=str(user.id),
        username=user.username,
        display_name=user.display_name,
        avatar_url=user.avatar_url,
    )
