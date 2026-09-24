"""
RSS/Atom feed tests — ``/feeds`` endpoints, XML structure, visibility
filtering, per-category content and ``<link>`` discovery tags.
"""

import uuid
import xml.etree.ElementTree as ET

import pytest

from songhive.api.semantic_meta import feed_link_tags
from songhive.models._enums import Visibility
from songhive.models.activity import Activity
from songhive.models.album import Album
from songhive.models.artist import Artist
from songhive.models.external_track import ExternalTrack
from songhive.models.genre import Genre, GenreTrack
from songhive.models.library import Library
from songhive.models.library_track import LibraryTrack
from songhive.models.playlist import Playlist, PlaylistTrack
from songhive.models.tag import Tag, TagTrack
from songhive.models.track import Track
from songhive.models.user import User
from songhive.services.feeds import track_item

ATOM_NS = "{http://www.w3.org/2005/Atom}"


def _parse(response):
    return ET.fromstring(response.content)


def _rss_items(root):
    return root.find("channel").findall("item")


def _rss_titles(root):
    return [item.find("title").text for item in _rss_items(root)]


def _atom_entries(root):
    return root.findall(f"{ATOM_NS}entry")


def _atom_titles(root):
    return [entry.find(f"{ATOM_NS}title").text for entry in _atom_entries(root)]


async def _make_artist(session, name: str = "Feed Artist") -> Artist:
    artist = Artist(name=name, bio="Artist bio")
    session.add(artist)
    await session.flush()
    return artist


async def _make_album(
    session,
    owner: User,
    artist: Artist,
    title: str = "Feed Album",
    visibility: str = Visibility.PUBLIC.value,
) -> Album:
    album = Album(
        title=title,
        artist_id=artist.id,
        owner_id=owner.id,
        visibility=visibility,
    )
    session.add(album)
    await session.flush()
    return album


async def _make_track(
    session,
    owner: User,
    artist: Artist,
    title: str = "Feed Track",
    album: Album | None = None,
    visibility: str = Visibility.PUBLIC.value,
) -> Track:
    track = Track(
        title=title,
        artist_id=artist.id,
        album_id=album.id if album is not None else None,
        owner_id=owner.id,
        visibility=visibility,
    )
    session.add(track)
    await session.flush()
    return track


def _make_activity(entity_type: str, entity_id: str, **overrides) -> Activity:
    """Build a minimally valid Activity, allowing per-field overrides."""
    object_id = uuid.uuid4().hex[:12]
    params = {
        "entity_type": entity_type,
        "entity_id": str(entity_id),
        "activity_type": "create",
        "source_type": "local",
        "source_actor": "https://local.example/users/alice",
        "source_id": f"https://local.example/users/alice/objects/{object_id}",
        "local_object_id": object_id,
        "visibility": Visibility.PUBLIC.value,
    }
    params.update(overrides)
    return Activity(**params)


# ---------------------------------------------------------------------------
# Feed document structure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_user_feed_rss_structure(client, db_session, regular_user):
    activity = _make_activity(
        "user",
        regular_user.id,
        owner_user_id=regular_user.id,
        content="<p>hello</p>",
    )
    db_session.add(activity)
    await db_session.flush()

    resp = client.get(f"/feeds/users/{regular_user.username}.rss")

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/rss+xml")
    root = _parse(resp)
    assert root.tag == "rss"
    assert root.attrib["version"] == "2.0"
    channel = root.find("channel")
    assert channel.find("title").text == regular_user.username
    assert channel.find("link").text == f"http://testserver/@{regular_user.username}"
    items = _rss_items(root)
    assert len(items) == 1
    assert items[0].find("link").text == f"http://testserver/activities/{activity.id}"
    assert items[0].find("guid").text == f"http://testserver/activities/{activity.id}"
    assert items[0].find("pubDate") is not None
    assert "regular" in items[0].find("title").text


@pytest.mark.asyncio
async def test_user_feed_atom_structure(client, db_session, regular_user):
    activity = _make_activity("user", regular_user.id, owner_user_id=regular_user.id)
    db_session.add(activity)
    await db_session.flush()

    resp = client.get(f"/feeds/users/{regular_user.username}.atom")

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/atom+xml")
    root = _parse(resp)
    assert root.tag == f"{ATOM_NS}feed"
    assert root.find(f"{ATOM_NS}title").text == regular_user.username
    links = {link.attrib.get("rel"): link.attrib["href"] for link in root.findall(f"{ATOM_NS}link")}
    assert links["alternate"] == f"http://testserver/@{regular_user.username}"
    assert links["self"].endswith(f"/feeds/users/{regular_user.username}.atom")
    entries = _atom_entries(root)
    assert len(entries) == 1
    assert entries[0].find(f"{ATOM_NS}id").text == f"http://testserver/activities/{activity.id}"
    assert entries[0].find(f"{ATOM_NS}published") is not None
    author = entries[0].find(f"{ATOM_NS}author")
    assert author.find(f"{ATOM_NS}name").text == regular_user.username


