"""
Tests for the Subsonic API adapter (/rest/*.view) and the adapter registry.
"""

import array
import asyncio
import hashlib
import io
import json
import math
import shutil
import tempfile
import wave
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jwt
import pytest
import tornado.testing
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from songhive.adapters import registry as adapter_registry
from songhive.adapters.base import APIAdapter
from songhive.adapters.subsonic import now_playing
from songhive.api.app import create_app
from songhive.api.middleware.auth import create_api_token_jwt
from songhive.app import _build_tornado_app
from songhive.config.schema import SonghiveConfig
from songhive.models._enums import Visibility
from songhive.models.album import Album
from songhive.models.artist import Artist
from songhive.models.audit_log import AuditLog
from songhive.models.base import Base, get_session, init_db, reset_db
from songhive.models.history import ListeningHistory
from songhive.models.library import Library
from songhive.models.playlist import Playlist
from songhive.models.track import Track
from songhive.services.auth import create_user
from songhive.services.genres import add_genres_to_entity
from songhive.services.storage import StorageService
from songhive.storage import get_storage
from songhive.users.api_tokens import issue_api_token, revoke_api_token


def _creds(user, password="secret", fmt="json", **extra):
    """Return Subsonic auth params for a seeded user.

    ``fmt=None`` omits the ``f`` parameter (XML response).
    """
    params = {"u": user.username, "p": password, "v": "1.16.1", "c": "pytest"}
    if fmt is not None:
        params["f"] = fmt
    params.update(extra)
    return params


def _envelope(response):
    """Return the parsed subsonic-response payload, asserting HTTP 200."""
    assert response.status_code == 200
    return response.json()["subsonic-response"]


def _ok(response):
    body = _envelope(response)
    assert body["status"] == "ok"
    return body


def _failed(response):
    body = _envelope(response)
    assert body["status"] == "failed"
    return body


@pytest.fixture(autouse=True)
def _clear_now_playing():
    now_playing._clear()
    yield
    now_playing._clear()


@pytest.fixture
async def library(db_session, config, regular_user):
    """One artist, one public album, a public song and a private song."""
    artist = Artist(name="Adapter Artist", bio="An artist bio")
    db_session.add(artist)
    await db_session.flush()

    album = Album(
        title="Adapter Album",
        artist_id=artist.id,
        owner_id=str(regular_user.id),
        visibility=Visibility.PUBLIC.value,
        release_year=2020,
    )
    db_session.add(album)
    await db_session.flush()

    storage = StorageService(get_storage(config.storage), config.storage)
    mp3 = await storage.store_file(
        db_session,
        io.BytesIO(b"fake mp3 payload"),
        "audio/mpeg",
        owner_id=str(regular_user.id),
        visibility=Visibility.PUBLIC.value,
    )
    cover = await storage.store_file(
        db_session,
        io.BytesIO(b"\xff\xd8\xff\xe0fakejpeg"),
        "image/jpeg",
        owner_id=str(regular_user.id),
        visibility=Visibility.PUBLIC.value,
    )
    album.cover_file_id = cover.id

    song = Track(
        title="Song One",
        artist_id=artist.id,
        album_id=album.id,
        audio_file_id=mp3.id,
        owner_id=str(regular_user.id),
        visibility=Visibility.PUBLIC.value,
        duration=180.0,
        track_number=1,
    )
    # A private track inside the public album: filtered out of listings but
    # reachable by id through album-inherited access (ACL rule 8).
    hidden = Track(
        title="Hidden Song",
        artist_id=artist.id,
        album_id=album.id,
        audio_file_id=mp3.id,
        owner_id=str(regular_user.id),
        visibility=Visibility.PRIVATE.value,
        duration=90.0,
        track_number=2,
    )

    # A private album with a private track: invisible to other users entirely.
    private_album = Album(
        title="Secret Album",
        artist_id=artist.id,
        owner_id=str(regular_user.id),
        visibility=Visibility.PRIVATE.value,
    )
    db_session.add(private_album)
    await db_session.flush()

    secret = Track(
        title="Secret Song",
        artist_id=artist.id,
        album_id=private_album.id,
        audio_file_id=mp3.id,
        owner_id=str(regular_user.id),
        visibility=Visibility.PRIVATE.value,
        duration=60.0,
        track_number=1,
    )
    db_session.add_all([song, hidden, secret])
    await db_session.commit()
    return {
        "artist": artist,
        "album": album,
        "private_album": private_album,
        "song": song,
        "hidden": hidden,
        "secret": secret,
        "mp3": mp3,
        "cover": cover,
    }


