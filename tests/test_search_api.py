"""Tests for the aggregate /api/v1/search/ endpoint."""

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from pubby import Follower

from songhive.api.app import create_app
from songhive.api.deps import get_db
from songhive.federation.storage import create_activitypub_storage
from songhive.models._enums import Visibility
from songhive.models.album import Album
from songhive.models.artist import Artist
from songhive.models.base import init_db
from songhive.models.genre import Genre, GenreTrack
from songhive.models.library import Library
from songhive.models.playlist import Playlist
from songhive.models.tag import Tag, TagTrack
from songhive.models.track import Track
from songhive.models.user import User

BOB_ACTOR = "https://remote.example/users/bob"
CAROL_ACTOR = "https://mastodon.example/@carol"

BOB_DOC = {
    "id": BOB_ACTOR,
    "type": "Person",
    "preferredUsername": "bob",
    "name": "Bob Remote",
    "url": "https://remote.example/@bob",
    "inbox": "https://remote.example/users/bob/inbox",
    "icon": {"type": "Image", "url": "https://remote.example/bob.png"},
}

CAROL_DOC = {
    "id": CAROL_ACTOR,
    "type": "Person",
    "preferredUsername": "carol",
    "name": "Carol Elsewhere",
    "inbox": "https://mastodon.example/users/carol/inbox",
}


@pytest.fixture
def fed_config(config):
    """Federation-enabled copy of the test config."""
    fed = config.model_copy(deep=True)
    fed.federation.enabled = True
    fed.federation.instance_domain = "music.example.com"
    return fed


@pytest.fixture
def fed_app(fed_config, engine):
    """Create a federation-enabled test application."""
    init_db(engine=engine, force=True)
    return create_app(fed_config)


@pytest.fixture
def fed_client(fed_app, db_session, fake_redis_server, monkeypatch):
    """Test client bound to the federation-enabled app."""
    from fakeredis.aioredis import FakeRedis

    def _get_redis_client(_):
        return FakeRedis(server=fake_redis_server, decode_responses=True)

    monkeypatch.setattr("songhive.api.app.get_redis_client", _get_redis_client)

    async def _db():
        yield db_session

    with TestClient(fed_app) as client:
        client.app.dependency_overrides[get_db] = _db  # type: ignore
        yield client
        client.app.dependency_overrides.pop(get_db, None)  # type: ignore


def _seed_remote_actors(fed_config):
    """Store a remote follower and a cached actor in the federation tables."""
    storage = create_activitypub_storage(fed_config.database.url)
    storage.store_follower(
        Follower(
            actor_id=BOB_ACTOR,
            inbox=f"{BOB_ACTOR}/inbox",
            followed_at=datetime.now(timezone.utc),
            actor_data=BOB_DOC,
            target_actor_id="https://music.example.com/users/regular",
        )
    )
    storage.cache_remote_actor(CAROL_ACTOR, CAROL_DOC, datetime.now(timezone.utc))


async def _make_artist(session, name: str = "Test Artist") -> Artist:
    artist = Artist(name=name)
    session.add(artist)
    await session.flush()
    return artist


async def _make_album(
    session,
    artist: Artist,
    title: str = "Test Album",
    owner: User | None = None,
    visibility: str = Visibility.PUBLIC.value,
) -> Album:
    album = Album(
        title=title,
        artist_id=artist.id,
        owner_id=owner.id if owner is not None else None,
        visibility=visibility,
    )
    session.add(album)
    await session.flush()
    return album


async def _make_track(
    session,
    artist: Artist,
    album: Album | None = None,
    title: str = "Test Track",
    owner: User | None = None,
    visibility: str = Visibility.PUBLIC.value,
) -> Track:
    track = Track(
        title=title,
        artist_id=artist.id,
        album_id=album.id if album is not None else None,
        owner_id=owner.id if owner is not None else None,
        visibility=visibility,
    )
    session.add(track)
    await session.flush()
    return track


