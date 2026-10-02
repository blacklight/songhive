"""Tests for TIDAL stream resolution (BTS/MPD manifests) and stream policy."""

from __future__ import annotations

import base64
import json
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from songhive.external._tidal import stream as tidal_stream
from songhive.external._tidal.session import effective_quality
from songhive.external.errors import ExternalItemNotFound, ExternalPermissionDenied
from songhive.external.types import ExternalItemRef, ExternalStream
from songhive.services import secrets


class _FakeResponse:
    def __init__(self, payload, status_code: int = 200, headers: Optional[dict] = None):
        self._payload = payload
        self.status_code = status_code
        self.headers = headers or {}
        self.ok = status_code < 400

    def json(self):
        return self._payload


class _FakeRequester:
    def __init__(self, responses: dict):
        self.responses = responses
        self.calls: list[tuple[str, str, dict]] = []

    def request(self, method, path, params=None, data=None, headers=None):
        self.calls.append((method, path, dict(params or {})))
        entry = self.responses.get(path)
        if entry is None:
            raise ExternalItemNotFound(f"no fake response for {path}")
        if isinstance(entry, Exception):
            raise entry
        return entry


class _FakeUser:
    def __init__(self, user_id: int):
        self.id = user_id


class FakeTidalSession:
    def __init__(self, responses: Optional[dict] = None, user_id: int = 4242):
        self.request = _FakeRequester(responses or {})
        self.user = _FakeUser(user_id)


@pytest.fixture(autouse=True)
def _owner_stream_policy(monkeypatch):
    """Default the TIDAL stream policy to ``owner`` for every test.

    ``_provider_stream_policy`` resolves the live ``config.toml``, so without
    this stub a developer's local ``stream_policy`` setting leaks into the
    suite. Tests exercising other policies re-patch the attribute themselves
    (the later ``monkeypatch.setattr`` wins).
    """
    from songhive.services import streaming as streaming_service

    monkeypatch.setattr(streaming_service, "_provider_stream_policy", lambda _p: "owner")


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


def _bts_payload(urls, mime="audio/flac", encryption="NONE") -> dict:
    manifest = base64.b64encode(
        json.dumps({"mimeType": mime, "urls": urls, "encryptionType": encryption}).encode()
    ).decode()
    return {
        "manifestMimeType": "application/vnd.tidal.bts",
        "manifest": manifest,
    }


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
    return {
        "manifestMimeType": "application/dash+xml",
        "manifest": base64.b64encode(xml.encode()).decode(),
    }


def _patch_session(monkeypatch, session: FakeTidalSession):
    async def _session_for_config(config, redis=None):
        return session

    monkeypatch.setattr(tidal_stream, "session_for_config", _session_for_config)


def _item(provider_key: str = "12345") -> ExternalItemRef:
    return ExternalItemRef(provider_key=provider_key, display_path=provider_key)


# ----------------------------------------------------------------------
# open_stream: BTS / MPD manifests
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bts_returns_url_stream(monkeypatch):
    url = "https://sp-pr-fa.audio.tidal.com/track.flac"
    session = FakeTidalSession({"tracks/12345/playbackinfopostpaywall": _FakeResponse(_bts_payload([url]))})
    _patch_session(monkeypatch, session)

    stream = await tidal_stream.open_stream(_config(), _item())

    assert stream.kind == "url"
    assert stream.url == url
    assert stream.content_type == "audio/flac"
    assert stream.supports_range is True
    assert stream.safe_to_redirect is False


@pytest.mark.asyncio
async def test_bts_safe_to_redirect_when_configured(monkeypatch):
    url = "https://sp-pr-fa.audio.tidal.com/track.flac"
    session = FakeTidalSession({"tracks/12345/playbackinfopostpaywall": _FakeResponse(_bts_payload([url]))})
    _patch_session(monkeypatch, session)

    stream = await tidal_stream.open_stream(_config(redirect_streams=True), _item())

    assert stream.kind == "url"
    assert stream.safe_to_redirect is True