# ---------------------------------------------------------------------------
# Adapter registry
# ---------------------------------------------------------------------------


class TestAdapterRegistry:
    def test_subsonic_adapter_registered(self):
        assert "subsonic" in adapter_registry.list_adapters()
        adapter_cls = adapter_registry.get_adapter("subsonic")
        assert adapter_cls.name == "subsonic"

    def test_register_is_idempotent_and_rejects_conflicts(self):
        class Dummy(APIAdapter):
            name = "dummy-test"

            def is_enabled(self, config):
                return False

            def router(self):
                return None

        adapter_registry.register_adapter("dummy-test", Dummy)
        adapter_registry.register_adapter("dummy-test", Dummy)
        assert "dummy-test" in adapter_registry.list_adapters()

        class Other(APIAdapter):
            name = "dummy-test"

            def is_enabled(self, config):
                return True

            def router(self):
                return None

        with pytest.raises(ValueError):
            adapter_registry.register_adapter("dummy-test", Other)
        adapter_registry._REGISTRY.pop("dummy-test")

    def test_get_adapter_unknown_raises(self):
        with pytest.raises(KeyError):
            adapter_registry.get_adapter("nonexistent-adapter")

    def test_tornado_routes_collected_for_enabled_adapter(self, config):
        routes = adapter_registry.adapter_tornado_routes(config)
        patterns = [pattern for pattern, _ in routes]
        assert r"/rest/stream\.view" in patterns
        assert r"/rest/download\.view" in patterns

    def test_tornado_routes_empty_when_disabled(self, config):
        config.subsonic.enabled = False
        assert adapter_registry.adapter_tornado_routes(config) == []

    def test_adapter_not_mounted_when_disabled(self, config, engine):
        """A disabled adapter contributes no routes to the FastAPI app."""
        from songhive.api.app import create_app
        from songhive.models.base import init_db

        config.subsonic.enabled = False
        init_db(engine=engine, force=True)
        app = create_app(config)
        paths = {getattr(route, "path", None) for route in app.routes}
        assert "/rest/ping.view" not in paths


# ---------------------------------------------------------------------------
# Envelope, formats, auth
# ---------------------------------------------------------------------------


def test_ping_returns_xml_by_default(client, regular_user):
    response = client.get("/rest/ping.view", params=_creds(regular_user, fmt=None))
    assert response.status_code == 200
    assert "text/xml" in response.headers["content-type"]

    root = ET.fromstring(response.content)
    assert root.tag == "{http://subsonic.org/restapi}subsonic-response"
    assert root.attrib["status"] == "ok"
    assert root.attrib["version"] == "1.16.1"


def test_ping_json_format(client, regular_user):
    response = client.get("/rest/ping.view", params=_creds(regular_user, f="json"))
    body = _ok(response)
    assert body["version"] == "1.16.1"
    assert body["type"] == "songhive"


def test_ping_jsonp_format(client, regular_user):
    response = client.get("/rest/ping.view", params=_creds(regular_user, f="jsonp", callback="myCb"))
    assert response.status_code == 200
    assert response.text.startswith("myCb(")
    assert "text/javascript" in response.headers["content-type"]


def test_jsonp_unsafe_callback_falls_back_to_json(client, regular_user):
    response = client.get("/rest/ping.view", params=_creds(regular_user, f="jsonp", callback="alert(1)"))
    assert response.status_code == 200
    assert "application/json" in response.headers["content-type"]


