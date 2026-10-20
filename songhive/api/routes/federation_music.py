"""
Music-entity federation documents (artists, albums, tracks).
"""

import asyncio
import uuid
from typing import Any, Iterable, Optional

from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ...config.schema import SonghiveConfig
from ...federation import doc_cache
from ...federation.serializers import (
    MUSIC_ENTITY_CONTEXT,
    album_to_music_object,
    artist_to_music_object,
    track_to_audio_object,
    track_to_music_track_object,
)
from ...models import Album, Artist, LibraryTrack, Track, User, Visibility
from ...models.base import get_session
from ...services.federation import ensure_user_actor
from ...services.payments import access as payment_access
from ..deps import get_config
from .federation_common import (
    _document_ttl,
    _serve_cached_document,
)
from .profile_pages import _accepts_activitypub, _accepts_html, _spa_response

router = APIRouter(include_in_schema=False)


def _instance_actor_url(config: SonghiveConfig) -> str:
    return f"https://{config.federation.instance_domain}/ap/actor"


def _owner_actor_url(owner: Optional[User], config: SonghiveConfig) -> str:
    if owner is not None:
        ensure_user_actor(owner, config)
        if owner.actor_url:
            return owner.actor_url
    return _instance_actor_url(config)


async def _audio_items(
    db: AsyncSession,
    tracks: Iterable[Track],
    domain: str,
    actor_url: str,
    library_url: str,
    config: Optional[SonghiveConfig] = None,
) -> list[dict]:
    tracks = [track for track in tracks if track.artist is not None]
    for track in tracks:
        # The ``Audio`` object id lives under the *owner's* actor — the
        # ``get_object`` route resolves objects per owning user — so mint
        # the id lazily here to let library pages and later ``Create``
        # deliveries share one dereferenceable id for the track's upload.
        if not track.federation_object_id:
            track.federation_object_id = str(uuid.uuid4())
    if tracks:
        await db.flush()
    gates = await payment_access.effective_gates(db, tracks)
    items = []
    for track in tracks:
        owner = getattr(track, "owner", None)
        item_actor = _owner_actor_url(owner, config) if config is not None and owner is not None else actor_url
        doc = track_to_audio_object(
            track,
            track.artist,
            domain,
            actor_url=item_actor,
            ap_object_id=f"{item_actor}/objects/{track.federation_object_id}",
            library_url=library_url,
            sale=gates.get(str(track.id)),
        )
        if doc is not None:
            items.append(doc)
    return items


def _library_track_query(library_id: str):
    return (
        select(Track)
        .join(LibraryTrack, LibraryTrack.track_id == Track.id)
        .where(
            LibraryTrack.library_id == library_id,
            Track.visibility == Visibility.PUBLIC.value,
        )
        .options(
            selectinload(Track.artist),
            selectinload(Track.album),
            selectinload(Track.audio_file),
            selectinload(Track.genre_associations),
        )
        .order_by(LibraryTrack.created_at.desc())
    )


def _library_track_count(library_id: str):
    return (
        select(func.count(Track.id))
        .join(LibraryTrack, LibraryTrack.track_id == Track.id)
        .where(
            LibraryTrack.library_id == library_id,
            Track.visibility == Visibility.PUBLIC.value,
        )
    )


async def _followers_collection_dict(
    storage: Any,
    object_url: str,
) -> dict:
    """Return an ``OrderedCollection`` of an object's follower actor ids."""
    followers = await asyncio.to_thread(storage.get_followers_of_targets, {object_url})
    actor_ids = sorted({f.actor_id for f in followers})
    return {
        "@context": MUSIC_ENTITY_CONTEXT,
        "id": f"{object_url}/followers",
        "type": "OrderedCollection",
        "totalItems": len(actor_ids),
        "orderedItems": actor_ids,
    }


