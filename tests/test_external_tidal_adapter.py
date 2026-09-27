"""Tests for the TIDAL external-library adapter (offline, fake session)."""

from __future__ import annotations

from typing import Any, Optional

import pytest

from songhive.external._tidal import TidalExternalAdapter
from songhive.external._tidal.api import NotModified, TidalApiClient, is_allowed_media_url
from songhive.external._tidal.mapping import (
    map_album,
    map_artist,
    map_playlist,
    map_playlist_items,
    map_track,
    track_display_path,
)
from songhive.external._tidal.session import effective_quality
from songhive.external.errors import (
    ExternalItemNotFound,
    ExternalPermissionDenied,
    ExternalRateLimited,
)
from songhive.external.types import ExternalItemRef


class _FakeResponse:
    def __init__(self, payload: Any, status_code: int = 200, headers: Optional[dict] = None):
        self._payload = payload
        self.status_code = status_code
        self.headers = headers or {}
        self.ok = status_code < 400

    def json(self):
        return self._payload

    def raise_for_status(self):
        if not self.ok:
            import requests

            raise requests.HTTPError(response=self)


class _FakeRequester:
    """Stand-in for ``session.request`` returning scripted responses."""

    def __init__(self, responses: dict):
        self.responses = responses
        self.calls: list[tuple[str, str, dict]] = []

    def request(self, method, path, params=None, data=None, headers=None):
        self.calls.append((method, path, dict(params or {})))
        entry = self.responses.get(path)
        if entry is None:
            return _FakeResponse({}, 404)
        if isinstance(entry, Exception):
            raise entry
        return entry


class _FakeUser:
    def __init__(self, user_id: int):
        self.id = user_id
        self.username = "tidaluser"
        self.email = "u@example.com"


class FakeTidalSession:
    """Minimal stand-in for ``tidalapi.Session`` used by the adapter tests."""

    def __init__(self, responses: Optional[dict] = None, user_id: int = 4242):
        self.request = _FakeRequester(responses or {})
        self.user = _FakeUser(user_id)
        self.access_token = "at"
        self.refresh_token = "rt"
        self.token_type = "Bearer"
        self.is_pkce = False
        self.config = type("Config", (), {"quality": "LOSSLESS", "item_limit": 1000})()


def _config(**overrides) -> dict:
    base = {
        "access_token": "at",
        "refresh_token": "rt",
        "token_type": "Bearer",
        "user_id": "4242",
        "country_code": "US",
        "expiry_time": 9_999_999_999,
        "is_pkce": False,
    }
    base.update(overrides)
    return base


def _adapter_with_session(session: FakeTidalSession, monkeypatch) -> TidalExternalAdapter:
    adapter = TidalExternalAdapter()
    monkeypatch.setattr(TidalExternalAdapter, "_session", lambda self, config: _async_return(session))
    return adapter


async def _async_return(value):
    return value


# ----------------------------------------------------------------------
# Mapping
# ----------------------------------------------------------------------


def _track_json(**overrides) -> dict:
    base = {
        "id": 12345,
        "title": "Song",
        "duration": 200,
        "trackNumber": 3,
        "volumeNumber": 1,
        "streamStartDate": "2020-01-01T00:00:00Z",
        "isrc": "USXYZ1234567",
        "artists": [
            {"id": 1, "name": "Main Artist", "type": "MAIN"},
            {"id": 2, "name": "Feat Artist", "type": "FEATURED"},
        ],
        "album": {
            "id": 99,
            "title": "The Album",
            "cover": "abc-def-123",
            "artist": {"id": 1, "name": "Main Artist"},
        },
    }
    base.update(overrides)
    return base


def test_map_track_multi_artist():
    m = map_track(_track_json())
    assert m.artist == "Main Artist"
    assert m.artists == ("Main Artist", "Feat Artist")
    assert m.album == "The Album"
    assert m.album_artists == ("Main Artist",)
    assert m.provider_ids["tidal"] == "12345"
    assert m.provider_ids["isrc"] == "USXYZ1234567"


def test_map_track_version_suffix():
    m = map_track(_track_json(version="Remastered"))
    assert m.title == "Song (Remastered)"
    # No double suffix when the title already contains it.
    m2 = map_track(_track_json(title="Song (Remastered)", version="Remastered"))
    assert m2.title == "Song (Remastered)"


def test_map_track_missing_album():
    m = map_track(_track_json(album=None))
    assert m.album == ""
    assert m.cover_url is None
    assert "tidal_album" not in m.provider_ids


