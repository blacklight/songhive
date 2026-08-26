"""
MusicBrainz enrichment tests.
"""

import datetime
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from songhive.config.schema import MusicBrainzConfig, StorageConfig
from songhive.models.album import Album
from songhive.models.artist import Artist
from songhive.models.track import Track
from songhive.services.musicbrainz import (
    MusicBrainzService,
    _first_artist_id,
    _first_release_id,
    _guess_image_mime,
)
from songhive.services.storage import StorageService
from songhive.storage import get_storage
from songhive.tasks.musicbrainz import enrich_track


@pytest.fixture
def local_storage_service(tmp_path):
    """Create a StorageService backed by a local temp directory."""
    config = StorageConfig(backend="local", local_path=tmp_path / "media")
    backend = get_storage(config)
    return StorageService(backend, config)


@pytest.fixture
def musicbrainz_config():
    """Return an enabled MusicBrainz configuration for testing."""
    return MusicBrainzConfig(enabled=True, rate_limit_per_second=100.0)


@pytest.fixture
def mock_client():
    """Return a mock httpx.AsyncClient that returns a PNG cover image."""
    client = AsyncMock()
    client.get = AsyncMock(
        return_value=Mock(
            status_code=200,
            url="https://coverartarchive.org/release/release-1/front",
            content=b"\x89PNG\r\n\x1a\nfake",
            headers={},
            raise_for_status=Mock(),
        )
    )
    return client


@pytest.fixture
def recording_result():
    """Return a minimal MusicBrainz recording search result."""
    return {
        "recording-list": [
            {
                "id": "recording-1",
                "title": "Test Song",
                "artist-credit-phrase": "Test Artist",
                "artist-credit": [
                    {
                        "artist": {
                            "id": "artist-mbid-1",
                            "name": "Test Artist",
                        }
                    }
                ],
            }
        ]
    }


@pytest.fixture
def recording_details():
    """Return a minimal MusicBrainz recording detail response."""
    return {
        "recording": {
            "id": "recording-1",
            "title": "Test Song",
            "release-list": [{"id": "release-1", "title": "Test Album"}],
        }
    }


@pytest.mark.asyncio
async def test_enrich_track_disabled_does_nothing(db_session):
    """When MusicBrainz is disabled, enrichment returns False."""
    service = MusicBrainzService(MusicBrainzConfig(enabled=False))
    result = await service.enrich_track(db_session, "00000000-0000-0000-0000-000000000000")
    assert result is False


@pytest.mark.asyncio
async def test_enrich_fills_missing_only(
    db_session,
    musicbrainz_config,
    mock_client,
    recording_result,
    recording_details,
    monkeypatch,
):
    """Enrichment only populates empty fields and never overwrites existing values."""
    artist = Artist(name="Existing Artist", musicbrainz_id="existing-artist-mbid")
    db_session.add(artist)
    await db_session.flush()

    album = Album(
        title="Existing Album",
        artist_id=artist.id,
        musicbrainz_id="existing-album-mbid",
    )
    db_session.add(album)
    await db_session.flush()

    track = Track(
        title="Test Song",
        artist_id=artist.id,
        album_id=album.id,
        musicbrainz_id="existing-track-mbid",
    )
    db_session.add(track)
    await db_session.flush()

    service = MusicBrainzService(musicbrainz_config, client=mock_client)

    monkeypatch.setattr(
        "songhive.services.musicbrainz.musicbrainzngs.search_recordings",
        lambda **_: recording_result,
    )
    monkeypatch.setattr(
        "songhive.services.musicbrainz.musicbrainzngs.get_recording_by_id",
        lambda *_: recording_details,
    )

    result = await service.enrich_track(db_session, str(track.id))

    assert result is True
    assert track.musicbrainz_id == "existing-track-mbid"
    assert artist.musicbrainz_id == "existing-artist-mbid"
    assert album.musicbrainz_id == "existing-album-mbid"