def test_ping_wrong_password_returns_40(client, regular_user):
    body = _failed(client.get("/rest/ping.view", params=_creds(regular_user, p="nope")))
    assert body["error"]["code"] == 40


def test_ping_missing_user_returns_10(client):
    body = _failed(client.get("/rest/ping.view", params={"p": "x", "f": "json"}))
    assert body["error"]["code"] == 10


def test_ping_missing_credentials_returns_10(client, regular_user):
    body = _failed(client.get("/rest/ping.view", params={"u": regular_user.username, "f": "json"}))
    assert body["error"]["code"] == 10


def _salted_params(user, password, salt="c19b2d"):
    """Return ``t``/``s`` auth params as a Subsonic client computes them."""
    token = hashlib.md5((password + salt).encode("utf-8"), usedforsecurity=False).hexdigest()
    return {"u": user.username, "t": token, "s": salt, "f": "json"}


async def test_salted_token_auth_with_api_token(client, db_session, config, regular_user):
    """``t``/``s`` auth works when the client password is an API token."""
    _, raw_jwt = await issue_api_token(db_session, regular_user, config, "salted-test", None)
    await db_session.commit()

    body = _ok(client.get("/rest/ping.view", params=_salted_params(regular_user, raw_jwt)))
    assert body["status"] == "ok"


async def test_salted_token_auth_wrong_values_return_40(client, db_session, config, regular_user):
    _, raw_jwt = await issue_api_token(db_session, regular_user, config, "salted-test", None)
    await db_session.commit()

    # The real password cannot be verified: bcrypt-only storage.
    body = _failed(client.get("/rest/ping.view", params=_salted_params(regular_user, "secret")))
    assert body["error"]["code"] == 40

    # Neither can a hash of some unrelated string.
    body = _failed(client.get("/rest/ping.view", params=_salted_params(regular_user, "bogus")))
    assert body["error"]["code"] == 40

    # And a valid token under a different username fails too.
    params = {**_salted_params(regular_user, raw_jwt), "u": "nobody"}
    body = _failed(client.get("/rest/ping.view", params=params))
    assert body["error"]["code"] == 40


def test_salted_token_partial_params_return_10(client, regular_user):
    body = _failed(client.get("/rest/ping.view", params={"u": regular_user.username, "t": "abc", "f": "json"}))
    assert body["error"]["code"] == 10
    body = _failed(client.get("/rest/ping.view", params={"u": regular_user.username, "s": "xyz", "f": "json"}))
    assert body["error"]["code"] == 10


async def test_salted_token_revoked_and_expired_return_40(client, db_session, config, regular_user):
    api_token, raw_jwt = await issue_api_token(db_session, regular_user, config, "revoked", None)
    _, expired_jwt = await issue_api_token(
        db_session,
        regular_user,
        config,
        "expired",
        datetime.now(timezone.utc) - timedelta(seconds=1),
    )
    await db_session.commit()
    await revoke_api_token(db_session, api_token.id, regular_user.id)
    await db_session.commit()

    for raw in (raw_jwt, expired_jwt):
        body = _failed(client.get("/rest/ping.view", params=_salted_params(regular_user, raw)))
        assert body["error"]["code"] == 40


async def test_salted_token_tolerates_created_at_drift(client, db_session, config, regular_user):
    """Tokens whose ``created_at`` straddles a second boundary vs the JWT
    ``iat`` claim still authenticate via the neighboring-second candidates."""
    api_token, raw_jwt = await issue_api_token(db_session, regular_user, config, "drift", None)
    iat_epoch = jwt.decode(raw_jwt, config.auth.secret_key, algorithms=["HS256"])["iat"]
    api_token.created_at = datetime.fromtimestamp(iat_epoch + 1, timezone.utc)
    await db_session.commit()

    _ok(client.get("/rest/ping.view", params=_salted_params(regular_user, raw_jwt)))


