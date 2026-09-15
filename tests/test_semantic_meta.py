"""
Tests for semantic <head> tag injection on SPA object pages.
"""

import pytest

from songhive.api.semantic_meta import inject_head_tags, match_object_path
from songhive.models._enums import Visibility
from songhive.models.album import Album
from songhive.models.artist import Artist
from songhive.models.genre import Genre
from songhive.models.library import Library
from songhive.models.playlist import Playlist
from songhive.models.stored_file import StoredFile
from songhive.models.track import Track
from songhive.services.tags import add_tags_to_entity


@pytest.mark.parametrize(
    "path,expected",
    [
        ("/tracks/abc", ("track", "abc")),
        ("/tracks/abc/edit", ("track", "abc")),
        ("/tracks/abc/activities", ("track", "abc")),
        ("/albums/abc", ("album", "abc")),
        ("/artists/abc", ("artist", "abc")),
        ("/playlists/abc", ("playlist", "abc")),
        ("/libraries/abc", ("library", "abc")),
        ("/genres/rock", ("genre", "rock")),
        ("/tags/rock", ("tag", "rock")),
        ("/@alice", ("user", "alice")),
        ("/@alice/posts", ("user", "alice")),
        ("/@alice/followers", ("user", "alice")),
    ],
)
def test_match_object_path_matches(path, expected):
    assert match_object_path(path) == expected


@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/tracks",
        "/tracks/abc/unknown",
        "/tracks/abc/edit/extra",
        "/tags",
        "/tags/rock/extra",
        "/genres/rock/extra",
        "/@alice/unknown",
        "/@alice/posts/extra",
        "/search",
        "/login",
        "/share/sometoken",
    ],
)
def test_match_object_path_ignores(path):
    assert match_object_path(path) is None


def test_inject_head_tags_inserts_before_head_close():
    body = "<html><head><title>x</title></head><body></body></html>"
    result = inject_head_tags(body, ['<meta property="og:title" content="t">'])
    assert '<meta property="og:title" content="t"></head>' in result


def test_inject_head_tags_appends_without_head():
    body = "<html><body></body></html>"
    result = inject_head_tags(body, ["<meta a>"])
    assert result.endswith("<meta a>")


@pytest.fixture
async def public_artist(db_session):
    artist = Artist(name="Meta Artist", bio="Artist bio", image_url="https://img.example.com/a.png")
    db_session.add(artist)
    await db_session.commit()
    return artist


@pytest.fixture
async def public_album(db_session, regular_user, public_artist):
    album = Album(
        title="Meta Album",
        artist_id=public_artist.id,
        owner_id=regular_user.id,
        visibility=Visibility.PUBLIC.value,
        description="Album description",
        cover_url="https://img.example.com/cover.jpg",
    )
    db_session.add(album)
    await db_session.commit()
    return album


@pytest.fixture
async def public_track(db_session, regular_user, public_artist, public_album):
    track = Track(
        title="Meta Track",
        artist_id=public_artist.id,
        album_id=public_album.id,
        owner_id=regular_user.id,
        visibility=Visibility.PUBLIC.value,
        description="Track description",
    )
    db_session.add(track)
    await db_session.commit()
    return track


async def test_track_page_injects_og_tags(client, public_track):
    response = client.get(f"/tracks/{public_track.id}")
    assert response.status_code == 200
    text = response.text
    assert '<meta property="og:title" content="Meta Artist - Meta Track">' in text
    assert '<meta property="og:description" content="Meta Album">' in text
    assert '<meta property="og:type" content="music.song">' in text
    assert f'<meta property="og:url" content="http://testserver/tracks/{public_track.id}">' in text
    assert '<meta property="og:site_name" content="Songhive">' in text
    assert '<meta property="og:image" content="https://img.example.com/cover.jpg">' in text
    assert "twitter:card" in text


async def test_track_page_tag_links(client, public_track, db_session, regular_user):
    await add_tags_to_entity(db_session, "track", public_track.id, ["rock", "live"], user_id=regular_user.id)
    await db_session.commit()
    response = client.get(f"/tracks/{public_track.id}")
    assert '<link rel="tag" href="/tags/rock" title="rock">' in response.text
    assert '<link rel="tag" href="/tags/live" title="live">' in response.text


