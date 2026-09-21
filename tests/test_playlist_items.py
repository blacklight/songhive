"""Tests for mixed playlist items — tracks and podcast episodes in one playlist."""

import pytest
from sqlalchemy.exc import IntegrityError

from songhive.models._enums import Visibility
from songhive.models.artist import Artist
from songhive.models.playlist import Playlist, PlaylistTrack
from songhive.models.podcast import Podcast, PodcastEpisode
from songhive.models.track import Track
from songhive.services import podcasts as podcasts_service
from songhive.services.podcasts import FetchResult

FEED_URL = "https://pod.example/feed.xml"

RSS_FEED = b"""<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
  <channel>
    <title>Test Show</title>
    <link>https://pod.example/</link>
    <description>A test podcast.</description>
    <itunes:author>Jane Doe</itunes:author>
    <item>
      <title>Episode One</title>
      <guid>ep-1</guid>
      <link>https://pod.example/ep1</link>
      <pubDate>Mon, 01 Sep 2025 10:00:00 GMT</pubDate>
      <enclosure url="https://cdn.pod.example/ep1.mp3" type="audio/mpeg" length="12345"/>
      <itunes:duration>12:34</itunes:duration>
    </item>
    <item>
      <title>Episode Two</title>
      <guid>ep-2</guid>
      <pubDate>Mon, 08 Sep 2025 10:00:00 GMT</pubDate>
      <enclosure url="https://cdn.pod.example/ep2.mp3" type="audio/mpeg" length="999"/>
      <itunes:duration>3600</itunes:duration>
    </item>
  </channel>
</rss>
"""


@pytest.fixture
async def podcast(db_session, regular_user, config, monkeypatch):
    """Subscribe ``regular_user`` to a stubbed two-episode feed."""
    monkeypatch.setattr(
        podcasts_service,
        "fetch_feed",
        lambda *a, **kw: FetchResult(body=RSS_FEED, final_url=FEED_URL),
    )
    podcast, _ = await podcasts_service.subscribe(db_session, regular_user, FEED_URL, config)
    await db_session.commit()
    return podcast


async def _episodes(db_session, podcast) -> list[PodcastEpisode]:
    """Return the podcast's episodes oldest first."""
    episodes, _ = await podcasts_service.list_episodes(db_session, podcast.id, oldest_first=True)
    return episodes


async def _make_playlist(db_session, owner, name="Mix") -> Playlist:
    playlist = Playlist(name=name, owner_id=owner.id, visibility=Visibility.PUBLIC.value)
    db_session.add(playlist)
    await db_session.flush()
    return playlist


async def _make_track(
    db_session,
    owner,
    title="Mix Track",
    duration=90.0,
    visibility=Visibility.PUBLIC.value,
) -> Track:
    artist = Artist(name="Mix Artist")
    db_session.add(artist)
    await db_session.flush()
    track = Track(
        title=title,
        artist_id=artist.id,
        owner_id=owner.id,
        visibility=visibility,
        duration=duration,
    )
    db_session.add(track)
    await db_session.flush()
    return track


def _create_playlist(client, user, auth_headers, name="Mix") -> dict:
    response = client.post(
        "/api/v1/playlists/",
        params={"visibility": "public"},
        json={"name": name},
        headers=auth_headers(user),
    )
    assert response.status_code == 201
    return response.json()


def _item_types(items) -> list[str]:
    return [item["type"] for item in items]


