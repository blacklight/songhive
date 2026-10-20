"""Track metadata enrichment — part of the ``musicbrainz`` service."""

import datetime
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, cast

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from ...models.album import Album
from ...models.artist import Artist
from ...models.track import Track
from ...storage.exc import is_unique_constraint_error

if TYPE_CHECKING:
    from ...config.schema import MusicBrainzConfig
    from ...services.storage import StorageService

import logging

from .metadata import (
    _is_missing_album,
    _is_missing_artist,
    _is_missing_title,
    _normalize_name,
    _recording_artist,
    _recording_duration,
    _recording_title,
    _release_disc_number,
    _release_group_id,
    _release_id,
    _release_rank,
    _release_title,
    _release_track_number,
    _release_year,
)

logger = logging.getLogger(__name__)


class _TrackEnrichment:
    if TYPE_CHECKING:
        # Provided by the host class (MusicBrainzService).
        config: MusicBrainzConfig
        search_recordings: Callable[..., Any]
        fetch_recording: Callable[..., Any]
        fetch_artist: Callable[..., Any]
        _store_cover_art: Callable[..., Any]
        _resolve_artist_image_relations: Callable[..., Any]
        _resolve_wikidata_image: Callable[..., Any]

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

        # Provider-managed tracks (e.g. TIDAL) declare ``editable_fields``;
        # their provider metadata is immutable, so enrichment may only set
        # the MusicBrainz recording id and raw metadata — never title,
        # artist, album, or numbering.
        from ..provider_catalog import editable_fields_for_entity

        provider_locked = await editable_fields_for_entity(session, "track", str(track.id)) is not None
        await self._apply_metadata(
            session,
            track,
            artist,
            album,
            recording,
            details,
            release,
            storage_service,
            provider_locked=provider_locked,
        )

        self._mark_enriched(track)
        await session.commit()
        return True

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
        provider_locked: bool = False,
    ) -> None:
        """Populate missing metadata from a MusicBrainz recording and release."""
        self._apply_recording_scalars(track, recording)
        if provider_locked:
            self._store_raw_metadata(track, recording, details)
            return

        new_artist = await self._apply_recording_artist(session, track, artist, recording)
        album = await self._apply_release_metadata(session, track, album, release, new_artist)

        release_id = _release_id(release)
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

    def _apply_recording_scalars(self, track: Track, recording: Dict[str, Any]) -> None:
        """Copy scalar recording fields onto the track."""
        recording_id = recording.get("id")
        if recording_id and track.musicbrainz_id is None:
            track.musicbrainz_id = recording_id

        recording_title = _recording_title(recording)
        if recording_title and _is_missing_title(track):
            track.title = recording_title

        recording_duration = _recording_duration(recording)
        if recording_duration is not None and track.duration is None:
            track.duration = recording_duration

    async def _apply_recording_artist(
        self,
        session,
        track: Track,
        artist: Optional[Artist],
        recording: Dict[str, Any],
    ) -> Optional[Artist]:
        """Resolve or update the track's artist from the recording."""
        new_artist: Optional[Artist] = None
        artist_name, artist_mbid = _recording_artist(recording)
        if artist_name and _is_missing_artist(artist):
            new_artist = await self._find_or_create_artist(session, artist_name, artist_mbid)
            track.artist_id = str(new_artist.id)
            from ..music import reconcile_primary_credit

            await reconcile_primary_credit(session, track)
        elif (
            artist
            and artist_mbid
            and artist.musicbrainz_id is None
            and (_is_missing_artist(artist) or _normalize_name(artist.name) == _normalize_name(artist_name or ""))
        ):
            artist.musicbrainz_id = artist_mbid
        return new_artist

    async def _apply_release_metadata(
        self,
        session,
        track: Track,
        album: Optional[Album],
        release: Optional[Dict[str, Any]],
        new_artist: Optional[Artist],
    ) -> Optional[Album]:
        """Copy release fields and resolve the track's album."""
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
            return new_album
        if album and release_title:
            if _is_missing_album(album) and release_title:
                album.title = release_title
            if release_id and album.musicbrainz_id is None:
                album.musicbrainz_id = release_id
            if release_year and album.release_year is None:
                album.release_year = release_year
            if _is_missing_artist(album.artist) and target_artist_id:
                album.artist_id = target_artist_id
                from ..music import reconcile_primary_credit

                await reconcile_primary_credit(session, album)
        return album

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

    @staticmethod
    def _mark_enriched(track: Track) -> None:
        """Mark a track as having been processed by MusicBrainz."""
        track.musicbrainz_enriched_at = datetime.datetime.now(datetime.timezone.utc)
