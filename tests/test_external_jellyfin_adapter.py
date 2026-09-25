"""Tests for the Jellyfin external-library adapter.

The adapter is exercised against a small in-memory fake httpx client so no
network or credentials are needed. ``server_url`` uses a public IP literal
(``8.8.8.8``) so the SSRF check passes without DNS; hostname-resolution cases
patch ``loop.getaddrinfo`` directly.
"""

from __future__ import annotations

import asyncio
import json
import socket
import urllib.parse
from typing import Any, Optional

import pytest

from songhive.external import _jellyfin as jellyfin_module
from songhive.external._jellyfin import JellyfinExternalAdapter
from songhive.external.errors import (
    ExternalConfigError,
    UnsupportedExternalOperation,
)
from songhive.external.types import ExternalItemRef

_BASE = "http://8.8.8.8:8096"


def _config(**overrides) -> dict:
    cfg: dict = {
        "server_url": _BASE,
        "api_key": "test-api-key",
        "verify_ssl": False,
    }
    cfg.update(overrides)
    return cfg


def _track_item(item_id: str = "track-1", **overrides) -> dict:
    item: dict = {
        "Id": item_id,
        "Name": "A Song",
        "Type": "Audio",
        "Album": "An Album",
        "AlbumArtist": "The Band",
        "AlbumArtists": [{"Name": "The Band", "Id": "artist-1"}],
        "Artists": ["The Band", "Guest Singer"],
        "IndexNumber": 3,
        "ParentIndexNumber": 1,
        "ProductionYear": 2001,
        "RunTimeTicks": 1_850_000_000,
        "Genres": ["Rock", "Indie"],
        "Etag": "etag-1",
        "DateModified": "2024-05-01T12:00:00Z",
        "Path": "/music/album/song.flac",
        "ProviderIds": {"MusicBrainzTrack": "mb-track-1", "MusicBrainzAlbum": "mb-album-1"},
        "MediaSources": [{"Container": "flac", "Size": 12_345_678}],
        "ImageTags": {"Primary": "tag"},
    }
    item.update(overrides)
    return item


class _FakeResponse:
    """Mimics an httpx ``Response``."""

    def __init__(self, status: int, payload: Any = None):
        self.status_code = status
        self.is_error = status >= 400
        self._payload = payload
        self.content = b"" if payload is None else json.dumps(payload).encode("utf-8")

    def json(self) -> Any:
        return self._payload


class _FakeJellyfinServer:
    """Canned Jellyfin API responses keyed by path."""

    def __init__(self) -> None:
        self.items: list[dict] = []
        self.albums: list[dict] = []
        self.artists: list[dict] = []
        self.playlists: list[dict] = []
        self.playlist_entries: dict[str, list[dict]] = {}
        self.media_folders: list[dict] = []
        self.auth_token: Optional[str] = "session-token"
        self.reject_credentials = False
        self.requests: list[dict] = []

    def handle_get(self, path: str, params: dict, headers: dict) -> _FakeResponse:
        self.requests.append({"path": path, "params": dict(params), "headers": dict(headers)})
        if path.endswith("/System/Info/Public"):
            return _FakeResponse(200, {"ServerName": "Fake", "Version": "10.9.0"})
        if path.endswith("/System/Info"):
            if self.reject_credentials:
                return _FakeResponse(401)
            return _FakeResponse(200, {"ServerName": "Fake", "Id": "server-1"})
        if path.endswith("/Library/MediaFolders"):
            return _FakeResponse(200, {"Items": self.media_folders})
        if "/Playlists/" in path and path.endswith("/Items"):
            playlist_id = path.split("/Playlists/")[1].split("/Items")[0]
            return _FakeResponse(200, {"Items": self.playlist_entries.get(playlist_id, [])})
        if path.endswith("/Items"):
            include = str(params.get("IncludeItemTypes", ""))
            if include == "Audio":
                items = self.items
            elif include == "MusicAlbum":
                items = self.albums
            elif include == "MusicArtist":
                items = self.artists
            elif include == "Playlist":
                items = self.playlists
            else:
                items = []
            return _FakeResponse(200, {"Items": items, "TotalRecordCount": len(items)})
        return _FakeResponse(404)

    def handle_post(self, path: str, payload: Any) -> _FakeResponse:
        self.requests.append({"path": path, "json": payload})
        if path.endswith("/Users/AuthenticateByName"):
            if self.auth_token is None:
                return _FakeResponse(401)
            return _FakeResponse(200, {"AccessToken": self.auth_token, "User": {"Id": "user-1"}})
        return _FakeResponse(404)


