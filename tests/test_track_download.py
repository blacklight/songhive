"""
Tests for track download endpoint including external library sources.
"""

import io
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from songhive.models._enums import Visibility
from songhive.models.artist import Artist
from songhive.models.external_library import ExternalLibrary
from songhive.models.external_track import ExternalTrack
from songhive.models.library import Library
from songhive.models.track import Track
from songhive.services.auth import create_user
from songhive.services.storage import StorageService
from songhive.storage import get_storage


@pytest.fixture
async def download_user(db_session):
    return await create_user(db_session, "downloader", "downloader@example.com", "secret")


@pytest.fixture
async def download_artist(db_session):
    artist = Artist(name="Download Artist")
    db_session.add(artist)
    await db_session.flush()
    return artist


@pytest.fixture
async def download_library(db_session, download_user):
    library = Library(name="Download Library", owner_id=str(download_user.id))
    db_session.add(library)
    await db_session.flush()
    return library


@pytest.fixture
def storage_service(config):
    return StorageService(get_storage(config.storage), config.storage)


@pytest.fixture
async def local_track(db_session, download_user, download_artist, download_library, storage_service, config):
    data = b"local track data"
    file = await storage_service.store_file(
        db_session,
        io.BytesIO(data),
        "audio/mpeg",
        owner_id=str(download_user.id),
        visibility=Visibility.PUBLIC.value,
    )
    db_session.add(file)
    track = Track(
        title="Local Track",
        artist_id=download_artist.id,
        audio_file_id=file.id,
        owner_id=str(download_user.id),
        visibility=Visibility.PUBLIC.value,
        duration=1.0,
    )
    db_session.add(track)
    await db_session.flush()
    return track, data


@pytest.fixture
async def external_iterator_track(db_session, download_user, download_artist, download_library):
    data = b"external iterator data"
    lib = ExternalLibrary(
        name="Iterator Download",
        provider_type="fake",
        library_id=download_library.id,
        created_by_id=str(download_user.id),
        config={
            "items": {
                "download.mp3": {"data": list(data), "mimetype": "audio/mpeg"},
            }
        },
        capabilities={"read_bytes": True, "download": True},
    )
    db_session.add(lib)
    await db_session.flush()

    et = ExternalTrack(
        track_id=None,
        external_library_id=lib.id,
        provider_key="download.mp3",
        provider_size=len(data),
        provider_mime_type="audio/mpeg",
        state="active",
    )
    db_session.add(et)
    await db_session.flush()

    track = Track(
        title="External Iterator",
        artist_id=download_artist.id,
        audio_file_id=None,
        owner_id=str(download_user.id),
        visibility=Visibility.PUBLIC.value,
        duration=1.0,
        audio_mime_type=et.provider_mime_type,
    )
    db_session.add(track)
    await db_session.flush()
    et.track_id = track.id
    await db_session.flush()
    return track, data


@pytest.fixture
async def external_path_track(db_session, download_user, download_artist, download_library):
    data = b"external path data"
    lib = ExternalLibrary(
        name="Path Download",
        provider_type="fake",
        library_id=download_library.id,
        created_by_id=str(download_user.id),
        config={
            "items": {
                "path.mp3": {"data": list(data), "mimetype": "audio/mpeg"},
            },
            "prefer_path": True,
        },
        capabilities={"read_bytes": True, "download": True},
    )
    db_session.add(lib)
    await db_session.flush()

    et = ExternalTrack(
        track_id=None,
        external_library_id=lib.id,
        provider_key="path.mp3",
        provider_size=len(data),
        provider_mime_type="audio/mpeg",
        state="active",
    )
    db_session.add(et)
    await db_session.flush()

    track = Track(
        title="External Path",
        artist_id=download_artist.id,
        audio_file_id=None,
        owner_id=str(download_user.id),
        visibility=Visibility.PUBLIC.value,
        duration=1.0,
        audio_mime_type=et.provider_mime_type,
    )
    db_session.add(track)
    await db_session.flush()
    et.track_id = track.id
    await db_session.flush()
    return track, data


@pytest.fixture
async def external_safe_url_track(db_session, download_user, download_artist, download_library):
    data = b"safe url data"
    lib = ExternalLibrary(
        name="Safe URL Download",
        provider_type="fake",
        library_id=download_library.id,
        created_by_id=str(download_user.id),
        config={
            "items": {
                "url.mp3": {"data": list(data), "mimetype": "audio/mpeg"},
            },
            "safe_url": True,
        },
        capabilities={"stream_url": True},
    )
    db_session.add(lib)
    await db_session.flush()

    et = ExternalTrack(
        track_id=None,
        external_library_id=lib.id,
        provider_key="url.mp3",
        provider_size=len(data),
        provider_mime_type="audio/mpeg",
        state="active",
    )
    db_session.add(et)
    await db_session.flush()

    track = Track(
        title="External Safe URL",
        artist_id=download_artist.id,
        audio_file_id=None,
        owner_id=str(download_user.id),
        visibility=Visibility.PUBLIC.value,
        duration=1.0,
        audio_mime_type=et.provider_mime_type,
    )
    db_session.add(track)
    await db_session.flush()
    et.track_id = track.id
    await db_session.flush()
    return track


