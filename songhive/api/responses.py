"""Shared Pydantic response models and summary builders.

These live outside individual route modules so that nested summary objects can
be reused across the API without creating import cycles.
"""

from datetime import datetime
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
    actor_url: Optional[str] = None


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
    external_url: Optional[str] = None
    image_url: Optional[str] = None
    release_year: Optional[int] = None
    owner_id: Optional[str] = None
    visibility: str = Visibility.PRIVATE.value
    # Payment overlay — set only when an active sale gates the track.
    paid: bool = False
    unpaid_policy: Optional[str] = None
    price_minor: Optional[int] = None
    currency: Optional[str] = None


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
    description: Optional[str] = None
    extra_artists: List[str] = []
    audio_url: Optional[str] = None
    external_url: Optional[str] = None
    image_url: Optional[str] = None
    release_year: Optional[int] = None
    owner_id: Optional[str] = None
    visibility: str = Visibility.PRIVATE.value
    filename: Optional[str] = None
    artist: Optional[ArtistSummary] = None
    album: Optional[AlbumSummary] = None
    owner: Optional[UserSummary] = None
    tags: List[str] = []
    genres: List[str] = []
    favorited: Optional[bool] = None
    in_collection: bool = False
    is_external: bool = False
    external_library_id: Optional[str] = None
    external_track_id: Optional[str] = None
    external_provider_type: Optional[str] = None
    external_state: Optional[str] = None
    can_stream: Optional[bool] = None
    can_download: Optional[bool] = None
    can_write_tags: Optional[bool] = None
    can_rename_source: Optional[bool] = None
    can_delete_source: Optional[bool] = None
    # Payment overlay — set only when an active sale gates the track.
    paid: bool = False
    unpaid_policy: Optional[str] = None
    price_minor: Optional[int] = None
    currency: Optional[str] = None
    # Provider-declared list of locally-editable fields (e.g. TIDAL allows
    # only genres/tags); ``None`` for local or unrestricted tracks.
    editable_fields: Optional[List[str]] = None
    # Stream renditions the provider can serve (e.g. YouTube's
    # ``["audio", "video"]``); ``None`` for local or single-variant tracks.
    stream_variants: Optional[List[str]] = None
    # Exposed so list consumers can interleave remote entities under the
    # ``created_at``/``updated_at`` sort fields.
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


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


def _provider_stream_policy(provider_type: str) -> Optional[str]:
    """Return the instance-configured stream policy for a provider, if any."""
    try:
        from ..config.loader import load_config

        provider_cfg = getattr(load_config([]).external_libraries, provider_type, None)
        policy = getattr(provider_cfg, "stream_policy", None)
    except Exception:
        return None
    return policy if isinstance(policy, str) and policy else None


def _provider_external_url(external_library, external_ref) -> Optional[str]:
    """Return the provider's public browse URL for an entity, when known."""
    try:
        from ..external.registry import get_external_adapter

        adapter = get_external_adapter(external_library.provider_type)()
        kind = getattr(external_ref, "kind", None) or "track"
        return adapter.external_url(kind, external_ref.provider_key)
    except Exception:
        return None


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
    *,
    user=None,
    session=None,
    policy_cache: Optional[dict] = None,
) -> Optional[TrackSummary]:
    """Build a TrackSummary, resolving the audio URL, cover, and effective year.

    When ``session`` and ``user`` are provided, provider stream policies
    (e.g. TIDAL ``stream_policy``) are applied to ``audio_url``.
    """
    if track is None:
        return None

    audio_url = None
    external_url = None
    paid = False
    unpaid_policy = None
    price_minor = None
    currency = None
    if track.audio_file_id and _is_loaded(track, "audio_file") and track.audio_file:
        audio_url = await storage.get_url(track.audio_file)
    else:
        external_ref = getattr(track, "external_track", None) or getattr(track, "external_item", None)
        if external_ref is not None and external_ref.state == "active":
            audio_url = f"/api/v1/tracks/{track.id}/download"
            external_library = getattr(external_ref, "external_library", None)
            if (
                session is not None
                and external_library is not None
                and _provider_stream_policy(external_library.provider_type)
            ):
                from ..services.streaming import external_stream_allowed

                if not await external_stream_allowed(session, external_library, user, cache=policy_cache):
                    audio_url = None
                    external_url = _provider_external_url(external_library, external_ref)

    if session is not None:
        from ..services.payments import access as payment_access

        gate_cache = policy_cache.setdefault("sale_gates", {}) if policy_cache is not None else None
        track_access = await payment_access.gated_access(session, track, user, gate_cache=gate_cache)
        if track_access is not None and track_access.sale is not None:
            sale = track_access.sale
            paid = True
            unpaid_policy = sale.unpaid_policy
            price_minor = sale.price_minor
            currency = sale.currency
            audio_url = payment_access.audio_url_for(track, track_access)

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
        external_url=external_url,
        image_url=await _track_image_url(track, storage),
        release_year=_track_release_year(track),
        owner_id=track.owner_id,
        visibility=track.visibility,
        paid=paid,
        unpaid_policy=unpaid_policy,
        price_minor=price_minor,
        currency=currency,
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
        actor_url=user.actor_url,
    )