class _FakeClient:
    """In-memory stand-in for ``httpx.AsyncClient``."""

    def __init__(self, server: _FakeJellyfinServer, **kwargs: Any):
        self.kwargs = kwargs
        self.server = server
        self.base_url = kwargs.get("base_url") or ""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def _path(self, url: str) -> str:
        return urllib.parse.urlparse(url).path

    async def get(self, url: str, **kwargs: Any) -> _FakeResponse:
        return self.server.handle_get(
            self._path(url),
            kwargs.get("params") or {},
            kwargs.get("headers") or {},
        )

    async def post(self, url: str, **kwargs: Any) -> _FakeResponse:
        return self.server.handle_post(self._path(url), kwargs.get("json"))


async def _collect(aiter) -> list:
    return [item async for item in aiter]


@pytest.fixture
def jellyfin_server() -> _FakeJellyfinServer:
    return _FakeJellyfinServer()


@pytest.fixture
def jellyfin_client(monkeypatch: pytest.MonkeyPatch, jellyfin_server: _FakeJellyfinServer):
    def _client(**kwargs: Any) -> _FakeClient:
        return _FakeClient(jellyfin_server, **kwargs)

    monkeypatch.setattr(jellyfin_module.httpx, "AsyncClient", _client)
    return jellyfin_server


# ---------------------------------------------------------------------------
# SSRF / server_url validation
# ---------------------------------------------------------------------------


class TestServerUrlValidation:
    @pytest.mark.asyncio
    async def test_missing_server_url(self):
        adapter = JellyfinExternalAdapter()
        with pytest.raises(ExternalConfigError):
            await adapter.validate_config({"api_key": "x"})

    @pytest.mark.asyncio
    async def test_rejects_non_http_scheme(self):
        adapter = JellyfinExternalAdapter()
        with pytest.raises(ExternalConfigError):
            await adapter._server_url(_config(server_url="ftp://8.8.8.8"))

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "host",
        ["127.0.0.1", "10.0.0.5", "192.168.1.1", "169.254.1.1", "0.0.0.0", "[::1]", "[fd00::1]"],
    )
    async def test_rejects_non_public_addresses(self, host: str):
        adapter = JellyfinExternalAdapter()
        with pytest.raises(ExternalConfigError):
            await adapter._server_url(_config(server_url=f"http://{host}:8096"))

    @pytest.mark.asyncio
    async def test_accepts_public_ip_literal(self):
        adapter = JellyfinExternalAdapter()
        url = await adapter._server_url(_config(server_url="http://8.8.8.8:8096/"))
        assert url == "http://8.8.8.8:8096"

    @pytest.mark.asyncio
    async def test_rejects_hostname_resolving_to_private(self, monkeypatch: pytest.MonkeyPatch):
        async def _fake_getaddrinfo(self, host, port, *args, **kwargs):
            return [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.1.2.3", port)),
            ]

        monkeypatch.setattr(asyncio.BaseEventLoop, "getaddrinfo", _fake_getaddrinfo)
        adapter = JellyfinExternalAdapter()
        with pytest.raises(ExternalConfigError):
            await adapter._server_url(_config(server_url="http://jellyfin.internal:8096"))

    @pytest.mark.asyncio
    async def test_accepts_hostname_resolving_public(self, monkeypatch: pytest.MonkeyPatch):
        async def _fake_getaddrinfo(self, host, port, *args, **kwargs):
            return [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port)),
            ]

        monkeypatch.setattr(asyncio.BaseEventLoop, "getaddrinfo", _fake_getaddrinfo)
        adapter = JellyfinExternalAdapter()
        url = await adapter._server_url(_config(server_url="https://jellyfin.example.com"))
        assert url == "https://jellyfin.example.com"