async def test_api_token_jwt_reconstructs_byte_identical(db_session, config, regular_user):
    """Salted-token verification depends on deterministic HS256 output:
    the JWT rebuilt from the stored row must equal the issued token."""
    api_token, raw_jwt = await issue_api_token(db_session, regular_user, config, "det", None)
    await db_session.commit()

    rebuilt = create_api_token_jwt(
        str(regular_user.id),
        config.auth.secret_key,
        api_token.jti,
        api_token.expires_at,
        iat=api_token.created_at,
    )
    assert rebuilt == raw_jwt


def test_inactive_user_rejected(client, inactive_user):
    body = _failed(client.get("/rest/ping.view", params=_creds(inactive_user)))
    assert body["error"]["code"] == 40


async def test_api_key_auth(client, db_session, config, regular_user):
    _, raw_jwt = await issue_api_token(db_session, regular_user, config, "subsonic-test", None)
    await db_session.commit()

    body = _ok(client.get("/rest/ping.view", params={"u": regular_user.username, "apiKey": raw_jwt, "f": "json"}))
    assert body["status"] == "ok"

    # The same token is also accepted in the p parameter.
    _ok(client.get("/rest/ping.view", params={"u": regular_user.username, "p": raw_jwt, "f": "json"}))

    # But not for a different user.
    other = client.get("/rest/ping.view", params={"u": "nobody", "apiKey": raw_jwt, "f": "json"})
    assert _failed(other)["error"]["code"] == 40


def test_post_form_auth(client, regular_user):
    """Subsonic POST endpoints accept urlencoded form parameters."""
    response = client.post("/rest/ping.view", data=_creds(regular_user, f="json"))
    assert _ok(response)["status"] == "ok"


def test_newer_protocol_version_returns_30(client, regular_user):
    body = _failed(client.get("/rest/ping.view", params=_creds(regular_user, v="9.9.9")))
    assert body["error"]["code"] == 30


def test_unimplemented_view_returns_generic_error(client, regular_user):
    body = _failed(client.get("/rest/getInternetRadioStations.view", params=_creds(regular_user)))
    assert body["error"]["code"] == 0


# ---------------------------------------------------------------------------
# System + browsing
# ---------------------------------------------------------------------------


def test_get_license(client, regular_user):
    body = _ok(client.get("/rest/getLicense.view", params=_creds(regular_user)))
    assert body["license"]["valid"] is True


def test_get_opensubsonic_extensions(client, regular_user):
    body = _ok(client.get("/rest/getOpenSubsonicExtensions.view", params=_creds(regular_user)))
    names = {ext["name"] for ext in body["openSubsonicExtensions"]["extension"]}
    assert "apiKeyAuthentication" in names


def test_get_music_folders(client, library, regular_user):
    body = _ok(client.get("/rest/getMusicFolders.view", params=_creds(regular_user)))
    folders = body["musicFolders"]["musicFolder"]
    assert any(f["id"] == "0" for f in folders)


def test_get_artists_and_indexes(client, library, regular_user):
    body = _ok(client.get("/rest/getArtists.view", params=_creds(regular_user)))
    index = body["artists"]["index"]
    artist_entries = [a for group in index for a in group["artist"]]
    assert {a["name"] for a in artist_entries} == {"Adapter Artist"}

    body = _ok(client.get("/rest/getIndexes.view", params=_creds(regular_user)))
    index = body["indexes"]["index"]
    artist_entries = [a for group in index for a in group["artist"]]
    assert {a["name"] for a in artist_entries} == {"Adapter Artist"}


def test_get_artist_returns_albums(client, library, regular_user):
    artist_id = str(library["artist"].id)
    body = _ok(client.get("/rest/getArtist.view", params=_creds(regular_user, id=artist_id)))
    assert body["artist"]["name"] == "Adapter Artist"
    assert body["artist"]["album"][0]["name"] == "Adapter Album"


