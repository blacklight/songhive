"""Tests for the YouTube external-library adapter (offline, fake clients)."""

from __future__ import annotations

import json
from typing import Any, Optional

import pytest

from songhive.external._youtube import YouTubeExternalAdapter
from songhive.external._youtube import cookies as yt_cookies
from songhive.external._youtube.conf import (
    api_mode,
    download_format,
    downloads_allowed,
    playlist_ttl,
    video_format,
)
from songhive.external._youtube.mapping import (
    map_album,
    map_artist,
    map_playlist,
    map_playlist_items,
    map_track,
    map_track_ref,
    parse_hms_duration,
    parse_iso8601_duration,
    parse_youtube_url,
    pick_thumbnail,
)
from songhive.external._youtube.session import auth_mode
from songhive.external.errors import ExternalConfigError, ExternalPermissionDenied
from songhive.external.types import ExternalItemRef


def _oauth_config(**overrides) -> dict:
    base = {
        "auth_mode": "oauth",
        "access_token": "at",
        "refresh_token": "rt",
        "token_type": "Bearer",
        "client_id": "cid",
        "client_secret": "csec",
        "expiry_time": 9_999_999_999,
    }
    base.update(overrides)
    return base


def _browser_config(**overrides) -> dict:
    base = {
        "auth_mode": "browser",
        "request_headers": "Cookie: SID=abc; HSID=def\nX-Goog-AuthUser: 0\nUser-Agent: UA",
    }
    base.update(overrides)
    return base


# ----------------------------------------------------------------------
# URL parsing
# ----------------------------------------------------------------------


def test_parse_youtube_url_watch_variants():
    assert parse_youtube_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == (
        "track",
        "dQw4w9WgXcQ",
    )
    assert parse_youtube_url("https://youtu.be/dQw4w9WgXcQ") == ("track", "dQw4w9WgXcQ")
    assert parse_youtube_url("https://music.youtube.com/watch?v=dQw4w9WgXcQ") == (
        "track",
        "dQw4w9WgXcQ",
    )
    assert parse_youtube_url("https://www.youtube.com/shorts/dQw4w9WgXcQ") == (
        "track",
        "dQw4w9WgXcQ",
    )
    assert parse_youtube_url("dQw4w9WgXcQ") == ("track", "dQw4w9WgXcQ")


def test_parse_youtube_url_playlist_and_channel():
    assert parse_youtube_url("https://www.youtube.com/playlist?list=PLabc") == (
        "playlist",
        "PLabc",
    )
    assert parse_youtube_url("https://www.youtube.com/channel/UCabcdef") == (
        "artist",
        "UCabcdef",
    )
    assert parse_youtube_url("https://www.youtube.com/@somehandle") == (
        "artist",
        "@somehandle",
    )


def test_parse_youtube_url_rejects_non_youtube():
    assert parse_youtube_url("https://example.com/watch?v=x") is None
    assert parse_youtube_url("just some text") is None
    assert parse_youtube_url("") is None
    assert parse_youtube_url(None) is None


# ----------------------------------------------------------------------
# Duration / thumbnail helpers
# ----------------------------------------------------------------------


def test_duration_parsers():
    assert parse_hms_duration("3:12") == 192.0
    assert parse_hms_duration("1:02:03") == 3723.0
    assert parse_hms_duration("bogus") is None
    assert parse_iso8601_duration("PT4M13S") == 253.0
    assert parse_iso8601_duration("PT1H2M3S") == 3723.0
    assert parse_iso8601_duration("P1D") == 86400.0
    assert parse_iso8601_duration("nope") is None


def test_pick_thumbnail_prefers_largest():
    thumbs = [
        {"url": "https://i/s.jpg", "width": 60, "height": 60},
        {"url": "https://i/l.jpg", "width": 480, "height": 360},
        {"url": "not-a-url", "width": 9999, "height": 9999},
    ]
    assert pick_thumbnail(thumbs) == "https://i/l.jpg"
    assert pick_thumbnail(None) is None
    assert pick_thumbnail([]) is None


# ----------------------------------------------------------------------
# Track mapping
# ----------------------------------------------------------------------


