"""
Image enrichment tests.
"""

import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from songhive.config.schema import MusicBrainzConfig, StorageConfig
from songhive.models.album import Album
from songhive.models.artist import Artist
from songhive.models.track import Track
from songhive.services.musicbrainz import (
    MusicBrainzService,
    _is_image_relation,
    _is_valid_image,
    _looks_like_image_url,
)
from songhive.services.storage import StorageService
from songhive.storage import get_storage
from songhive.tasks.images import enrich_images


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
    """Return a mock httpx.AsyncClient that returns a PNG image."""
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
def artist_with_image_relation():
    """Return a MusicBrainz artist response with a direct image URL relation."""
    return {
        "artist": {
            "id": "artist-mbid-1",
            "name": "Test Artist",
            "url-relation-list": [
                {
                    "type": "image",
                    "target": "https://example.com/artist.png",
                }
            ],
        }
    }


@pytest.fixture
def artist_with_wikimedia_relation():
    """Return a MusicBrainz artist response with a Wikimedia Commons relation."""
    return {
        "artist": {
            "id": "artist-mbid-1",
            "name": "Test Artist",
            "url-relation-list": [
                {
                    "type": "image",
                    "target": "https://commons.wikimedia.org/wiki/File:Test_Artist.jpg",
                }
            ],
        }
    }


@pytest.mark.asyncio
async def test_enrich_images_disabled_does_nothing(db_session):
    """When MusicBrainz is disabled, image enrichment returns False."""
    service = MusicBrainzService(MusicBrainzConfig(enabled=False))
    result = await service.enrich_images(db_session, "00000000-0000-0000-0000-000000000000")
    assert result is False


@pytest.mark.asyncio
async def test_enrich_images_disabled_when_artist_images_off(db_session):
    """When artist image fetching is disabled, image enrichment returns False."""
    service = MusicBrainzService(MusicBrainzConfig(enabled=True, fetch_artist_images=False))
    result = await service.enrich_images(db_session, "00000000-0000-0000-0000-000000000000")
    assert result is False


@pytest.mark.asyncio
async def test_enrich_images_stores_artist_image_from_direct_url(
    db_session,
    musicbrainz_config,
    mock_client,
    artist_with_image_relation,
    local_storage_service,
    monkeypatch,
):
    """Image enrichment downloads and stores an artist image from a direct URL."""
    artist = Artist(name="Test Artist", musicbrainz_id="artist-mbid-1")
    db_session.add(artist)
    await db_session.flush()

    track = Track(title="Test Song", artist_id=artist.id)
    db_session.add(track)
    await db_session.flush()

    service = MusicBrainzService(musicbrainz_config, client=mock_client)
    monkeypatch.setattr(
        "songhive.services.musicbrainz.musicbrainzngs.get_artist_by_id",
        lambda *_: artist_with_image_relation,
    )

    result = await service.enrich_images(
        db_session,
        str(track.id),
        storage_service=local_storage_service,
    )

    assert result is True
    assert artist.image_file_id is not None
    assert artist.image_enriched_at is not None


@pytest.mark.asyncio
async def test_enrich_images_resolves_wikimedia_url(
    db_session,
    musicbrainz_config,
    mock_client,
    artist_with_wikimedia_relation,
    local_storage_service,
    monkeypatch,
):
    """Image enrichment resolves a Wikimedia Commons URL to a direct image."""
    artist = Artist(name="Test Artist", musicbrainz_id="artist-mbid-1")
    db_session.add(artist)
    await db_session.flush()

    track = Track(title="Test Song", artist_id=artist.id)
    db_session.add(track)
    await db_session.flush()

    service = MusicBrainzService(musicbrainz_config, client=mock_client)

    wikimedia_api_response = {
        "query": {
            "pages": {
                "12345": {
                    "imageinfo": [
                        {
                            "url": "https://upload.wikimedia.org/wikipedia/commons/test.jpg",
                            "mime": "image/jpeg",
                        }
                    ]
                }
            }
        }
    }

    def _mock_get(url, **_):
        if "commons.wikimedia.org/w/api.php" in str(url):
            return Mock(status_code=200, json=Mock(return_value=wikimedia_api_response))
        return Mock(
            status_code=200,
            url="https://upload.wikimedia.org/wikipedia/commons/test.jpg",
            content=b"\xff\xd8fake",
            headers={},
        )

    mock_client.get = AsyncMock(side_effect=_mock_get)
    monkeypatch.setattr(
        "songhive.services.musicbrainz.musicbrainzngs.get_artist_by_id",
        lambda *_: artist_with_wikimedia_relation,
    )

    result = await service.enrich_images(
        db_session,
        str(track.id),
        storage_service=local_storage_service,
    )

    assert result is True
    assert artist.image_file_id is not None
    assert artist.image_enriched_at is not None