def test_get_album_returns_accessible_songs_only(client, library, regular_user, other_user):
    album_id = str(library["album"].id)
    owner = _ok(client.get("/rest/getAlbum.view", params=_creds(regular_user, id=album_id)))
    assert {s["title"] for s in owner["album"]["song"]} == {"Song One", "Hidden Song"}

    other = _ok(client.get("/rest/getAlbum.view", params=_creds(other_user, id=album_id)))
    assert {s["title"] for s in other["album"]["song"]} == {"Song One"}


def test_get_song(client, library, regular_user):
    song_id = str(library["song"].id)
    body = _ok(client.get("/rest/getSong.view", params=_creds(regular_user, id=song_id)))
    song = body["song"]
    assert song["title"] == "Song One"
    assert song["album"] == "Adapter Album"
    assert song["artist"] == "Adapter Artist"
    assert song["coverArt"] == f"tr-{song_id}"


def test_get_song_private_denied(client, library, other_user):
    body = _failed(client.get("/rest/getSong.view", params=_creds(other_user, id=str(library["secret"].id))))
    assert body["error"]["code"] == 70


def test_get_music_directory(client, library, regular_user):
    artist_id = str(library["artist"].id)
    body = _ok(client.get("/rest/getMusicDirectory.view", params=_creds(regular_user, id=artist_id)))
    assert body["directory"]["child"][0]["title"] == "Adapter Album"

    album_id = str(library["album"].id)
    body = _ok(client.get("/rest/getMusicDirectory.view", params=_creds(regular_user, id=album_id)))
    assert {c["title"] for c in body["directory"]["child"]} == {"Song One", "Hidden Song"}


def test_search3(client, library, regular_user):
    body = _ok(client.get("/rest/search3.view", params=_creds(regular_user, query="Adapter")))
    result = body["searchResult3"]
    assert result["artist"][0]["name"] == "Adapter Artist"
    assert result["album"][0]["name"] == "Adapter Album"

    body = _ok(client.get("/rest/search3.view", params=_creds(regular_user, query="Song One")))
    assert body["searchResult3"]["song"][0]["title"] == "Song One"


async def _add_genre(db_session, track_id):
    await add_genres_to_entity(db_session, "track", track_id, ["rock"])
    await db_session.commit()


async def test_get_genres(client, library, regular_user, db_session):
    await _add_genre(db_session, str(library["song"].id))
    body = _ok(client.get("/rest/getGenres.view", params=_creds(regular_user)))
    genres = {g["value"]: g for g in body["genres"]["genre"]}
    assert genres["rock"]["songCount"] == 1

    body = _ok(client.get("/rest/getSongsByGenre.view", params=_creds(regular_user, genre="rock")))
    assert body["songsByGenre"]["song"][0]["title"] == "Song One"


def test_get_random_songs(client, library, regular_user):
    body = _ok(client.get("/rest/getRandomSongs.view", params=_creds(regular_user, size=10)))
    titles = {s["title"] for s in body["randomSongs"]["song"]}
    assert titles == {"Song One", "Hidden Song", "Secret Song"}


def test_get_album_list(client, library, regular_user):
    body = _ok(client.get("/rest/getAlbumList2.view", params=_creds(regular_user, type="newest")))
    names = [a["name"] for a in body["albumList2"]["album"]]
    assert "Adapter Album" in names

    body = _ok(
        client.get(
            "/rest/getAlbumList2.view",
            params=_creds(regular_user, type="byYear", fromYear=2000, toYear=2025),
        )
    )
    names = [a["name"] for a in body["albumList2"]["album"]]
    assert names == ["Adapter Album"]

    body = _failed(client.get("/rest/getAlbumList2.view", params=_creds(regular_user, type="byYear")))
    assert body["error"]["code"] == 10


# ---------------------------------------------------------------------------
# Starred / favorites
# ---------------------------------------------------------------------------


def test_star_unstar_flow(client, library, regular_user):
    song_id = str(library["song"].id)

    body = _ok(client.get("/rest/getStarred2.view", params=_creds(regular_user)))
    assert body["starred2"]["song"] == []

    _ok(client.get("/rest/star.view", params=_creds(regular_user, id=song_id)))

    body = _ok(client.get("/rest/getStarred2.view", params=_creds(regular_user)))
    assert body["starred2"]["song"][0]["id"] == song_id
    assert "starred" in body["starred2"]["song"][0]

    _ok(client.get("/rest/unstar.view", params=_creds(regular_user, id=song_id)))
    body = _ok(client.get("/rest/getStarred2.view", params=_creds(regular_user)))
    assert body["starred2"]["song"] == []


