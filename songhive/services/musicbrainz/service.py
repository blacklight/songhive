"""MusicBrainz enrichment service."""

import asyncio
import io
import logging
import time
from typing import TYPE_CHECKING, Any, Dict, List, Optional, cast

import httpx
import musicbrainzngs

from ...config.schema import MusicBrainzConfig
from ...models.album import Album
from .artist_image import _ArtistImageResolution
from .enrichment import _TrackEnrichment
from .images import _ImageEnrichment
from .metadata import (
    _escape_query_term,
    _guess_image_mime,
    _ranked_cover_art_image_urls,
)

if TYPE_CHECKING:
    from ...services.storage import StorageService

logger = logging.getLogger(__name__)


class MusicBrainzService(_TrackEnrichment, _ImageEnrichment, _ArtistImageResolution):
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

    async def search_releases(
        self,
        *,
        query: Optional[str] = None,
        artist: Optional[str] = None,
        release: Optional[str] = None,
        limit: int = 10,
    ) -> Dict[str, Any]:
        """Search for releases using a free-form or structured query."""
        if query is None:
            parts = []
            if artist:
                parts.append(f"artist:{_escape_query_term(artist)}")
            if release:
                parts.append(f"release:{_escape_query_term(release)}")
            query = " ".join(parts)

        if not query:
            return {}

        return cast(
            Dict[str, Any],
            await self._call(
                musicbrainzngs.search_releases,
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

    async def fetch_cover_image(
        self,
        release_id: str,
        release_group_id: Optional[str] = None,
    ) -> Optional[bytes]:
        """
        Download the best-ranked front cover image bytes for a release.

        The Cover Art Archive listing for the release is preferred so that
        approved front images win over pending or unapproved ones. When no
        usable front image is found for the release itself, the release
        group's front image is tried as a last resort — it resolves to the
        canonical cover of a sibling release in the same group.
        """
        if not self.config.fetch_cover_art:
            return None

        urls: List[str] = []
        listing = await self._fetch_cover_art_listing(release_id)
        if listing is not None:
            urls.extend(_ranked_cover_art_image_urls(listing))
        if not urls:
            urls.append(f"https://coverartarchive.org/release/{release_id}/front")
        if release_group_id:
            urls.append(f"https://coverartarchive.org/release-group/{release_group_id}/front")

        for url in urls:
            data = await self._download_image(url)
            if data:
                return data
        return None

    async def _fetch_cover_art_listing(self, release_id: str) -> Optional[Dict[str, Any]]:
        """Return the Cover Art Archive image listing for a release, if any."""
        try:
            response = await self._client.get(
                f"https://coverartarchive.org/release/{release_id}",
                follow_redirects=True,
            )
        except Exception as exc:
            logger.debug("Cover art listing request failed for %s: %s", release_id, exc)
            return None

        if response.status_code != 200:
            return None

        try:
            payload = response.json()
        except Exception:
            return None
        return payload if isinstance(payload, dict) else None

    async def _store_cover_art(
        self,
        session,
        album: Album,
        release_id: str,
        storage_service: StorageService,
        owner_id: Optional[str] = None,
        release_group_id: Optional[str] = None,
    ) -> None:
        """Fetch and store cover art for an album when missing."""
        if album.cover_file_id is not None:
            return

        data = await self.fetch_cover_image(release_id, release_group_id)
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