def test_map_track_ytmusic_shape():
    meta = map_track(
        {
            "videoId": "vid001",
            "title": "Great Song",
            "artists": [{"name": "The Band", "id": "UC9"}],
            "album": {"name": "The Album", "id": "MPRE1"},
            "duration": "3:12",
            "year": 2020,
            "thumbnails": [{"url": "https://i/t.jpg", "width": 226, "height": 226}],
        }
    )
    assert meta is not None
    assert meta.title == "Great Song"
    assert meta.artist == "The Band"
    assert meta.artists == ("The Band",)
    assert meta.album == "The Album"
    assert meta.duration == 192.0
    assert meta.release_year == 2020
    assert meta.cover_url == "https://i/t.jpg"
    assert meta.provider_ids == {"youtube": "vid001"}
    assert meta.artist_provider_key == "UC9"
    assert meta.album_artist_provider_key == "MPRE1"


def test_map_track_data_api_shape():
    meta = map_track(
        {
            "id": "vid002",
            "snippet": {
                "title": "API Video",
                "channelTitle": "Channel Name",
                "channelId": "UCx",
                "publishedAt": "2021-05-01T00:00:00Z",
                "description": "long text",
                "tags": ["music", "rock"],
                "thumbnails": {"high": {"url": "https://i/h.jpg"}},
            },
            "contentDetails": {"duration": "PT4M13S"},
        }
    )
    assert meta is not None
    assert meta.title == "API Video"
    assert meta.artist == "Channel Name"
    assert meta.duration == 253.0
    assert meta.release_year == 2021
    assert meta.cover_url == "https://i/h.jpg"
    assert meta.genres == ("music", "rock")
    assert meta.provider_ids == {"youtube": "vid002"}
    assert meta.artist_provider_key == "UCx"


def test_map_track_playlist_item_shape():
    meta = map_track(
        {
            "snippet": {
                "title": "Nested Video",
                "channelTitle": "Ch",
                "channelId": "UCn",
                "resourceId": {"kind": "youtube#video", "videoId": "vid003"},
            },
            "contentDetails": {"videoId": "vid003"},
            "_source": "youtube_api",
        }
    )
    assert meta is not None
    assert meta.provider_ids == {"youtube": "vid003"}


def test_map_track_ytdlp_shape():
    meta = map_track(
        {
            "id": "vid004",
            "title": "DLP Video",
            "channel": "Uploader",
            "channel_id": "UCd",
            "duration": 61,
            "upload_date": "20190210",
            "thumbnail": "https://i/d.jpg",
            "categories": ["Music"],
            "webpage_url": "https://www.youtube.com/watch?v=vid004",
        }
    )
    assert meta is not None
    assert meta.title == "DLP Video"
    assert meta.artist == "Uploader"
    assert meta.duration == 61.0
    assert meta.release_year == 2019
    assert meta.genres == ("Music",)
    assert meta.artist_provider_key == "UCd"


def test_map_track_ref_builds_display_path():
    ref = map_track_ref(
        {
            "videoId": "vid005",
            "title": "A/B Song",
            "artists": [{"name": "Chan"}],
        }
    )
    assert ref is not None
    assert ref.provider_key == "vid005"
    assert ref.display_path == "Chan/A_B Song.m4a"
    assert ref.metadata is not None and ref.metadata.title == "A/B Song"


def test_map_track_missing_id_returns_none():
    assert map_track({"_source": "ytmusic", "title": "no id"}) is None
    assert map_track({"_source": "youtube_api", "snippet": {}}) is None
    assert map_track("not a dict") is None


# ----------------------------------------------------------------------
# Artist (channel) mapping
# ----------------------------------------------------------------------


def test_map_artist_ytmusic_subscription():
    artist = map_artist(
        {
            "browseId": "UCsub",
            "artist": "Subscribed Channel",
            "subscribers": "1M",
            "thumbnails": [{"url": "https://i/c.jpg", "width": 60, "height": 60}],
        }
    )
    assert artist is not None
    assert artist.provider_key == "UCsub"
    assert artist.name == "Subscribed Channel"
    assert artist.image_url == "https://i/c.jpg"
    assert artist.provider_ids == {"youtube": "UCsub"}