@pytest.mark.asyncio
async def test_mpd_returns_iterator_stream(monkeypatch):
    session = FakeTidalSession({"tracks/12345/playbackinfopostpaywall": _FakeResponse(_mpd_payload())})
    _patch_session(monkeypatch, session)

    stream = await tidal_stream.open_stream(_config(), _item())

    assert stream.kind == "iterator"
    assert stream.content_type == "audio/mp4"
    assert stream.supports_range is False
    assert stream.iterator is not None
    await stream.iterator.aclose()


@pytest.mark.asyncio
async def test_encrypted_manifest_raises_not_found(monkeypatch):
    session = FakeTidalSession(
        {
            "tracks/12345/playbackinfopostpaywall": _FakeResponse(
                _bts_payload(["https://sp-pr-fa.audio.tidal.com/x"], encryption="OLD_STUFF")
            )
        }
    )
    _patch_session(monkeypatch, session)

    with pytest.raises(ExternalItemNotFound):
        await tidal_stream.open_stream(_config(), _item())


@pytest.mark.asyncio
async def test_missing_manifest_raises_not_found(monkeypatch):
    session = FakeTidalSession({"tracks/12345/playbackinfopostpaywall": _FakeResponse({"manifestMimeType": ""})})
    _patch_session(monkeypatch, session)

    with pytest.raises(ExternalItemNotFound):
        await tidal_stream.open_stream(_config(), _item())


@pytest.mark.asyncio
async def test_non_tidal_stream_url_denied(monkeypatch):
    session = FakeTidalSession(
        {"tracks/12345/playbackinfopostpaywall": _FakeResponse(_bts_payload(["https://evil.example.com/track.flac"]))}
    )
    _patch_session(monkeypatch, session)

    with pytest.raises(ExternalPermissionDenied):
        await tidal_stream.open_stream(_config(), _item())


@pytest.mark.asyncio
async def test_manifest_cache_absorbs_burst(monkeypatch, fake_redis):
    url = "https://sp-pr-fa.audio.tidal.com/track.flac"
    session = FakeTidalSession({"tracks/12345/playbackinfopostpaywall": _FakeResponse(_bts_payload([url]))})
    _patch_session(monkeypatch, session)

    for _ in range(4):
        stream = await tidal_stream.open_stream(_config(), _item(), redis=fake_redis)
        assert stream.kind == "url"

    playback_calls = [c for c in session.request.calls if "playbackinfopostpaywall" in c[1]]
    assert len(playback_calls) == 1


@pytest.mark.asyncio
async def test_manifest_cache_keyed_by_track_and_quality(monkeypatch, fake_redis):
    url = "https://sp-pr-fa.audio.tidal.com/track.flac"
    responses = {
        "tracks/12345/playbackinfopostpaywall": _FakeResponse(_bts_payload([url])),
        "tracks/999/playbackinfopostpaywall": _FakeResponse(_bts_payload([url])),
    }
    session = FakeTidalSession(responses)
    _patch_session(monkeypatch, session)

    await tidal_stream.open_stream(_config(), _item("12345"), redis=fake_redis)
    await tidal_stream.open_stream(_config(), _item("999"), redis=fake_redis)
    await tidal_stream.open_stream(_config(quality="HIGH"), _item("12345"), redis=fake_redis)

    assert len(session.request.calls) == 3


def test_mpd_segment_urls_init_first():
    urls = tidal_stream._mpd_segment_urls(_MPD_XML)
    assert urls == [
        "https://sp-pr-fa.audio.tidal.com/init.mp4",
        "https://sp-pr-fa.audio.tidal.com/seg-1.m4s",
        "https://sp-pr-fa.audio.tidal.com/seg-2.m4s",
        "https://sp-pr-fa.audio.tidal.com/seg-3.m4s",
    ]


def _httpx_client_for(bodies: dict[str, bytes]):
    """Fake httpx.AsyncClient whose streamed GETs return ``bodies[url]``."""

    def _make_response(url: str):
        resp = MagicMock()
        data = bodies[url]
        resp.status_code = 200
        resp.headers = {"content-length": str(len(data))}

        async def _chunks(chunk_size=65536):
            for i in range(0, len(data), chunk_size):
                yield data[i : i + chunk_size]

        resp.aiter_bytes = _chunks
        return resp

    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)

    def _stream(method, url, headers=None):
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=_make_response(url))
        ctx.__aexit__ = AsyncMock(return_value=False)
        return ctx

    client.stream = MagicMock(side_effect=_stream)
    return client


