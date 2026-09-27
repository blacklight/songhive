"""
Tests for the M3U export endpoints (``/api/v1/{albums,playlists}/{id}/m3u``).
"""

import io

import pytest
from sqlalchemy import func, select

from songhive.models._enums import Visibility
from songhive.models.album import Album
from songhive.models.artist import Artist
from songhive.models.playlist import Playlist, PlaylistTrack
from songhive.models.podcast import Podcast, PodcastEpisode
from songhive.models.remote_object import RemoteObject
from songhive.models.share_token import ShareToken
from songhive.models.track import Track
from songhive.services import acl, sharing
from songhive.services.storage import StorageService
from songhive.storage import get_storage


@pytest.fixture
def storage_service(config):
    return StorageService(get_storage(config.storage), config.storage)


@pytest.fixture
async def m3u_artist(db_session):
    artist = Artist(name="M3U Artist")
    db_session.add(artist)
    await db_session.flush()
    return artist


async def _make_track(
    db_session,
    storage_service,
    artist,
    owner,
    *,
    title="Track",
    visibility=Visibility.PUBLIC.value,
    album=None,
    duration=90.0,
    track_number=1,
    disc_number=1,
    with_audio=True,
):
    audio_file_id = None
    if with_audio:
        stored = await storage_service.store_file(
            db_session,
            io.BytesIO(b"audio-bytes"),
            "audio/mpeg",
            owner_id=owner.id,
            visibility=visibility,
            original_filename=f"{title}.mp3",
        )
        audio_file_id = stored.id
    track = Track(
        title=title,
        artist_id=artist.id,
        album_id=album.id if album is not None else None,
        audio_file_id=audio_file_id,
        owner_id=str(owner.id),
        visibility=visibility,
        duration=duration,
        track_number=track_number,
        disc_number=disc_number,
    )
    db_session.add(track)
    await db_session.flush()
    return track


async def _make_album(db_session, artist, owner, *, title="Album", visibility=Visibility.PUBLIC.value):
    album = Album(
        title=title,
        artist_id=artist.id,
        owner_id=str(owner.id),
        visibility=visibility,
    )
    db_session.add(album)
    await db_session.flush()
    return album


async def _make_playlist(db_session, owner, *, name="Mix", visibility=Visibility.PUBLIC.value):
    playlist = Playlist(name=name, owner_id=owner.id, visibility=visibility)
    db_session.add(playlist)
    await db_session.flush()
    return playlist


async def _share_token_count(db_session, item_type, item_id) -> int:
    result = await db_session.execute(
        select(func.count())
        .select_from(ShareToken)
        .where(ShareToken.item_type == item_type, ShareToken.item_id == item_id)
    )
    return result.scalar() or 0


def _entries(document: str):
    """Return the non-header lines (URLs) of an M3U document."""
    return [line for line in document.splitlines() if line and not line.startswith("#")]


# ---------------------------------------------------------------------------
# Album export
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_album_m3u_anonymous_public_album(client, db_session, storage_service, m3u_artist, regular_user):
    """Anonymous export of a public album renders EXTINF + stream URLs in disc/track order."""
    album = await _make_album(db_session, m3u_artist, regular_user, title="The Album")
    second = await _make_track(
        db_session,
        storage_service,
        m3u_artist,
        regular_user,
        title="Second Song",
        album=album,
        duration=61.6,
        track_number=2,
    )
    first = await _make_track(
        db_session,
        storage_service,
        m3u_artist,
        regular_user,
        title="First Song",
        album=album,
        duration=90.0,
        track_number=1,
    )
    await db_session.commit()

    response = client.get(f"/api/v1/albums/{album.id}/m3u")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("audio/x-mpegurl")
    assert response.headers["content-disposition"] == 'attachment; filename="The Album.m3u"'
    assert "x-m3u-skipped" not in response.headers

    body = response.text
    lines = body.splitlines()
    assert lines[0] == "#EXTM3U"
    assert "#PLAYLIST:The Album" in lines
    # Track-number ordering, integer EXTINF durations, "Artist - Title" labels.
    assert lines.index("#EXTINF:90,M3U Artist - First Song") < lines.index("#EXTINF:61,M3U Artist - Second Song")
    urls = _entries(body)
    assert urls == [
        f"http://testserver/api/v1/stream/{first.id}",
        f"http://testserver/api/v1/stream/{second.id}",
    ]