def test_track_display_path():
    path = track_display_path(_track_json())
    assert path == "Main Artist/The Album/03 Song.flac"


def test_map_playlist_items_skips_videos_dense_positions():
    payload = {
        "items": [
            {"type": "track", "item": {"id": 10}},
            {"type": "video", "item": {"id": 20}},
            {"type": "track", "item": {"id": 30}},
        ]
    }
    entries = map_playlist_items(payload)
    assert [(pos, key) for pos, key, _ in entries] == [(0, "10"), (1, "30")]


def test_map_album_and_artist_and_playlist():
    album = map_album(
        {
            "id": 99,
            "title": "Alb",
            "cover": "c1-c2",
            "releaseDate": "2021-05-01",
            "artists": [{"id": 7, "name": "A", "type": "MAIN"}],
            "universal_product_number": "123456789012",
        }
    )
    assert album is not None
    assert album.provider_key == "99"
    assert album.artist_names == ("A",)
    assert album.artist_provider_keys == ("7",)
    assert album.release_year == 2021
    assert album.provider_ids["upc"] == "123456789012"

    artist = map_artist({"id": 7, "name": "A", "picture": "p1-p2"})
    assert artist is not None
    assert artist.image_url == "https://resources.tidal.com/images/p1/p2/750x750.jpg"

    playlist = map_playlist(
        {
            "uuid": "pl-1",
            "title": "Mix",
            "description": "d",
            "squareImage": "s1-s2",
            "creator": {"id": 4242, "name": "me"},
        }
    )
    assert playlist is not None
    assert playlist.provider_key == "pl-1"
    assert playlist.owner_name == "me"
    assert playlist.entries == ()


# ----------------------------------------------------------------------
# Api client: paging, errors, etags
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_iter_paged_envelope_and_termination():
    pages = {
        "users/4242/favorites/tracks": _FakeResponse(
            {
                "items": [{"item": {"id": 1}}, {"item": {"id": 2}}],
                "totalNumberOfItems": 3,
            }
        ),
    }

    session = FakeTidalSession(pages)
    client = TidalApiClient(session, max_rps=1000)

    # First page returns 2 of 3; the client requests a second page.
    responses = {0: pages["users/4242/favorites/tracks"]}
    calls = []

    def fake_request(method, path, params=None, data=None, headers=None):
        calls.append(dict(params or {}))
        offset = (params or {}).get("offset", 0)
        if offset == 0:
            return responses[0]
        return _FakeResponse({"items": [{"item": {"id": 3}}], "totalNumberOfItems": 3})

    session.request.request = fake_request  # type: ignore[method-assign]
    got = [entry async for entry in client.iter_paged("users/4242/favorites/tracks", item_path="item")]
    assert [e["id"] for e in got] == [1, 2, 3]
    assert len(calls) == 2
    assert calls[1]["offset"] == 2


@pytest.mark.asyncio
async def test_429_maps_to_rate_limited():
    import requests

    response = _FakeResponse({"error": "too many"}, 429, headers={"Retry-After": "7"})
    session = FakeTidalSession()

    def raise_429(*a, **k):
        err = requests.HTTPError(response=response)
        raise err

    session.request.request = raise_429  # type: ignore[method-assign]
    client = TidalApiClient(session, max_rps=1000)
    with pytest.raises(ExternalRateLimited) as exc_info:
        await client.get_json("tracks/1")
    assert exc_info.value.retry_after == 7.0


@pytest.mark.asyncio
async def test_404_maps_to_not_found():
    import requests

    response = _FakeResponse({}, 404)
    session = FakeTidalSession()

    def raise_404(*a, **k):
        raise requests.HTTPError(response=response)

    session.request.request = raise_404  # type: ignore[method-assign]
    client = TidalApiClient(session, max_rps=1000)
    with pytest.raises(ExternalItemNotFound):
        await client.get_json("tracks/nope")


@pytest.mark.asyncio
async def test_401_maps_to_permission_denied():
    import requests

    response = _FakeResponse({}, 401)
    session = FakeTidalSession()

    def raise_401(*a, **k):
        raise requests.HTTPError(response=response)

    session.request.request = raise_401  # type: ignore[method-assign]
    client = TidalApiClient(session, max_rps=1000)
    with pytest.raises(ExternalPermissionDenied):
        await client.get_json("sessions")


