"""
MusicBrainz enrichment service.
"""

import asyncio
import io
import logging
import time
from typing import Any, Dict, Optional, cast

import httpx
import musicbrainzngs

from ..config.schema import MusicBrainzConfig
from ..models.album import Album
from ..models.artist import Artist
from ..models.track import Track
from ..services.storage import StorageService

logger = logging.getLogger(__name__)


class MusicBrainzService:
    """Async wrapper around the MusicBrainz / Cover Art Archive APIs."""

    def __init__(
        self,
        config: MusicBrainzConfig,
        client: Optional[httpx.AsyncClient] = None,
    ):
        self.config = config
        self._client = client or httpx.AsyncClient(timeout=30.0)
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
                parts.append(f"artist:{artist}")
            if title:
                parts.append(f"recording:{title}")
            if release:
                parts.append(f"release:{release}")
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
        if response.status_code == 307:
            return str(response.headers.get("location"))
        return None

    async def fetch_cover_image(self, release_id: str) -> Optional[bytes]:
        """Download the front cover image bytes for a release."""
        url = await self.fetch_release(release_id)
        if url is None:
            return None

        try:
            response = await self._client.get(url, follow_redirects=True)
            response.raise_for_status()
            return response.content
        except Exception as exc:
            logger.debug("Could not download cover art from %s: %s", url, exc)
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
    ) -> bool:
        """
        Enrich a track with MusicBrainz data and optionally cover art.

        :returns: ``True`` if the track was updated.
        """
        if not self.config.enabled:
            return False

        track = await session.get(Track, track_id)
        if track is None:
            logger.warning("Track %s not found for MusicBrainz enrichment", track_id)
            return False

        artist = await session.get(Artist, track.artist_id) if track.artist_id else None
        album = await session.get(Album, track.album_id) if track.album_id else None

        results = await self.search_recordings(
            artist=artist.name if artist else None,
            title=track.title,
            release=album.title if album else None,
            limit=5,
        )
        recordings = results.get("recording-list", [])
        if not recordings:
            return False

        recording = recordings[0]
        recording_id = recording.get("id")
        if not recording_id:
            return False

        try:
            details = await self.fetch_recording(recording_id, include_releases=True)
        except Exception as exc:
            logger.debug("Could not fetch recording %s: %s", recording_id, exc)
            details = {"recording": recording}

        if track.musicbrainz_id is None:
            track.musicbrainz_id = recording_id

        if track.artist_id and artist and artist.musicbrainz_id is None:
            artist_mbid = _first_artist_id(recording)
            if artist_mbid:
                artist.musicbrainz_id = artist_mbid

        release_id = _first_release_id(details)
        if album and album.musicbrainz_id is None and release_id:
            album.musicbrainz_id = release_id

        if storage_service and album and album.cover_file_id is None and release_id:
            await self._store_cover_art(
                session,
                album,
                release_id,
                storage_service,
                owner_id=track.owner_id,
            )

        raw = track.raw_metadata or {}
        raw["mb_recording"] = details.get("recording", recording)
        track.raw_metadata = raw

        await session.commit()
        return True


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


def _first_release_id(details: Dict[str, Any]) -> Optional[str]:
    """Return the first release ID from a recording detail response."""
    recording = details.get("recording", {})
    releases = recording.get("release-list", [])
    if releases:
        return releases[0].get("id")
    return None


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