# ---------------------------------------------------------------------------
# validate_config / capabilities
# ---------------------------------------------------------------------------


class TestValidateConfig:
    @pytest.mark.asyncio
    async def test_capabilities(self, jellyfin_client: _FakeJellyfinServer):
        adapter = JellyfinExternalAdapter()
        caps = await adapter.validate_config(_config())
        assert caps.limits["entity_import"] is True
        assert caps.list_items is True
        assert caps.list_albums is True
        assert caps.list_artists is True
        assert caps.list_playlists is True
        assert caps.read_bytes is False
        assert caps.stream_url is True
        assert caps.range_read is True
        assert caps.download is True
        assert caps.compute_hash is False
        assert caps.write_tags is False
        assert caps.rename_source is False
        assert caps.delete_source is False
        assert caps.detect_changes is True

    @pytest.mark.asyncio
    async def test_include_toggles_gate_capabilities(self, jellyfin_client: _FakeJellyfinServer):
        adapter = JellyfinExternalAdapter()
        caps = await adapter.validate_config(_config(include_albums=False, include_playlists=False))
        assert caps.list_items is True
        assert caps.list_albums is False
        assert caps.list_playlists is False
        assert caps.list_artists is True

    @pytest.mark.asyncio
    async def test_bad_credentials(self, jellyfin_client: _FakeJellyfinServer):
        jellyfin_client.reject_credentials = True
        adapter = JellyfinExternalAdapter()
        with pytest.raises(ExternalConfigError):
            await adapter.validate_config(_config())

    @pytest.mark.asyncio
    async def test_missing_credentials(self, jellyfin_client: _FakeJellyfinServer):
        adapter = JellyfinExternalAdapter()
        with pytest.raises(ExternalConfigError):
            await adapter.validate_config({"server_url": _BASE, "verify_ssl": False})

    @pytest.mark.asyncio
    async def test_password_auth_exchanges_token(self, jellyfin_client: _FakeJellyfinServer):
        adapter = JellyfinExternalAdapter()
        await adapter.validate_config({"server_url": _BASE, "username": "u", "password": "p", "verify_ssl": False})
        auth_requests = [r for r in jellyfin_client.requests if "/AuthenticateByName" in r["path"]]
        assert auth_requests
        assert auth_requests[0]["json"] == {"Username": "u", "Pw": "p"}


# ---------------------------------------------------------------------------
# iter_items
# ---------------------------------------------------------------------------