@pytest.mark.asyncio
async def test_if_none_match_304_raises_not_modified():
    session = FakeTidalSession()

    def return_304(method, path, params=None, data=None, headers=None):
        assert headers and headers.get("If-None-Match") == '"v1"'
        return _FakeResponse(None, 304)

    session.request.request = return_304  # type: ignore[method-assign]
    client = TidalApiClient(session, max_rps=1000)
    with pytest.raises(NotModified):
        await client.get_json("playlists/x/items", etag='"v1"')


def test_is_allowed_media_url():
    assert is_allowed_media_url("https://sp-pr-fa.audio.tidal.com/x.flac")
    assert is_allowed_media_url("https://resources.tidal.com/images/a/b.jpg")
    assert not is_allowed_media_url("http://sp-pr-fa.audio.tidal.com/x.flac")
    assert not is_allowed_media_url("https://evil.example.com/x.flac")
    assert is_allowed_media_url("https://cdn.example.com/x", extra_hosts=("cdn.example.com",))


def test_effective_quality_downgrades_hires_without_pkce():
    assert effective_quality(_config(quality="HI_RES_LOSSLESS")) == "LOSSLESS"
    assert effective_quality(_config(quality="HI_RES_LOSSLESS", is_pkce=True)) == "HI_RES_LOSSLESS"
    assert effective_quality(_config()) == "LOSSLESS"
    assert effective_quality(_config(quality="bogus")) == "LOSSLESS"


# ----------------------------------------------------------------------
# Adapter iter_* against a fake session
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_iter_items_maps_saved_tracks(monkeypatch):
    track = _track_json()
    session = FakeTidalSession(
        {"users/4242/favorites/tracks": _FakeResponse({"items": [{"item": track}], "totalNumberOfItems": 1})}
    )
    adapter = _adapter_with_session(session, monkeypatch)
    refs = [r async for r in adapter.iter_items(_config())]
    assert len(refs) == 1
    assert refs[0].provider_key == "12345"
    assert refs[0].metadata is not None
    assert refs[0].metadata.title == "Song"


@pytest.mark.asyncio
async def test_validate_config_requires_credentials():
    adapter = TidalExternalAdapter()
    from songhive.external.errors import ExternalConfigError

    with pytest.raises(ExternalConfigError):
        await adapter.validate_config({})


@pytest.mark.asyncio
async def test_validate_config_capabilities(monkeypatch):
    session = FakeTidalSession({"sessions": _FakeResponse({"sessionId": "s", "countryCode": "US", "userId": 4242})})
    adapter = _adapter_with_session(session, monkeypatch)
    caps = await adapter.validate_config(_config())
    assert caps.limits is not None
    assert caps.limits["entity_import"] is True
    assert "playlist" in caps.limits["lazy_contents"]
    assert caps.limits["immutable_tracks"] is True
    assert caps.limits["editable_fields"] == ["genres", "tags"]
    assert caps.stream_url is True


# ----------------------------------------------------------------------
# iter_contents (lazy container contents)
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_iter_contents_maps_entries_with_metadata(monkeypatch):
    session = FakeTidalSession(
        {
            "playlists/pl-uuid/items": _FakeResponse(
                {
                    "items": [
                        {"type": "track", "item": _track_json(id=1)},
                        {"type": "video", "item": {"id": 99}},
                        {"type": "track", "item": _track_json(id=2, title="Second")},
                    ],
                    "totalNumberOfItems": 3,
                },
                headers={"ETag": '"tag-1"'},
            )
        }
    )
    adapter = _adapter_with_session(session, monkeypatch)

    contents = await adapter.iter_contents(_config(), "playlist", "pl-uuid")
    assert contents.etag == '"tag-1"'
    # Video skipped, positions stay dense.
    assert [e.position for e in contents.entries] == [0, 1]
    assert [e.provider_key for e in contents.entries] == ["1", "2"]
    assert all(e.metadata is not None for e in contents.entries)
    assert contents.entries[0].metadata.title == "Song"


@pytest.mark.asyncio
async def test_iter_contents_304_raises_contents_not_modified(monkeypatch):
    from songhive.external.types import ContentsNotModified

    session = FakeTidalSession({"playlists/pl-uuid/items": _FakeResponse(None, 304)})
    adapter = _adapter_with_session(session, monkeypatch)

    with pytest.raises(ContentsNotModified):
        await adapter.iter_contents(_config(), "playlist", "pl-uuid", etag='"tag-1"')