@router.get("/artists/{artist_id}")
async def get_artist_document(
    artist_id: str,
    request: Request,
    config: SonghiveConfig = Depends(get_config),
) -> Response:
    """Serve an Artist as a federated music ``Artist`` document.

    Artists have no visibility of their own (they are public containers),
    so the document is always dereferenceable. ``attributedTo`` points at
    the instance actor — artists are shared catalog entities with no owner.
    """
    if _accepts_html(request) and not _accepts_activitypub(request):
        return _spa_response()
    ttl = _document_ttl(config)

    async def _render() -> Optional[dict]:
        async with get_session() as db:
            artist = (
                await db.execute(select(Artist).options(selectinload(Artist.image_file)).where(Artist.id == artist_id))
            ).scalar_one_or_none()
            if artist is None:
                return None
            doc = artist_to_music_object(artist, config.federation.instance_domain, _instance_actor_url(config))
            doc["@context"] = MUSIC_ENTITY_CONTEXT
            return doc

    document = await doc_cache.get_or_render(("artist", artist_id), ttl, _render)
    return _serve_cached_document(request, ("artist", artist_id), ttl, document)


async def _album_music_document(db: AsyncSession, album_id: str, config: SonghiveConfig) -> Optional[dict]:
    """Render the federated ``Album`` document for a public album, or ``None``."""
    album = (
        await db.execute(
            select(Album)
            .options(selectinload(Album.artist), selectinload(Album.cover_file))
            .where(Album.id == album_id, Album.visibility == Visibility.PUBLIC.value)
        )
    ).scalar_one_or_none()
    if album is None:
        return None
    return album_to_music_object(album, album.artist, config.federation.instance_domain, _instance_actor_url(config))


@router.get("/albums/{album_id}")
async def get_album_document(
    album_id: str,
    request: Request,
    config: SonghiveConfig = Depends(get_config),
) -> Response:
    """Serve a public Album as a federated music ``Album`` document."""
    if _accepts_html(request) and not _accepts_activitypub(request):
        return _spa_response()
    ttl = _document_ttl(config)

    async def _render() -> Optional[dict]:
        async with get_session() as db:
            doc = await _album_music_document(db, album_id, config)
            if doc is not None:
                doc["@context"] = MUSIC_ENTITY_CONTEXT
            return doc

    document = await doc_cache.get_or_render(("album", album_id), ttl, _render)
    return _serve_cached_document(request, ("album", album_id), ttl, document)


def _find_artist_credit(doc: dict, artist_id: str) -> Optional[dict]:
    """
    Return the ``artist_credit`` entry crediting ``artist_id``, or ``None``.

    Credit ids end with ``/credits/{artist_id}`` — matching the suffix (not
    the contained artist doc) keeps the lookup unambiguous on nested docs.
    """

    def _match(credits: Any) -> Optional[dict]:
        for credit in credits or []:
            if isinstance(credit, dict) and str(credit.get("id") or "").rsplit("/credits/", 1)[-1] == artist_id:
                return credit
        return None

    found = _match(doc.get("artist_credit"))
    if found is None:
        album = doc.get("album")
        if isinstance(album, dict):
            found = _match(album.get("artist_credit"))
    return found


@router.get("/albums/{album_id}/credits/{artist_id}")
async def get_album_credit_document(
    album_id: str,
    artist_id: str,
    request: Request,
    config: SonghiveConfig = Depends(get_config),
) -> Response:
    """Dereference an ``ArtistCredit`` embedded in an ``Album`` document."""
    ttl = _document_ttl(config)

    async def _render() -> Optional[dict]:
        async with get_session() as db:
            doc = await _album_music_document(db, album_id, config)
            if doc is None:
                return None
            credit = _find_artist_credit(doc, artist_id)
            if credit is not None:
                credit = {"@context": MUSIC_ENTITY_CONTEXT, **credit}
            return credit

    document = await doc_cache.get_or_render(("album", album_id, "credit", artist_id), ttl, _render)
    return _serve_cached_document(request, ("album", album_id, "credit", artist_id), ttl, document)