@pytest.mark.asyncio
async def test_album_m3u_private_album_denied_anonymously(client, db_session, m3u_artist, regular_user):
    """A private album is not exportable by anonymous or unrelated users."""
    album = await _make_album(db_session, m3u_artist, regular_user, visibility=Visibility.PRIVATE.value)
    await db_session.commit()

    assert client.get(f"/api/v1/albums/{album.id}/m3u").status_code == 403
    assert client.get("/api/v1/albums/nonexistent/m3u").status_code == 404


@pytest.mark.asyncio
async def test_album_m3u_exclude_skips_private_tracks(
    client, db_session, storage_service, m3u_artist, regular_user, auth_headers
):
    """access=exclude drops non-public tracks from a private album's export."""
    album = await _make_album(db_session, m3u_artist, regular_user, visibility=Visibility.PRIVATE.value)
    public_track = await _make_track(
        db_session,
        storage_service,
        m3u_artist,
        regular_user,
        title="Public Song",
        album=album,
        track_number=1,
    )
    await _make_track(
        db_session,
        storage_service,
        m3u_artist,
        regular_user,
        title="Secret Song",
        album=album,
        visibility=Visibility.PRIVATE.value,
        track_number=2,
    )
    await db_session.commit()

    response = client.get(f"/api/v1/albums/{album.id}/m3u", headers=auth_headers(regular_user))
    assert response.status_code == 200, response.text
    urls = _entries(response.text)
    assert urls == [f"http://testserver/api/v1/stream/{public_track.id}"]
    assert response.headers["x-m3u-skipped"] == "1"
    assert await _share_token_count(db_session, "album", str(album.id)) == 0