def test_map_artist_data_api_subscription():
    artist = map_artist(
        {
            "snippet": {
                "title": "Chan",
                "resourceId": {"kind": "youtube#channel", "channelId": "UC10"},
                "thumbnails": {"medium": {"url": "https://i/m.jpg"}},
                "description": "bio",
            }
        }
    )
    assert artist is not None
    assert artist.provider_key == "UC10"
    assert artist.name == "Chan"
    assert artist.image_url == "https://i/m.jpg"
    assert artist.bio == "bio"


# ----------------------------------------------------------------------
# Album / playlist mapping
# ----------------------------------------------------------------------


def test_map_album_ytmusic():
    album = map_album(
        {
            "browseId": "MPRE1",
            "title": "Album Title",
            "artists": [{"name": "The Band", "id": "UCa"}],
            "year": 2022,
            "thumbnails": [{"url": "https://i/a.jpg", "width": 544, "height": 544}],
        }
    )
    assert album is not None
    assert album.provider_key == "MPRE1"
    assert album.title == "Album Title"
    assert album.artist_names == ("The Band",)
    assert album.artist_provider_keys == ("UCa",)
    assert album.release_year == 2022


def test_map_playlist_ytmusic():
    playlist = map_playlist(
        {
            "playlistId": "PL1",
            "title": "Favorites",
            "description": "desc",
            "author": {"name": "Me"},
            "thumbnails": [{"url": "https://i/p.jpg", "width": 192, "height": 192}],
        }
    )
    assert playlist is not None
    assert playlist.provider_key == "PL1"
    assert playlist.title == "Favorites"
    assert playlist.owner_name == "Me"


def test_map_playlist_data_api():
    playlist = map_playlist(
        {
            "id": "PL2",
            "etag": "etag-123",
            "snippet": {
                "title": "API Playlist",
                "channelTitle": "Owner",
                "thumbnails": {"standard": {"url": "https://i/s.jpg"}},
            },
        }
    )
    assert playlist is not None
    assert playlist.provider_key == "PL2"
    assert playlist.etag == "etag-123"
    assert playlist.owner_name == "Owner"


def test_map_playlist_items_ytmusic():
    payload = {
        "_source": "ytmusic",
        "tracks": [
            {"videoId": "v1", "title": "One"},
            {"videoId": "v2", "title": "Two", "isAvailable": False},
            {"videoId": None},
            {"videoId": "v3", "title": "Three"},
        ],
    }
    entries = map_playlist_items(payload)
    assert [(pos, key) for pos, key, _ in entries] == [(0, "v1"), (1, "v3")]


def test_map_playlist_items_data_api():
    payload = {
        "_source": "youtube_api",
        "items": [
            {"contentDetails": {"videoId": "va"}},
            {"snippet": {"resourceId": {"videoId": "vb"}}, "contentDetails": {}},
            {"snippet": {}},
        ],
    }
    entries = map_playlist_items(payload)
    assert [(pos, key) for pos, key, _ in entries] == [(0, "va"), (1, "vb")]


# ----------------------------------------------------------------------
# Cookies
# ----------------------------------------------------------------------


def test_parse_request_headers():
    headers = yt_cookies.parse_request_headers("Cookie: A=1; B=2\nX-Goog-AuthUser: 0\nAccept: */*\n# comment\n")
    assert headers["cookie"] == "A=1; B=2"
    assert headers["x-goog-authuser"] == "0"


def test_auth_headers_for_config_filters():
    headers = yt_cookies.auth_headers_for_config(
        {"request_headers": "Cookie: A=1\nSec-Fetch-Mode: cors\nUser-Agent: UA"}
    )
    assert headers is not None
    assert headers["cookie"] == "A=1"
    assert headers["user-agent"] == "UA"
    assert "sec-fetch-mode" not in headers
    assert headers["x-goog-authuser"] == "0"