async def _music_track_document(db: AsyncSession, track_id: str, config: SonghiveConfig) -> Optional[dict]:
    """Render the federated ``Track`` document for a public track, or ``None``."""
    track = (
        await db.execute(
            select(Track)
            .options(
                selectinload(Track.artist).selectinload(Artist.image_file),
                selectinload(Track.album).selectinload(Album.artist),
                selectinload(Track.album).selectinload(Album.cover_file),
                selectinload(Track.owner),
            )
            .where(Track.id == track_id, Track.visibility == Visibility.PUBLIC.value)
        )
    ).scalar_one_or_none()
    if track is None or track.artist is None:
        return None
    return track_to_music_track_object(
        track,
        track.artist,
        config.federation.instance_domain,
        _owner_actor_url(track.owner, config),
    )


@router.get("/music/tracks/{track_id}")
async def get_music_track_document(
    track_id: str,
    request: Request,
    config: SonghiveConfig = Depends(get_config),
) -> Response:
    """
    Serve a public track's federated ``Track`` document.

    ``/tracks/{id}`` stays the human page (its AP dereference returns the
    published ``Audio``); the catalog ``Track`` object dereferences at
    ``/music/tracks/{id}`` — matching the upload/recording split Funkwhale
    makes between its ``uploads`` and ``tracks`` federation URLs.
    """
    if _accepts_html(request) and not _accepts_activitypub(request):
        return RedirectResponse(url=f"/tracks/{track_id}", status_code=status.HTTP_307_TEMPORARY_REDIRECT)
    ttl = _document_ttl(config)

    async def _render() -> Optional[dict]:
        async with get_session() as db:
            doc = await _music_track_document(db, track_id, config)
            if doc is not None:
                doc["@context"] = MUSIC_ENTITY_CONTEXT
            return doc

    document = await doc_cache.get_or_render(("mtrack", track_id), ttl, _render)
    return _serve_cached_document(request, ("mtrack", track_id), ttl, document)


@router.get("/music/tracks/{track_id}/album")
async def get_music_track_album_document(
    track_id: str,
    request: Request,
    config: SonghiveConfig = Depends(get_config),
) -> Response:
    """
    Serve the synthesized single-track ``Album`` of an album-less track.

    Real albums dereference at ``/albums/{id}``; only tracks without an
    album answer here, matching the ``{track}/album`` document embedded in
    their ``Audio`` objects.
    """
    ttl = _document_ttl(config)

    async def _render() -> Optional[dict]:
        async with get_session() as db:
            doc = await _music_track_document(db, track_id, config)
            album = doc.get("album") if doc is not None else None
            if doc is None or not isinstance(album, dict) or album.get("id") != f"{doc.get('id')}/album":
                return None
            return {"@context": MUSIC_ENTITY_CONTEXT, **album}

    document = await doc_cache.get_or_render(("mtrack", track_id, "album"), ttl, _render)
    return _serve_cached_document(request, ("mtrack", track_id, "album"), ttl, document)


@router.get("/music/tracks/{track_id}/album/credits/{artist_id}")
@router.get("/music/tracks/{track_id}/credits/{artist_id}")
async def get_music_track_credit_document(
    track_id: str,
    artist_id: str,
    request: Request,
    config: SonghiveConfig = Depends(get_config),
) -> Response:
    """Dereference an ``ArtistCredit`` embedded in a ``Track`` document."""
    ttl = _document_ttl(config)

    async def _render() -> Optional[dict]:
        async with get_session() as db:
            doc = await _music_track_document(db, track_id, config)
            if doc is None:
                return None
            credit = _find_artist_credit(doc, artist_id)
            if credit is not None:
                credit = {"@context": MUSIC_ENTITY_CONTEXT, **credit}
            return credit

    document = await doc_cache.get_or_render(("mtrack", track_id, "credit", artist_id), ttl, _render)
    return _serve_cached_document(request, ("mtrack", track_id, "credit", artist_id), ttl, document)