async def _make_playlist(
    session,
    owner: User,
    name: str = "Test Playlist",
    description: str | None = None,
    visibility: str = Visibility.PUBLIC.value,
) -> Playlist:
    playlist = Playlist(
        name=name,
        owner_id=owner.id,
        description=description,
        visibility=visibility,
    )
    session.add(playlist)
    await session.flush()
    return playlist


async def _make_library(
    session,
    owner: User,
    name: str = "Test Library",
    description: str | None = None,
    visibility: str = Visibility.PUBLIC.value,
) -> Library:
    library = Library(
        name=name,
        owner_id=owner.id,
        description=description,
        visibility=visibility,
    )
    session.add(library)
    await session.flush()
    return library


async def _make_tag(session, name: str) -> Tag:
    tag = Tag(name=name)
    session.add(tag)
    await session.flush()
    return tag


async def _make_genre(session, name: str) -> Genre:
    genre = Genre(name=name)
    session.add(genre)
    await session.flush()
    return genre


def _section_ids(data, entity):
    for section in data["sections"]:
        if section["entity"] == entity:
            return [item["id"] for item in section["items"]]
    return []


def _section_total(data, entity):
    for section in data["sections"]:
        if section["entity"] == entity:
            return section["total"]
    return 0


@pytest.mark.asyncio
async def test_search_empty_query_returns_empty(client):
    """An empty q short-circuits to an empty response."""
    response = client.get("/api/v1/search")
    assert response.status_code == 200
    data = response.json()
    assert data["query"] == ""
    assert data["sections"] == []

    response = client.get("/api/v1/search?q=")
    assert response.status_code == 200
    data = response.json()
    assert data["query"] == ""
    assert data["sections"] == []


@pytest.mark.asyncio
async def test_search_entity_allowlist(client, regular_user, db_session, auth_headers):
    """The ``entities`` allowlist limits the returned sections."""
    artist = await _make_artist(db_session, name="Rep")
    album = await _make_album(db_session, artist, title="Repeater", owner=regular_user)
    await db_session.commit()

    response = client.get("/api/v1/search?q=rep&entities=artists,albums")
    assert response.status_code == 200
    data = response.json()
    assert [s["entity"] for s in data["sections"]] == ["albums", "artists"]
    assert _section_ids(data, "artists") == [str(artist.id)]
    assert _section_ids(data, "albums") == [str(album.id)]
    assert _section_total(data, "tracks") == 0


@pytest.mark.asyncio
async def test_search_respects_limit(client, regular_user, db_session):
    """``limit`` caps per-section items but total counts remain unfiltered."""
    artist = await _make_artist(db_session, name="Shared Artist")
    for i in range(3):
        await _make_track(
            db_session,
            artist,
            title=f"Track {i}",
            owner=regular_user,
        )
    await db_session.commit()

    response = client.get("/api/v1/search?q=track&entities=tracks&limit=2")
    assert response.status_code == 200
    data = response.json()
    assert len(data["sections"]) == 1
    section = data["sections"][0]
    assert section["entity"] == "tracks"
    assert len(section["items"]) == 2
    assert section["total"] == 3


@pytest.mark.asyncio
async def test_search_visibility_anonymous(client, regular_user, other_user, db_session, auth_headers):
    """Anonymous users only see public items; owners see their private ones."""
    artist = await _make_artist(db_session, name="Visible Artist")
    public_track = await _make_track(
        db_session,
        artist,
        title="Public Track",
        owner=regular_user,
        visibility=Visibility.PUBLIC.value,
    )
    private_track = await _make_track(
        db_session,
        artist,
        title="Private Track",
        owner=regular_user,
        visibility=Visibility.PRIVATE.value,
    )
    other_private = await _make_track(
        db_session,
        artist,
        title="Other Private Track",
        owner=other_user,
        visibility=Visibility.PRIVATE.value,
    )
    await db_session.commit()

    response = client.get("/api/v1/search?q=track&entities=tracks")
    assert response.status_code == 200
    data = response.json()
    ids = _section_ids(data, "tracks")
    assert str(public_track.id) in ids
    assert str(private_track.id) not in ids
    assert str(other_private.id) not in ids

    response = client.get(
        "/api/v1/search?q=track&entities=tracks",
        headers=auth_headers(regular_user),
    )
    data = response.json()
    ids = _section_ids(data, "tracks")
    assert str(public_track.id) in ids
    assert str(private_track.id) in ids
    assert str(other_private.id) not in ids