def test_star_inaccessible_track_is_ignored(client, library, other_user, db_session):
    _ok(client.get("/rest/star.view", params=_creds(other_user, id=str(library["secret"].id))))
    body = _ok(client.get("/rest/getStarred2.view", params=_creds(other_user)))
    assert body["starred2"]["song"] == []


# ---------------------------------------------------------------------------
# Playlists
# ---------------------------------------------------------------------------


async def test_playlist_lifecycle(client, library, regular_user, db_session):
    song_id = str(library["song"].id)

    # Create with initial entries.
    created = _ok(client.get("/rest/createPlaylist.view", params=_creds(regular_user, name="Mix", songId=[song_id])))
    playlist_id = created["playlist"]["id"]
    assert created["playlist"]["name"] == "Mix"
    assert created["playlist"]["entry"][0]["id"] == song_id

    # Listed.
    body = _ok(client.get("/rest/getPlaylists.view", params=_creds(regular_user)))
    assert any(p["id"] == playlist_id for p in body["playlists"]["playlist"])

    # Fetched with entries.
    body = _ok(client.get("/rest/getPlaylist.view", params=_creds(regular_user, id=playlist_id)))
    assert body["playlist"]["songCount"] == 1

    # Update: rename + remove the entry at index 0.
    _ok(
        client.get(
            "/rest/updatePlaylist.view",
            params=_creds(regular_user, playlistId=playlist_id, name="Renamed", songIndexToRemove="0"),
        )
    )
    body = _ok(client.get("/rest/getPlaylist.view", params=_creds(regular_user, id=playlist_id)))
    assert body["playlist"]["name"] == "Renamed"
    assert body["playlist"].get("entry", []) == []

    # Audit rows exist for the mutations.
    actions = {
        row.action
        for row in (await db_session.execute(select(AuditLog).where(AuditLog.target_id == playlist_id))).scalars().all()
    }
    assert {"playlist.create", "playlist.update"} <= actions

    # Delete.
    _ok(client.get("/rest/deletePlaylist.view", params=_creds(regular_user, id=playlist_id)))
    body = _failed(client.get("/rest/getPlaylist.view", params=_creds(regular_user, id=playlist_id)))
    assert body["error"]["code"] == 70


async def test_playlist_update_requires_ownership(client, library, other_user, regular_user, db_session):
    playlist = Playlist(name="Not yours", owner_id=str(regular_user.id), visibility=Visibility.PRIVATE.value)
    db_session.add(playlist)
    await db_session.commit()

    body = _failed(
        client.get("/rest/updatePlaylist.view", params=_creds(other_user, playlistId=str(playlist.id), name="x"))
    )
    assert body["error"]["code"] == 70  # invisible → not found


async def test_playlist_public_visibility_listed_for_others(client, regular_user, other_user, db_session):
    playlist = Playlist(name="Shared", owner_id=str(regular_user.id), visibility=Visibility.PUBLIC.value)
    db_session.add(playlist)
    await db_session.commit()

    body = _ok(client.get("/rest/getPlaylists.view", params=_creds(other_user)))
    assert any(p["id"] == str(playlist.id) for p in body["playlists"]["playlist"])


# ---------------------------------------------------------------------------
# Playback reporting + now playing
# ---------------------------------------------------------------------------


async def test_scrobble_records_listen(client, library, regular_user, db_session):
    song_id = str(library["song"].id)
    _ok(client.get("/rest/scrobble.view", params=_creds(regular_user, id=song_id)))

    rows = (
        (await db_session.execute(select(ListeningHistory).where(ListeningHistory.track_id == song_id))).scalars().all()
    )
    assert len(rows) == 1