async def test_track_page_description_falls_back_to_track_description(client, public_track, db_session):
    public_track.album_id = None
    await db_session.commit()
    response = client.get(f"/tracks/{public_track.id}")
    assert '<meta property="og:description" content="Track description">' in response.text


async def test_private_track_has_no_og_tags_anonymous(client, db_session, regular_user, public_artist):
    track = Track(
        title="Secret Track",
        artist_id=public_artist.id,
        owner_id=regular_user.id,
        visibility=Visibility.PRIVATE.value,
    )
    db_session.add(track)
    await db_session.commit()
    response = client.get(f"/tracks/{track.id}")
    assert response.status_code == 200
    assert "og:title" not in response.text
    assert "Secret Track" not in response.text


async def test_private_track_og_tags_for_owner(client, db_session, regular_user, public_artist, auth_headers):
    track = Track(
        title="Secret Track",
        artist_id=public_artist.id,
        owner_id=regular_user.id,
        visibility=Visibility.PRIVATE.value,
    )
    db_session.add(track)
    await db_session.commit()
    response = client.get(f"/tracks/{track.id}", headers=auth_headers(regular_user))
    assert '<meta property="og:title" content="Meta Artist - Secret Track">' in response.text


async def test_private_track_inherits_public_album_access(
    client, db_session, regular_user, public_artist, public_album
):
    track = Track(
        title="Album Track",
        artist_id=public_artist.id,
        album_id=public_album.id,
        owner_id=regular_user.id,
        visibility=Visibility.PRIVATE.value,
    )
    db_session.add(track)
    await db_session.commit()
    response = client.get(f"/tracks/{track.id}")
    assert '<meta property="og:title" content="Meta Artist - Album Track">' in response.text


def test_track_activities_subpath_injects_tags(client, public_track):
    response = client.get(f"/tracks/{public_track.id}/activities")
    assert '<meta property="og:title" content="Meta Artist - Meta Track">' in response.text


def test_album_page_injects_og_tags(client, public_album):
    response = client.get(f"/albums/{public_album.id}")
    text = response.text
    assert '<meta property="og:title" content="Meta Artist - Meta Album">' in text
    assert '<meta property="og:description" content="Album description">' in text
    assert '<meta property="og:type" content="music.album">' in text
    assert f'<meta property="og:url" content="http://testserver/albums/{public_album.id}">' in text
    assert '<meta property="og:image" content="https://img.example.com/cover.jpg">' in text


async def test_album_page_tag_links(client, public_album, db_session, regular_user):
    await add_tags_to_entity(db_session, "album", public_album.id, ["jazz"], user_id=regular_user.id)
    await db_session.commit()
    response = client.get(f"/albums/{public_album.id}")
    assert '<link rel="tag" href="/tags/jazz" title="jazz">' in response.text


def test_artist_page_injects_og_tags(client, public_artist):
    response = client.get(f"/artists/{public_artist.id}")
    text = response.text
    assert '<meta property="og:title" content="Meta Artist">' in text
    assert '<meta property="og:description" content="Artist bio">' in text
    assert '<meta property="og:type" content="music.artist">' in text
    assert '<meta property="og:image" content="https://img.example.com/a.png">' in text


async def test_playlist_page_injects_og_tags(client, db_session, regular_user):
    playlist = Playlist(
        name="My Playlist",
        owner_id=regular_user.id,
        visibility=Visibility.PUBLIC.value,
        description="Playlist description",
    )
    db_session.add(playlist)
    await db_session.commit()
    response = client.get(f"/playlists/{playlist.id}")
    text = response.text
    assert '<meta property="og:title" content="My Playlist">' in text
    # The owner username is preferred over the description.
    assert '<meta property="og:description" content="regular">' in text
    assert '<meta property="og:type" content="music.playlist">' in text