@pytest.mark.asyncio
async def test_enrich_images_skips_already_enriched(
    db_session,
    musicbrainz_config,
    monkeypatch,
):
    """Image enrichment skips artists that were already processed."""
    import datetime

    artist = Artist(name="Test Artist", musicbrainz_id="artist-mbid-1")
    artist.image_enriched_at = datetime.datetime.now(datetime.timezone.utc)
    db_session.add(artist)
    await db_session.flush()

    track = Track(title="Test Song", artist_id=artist.id)
    db_session.add(track)
    await db_session.flush()

    service = MusicBrainzService(musicbrainz_config)
    get_artist_mock = Mock()
    monkeypatch.setattr(
        "songhive.services.musicbrainz.musicbrainzngs.get_artist_by_id",
        get_artist_mock,
    )

    result = await service.enrich_images(db_session, str(track.id))

    assert result is False
    get_artist_mock.assert_not_called()


@pytest.mark.asyncio
async def test_enrich_images_falls_back_to_url_without_storage(
    db_session,
    musicbrainz_config,
    mock_client,
    artist_with_image_relation,
    monkeypatch,
):
    """When no storage service is provided, the image URL is stored instead."""
    artist = Artist(name="Test Artist", musicbrainz_id="artist-mbid-1")
    db_session.add(artist)
    await db_session.flush()

    track = Track(title="Test Song", artist_id=artist.id)
    db_session.add(track)
    await db_session.flush()

    service = MusicBrainzService(musicbrainz_config, client=mock_client)
    monkeypatch.setattr(
        "songhive.services.musicbrainz.musicbrainzngs.get_artist_by_id",
        lambda *_: artist_with_image_relation,
    )

    result = await service.enrich_images(db_session, str(track.id))

    assert result is True
    assert artist.image_url == "https://example.com/artist.png"
    assert artist.image_file_id is None
    assert artist.image_enriched_at is not None


@pytest.mark.asyncio
async def test_enrich_images_stores_album_cover_when_missing(
    db_session,
    musicbrainz_config,
    mock_client,
    local_storage_service,
    monkeypatch,
):
    """Image enrichment stores an album cover when the album lacks one."""
    artist = Artist(name="Test Artist", musicbrainz_id="artist-mbid-1")
    db_session.add(artist)
    await db_session.flush()

    album = Album(
        title="Test Album",
        artist_id=artist.id,
        musicbrainz_id="release-1",
    )
    db_session.add(album)
    await db_session.flush()

    track = Track(title="Test Song", artist_id=artist.id, album_id=album.id)
    db_session.add(track)
    await db_session.flush()

    service = MusicBrainzService(musicbrainz_config, client=mock_client)
    get_artist_mock = Mock(return_value={"artist": {"id": "artist-mbid-1", "name": "Test Artist"}})
    monkeypatch.setattr(
        "songhive.services.musicbrainz.musicbrainzngs.get_artist_by_id",
        get_artist_mock,
    )

    result = await service.enrich_images(
        db_session,
        str(track.id),
        storage_service=local_storage_service,
    )

    assert result is True
    assert album.cover_file_id is not None
    assert album.cover_enriched_at is not None


@pytest.mark.asyncio
async def test_enrich_images_returns_false_when_track_not_found(db_session, musicbrainz_config):
    """enrich_images returns False when the track does not exist."""
    service = MusicBrainzService(musicbrainz_config)
    result = await service.enrich_images(db_session, "missing-id")
    assert result is False


