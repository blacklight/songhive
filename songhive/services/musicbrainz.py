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

# Number of ranked MusicBrainz releases tried when the release linked to an
# album yields no cover art.
_MAX_COVER_RELEASE_CANDIDATES = 3

# MusicBrainz release statuses that describe unofficial or promotional
# products. A missing status is treated as neutral; unknown statuses rank
# between the two.
_RELEASE_STATUS_SCORE = {
    "official": 3,
    "promotion": -1,
    "bootleg": -3,
    "pseudo-release": -3,
}

# Release-group secondary types marking a derivative product rather than the
# canonical release (live versions, compilations, remixes, ...).
_NON_CANONICAL_SECONDARY_TYPES = {
    "live",
    "compilation",
    "remix",
    "dj-mix",
    "mixtape/street",
    "demo",
    "spokenword",
    "interview",
    "audiobook",
    "audio drama",
    "field recording",
}

# Disambiguation comments that strongly suggest the release is not the
# canonical edition.
_NON_CANONICAL_DISAMBIGUATION = ("bootleg", "unofficial", "promo")

# Disambiguation comments marking a special edition; the standard edition is
# preferred because it carries the canonical cover art.
_SPECIAL_EDITION_DISAMBIGUATION = (
    "limited",
    "deluxe",
    "collector",
    "box set",
    "fan club",
    "special edition",
    "expanded",
    "reissue",
    "remaster",
    "anniversary",
)

# Release countries preferred on ties: worldwide digital releases and the
# major physical markets tend to carry the canonical cover.
_PREFERRED_RELEASE_COUNTRIES = {"xe", "us", "gb", "jp", "de", "fr", "ca", "au"}


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
        """
        Return the most canonical release for a recording.

        Releases are ranked by ``_release_rank``: a title match comes first,
        then unofficial statuses (bootleg, promotion), special-edition
        disambiguations and secondary release types are penalized, and
        releases with front artwork, an album primary type and the earliest
        release date are preferred.
        """
        rec = details.get("recording") if details else None
        if not rec:
            rec = recording

        releases: List[Dict[str, Any]] = rec.get("release-list") or rec.get("releases") or []
        if not releases:
            releases = recording.get("release-list") or recording.get("releases") or []
        if not releases:
            return None

        album_title = album.title if album is not None and not _is_missing_album(album) else None
        return max(releases, key=lambda release: _release_rank(release, album_title))

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
                release_group_id=_release_group_id(release),
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
        release_group_id: Optional[str] = None,
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
            release_group_id=release_group_id,
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

    async def _find_artist_image_url(self, artist_id: str) -> Optional[str]:
        """Return a candidate image URL for an artist from any available source."""
        try:
            result = await self.fetch_artist(artist_id, include_url_rels=True)
        except Exception as exc:
            logger.debug("Could not fetch artist %s: %s", artist_id, exc)
            return None

        artist = result.get("artist") if isinstance(result, dict) else None
        if not isinstance(artist, dict):
            return None

        image_url = await self._resolve_artist_image_relations(artist)
        if image_url:
            return image_url

        image_url = await self._resolve_wikidata_image(artist)
        if image_url:
            return image_url

        return None

    async def _resolve_artist_image_relations(self, artist: Dict[str, Any]) -> Optional[str]:
        """Resolve image-like URL relations from an already-fetched artist."""
        relations = (
            artist.get("url-relation-list")
            or artist.get("url-relations")
            or artist.get("relation-list")
            or artist.get("relations")
            or []
        )
        if not isinstance(relations, list):
            return None

        preferred: List[str] = []
        deferred: List[str] = []
        for relation in relations:
            if not isinstance(relation, dict):
                continue
            if not _is_image_relation(relation):
                continue
            # Logos are less useful as artist images than photos, so they are
            # only tried after every other image-like relation.
            targets = deferred if _is_logo_relation(relation) else preferred
            url = relation.get("url", {})
            if isinstance(url, dict):
                target = url.get("resource") or url.get("id")
                if isinstance(target, str) and target.startswith("http"):
                    targets.append(target)
            target = relation.get("target")
            if isinstance(target, str) and target.startswith("http"):
                targets.append(target)

        for target in preferred + deferred:
            resolved = await self._resolve_image_url(target)
            if resolved:
                return resolved

        return None

    async def _resolve_wikidata_image(self, artist: Dict[str, Any]) -> Optional[str]:
        """Resolve an image from a Wikidata P18 claim linked to the artist."""
        relations = (
            artist.get("url-relation-list")
            or artist.get("url-relations")
            or artist.get("relation-list")
            or artist.get("relations")
            or []
        )
        if not isinstance(relations, list):
            return None

        wikidata_id: Optional[str] = None
        for relation in relations:
            if not isinstance(relation, dict):
                continue
            relation_type = relation.get("type") or ""
            if not isinstance(relation_type, str) or "wikidata" not in relation_type.lower():
                continue
            target = relation.get("target")
            if isinstance(target, str) and target.startswith("https://www.wikidata.org/wiki/"):
                wikidata_id = target.rsplit("/", 1)[-1]
                if wikidata_id and wikidata_id.startswith("Q"):
                    break
            url = relation.get("url", {})
            if isinstance(url, dict):
                resource = url.get("resource") or url.get("id")
                if isinstance(resource, str) and resource.startswith("https://www.wikidata.org/wiki/"):
                    wikidata_id = resource.rsplit("/", 1)[-1]
                    if wikidata_id and wikidata_id.startswith("Q"):
                        break

        if not wikidata_id:
            return None

        try:
            response = await self._client.get(
                f"https://www.wikidata.org/wiki/Special:EntityData/{wikidata_id}.json",
                follow_redirects=True,
            )
        except Exception as exc:
            logger.debug("Wikidata request failed for %s: %s", wikidata_id, exc)
            return None

        if response.status_code != 200:
            return None

        try:
            payload = response.json()
        except Exception:
            return None

        entity = payload.get("entities", {}).get(wikidata_id, {})
        claims = entity.get("claims", {})
        p18 = claims.get("P18", [])
        if not isinstance(p18, list):
            return None

        for claim in p18:
            if not isinstance(claim, dict):
                continue
            datavalue = claim.get("mainsnak", {}).get("datavalue", {})
            filename = datavalue.get("value")
            if not isinstance(filename, str):
                continue
            filename = filename.replace(" ", "_")
            commons_url = f"https://commons.wikimedia.org/wiki/File:{urllib.parse.quote(filename)}"
            resolved = await self._wikimedia_file_url(commons_url)
            if resolved:
                return resolved

        return None

    async def _resolve_image_url(self, url: str) -> Optional[str]:
        """Resolve a relation URL to a direct image URL."""
        parsed = urllib.parse.urlparse(url)

        # web.archive.org saved snapshots: extract the archived URL
        if parsed.netloc.endswith("web.archive.org") and "/web/" in (parsed.path or ""):
            parts = (parsed.path or "").split("/")
            if len(parts) >= 4 and parts[1] == "web":
                archived_url = "/".join(parts[3:])
                if archived_url.startswith("http"):
                    return await self._resolve_image_url(archived_url)

        if parsed.netloc.endswith("commons.wikimedia.org"):
            return await self._wikimedia_file_url(url)

        if "scdn.co" in parsed.netloc or parsed.netloc.endswith("mzstatic.com"):
            return url

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


