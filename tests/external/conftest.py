"""Shared fixtures for external-library integration tests."""

import httpx
import pytest
from sqlalchemy import select
from tornado.httpserver import HTTPServer

from songhive.app import _build_tornado_app
from songhive.external._fake import FakeExternalAdapter
from songhive.external.registry import _REGISTRY, register_external_adapter
from songhive.external.sync import sync_external_library
from songhive.models.external_library import ExternalLibrary
from songhive.models.external_track import ExternalTrack
from songhive.models.track import Track


@pytest.fixture(autouse=True)
def fake_adapter():
    """Register the fake adapter for every test and clean up afterward."""
    register_external_adapter("fake", FakeExternalAdapter)
    yield
    _REGISTRY.pop("fake", None)


def _fake_config() -> dict:
    """Return the default in-memory fake-adapter config with two FLAC items."""
    return {
        "items": {
            "track1.flac": {
                "data": list(b"X" * 32000),
                "mimetype": "audio/flac",
                "metadata": {
                    "title": "Stream / Download One",
                    "artist": "Artist A",
                    "album": "Album A",
                    "duration": 31.0,
                },
            },
            "track2.flac": {
                "data": list(b"external2"),
                "mimetype": "audio/flac",
                "sha256": "a" * 64,
                "metadata": {
                    "title": "Second Track",
                    "artist": "Artist B",
                    "album": "Album B",
                    "duration": 200.0,
                },
            },
        },
        "secret_key": "fixture-secret",
    }


def _admin_create_payload(overrides: dict | None = None) -> dict:
    """Return a default admin create payload with two fake items."""
    payload = {
        "provider_type": "fake",
        "library_name": "Integration External Library",
        "include_in_library_index": True,
        "config": _fake_config(),
    }
    if overrides:
        payload.update(overrides)
    return payload


@pytest.fixture
async def fake_redis():
    """Return a fake Redis client backed by an isolated in-memory server."""
    from fakeredis import FakeServer
    from fakeredis.aioredis import FakeRedis

    return FakeRedis(server=FakeServer(), decode_responses=True)


@pytest.fixture
async def external_library(client, admin_user, auth_headers, db_session):
    """Create an admin-managed external library with two in-memory items through the API."""
    response = client.post(
        "/api/v1/admin/external-libraries",
        json=_admin_create_payload(),
        headers=auth_headers(admin_user),
    )
    assert response.status_code == 201
    data = response.json()
    library = await db_session.get(ExternalLibrary, data["id"])
    assert library is not None
    return library


@pytest.fixture
async def synced_external_library(external_library, db_session, fake_redis, admin_user):
    """Sync the external-library fixture and return it with its tracks."""
    run = await sync_external_library(
        db_session,
        str(external_library.id),
        triggered_by="manual",
        triggered_by_user_id=str(admin_user.id),
        redis=fake_redis,
    )
    assert run.status == "success"
    assert run.items_seen == 2
    assert run.tracks_created == 2

    await db_session.refresh(external_library)

    result = await db_session.execute(
        select(Track)
        .join(ExternalTrack, ExternalTrack.track_id == Track.id)
        .where(ExternalTrack.external_library_id == str(external_library.id))
        .order_by(ExternalTrack.provider_key)
    )
    tracks = list(result.scalars().all())
    assert len(tracks) == 2
    return external_library, tracks


@pytest.fixture
async def tornado_client(app, config, fake_redis, tmp_path):
    """Start a Tornado HTTP server and yield an httpx async client for it."""
    config.external_libraries.stream_temp_dir = tmp_path / "streams"
    tornado_app = _build_tornado_app(config, app, tornado_redis=fake_redis)
    server = HTTPServer(tornado_app)
    server.listen(0)
    port = list(server._sockets.values())[0].getsockname()[1]
    base_url = f"http://127.0.0.1:{port}"
    try:
        async with httpx.AsyncClient(base_url=base_url, timeout=httpx.Timeout(60.0)) as http_client:
            yield http_client
    finally:
        server.stop()
