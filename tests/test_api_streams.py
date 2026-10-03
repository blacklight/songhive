"""API tests for the native HTTP stream directory (GET /api/v1/streams/)."""

import json

import pytest
from fastapi import status

from songhive.models.output_stream import OutputStream
from songhive.models.playback_session import PlaybackSession, PlaybackSessionOutput
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


async def _make_stream_session(db, user, output, *, state: str = "playing") -> PlaybackSession:
    """Attach a playback session to ``output`` as its driving stream session."""
    session = PlaybackSession(
        user_id=str(user.id),
        state=state,
        current_index=0,
        position_seconds=0.0,
        queue=[{"id": "t1", "title": "Track", "artist": "Artist", "duration": 100}],
    )
    db.add(session)
    await db.flush()
    db.add(
        PlaybackSessionOutput(
            session_id=session.id,
            output_kind="stream",
            output_stream_id=str(output.id),
            status="live",
        )
    )
    await db.flush()
    return session


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
async def test_list_streams_owner_username_filter(client, regular_user, other_user, db_session):
    """``owner_username`` narrows the directory to one user's mounts."""
    await _make_http_output(db_session, regular_user, "regular-mount")
    await _make_http_output(db_session, other_user, "other-mount")

    response = client.get("/api/v1/streams/", params={"owner_username": "regular"})
    assert response.status_code == status.HTTP_200_OK
    assert [s["mount"] for s in response.json()] == ["regular-mount"]


@pytest.mark.asyncio
async def test_list_streams_owner_username_unknown_user(client):
    """Filtering by an unknown username is a 404, like other owner filters."""
    response = client.get("/api/v1/streams/", params={"owner_username": "nobody"})
    assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.asyncio
async def test_list_streams_owner_username_keeps_visibility_rules(client, regular_user, auth_headers, db_session):
    """The owner filter composes with mount visibility: private and disabled
    mounts still only show to the owner themselves."""
    await _make_http_output(db_session, regular_user, "public-mount")
    await _make_http_output(db_session, regular_user, "private-mount", listen_token="s3cret")
    await _make_http_output(db_session, regular_user, "disabled-mount", enabled=False)

    anon = client.get("/api/v1/streams/", params={"owner_username": "regular"})
    assert [s["mount"] for s in anon.json()] == ["public-mount"]

    owner = client.get(
        "/api/v1/streams/",
        params={"owner_username": "regular"},
        headers=auth_headers(regular_user),
    )
    assert sorted(s["mount"] for s in owner.json()) == [
        "disabled-mount",
        "private-mount",
        "public-mount",
    ]


@pytest.mark.asyncio
async def test_list_streams_reports_playback_state(client, regular_user, other_user, db_session, fake_redis):
    """A live mount exposes its driving session's state for paused reporting."""
    live = await _make_http_output(db_session, regular_user, "live")
    paused = await _make_http_output(db_session, other_user, "paused")
    await _make_http_output(db_session, regular_user, "no-session")
    await _make_stream_session(db_session, regular_user, live, state="playing")
    await _make_stream_session(db_session, other_user, paused, state="paused")
    await _publish_meta(fake_redis, "live", song="A - T")
    await _publish_meta(fake_redis, "paused", song="A - T")
    await _publish_meta(fake_redis, "no-session", song="A - T")

    data = {s["mount"]: s for s in client.get("/api/v1/streams/").json()}
    assert data["live"]["playback_state"] == "playing"
    assert data["paused"]["playback_state"] == "paused"
    assert data["no-session"]["playback_state"] is None


@pytest.mark.asyncio
async def test_list_streams_can_manage_flags(client, regular_user, other_user, admin_user, auth_headers, db_session):
    """``can_manage`` is set for the owner and admins, not other users."""
    await _make_http_output(db_session, regular_user, "radio")

    anon = client.get("/api/v1/streams/").json()
    assert anon[0]["can_manage"] is False

    other = client.get("/api/v1/streams/", headers=auth_headers(other_user)).json()
    assert other[0]["can_manage"] is False
    assert other[0]["is_owner"] is False

    owner = client.get("/api/v1/streams/", headers=auth_headers(regular_user)).json()
    assert owner[0]["can_manage"] is True
    assert owner[0]["is_owner"] is True

    admin = client.get("/api/v1/streams/", headers=auth_headers(admin_user)).json()
    assert admin[0]["can_manage"] is True
    assert admin[0]["is_owner"] is False


