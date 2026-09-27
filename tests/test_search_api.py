"""Tests for the aggregate /api/v1/search/ endpoint."""

from datetime import datetime, timezone
from typing import Optional

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
async def test_search_users_respects_profile_visibility(client, regular_user, other_user, db_session, auth_headers):
    """The users search section honours directory profile visibility."""
    regular_user.profile_visibility = "local"
    other_user.profile_visibility = "private"
    await db_session.commit()

    response = client.get("/api/v1/search?q=e&entities=users")
    assert response.status_code == 200
    assert _section_ids(response.json(), "users") == []

    response = client.get(
        "/api/v1/search?q=e&entities=users",
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 200
    assert _section_ids(response.json(), "users") == ["regular"]


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


# ---------------------------------------------------------------------------
# Direct URL lookups
# ---------------------------------------------------------------------------

LOCAL_ORIGIN = "http://testserver"
REMOTE_DOMAIN = "remote.invalid"  # .invalid never resolves — fetch is stubbed
REMOTE_ACTOR_URL = f"https://{REMOTE_DOMAIN}/users/alice"
REMOTE_TRACK_URL = f"https://{REMOTE_DOMAIN}/tracks/track1"

REMOTE_ACTOR_DOC = {
    "id": REMOTE_ACTOR_URL,
    "type": "Person",
    "preferredUsername": "alice",
    "name": "Alice Remote",
    "inbox": f"{REMOTE_ACTOR_URL}/inbox",
}

REMOTE_TRACK_DOC = {
    "id": f"https://{REMOTE_DOMAIN}/users/alice/objects/track1",
    "type": "Audio",
    "attributedTo": REMOTE_ACTOR_URL,
    "name": "Remote Song",
    "url": {
        "type": "Link",
        "href": f"https://{REMOTE_DOMAIN}/media/song.ogg",
        "mediaType": "audio/ogg",
    },
    "to": ["https://www.w3.org/ns/activitystreams#Public"],
}


@pytest.fixture
def remote_fetcher(monkeypatch):
    """Stub ``remote_content.guarded_fetch`` with a canned-document router."""
    from songhive.federation.fetch import FetchResult
    from songhive.services import remote_content as rc

    class _Fetcher:
        def __init__(self, routes: dict):
            self.routes = routes
            self.calls: list[str] = []

        def __call__(self, url, *, check_url=None, **kwargs):
            self.calls.append(url)
            if check_url is not None:
                check_url(url)
            response = self.routes[url]
            if isinstance(response, Exception):
                raise response
            import json as jsonlib

            return FetchResult(
                url=url,
                status_code=200,
                content_type="application/activity+json",
                body=jsonlib.dumps(response).encode(),
                headers={},
            )

    def _install(routes: dict) -> _Fetcher:
        fake = _Fetcher(routes)
        monkeypatch.setattr(rc, "guarded_fetch", fake)
        return fake

    return _install


def _search(client, url: str, headers: Optional[dict] = None):
    return client.get("/api/v1/search", params={"q": url}, headers=headers or {})


@pytest.mark.asyncio
async def test_url_lookup_local_track(client, regular_user, db_session):
    """A local track URL resolves to exactly one track result."""
    artist = await _make_artist(db_session, name="URL Artist")
    track = await _make_track(db_session, artist, title="Linked Track", owner=regular_user)
    await db_session.commit()

    response = _search(client, f"{LOCAL_ORIGIN}/tracks/{track.id}")
    assert response.status_code == 200
    data = response.json()
    assert [s["entity"] for s in data["sections"]] == ["tracks"]
    section = data["sections"][0]
    assert section["total"] == 1
    assert [i["id"] for i in section["items"]] == [str(track.id)]
    assert section["items"][0]["title"] == "Linked Track"
    assert section["items"][0]["url"] == f"/tracks/{track.id}"
    assert "URL Artist" in section["items"][0]["subtitle"]


@pytest.mark.asyncio
async def test_url_lookup_local_entities(client, regular_user, db_session):
    """Album, artist, playlist and library URLs each resolve to one result."""
    artist = await _make_artist(db_session, name="URL Artist")
    album = await _make_album(db_session, artist, title="URL Album", owner=regular_user)
    playlist = await _make_playlist(db_session, regular_user, name="URL Playlist")
    library = await _make_library(db_session, regular_user, name="URL Library")
    await db_session.commit()

    cases = [
        (f"{LOCAL_ORIGIN}/albums/{album.id}", "albums", str(album.id)),
        (f"{LOCAL_ORIGIN}/artists/{artist.id}", "artists", str(artist.id)),
        (f"{LOCAL_ORIGIN}/playlists/{playlist.id}", "playlists", str(playlist.id)),
        (f"{LOCAL_ORIGIN}/libraries/{library.id}", "libraries", str(library.id)),
    ]
    for url, entity, expected_id in cases:
        response = _search(client, url)
        assert response.status_code == 200
        data = response.json()
        assert [s["entity"] for s in data["sections"]] == [entity]
        assert _section_ids(data, entity) == [expected_id]


@pytest.mark.asyncio
async def test_url_lookup_api_prefix_and_trailing_slash(client, regular_user, db_session):
    """``/api/v1/``-prefixed and trailing-slash resource URLs resolve too."""
    artist = await _make_artist(db_session, name="URL Artist")
    track = await _make_track(db_session, artist, title="Linked Track", owner=regular_user)
    await db_session.commit()

    for url in (
        f"{LOCAL_ORIGIN}/api/v1/tracks/{track.id}",
        f"{LOCAL_ORIGIN}/tracks/{track.id}/",
    ):
        data = _search(client, url).json()
        assert _section_ids(data, "tracks") == [str(track.id)]


@pytest.mark.asyncio
async def test_url_lookup_private_track_hidden_from_anonymous(
    client, regular_user, other_user, db_session, auth_headers
):
    """A private track URL yields no result for anonymous or foreign users."""
    artist = await _make_artist(db_session, name="URL Artist")
    track = await _make_track(
        db_session,
        artist,
        title="Secret Track",
        owner=regular_user,
        visibility=Visibility.PRIVATE.value,
    )
    await db_session.commit()
    url = f"{LOCAL_ORIGIN}/tracks/{track.id}"

    for headers in ({}, auth_headers(other_user)):
        data = _search(client, url, headers=headers).json()
        assert [s["entity"] for s in data["sections"]] == ["tracks"]
        assert _section_ids(data, "tracks") == []

    data = _search(client, url, headers=auth_headers(regular_user)).json()
    assert _section_ids(data, "tracks") == [str(track.id)]


@pytest.mark.asyncio
async def test_url_lookup_local_visibility_url(client, regular_user, other_user, db_session, auth_headers):
    """A LOCAL-visibility track URL is hidden anonymously, shown to users."""
    artist = await _make_artist(db_session, name="URL Artist")
    track = await _make_track(
        db_session,
        artist,
        title="Local Track",
        owner=regular_user,
        visibility=Visibility.LOCAL.value,
    )
    await db_session.commit()
    url = f"{LOCAL_ORIGIN}/tracks/{track.id}"

    assert _section_ids(_search(client, url).json(), "tracks") == []
    data = _search(client, url, headers=auth_headers(other_user)).json()
    assert _section_ids(data, "tracks") == [str(track.id)]


@pytest.mark.asyncio
async def test_url_lookup_share_link_grants_access(client, regular_user, db_session, auth_headers):
    """A share short link yields the shared item even for anonymous callers."""
    artist = await _make_artist(db_session, name="URL Artist")
    track = await _make_track(
        db_session,
        artist,
        title="Shared Track",
        owner=regular_user,
        visibility=Visibility.PRIVATE.value,
    )
    await db_session.commit()

    response = client.post(
        "/api/v1/share-urls",
        json={"item_type": "track", "item_id": str(track.id)},
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 201
    share = response.json()
    token = share["token"]

    for url in (share["url"], f"{LOCAL_ORIGIN}/share/{token}"):
        data = _search(client, url).json()
        assert [s["entity"] for s in data["sections"]] == ["tracks"]
        items = data["sections"][0]["items"]
        assert [i["id"] for i in items] == [str(track.id)]
        # The token is the only grant — the result links back to the share
        # page, which sets the share_token cookie the entity API checks.
        assert items[0]["url"] == f"/share/{token}"


@pytest.mark.asyncio
async def test_url_lookup_token_query_param_grants_access(client, regular_user, db_session, auth_headers):
    """A ``?token=`` resource URL grants access to a private item."""
    artist = await _make_artist(db_session, name="URL Artist")
    track = await _make_track(
        db_session,
        artist,
        title="Shared Track",
        owner=regular_user,
        visibility=Visibility.PRIVATE.value,
    )
    await db_session.commit()

    share = client.post(
        "/api/v1/share-urls",
        json={"item_type": "track", "item_id": str(track.id)},
        headers=auth_headers(regular_user),
    ).json()

    data = _search(client, f"{LOCAL_ORIGIN}/tracks/{track.id}?token={share['token']}").json()
    items = data["sections"][0]["items"]
    assert [i["id"] for i in items] == [str(track.id)]
    assert items[0]["url"] == f"/share/{share['token']}"

    # The ambient grant (header / cookie) applies to a bare resource URL too.
    data = _search(
        client,
        f"{LOCAL_ORIGIN}/tracks/{track.id}",
        headers={"X-Share-Token": share["token"]},
    ).json()
    assert [i["id"] for i in data["sections"][0]["items"]] == [str(track.id)]


@pytest.mark.asyncio
async def test_url_lookup_invalid_and_revoked_tokens(client, regular_user, db_session, auth_headers):
    """Unknown, revoked, or wrong-item tokens produce no result."""
    artist = await _make_artist(db_session, name="URL Artist")
    track = await _make_track(
        db_session,
        artist,
        title="Shared Track",
        owner=regular_user,
        visibility=Visibility.PRIVATE.value,
    )
    await db_session.commit()

    share = client.post(
        "/api/v1/share-urls",
        json={"item_type": "track", "item_id": str(track.id)},
        headers=auth_headers(regular_user),
    ).json()
    token = share["token"]

    assert _search(client, f"{LOCAL_ORIGIN}/api/v1/share/not-a-token").json()["sections"] == []

    # A revoked token stops resolving.
    revoke = client.delete(f"/api/v1/share-urls/{share['id']}", headers=auth_headers(regular_user))
    assert revoke.status_code == 204
    assert _search(client, f"{LOCAL_ORIGIN}/api/v1/share/{token}").json()["sections"] == []
    data = _search(client, f"{LOCAL_ORIGIN}/tracks/{track.id}?token={token}").json()
    assert _section_ids(data, "tracks") == []


@pytest.mark.asyncio
async def test_url_lookup_missing_and_unsupported(client):
    """Missing resources and unrecognized local paths return empty results."""
    # A nonexistent id looks exactly like an unauthorized one.
    data = _search(client, f"{LOCAL_ORIGIN}/tracks/does-not-exist").json()
    assert [s["entity"] for s in data["sections"]] == ["tracks"]
    assert _section_ids(data, "tracks") == []

    # Unrecognized local paths yield no sections at all.
    data = _search(client, f"{LOCAL_ORIGIN}/some/random/page").json()
    assert data["sections"] == []


@pytest.mark.asyncio
async def test_url_lookup_local_profile(client, regular_user, db_session):
    """A ``/@user`` local URL resolves to the users section."""
    await db_session.commit()
    data = _search(client, f"{LOCAL_ORIGIN}/@{regular_user.username}").json()
    assert [s["entity"] for s in data["sections"]] == ["users"]
    items = data["sections"][0]["items"]
    assert [i["id"] for i in items] == [regular_user.username]
    assert items[0]["url"] == f"/@{regular_user.username}"


@pytest.mark.asyncio
async def test_url_lookup_suppresses_text_results(client, regular_user, db_session):
    """A direct URL never returns unrelated text matches."""
    artist = await _make_artist(db_session, name="testserver Artist")
    track = await _make_track(db_session, artist, title="testserver song", owner=regular_user)
    other = await _make_track(db_session, artist, title="testserver other", owner=regular_user)
    await db_session.commit()

    data = _search(client, f"{LOCAL_ORIGIN}/tracks/{track.id}").json()
    assert len(data["sections"]) == 1
    assert _section_ids(data, "tracks") == [str(track.id)]
    assert str(other.id) not in _section_ids(data, "tracks")


@pytest.mark.asyncio
async def test_url_lookup_remote_track_not_yet_cached(client, regular_user, auth_headers, db_session, remote_fetcher):
    """A remote track URL is dereferenced even when never federated."""
    await db_session.commit()
    remote_fetcher({REMOTE_TRACK_URL: REMOTE_TRACK_DOC, REMOTE_ACTOR_URL: REMOTE_ACTOR_DOC})

    response = _search(client, REMOTE_TRACK_URL, headers=auth_headers(regular_user))
    assert response.status_code == 200
    data = response.json()
    assert [s["entity"] for s in data["sections"]] == ["remote"]
    section = data["sections"][0]
    assert section["total"] == 1
    item = section["items"][0]
    assert item["type"] == "track"
    assert item["title"] == "Remote Song"
    assert item["url"].startswith("/remote/track/")
    assert REMOTE_DOMAIN in item["subtitle"]


@pytest.mark.asyncio
async def test_url_lookup_remote_requires_lookup_permission(client, db_session, remote_fetcher):
    """Anonymous callers get no remote result under the default policy."""
    await db_session.commit()
    fake = remote_fetcher({REMOTE_TRACK_URL: REMOTE_TRACK_DOC})

    data = _search(client, REMOTE_TRACK_URL).json()
    assert [s["entity"] for s in data["sections"]] == ["remote"]
    assert _section_ids(data, "remote") == []
    assert fake.calls == []


@pytest.mark.asyncio
async def test_url_lookup_remote_fetch_failure_is_empty(client, regular_user, auth_headers, db_session, remote_fetcher):
    """A failed remote dereference yields an empty section, not an error."""
    from songhive.federation.fetch import FetchError

    await db_session.commit()
    remote_fetcher({REMOTE_TRACK_URL: FetchError("boom", status_code=502)})

    response = _search(client, REMOTE_TRACK_URL, headers=auth_headers(regular_user))
    assert response.status_code == 200
    data = response.json()
    assert [s["entity"] for s in data["sections"]] == ["remote"]
    assert _section_ids(data, "remote") == []


@pytest.mark.asyncio
async def test_url_lookup_local_spa_remote_page(client, regular_user, auth_headers, db_session, remote_fetcher):
    """A local ``/remote/…`` SPA URL resolves to the cached remote object."""
    await db_session.commit()
    remote_fetcher({REMOTE_TRACK_URL: REMOTE_TRACK_DOC, REMOTE_ACTOR_URL: REMOTE_ACTOR_DOC})
    first = _search(client, REMOTE_TRACK_URL, headers=auth_headers(regular_user)).json()
    remote_url = first["sections"][0]["items"][0]["url"]

    data = _search(client, f"{LOCAL_ORIGIN}{remote_url}", headers=auth_headers(regular_user)).json()
    assert [s["entity"] for s in data["sections"]] == ["remote"]
    assert [i["url"] for i in data["sections"][0]["items"]] == [remote_url]


@pytest.mark.asyncio
async def test_url_lookup_local_instance_domain(fed_client, fed_config, regular_user, db_session):
    """URLs on the configured instance domain resolve locally."""
    track = await _make_track(db_session, await _make_artist(db_session), owner=regular_user)
    await db_session.commit()

    data = _search(fed_client, f"https://music.example.com/tracks/{track.id}").json()
    assert _section_ids(data, "tracks") == [str(track.id)]