@pytest.mark.asyncio
async def test_album_m3u_token_embeds_share_token(
    client, db_session, storage_service, m3u_artist, regular_user, auth_headers
):
    """access=token mints a container share token embedded in private track URLs."""
    album = await _make_album(db_session, m3u_artist, regular_user, visibility=Visibility.PRIVATE.value)
    private_track = await _make_track(
        db_session,
        storage_service,
        m3u_artist,
        regular_user,
        title="Secret Song",
        album=album,
        visibility=Visibility.PRIVATE.value,
    )
    public_track = await _make_track(
        db_session,
        storage_service,
        m3u_artist,
        regular_user,
        title="Public Song",
        album=album,
        track_number=2,
    )
    await db_session.commit()

    response = client.get(
        f"/api/v1/albums/{album.id}/m3u?access=token",
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 200, response.text
    assert "x-m3u-skipped" not in response.headers
    urls = _entries(response.text)
    assert len(urls) == 2

    private_url = next(u for u in urls if str(private_track.id) in u)
    assert private_url.startswith(f"http://testserver/api/v1/stream/{private_track.id}?token=")
    raw_token = private_url.split("?token=", 1)[1]
    # The album share token grants stream access to member tracks via
    # derived ACL rules (track → album → share token).
    assert await acl.can_access(db_session, None, "track", str(private_track.id), share_token=raw_token)
    # Public tracks keep clean URLs.
    assert f"http://testserver/api/v1/stream/{public_track.id}" in urls
    assert await _share_token_count(db_session, "album", str(album.id)) == 1


@pytest.mark.asyncio
async def test_album_m3u_token_mode_does_not_mint_when_unneeded(
    client, db_session, storage_service, m3u_artist, regular_user, auth_headers
):
    """access=token on an all-public album creates no share-token row."""
    album = await _make_album(db_session, m3u_artist, regular_user)
    await _make_track(db_session, storage_service, m3u_artist, regular_user, album=album)
    await db_session.commit()

    response = client.get(
        f"/api/v1/albums/{album.id}/m3u?access=token",
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 200, response.text
    assert await _share_token_count(db_session, "album", str(album.id)) == 0


@pytest.mark.asyncio
async def test_album_m3u_token_requires_manage_rights(
    client, db_session, storage_service, m3u_artist, regular_user, other_user, auth_headers
):
    """access=token without manage rights (or a valid share token) is rejected."""
    album = await _make_album(db_session, m3u_artist, regular_user, visibility=Visibility.LOCAL.value)
    await _make_track(
        db_session,
        storage_service,
        m3u_artist,
        regular_user,
        album=album,
        visibility=Visibility.LOCAL.value,
    )
    await db_session.commit()

    # ``other_user`` can see a local album but cannot mint share tokens for it.
    response = client.get(
        f"/api/v1/albums/{album.id}/m3u?access=token",
        headers=auth_headers(other_user),
    )
    assert response.status_code == 403
    # Anonymous callers cannot request token embedding either.
    response = client.get(f"/api/v1/albums/{album.id}/m3u?access=token")
    assert response.status_code == 403
    # ...but exclusion mode still works for both.
    response = client.get(f"/api/v1/albums/{album.id}/m3u", headers=auth_headers(other_user))
    assert response.status_code == 200
    assert _entries(response.text) == []
    assert response.headers["x-m3u-skipped"] == "1"


# ---------------------------------------------------------------------------
# Playlist export
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_playlist_m3u_mixed_items_in_order(client, db_session, storage_service, m3u_artist, regular_user):
    """Playlist export preserves position order across tracks and episodes."""
    playlist = await _make_playlist(db_session, regular_user, name="Road Mix")
    track = await _make_track(
        db_session,
        storage_service,
        m3u_artist,
        regular_user,
        title="Playlist Song",
        duration=42.0,
    )
    podcast = Podcast(feed_url="https://pods.example/feed.xml", title="The Pod")
    db_session.add(podcast)
    await db_session.flush()
    episode = PodcastEpisode(
        podcast_id=podcast.id,
        guid="ep-1",
        title="Episode One",
        audio_url="https://pods.example/ep1.mp3",
        audio_type="audio/mpeg",
        duration_seconds=1800,
    )
    db_session.add(episode)
    await db_session.flush()
    db_session.add_all(
        [
            PlaylistTrack(playlist_id=playlist.id, podcast_episode_id=episode.id, position=1),
            PlaylistTrack(playlist_id=playlist.id, track_id=track.id, position=2),
        ]
    )
    await db_session.commit()

    response = client.get(f"/api/v1/playlists/{playlist.id}/m3u")
    assert response.status_code == 200, response.text
    lines = response.text.splitlines()
    assert lines[0] == "#EXTM3U"
    assert "#PLAYLIST:Road Mix" in lines
    assert lines.index("#EXTINF:1800,The Pod - Episode One") < lines.index("#EXTINF:42,M3U Artist - Playlist Song")
    assert _entries(response.text) == [
        "https://pods.example/ep1.mp3",
        f"http://testserver/api/v1/stream/{track.id}",
    ]


@pytest.mark.asyncio
async def test_playlist_m3u_private_track_only_for_owner(
    client, db_session, storage_service, m3u_artist, regular_user, auth_headers
):
    """Private member tracks are invisible to anonymous exports."""
    playlist = await _make_playlist(db_session, regular_user)
    public_track = await _make_track(db_session, storage_service, m3u_artist, regular_user, title="Seen")
    private_track = await _make_track(
        db_session,
        storage_service,
        m3u_artist,
        regular_user,
        title="Unseen",
        visibility=Visibility.PRIVATE.value,
    )
    db_session.add_all(
        [
            PlaylistTrack(playlist_id=playlist.id, track_id=public_track.id, position=1),
            PlaylistTrack(playlist_id=playlist.id, track_id=private_track.id, position=2),
        ]
    )
    await db_session.commit()

    # Anonymous listing filters the private member out entirely.
    anon = client.get(f"/api/v1/playlists/{playlist.id}/m3u")
    assert _entries(anon.text) == [f"http://testserver/api/v1/stream/{public_track.id}"]

    # The owner sees it — derived access through the public playlist makes it
    # anonymously playable, so the URL stays token-free.
    owner = client.get(f"/api/v1/playlists/{playlist.id}/m3u", headers=auth_headers(regular_user))
    assert _entries(owner.text) == [
        f"http://testserver/api/v1/stream/{public_track.id}",
        f"http://testserver/api/v1/stream/{private_track.id}",
    ]


@pytest.mark.asyncio
async def test_playlist_m3u_share_token_reused(
    client, db_session, storage_service, m3u_artist, regular_user, other_user, auth_headers
):
    """A valid container share token is reused for non-public member tracks."""
    playlist = await _make_playlist(db_session, regular_user, visibility=Visibility.PRIVATE.value)
    local_track = await _make_track(
        db_session,
        storage_service,
        m3u_artist,
        regular_user,
        title="Local Song",
        visibility=Visibility.LOCAL.value,
    )
    db_session.add(PlaylistTrack(playlist_id=playlist.id, track_id=local_track.id, position=1))
    await db_session.commit()
    _row, raw_token = await sharing.create_share_token(db_session, "playlist", str(playlist.id), str(regular_user.id))
    await db_session.commit()

    # An authenticated non-owner holding the playlist share token can export
    # with token embedding; the supplied token is reused, not duplicated.
    response = client.get(
        f"/api/v1/playlists/{playlist.id}/m3u?access=token&token={raw_token}",
        headers=auth_headers(other_user),
    )
    assert response.status_code == 200, response.text
    urls = _entries(response.text)
    assert urls == [f"http://testserver/api/v1/stream/{local_track.id}?token={raw_token}"]
    assert await _share_token_count(db_session, "playlist", str(playlist.id)) == 1


@pytest.mark.asyncio
async def test_playlist_m3u_remote_object_policy(client, db_session, regular_user):
    """Remote objects are only exported when anonymous streaming is allowed."""
    playlist = await _make_playlist(db_session, regular_user)
    remote = RemoteObject(
        canonical_url="https://remote.example/objects/1",
        domain="remote.example",
        object_type="Audio",
        actor_url="https://remote.example/users/dj",
        visibility="public",
        name="Remote Tune",
        audio_url="https://remote.example/media/tune.mp3",
    )
    db_session.add(remote)
    await db_session.flush()
    db_session.add(PlaylistTrack(playlist_id=playlist.id, remote_object_id=remote.id, position=1))
    await db_session.commit()

    # Default ``remote_search_access`` is authenticated: M3U players fetch
    # anonymously, so the entry is skipped.
    response = client.get(f"/api/v1/playlists/{playlist.id}/m3u")
    assert response.status_code == 200
    assert _entries(response.text) == []
    assert response.headers["x-m3u-skipped"] == "1"

    client.app.state.config.federation.remote_search_access = "public"
    try:
        response = client.get(f"/api/v1/playlists/{playlist.id}/m3u")
        assert response.status_code == 200
        assert _entries(response.text) == [f"http://testserver/api/v1/remote/objects/{remote.id}/stream"]
        assert "#EXTINF:-1,Remote Tune" in response.text
    finally:
        client.app.state.config.federation.remote_search_access = "authenticated"


@pytest.mark.asyncio
async def test_playlist_m3u_skips_track_without_audio(client, db_session, m3u_artist, regular_user):
    """Tracks without a playable media source are omitted and counted."""
    playlist = await _make_playlist(db_session, regular_user)
    track = await _make_track(db_session, None, m3u_artist, regular_user, with_audio=False)
    db_session.add(PlaylistTrack(playlist_id=playlist.id, track_id=track.id, position=1))
    await db_session.commit()

    response = client.get(f"/api/v1/playlists/{playlist.id}/m3u")
    assert response.status_code == 200
    assert _entries(response.text) == []
    assert response.headers["x-m3u-skipped"] == "1"


@pytest.mark.asyncio
async def test_playlist_m3u_filename_sanitized(client, db_session, regular_user):
    """The download filename is derived from a filesystem-safe playlist name."""
    playlist = await _make_playlist(db_session, regular_user, name='we/ir:d "mix"?')
    await db_session.commit()

    response = client.get(f"/api/v1/playlists/{playlist.id}/m3u")
    assert response.status_code == 200
    assert response.headers["content-disposition"] == 'attachment; filename="we_ir_d _mix__.m3u"'