def test_netscape_from_cookie_header():
    text = yt_cookies.cookie_header_to_netscape("A=1; B=two; FLAG")
    assert "Netscape" in text
    assert ".youtube.com\tTRUE\t/\tTRUE\t0\tA\t1" in text
    assert ".youtube.com\tTRUE\t/\tTRUE\t0\tB\ttwo" in text
    assert "FLAG" not in text


def test_netscape_cookies_for_config_prefers_explicit():
    cfg = {"cookies": "SID=xyz; OTHER=1"}
    text = yt_cookies.netscape_cookies_for_config(cfg)
    assert text is not None and "\tSID\txyz" in text


def test_netscape_cookies_passthrough():
    original = "# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t0\tA\t1\n"
    assert yt_cookies.netscape_cookies_for_config({"cookies": original}) == original


def test_netscape_cookies_from_request_headers():
    text = yt_cookies.netscape_cookies_for_config(_browser_config())
    assert text is not None
    assert "\tSID\tabc" in text
    assert "\tHSID\tdef" in text


def test_cookie_file_for_ytdlp():
    stream = yt_cookies.cookie_file_for_ytdlp(_browser_config())
    assert stream is not None
    stream.seek(0)
    assert "SID\tabc" in stream.read()
    assert yt_cookies.cookie_file_for_ytdlp(_oauth_config()) is None


def test_session_material_is_redacted_in_responses():
    """Pasted browser sessions are credentials: never return them raw."""
    from songhive.services.secrets import redact_config

    config = _browser_config(cookies="A=1; B=2")
    redacted = redact_config(config)
    assert redacted["request_headers"] == "<redacted>"
    assert redacted["cookies"] == "<redacted>"


# ----------------------------------------------------------------------
# Config helpers
# ----------------------------------------------------------------------


def test_api_mode_and_download_format(monkeypatch):
    monkeypatch.setattr(
        "songhive.external._youtube.conf.instance_youtube_config",
        lambda: type("Cfg", (), {"download_format": "audio"})(),
    )
    assert api_mode({}) == "auto"
    assert api_mode({"api_mode": "MUSIC"}) == "music"
    assert api_mode({"api_mode": "bogus"}) == "auto"
    assert download_format({}) == "audio"
    assert download_format({"download_format": "video"}) == "video"
    assert download_format({"download_format": "bogus"}) == "audio"


def test_playlist_ttl_clamps_to_instance_minimum(monkeypatch):
    instance = type(
        "Cfg",
        (),
        {"minimum_playlist_ttl_seconds": 300, "playlist_ttl_seconds": 21600},
    )()
    monkeypatch.setattr("songhive.external._youtube.conf.instance_youtube_config", lambda: instance)
    assert playlist_ttl({}) == 21600
    assert playlist_ttl({"playlist_ttl_seconds": 60}) == 300
    assert playlist_ttl({"playlist_ttl_seconds": 999999}) == 999999
    assert playlist_ttl({"playlist_ttl_seconds": "junk"}) == 21600


def test_downloads_allowed_instance_flag(monkeypatch):
    monkeypatch.setattr(
        "songhive.external._youtube.conf.instance_youtube_config",
        lambda: type("Cfg", (), {"allow_downloads": False})(),
    )
    assert downloads_allowed() is False
    monkeypatch.setattr(
        "songhive.external._youtube.conf.instance_youtube_config",
        lambda: type("Cfg", (), {"allow_downloads": True})(),
    )
    assert downloads_allowed() is True


def test_video_format_default_and_override(monkeypatch):
    monkeypatch.setattr(
        "songhive.external._youtube.conf.instance_youtube_config",
        lambda: type("Cfg", (), {"video_format": "best"})(),
    )
    assert video_format({}) == "best"
    assert video_format({"video_format": "bv"}) == "bv"


# ----------------------------------------------------------------------
# Session auth-mode detection
# ----------------------------------------------------------------------


def test_auth_mode_detection():
    assert auth_mode(_oauth_config()) == "oauth"
    assert auth_mode(_browser_config()) == "browser"
    assert auth_mode({"access_token": "x"}) == "oauth"
    assert auth_mode({"cookies": "A=1"}) == "browser"
    assert auth_mode({}) is None


