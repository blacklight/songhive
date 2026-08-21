"""
MusicBrainz enrichment tests.
"""

import time
from unittest.mock import AsyncMock, Mock

import pytest

from songhive.config.schema import MusicBrainzConfig, StorageConfig
from songhive.models.album import Album
from songhive.models.artist import Artist
from songhive.models.track import Track
from songhive.services.musicbrainz import MusicBrainzService
from songhive.services.storage import StorageService
from songhive.storage import get_storage


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