@pytest.mark.asyncio
async def test_enrich_fetches_cover_art_when_missing(
    db_session,
    musicbrainz_config,
    mock_client,
    recording_result,
    recording_details,
    local_storage_service,
    monkeypatch,
):
    """Enrichment downloads and stores cover art when the album lacks one."""
    artist = Artist(name="Test Artist")
    db_session.add(artist)
    await db_session.flush()

    album = Album(title="Test Album", artist_id=artist.id)
    db_session.add(album)
    await db_session.flush()

    track = Track(
        title="Test Song",
        artist_id=artist.id,
        album_id=album.id,
    )
    db_session.add(track)
    await db_session.flush()

    service = MusicBrainzService(musicbrainz_config, client=mock_client)

    monkeypatch.setattr(
        "songhive.services.musicbrainz.musicbrainzngs.search_recordings",
        lambda **_: recording_result,
    )
    monkeypatch.setattr(
        "songhive.services.musicbrainz.musicbrainzngs.get_recording_by_id",
        lambda *_: recording_details,
    )

    result = await service.enrich_track(db_session, str(track.id), storage_service=local_storage_service)

    assert result is True
    assert track.musicbrainz_id == "recording-1"
    assert artist.musicbrainz_id == "artist-mbid-1"
    assert album.musicbrainz_id == "release-1"
    assert album.cover_file_id is not None