async def test_library_page_injects_og_tags(client, db_session, regular_user):
    library = Library(
        name="My Library",
        owner_id=regular_user.id,
        visibility=Visibility.PUBLIC.value,
        description="Library description",
    )
    db_session.add(library)
    await db_session.commit()
    response = client.get(f"/libraries/{library.id}")
    text = response.text
    assert '<meta property="og:title" content="My Library">' in text
    assert '<meta property="og:description" content="regular">' in text
    assert '<meta property="og:type" content="music.playlist">' in text
    assert f'<meta property="og:url" content="http://testserver/libraries/{library.id}">' in text


async def test_tag_page_injects_og_tags(client, db_session, regular_user, public_track):
    await add_tags_to_entity(db_session, "track", public_track.id, ["rock"], user_id=regular_user.id)
    await db_session.commit()
    response = client.get("/tags/rock")
    text = response.text
    assert '<meta property="og:title" content="#rock">' in text
    assert '<meta property="og:type" content="website">' in text
    assert '<meta property="og:url" content="http://testserver/tags/rock">' in text
    assert '<link rel="tag" href="/tags/rock" title="rock">' in text


async def test_genre_page_injects_og_tags(client, db_session):
    db_session.add(Genre(name="rock"))
    await db_session.commit()
    response = client.get("/genres/rock")
    text = response.text
    assert '<meta property="og:title" content="rock">' in text
    assert '<meta property="og:type" content="website">' in text
    assert '<meta property="og:url" content="http://testserver/genres/rock">' in text


async def test_user_profile_page_injects_og_tags(client, db_session, regular_user):
    regular_user.bio = "  Some bio text\n"
    regular_user.avatar_url = "https://img.example.com/avatar.png"
    await db_session.commit()
    response = client.get("/@regular")
    text = response.text
    assert '<meta property="og:title" content="regular">' in text
    assert '<meta property="og:description" content="Some bio text">' in text
    assert '<meta property="og:type" content="profile">' in text
    assert '<meta property="og:url" content="http://testserver/@regular">' in text
    assert '<meta property="og:image" content="https://img.example.com/avatar.png">' in text


async def test_user_subpage_injects_og_tags(client, db_session, regular_user):
    # The SPA fallback opens its own session, so the user must be committed.
    await db_session.commit()
    response = client.get("/@regular/followers")
    assert '<meta property="og:title" content="regular">' in response.text


def test_non_object_pages_have_no_og_tags(client):
    for path in ("/", "/tracks", "/albums", "/search", "/tags"):
        response = client.get(path)
        assert response.status_code == 200
        assert "og:title" not in response.text, path


def test_unknown_object_has_no_og_tags(client):
    response = client.get("/tracks/does-not-exist")
    assert response.status_code == 200
    assert "og:title" not in response.text


def test_unknown_tag_has_no_og_tags(client):
    response = client.get("/tags/definitelynotatag")
    assert response.status_code == 200
    assert "og:title" not in response.text


async def test_stored_file_image_is_absolute(client, db_session, regular_user, public_artist):
    stored = StoredFile(
        storage_path="files/ab/cd/abcdef",
        storage_backend="local",
        content_type="image/png",
        size=10,
        sha256="a" * 64,
        owner_id=regular_user.id,
        visibility=Visibility.PUBLIC.value,
    )
    db_session.add(stored)
    await db_session.flush()
    track = Track(
        title="Imaged Track",
        artist_id=public_artist.id,
        image_file_id=stored.id,
        owner_id=regular_user.id,
        visibility=Visibility.PUBLIC.value,
    )
    db_session.add(track)
    await db_session.commit()
    response = client.get(f"/tracks/{track.id}")
    assert f'<meta property="og:image" content="http://testserver/api/v1/files/{stored.id}/download">' in response.text


async def test_track_page_injects_artist_and_uploader_tags(client, public_track, public_artist):
    response = client.get(f"/tracks/{public_track.id}")
    text = response.text
    assert f'<meta property="music:musician" content="http://testserver/artists/{public_artist.id}">' in text
    assert f'<meta property="music:album" content="http://testserver/albums/{public_track.album_id}">' in text
    assert '<link rel="author" href="http://testserver/@regular" title="regular">' in text
    assert '<meta name="author" content="regular">' in text
    assert '<meta name="fediverse:creator" content="@regular@testserver">' in text