# ----------------------------------------------------------------------
# Adapter
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_validate_config_requires_credentials():
    adapter = YouTubeExternalAdapter()
    with pytest.raises(ExternalConfigError):
        await adapter.validate_config({})


@pytest.mark.asyncio
async def test_validate_config_advertises_capabilities(monkeypatch):
    monkeypatch.setattr(
        YouTubeExternalAdapter,
        "_client",
        lambda self, config: _FakeYouTubeClient(mode="music"),
    )
    # Isolate from the instance's real config.toml — defaults apply.
    monkeypatch.setattr(
        "songhive.external._youtube.conf.instance_youtube_config",
        lambda: None,
    )
    adapter = YouTubeExternalAdapter()
    caps = await adapter.validate_config(_oauth_config())
    assert caps.stream_url is True
    assert caps.range_read is True
    assert caps.list_items is True
    assert caps.list_playlists is True
    assert caps.list_artists is True
    assert caps.limits["search"] is True
    assert caps.limits["lazy_contents"] == ["playlist", "album"]
    assert "video" in caps.limits["stream_variants"]


class _FakeYouTubeClient:
    """Stand-in for ``YouTubeClient`` returning scripted payloads."""

    def __init__(self, mode: str = "music", liked=None, playlists=None, contents=None):
        self._mode = mode
        self._liked = liked or []
        self._playlists = playlists or []
        self._contents = contents

    async def api_mode(self) -> str:
        return self._mode

    async def iter_liked_videos(self):
        for payload in self._liked:
            yield payload

    async def iter_playlists(self):
        for payload in self._playlists:
            yield payload

    async def iter_subscriptions(self):
        return
        yield  # pragma: no cover

    async def iter_albums(self):
        return
        yield  # pragma: no cover

    async def fetch_playlist_contents(self, provider_key):
        if self._contents is None:
            raise ExternalConfigError("no contents")
        return self._contents


@pytest.mark.asyncio
async def test_iter_items_maps_liked_videos(monkeypatch):
    client = _FakeYouTubeClient(
        liked=[
            {"videoId": "v1", "title": "One", "artists": [{"name": "A"}], "_source": "ytmusic"},
            {"videoId": None, "_source": "ytmusic"},
            {"videoId": "v2", "title": "Two", "_source": "ytmusic"},
        ]
    )
    monkeypatch.setattr(YouTubeExternalAdapter, "_client", lambda self, cfg: client)
    adapter = YouTubeExternalAdapter()
    refs = [ref async for ref in adapter.iter_items(_oauth_config())]
    assert [r.provider_key for r in refs] == ["v1", "v2"]
    assert refs[0].metadata.title == "One"


@pytest.mark.asyncio
async def test_iter_items_respects_include_tracks(monkeypatch):
    client = _FakeYouTubeClient(liked=[{"videoId": "v1", "_source": "ytmusic"}])
    monkeypatch.setattr(YouTubeExternalAdapter, "_client", lambda self, cfg: client)
    adapter = YouTubeExternalAdapter()
    refs = [ref async for ref in adapter.iter_items(_oauth_config(include_tracks=False))]
    assert refs == []


@pytest.mark.asyncio
async def test_iter_playlists(monkeypatch):
    client = _FakeYouTubeClient(playlists=[{"playlistId": "PL9", "title": "Mine", "_source": "ytmusic"}])
    monkeypatch.setattr(YouTubeExternalAdapter, "_client", lambda self, cfg: client)
    adapter = YouTubeExternalAdapter()
    playlists = [p async for p in adapter.iter_playlists(_oauth_config())]
    assert [p.provider_key for p in playlists] == ["PL9"]


@pytest.mark.asyncio
async def test_iter_contents_playlist(monkeypatch):
    client = _FakeYouTubeClient(
        contents={
            "_source": "ytmusic",
            "tracks": [
                {"videoId": "va", "title": "A"},
                {"videoId": "vb", "title": "B"},
            ],
        }
    )
    monkeypatch.setattr(YouTubeExternalAdapter, "_client", lambda self, cfg: client)
    adapter = YouTubeExternalAdapter()
    contents = await adapter.iter_contents(_oauth_config(), "playlist", "PL9")
    assert [(e.position, e.provider_key) for e in contents.entries] == [
        (0, "va"),
        (1, "vb"),
    ]
    assert contents.entries[0].metadata.title == "A"