@pytest.mark.asyncio
async def test_iter_contents_album_path(monkeypatch):
    session = FakeTidalSession(
        {
            "albums/77/items": _FakeResponse(
                {
                    "items": [
                        {"item": _track_json(id=10)},
                        {"item": _track_json(id=11)},
                    ],
                    "totalNumberOfItems": 2,
                }
            )
        }
    )
    adapter = _adapter_with_session(session, monkeypatch)

    contents = await adapter.iter_contents(_config(), "album", "77")
    assert [e.provider_key for e in contents.entries] == ["10", "11"]
    method, path, _ = session.request.calls[0]
    assert path == "albums/77/items"


# ----------------------------------------------------------------------
# download() — tagged FLAC/AAC via the shared remux pipeline
# ----------------------------------------------------------------------


def _patch_remux_pipeline(monkeypatch, captured: dict, *, cover: bool = False):
    """Stub the network + ffmpeg layers so download() runs offline."""
    from pathlib import Path

    from songhive.external._tidal import remux as tidal_remux
    from songhive.external._tidal import stream as tidal_stream

    async def _manifest(config, track_id, quality, redis):
        captured["quality"] = quality
        return captured["manifest"]

    async def _fetch(url, output, *, timeout, max_bytes):
        captured.setdefault("fetches", []).append(url)
        output.write_bytes(b"AUDIO")
        return 5

    async def _cover(url, workdir, *, timeout):
        captured["cover_url"] = url
        if not cover:
            return None
        path = workdir / "cover.jpg"
        path.write_bytes(b"JPEG")
        return path

    async def _ffmpeg(args, *, timeout):
        captured.setdefault("ffmpeg", []).append(args)
        Path(args[-1]).write_bytes(b"TAGGED")

    monkeypatch.setattr(tidal_stream, "_resolve_manifest", _manifest)
    monkeypatch.setattr(tidal_remux, "_fetch_url_to_file", _fetch)
    monkeypatch.setattr(tidal_remux, "_fetch_cover", _cover)
    monkeypatch.setattr(tidal_remux, "_run_ffmpeg", _ffmpeg)
    monkeypatch.setattr(tidal_remux, "downloads_allowed", lambda: True)


def _download_item() -> ExternalItemRef:
    metadata = map_track(_track_json())
    return ExternalItemRef(provider_key="12345", display_path="12345", metadata=metadata)


_MPD_XML = """<?xml version="1.0"?>
<MPD xmlns="urn:mpeg:dash:schema:mpd:2011" mediaPresentationDuration="PT12S" type="static">
  <Period>
    <AdaptationSet mimeType="audio/mp4">
      <Representation id="0" bandwidth="100000">
        <SegmentTemplate timescale="44100"
            initialization="https://sp-pr-fa.audio.tidal.com/init.mp4"
            media="https://sp-pr-fa.audio.tidal.com/seg-$Number$.m4s"
            startNumber="1">
          <SegmentTimeline>
            <S d="176400" r="2"/>
          </SegmentTimeline>
        </SegmentTemplate>
      </Representation>
    </AdaptationSet>
  </Period>
</MPD>
"""


def _mpd_payload(xml: str = _MPD_XML) -> dict:
    import base64

    return {
        "manifestMimeType": "application/dash+xml",
        "manifest": base64.b64encode(xml.encode()).decode(),
    }


def _item(provider_key: str = "12345") -> ExternalItemRef:
    return ExternalItemRef(provider_key=provider_key, display_path=provider_key)