def test_scrobble_now_playing_and_get_now_playing(client, library, regular_user):
    song_id = str(library["song"].id)
    _ok(client.get("/rest/scrobble.view", params=_creds(regular_user, id=song_id, submission="false")))

    body = _ok(client.get("/rest/getNowPlaying.view", params=_creds(regular_user)))
    entry = body["nowPlaying"]["entry"][0]
    assert entry["id"] == song_id
    assert entry["username"] == regular_user.username


# ---------------------------------------------------------------------------
# Binary endpoints (FastAPI fallback)
# ---------------------------------------------------------------------------


def test_stream_serves_audio_bytes(client, library, regular_user):
    song_id = str(library["song"].id)
    response = client.get("/rest/stream.view", params=_creds(regular_user, id=song_id))
    assert response.status_code == 200
    assert response.content == b"fake mp3 payload"
    assert "audio/mpeg" in response.headers["content-type"]


def test_stream_private_track_denied(client, library, other_user):
    body = _failed(client.get("/rest/stream.view", params=_creds(other_user, id=str(library["secret"].id))))
    assert body["error"]["code"] == 50


def test_stream_missing_id_returns_10(client, regular_user):
    body = _failed(client.get("/rest/stream.view", params=_creds(regular_user)))
    assert body["error"]["code"] == 10


def test_download_sets_attachment(client, library, regular_user):
    response = client.get("/rest/download.view", params=_creds(regular_user, id=str(library["song"].id)))
    assert response.status_code == 200
    assert "attachment" in response.headers.get("content-disposition", "")


def test_get_cover_art_track_fallback(client, library, regular_user):
    # The song has no image; the album cover resolves through tr-<id>.
    response = client.get("/rest/getCoverArt.view", params=_creds(regular_user, id=f"tr-{library['song'].id}"))
    assert response.status_code == 200
    assert "image/jpeg" in response.headers["content-type"]

    response = client.get("/rest/getCoverArt.view", params=_creds(regular_user, id=f"al-{library['album'].id}"))
    assert response.status_code == 200


def test_get_cover_art_public_album_visible_to_others(client, library, other_user):
    # The album is public, so other users can fetch its cover too.
    response = client.get("/rest/getCoverArt.view", params=_creds(other_user, id=f"al-{library['album'].id}"))
    assert response.status_code == 200
    assert "image/jpeg" in response.headers["content-type"]


async def test_get_cover_art_private_album_denied(client, library, other_user, db_session):
    album = library["album"]
    album.visibility = Visibility.PRIVATE.value
    await db_session.commit()
    body = _failed(client.get("/rest/getCoverArt.view", params=_creds(other_user, id=f"al-{album.id}")))
    assert body["error"]["code"] == 70