def _release_group_id(release: Optional[Dict[str, Any]]) -> Optional[str]:
    """Return the release-group MusicBrainz ID for a release, if present."""
    if not release:
        return None
    group = release.get("release-group")
    if isinstance(group, dict):
        group_id = group.get("id")
        if isinstance(group_id, str) and group_id:
            return group_id
    return None


def _release_rank(release: Dict[str, Any], album_title: Optional[str]) -> Tuple[int, ...]:
    """
    Return a ranking tuple for a release; higher ranks are more canonical.

    Title matches come first, then unofficial statuses (bootleg, promotion),
    special-edition disambiguation comments and secondary release-group types
    are penalized, and releases whose group is a plain album, which report
    front artwork and which have the earliest release date are preferred —
    i.e. the original press of an album rather than a bootleg, promo or
    limited reissue.
    """
    return (
        _release_title_score(release, album_title),
        _release_status_score(release),
        _release_disambiguation_score(release),
        _release_primary_type_score(release),
        _release_secondary_type_score(release),
        _release_cover_art_score(release),
        -_release_date_value(release),
        _release_country_score(release),
    )


def _release_title_score(release: Dict[str, Any], album_title: Optional[str]) -> int:
    """Score how closely a release title matches the local album title."""
    title = _release_title(release)
    if not title or not album_title:
        return 0
    if title.strip().lower() == album_title.strip().lower():
        return 2
    normalized_title = _normalize_release_title(title)
    if normalized_title and normalized_title == _normalize_release_title(album_title):
        return 1
    return 0


_NORMALIZED_TITLE_STRIP_RE = re.compile(r"[(\[][^)\]]*[)\]]")
_NORMALIZED_TITLE_CHARS_RE = re.compile(r"[^a-z0-9]+")


def _normalize_release_title(title: str) -> str:
    """Normalize a title for fuzzy release/album comparison."""
    normalized = _NORMALIZED_TITLE_STRIP_RE.sub(" ", title.lower())
    return _NORMALIZED_TITLE_CHARS_RE.sub(" ", normalized).strip()