@pytest.mark.asyncio
async def test_search_includes_subtitles_and_urls(client, regular_user, db_session):
    """Tracks, albums, and artists include useful subtitle and URL metadata."""
    artist = await _make_artist(db_session, name="Paranoid Collective")
    album = await _make_album(db_session, artist, title="Paranoid", owner=regular_user)
    track = await _make_track(db_session, artist, album, title="Paranoid Android", owner=regular_user)
    await db_session.commit()

    response = client.get("/api/v1/search?q=paranoid&entities=tracks,albums,artists")
    assert response.status_code == 200
    data = response.json()

    track_items = [s for s in data["sections"] if s["entity"] == "tracks"][0]["items"]
    assert len(track_items) == 1
    assert track_items[0]["url"] == f"/tracks/{track.id}"
    assert "Paranoid Collective" in track_items[0]["subtitle"]
    assert "Paranoid" in track_items[0]["subtitle"]

    album_items = [s for s in data["sections"] if s["entity"] == "albums"][0]["items"]
    assert album_items[0]["url"] == f"/albums/{album.id}"
    assert album_items[0]["subtitle"] == "Paranoid Collective"

    artist_items = [s for s in data["sections"] if s["entity"] == "artists"][0]["items"]
    assert artist_items[0]["url"] == f"/artists/{artist.id}"


@pytest.mark.asyncio
async def test_search_playlists_and_libraries(client, regular_user, db_session):
    """Playlists and libraries are searchable by name and description."""
    playlist = await _make_playlist(
        db_session,
        regular_user,
        name="Summer Vibes",
        description="Sunny archive tracks",
    )
    library = await _make_library(
        db_session,
        regular_user,
        name="Summer Archive",
        description="Sunny vibes files",
    )
    await db_session.commit()

    for query in ["Summer", "sunny", "VIBES", "archive"]:
        response = client.get(f"/api/v1/search?q={query}&entities=playlists,libraries")
        assert response.status_code == 200
        data = response.json()
        assert str(playlist.id) in _section_ids(data, "playlists")
        assert str(library.id) in _section_ids(data, "libraries")


@pytest.mark.asyncio
async def test_search_users(client, regular_user, other_user, db_session):
    """Users appear in search as active public profiles."""
    regular_user.display_name = "Alice"
    other_user.is_active = False
    await db_session.commit()

    response = client.get("/api/v1/search?q=ali&entities=users")
    assert response.status_code == 200
    data = response.json()
    ids = _section_ids(data, "users")
    assert str(regular_user.username) in ids
    assert str(other_user.username) not in ids

    user_item = [s for s in data["sections"] if s["entity"] == "users"][0]["items"][0]
    assert user_item["url"] == f"/@{regular_user.username}"
    assert user_item["title"] == "Alice"