@pytest.mark.asyncio
async def test_rate_limiter_enforces_limit(db_session, musicbrainz_config, monkeypatch):
    """The rate limiter sleeps at least the configured interval between requests."""
    service = MusicBrainzService(MusicBrainzConfig(enabled=True, rate_limit_per_second=10.0))

    call_count = 0

    def mock_call(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return {"recording-list": []}

    monkeypatch.setattr(
        "songhive.services.musicbrainz.musicbrainzngs.search_recordings",
        mock_call,
    )

    start = time.monotonic()
    await service.search_recordings(query="test")
    await service.search_recordings(query="test")
    elapsed = time.monotonic() - start

    assert call_count == 2
    assert elapsed >= 0.08


# ---------------------------------------------------------------------------
# Search / lookup error paths and no results
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_recordings_returns_empty_when_no_query(musicbrainz_config):
    """search_recordings returns an empty dict when no query can be built."""
    service = MusicBrainzService(musicbrainz_config)
    result = await service.search_recordings()
    assert result == {}


@pytest.mark.asyncio
async def test_fetch_recording_includes_artist_rels(musicbrainz_config, monkeypatch):
    """fetch_recording requests artist relationships when asked."""
    service = MusicBrainzService(musicbrainz_config)
    calls = []

    def fake_get_recording_by_id(recording_id, includes):
        calls.append((recording_id, includes))
        return {"recording": {"id": recording_id}}

    monkeypatch.setattr(
        "songhive.services.musicbrainz.musicbrainzngs.get_recording_by_id",
        fake_get_recording_by_id,
    )

    result = await service.fetch_recording("rec-1", include_artist_rels=True)

    assert result == {"recording": {"id": "rec-1"}}
    assert calls == [("rec-1", ["artist-rels"])]


@pytest.mark.asyncio
async def test_enrich_track_returns_false_when_track_not_found(db_session, musicbrainz_config):
    """enrich_track returns False when the track does not exist."""
    service = MusicBrainzService(musicbrainz_config)
    result = await service.enrich_track(db_session, "missing-id")
    assert result is False


@pytest.mark.asyncio
async def test_enrich_track_returns_false_when_no_recordings(db_session, musicbrainz_config, monkeypatch):
    """enrich_track returns False when MusicBrainz has no matching recordings."""
    artist = Artist(name="Artist")
    db_session.add(artist)
    await db_session.flush()

    album = Album(title="Album", artist_id=artist.id)
    db_session.add(album)
    await db_session.flush()

    track = Track(title="Song", artist_id=artist.id, album_id=album.id)
    db_session.add(track)
    await db_session.flush()

    service = MusicBrainzService(musicbrainz_config)
    monkeypatch.setattr(
        "songhive.services.musicbrainz.musicbrainzngs.search_recordings",
        lambda **_: {"recording-list": []},
    )

    result = await service.enrich_track(db_session, str(track.id))
    assert result is False


@pytest.mark.asyncio
async def test_enrich_track_returns_false_when_recording_has_no_id(db_session, musicbrainz_config, monkeypatch):
    """enrich_track returns False when the best match lacks a MusicBrainz ID."""
    artist = Artist(name="Artist")
    db_session.add(artist)
    await db_session.flush()

    track = Track(title="Song", artist_id=artist.id)
    db_session.add(track)
    await db_session.flush()

    service = MusicBrainzService(musicbrainz_config)
    monkeypatch.setattr(
        "songhive.services.musicbrainz.musicbrainzngs.search_recordings",
        lambda **_: {"recording-list": [{"title": "Song"}]},
    )

    result = await service.enrich_track(db_session, str(track.id))
    assert result is False


@pytest.mark.asyncio
async def test_fetch_recording_details_falls_back_on_error(musicbrainz_config, monkeypatch):
    """_fetch_recording_details falls back to the search result on error."""
    service = MusicBrainzService(musicbrainz_config)
    monkeypatch.setattr(
        service,
        "fetch_recording",
        AsyncMock(side_effect=RuntimeError("network")),
    )

    recording = {"id": "rec-1", "title": "Song"}
    result = await service._fetch_recording_details("rec-1", recording)

    assert result == {"recording": recording}


# ---------------------------------------------------------------------------
# Cover art fetching
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_release_returns_none_when_cover_art_disabled(mock_client):
    """fetch_release returns None when cover art fetching is disabled."""
    config = MusicBrainzConfig(enabled=True, fetch_cover_art=False)
    service = MusicBrainzService(config, client=mock_client)
    result = await service.fetch_release("release-1")
    assert result is None


@pytest.mark.asyncio
async def test_fetch_release_handles_request_exception(musicbrainz_config, mock_client):
    """fetch_release returns None when the request fails."""
    service = MusicBrainzService(musicbrainz_config, client=mock_client)
    mock_client.get = AsyncMock(side_effect=RuntimeError("network"))
    result = await service.fetch_release("release-1")
    assert result is None


@pytest.mark.asyncio
async def test_fetch_release_returns_redirect_location(musicbrainz_config, mock_client):
    """fetch_release returns the Location header for a 307 response."""
    service = MusicBrainzService(musicbrainz_config, client=mock_client)
    mock_client.get = AsyncMock(
        return_value=Mock(
            status_code=307,
            headers={"location": "https://example.com/cover.jpg"},
            url="https://coverartarchive.org/release/release-1/front",
        )
    )
    result = await service.fetch_release("release-1")
    assert result == "https://example.com/cover.jpg"


@pytest.mark.asyncio
async def test_fetch_release_returns_none_for_unexpected_status(musicbrainz_config, mock_client):
    """fetch_release returns None for response codes other than 200 or 307."""
    service = MusicBrainzService(musicbrainz_config, client=mock_client)
    mock_client.get = AsyncMock(
        return_value=Mock(
            status_code=404,
            url="https://coverartarchive.org/release/release-1/front",
            headers={},
        )
    )
    result = await service.fetch_release("release-1")
    assert result is None


@pytest.mark.asyncio
async def test_fetch_cover_image_returns_none_when_disabled(mock_client):
    """fetch_cover_image returns None when cover art fetching is disabled."""
    config = MusicBrainzConfig(enabled=True, fetch_cover_art=False)
    service = MusicBrainzService(config, client=mock_client)
    result = await service.fetch_cover_image("release-1")
    assert result is None


@pytest.mark.asyncio
async def test_fetch_cover_image_handles_download_exception(musicbrainz_config, mock_client):
    """fetch_cover_image returns None when the cover request raises an exception."""
    service = MusicBrainzService(musicbrainz_config, client=mock_client)
    mock_client.get = AsyncMock(side_effect=RuntimeError("network"))

    result = await service.fetch_cover_image("release-1")
    assert result is None


@pytest.mark.asyncio
async def test_fetch_cover_image_follows_redirect(musicbrainz_config, mock_client):
    """fetch_cover_image follows 3xx redirects from the Cover Art Archive."""
    service = MusicBrainzService(musicbrainz_config, client=mock_client)
    redirect = Mock(
        status_code=307,
        headers={"location": "https://example.com/cover.jpg"},
    )
    image = Mock(
        status_code=200,
        url="https://example.com/cover.jpg",
        content=b"\x89PNG\r\n\x1a\nfake",
        headers={},
    )
    mock_client.get = AsyncMock(side_effect=[redirect, image])

    result = await service.fetch_cover_image("release-1")
    assert result == b"\x89PNG\r\n\x1a\nfake"


@pytest.mark.asyncio
async def test_store_cover_art_skips_when_album_has_cover(musicbrainz_config, db_session):
    """_store_cover_art returns immediately when the album already has cover art."""
    service = MusicBrainzService(musicbrainz_config)
    album = SimpleNamespace(cover_file_id="existing-cover", visibility="private")
    await service._store_cover_art(db_session, album, "release-1", Mock())
    assert album.cover_file_id == "existing-cover"


@pytest.mark.asyncio
async def test_store_cover_art_skips_when_no_image_data(musicbrainz_config, db_session, monkeypatch):
    """_store_cover_art returns when no cover image can be downloaded."""
    service = MusicBrainzService(musicbrainz_config)
    monkeypatch.setattr(service, "fetch_cover_image", AsyncMock(return_value=None))
    album = SimpleNamespace(cover_file_id=None, visibility="private")
    await service._store_cover_art(db_session, album, "release-1", Mock())
    assert album.cover_file_id is None


@pytest.mark.asyncio
async def test_maybe_store_cover_art_skips_when_cover_exists(musicbrainz_config, db_session):
    """_maybe_store_cover_art returns immediately when the album already has cover art."""
    service = MusicBrainzService(musicbrainz_config)
    album = SimpleNamespace(cover_file_id="existing-cover", visibility="private")
    await service._maybe_store_cover_art(db_session, album, "release-1", Mock())
    assert album.cover_file_id == "existing-cover"


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def test_first_artist_id_returns_none_for_invalid_credit():
    """_first_artist_id returns None when the artist credit is missing or malformed."""
    assert _first_artist_id({}) is None
    assert _first_artist_id({"artist-credit": []}) is None
    assert _first_artist_id({"artist-credit": ["not-a-dict"]}) is None
    assert _first_artist_id({"artist-credit": [{"artist": "not-a-dict"}]}) is None


def test_first_release_id_returns_none_for_invalid_details():
    """_first_release_id returns None when no releases are present."""
    assert _first_release_id({}) is None
    assert _first_release_id({"recording": {}}) is None
    assert _first_release_id({"recording": {"release-list": []}}) is None


def test_guess_image_mime_branches():
    """_guess_image_mime detects common image formats and defaults to JPEG."""
    assert _guess_image_mime(b"\x89PNG\r\n\x1a\n") == "image/png"
    assert _guess_image_mime(b"\xff\xd8") == "image/jpeg"
    assert _guess_image_mime(b"GIF89a") == "image/gif"
    assert _guess_image_mime(b"RIFF" + b"\x00" * 4 + b"WEBP") == "image/webp"
    assert _guess_image_mime(b"unknown") == "image/jpeg"


# ---------------------------------------------------------------------------
# songhive.tasks.musicbrainz helpers
# ---------------------------------------------------------------------------


class _FakeSessionContext:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *args):
        return None