def test_looks_like_image_url():
    """_looks_like_image_url matches common image extensions."""
    assert _looks_like_image_url("https://example.com/photo.jpg") is True
    assert _looks_like_image_url("https://example.com/photo.jpeg") is True
    assert _looks_like_image_url("https://example.com/photo.png") is True
    assert _looks_like_image_url("https://example.com/photo.webp") is True
    assert _looks_like_image_url("https://example.com/photo.gif") is True
    assert _looks_like_image_url("https://example.com/page") is False


def test_is_valid_image():
    """_is_valid_image recognizes common image signatures."""
    assert _is_valid_image(b"\x89PNG\r\n\x1a\n") is True
    assert _is_valid_image(b"\xff\xd8") is True
    assert _is_valid_image(b"GIF89a") is True
    assert _is_valid_image(b"RIFF" + b"\x00" * 4 + b"WEBP") is True
    assert _is_valid_image(b"not an image") is False
    assert _is_valid_image(b"") is False


def test_is_image_relation():
    """_is_image_relation recognizes image-like URL relations."""
    assert _is_image_relation({"type": "image"}) is True
    assert _is_image_relation({"type": "logo"}) is True
    assert _is_image_relation({"type": "wikimedia"}) is True
    assert _is_image_relation({"type": "official homepage"}) is False
    assert _is_image_relation({"type": "photo"}) is True
    assert _is_image_relation({}) is False


class _FakeSessionContext:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *args):
        return None


def _make_mb_config(enabled=True, fetch_artist_images=True):
    return SimpleNamespace(
        database=SimpleNamespace(url="sqlite+aiosqlite:///test.db"),
        storage=SimpleNamespace(backend="local", local_path="/tmp/media"),
        musicbrainz=SimpleNamespace(enabled=enabled, fetch_artist_images=fetch_artist_images),
    )


def test_enrich_images_task_returns_false_when_disabled(monkeypatch, tmp_path):
    """The Celery task returns False when image enrichment is disabled."""
    monkeypatch.setattr("songhive.config.load_config", lambda *_: _make_mb_config(enabled=False))
    result = enrich_images("track-1")
    assert result is False


def test_enrich_images_task_returns_false_when_artist_images_off(monkeypatch, tmp_path):
    """The Celery task returns False when artist image fetching is disabled."""
    monkeypatch.setattr(
        "songhive.config.load_config",
        lambda *_: _make_mb_config(enabled=True, fetch_artist_images=False),
    )
    result = enrich_images("track-1")
    assert result is False


@pytest.mark.asyncio
async def test_enrich_artist_image_by_id(
    db_session,
    musicbrainz_config,
    mock_client,
    artist_with_image_relation,
    local_storage_service,
    monkeypatch,
):
    """Image enrichment can target an artist directly by ID."""
    artist = Artist(name="Test Artist", musicbrainz_id="artist-mbid-1")
    db_session.add(artist)
    await db_session.flush()

    service = MusicBrainzService(musicbrainz_config, client=mock_client)
    monkeypatch.setattr(
        "songhive.services.musicbrainz.musicbrainzngs.get_artist_by_id",
        lambda *_: artist_with_image_relation,
    )

    result = await service.enrich_artist_image_by_id(
        db_session,
        str(artist.id),
        storage_service=local_storage_service,
    )

    assert result is True
    assert artist.image_file_id is not None
    assert artist.image_enriched_at is not None


@pytest.mark.asyncio
async def test_enrich_album_cover_by_id(
    db_session,
    musicbrainz_config,
    mock_client,
    local_storage_service,
    monkeypatch,
):
    """Cover enrichment can target an album directly by ID."""
    artist = Artist(name="Test Artist", musicbrainz_id="artist-mbid-1")
    db_session.add(artist)
    await db_session.flush()

    album = Album(
        title="Test Album",
        artist_id=artist.id,
        musicbrainz_id="release-1",
    )
    db_session.add(album)
    await db_session.flush()

    service = MusicBrainzService(musicbrainz_config, client=mock_client)
    get_artist_mock = Mock(return_value={"artist": {"id": "artist-mbid-1", "name": "Test Artist"}})
    monkeypatch.setattr(
        "songhive.services.musicbrainz.musicbrainzngs.get_artist_by_id",
        get_artist_mock,
    )

    result = await service.enrich_album_cover_by_id(
        db_session,
        str(album.id),
        storage_service=local_storage_service,
    )

    assert result is True
    assert album.cover_file_id is not None
    assert album.cover_enriched_at is not None