@pytest.mark.asyncio
async def test_search_tags_and_genres(client, regular_user, db_session):
    """Tags and genres are returned with item counts and named links."""
    track = await _make_track(
        db_session,
        await _make_artist(db_session, name="Artist"),
        title="Tagged Track",
        owner=regular_user,
    )
    tag = await _make_tag(db_session, "summer_beats")
    genre = await _make_genre(db_session, "indie")
    db_session.add(TagTrack(tag_id=tag.id, track_id=track.id, user_id=regular_user.id))
    db_session.add(GenreTrack(genre_id=genre.id, track_id=track.id))
    await db_session.commit()

    response = client.get("/api/v1/search?q=summer&entities=tags")
    assert response.status_code == 200
    data = response.json()
    tag_items = [s for s in data["sections"] if s["entity"] == "tags"][0]["items"]
    assert any(item["id"] == "summer_beats" and item["url"] == "/tags/summer_beats" for item in tag_items)

    response = client.get("/api/v1/search?q=indie&entities=genres")
    assert response.status_code == 200
    data = response.json()
    genre_items = [s for s in data["sections"] if s["entity"] == "genres"][0]["items"]
    assert any(item["id"] == "indie" and item["url"] == "/genres/indie" for item in genre_items)


@pytest.mark.asyncio
async def test_search_unknown_entities_ignored(client):
    """Unknown values in ``entities`` are ignored."""
    response = client.get("/api/v1/search?q=test&entities=tracks,unknown")
    assert response.status_code == 200
    data = response.json()
    assert [s["entity"] for s in data["sections"]] == ["tracks"]


@pytest.mark.asyncio
async def test_search_hashtag_prefix_searches_tags_only(client, regular_user, db_session):
    """A ``#``-prefixed query strips the prefix and searches the tags table."""
    artist = await _make_artist(db_session, name="Summer Artist")
    track = await _make_track(
        db_session,
        artist,
        title="Summer Song",
        owner=regular_user,
    )
    tag = await _make_tag(db_session, "summer_beats")
    db_session.add(TagTrack(tag_id=tag.id, track_id=track.id, user_id=regular_user.id))
    await db_session.commit()

    # The '#' must be percent-encoded or it is treated as a URL fragment.
    response = client.get("/api/v1/search?q=%23summer")
    assert response.status_code == 200
    data = response.json()
    assert data["query"] == "#summer"
    assert [s["entity"] for s in data["sections"]] == ["tags"]
    items = data["sections"][0]["items"]
    assert [item["id"] for item in items] == ["summer_beats"]
    assert items[0]["url"] == "/tags/summer_beats"

    # Even an explicit entity allowlist does not widen a hashtag lookup.
    response = client.get("/api/v1/search?q=%23summer&entities=tracks")
    assert response.status_code == 200
    data = response.json()
    assert [s["entity"] for s in data["sections"]] == ["tags"]


@pytest.mark.asyncio
async def test_search_bare_hash_lists_tags_by_popularity(client, regular_user, db_session):
    """A bare ``#`` returns all visible tags ordered by item count."""
    artist = await _make_artist(db_session)
    tracks = [await _make_track(db_session, artist, title=f"Track {i}", owner=regular_user) for i in range(3)]
    popular = await _make_tag(db_session, "popular")
    niche = await _make_tag(db_session, "niche")
    for track in tracks:
        db_session.add(TagTrack(tag_id=popular.id, track_id=track.id, user_id=regular_user.id))
    db_session.add(TagTrack(tag_id=niche.id, track_id=tracks[0].id, user_id=regular_user.id))
    await db_session.commit()

    response = client.get("/api/v1/search?q=%23")
    assert response.status_code == 200
    data = response.json()
    assert [s["entity"] for s in data["sections"]] == ["tags"]
    section = data["sections"][0]
    assert section["total"] == 2
    assert [item["id"] for item in section["items"]] == ["popular", "niche"]
    assert section["items"][0]["subtitle"] == "3 items"


@pytest.mark.asyncio
async def test_search_hashtag_no_match_returns_empty_tags_section(client, db_session):
    """A ``#`` query with no matching tag yields a single empty tags section."""
    response = client.get("/api/v1/search?q=%23missing")
    assert response.status_code == 200
    data = response.json()
    assert [s["entity"] for s in data["sections"]] == ["tags"]
    assert data["sections"][0]["items"] == []
    assert data["sections"][0]["total"] == 0