class _FakeMBService:
    def __init__(self, config):
        pass

    async def enrich_track(self, session, track_id, storage_service, force=False):
        return True


class _FailingMBService:
    def __init__(self, config):
        pass

    async def enrich_track(self, session, track_id, storage_service, force=False):
        raise RuntimeError("boom")


def _make_mb_config(enabled=True):
    return SimpleNamespace(
        database=SimpleNamespace(url="sqlite+aiosqlite:///test.db"),
        storage=SimpleNamespace(backend="local", local_path="/tmp/media"),
        musicbrainz=SimpleNamespace(enabled=enabled),
    )


def _patch_mb_task_env(monkeypatch, tmp_path, mb_service_cls=_FakeMBService):
    monkeypatch.setattr("songhive.config.load_config", lambda *_: _make_mb_config(enabled=True))
    monkeypatch.setattr("songhive.models.base.init_db", Mock())
    monkeypatch.setattr(
        "songhive.models.base.get_session",
        lambda: _FakeSessionContext(Mock()),
    )
    monkeypatch.setattr("songhive.storage.get_storage", Mock(return_value=Mock()))
    monkeypatch.setattr("songhive.services.musicbrainz.MusicBrainzService", mb_service_cls)


# ---------------------------------------------------------------------------
# songhive.tasks.musicbrainz.enrich_track
# ---------------------------------------------------------------------------


def test_enrich_track_task_returns_false_when_disabled(monkeypatch, tmp_path):
    """The Celery task returns False when MusicBrainz is disabled."""
    monkeypatch.setattr("songhive.config.load_config", lambda *_: _make_mb_config(enabled=False))
    result = enrich_track("track-1")
    assert result is False


def test_enrich_track_task_returns_true_when_enabled(monkeypatch, tmp_path):
    """The Celery task returns the service result when enabled."""
    _patch_mb_task_env(monkeypatch, tmp_path)
    result = enrich_track("track-1")
    assert result is True