@pytest.mark.asyncio
async def test_feed_unknown_format_404(client, regular_user):
    resp = client.get(f"/feeds/users/{regular_user.username}.xml")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_feed_unknown_user_404(client):
    resp = client.get("/feeds/users/nobody.rss")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Visibility filtering
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_user_feed_anonymous_sees_public_only(client, db_session, regular_user):
    public = _make_activity("user", regular_user.id, owner_user_id=regular_user.id)
    local = _make_activity(
        "user",
        regular_user.id,
        owner_user_id=regular_user.id,
        visibility=Visibility.LOCAL.value,
    )
    followers = _make_activity(
        "user",
        regular_user.id,
        owner_user_id=regular_user.id,
        visibility=Visibility.FOLLOWERS.value,
    )
    db_session.add_all([public, local, followers])
    await db_session.flush()

    resp = client.get(f"/feeds/users/{regular_user.username}.rss")
    items = _rss_items(_parse(resp))
    assert [i.find("guid").text for i in items] == [f"http://testserver/activities/{public.id}"]


@pytest.mark.asyncio
async def test_user_feed_authenticated_sees_local(client, db_session, regular_user, other_user, auth_headers):
    public = _make_activity("user", regular_user.id, owner_user_id=regular_user.id)
    local = _make_activity(
        "user",
        regular_user.id,
        owner_user_id=regular_user.id,
        visibility=Visibility.LOCAL.value,
    )
    db_session.add_all([public, local])
    await db_session.flush()

    resp = client.get(
        f"/feeds/users/{regular_user.username}.rss",
        headers=auth_headers(other_user),
    )
    guids = [i.find("guid").text for i in _rss_items(_parse(resp))]
    assert f"http://testserver/activities/{local.id}" in guids
    assert f"http://testserver/activities/{public.id}" in guids


# ---------------------------------------------------------------------------
# Entity activities feeds
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_track_activities_feed(client, db_session, regular_user):
    artist = await _make_artist(db_session)
    track = await _make_track(db_session, regular_user, artist)
    activity = _make_activity("track", track.id, owner_user_id=regular_user.id)
    db_session.add(activity)
    await db_session.flush()

    resp = client.get(f"/feeds/tracks/{track.id}/activities.rss")

    assert resp.status_code == 200
    root = _parse(resp)
    channel = root.find("channel")
    assert "Feed Track" in channel.find("title").text
    assert channel.find("link").text == f"http://testserver/tracks/{track.id}"
    items = _rss_items(root)
    assert [i.find("guid").text for i in items] == [f"http://testserver/activities/{activity.id}"]


@pytest.mark.asyncio
async def test_artist_activities_feed(client, db_session, regular_user):
    artist = await _make_artist(db_session)
    activity = _make_activity("artist", artist.id, owner_user_id=regular_user.id)
    db_session.add(activity)
    await db_session.flush()

    resp = client.get(f"/feeds/artists/{artist.id}/activities.atom")

    assert resp.status_code == 200
    entries = _atom_entries(_parse(resp))
    assert [e.find(f"{ATOM_NS}id").text for e in entries] == [f"http://testserver/activities/{activity.id}"]


@pytest.mark.asyncio
async def test_private_track_activities_feed_forbidden(client, db_session, regular_user, other_user):
    artist = await _make_artist(db_session)
    track = await _make_track(
        db_session,
        other_user,
        artist,
        visibility=Visibility.PRIVATE.value,
    )
    await db_session.flush()

    resp = client.get(f"/feeds/tracks/{track.id}/activities.rss")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_entity_activities_feed_unknown_collection_404(client):
    resp = client.get("/feeds/bogus/abc/activities.rss")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_entity_activities_feed_missing_entity_404(client):
    resp = client.get("/feeds/tracks/does-not-exist/activities.rss")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Artist releases feed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_artist_feed_dedupes_album_tracks(client, db_session, regular_user):
    """A track that belongs to an album is represented by the album entry."""
    artist = await _make_artist(db_session)
    album = await _make_album(db_session, regular_user, artist)
    await _make_track(db_session, regular_user, artist, title="Album Track", album=album)
    await _make_track(db_session, regular_user, artist, title="Single")
    await db_session.flush()

    resp = client.get(f"/feeds/artists/{artist.id}.rss")

    assert resp.status_code == 200
    titles = _rss_titles(_parse(resp))
    assert len(titles) == 2
    assert any("Feed Album" in title for title in titles)
    assert any("Single" in title for title in titles)
    assert not any("Album Track" in title for title in titles)