@pytest.mark.asyncio
async def test_entity_from_payload():
    adapter = YouTubeExternalAdapter()
    entity = adapter.entity_from_payload(
        {},
        "track",
        {"videoId": "v9", "title": "T", "_source": "ytmusic"},
    )
    assert entity is not None and entity.provider_key == "v9"
    assert adapter.entity_from_payload({}, "track", "junk") is None
    assert adapter.entity_from_payload({}, "bogus", {}) is None


def test_external_url():
    adapter = YouTubeExternalAdapter()
    assert adapter.external_url("track", "v1") == "https://www.youtube.com/watch?v=v1"
    assert adapter.external_url("playlist", "PL1") == ("https://www.youtube.com/playlist?list=PL1")
    assert adapter.external_url("artist", "UC1") == "https://www.youtube.com/channel/UC1"
    assert adapter.external_url("bogus", "x") is None


@pytest.mark.asyncio
async def test_download_gated_by_instance_flag(monkeypatch):
    monkeypatch.setattr(
        "songhive.external._youtube.conf.instance_youtube_config",
        lambda: type("Cfg", (), {"allow_downloads": False})(),
    )
    adapter = YouTubeExternalAdapter()
    item = ExternalItemRef(provider_key="v1", display_path="a/b.m4a")
    with pytest.raises(ExternalPermissionDenied):
        await adapter.download(_oauth_config(), item)


@pytest.mark.asyncio
async def test_open_stream_audio_variant(monkeypatch):
    adapter = YouTubeExternalAdapter()
    captured: dict[str, Any] = {}

    async def _fake_open(config, item, *, variant="audio", range=None, redis=None):
        captured["variant"] = variant
        from songhive.external.types import ExternalStream

        return ExternalStream(kind="url", url="https://gv/x", content_type="audio/mp4")

    monkeypatch.setattr("songhive.external._youtube.stream.open_stream", _fake_open)
    item = ExternalItemRef(provider_key="v1", display_path="a/b.m4a")
    stream = await adapter.open_stream(_oauth_config(), item)
    assert captured["variant"] == "audio"
    assert stream.url == "https://gv/x"

    item_video = ExternalItemRef(provider_key="v1", display_path="a/b.m4a", variant="video")
    await adapter.open_stream(_oauth_config(), item_video)
    assert captured["variant"] == "video"


@pytest.mark.asyncio
async def test_search_url_resolves_track(monkeypatch):
    adapter = YouTubeExternalAdapter()

    async def _fake_payload(self, config, kind, provider_key):
        assert kind == "track" and provider_key == "vid001"
        return {"videoId": "vid001", "title": "URL Song", "_source": "ytmusic"}

    monkeypatch.setattr(YouTubeExternalAdapter, "fetch_entity_payload", _fake_payload)
    results = await adapter.search(_oauth_config(), "https://youtu.be/vid001")
    assert len(results) == 1
    assert results[0]["kind"] == "track"
    assert results[0]["provider_key"] == "vid001"
    assert results[0]["title"] == "URL Song"


@pytest.mark.asyncio
async def test_search_text_uses_catalog(monkeypatch):
    client = _FakeYouTubeClient()
    client.search = _fake_search  # type: ignore[attr-defined]
    monkeypatch.setattr(YouTubeExternalAdapter, "_client", lambda self, cfg: client)
    adapter = YouTubeExternalAdapter()
    results = await adapter.search(_oauth_config(), "some song")
    assert results == [
        {
            "kind": "track",
            "provider_key": "sv1",
            "title": "Found",
            "subtitle": "Chan",
            "image_url": "https://i/s.jpg",
            "external_url": "https://www.youtube.com/watch?v=sv1",
        }
    ]


async def _fake_search(query, limit=20):
    return [
        {
            "videoId": "sv1",
            "title": "Found",
            "artists": [{"name": "Chan"}],
            "thumbnails": [{"url": "https://i/s.jpg", "width": 60}],
            "_source": "ytmusic",
            "_kind": "track",
        }
    ]