def test_enrich_track_task_returns_false_on_exception(monkeypatch, tmp_path, caplog):
    """The Celery task catches exceptions and returns False."""
    _patch_mb_task_env(monkeypatch, tmp_path, mb_service_cls=_FailingMBService)
    result = enrich_track("track-1")
    assert result is False
    assert "MusicBrainz enrichment failed" in caplog.text


@pytest.mark.asyncio
async def test_enrich_fills_missing_metadata(
    db_session,
    musicbrainz_config,
    mock_client,
    recording_result,
    recording_details,
    local_storage_service,
    monkeypatch,
):
    """Enrichment populates missing title, artist, album, cover, and metadata."""
    from songhive.models.stored_file import StoredFile

    unknown_artist = Artist(name="Unknown Artist")
    db_session.add(unknown_artist)
    await db_session.flush()

    audio_file = StoredFile(
        storage_path="files/song",
        storage_backend="local",
        content_type="audio/mpeg",
        size=1,
        sha256="a" * 64,
        original_filename="unknown_song.mp3",
    )
    db_session.add(audio_file)
    await db_session.flush()

    track = Track(
        title="unknown_song",
        artist_id=unknown_artist.id,
        audio_file_id=str(audio_file.id),
        audio_mime_type="audio/mpeg",
    )
    db_session.add(track)
    await db_session.flush()

    service = MusicBrainzService(musicbrainz_config, client=mock_client)

    monkeypatch.setattr(
        "songhive.services.musicbrainz.musicbrainzngs.search_recordings",
        lambda **_: recording_result,
    )
    monkeypatch.setattr(
        "songhive.services.musicbrainz.musicbrainzngs.get_recording_by_id",
        lambda *_: recording_details,
    )

    result = await service.enrich_track(
        db_session,
        str(track.id),
        storage_service=local_storage_service,
    )

    assert result is True
    assert track.title == "Test Song"
    assert track.musicbrainz_id == "recording-1"
    assert track.musicbrainz_enriched_at is not None

    enriched_artist = await db_session.get(Artist, track.artist_id)
    assert enriched_artist is not None
    assert enriched_artist.name == "Test Artist"
    assert enriched_artist.musicbrainz_id == "artist-mbid-1"

    album = await db_session.get(Album, track.album_id)
    assert album is not None
    assert album.title == "Test Album"
    assert album.musicbrainz_id == "release-1"
    assert album.cover_file_id is not None


@pytest.mark.asyncio
async def test_enrich_skips_already_enriched_without_force(
    db_session,
    musicbrainz_config,
    monkeypatch,
):
    """enrich_track skips tracks that were already enriched unless forced."""
    artist = Artist(name="Artist")
    db_session.add(artist)
    await db_session.flush()

    track = Track(title="Song", artist_id=artist.id)
    track.musicbrainz_enriched_at = datetime.datetime.now(datetime.timezone.utc)
    db_session.add(track)
    await db_session.flush()

    service = MusicBrainzService(musicbrainz_config)
    search_mock = Mock(return_value={"recording-list": []})
    monkeypatch.setattr(
        "songhive.services.musicbrainz.musicbrainzngs.search_recordings",
        search_mock,
    )

    result = await service.enrich_track(db_session, str(track.id))
    assert result is False
    search_mock.assert_not_called()

    result = await service.enrich_track(db_session, str(track.id), force=True)
    assert result is False
    search_mock.assert_called_once()


@pytest.mark.asyncio
async def test_enrich_no_match_marks_enriched(
    db_session,
    musicbrainz_config,
    monkeypatch,
):
    """When MusicBrainz has no match the track is marked as enriched."""
    artist = Artist(name="Artist")
    db_session.add(artist)
    await db_session.flush()

    track = Track(title="Song", artist_id=artist.id)
    db_session.add(track)
    await db_session.flush()

    service = MusicBrainzService(musicbrainz_config)
    monkeypatch.setattr(
        "songhive.services.musicbrainz.musicbrainzngs.search_recordings",
        lambda **_: {"recording-list": []},
    )

    result = await service.enrich_track(db_session, str(track.id))
    assert result is False
    assert track.musicbrainz_enriched_at is not None