@pytest.mark.asyncio
async def test_dash_segments_init_then_media():
    bodies = {
        "https://sp-pr-fa.audio.tidal.com/init.mp4": b"INIT",
        "https://sp-pr-fa.audio.tidal.com/seg-1.m4s": b"S1",
        "https://sp-pr-fa.audio.tidal.com/seg-2.m4s": b"S2",
    }
    urls = list(bodies)
    with patch(
        "songhive.external._tidal.stream.httpx.AsyncClient",
        return_value=_httpx_client_for(bodies),
    ):
        chunks = [c async for c in tidal_stream._dash_segments(urls)]
    assert b"".join(chunks) == b"INITS1S2"


def test_effective_quality():
    assert effective_quality({"quality": "HI_RES_LOSSLESS"}) == "LOSSLESS"
    assert effective_quality({"quality": "HI_RES_LOSSLESS", "is_pkce": True}) == "HI_RES_LOSSLESS"
    assert effective_quality({}) == "LOSSLESS"


# ----------------------------------------------------------------------
# Stream policy enforcement in services.streaming
# ----------------------------------------------------------------------


async def _seed_tidal_track(db_session, owner, *, provider_key="98765"):
    """Create a Track referenced by an entity-backed TIDAL ExternalItem."""
    from songhive.models.artist import Artist
    from songhive.models.external_item import ExternalItem
    from songhive.models.external_library import ExternalLibrary
    from songhive.models.library import Library
    from songhive.models.track import Track

    library = Library(name="Tidal Lib", owner_id=str(owner.id), visibility="private")
    db_session.add(library)
    await db_session.flush()

    external_library = ExternalLibrary(
        library_id=str(library.id),
        provider_type="tidal",
        config=secrets.encrypt_json(_config()),
        enabled=True,
        created_by_id=str(owner.id),
        capabilities={"stream_url": True, "download": True},
    )
    db_session.add(external_library)
    artist = Artist(name="Tidal Artist")
    db_session.add(artist)
    await db_session.flush()

    track = Track(
        title="Tidal Track",
        artist_id=artist.id,
        owner_id=str(owner.id),
        visibility="private",
    )
    db_session.add(track)
    await db_session.flush()

    item = ExternalItem(
        external_library_id=str(external_library.id),
        kind="track",
        provider_key=provider_key,
        track_id=str(track.id),
        state="active",
    )
    db_session.add(item)
    await db_session.flush()
    return external_library, track, item


async def _fake_stream(config, item, *, range=None):  # noqa: A002 - adapter signature
    async def _chunks():
        yield b"audio"

    return ExternalStream(
        kind="iterator",
        iterator=_chunks(),
        content_type="audio/flac",
        supports_range=False,
    )


def _patch_adapter_open_stream(monkeypatch, sink: Optional[dict] = None):
    from songhive.external._tidal import TidalExternalAdapter

    async def _open_stream(self, config, item, *, range=None):  # noqa: A002
        if sink is not None:
            sink["config"] = config
        return await _fake_stream(config, item, range=range)

    monkeypatch.setattr(TidalExternalAdapter, "open_stream", _open_stream)


@pytest.mark.asyncio
async def test_stream_policy_owner_allows_owner(db_session, make_user, monkeypatch):
    from songhive.services.streaming import resolve_external_stream

    owner = await make_user("tidalowner")
    _patch_adapter_open_stream(monkeypatch)
    _, track, _ = await _seed_tidal_track(db_session, owner)

    stream = await resolve_external_stream(db_session, str(track.id), user=owner)
    assert stream is not None
    assert stream.kind == "iterator"
    await stream.iterator.aclose()


@pytest.mark.asyncio
async def test_stream_policy_owner_denies_non_owner(db_session, make_user, monkeypatch):
    from songhive.services.streaming import resolve_external_stream

    owner = await make_user("tidalowner")
    listener = await make_user("listener")
    _patch_adapter_open_stream(monkeypatch)
    _, track, _ = await _seed_tidal_track(db_session, owner)

    with pytest.raises(ExternalPermissionDenied):
        await resolve_external_stream(db_session, str(track.id), user=listener)