@pytest.mark.asyncio
async def test_check_constraint_rejects_invalid_rows(db_session, regular_user):
    """A playlist row must carry exactly one of track_id / podcast_episode_id."""
    playlist = await _make_playlist(db_session, regular_user)
    podcast = Podcast(feed_url=FEED_URL, title="Show")
    db_session.add(podcast)
    await db_session.flush()

    db_session.add(PlaylistTrack(playlist_id=playlist.id, position=1))
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_add_episode_ids_to_playlist(client, db_session, regular_user, auth_headers, podcast):
    """``episode_ids`` on the add endpoint appends episode items."""
    playlist = _create_playlist(client, regular_user, auth_headers)
    episodes = await _episodes(db_session, podcast)

    response = client.post(
        f"/api/v1/playlists/{playlist['id']}/tracks",
        json={"episode_ids": [str(episodes[0].id)]},
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 201
    body = response.json()
    assert body["added"] == 1
    assert body["track_ids"] == []
    assert body["episode_ids"] == [str(episodes[0].id)]

    items = client.get(f"/api/v1/playlists/{playlist['id']}/items", headers=auth_headers(regular_user)).json()
    assert len(items) == 1
    item = items[0]
    assert item["type"] == "episode"
    assert item["episode"]["id"] == str(episodes[0].id)
    assert item["episode"]["title"] == "Episode One"
    assert item["episode"]["podcast_title"] == "Test Show"
    assert item["episode"]["audio_url"] == "https://cdn.pod.example/ep1.mp3"
    assert item["episode"]["played"] is False


@pytest.mark.asyncio
async def test_add_podcast_id_adds_all_episodes_oldest_first(client, db_session, regular_user, auth_headers, podcast):
    """``podcast_id`` adds every cataloged episode, oldest first."""
    playlist = _create_playlist(client, regular_user, auth_headers)

    response = client.post(
        f"/api/v1/playlists/{playlist['id']}/tracks",
        json={"podcast_id": str(podcast.id)},
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 201
    assert response.json()["added"] == 2

    items = client.get(f"/api/v1/playlists/{playlist['id']}/items", headers=auth_headers(regular_user)).json()
    assert _item_types(items) == ["episode", "episode"]
    assert [item["episode"]["title"] for item in items] == ["Episode One", "Episode Two"]


@pytest.mark.asyncio
async def test_items_lists_mixed_rows_in_position_order(client, db_session, regular_user, auth_headers, podcast):
    """The items endpoint returns tracks and episodes interleaved by position."""
    playlist = _create_playlist(client, regular_user, auth_headers)
    track = await _make_track(db_session, regular_user)
    episodes = await _episodes(db_session, podcast)
    headers = auth_headers(regular_user)

    response = client.post(
        f"/api/v1/playlists/{playlist['id']}/tracks",
        json={"track_ids": [str(track.id)], "episode_ids": [str(episodes[0].id)]},
        headers=headers,
    )
    assert response.status_code == 201
    assert response.json()["added"] == 2

    items = client.get(f"/api/v1/playlists/{playlist['id']}/items", headers=headers).json()
    assert _item_types(items) == ["track", "episode"]
    assert items[0]["track"]["id"] == str(track.id)
    assert items[1]["episode"]["id"] == str(episodes[0].id)
    assert [item["position"] for item in items] == [1, 2]

    # The legacy track-only listing keeps working and skips episodes.
    tracks_resp = client.get(f"/api/v1/playlists/{playlist['id']}/tracks", headers=headers)
    assert [entry["id"] for entry in tracks_resp.json()] == [str(track.id)]
    assert tracks_resp.headers["X-Total-Count"] == "1"


@pytest.mark.asyncio
async def test_add_duplicate_episode_conflicts_then_allows(client, db_session, regular_user, auth_headers, podcast):
    """Duplicate episode ids produce a 409 unless ``allow_duplicates`` is set."""
    playlist = _create_playlist(client, regular_user, auth_headers)
    episodes = await _episodes(db_session, podcast)
    headers = auth_headers(regular_user)

    client.post(
        f"/api/v1/playlists/{playlist['id']}/tracks",
        json={"episode_ids": [str(episodes[0].id)]},
        headers=headers,
    )

    conflict = client.post(
        f"/api/v1/playlists/{playlist['id']}/tracks",
        json={"episode_ids": [str(episodes[0].id)]},
        headers=headers,
    )
    assert conflict.status_code == 409
    assert conflict.json()["episode_ids"] == [str(episodes[0].id)]

    allowed = client.post(
        f"/api/v1/playlists/{playlist['id']}/tracks",
        json={"episode_ids": [str(episodes[0].id)], "allow_duplicates": True},
        headers=headers,
    )
    assert allowed.status_code == 201
    assert allowed.json()["added"] == 1


@pytest.mark.asyncio
async def test_remove_mixed_items(client, db_session, regular_user, auth_headers, podcast):
    """The remove endpoint accepts ``episode_ids`` alongside ``track_ids``."""
    playlist = _create_playlist(client, regular_user, auth_headers)
    track = await _make_track(db_session, regular_user)
    episodes = await _episodes(db_session, podcast)
    headers = auth_headers(regular_user)

    client.post(
        f"/api/v1/playlists/{playlist['id']}/tracks",
        json={"track_ids": [str(track.id)], "episode_ids": [str(e.id) for e in episodes]},
        headers=headers,
    )

    response = client.post(
        f"/api/v1/playlists/{playlist['id']}/tracks/remove",
        json={"episode_ids": [str(episodes[0].id)]},
        headers=headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["removed"] == 1
    assert body["episode_ids"] == [str(episodes[0].id)]
    assert body["track_ids"] == []

    items = client.get(f"/api/v1/playlists/{playlist['id']}/items", headers=headers).json()
    assert _item_types(items) == ["track", "episode"]
    # Positions are renormalized after removal.
    assert [item["position"] for item in items] == [1, 2]


@pytest.mark.asyncio
async def test_reorder_accepts_mixed_item_ids(client, db_session, regular_user, auth_headers, podcast):
    """``item_ids`` moves an episode ahead of a track."""
    playlist = _create_playlist(client, regular_user, auth_headers)
    track = await _make_track(db_session, regular_user)
    episodes = await _episodes(db_session, podcast)
    headers = auth_headers(regular_user)

    client.post(
        f"/api/v1/playlists/{playlist['id']}/tracks",
        json={"track_ids": [str(track.id)], "episode_ids": [str(episodes[0].id)]},
        headers=headers,
    )

    response = client.post(
        f"/api/v1/playlists/{playlist['id']}/tracks/reorder",
        json={"item_ids": [str(episodes[0].id)], "position": 1},
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json()["item_ids"] == [str(episodes[0].id)]

    items = client.get(f"/api/v1/playlists/{playlist['id']}/items", headers=headers).json()
    assert _item_types(items) == ["episode", "track"]

    # The deprecated ``track_ids`` alias still works.
    response = client.post(
        f"/api/v1/playlists/{playlist['id']}/tracks/reorder",
        json={"track_ids": [str(track.id)], "position": 1},
        headers=headers,
    )
    assert response.status_code == 200
    items = client.get(f"/api/v1/playlists/{playlist['id']}/items", headers=headers).json()
    assert _item_types(items) == ["track", "episode"]


@pytest.mark.asyncio
async def test_playlist_stats_count_episodes(client, db_session, regular_user, auth_headers, podcast):
    """Stats report track_count, episode_count and combined duration."""
    playlist = _create_playlist(client, regular_user, auth_headers)
    track = await _make_track(db_session, regular_user, duration=90.0)
    episodes = await _episodes(db_session, podcast)
    headers = auth_headers(regular_user)

    client.post(
        f"/api/v1/playlists/{playlist['id']}/tracks",
        json={"track_ids": [str(track.id)], "episode_ids": [str(episodes[0].id)]},
        headers=headers,
    )

    stats = client.get(f"/api/v1/playlists/{playlist['id']}/stats", headers=headers).json()
    assert stats["track_count"] == 1
    assert stats["episode_count"] == 1
    # 90s track + 754s episode (12:34).
    assert stats["total_duration"] == pytest.approx(90.0 + 754.0)


@pytest.mark.asyncio
async def test_items_search_matches_episodes(client, db_session, regular_user, auth_headers, podcast):
    """The ``q`` filter matches episode titles and podcast metadata."""
    playlist = _create_playlist(client, regular_user, auth_headers)
    track = await _make_track(db_session, regular_user)
    episodes = await _episodes(db_session, podcast)
    headers = auth_headers(regular_user)

    client.post(
        f"/api/v1/playlists/{playlist['id']}/tracks",
        json={"track_ids": [str(track.id)], "episode_ids": [str(e.id) for e in episodes]},
        headers=headers,
    )

    response = client.get(
        f"/api/v1/playlists/{playlist['id']}/items",
        params={"q": "episode two"},
        headers=headers,
    )
    assert _item_types(response.json()) == ["episode"]

    # Podcast title matches too.
    response = client.get(
        f"/api/v1/playlists/{playlist['id']}/items",
        params={"q": "test show"},
        headers=headers,
    )
    assert _item_types(response.json()) == ["episode", "episode"]


@pytest.mark.asyncio
async def test_items_marks_played_episodes(client, db_session, regular_user, auth_headers, podcast):
    """The items payload carries the caller's per-episode played flag."""
    playlist = _create_playlist(client, regular_user, auth_headers)
    episodes = await _episodes(db_session, podcast)
    headers = auth_headers(regular_user)

    client.post(
        f"/api/v1/playlists/{playlist['id']}/tracks",
        json={"episode_ids": [str(e.id) for e in episodes]},
        headers=headers,
    )
    client.post(f"/api/v1/podcasts/episodes/{episodes[0].id}/played", headers=headers)

    items = client.get(f"/api/v1/playlists/{playlist['id']}/items", headers=headers).json()
    assert [item["episode"]["played"] for item in items] == [True, False]


@pytest.mark.asyncio
async def test_add_episode_rejected_when_podcasts_disabled(
    client, db_session, regular_user, auth_headers, config, podcast
):
    """Episode sources are rejected with 422 when the podcasts feature is off."""
    config.podcasts.enabled = False
    playlist = _create_playlist(client, regular_user, auth_headers)
    episodes = await _episodes(db_session, podcast)

    for body in ({"episode_ids": [str(episodes[0].id)]}, {"podcast_id": str(podcast.id)}):
        response = client.post(
            f"/api/v1/playlists/{playlist['id']}/tracks",
            json=body,
            headers=auth_headers(regular_user),
        )
        assert response.status_code == 422


@pytest.mark.asyncio
async def test_items_hides_inaccessible_tracks_but_keeps_episodes(
    client, db_session, regular_user, other_user, auth_headers, podcast
):
    """Anonymous/non-owner viewers still see episode rows while private tracks drop out."""
    playlist = _create_playlist(client, regular_user, auth_headers)
    episodes = await _episodes(db_session, podcast)
    headers = auth_headers(regular_user)

    private_track = await _make_track(
        db_session,
        regular_user,
        title="Secret Track",
        visibility=Visibility.PRIVATE.value,
    )
    await db_session.commit()

    client.post(
        f"/api/v1/playlists/{playlist['id']}/tracks",
        json={"track_ids": [str(private_track.id)], "episode_ids": [str(episodes[0].id)]},
        headers=headers,
    )

    items = client.get(f"/api/v1/playlists/{playlist['id']}/items", headers=auth_headers(other_user)).json()
    assert _item_types(items) == ["episode"]


@pytest.mark.asyncio
async def test_add_unknown_episode_ids_ignored(client, regular_user, auth_headers):
    """Episode ids that don't exist in the catalog resolve to nothing."""
    playlist = _create_playlist(client, regular_user, auth_headers)
    response = client.post(
        f"/api/v1/playlists/{playlist['id']}/tracks",
        json={"episode_ids": ["00000000-0000-0000-0000-000000000000"]},
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 201
    assert response.json()["added"] == 0


@pytest.mark.asyncio
async def test_episode_items_removed_with_episode_row(client, db_session, regular_user, auth_headers, podcast):
    """Deleting a podcast episode cascades its playlist entries away."""
    playlist = _create_playlist(client, regular_user, auth_headers)
    episodes = await _episodes(db_session, podcast)
    headers = auth_headers(regular_user)

    client.post(
        f"/api/v1/playlists/{playlist['id']}/tracks",
        json={"episode_ids": [str(episodes[0].id)]},
        headers=headers,
    )

    await db_session.delete(episodes[0])
    await db_session.commit()

    items = client.get(f"/api/v1/playlists/{playlist['id']}/items", headers=headers).json()
    assert items == []