@pytest.mark.asyncio
async def test_enrich_track_allows_duplicate_musicbrainz_id(
    db_session,
    musicbrainz_config,
    recording_result,
    recording_details,
    monkeypatch,
):
    """Multiple tracks may map to the same MusicBrainz recording."""
    artist = Artist(name="Test Artist")
    db_session.add(artist)
    await db_session.flush()

    album = Album(title="Test Album", artist_id=artist.id)
    db_session.add(album)
    await db_session.flush()

    track1 = Track(title="Test Song", artist_id=artist.id, album_id=album.id)
    track2 = Track(title="Test Song 2", artist_id=artist.id, album_id=album.id)
    db_session.add_all([track1, track2])
    await db_session.flush()

    service = MusicBrainzService(musicbrainz_config)
    monkeypatch.setattr(
        "songhive.services.musicbrainz.musicbrainzngs.search_recordings",
        lambda **_: recording_result,
    )
    monkeypatch.setattr(
        "songhive.services.musicbrainz.musicbrainzngs.get_recording_by_id",
        lambda *_: recording_details,
    )

    result1 = await service.enrich_track(db_session, str(track1.id))
    result2 = await service.enrich_track(db_session, str(track2.id))

    assert result1 is True
    assert result2 is True
    assert track1.musicbrainz_id == "recording-1"
    assert track2.musicbrainz_id == "recording-1"
    assert track1.musicbrainz_enriched_at is not None
    assert track2.musicbrainz_enriched_at is not None


@pytest.mark.asyncio
async def test_find_or_create_artist_handles_concurrent_insert(
    db_session,
    musicbrainz_config,
    monkeypatch,
):
    """A unique conflict while creating an artist falls back to the existing row."""
    from sqlalchemy.exc import IntegrityError

    existing = Artist(name="Test Artist", musicbrainz_id="artist-mbid-1")
    db_session.add(existing)
    await db_session.flush()

    service = MusicBrainzService(musicbrainz_config)

    calls = 0

    async def _fake_execute(stmt):
        nonlocal calls
        calls += 1
        if calls < 3:
            return SimpleNamespace(scalar_one_or_none=lambda: None)
        return SimpleNamespace(scalar_one_or_none=lambda: existing)

    async def _fake_flush(*_, **__):
        raise IntegrityError("unique", None, Exception("UNIQUE constraint failed"))

    monkeypatch.setattr(db_session, "execute", _fake_execute)
    monkeypatch.setattr(db_session, "flush", _fake_flush)

    artist = await service._find_or_create_artist(db_session, "Other Name", mbid="artist-mbid-1")
    assert artist is existing


@pytest.mark.asyncio
async def test_find_or_create_artist_re_raises_non_unique_integrity_error(
    db_session,
    musicbrainz_config,
    monkeypatch,
):
    """A non-unique IntegrityError while creating an artist is re-raised."""
    from sqlalchemy.exc import IntegrityError

    service = MusicBrainzService(musicbrainz_config)

    monkeypatch.setattr(
        db_session,
        "execute",
        AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: None)),
    )
    monkeypatch.setattr(
        db_session,
        "flush",
        AsyncMock(side_effect=IntegrityError("stmt", None, Exception("FOREIGN KEY constraint failed"))),
    )

    with pytest.raises(IntegrityError):
        await service._find_or_create_artist(db_session, "Artist", mbid="artist-mbid-1")


@pytest.mark.asyncio
async def test_find_or_create_album_handles_concurrent_insert(
    db_session,
    musicbrainz_config,
    monkeypatch,
):
    """A unique conflict while creating an album falls back to the existing row."""
    from sqlalchemy.exc import IntegrityError

    existing_artist = Artist(name="Test Artist")
    db_session.add(existing_artist)
    await db_session.flush()

    existing = Album(
        title="Test Album",
        artist_id=existing_artist.id,
        musicbrainz_id="release-1",
    )
    db_session.add(existing)
    await db_session.flush()

    service = MusicBrainzService(musicbrainz_config)

    calls = 0

    async def _fake_execute(stmt):
        nonlocal calls
        calls += 1
        if calls < 3:
            return SimpleNamespace(scalar_one_or_none=lambda: None)
        return SimpleNamespace(scalar_one_or_none=lambda: existing)

    async def _fake_flush(*_, **__):
        raise IntegrityError("unique", None, Exception("UNIQUE constraint failed"))

    monkeypatch.setattr(db_session, "execute", _fake_execute)
    monkeypatch.setattr(db_session, "flush", _fake_flush)

    album = await service._find_or_create_album(
        db_session,
        title="Other Album",
        artist_id=existing_artist.id,
        mbid="release-1",
    )
    assert album is existing