@pytest.fixture
async def external_proxy_url_track(db_session, download_user, download_artist, download_library):
    data = b"proxy url data"
    lib = ExternalLibrary(
        name="Proxy URL Download",
        provider_type="fake",
        library_id=download_library.id,
        created_by_id=str(download_user.id),
        config={
            "items": {
                "url.mp3": {"data": list(data), "mimetype": "audio/mpeg"},
            },
            "prefer_url": True,
        },
        capabilities={"stream_url": True},
    )
    db_session.add(lib)
    await db_session.flush()

    et = ExternalTrack(
        track_id=None,
        external_library_id=lib.id,
        provider_key="url.mp3",
        provider_size=len(data),
        provider_mime_type="audio/mpeg",
        state="active",
    )
    db_session.add(et)
    await db_session.flush()

    track = Track(
        title="External Proxy URL",
        artist_id=download_artist.id,
        audio_file_id=None,
        owner_id=str(download_user.id),
        visibility=Visibility.PUBLIC.value,
        duration=1.0,
        audio_mime_type=et.provider_mime_type,
    )
    db_session.add(track)
    await db_session.flush()
    et.track_id = track.id
    await db_session.flush()
    return track, data


@pytest.fixture
async def tombstoned_external_track(db_session, download_user, download_artist, download_library):
    data = b"tombstoned data"
    lib = ExternalLibrary(
        name="Tombstoned Download",
        provider_type="fake",
        library_id=download_library.id,
        created_by_id=str(download_user.id),
        config={
            "items": {
                "gone.mp3": {"data": list(data), "mimetype": "audio/mpeg"},
            }
        },
        capabilities={"read_bytes": True, "download": True},
    )
    db_session.add(lib)
    await db_session.flush()

    et = ExternalTrack(
        track_id=None,
        external_library_id=lib.id,
        provider_key="gone.mp3",
        provider_size=len(data),
        provider_mime_type="audio/mpeg",
        state="tombstoned",
    )
    db_session.add(et)
    await db_session.flush()

    track = Track(
        title="Tombstoned Track",
        artist_id=download_artist.id,
        audio_file_id=None,
        owner_id=str(download_user.id),
        visibility=Visibility.PUBLIC.value,
        duration=1.0,
        audio_mime_type=et.provider_mime_type,
    )
    db_session.add(track)
    await db_session.flush()
    et.track_id = track.id
    await db_session.flush()
    return track


def _mock_httpx(data: bytes):
    """Return a patch context manager that yields the given bytes from httpx."""
    resp = MagicMock()
    resp.status_code = 200
    resp.headers = {"content-type": "audio/mpeg", "content-length": str(len(data))}

    async def _chunks(**kwargs):
        chunk_size = kwargs.get("chunk_size", 1024)
        for i in range(0, len(data), chunk_size):
            yield data[i : i + chunk_size]

    resp.aiter_bytes = _chunks

    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.stream = MagicMock()
    client.stream.return_value.__aenter__ = AsyncMock(return_value=resp)
    client.stream.return_value.__aexit__ = AsyncMock(return_value=False)

    return patch("songhive.services.streaming.httpx.AsyncClient", return_value=client)


def test_download_local_track(client, local_track, auth_headers, download_user):
    track, data = local_track
    response = client.get(f"/api/v1/tracks/{track.id}/download", headers=auth_headers(download_user))
    assert response.status_code == 200
    assert response.content == data
    assert "audio/mpeg" in response.headers.get("content-type", "")


def test_download_external_iterator(client, external_iterator_track, auth_headers, download_user):
    track, data = external_iterator_track
    response = client.get(f"/api/v1/tracks/{track.id}/download", headers=auth_headers(download_user))
    assert response.status_code == 200
    assert response.content == data
    assert "audio/mpeg" in response.headers.get("content-type", "")
    assert "filename" in response.headers.get("content-disposition", "").lower()


def test_download_external_path(client, external_path_track, auth_headers, download_user):
    track, data = external_path_track
    response = client.get(f"/api/v1/tracks/{track.id}/download", headers=auth_headers(download_user))
    assert response.status_code == 200
    assert response.content == data


def test_download_external_safe_url(client, external_safe_url_track, auth_headers, download_user):
    track = external_safe_url_track
    response = client.get(
        f"/api/v1/tracks/{track.id}/download",
        headers=auth_headers(download_user),
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert "songhive.invalid" in (response.headers.get("location") or "")


def test_download_external_proxy_url(client, external_proxy_url_track, auth_headers, download_user):
    track, data = external_proxy_url_track
    with _mock_httpx(data):
        response = client.get(f"/api/v1/tracks/{track.id}/download", headers=auth_headers(download_user))
    assert response.status_code == 200
    assert response.content == data


def test_download_tombstoned_external(client, tombstoned_external_track, auth_headers, download_user):
    track = tombstoned_external_track
    response = client.get(f"/api/v1/tracks/{track.id}/download", headers=auth_headers(download_user))
    assert response.status_code == 404