@pytest.mark.asyncio
async def test_stream_policy_denies_anonymous(db_session, make_user, monkeypatch):
    from songhive.services.streaming import resolve_external_stream

    owner = await make_user("tidalowner")
    _patch_adapter_open_stream(monkeypatch)
    _, track, _ = await _seed_tidal_track(db_session, owner)

    with pytest.raises(ExternalPermissionDenied):
        await resolve_external_stream(db_session, str(track.id), user=None)


@pytest.mark.asyncio
async def test_stream_policy_admin_allowed(db_session, make_user, monkeypatch):
    from songhive.services.streaming import resolve_external_stream

    owner = await make_user("tidalowner")
    admin = await make_user("siteadmin", role="admin")
    _patch_adapter_open_stream(monkeypatch)
    _, track, _ = await _seed_tidal_track(db_session, owner)

    stream = await resolve_external_stream(db_session, str(track.id), user=admin)
    assert stream is not None
    await stream.iterator.aclose()


@pytest.mark.asyncio
async def test_stream_policy_anyone_allows_authenticated(db_session, make_user, monkeypatch):
    from songhive.services import streaming as streaming_service
    from songhive.services.streaming import resolve_external_stream

    owner = await make_user("tidalowner")
    listener = await make_user("listener")
    _patch_adapter_open_stream(monkeypatch)
    _, track, _ = await _seed_tidal_track(db_session, owner)

    monkeypatch.setattr(streaming_service, "_provider_stream_policy", lambda _p: "anyone")
    stream = await resolve_external_stream(db_session, str(track.id), user=listener)
    assert stream is not None
    await stream.iterator.aclose()


@pytest.mark.asyncio
async def test_stream_policy_anyone_still_denies_anonymous(db_session, make_user, monkeypatch):
    from songhive.services import streaming as streaming_service
    from songhive.services.streaming import resolve_external_stream

    owner = await make_user("tidalowner")
    _patch_adapter_open_stream(monkeypatch)
    _, track, _ = await _seed_tidal_track(db_session, owner)

    monkeypatch.setattr(streaming_service, "_provider_stream_policy", lambda _p: "anyone")
    with pytest.raises(ExternalPermissionDenied):
        await resolve_external_stream(db_session, str(track.id), user=None)


@pytest.mark.asyncio
async def test_stream_policy_listener_account_uses_own_config(db_session, make_user, monkeypatch):
    from songhive.models.external_library import ExternalLibrary
    from songhive.models.library import Library
    from songhive.services import streaming as streaming_service
    from songhive.services.streaming import resolve_external_stream

    owner = await make_user("tidalowner")
    listener = await make_user("listener")

    # Listener's own TIDAL library carries a distinguishable config.
    listener_library = Library(name="Listener Tidal", owner_id=str(listener.id))
    db_session.add(listener_library)
    await db_session.flush()
    db_session.add(
        ExternalLibrary(
            library_id=str(listener_library.id),
            provider_type="tidal",
            config=secrets.encrypt_json(_config(access_token="listener-token")),
            enabled=True,
            created_by_id=str(listener.id),
        )
    )
    await db_session.flush()

    sink: dict = {}
    _patch_adapter_open_stream(monkeypatch, sink)
    _, track, _ = await _seed_tidal_track(db_session, owner)

    monkeypatch.setattr(streaming_service, "_provider_stream_policy", lambda _p: "listener_account")
    stream = await resolve_external_stream(db_session, str(track.id), user=listener)
    assert stream is not None
    await stream.iterator.aclose()
    assert sink["config"].get("access_token") == "listener-token"


@pytest.mark.asyncio
async def test_stream_policy_listener_account_falls_back_to_denied(db_session, make_user, monkeypatch):
    from songhive.services import streaming as streaming_service
    from songhive.services.streaming import resolve_external_stream

    owner = await make_user("tidalowner")
    listener = await make_user("listener")
    _patch_adapter_open_stream(monkeypatch)
    _, track, _ = await _seed_tidal_track(db_session, owner)

    monkeypatch.setattr(streaming_service, "_provider_stream_policy", lambda _p: "listener_account")
    with pytest.raises(ExternalPermissionDenied):
        await resolve_external_stream(db_session, str(track.id), user=listener)