class TestSubsonicTornadoStream(tornado.testing.AsyncHTTPTestCase):
    """Tests for /rest/stream.view + /rest/download.view via _build_tornado_app."""

    def _create_wav(self, path, duration, sample_rate=8000):
        samples = int(duration * sample_rate)
        data = array.array(
            "h",
            (int(32767 * math.sin(2 * math.pi * 440 * i / sample_rate)) for i in range(samples)),
        )
        with wave.open(str(path), "w") as f:
            f.setnchannels(1)
            f.setsampwidth(2)
            f.setframerate(sample_rate)
            f.writeframes(data.tobytes())

    async def _create_tables(self):
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def _seed(self):
        self.media_path.mkdir(parents=True, exist_ok=True)
        storage_service = StorageService(get_storage(self.config.storage), self.config.storage)
        async with get_session() as session:
            self.user = await create_user(session, "sub", "sub@example.com", "secret")
            artist = Artist(name="Sub Artist")
            library = Library(name="Sub Library", owner_id=str(self.user.id))
            session.add_all([artist, library])
            await session.flush()

            wav_path = self._tmp / "song.wav"
            self._create_wav(wav_path, 1)
            with open(wav_path, "rb") as f:
                wav_file = await storage_service.store_file(
                    session,
                    f,
                    "audio/wav",
                    owner_id=str(self.user.id),
                    visibility=Visibility.PUBLIC.value,
                )
            session.add(wav_file)

            self.public_track = Track(
                title="Public Sub Track",
                artist_id=artist.id,
                audio_file_id=wav_file.id,
                owner_id=str(self.user.id),
                visibility=Visibility.PUBLIC.value,
                duration=1.0,
            )
            self.private_track = Track(
                title="Private Sub Track",
                artist_id=artist.id,
                audio_file_id=wav_file.id,
                owner_id=str(self.user.id),
                visibility=Visibility.PRIVATE.value,
                duration=1.0,
            )
            session.add_all([self.public_track, self.private_track])
            await session.commit()

    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp())
        self.media_path = self._tmp / "media"
        self.config = SonghiveConfig(
            auth={"secret_key": "a" * 64},
            database={"url": f"sqlite+aiosqlite:///{self._tmp / 'songhive.db'}"},
            server={"cors_origins": ["*"]},
            storage={"backend": "local", "local_path": str(self.media_path)},
        )
        self.engine = create_async_engine(self.config.database.url, poolclass=NullPool)
        init_db(engine=self.engine, force=True)
        asyncio.run(self._create_tables())
        asyncio.run(self._seed())
        super().setUp()

    def tearDown(self):
        self.io_loop.run_sync(self.engine.dispose)
        reset_db()
        super().tearDown()
        shutil.rmtree(self._tmp, ignore_errors=True)

    def get_app(self):
        from fakeredis.aioredis import FakeRedis

        app = create_app(self.config)
        app.state.redis = FakeRedis(decode_responses=True)
        return _build_tornado_app(self.config, app)

    def _auth(self, **extra):
        params = "u=sub&p=secret&v=1.16.1&c=tornado-test"
        for key, value in extra.items():
            params += f"&{key}={value}"
        return params

    def _envelope(self, response):
        root = ET.fromstring(response.body)
        assert root.tag == "{http://subsonic.org/restapi}subsonic-response"
        return root

    def test_stream_view_serves_audio(self):
        response = self.fetch(f"/rest/stream.view?{self._auth()}&id={self.public_track.id}")
        assert response.code == 200
        assert "audio/wav" in (response.headers.get("Content-Type") or "")
        assert response.body[:4] == b"RIFF"

    def test_stream_view_wrong_credentials_returns_40(self):
        response = self.fetch(f"/rest/stream.view?u=sub&p=wrong&id={self.public_track.id}&f=json")
        assert response.code == 200
        body = json.loads(response.body)["subsonic-response"]
        assert body["status"] == "failed"
        assert body["error"]["code"] == 40

    def test_stream_view_missing_id_returns_10(self):
        response = self.fetch(f"/rest/stream.view?{self._auth()}&f=json")
        body = json.loads(response.body)["subsonic-response"]
        assert body["error"]["code"] == 10

    def test_stream_view_owner_streams_private_track(self):
        response = self.fetch(f"/rest/stream.view?{self._auth()}&id={self.private_track.id}&f=json")
        assert response.code == 200
        assert "audio/wav" in (response.headers.get("Content-Type") or "")

    def test_stream_view_missing_track_returns_70(self):
        response = self.fetch(f"/rest/stream.view?{self._auth()}&id={self.public_track.id}deadbeef&f=json")
        body = json.loads(response.body)["subsonic-response"]
        assert body["status"] == "failed"
        assert body["error"]["code"] == 70

    def test_download_view_sets_attachment(self):
        response = self.fetch(f"/rest/download.view?{self._auth()}&id={self.public_track.id}")
        assert response.code == 200
        disposition = response.headers.get("Content-Disposition") or ""
        assert "attachment" in disposition

    def test_stream_view_range_request(self):
        response = self.fetch(
            f"/rest/stream.view?{self._auth()}&id={self.public_track.id}",
            headers={"Range": "bytes=0-99"},
        )
        assert response.code == 206
        assert len(response.body) == 100