class TestIterItems:
    @pytest.mark.asyncio
    async def test_track_metadata_mapping(self, jellyfin_client: _FakeJellyfinServer):
        jellyfin_client.items = [_track_item()]
        adapter = JellyfinExternalAdapter()
        items = await _collect(adapter.iter_items(_config()))
        assert len(items) == 1
        item = items[0]
        assert item.provider_key == "track-1"
        assert item.etag == "etag-1"
        assert item.size == 12_345_678
        assert item.mime_type == "audio/x-flac"
        assert item.display_path == "The Band/An Album/03 A Song.flac"
        metadata = item.metadata
        assert metadata is not None
        assert metadata.title == "A Song"
        assert metadata.artist == "The Band"
        assert metadata.artists == ("The Band", "Guest Singer")
        assert metadata.album == "An Album"
        assert metadata.album_artist == "The Band"
        assert metadata.track_number == 3
        assert metadata.disc_number == 1
        assert metadata.duration == pytest.approx(185.0)
        assert metadata.release_year == 2001
        assert metadata.genres == ("Rock", "Indie")
        assert metadata.musicbrainz_id == "mb-track-1"
        assert metadata.provider_ids["MusicBrainzAlbum"] == "mb-album-1"
        assert metadata.cover_url == f"{_BASE}/Items/track-1/Images/Primary"

    @pytest.mark.asyncio
    async def test_sends_since_filter(self, jellyfin_client: _FakeJellyfinServer):
        from datetime import datetime, timezone

        adapter = JellyfinExternalAdapter()
        await _collect(adapter.iter_items(_config(), since=datetime(2024, 5, 1, tzinfo=timezone.utc)))
        items_requests = [
            r
            for r in jellyfin_client.requests
            if r["path"].endswith("/Items") and r["params"].get("IncludeItemTypes") == "Audio"
        ]
        assert items_requests
        assert items_requests[0]["params"]["MinDateLastSaved"] == "20240501000000"

    @pytest.mark.asyncio
    async def test_collections_filter_by_parent_id(self, jellyfin_client: _FakeJellyfinServer):
        jellyfin_client.media_folders = [
            {"Id": "lib-music", "Name": "Music"},
            {"Id": "lib-other", "Name": "Audiobooks"},
        ]
        adapter = JellyfinExternalAdapter()
        await _collect(adapter.iter_items(_config(collections=["Music"])))
        media_requests = [r for r in jellyfin_client.requests if r["path"].endswith("/Library/MediaFolders")]
        assert media_requests
        items_requests = [
            r
            for r in jellyfin_client.requests
            if r["path"].endswith("/Items") and r["params"].get("IncludeItemTypes") == "Audio"
        ]
        assert items_requests
        assert items_requests[0]["params"]["ParentId"] == "lib-music"

    @pytest.mark.asyncio
    async def test_sends_api_key_header(self, jellyfin_client: _FakeJellyfinServer):
        adapter = JellyfinExternalAdapter()
        await _collect(adapter.iter_items(_config()))
        items_requests = [r for r in jellyfin_client.requests if r["path"].endswith("/Items")]
        assert items_requests
        assert items_requests[0]["headers"]["X-Emby-Token"] == "test-api-key"


# ---------------------------------------------------------------------------
# Entity iterators
# ---------------------------------------------------------------------------


class TestEntityIterators:
    @pytest.mark.asyncio
    async def test_iter_albums(self, jellyfin_client: _FakeJellyfinServer):
        jellyfin_client.albums = [
            {
                "Id": "album-1",
                "Name": "An Album",
                "AlbumArtists": [{"Name": "The Band", "Id": "artist-1"}],
                "ProductionYear": 2001,
                "Genres": ["Rock"],
                "ProviderIds": {"MusicBrainzAlbum": "mb-album-1"},
                "ImageTags": {"Primary": "tag"},
                "Etag": "etag-a",
            }
        ]
        adapter = JellyfinExternalAdapter()
        albums = await _collect(adapter.iter_albums(_config()))
        assert len(albums) == 1
        album = albums[0]
        assert album.provider_key == "album-1"
        assert album.title == "An Album"
        assert album.artist_names == ("The Band",)
        assert album.release_year == 2001
        assert album.genres == ("Rock",)
        assert album.cover_url == f"{_BASE}/Items/album-1/Images/Primary"
        assert album.provider_ids["MusicBrainzAlbum"] == "mb-album-1"

    @pytest.mark.asyncio
    async def test_iter_artists(self, jellyfin_client: _FakeJellyfinServer):
        jellyfin_client.artists = [
            {
                "Id": "artist-1",
                "Name": "The Band",
                "Overview": "A band.",
                "ProviderIds": {"MusicBrainzArtist": "mb-artist-1"},
                "ImageTags": {"Primary": "tag"},
                "Etag": "etag-ar",
            }
        ]
        adapter = JellyfinExternalAdapter()
        artists = await _collect(adapter.iter_artists(_config()))
        assert len(artists) == 1
        artist = artists[0]
        assert artist.provider_key == "artist-1"
        assert artist.name == "The Band"
        assert artist.bio == "A band."
        assert artist.image_url == f"{_BASE}/Items/artist-1/Images/Primary"
        assert artist.provider_ids["MusicBrainzArtist"] == "mb-artist-1"

    @pytest.mark.asyncio
    async def test_iter_playlists_preserves_order(self, jellyfin_client: _FakeJellyfinServer):
        jellyfin_client.playlists = [{"Id": "pl-1", "Name": "Mix", "Overview": "desc", "Etag": "etag-p"}]
        jellyfin_client.playlist_entries = {
            "pl-1": [{"Id": "t-b"}, {"Id": "t-a"}, {"Id": "t-c"}],
        }
        adapter = JellyfinExternalAdapter()
        playlists = await _collect(adapter.iter_playlists(_config()))
        assert len(playlists) == 1
        playlist = playlists[0]
        assert playlist.provider_key == "pl-1"
        assert playlist.title == "Mix"
        assert playlist.description == "desc"
        assert [e.track_provider_key for e in playlist.entries] == ["t-b", "t-a", "t-c"]
        assert [e.position for e in playlist.entries] == [0, 1, 2]