@pytest.mark.asyncio
async def test_search_users_includes_remote_actors(fed_client, fed_config):
    """With ``remote_users``, followers and cached actors appear as handles."""
    _seed_remote_actors(fed_config)

    response = fed_client.get("/api/v1/search?q=bob&entities=users&remote_users=true")
    assert response.status_code == 200
    data = response.json()
    section = [s for s in data["sections"] if s["entity"] == "users"][0]
    remote_items = [i for i in section["items"] if i["id"] == BOB_ACTOR]
    assert len(remote_items) == 1
    item = remote_items[0]
    assert item["name"] == "bob@remote.example"
    assert item["title"] == "Bob Remote"
    assert item["subtitle"] == "@bob@remote.example"
    assert item["image_url"] == "https://remote.example/bob.png"
    assert item["url"] == "https://remote.example/@bob"

    response = fed_client.get("/api/v1/search?q=carol&entities=users&remote_users=true")
    assert response.status_code == 200
    data = response.json()
    names = [i["name"] for s in data["sections"] for i in s["items"]]
    assert "carol@mastodon.example" in names


@pytest.mark.asyncio
async def test_search_users_remote_flag_off_excludes_remote(fed_client, fed_config):
    """Without ``remote_users`` the users section stays local-only."""
    _seed_remote_actors(fed_config)

    response = fed_client.get("/api/v1/search?q=bob&entities=users")
    assert response.status_code == 200
    data = response.json()
    names = [i["name"] for s in data["sections"] for i in s["items"]]
    assert "bob@remote.example" not in names


@pytest.mark.asyncio
async def test_search_users_remote_disabled_without_federation(client, db_session):
    """With federation disabled the flag is a no-op and nothing breaks."""
    response = client.get("/api/v1/search?q=bob&entities=users&remote_users=true")
    assert response.status_code == 200
    data = response.json()
    section = [s for s in data["sections"] if s["entity"] == "users"][0]
    assert section["items"] == []


@pytest.mark.asyncio
async def test_search_users_remote_narrows_by_domain(fed_client, fed_config):
    """A ``user@domain`` query filters remote matches by actor domain."""
    _seed_remote_actors(fed_config)

    response = fed_client.get("/api/v1/search?q=bob%40mastodon&entities=users&remote_users=true")
    assert response.status_code == 200
    data = response.json()
    names = [i["name"] for s in data["sections"] for i in s["items"]]
    assert "bob@remote.example" not in names

    response = fed_client.get("/api/v1/search?q=carol%40mastodon&entities=users&remote_users=true")
    assert response.status_code == 200
    data = response.json()
    names = [i["name"] for s in data["sections"] for i in s["items"]]
    assert "carol@mastodon.example" in names


@pytest.mark.asyncio
async def test_search_users_remote_excludes_local_and_blocked(fed_client, fed_config):
    """Cached actors on the instance domain or blocked domains are skipped."""
    fed_config.federation.blocked_instances = ["blocked.example"]
    storage = create_activitypub_storage(fed_config.database.url)
    storage.cache_remote_actor(
        "https://music.example.com/users/regular",
        {"id": "https://music.example.com/users/regular", "preferredUsername": "regular"},
        datetime.now(timezone.utc),
    )
    storage.cache_remote_actor(
        "https://blocked.example/users/eve",
        {"id": "https://blocked.example/users/eve", "preferredUsername": "eve"},
        datetime.now(timezone.utc),
    )

    response = fed_client.get("/api/v1/search?q=eve&entities=users&remote_users=true")
    assert response.status_code == 200
    names = [i["name"] for s in response.json()["sections"] for i in s["items"]]
    assert "eve@blocked.example" not in names

    response = fed_client.get("/api/v1/search?q=regular&entities=users&remote_users=true")
    assert response.status_code == 200
    items = [i for s in response.json()["sections"] for i in s["items"]]
    # Only the local account matches — not its cached actor document.
    assert all(i["id"] != "https://music.example.com/users/regular" for i in items)