@pytest.mark.asyncio
async def test_resolve_image_enrichment_targets(db_session):
    """resolve_image_enrichment_targets returns unenriched entities with MBIDs."""
    from songhive.services.admin_tasks import resolve_image_enrichment_targets

    enriched_artist = Artist(name="Enriched", musicbrainz_id="artist-1")
    enriched_artist.image_enriched_at = datetime.datetime.now(datetime.timezone.utc)
    db_session.add(enriched_artist)

    pending_artist = Artist(name="Pending", musicbrainz_id="artist-2")
    db_session.add(pending_artist)

    no_mbid_artist = Artist(name="No MBID")
    db_session.add(no_mbid_artist)
    await db_session.flush()

    enriched_album = Album(
        title="Enriched",
        artist_id=pending_artist.id,
        musicbrainz_id="album-1",
        cover_enriched_at=datetime.datetime.now(datetime.timezone.utc),
    )
    db_session.add(enriched_album)

    pending_album = Album(
        title="Pending",
        artist_id=pending_artist.id,
        musicbrainz_id="album-2",
    )
    db_session.add(pending_album)

    no_mbid_album = Album(title="No MBID", artist_id=pending_artist.id)
    db_session.add(no_mbid_album)
    await db_session.flush()

    artist_ids, album_ids = await resolve_image_enrichment_targets(db_session, all_=True)

    assert str(pending_artist.id) in artist_ids
    assert str(enriched_artist.id) not in artist_ids
    assert str(no_mbid_artist.id) not in artist_ids

    assert str(pending_album.id) in album_ids
    assert str(enriched_album.id) not in album_ids
    assert str(no_mbid_album.id) not in album_ids


@pytest.mark.asyncio
async def test_resolve_image_enrichment_targets_specific_ids(db_session):
    """Specific artist_id and album_id are returned when they have MBIDs."""
    from songhive.services.admin_tasks import resolve_image_enrichment_targets

    artist = Artist(name="Test Artist", musicbrainz_id="artist-1")
    db_session.add(artist)
    await db_session.flush()
    album = Album(title="Test Album", artist_id=artist.id, musicbrainz_id="album-1")
    db_session.add(album)
    no_mbid = Artist(name="No MBID")
    db_session.add(no_mbid)
    await db_session.flush()

    artist_ids, album_ids = await resolve_image_enrichment_targets(db_session, artist_id=str(artist.id))
    assert artist_ids == [str(artist.id)]
    assert album_ids == []

    artist_ids, album_ids = await resolve_image_enrichment_targets(db_session, album_id=str(album.id))
    assert artist_ids == []
    assert album_ids == [str(album.id)]

    artist_ids, album_ids = await resolve_image_enrichment_targets(db_session, artist_id=str(no_mbid.id))
    assert artist_ids == []
    assert album_ids == []


@pytest.mark.asyncio
async def test_bulk_enrich_images_dry_run(db_session, local_storage_service):
    """bulk_enrich_images dry_run returns counts without making changes."""
    from songhive.services.admin_tasks import bulk_enrich_images

    artist = Artist(name="Test Artist", musicbrainz_id="artist-1")
    db_session.add(artist)
    await db_session.flush()
    album = Album(title="Test Album", artist_id=artist.id, musicbrainz_id="album-1")
    db_session.add(album)
    await db_session.flush()

    mb_service = MusicBrainzService(MusicBrainzConfig(enabled=True))

    counts = await bulk_enrich_images(
        db_session,
        mb_service,
        local_storage_service,
        all_=True,
        dry_run=True,
    )

    assert counts["artists"] == 1
    assert counts["albums"] == 1
    assert counts["updated"] == 0
    assert counts["failed"] == 0