@pytest.mark.asyncio
async def test_artist_feed_private_content_hidden(client, db_session, regular_user, other_user):
    artist = await _make_artist(db_session)
    await _make_track(db_session, other_user, artist, title="Hidden", visibility=Visibility.PRIVATE.value)
    await _make_album(db_session, other_user, artist, title="Secret Album", visibility=Visibility.PRIVATE.value)
    await db_session.flush()

    resp = client.get(f"/feeds/artists/{artist.id}.rss")

    assert resp.status_code == 200
    assert _rss_items(_parse(resp)) == []


@pytest.mark.asyncio
async def test_artist_feed_missing_404(client):
    resp = client.get("/feeds/artists/does-not-exist.rss")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Playlist and library feeds
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_playlist_feed_lists_added_tracks(client, db_session, regular_user):
    artist = await _make_artist(db_session)
    track = await _make_track(db_session, regular_user, artist)
    private_track = await _make_track(
        db_session,
        regular_user,
        artist,
        title="Private Track",
        visibility=Visibility.PRIVATE.value,
    )
    playlist = Playlist(
        name="Feed Playlist",
        owner_id=regular_user.id,
        visibility=Visibility.PUBLIC.value,
    )
    db_session.add(playlist)
    await db_session.flush()
    db_session.add_all(
        [
            PlaylistTrack(playlist_id=playlist.id, track_id=track.id, position=0),
            PlaylistTrack(playlist_id=playlist.id, track_id=private_track.id, position=1),
        ]
    )
    await db_session.flush()

    resp = client.get(f"/feeds/playlists/{playlist.id}.rss")

    assert resp.status_code == 200
    root = _parse(resp)
    assert root.find("channel").find("title").text == "Feed Playlist"
    titles = _rss_titles(root)
    # The private track is filtered for anonymous readers even though the
    # playlist itself is public.
    assert len(titles) == 1
    assert "Feed Track" in titles[0]


@pytest.mark.asyncio
async def test_playlist_feed_includes_episodes(client, db_session, regular_user):
    """Playlist feeds carry podcast episodes with their remote enclosure URL."""
    from songhive.models.podcast import Podcast, PodcastEpisode

    artist = await _make_artist(db_session)
    track = await _make_track(db_session, regular_user, artist)
    podcast = Podcast(
        feed_url="https://pod.example/feed.xml",
        title="Feed Show",
        author="Feed Author",
    )
    db_session.add(podcast)
    await db_session.flush()
    episode = PodcastEpisode(
        podcast_id=podcast.id,
        guid="ep-1",
        title="Feed Episode",
        link="https://pod.example/ep1",
        audio_url="https://cdn.pod.example/ep1.mp3",
        audio_type="audio/mpeg",
        audio_length=12345,
    )
    db_session.add(episode)
    playlist = Playlist(
        name="Feed Playlist",
        owner_id=regular_user.id,
        visibility=Visibility.PUBLIC.value,
    )
    db_session.add(playlist)
    await db_session.flush()
    db_session.add_all(
        [
            PlaylistTrack(playlist_id=playlist.id, track_id=track.id, position=0),
            PlaylistTrack(playlist_id=playlist.id, podcast_episode_id=episode.id, position=1),
        ]
    )
    await db_session.flush()

    resp = client.get(f"/feeds/playlists/{playlist.id}.rss")

    assert resp.status_code == 200
    items = _rss_items(_parse(resp))
    assert len(items) == 2
    episode_item = next(i for i in items if "Feed Episode" in (i.find("title").text or ""))
    # Episode entries keep the upstream enclosure — readers stream the source.
    enclosure = episode_item.find("enclosure")
    assert enclosure is not None
    assert enclosure.attrib["url"] == "https://cdn.pod.example/ep1.mp3"
    assert enclosure.attrib["type"] == "audio/mpeg"
    assert enclosure.attrib["length"] == "12345"
    assert episode_item.find("link").text == "https://pod.example/ep1"