@pytest.mark.asyncio
async def test_list_streams_admin_sees_private_and_disabled(client, regular_user, admin_user, auth_headers, db_session):
    """Admins see token-protected and disabled mounts like the owner does."""
    await _make_http_output(db_session, regular_user, "private-mount", listen_token="s3cret")
    await _make_http_output(db_session, regular_user, "disabled-mount", enabled=False)

    data = {s["mount"]: s for s in client.get("/api/v1/streams/", headers=auth_headers(admin_user)).json()}
    assert sorted(data) == ["disabled-mount", "private-mount"]
    assert data["private-mount"]["can_manage"] is True
    assert data["private-mount"]["stream_url"] == "/streams/private-mount?token=s3cret"


@pytest.mark.asyncio
async def test_update_stream_requires_manage_rights(
    client, regular_user, other_user, admin_user, auth_headers, db_session
):
    """PATCH toggles ``enabled`` for the owner or an admin; others get 404/401."""
    output = await _make_http_output(db_session, regular_user, "radio")

    assert (
        client.patch(f"/api/v1/streams/{output.id}", json={"enabled": False}).status_code
        == status.HTTP_401_UNAUTHORIZED
    )
    response = client.patch(
        f"/api/v1/streams/{output.id}",
        json={"enabled": False},
        headers=auth_headers(other_user),
    )
    assert response.status_code == status.HTTP_404_NOT_FOUND

    response = client.patch(
        f"/api/v1/streams/{output.id}",
        json={"enabled": False},
        headers=auth_headers(admin_user),
    )
    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {"id": str(output.id), "enabled": False}
    await db_session.refresh(output)
    assert output.enabled is False

    response = client.patch(
        f"/api/v1/streams/{output.id}",
        json={"enabled": True},
        headers=auth_headers(regular_user),
    )
    assert response.status_code == status.HTTP_200_OK
    assert response.json()["enabled"] is True


@pytest.mark.asyncio
async def test_update_stream_rejects_non_http_output(client, regular_user, auth_headers, db_session):
    """PATCH on a non-HTTP output is a 404 — only native mounts are streams."""
    output = OutputStream(
        user_id=str(regular_user.id),
        provider_type="icecast",
        name="relay",
        config=encrypt_json({"host": "icecast.example", "mount": "/radio"}),
        enabled=True,
    )
    db_session.add(output)
    await db_session.flush()

    response = client.patch(
        f"/api/v1/streams/{output.id}",
        json={"enabled": False},
        headers=auth_headers(regular_user),
    )
    assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.asyncio
async def test_stream_command_toggles_playback(client, regular_user, auth_headers, db_session):
    """play/pause commands reach the session driving the stream."""
    output = await _make_http_output(db_session, regular_user, "radio")
    session = await _make_stream_session(db_session, regular_user, output, state="playing")

    response = client.post(
        f"/api/v1/streams/{output.id}/command",
        json={"command": "pause"},
        headers=auth_headers(regular_user),
    )
    assert response.status_code == status.HTTP_200_OK
    assert response.json()["state"] == "paused"
    await db_session.refresh(session)
    assert session.state == "paused"

    response = client.post(
        f"/api/v1/streams/{output.id}/command",
        json={"command": "play"},
        headers=auth_headers(regular_user),
    )
    assert response.status_code == status.HTTP_200_OK
    assert response.json()["state"] == "playing"


@pytest.mark.asyncio
async def test_stream_command_admin_controls_other_users_stream(
    client, regular_user, admin_user, other_user, auth_headers, db_session
):
    """Admins may command another user's stream; regular users may not."""
    output = await _make_http_output(db_session, regular_user, "radio")
    await _make_stream_session(db_session, regular_user, output, state="playing")

    response = client.post(
        f"/api/v1/streams/{output.id}/command",
        json={"command": "pause"},
        headers=auth_headers(other_user),
    )
    assert response.status_code == status.HTTP_404_NOT_FOUND

    response = client.post(
        f"/api/v1/streams/{output.id}/command",
        json={"command": "pause"},
        headers=auth_headers(admin_user),
    )
    assert response.status_code == status.HTTP_200_OK
    assert response.json()["state"] == "paused"


@pytest.mark.asyncio
async def test_stream_command_no_session_and_bad_command(client, regular_user, auth_headers, db_session):
    """A stream with no driving session is a 409; unknown commands are 422."""
    output = await _make_http_output(db_session, regular_user, "radio")

    response = client.post(
        f"/api/v1/streams/{output.id}/command",
        json={"command": "pause"},
        headers=auth_headers(regular_user),
    )
    assert response.status_code == status.HTTP_409_CONFLICT

    session = await _make_stream_session(db_session, regular_user, output, state="playing")
    assert session is not None
    response = client.post(
        f"/api/v1/streams/{output.id}/command",
        json={"command": "seek"},
        headers=auth_headers(regular_user),
    )
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


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
