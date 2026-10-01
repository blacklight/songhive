"""API tests for the native HTTP stream directory (GET /api/v1/streams/)."""

import json

import pytest
from fastapi import status

from songhive.models.output_stream import OutputStream
from songhive.services.secrets import encrypt_json
from songhive.streams.http import stream_listener_key, stream_meta_key


async def _make_http_output(db, user, mount: str = "radio", *, enabled: bool = True, **overrides) -> OutputStream:
    """Persist an HTTP output stream row owned by ``user``."""
    cfg = {
        "mount": mount,
        "format": "mp3",
        "bitrate": "128k",
        "sample_rate": 44100,
        **overrides,
    }
    output = OutputStream(
        user_id=str(user.id),
        provider_type="http",
        name=f"output-{mount}",
        config=encrypt_json(cfg),
        enabled=enabled,
    )
    db.add(output)
    await db.flush()
    return output


async def _publish_meta(fake_redis, mount: str, **meta) -> None:
    """Seed the driver's meta blob so the mount looks live."""
    payload = {"mount": mount, "content_type": "audio/mpeg", "bitrate": "128k", **meta}
    await fake_redis.set(stream_meta_key(mount), json.dumps(payload), ex=30)


@pytest.mark.asyncio
async def test_list_streams_anonymous_sees_public_only(client, regular_user, db_session):
    """Anonymous visitors see public mounts but not token-protected ones."""
    await _make_http_output(db_session, regular_user, "public-mount")
    await _make_http_output(db_session, regular_user, "private-mount", listen_token="s3cret")

    response = client.get("/api/v1/streams/")
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert [s["mount"] for s in data] == ["public-mount"]
    assert data[0]["visibility"] == "public"
    assert data[0]["stream_url"] == "/streams/public-mount"
    assert "token" not in data[0]["stream_url"]
    assert data[0]["is_owner"] is False


@pytest.mark.asyncio
async def test_list_streams_owner_sees_private_with_token_url(client, regular_user, auth_headers, db_session):
    """The owner sees token-protected mounts and gets a playable token URL."""
    await _make_http_output(db_session, regular_user, "private-mount", listen_token="s3cret")

    response = client.get("/api/v1/streams/", headers=auth_headers(regular_user))
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert [s["mount"] for s in data] == ["private-mount"]
    assert data[0]["visibility"] == "private"
    assert data[0]["is_owner"] is True
    assert data[0]["stream_url"] == "/streams/private-mount?token=s3cret"


@pytest.mark.asyncio
async def test_list_streams_other_user_cannot_see_private(client, regular_user, other_user, auth_headers, db_session):
    """Another authenticated user does not see someone's private mount."""
    await _make_http_output(db_session, regular_user, "private-mount", listen_token="s3cret")
    await _make_http_output(db_session, other_user, "other-public")

    response = client.get("/api/v1/streams/", headers=auth_headers(other_user))
    assert response.status_code == status.HTTP_200_OK
    assert [s["mount"] for s in response.json()] == ["other-public"]


@pytest.mark.asyncio
async def test_list_streams_disabled_hidden_from_others(client, regular_user, other_user, auth_headers, db_session):
    """Disabled outputs are only listed for their owner."""
    await _make_http_output(db_session, regular_user, "disabled-mount", enabled=False)

    anon = client.get("/api/v1/streams/")
    assert anon.status_code == status.HTTP_200_OK
    assert anon.json() == []

    other = client.get("/api/v1/streams/", headers=auth_headers(other_user))
    assert other.json() == []

    owner = client.get("/api/v1/streams/", headers=auth_headers(regular_user))
    data = owner.json()
    assert [s["mount"] for s in data] == ["disabled-mount"]
    assert data[0]["enabled"] is False
    assert data[0]["online"] is False


@pytest.mark.asyncio
async def test_list_streams_status_and_now_playing(client, regular_user, db_session, fake_redis):
    """Meta blob presence marks the stream online and carries now-playing info."""
    await _make_http_output(db_session, regular_user, "live", genre="Ambient", description=" chill ")
    await _publish_meta(
        fake_redis,
        "live",
        song="Artist - Song",
        track_id="track-1",
        title="Song",
        artist="Artist",
        album="LP",
        description="Chill beats",
    )

    response = client.get("/api/v1/streams/")
    assert response.status_code == status.HTTP_200_OK
    stream = response.json()[0]
    assert stream["online"] is True
    assert stream["enabled"] is True
    assert stream["genre"] == "Ambient"
    assert stream["description"] == "Chill beats"
    assert stream["now_playing"] == {
        "track_id": "track-1",
        "title": "Song",
        "artist": "Artist",
        "album": "LP",
    }

    # Offline mounts report neither now-playing nor a live status.
    await fake_redis.delete(stream_meta_key("live"))
    stream = client.get("/api/v1/streams/").json()[0]
    assert stream["online"] is False
    assert stream["now_playing"] is None


@pytest.mark.asyncio
async def test_list_streams_listener_count(client, regular_user, db_session, fake_redis):
    """Listener TTL keys surface as an approximate listener count."""
    await _make_http_output(db_session, regular_user, "radio")
    await _publish_meta(fake_redis, "radio", song="")
    await fake_redis.set(stream_listener_key("radio", "a"), "1", ex=30)
    await fake_redis.set(stream_listener_key("radio", "b"), "1", ex=30)

    stream = client.get("/api/v1/streams/").json()[0]
    assert stream["listener_count"] == 2
    # A meta key with no song still counts as online.
    assert stream["online"] is True
    assert stream["now_playing"] is None


@pytest.mark.asyncio
async def test_list_streams_includes_owner_profile(client, regular_user, db_session):
    """Each stream carries its owner's public profile summary."""
    regular_user.display_name = "DJ Regular"
    regular_user.avatar_url = "https://cdn.example.com/avatar.png"
    await db_session.flush()
    await _make_http_output(db_session, regular_user, "radio")

    stream = client.get("/api/v1/streams/").json()[0]
    assert stream["owner"] == {
        "username": "regular",
        "display_name": "DJ Regular",
        "avatar_url": "https://cdn.example.com/avatar.png",
    }


@pytest.mark.asyncio
async def test_list_streams_ignores_non_http_outputs(client, regular_user, db_session):
    """Icecast/snapcast outputs are not native mounts and never listed."""
    output = OutputStream(
        user_id=str(regular_user.id),
        provider_type="icecast",
        name="relay",
        config=encrypt_json({"host": "icecast.example", "mount": "/radio"}),
        enabled=True,
    )
    db_session.add(output)
    await db_session.flush()

    assert client.get("/api/v1/streams/").json() == []