def _release_status_score(release: Dict[str, Any]) -> int:
    """Score a release status; unofficial products rank below official ones."""
    status = release.get("status")
    if not isinstance(status, str) or not status.strip():
        return 1
    return _RELEASE_STATUS_SCORE.get(status.strip().lower(), 0)


def _release_disambiguation_score(release: Dict[str, Any]) -> int:
    """Penalize releases whose disambiguation marks a non-canonical edition."""
    parts = [release.get("disambiguation")]
    group = release.get("release-group")
    if isinstance(group, dict):
        parts.append(group.get("disambiguation"))
    text = " ".join(p.lower() for p in parts if isinstance(p, str) and p)
    if not text:
        return 0
    if any(marker in text for marker in _NON_CANONICAL_DISAMBIGUATION):
        return -3
    if any(marker in text for marker in _SPECIAL_EDITION_DISAMBIGUATION):
        return -1
    return 0


def _release_primary_type_score(release: Dict[str, Any]) -> int:
    """Prefer releases whose group is a plain album over singles, EPs, etc."""
    group = release.get("release-group")
    if not isinstance(group, dict):
        return 1
    primary = group.get("primary-type") or group.get("type")
    if not isinstance(primary, str) or not primary.strip():
        return 1
    return 2 if primary.strip().lower() == "album" else 0


def _release_secondary_type_score(release: Dict[str, Any]) -> int:
    """Penalize releases whose group is a live, compilation or remix variant."""
    group = release.get("release-group")
    if not isinstance(group, dict):
        return 0
    secondary = group.get("secondary-type-list") or group.get("secondary-types") or []
    types = {str(entry).strip().lower() for entry in secondary if entry}
    return -2 if types & _NON_CANONICAL_SECONDARY_TYPES else 0


def _release_cover_art_score(release: Dict[str, Any]) -> int:
    """Prefer releases the Cover Art Archive reports artwork for."""
    archive = release.get("cover-art-archive") or release.get("cover_art_archive")
    if not isinstance(archive, dict):
        return 0
    for key in ("front", "artwork"):
        if str(archive.get(key)).strip().lower() in {"true", "1"}:
            return 1
    count = archive.get("count")
    try:
        if count is not None and int(count) > 0:
            return 1
    except (TypeError, ValueError):
        pass
    return -1


def _release_date_value(release: Dict[str, Any]) -> int:
    """Return a sortable ordinal for the release date; missing dates sort last."""
    date = release.get("date")
    events = release.get("release-events")
    if not date and isinstance(events, list):
        for event in events:
            if isinstance(event, dict) and event.get("date"):
                date = event["date"]
                break
    if not date:
        group = release.get("release-group")
        if isinstance(group, dict):
            date = group.get("first-release-date")

    match = re.match(r"(\d{4})(?:-(\d{1,2}))?(?:-(\d{1,2}))?", str(date or ""))
    if not match:
        return 99999999
    year = int(match.group(1))
    month = int(match.group(2) or 0)
    day = int(match.group(3) or 0)
    return year * 10000 + month * 100 + day


def _release_country_score(release: Dict[str, Any]) -> int:
    """Prefer worldwide and major-market releases on ties."""
    codes = set()
    country = release.get("country")
    if isinstance(country, str) and country.strip():
        codes.add(country.strip().lower())
    events = release.get("release-events")
    if isinstance(events, list):
        for event in events:
            if not isinstance(event, dict):
                continue
            area = event.get("area")
            if not isinstance(area, dict):
                continue
            for code in area.get("iso-3166-1-code-list") or []:
                if isinstance(code, str):
                    codes.add(code.lower())
    if "xw" in codes:
        return 2
    if codes & _PREFERRED_RELEASE_COUNTRIES:
        return 1
    return 0


def _ranked_cover_art_image_urls(payload: Dict[str, Any]) -> List[str]:
    """Return front-cover image URLs from a Cover Art Archive listing, best first."""
    images = payload.get("images")
    if not isinstance(images, list):
        return []

    fronts = [image for image in images if isinstance(image, dict) and _cover_art_is_front(image)]
    # Approved edits first; the archive's own ordering is preserved otherwise.
    fronts.sort(key=lambda image: image.get("approved") is not True)

    urls = []
    for image in fronts:
        url = image.get("image")
        if isinstance(url, str) and url.startswith("http"):
            urls.append(url)
    return urls


def _cover_art_is_front(image: Dict[str, Any]) -> bool:
    """Return ``True`` when a Cover Art Archive image is marked as a front."""
    if image.get("front") is True:
        return True
    types = image.get("types")
    if isinstance(types, list):
        return any(str(entry).strip().lower() == "front" for entry in types)
    return False


def _is_logo_relation(relation: Dict[str, Any]) -> bool:
    """Return ``True`` when a URL relation points to a logo rather than a photo."""
    return str(relation.get("type") or "").strip().lower() == "logo"


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