@pytest.mark.asyncio
async def test_download_bts_produces_tagged_flac(monkeypatch):
    captured = {
        "manifest": {
            "mode": "bts",
            "urls": ["https://sp-pr-fa.audio.tidal.com/track.flac"],
            "mime_type": "audio/flac",
        }
    }
    _patch_remux_pipeline(monkeypatch, captured)
    adapter = TidalExternalAdapter()
    monkeypatch.setattr("songhive.external._tidal.downloads_allowed", lambda: True)

    stream = await adapter.download(_config(download_format="flac"), _download_item())

    assert stream.kind == "path"
    assert stream.content_type == "audio/flac"
    assert stream.temporary is True
    assert str(stream.path).endswith(".flac")
    args = captured["ffmpeg"][-1]
    assert "-f" in args and args[args.index("-f") + 1] == "flac"
    assert "-metadata" in args
    joined = " ".join(args)
    assert "title=Song" in joined
    assert "ISRC=USXYZ1234567" in joined
    stream.path.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_download_aac_uses_mp4_container(monkeypatch):
    captured = {
        "manifest": {
            "mode": "bts",
            "urls": ["https://sp-pr-fa.audio.tidal.com/track.m4a"],
            "mime_type": "audio/mp4",
        }
    }
    _patch_remux_pipeline(monkeypatch, captured)
    adapter = TidalExternalAdapter()
    monkeypatch.setattr("songhive.external._tidal.downloads_allowed", lambda: True)

    stream = await adapter.download(_config(download_format="aac"), _download_item())

    assert str(stream.path).endswith(".m4a")
    assert stream.content_type == "audio/mp4"
    args = captured["ffmpeg"][-1]
    assert args[args.index("-f") + 1] == "mp4"
    stream.path.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_download_mpd_remuxes_via_playlist(monkeypatch):
    captured = {
        "manifest": {
            "mode": "mpd",
            "urls": [
                "https://sp-pr-fa.audio.tidal.com/init.mp4",
                "https://sp-pr-fa.audio.tidal.com/seg-1.m4s",
            ],
            "mime_type": "audio/mp4",
        }
    }
    _patch_remux_pipeline(monkeypatch, captured, cover=True)
    adapter = TidalExternalAdapter()
    monkeypatch.setattr("songhive.external._tidal.downloads_allowed", lambda: True)

    import dataclasses

    item = _download_item()
    item = dataclasses.replace(
        item,
        metadata=dataclasses.replace(
            item.metadata,
            cover_url="https://resources.tidal.com/images/abc/1280x1280.jpg",
        ),
    )
    stream = await adapter.download(_config(), item)

    assert stream.kind == "path"
    args = captured["ffmpeg"][-1]
    joined = " ".join(args)
    # ffmpeg reads a generated HLS playlist with the restricted whitelist.
    assert "-protocol_whitelist" in args
    assert "file,http,https,tcp,tls,crypto" in joined
    assert any(a.endswith(".m3u8") for a in args)
    # Cover attached as a second input.
    assert "attached_pic" in joined
    stream.path.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_download_disabled_denied(monkeypatch):
    adapter = TidalExternalAdapter()
    monkeypatch.setattr("songhive.external._tidal.downloads_allowed", lambda: False)

    with pytest.raises(ExternalPermissionDenied):
        await adapter.download(_config(), _download_item())


@pytest.mark.asyncio
async def test_remux_mode_returns_seekable_cached_file(monkeypatch, tmp_path):
    from songhive.external._tidal import stream as tidal_stream
    from songhive.services.remote_audio_cache import RemoteAudioCache

    session = FakeTidalSession({"tracks/12345/playbackinfopostpaywall": _FakeResponse(_mpd_payload())})

    async def _session_for_config(config, redis=None):
        return session

    monkeypatch.setattr(tidal_stream, "session_for_config", _session_for_config)

    cache = RemoteAudioCache(tmp_path, retention_seconds=3600, max_bytes=0)
    monkeypatch.setattr(
        "songhive.services.remote_audio_cache.get_remote_audio_cache",
        lambda config=None: cache,
        raising=False,
    )

    builds = []

    from songhive.external._tidal import remux as tidal_remux

    async def _remux(m3u8, output, *, timeout, metadata=None, cover_path=None, container="flac"):
        builds.append(m3u8)
        output.write_bytes(b"REMUXED")

    monkeypatch.setattr(tidal_remux, "remux_playlist_to_file", _remux)

    first = await tidal_stream.open_stream(_config(mpd_mode="remux"), _item())
    second = await tidal_stream.open_stream(_config(mpd_mode="remux"), _item())

    assert first.kind == "path"
    assert first.supports_range is True
    assert first.path.read_bytes() == b"REMUXED"
    assert first.path == second.path
    assert len(builds) == 1  # second play served from cache


@pytest.mark.asyncio
async def test_segments_mode_writes_no_cache_files(monkeypatch, tmp_path):
    from songhive.external._tidal import stream as tidal_stream

    session = FakeTidalSession({"tracks/12345/playbackinfopostpaywall": _FakeResponse(_mpd_payload())})

    async def _session_for_config(config, redis=None):
        return session

    monkeypatch.setattr(tidal_stream, "session_for_config", _session_for_config)

    from songhive.services.remote_audio_cache import RemoteAudioCache

    cache = RemoteAudioCache(tmp_path, retention_seconds=3600, max_bytes=0)
    monkeypatch.setattr(
        "songhive.services.remote_audio_cache.get_remote_audio_cache",
        lambda config=None: cache,
        raising=False,
    )

    stream = await tidal_stream.open_stream(_config(), _item())
    assert stream.kind == "iterator"
    await stream.iterator.aclose()
    assert list(tmp_path.iterdir()) == []
