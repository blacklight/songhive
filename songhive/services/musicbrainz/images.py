"""Image enrichment — part of the ``musicbrainz`` service."""

import datetime
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, cast

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from ...models.album import Album
from ...models.artist import Artist
from ...models.track import Track

if TYPE_CHECKING:
    from ...config.schema import MusicBrainzConfig
    from ...services.storage import StorageService

import logging

from .metadata import (
    _MAX_COVER_RELEASE_CANDIDATES,
    _is_missing_album,
    _is_missing_artist,
    _release_group_id,
    _release_rank,
)

logger = logging.getLogger(__name__)


class _ImageEnrichment:
    if TYPE_CHECKING:
        # Provided by the host class (MusicBrainzService).
        config: MusicBrainzConfig
        _find_artist_image_url: Callable[..., Any]
        _download_image: Callable[..., Any]
        _store_image_file: Callable[..., Any]
        _store_cover_art: Callable[..., Any]
        search_releases: Callable[..., Any]

    async def enrich_images(
        self,
        session,
        track_id: str,
        storage_service: Optional[StorageService] = None,
        force: bool = False,
    ) -> bool:
        """
        Enrich an artist image and album cover for a track.

        Artist images are resolved from MusicBrainz URL relations (e.g.
        Wikimedia Commons or direct image URLs). Album covers use the Cover Art
        Archive when a release MusicBrainz ID is known.

        :returns: ``True`` if the artist or album was updated.
        """
        if not self.config.enabled or not self.config.fetch_artist_images:
            return False

        track = await self._load_track_for_images(session, track_id)
        if track is None:
            return False

        updated = False
        artist = track.artist
        if artist is not None:
            artist_updated = await self.enrich_artist_image(
                session,
                artist,
                storage_service,
                owner_id=track.owner_id,
                visibility=track.visibility,
                force=force,
            )
            updated = updated or artist_updated

        album = track.album
        if album is not None and storage_service is not None:
            album_updated = await self._maybe_store_cover_art_for_image_enrichment(
                session,
                album,
                storage_service,
                force=force,
            )
            updated = updated or album_updated

        await session.commit()
        return updated

    async def _load_track_for_images(
        self,
        session,
        track_id: str,
    ) -> Optional[Track]:
        """Fetch the track and its artist and album for image enrichment."""
        result = await session.execute(
            select(Track)
            .options(
                selectinload(Track.artist),
                selectinload(Track.album),
            )
            .where(Track.id == track_id)
        )
        track = cast(Optional[Track], result.scalar_one_or_none())
        if track is None:
            logger.warning("Track %s not found for image enrichment", track_id)
            return None
        return track

    async def enrich_artist_image_by_id(
        self,
        session,
        artist_id: str,
        storage_service: Optional[StorageService] = None,
        force: bool = False,
    ) -> bool:
        """Fetch and store an artist image by artist ID."""
        result = await session.execute(select(Artist).where(Artist.id == artist_id))
        artist = cast(Optional[Artist], result.scalar_one_or_none())
        if artist is None:
            logger.warning("Artist %s not found for image enrichment", artist_id)
            return False

        return await self.enrich_artist_image(
            session,
            artist,
            storage_service,
            owner_id=None,
            visibility=None,
            force=force,
        )

    async def enrich_album_cover_by_id(
        self,
        session,
        album_id: str,
        storage_service: StorageService,
        force: bool = False,
    ) -> bool:
        """Fetch and store an album cover by album ID."""
        result = await session.execute(select(Album).where(Album.id == album_id))
        album = cast(Optional[Album], result.scalar_one_or_none())
        if album is None:
            logger.warning("Album %s not found for cover enrichment", album_id)
            return False

        return await self._maybe_store_cover_art_for_image_enrichment(
            session,
            album,
            storage_service,
            force=force,
        )

    async def enrich_artist_image(
        self,
        session,
        artist: Artist,
        storage_service: Optional[StorageService] = None,
        *,
        owner_id: Optional[str] = None,
        visibility: Optional[str] = None,
        force: bool = False,
    ) -> bool:
        """Fetch and store an artist image when missing."""
        if artist.image_file_id is not None and not force:
            return False
        if not force and artist.image_enriched_at is not None:
            logger.debug("Artist %s already image enriched; skipping", artist.id)
            return False
        if not artist.musicbrainz_id:
            return False

        image_url = await self._find_artist_image_url(artist.musicbrainz_id)
        if not image_url:
            return False

        if storage_service is None:
            artist.image_url = image_url
            self._mark_image_enriched(artist)
            return True

        data = await self._download_image(image_url)
        if not data:
            logger.debug(
                "Could not download artist image for %s from %s",
                artist.id,
                image_url,
            )
            return False

        stored = await self._store_image_file(
            session,
            artist,
            "image_file_id",
            data,
            storage_service,
            owner_id=owner_id,
            visibility=visibility,
        )
        if stored is None:
            logger.debug("Could not store artist image for %s", artist.id)
            return False

        artist.image_url = None
        self._mark_image_enriched(artist)
        return True

    async def _maybe_store_cover_art_for_image_enrichment(
        self,
        session,
        album: Album,
        storage_service: StorageService,
        force: bool = False,
    ) -> bool:
        """
        Store album cover art when missing.

        The release already linked to the album is tried first — it was picked
        by metadata enrichment. When it yields no artwork (or no release is
        linked at all), MusicBrainz is searched for releases of the same album
        and the most canonical candidates are tried in ranked order. When a
        searched release provides the artwork, the album is re-linked to it so
        later enrichment (and the stored MusicBrainz ID) reflects the better
        match.
        """
        if album.cover_file_id is not None and not force:
            return False
        if not force and album.cover_enriched_at is not None:
            return False

        if album.musicbrainz_id:
            await self._store_cover_art(
                session,
                album,
                album.musicbrainz_id,
                storage_service,
                owner_id=album.owner_id,
            )
            if album.cover_file_id is not None:
                self._mark_cover_enriched(album)
                return True

        candidates = await self._ranked_cover_releases(album)
        for release in candidates[:_MAX_COVER_RELEASE_CANDIDATES]:
            release_id = release.get("id")
            if not isinstance(release_id, str) or not release_id or release_id == album.musicbrainz_id:
                continue
            had_cover = album.cover_file_id is not None
            await self._store_cover_art(
                session,
                album,
                release_id,
                storage_service,
                owner_id=album.owner_id,
                release_group_id=_release_group_id(release),
            )
            if album.cover_file_id is None:
                continue
            if not had_cover:
                await self._link_album_release(session, album, release_id)
            self._mark_cover_enriched(album)
            return True

        return False

    async def _ranked_cover_releases(self, album: Album) -> List[Dict[str, Any]]:
        """Search MusicBrainz for releases of an album, most canonical first."""
        if _is_missing_album(album):
            return []

        artist_name = None
        if album.artist is not None and not _is_missing_artist(album.artist):
            artist_name = album.artist.name

        try:
            results = await self.search_releases(
                artist=artist_name,
                release=album.title,
                limit=10,
            )
        except Exception as exc:
            logger.debug("Release search failed for album %s: %s", album.id, exc)
            return []

        releases = results.get("release-list") or []
        candidates = [release for release in releases if isinstance(release, dict) and release.get("id")]
        return sorted(
            candidates,
            key=lambda release: _release_rank(release, album.title),
            reverse=True,
        )

    @staticmethod
    async def _link_album_release(session, album: Album, release_id: str) -> None:
        """Point an album at a different MusicBrainz release when it is free."""
        result = await session.execute(select(Album.id).where(Album.musicbrainz_id == release_id).limit(1))
        if result.scalar_one_or_none() is None:
            album.musicbrainz_id = release_id

    @staticmethod
    def _mark_image_enriched(artist: Artist) -> None:
        """Mark an artist as having been processed for images."""
        artist.image_enriched_at = datetime.datetime.now(datetime.timezone.utc)

    @staticmethod
    def _mark_cover_enriched(album: Album) -> None:
        """Mark an album as having been processed for cover art."""
        album.cover_enriched_at = datetime.datetime.now(datetime.timezone.utc)
