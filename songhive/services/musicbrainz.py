"""
MusicBrainz enrichment service.
"""

import asyncio
import datetime
import io
import logging
import re
import time
import urllib.parse
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, cast

import httpx
import musicbrainzngs
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from ..config.schema import MusicBrainzConfig
from ..models.album import Album
from ..models.artist import Artist
from ..models.stored_file import StoredFile
from ..models.track import Track
from ..services.storage import StorageService, is_unique_constraint_error

logger = logging.getLogger(__name__)


# The Cover Art Archive /front endpoint can return a chain of 307/302
# redirects before the actual image, so follow them manually up to this many.
_MAX_COVER_ART_REDIRECTS = 10


def _escape_query_term(term: str) -> str:
    """Quote a MusicBrainz query term so spaces and special chars are handled."""
    escaped = term.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


class MusicBrainzService:
    """Async wrapper around the MusicBrainz / Cover Art Archive APIs."""

    def __init__(
        self,
        config: MusicBrainzConfig,
        client: Optional[httpx.AsyncClient] = None,
    ):
        self.config = config
        self._client = client or httpx.AsyncClient(
            timeout=30.0,
            follow_redirects=True,
            headers={"User-Agent": config.user_agent},
        )
        self._lock = asyncio.Lock()
        self._last_request: Optional[float] = None
        if config.enabled:
            musicbrainzngs.set_useragent(
                "Songhive",
                "0.1",
                config.user_agent,
            )

    @property
    def _interval(self) -> float:
        return 1.0 / max(self.config.rate_limit_per_second, 0.001)

    async def _throttle(self) -> None:
        async with self._lock:
            now = time.monotonic()
            if self._last_request is not None:
                sleep_for = self._interval - (now - self._last_request)
                if sleep_for > 0:
                    await asyncio.sleep(sleep_for)
            self._last_request = time.monotonic()

    async def _call(self, func, *args, **kwargs) -> Any:
        """Run a sync musicbrainzngs call under the rate limiter."""
        await self._throttle()
        return await asyncio.to_thread(func, *args, **kwargs)

    async def search_recordings(
        self,
        *,
        query: Optional[str] = None,
        artist: Optional[str] = None,
        title: Optional[str] = None,
        release: Optional[str] = None,
        limit: int = 10,
    ) -> Dict[str, Any]:
        """Search for recordings using a free-form or structured query."""
        if query is None:
            parts = []
            if artist:
                parts.append(f"artist:{_escape_query_term(artist)}")
            if title:
                parts.append(f"recording:{_escape_query_term(title)}")
            if release:
                parts.append(f"release:{_escape_query_term(release)}")
            query = " ".join(parts)

        if not query:
            return {}

        return cast(
            Dict[str, Any],
            await self._call(
                musicbrainzngs.search_recordings,
                query=query,
                limit=limit,
            ),
        )

    async def fetch_recording(
        self,
        recording_id: str,
        *,
        include_artist_rels: bool = False,
        include_releases: bool = False,
    ) -> Dict[str, Any]:
        """Fetch a recording by its MusicBrainz ID."""
        includes: list[str] = []
        if include_artist_rels:
            includes.append("artist-rels")
        if include_releases:
            includes.append("releases")

        return cast(
            Dict[str, Any],
            await self._call(
                musicbrainzngs.get_recording_by_id,
                recording_id,
                includes,
            ),
        )

    async def fetch_artist(
        self,
        artist_id: str,
        *,
        include_url_rels: bool = False,
    ) -> Dict[str, Any]:
        """Fetch an artist by its MusicBrainz ID."""
        includes: list[str] = []
        if include_url_rels:
            includes.append("url-rels")

        return cast(
            Dict[str, Any],
            await self._call(
                musicbrainzngs.get_artist_by_id,
                artist_id,
                includes,
            ),
        )

    async def fetch_release(self, release_id: str) -> Optional[str]:
        """Return the front cover URL for a release, or ``None``."""
        if not self.config.fetch_cover_art:
            return None

        url = f"https://coverartarchive.org/release/{release_id}/front"
        try:
            response = await self._client.get(url, follow_redirects=True)
        except Exception as exc:
            logger.debug("Cover art request failed for %s: %s", release_id, exc)
            return None

        if response.status_code == 200:
            return str(response.url)
        if 300 <= response.status_code < 400:
            return str(response.headers.get("location") or "")
        return None

    async def fetch_cover_image(self, release_id: str) -> Optional[bytes]:
        """Download the front cover image bytes for a release."""
        if not self.config.fetch_cover_art:
            return None

        url = f"https://coverartarchive.org/release/{release_id}/front"
        for _ in range(_MAX_COVER_ART_REDIRECTS):
            try:
                response = await self._client.get(url)
            except Exception as exc:
                logger.debug("Could not download cover art for %s: %s", release_id, exc)
                return None

            if 200 <= response.status_code < 300:
                data = response.content
                if isinstance(data, bytes) and data:
                    return data
                return None

            if 300 <= response.status_code < 400:
                location = response.headers.get("location")
                if not location:
                    logger.debug("Cover art redirect for %s has no Location header", release_id)
                    return None
                url = str(location)
                continue

            logger.debug("Cover art request for %s returned %s", release_id, response.status_code)
            return None

        logger.debug("Too many cover art redirects for %s", release_id)
        return None

    async def _store_cover_art(
        self,
        session,
        album: Album,
        release_id: str,
        storage_service: StorageService,
        owner_id: Optional[str] = None,
    ) -> None:
        """Fetch and store cover art for an album when missing."""
        if album.cover_file_id is not None:
            return

        data = await self.fetch_cover_image(release_id)
        if data is None:
            return

        content_type = _guess_image_mime(data)

        stored, _ = await storage_service.store_file(
            session,
            io.BytesIO(data),
            content_type,
            prefix="covers",
            owner_id=owner_id,
            visibility=album.visibility,
            return_duplicate=True,
        )
        album.cover_file_id = str(stored.id)

    async def enrich_track(
        self,
        session,
        track_id: str,
        storage_service: Optional[StorageService] = None,
        force: bool = False,
    ) -> bool:
        """
        Enrich a track with MusicBrainz data and optionally cover art.

        Missing fields (title, artist, album, year, track number, disc number,
        duration) are populated from MusicBrainz when a match is found. If no
        match can be found, the track is marked as enriched so it is not
        processed again.

        :returns: ``True`` if the track was updated.
        """
        if not self.config.enabled:
            return False

        track, artist, album = await self._load_track_context(session, track_id)
        if track is None:
            return False

        if not force and track.musicbrainz_enriched_at is not None:
            logger.debug("Track %s already enriched; skipping", track_id)
            return False

        recording = await self._find_best_recording(track, artist, album)
        if recording is None:
            self._mark_enriched(track)
            await session.commit()
            return False

        recording_id = recording.get("id")
        if not recording_id:
            self._mark_enriched(track)
            await session.commit()
            return False

        details = await self._fetch_recording_details(recording_id, recording)
        release = self._best_release(recording, details, album)
        await self._apply_metadata(
            session,
            track,
            artist,
            album,
            recording,
            details,
            release,
            storage_service,
        )

        self._mark_enriched(track)
        await session.commit()
        return True

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
            self._mark_image_enriched(artist)
            return False

        image_url = await self._fetch_artist_image_url(artist.musicbrainz_id)
        if not image_url:
            self._mark_image_enriched(artist)
            return False

        if storage_service is None:
            artist.image_url = image_url
            self._mark_image_enriched(artist)
            return True

        data = await self._download_image(image_url)
        if data:
            stored = await self._store_image_file(
                session,
                artist,
                "image_file_id",
                data,
                storage_service,
                owner_id=owner_id,
                visibility=visibility,
            )
            if stored is not None:
                artist.image_url = None
                self._mark_image_enriched(artist)
                return True

        artist.image_url = image_url
        self._mark_image_enriched(artist)
        return True

    async def _maybe_store_cover_art_for_image_enrichment(
        self,
        session,
        album: Album,
        storage_service: StorageService,
        force: bool = False,
    ) -> bool:
        """Store album cover art when missing and a release ID is known."""
        if album.cover_file_id is not None and not force:
            return False
        if not force and album.cover_enriched_at is not None:
            return False
        if not album.musicbrainz_id:
            self._mark_cover_enriched(album)
            return False

        await self._store_cover_art(
            session,
            album,
            album.musicbrainz_id,
            storage_service,
            owner_id=album.owner_id,
        )
        self._mark_cover_enriched(album)
        return album.cover_file_id is not None

    @staticmethod
    def _mark_image_enriched(artist: Artist) -> None:
        """Mark an artist as having been processed for images."""
        artist.image_enriched_at = datetime.datetime.now(datetime.timezone.utc)

    @staticmethod
    def _mark_cover_enriched(album: Album) -> None:
        """Mark an album as having been processed for cover art."""
        album.cover_enriched_at = datetime.datetime.now(datetime.timezone.utc)

    async def _load_track_context(
        self,
        session,
        track_id: str,
    ) -> tuple[Optional[Track], Optional[Artist], Optional[Album]]:
        """Fetch the track and its associated artist and album."""
        result = await session.execute(
            select(Track)
            .options(
                selectinload(Track.artist),
                selectinload(Track.album).selectinload(Album.artist),
                selectinload(Track.audio_file),
            )
            .where(Track.id == track_id)
        )
        track = cast(Optional[Track], result.scalar_one_or_none())
        if track is None:
            logger.warning("Track %s not found for MusicBrainz enrichment", track_id)
            return None, None, None

        return track, track.artist, track.album

    async def _find_best_recording(
        self,
        track: Track,
        artist: Optional[Artist],
        album: Optional[Album],
    ) -> Optional[Dict[str, Any]]:
        """Search MusicBrainz and return the best matching recording."""
        results = await self.search_recordings(
            artist=artist.name if artist else None,
            title=track.title,
            release=album.title if album else None,
            limit=5,
        )
        recordings = results.get("recording-list", [])
        return recordings[0] if recordings else None

    async def _fetch_recording_details(
        self,
        recording_id: str,
        recording: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Fetch recording details, falling back to the search result on error."""
        try:
            return await self.fetch_recording(recording_id, include_releases=True)
        except Exception as exc:
            logger.debug("Could not fetch recording %s: %s", recording_id, exc)
            return {"recording": recording}

    def _best_release(
        self,
        recording: Dict[str, Any],
        details: Dict[str, Any],
        album: Optional[Album],
    ) -> Optional[Dict[str, Any]]:
        """Return the most relevant release for a recording."""
        rec = details.get("recording") if details else None
        if not rec:
            rec = recording

        releases: List[Dict[str, Any]] = rec.get("release-list") or rec.get("releases") or []
        if not releases:
            releases = recording.get("release-list") or recording.get("releases") or []
        if not releases:
            return None

        if album and album.title:
            for release in releases:
                title = release.get("title")
                if title and title.lower() == album.title.lower():
                    return release

        return releases[0]

    async def _apply_metadata(
        self,
        session,
        track: Track,
        artist: Optional[Artist],
        album: Optional[Album],
        recording: Dict[str, Any],
        details: Dict[str, Any],
        release: Optional[Dict[str, Any]],
        storage_service: Optional[StorageService],
    ) -> None:
        """Populate missing metadata from a MusicBrainz recording and release."""
        recording_id = recording.get("id")
        if recording_id and track.musicbrainz_id is None:
            track.musicbrainz_id = recording_id

        recording_title = _recording_title(recording)
        if recording_title and _is_missing_title(track):
            track.title = recording_title

        recording_duration = _recording_duration(recording)
        if recording_duration is not None and track.duration is None:
            track.duration = recording_duration

        new_artist: Optional[Artist] = None
        artist_name, artist_mbid = _recording_artist(recording)
        if artist_name and _is_missing_artist(artist):
            new_artist = await self._find_or_create_artist(session, artist_name, artist_mbid)
            track.artist_id = str(new_artist.id)
        elif (
            artist
            and artist_mbid
            and artist.musicbrainz_id is None
            and (_is_missing_artist(artist) or _normalize_name(artist.name) == _normalize_name(artist_name or ""))
        ):
            artist.musicbrainz_id = artist_mbid

        release_title = _release_title(release)
        release_year = _release_year(release)
        release_id = _release_id(release)

        track_number = _release_track_number(release)
        if track_number is not None and track.track_number is None:
            track.track_number = track_number

        disc_number = _release_disc_number(release)
        if disc_number is not None and track.disc_number is None:
            track.disc_number = disc_number

        target_artist_id = str(new_artist.id) if new_artist else track.artist_id
        if _is_missing_album(album) and release_title and target_artist_id:
            new_album = await self._find_or_create_album(
                session,
                title=release_title,
                artist_id=target_artist_id,
                mbid=release_id,
                year=release_year,
                owner_id=track.owner_id,
                visibility=track.visibility,
            )
            track.album_id = str(new_album.id)
            album = new_album
        elif album and release_title:
            if _is_missing_album(album) and release_title:
                album.title = release_title
            if release_id and album.musicbrainz_id is None:
                album.musicbrainz_id = release_id
            if release_year and album.release_year is None:
                album.release_year = release_year
            if _is_missing_artist(album.artist) and target_artist_id:
                album.artist_id = target_artist_id

        if storage_service and album and release_id:
            await self._maybe_store_cover_art(
                session,
                album,
                release_id,
                storage_service,
                owner_id=track.owner_id,
            )

        self._store_raw_metadata(track, recording, details)

    async def _find_or_create_artist(
        self,
        session,
        name: str,
        mbid: Optional[str] = None,
    ) -> Artist:
        """Return an existing artist by MBID or name, or create one."""
        if mbid:
            result = await session.execute(select(Artist).where(Artist.musicbrainz_id == mbid).limit(1))
            artist = cast(Optional[Artist], result.scalar_one_or_none())
            if artist:
                return artist

        result = await session.execute(select(Artist).where(func.lower(Artist.name) == name.lower()).limit(1))
        artist = cast(Optional[Artist], result.scalar_one_or_none())
        if artist:
            return artist

        artist = Artist(name=name, musicbrainz_id=mbid)
        try:
            async with session.begin_nested():
                session.add(artist)
                await session.flush([artist])
        except IntegrityError as exc:
            if not is_unique_constraint_error(exc):
                raise

            if mbid:
                result = await session.execute(select(Artist).where(Artist.musicbrainz_id == mbid).limit(1))
                artist = cast(Optional[Artist], result.scalar_one_or_none())
                if artist:
                    return artist

            result = await session.execute(select(Artist).where(func.lower(Artist.name) == name.lower()).limit(1))
            artist = cast(Optional[Artist], result.scalar_one_or_none())
            if artist:
                return artist

            raise

        return artist

    async def _find_or_create_album(
        self,
        session,
        title: str,
        artist_id: str,
        mbid: Optional[str] = None,
        year: Optional[int] = None,
        owner_id: Optional[str] = None,
        visibility: Optional[str] = None,
    ) -> Album:
        """Return an existing album by MBID or title/artist, or create one."""
        if mbid:
            result = await session.execute(select(Album).where(Album.musicbrainz_id == mbid).limit(1))
            album = cast(Optional[Album], result.scalar_one_or_none())
            if album:
                return album

        result = await session.execute(
            select(Album)
            .where(
                func.lower(Album.title) == title.lower(),
                Album.artist_id == artist_id,
            )
            .limit(1)
        )
        album = cast(Optional[Album], result.scalar_one_or_none())
        if album:
            return album

        album = Album(
            title=title,
            artist_id=artist_id,
            musicbrainz_id=mbid,
            release_year=year,
            owner_id=owner_id,
            visibility=visibility or "private",
        )
        try:
            async with session.begin_nested():
                session.add(album)
                await session.flush([album])
        except IntegrityError as exc:
            if not is_unique_constraint_error(exc):
                raise

            if mbid:
                result = await session.execute(select(Album).where(Album.musicbrainz_id == mbid).limit(1))
                album = cast(Optional[Album], result.scalar_one_or_none())
                if album:
                    return album

            result = await session.execute(
                select(Album)
                .where(
                    func.lower(Album.title) == title.lower(),
                    Album.artist_id == artist_id,
                )
                .limit(1)
            )
            album = cast(Optional[Album], result.scalar_one_or_none())
            if album:
                return album

            raise

        return album

    async def _maybe_store_cover_art(
        self,
        session,
        album: Album,
        release_id: str,
        storage_service: StorageService,
        owner_id: Optional[str] = None,
    ) -> None:
        """Store cover art for an album when it is missing and a release ID is known."""
        if album.cover_file_id is not None:
            return

        await self._store_cover_art(
            session,
            album,
            release_id,
            storage_service,
            owner_id=owner_id,
        )

    @staticmethod
    def _store_raw_metadata(
        track: Track,
        recording: Dict[str, Any],
        details: Dict[str, Any],
    ) -> None:
        """Store the MusicBrainz recording details in the track's raw metadata."""
        raw = track.raw_metadata or {}
        raw["mb_recording"] = details.get("recording", recording)
        track.raw_metadata = raw

    async def _fetch_artist_image_url(self, artist_id: str) -> Optional[str]:
        """Return a candidate image URL for an artist from MusicBrainz URL relations."""
        try:
            result = await self.fetch_artist(artist_id, include_url_rels=True)
        except Exception as exc:
            logger.debug("Could not fetch artist %s: %s", artist_id, exc)
            return None

        artist = result.get("artist") if isinstance(result, dict) else None
        if not isinstance(artist, dict):
            return None

        relations = (
            artist.get("url-relation-list")
            or artist.get("url-relations")
            or artist.get("relation-list")
            or artist.get("relations")
            or []
        )
        if not isinstance(relations, list):
            return None

        for relation in relations:
            if not isinstance(relation, dict):
                continue
            if not _is_image_relation(relation):
                continue
            url = relation.get("url", {})
            if isinstance(url, dict):
                target = url.get("resource") or url.get("id")
                if isinstance(target, str) and target.startswith("http"):
                    resolved = await self._resolve_image_url(target)
                    if resolved:
                        return resolved
            target = relation.get("target")
            if isinstance(target, str) and target.startswith("http"):
                resolved = await self._resolve_image_url(target)
                if resolved:
                    return resolved

        return None

    async def _resolve_image_url(self, url: str) -> Optional[str]:
        """Resolve a relation URL to a direct image URL."""
        parsed = urllib.parse.urlparse(url)
        if parsed.netloc.endswith("commons.wikimedia.org"):
            return await self._wikimedia_file_url(url)
        if _looks_like_image_url(url):
            return url
        return None

    async def _wikimedia_file_url(self, url: str) -> Optional[str]:
        """Resolve a Wikimedia Commons file page to a direct image URL."""
        parsed = urllib.parse.urlparse(url)
        path = parsed.path or ""
        if path.startswith("/wiki/Category:") or path.startswith("/wiki/File:"):
            title = path.split(":", 1)[1]
            title = urllib.parse.unquote(title)
            title = title.replace(" ", "_")
            api_url = (
                "https://commons.wikimedia.org/w/api.php"
                f"?action=query&titles=File:{urllib.parse.quote(title)}"
                "&prop=imageinfo&iiprop=url|mime&format=json&origin=*"
            )
            try:
                response = await self._client.get(api_url)
            except Exception as exc:
                logger.debug("Wikimedia API request failed for %s: %s", url, exc)
                return None

            if response.status_code != 200:
                return None

            try:
                payload = response.json()
            except Exception:
                return None

            pages = payload.get("query", {}).get("pages", {})
            for page in pages.values():
                if not isinstance(page, dict):
                    continue
                for info in page.get("imageinfo", []):
                    if isinstance(info, dict):
                        image_url = info.get("url")
                        if isinstance(image_url, str) and image_url.startswith("http"):
                            return image_url

        return None

    async def _download_image(self, url: str) -> Optional[bytes]:
        """Download an image from a URL, following redirects."""
        for _ in range(_MAX_COVER_ART_REDIRECTS):
            try:
                response = await self._client.get(url, follow_redirects=False)
            except Exception as exc:
                logger.debug("Image download failed for %s: %s", url, exc)
                return None

            if 200 <= response.status_code < 300:
                data = response.content
                if isinstance(data, bytes) and data and _is_valid_image(data):
                    return data
                return None

            if 300 <= response.status_code < 400:
                location = response.headers.get("location")
                if not location:
                    return None
                url = str(location)
                continue

            return None

        logger.debug("Too many redirects for image %s", url)
        return None

    async def _store_image_file(
        self,
        session,
        entity,
        field_name: str,
        data: bytes,
        storage_service: StorageService,
        owner_id: Optional[str] = None,
        visibility: Optional[str] = None,
    ) -> Optional[StoredFile]:
        """Store an image for an entity and assign its StoredFile."""
        content_type = _guess_image_mime(data)
        buffer = io.BytesIO(data)

        stored, _ = await storage_service.store_file(
            session,
            buffer,
            content_type,
            prefix="images",
            owner_id=owner_id,
            visibility=visibility or "private",
            return_duplicate=True,
        )
        if stored is None:
            return None

        setattr(entity, field_name, str(stored.id))
        relationship = field_name.replace("_file_id", "_file")
        if hasattr(entity, relationship):
            setattr(entity, relationship, stored)
        return stored

    @staticmethod
    def _mark_enriched(track: Track) -> None:
        """Mark a track as having been processed by MusicBrainz."""
        track.musicbrainz_enriched_at = datetime.datetime.now(datetime.timezone.utc)


def _recording_title(recording: Dict[str, Any]) -> Optional[str]:
    """Return a recording title, if present."""
    title = recording.get("title")
    return title if isinstance(title, str) and title.strip() else None


def _recording_duration(recording: Dict[str, Any]) -> Optional[float]:
    """Return a recording duration in seconds, if present."""
    length = recording.get("length")
    if length is None:
        return None
    try:
        return float(length) / 1000.0
    except (TypeError, ValueError):
        return None


def _recording_artist(recording: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    """Return the artist name and MusicBrainz ID from a recording."""
    phrase = recording.get("artist-credit-phrase")
    if isinstance(phrase, str) and phrase.strip():
        return phrase.strip(), _first_artist_id(recording)

    credit = recording.get("artist-credit")
    if not isinstance(credit, list):
        return None, None

    parts: List[str] = []
    mbid: Optional[str] = None
    for entry in credit:
        if not isinstance(entry, dict):
            continue
        if mbid is None:
            artist = entry.get("artist", {})
            if isinstance(artist, dict):
                mbid = artist.get("id")
        name = entry.get("name")
        if not name and isinstance(entry.get("artist"), dict):
            name = entry["artist"].get("name")
        if name:
            parts.append(str(name))
            join = entry.get("joinphrase")
            if join:
                parts.append(str(join))

    return ("".join(parts).strip() or None), mbid


def _first_artist_id(recording: Dict[str, Any]) -> Optional[str]:
    """Return the first artist ID from a recording search result."""
    credit = recording.get("artist-credit", [])
    if credit and isinstance(credit, list):
        first = credit[0]
        if isinstance(first, dict):
            artist = first.get("artist", {})
            if isinstance(artist, dict):
                return artist.get("id")
    return None


def _release_title(release: Optional[Dict[str, Any]]) -> Optional[str]:
    """Return a release title, if present."""
    if not release:
        return None
    title = release.get("title")
    return title if isinstance(title, str) and title.strip() else None


def _release_id(release: Optional[Dict[str, Any]]) -> Optional[str]:
    """Return a release MusicBrainz ID, if present."""
    if not release:
        return None
    return release.get("id")


def _release_year(release: Optional[Dict[str, Any]]) -> Optional[int]:
    """Return the release year, if present."""
    if not release:
        return None

    date = release.get("date") or release.get("first-release-date")
    if not date and isinstance(release.get("release-events"), list):
        events = release["release-events"]
        if events:
            date = events[0].get("date")

    if not date:
        return None

    match = re.search(r"\d{4}", str(date))
    return int(match.group(0)) if match else None


def _release_track_number(release: Optional[Dict[str, Any]]) -> Optional[int]:
    """Return the track number on the first medium, if present and numeric."""
    if not release:
        return None

    media_list = release.get("medium-list") or release.get("media") or []
    for medium in media_list:
        tracks = medium.get("track-list") or medium.get("track") or []
        if not isinstance(tracks, list):
            tracks = [tracks]
        for track in tracks:
            number = track.get("number")
            if number is None:
                continue
            match = re.match(r"(\d+)", str(number))
            if match:
                return int(match.group(1))

    return None


def _release_disc_number(release: Optional[Dict[str, Any]]) -> Optional[int]:
    """Return the medium/disc number, if present."""
    if not release:
        return None

    media_list = release.get("medium-list") or release.get("media") or []
    for medium in media_list:
        tracks = medium.get("track-list") or medium.get("track") or []
        if not isinstance(tracks, list):
            tracks = [tracks]
        if tracks:
            position = medium.get("position")
            if position is not None:
                try:
                    return int(position)
                except (TypeError, ValueError):
                    pass
            return 1

    return None


def _is_missing_title(track: Track) -> bool:
    """Return ``True`` when the track title is missing or derived from the filename."""
    if not track.title or not track.title.strip():
        return True

    if track.audio_file and track.audio_file.original_filename:
        return track.title == Path(track.audio_file.original_filename).stem

    return False


def _is_missing_artist(artist: Optional[Artist]) -> bool:
    """Return ``True`` when the artist name is missing or a generic placeholder."""
    if artist is None:
        return True
    name = (artist.name or "").strip()
    return not name or name == "Unknown Artist"


def _is_missing_album(album: Optional[Album]) -> bool:
    """Return ``True`` when the album title is missing or a generic placeholder."""
    if album is None:
        return True
    title = (album.title or "").strip()
    return not title or title == "Unknown Album"


def _normalize_name(name: Optional[str]) -> str:
    """Return a lowercased, stripped name for comparison."""
    return (name or "").strip().lower()


def _guess_image_mime(data: bytes) -> str:
    """Guess an image MIME type from the first few bytes."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
        return "image/gif"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"


_IMAGE_URL_RE = re.compile(r"\.(?:png|jpe?g|gif|webp)(?:\?|#|$)", re.IGNORECASE)


def _looks_like_image_url(url: str) -> bool:
    """Return ``True`` when a URL looks like a direct image link."""
    return _IMAGE_URL_RE.search(url) is not None


def _is_valid_image(data: bytes) -> bool:
    """Return ``True`` when the bytes start with a known image signature."""
    if not data:
        return False
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return True
    if data.startswith(b"\xff\xd8"):
        return True
    if data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
        return True
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return True
    return False


def _is_image_relation(relation: Dict[str, Any]) -> bool:
    """Return ``True`` when a MusicBrainz URL relation may point to an image."""
    relation_type = relation.get("type") or ""
    if not isinstance(relation_type, str):
        return False
    relation_type = relation_type.lower()
    if relation_type in {"image", "logo", "wikimedia"}:
        return True
    if "image" in relation_type or "photo" in relation_type or "picture" in relation_type:
        return True
    return False