@pytest.mark.asyncio
async def test_download_stream_enforces_policy(db_session, make_user, monkeypatch):
    from songhive.services.streaming import resolve_external_download_stream

    owner = await make_user("tidalowner")
    listener = await make_user("listener")
    _patch_adapter_open_stream(monkeypatch)
    _, track, _ = await _seed_tidal_track(db_session, owner)

    with pytest.raises(ExternalPermissionDenied):
        await resolve_external_download_stream(db_session, str(track.id), user=listener)


@pytest.mark.asyncio
async def test_stream_sets_audio_mime_type_lazily(db_session, make_user, monkeypatch):
    from songhive.services.streaming import resolve_external_stream

    owner = await make_user("tidalowner")
    _patch_adapter_open_stream(monkeypatch)
    _, track, item = await _seed_tidal_track(db_session, owner)
    assert track.audio_mime_type is None

    stream = await resolve_external_stream(db_session, str(track.id), user=owner)
    assert stream is not None
    await stream.iterator.aclose()
    await db_session.commit()
    await db_session.refresh(track)
    assert track.audio_mime_type == "audio/flac"


@pytest.mark.asyncio
async def test_not_found_marks_entity_unavailable(db_session, make_user, monkeypatch):
    from sqlalchemy import select

    from songhive.external._tidal import TidalExternalAdapter
    from songhive.models.provider_catalog import ProviderCatalogEntry
    from songhive.services.streaming import resolve_external_stream

    owner = await make_user("tidalowner")
    _, track, item = await _seed_tidal_track(db_session, owner)

    async def _gone(self, config, _item, *, range=None):  # noqa: A002
        raise ExternalItemNotFound("gone")

    monkeypatch.setattr(TidalExternalAdapter, "open_stream", _gone)

    with pytest.raises(ExternalItemNotFound):
        await resolve_external_stream(db_session, str(track.id), user=owner)
    await db_session.commit()

    await db_session.refresh(item)
    assert item.state == "error"
    assert "gone" in (item.sync_error or "")

    result = await db_session.execute(
        select(ProviderCatalogEntry).where(
            ProviderCatalogEntry.provider_type == "tidal",
            ProviderCatalogEntry.kind == "track",
            ProviderCatalogEntry.provider_key == "98765",
        )
    )
    entry = result.scalar_one_or_none()
    assert entry is not None
    assert entry.unavailable_at is not None


# ----------------------------------------------------------------------
# Response schema: audio_url / external_url under stream policy
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_track_response_hides_audio_url_for_non_owner(db_session, make_user, monkeypatch, config):
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from songhive.api._include import IncludeQuery
    from songhive.api.routes.tracks import _build_track_response
    from songhive.models.external_item import ExternalItem
    from songhive.models.external_library import ExternalLibrary
    from songhive.models.track import Track
    from songhive.services.storage import StorageService
    from songhive.storage import get_storage

    owner = await make_user("tidalowner")
    listener = await make_user("listener")
    _, track, _ = await _seed_tidal_track(db_session, owner)

    storage = StorageService(get_storage(config.storage), config.storage)

    track_id = str(track.id)
    db_session.expire(track)
    result = await db_session.execute(
        select(Track)
        .where(Track.id == track_id)
        .options(
            selectinload(Track.external_track),
            selectinload(Track.external_item)
            .selectinload(ExternalItem.external_library)
            .selectinload(ExternalLibrary.library),
        )
    )
    loaded = result.scalar_one()
    include = IncludeQuery(set())

    denied = await _build_track_response(loaded, listener, storage, include, db=db_session)
    assert denied.audio_url is None
    assert denied.external_url == "https://tidal.com/browse/track/98765"
    assert denied.can_stream is False

    allowed = await _build_track_response(loaded, owner, storage, include, db=db_session)
    assert allowed.audio_url is not None
    assert allowed.external_url is None
    assert allowed.can_stream is True