@pytest.mark.asyncio
async def test_search_foreign_url_returns_empty(monkeypatch):
    """A non-YouTube URL is a lookup, not a text query — no catalog search."""
    adapter = YouTubeExternalAdapter()

    def _no_client(self, cfg):
        raise AssertionError("client must not be built for foreign URLs")

    monkeypatch.setattr(YouTubeExternalAdapter, "_client", _no_client)
    results = await adapter.search(_oauth_config(), "https://bandcamp.example/track/x")
    assert results == []


# ----------------------------------------------------------------------
# Data API client
# ----------------------------------------------------------------------


class _FakeHttpResponse:
    def __init__(self, payload: Any, status_code: int = 200, headers: Optional[dict] = None):
        self._payload = payload
        self.status_code = status_code
        self.headers = headers or {}

    def json(self):
        return self._payload


def test_data_api_error_translation():
    from songhive.external._youtube.api import _raise_for_status
    from songhive.external.errors import (
        ExternalItemNotFound,
        ExternalLibraryError,
        ExternalPermissionDenied,
        ExternalRateLimited,
    )

    with pytest.raises(ExternalRateLimited):
        _raise_for_status(
            _FakeHttpResponse({"error": {"errors": [{"reason": "quotaExceeded"}]}}, 403),
            "op",
        )
    with pytest.raises(ExternalItemNotFound):
        _raise_for_status(_FakeHttpResponse({}, 404), "op")
    with pytest.raises(ExternalPermissionDenied):
        _raise_for_status(_FakeHttpResponse({}, 401), "op")
    with pytest.raises(ExternalPermissionDenied):
        _raise_for_status(_FakeHttpResponse({"error": {"errors": [{"reason": "forbidden"}]}}, 403), "op")
    with pytest.raises(ExternalLibraryError):
        _raise_for_status(_FakeHttpResponse({}, 500), "op")
    _raise_for_status(_FakeHttpResponse({"ok": True}, 200), "op")


# ----------------------------------------------------------------------
# yt-dlp helpers
# ----------------------------------------------------------------------


def test_stream_descriptor_round_trip():
    from songhive.external._youtube.ytdlp import StreamDescriptor, mime_for

    descriptor = StreamDescriptor(
        url="https://gv/x",
        http_headers={"User-Agent": "UA"},
        ext="m4a",
        mime=mime_for("m4a", variant="audio"),
        size=1234,
        expires_at=9999999999.0,
    )
    restored = StreamDescriptor.from_dict(json.loads(json.dumps(descriptor.to_dict())))
    assert restored.url == "https://gv/x"
    assert restored.http_headers == {"User-Agent": "UA"}
    assert restored.mime == "audio/mp4"
    assert restored.size == 1234


def test_mime_for_variants():
    from songhive.external._youtube.ytdlp import mime_for

    assert mime_for("m4a", variant="audio") == "audio/mp4"
    assert mime_for("webm", variant="audio") == "audio/webm"
    assert mime_for("webm", variant="video") == "video/webm"
    assert mime_for("mp4", variant="video") == "video/mp4"
    assert mime_for("weird", variant="audio") == "audio/mp4"


def test_ydl_params_attaches_cookies():
    from songhive.external._youtube.ytdlp import ydl_params

    params = ydl_params(_browser_config())
    assert "cookiefile" in params
    params_anon = ydl_params({})
    assert "cookiefile" not in params_anon


def test_translate_ydlp_error():
    from songhive.external._youtube.ytdlp import _translate_ydlp_error
    from songhive.external.errors import (
        ExternalItemNotFound,
        ExternalLibraryError,
        ExternalPermissionDenied,
    )

    assert isinstance(
        _translate_ydlp_error(Exception("Sign in to confirm you're not a bot"), "v"),
        ExternalPermissionDenied,
    )
    assert isinstance(_translate_ydlp_error(Exception("Video is private"), "v"), ExternalItemNotFound)
    assert isinstance(_translate_ydlp_error(Exception("mystery"), "v"), ExternalLibraryError)