async def test_user_content_is_escaped(client, db_session, regular_user, public_artist):
    payload = '"><script>alert(1)</script><meta property="x"'
    track = Track(
        title="Quoted",
        artist_id=public_artist.id,
        owner_id=regular_user.id,
        visibility=Visibility.PUBLIC.value,
        description=payload,
    )
    db_session.add(track)
    await db_session.commit()
    response = client.get(f"/tracks/{track.id}")
    text = response.text
    assert payload not in text
    assert "<script>alert(1)</script>" not in text
    assert "&quot;&gt;&lt;script&gt;alert(1)&lt;/script&gt;" in text


async def test_javascript_image_url_is_neutralised(client, db_session, regular_user, public_artist):
    public_artist.image_url = "javascript:alert(1)"
    await db_session.commit()
    response = client.get(f"/artists/{public_artist.id}")
    assert '<meta property="og:image" content="javascript:' not in response.text
    # Non-http(s) values are re-rooted under the instance base URL.
    assert '<meta property="og:image" content="http://testserver/javascript:alert(1)">' in response.text


async def test_multiline_description_is_collapsed(client, db_session, regular_user, public_artist):
    track = Track(
        title="Multiline",
        artist_id=public_artist.id,
        owner_id=regular_user.id,
        visibility=Visibility.PUBLIC.value,
        description="first line\nsecond line\r\n\tthird",
    )
    db_session.add(track)
    await db_session.commit()
    response = client.get(f"/tracks/{track.id}")
    assert '<meta property="og:description" content="first line second line third">' in response.text


async def test_track_page_injects_duration(client, db_session, public_track):
    public_track.duration = 187.6
    await db_session.commit()
    response = client.get(f"/tracks/{public_track.id}")
    assert '<meta property="music:duration" content="187">' in response.text


async def test_track_without_owner_has_no_author_tags(client, db_session, public_artist):
    track = Track(
        title="Orphaned Track",
        artist_id=public_artist.id,
        owner_id=None,
        visibility=Visibility.PUBLIC.value,
    )
    db_session.add(track)
    await db_session.commit()
    response = client.get(f"/tracks/{track.id}")
    assert 'rel="author"' not in response.text
    assert "fediverse:creator" not in response.text


async def test_album_page_injects_artist_and_uploader_tags(client, public_album, public_artist):
    response = client.get(f"/albums/{public_album.id}")
    text = response.text
    assert f'<meta property="music:musician" content="http://testserver/artists/{public_artist.id}">' in text
    assert '<link rel="author" href="http://testserver/@regular" title="regular">' in text
    assert '<meta name="fediverse:creator" content="@regular@testserver">' in text


async def test_playlist_page_injects_creator_tags(client, db_session, regular_user):
    playlist = Playlist(
        name="Owned Playlist",
        owner_id=regular_user.id,
        visibility=Visibility.PUBLIC.value,
    )
    db_session.add(playlist)
    await db_session.commit()
    response = client.get(f"/playlists/{playlist.id}")
    text = response.text
    assert '<meta property="music:creator" content="http://testserver/@regular">' in text
    assert '<link rel="author" href="http://testserver/@regular" title="regular">' in text
    assert '<meta name="fediverse:creator" content="@regular@testserver">' in text


async def test_library_page_injects_creator_tags(client, db_session, regular_user):
    library = Library(
        name="Owned Library",
        owner_id=regular_user.id,
        visibility=Visibility.PUBLIC.value,
    )
    db_session.add(library)
    await db_session.commit()
    response = client.get(f"/libraries/{library.id}")
    text = response.text
    assert '<meta property="music:creator" content="http://testserver/@regular">' in text
    assert '<meta name="author" content="regular">' in text


async def test_user_profile_page_injects_profile_username(client, db_session, regular_user):
    await db_session.commit()
    response = client.get("/@regular")
    assert '<meta property="profile:username" content="regular">' in response.text