# ---------------------------------------------------------------------------
# Streaming / download
# ---------------------------------------------------------------------------


class TestStreaming:
    @pytest.mark.asyncio
    async def test_open_stream_proxied_url(self, jellyfin_client: _FakeJellyfinServer):
        adapter = JellyfinExternalAdapter()
        item = ExternalItemRef(provider_key="track-1", display_path="track-1.flac", mime_type="audio/x-flac", size=100)
        stream = await adapter.open_stream(_config(), item)
        assert stream.kind == "url"
        assert stream.url == f"{_BASE}/Audio/track-1/stream"
        assert stream.headers["X-Emby-Token"] == "test-api-key"
        assert stream.safe_to_redirect is False
        assert stream.supports_range is True
        assert stream.size == 100

    @pytest.mark.asyncio
    async def test_open_stream_forwards_range(self, jellyfin_client: _FakeJellyfinServer):
        adapter = JellyfinExternalAdapter()
        item = ExternalItemRef(provider_key="track-1", display_path="track-1.flac", mime_type="audio/x-flac", size=1000)
        stream = await adapter.open_stream(_config(), item, range=(0, 499))
        assert stream.headers["Range"] == "bytes=0-499"
        assert stream.content_range == "bytes 0-499/1000"

    @pytest.mark.asyncio
    async def test_download_returns_stream(self, jellyfin_client: _FakeJellyfinServer):
        adapter = JellyfinExternalAdapter()
        item = ExternalItemRef(provider_key="track-1", display_path="track-1.flac")
        stream = await adapter.download(_config(), item)
        assert stream.kind == "url"
        assert "/Audio/track-1/stream" in stream.url

    @pytest.mark.asyncio
    async def test_healthcheck(self, jellyfin_client: _FakeJellyfinServer):
        adapter = JellyfinExternalAdapter()
        health = await adapter.healthcheck(_config())
        assert health.ok is True


# ---------------------------------------------------------------------------
# Unsupported operations
# ---------------------------------------------------------------------------


class TestUnsupportedOperations:
    @pytest.mark.asyncio
    async def test_unsupported_operations_raise(self):
        adapter = JellyfinExternalAdapter()
        item = ExternalItemRef(provider_key="track-1", display_path="track-1.flac")
        with pytest.raises(UnsupportedExternalOperation):
            await adapter.compute_sha256({}, item)
        with pytest.raises(UnsupportedExternalOperation):
            await adapter.write_metadata({}, item, None)  # type: ignore[arg-type]
        with pytest.raises(UnsupportedExternalOperation):
            await adapter.rename_source({}, item, "new")
        with pytest.raises(UnsupportedExternalOperation):
            await adapter.delete_source({}, item)
        with pytest.raises(UnsupportedExternalOperation):
            await adapter.read_metadata({}, item)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def test_jellyfin_registered():
    from songhive.external.registry import get_external_adapter

    assert get_external_adapter("jellyfin") is JellyfinExternalAdapter
