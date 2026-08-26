"""Shared Pydantic response models and summary builders.

These live outside individual route modules so that nested summary objects can
be reused across the API without creating import cycles.
"""

from typing import Optional

from pydantic import BaseModel, ConfigDict

from ..models._enums import Visibility
from ..services.storage import StorageService


class ArtistSummary(BaseModel):
    """Shallow artist object suitable for nesting inside other responses."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    image_url: Optional[str] = None


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
    owner_id: Optional[str] = None
    visibility: str = Visibility.PRIVATE.value


async def build_artist_summary(
    artist,
    storage: StorageService,
) -> Optional[ArtistSummary]:
    """Build an ArtistSummary, resolving the image URL when possible."""
    if artist is None:
        return None

    image_url = artist.image_url
    if artist.image_file_id and artist.image_file:
        image_url = await storage.get_url(artist.image_file)

    return ArtistSummary(
        id=str(artist.id),
        name=artist.name,
        image_url=image_url,
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


async def build_track_summary(
    track,
    storage: StorageService,
) -> Optional[TrackSummary]:
    """Build a TrackSummary, resolving the audio URL and artist/album when possible."""
    if track is None:
        return None

    audio_url = None
    if track.audio_file_id and track.audio_file:
        audio_url = await storage.get_url(track.audio_file)

    return TrackSummary(
        id=str(track.id),
        title=track.title,
        artist_id=track.artist_id,
        artist=await build_artist_summary(track.artist, storage),
        album_id=track.album_id,
        album=await build_album_summary(track.album, storage),
        track_number=track.track_number,
        disc_number=track.disc_number,
        duration=track.duration,
        audio_url=audio_url,
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