@pytest.mark.asyncio
async def test_private_playlist_feed_forbidden(client, db_session, other_user):
    playlist = Playlist(
        name="Secret Playlist",
        owner_id=other_user.id,
        visibility=Visibility.PRIVATE.value,
    )
    db_session.add(playlist)
    await db_session.flush()

    resp = client.get(f"/feeds/playlists/{playlist.id}.rss")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_library_feed_lists_added_tracks(client, db_session, regular_user):
    artist = await _make_artist(db_session)
    track = await _make_track(db_session, regular_user, artist)
    library = Library(
        name="Feed Library",
        owner_id=regular_user.id,
        visibility=Visibility.PUBLIC.value,
    )
    db_session.add(library)
    await db_session.flush()
    db_session.add(LibraryTrack(library_id=library.id, track_id=track.id))
    await db_session.flush()

    resp = client.get(f"/feeds/libraries/{library.id}.atom")

    assert resp.status_code == 200
    root = _parse(resp)
    assert root.find(f"{ATOM_NS}title").text == "Feed Library"
    entries = _atom_entries(root)
    assert len(entries) == 1
    assert "Feed Track" in entries[0].find(f"{ATOM_NS}title").text


# ---------------------------------------------------------------------------
# Tag and genre feeds
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tag_feed_lists_tagged_items(client, db_session, regular_user):
    artist = await _make_artist(db_session)
    track = await _make_track(db_session, regular_user, artist)
    tag = Tag(name="rock")
    db_session.add(tag)
    await db_session.flush()
    db_session.add(TagTrack(tag_id=tag.id, track_id=track.id))
    await db_session.flush()

    resp = client.get("/feeds/tags/rock.rss")

    assert resp.status_code == 200
    root = _parse(resp)
    assert root.find("channel").find("title").text == "#rock"
    titles = _rss_titles(root)
    assert titles == ["Feed Track"]


@pytest.mark.asyncio
async def test_tag_feed_hides_private_items(client, db_session, regular_user, other_user):
    artist = await _make_artist(db_session)
    track = await _make_track(
        db_session,
        other_user,
        artist,
        title="Private Tagged",
        visibility=Visibility.PRIVATE.value,
    )
    tag = Tag(name="rock")
    db_session.add(tag)
    await db_session.flush()
    db_session.add(TagTrack(tag_id=tag.id, track_id=track.id))
    await db_session.flush()

    resp = client.get("/feeds/tags/rock.rss")

    assert resp.status_code == 200
    assert _rss_items(_parse(resp)) == []


@pytest.mark.asyncio
async def test_tag_feed_unknown_tag_404(client):
    resp = client.get("/feeds/tags/definitelynotatag.rss")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_genre_feed_lists_tracks_and_albums(client, db_session, regular_user):
    artist = await _make_artist(db_session)
    track = await _make_track(db_session, regular_user, artist)
    album = await _make_album(db_session, regular_user, artist, title="Genre Album")
    genre = Genre(name="rock")
    db_session.add(genre)
    await db_session.flush()
    db_session.add(GenreTrack(genre_id=genre.id, track_id=track.id))
    await db_session.flush()
    # Albums join the genre through their track membership is not automatic;
    # the album must carry the association itself. Add it explicitly.
    from songhive.models.genre import GenreAlbum

    db_session.add(GenreAlbum(genre_id=genre.id, album_id=album.id))
    await db_session.flush()

    resp = client.get("/feeds/genres/rock.rss")

    assert resp.status_code == 200
    titles = _rss_titles(_parse(resp))
    assert len(titles) == 2
    assert any("Feed Track" in title for title in titles)
    assert any("Genre Album" in title for title in titles)


@pytest.mark.asyncio
async def test_genre_feed_unknown_genre_404(client):
    resp = client.get("/feeds/genres/definitelynotagenre.rss")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Limits and disabled feeds
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_feed_limit_is_configurable(client, db_session, regular_user):
    artist = await _make_artist(db_session)
    for i in range(3):
        await _make_track(db_session, regular_user, artist, title=f"Track {i}")
    await db_session.flush()

    client.app.state.config.feeds.max_items = 2
    try:
        resp = client.get(f"/feeds/artists/{artist.id}.rss")
        assert len(_rss_items(_parse(resp))) == 2
        # An explicit ?limit= cannot exceed the configured maximum.
        resp = client.get(f"/feeds/artists/{artist.id}.rss?limit=3")
        assert len(_rss_items(_parse(resp))) == 2
    finally:
        client.app.state.config.feeds.max_items = 20


@pytest.mark.asyncio
async def test_feeds_disabled_returns_404(client, regular_user):
    client.app.state.config.feeds.enabled = False
    try:
        resp = client.get(f"/feeds/users/{regular_user.username}.rss")
        assert resp.status_code == 404
    finally:
        client.app.state.config.feeds.enabled = True


# ---------------------------------------------------------------------------
# <link> discovery tags
# ---------------------------------------------------------------------------


def test_feed_link_tags_entity_feeds():
    tags = feed_link_tags("artist", "abc", "http://testserver")
    rss_link = (
        '<link rel="alternate" type="application/rss+xml" '
        'href="http://testserver/feeds/artists/abc.rss" title="RSS feed">'
    )
    atom_link = (
        '<link rel="alternate" type="application/atom+xml" '
        'href="http://testserver/feeds/artists/abc.atom" title="Atom feed">'
    )
    assert rss_link in tags
    assert atom_link in tags


def test_feed_link_tags_activities_variant():
    tags = feed_link_tags("artist", "abc", "http://testserver", activities=True)
    assert "http://testserver/feeds/artists/abc/activities.rss" in tags[0]
    assert "http://testserver/feeds/artists/abc/activities.atom" in tags[1]


def test_feed_link_tags_track_uses_activities_feed():
    tags = feed_link_tags("track", "abc", "http://testserver")
    assert "/feeds/tracks/abc/activities.rss" in tags[0]


def test_feed_link_tags_user_and_tag():
    assert "/feeds/users/alice.rss" in feed_link_tags("user", "alice", "http://testserver")[0]
    assert "/feeds/tags/rock.atom" in feed_link_tags("tag", "rock", "http://testserver")[1]


async def test_track_page_injects_feed_links(client, db_session, regular_user):
    artist = await _make_artist(db_session)
    track = await _make_track(db_session, regular_user, artist)
    await db_session.commit()

    resp = client.get(f"/tracks/{track.id}")
    assert resp.status_code == 200
    text = resp.text
    rss_href = f"http://testserver/feeds/tracks/{track.id}/activities.rss"
    atom_href = f"http://testserver/feeds/tracks/{track.id}/activities.atom"
    assert f'<link rel="alternate" type="application/rss+xml" href="{rss_href}" title="RSS feed">' in text
    assert f'<link rel="alternate" type="application/atom+xml" href="{atom_href}" title="Atom feed">' in text


async def test_artist_page_injects_releases_feed_links(client, db_session):
    artist = await _make_artist(db_session)
    await db_session.commit()

    resp = client.get(f"/artists/{artist.id}")
    text = resp.text
    assert f"/feeds/artists/{artist.id}.rss" in text
    assert f"/feeds/artists/{artist.id}.atom" in text


async def test_artist_activities_page_injects_activities_feed_links(client, db_session):
    artist = await _make_artist(db_session)
    await db_session.commit()

    resp = client.get(f"/artists/{artist.id}/activities")
    text = resp.text
    assert f"/feeds/artists/{artist.id}/activities.rss" in text


async def test_user_profile_page_injects_feed_links(client, db_session, regular_user):
    await db_session.commit()

    resp = client.get("/@regular")
    text = resp.text
    assert f"/feeds/users/{regular_user.username}.rss" in text
    assert f"/feeds/users/{regular_user.username}.atom" in text


async def test_feed_links_omitted_when_disabled(client, db_session, regular_user):
    artist = await _make_artist(db_session)
    track = await _make_track(db_session, regular_user, artist)
    await db_session.commit()

    client.app.state.config.feeds.enabled = False
    try:
        resp = client.get(f"/tracks/{track.id}")
        assert "/feeds/" not in resp.text
    finally:
        client.app.state.config.feeds.enabled = True


def test_track_item_external_enclosure():
    """An active external track gets a stream enclosure in feed items."""
    track = Track(title="External Track", artist_id="artist-1", visibility=Visibility.PUBLIC.value)
    track.id = "track-1"
    track.external_track = ExternalTrack(
        external_library_id="lib-1",
        provider_key="music/song.flac",
        provider_mime_type="audio/flac",
        provider_size=30_000_000,
        state="active",
    )

    item = track_item(track, "https://local.example")

    assert item.enclosure_url == "https://local.example/api/v1/stream/track-1"
    assert item.enclosure_type == "audio/flac"
    assert item.enclosure_length == 30_000_000


def test_track_item_inactive_external_track_has_no_enclosure():
    """A non-active external backing produces no feed enclosure."""
    track = Track(title="External Track", artist_id="artist-1", visibility=Visibility.PUBLIC.value)
    track.id = "track-1"
    track.external_track = ExternalTrack(
        external_library_id="lib-1",
        provider_key="music/song.flac",
        state="missing",
    )

    item = track_item(track, "https://local.example")

    assert item.enclosure_url is None
