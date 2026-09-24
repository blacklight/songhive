"""
Tests for the shared Funkwhale/Songhive federated-music dialect.

Covers Funkwhale URL recognition (``/federation/music/...`` and
``/federation/actors/...``), remote music document classification and
containment, embedded-entity caching, library first-page scanning,
object-scoped follows (FEP-efda) against remote libraries, and the new
music entity serializers.
"""

import json as jsonlib
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from songhive.api.app import create_app
from songhive.api.deps import get_db
from songhive.federation.fetch import FetchError, FetchResult
from songhive.federation.serializers import (
    album_to_music_object,
    artist_to_music_object,
    library_to_music_object,
    music_collection_page,
    track_to_audio_object,
    track_to_music_track_object,
)
from songhive.models._enums import Visibility
from songhive.models.album import Album
from songhive.models.artist import Artist
from songhive.models.base import init_db
from songhive.models.follow import FOLLOW_STATE_ACCEPTED, FOLLOW_STATE_PENDING
from songhive.models.library import Library
from songhive.models.library_track import LibraryTrack
from songhive.models.remote_object import RemoteObject
from songhive.models.track import Track
from songhive.models.user import User
from songhive.services import federation as federation_service
from songhive.services import follows as follows_service
from songhive.services import remote_content as rc
from songhive.services.remote_content import RemoteTargetKind

FW_DOMAIN = "remote.invalid"  # .invalid never resolves — fetch is always stubbed
FW_CHANNEL_ACTOR = f"https://{FW_DOMAIN}/federation/actors/channel"
FW_CHANNEL_INBOX = f"https://{FW_DOMAIN}/federation/actors/channel/inbox"
ARTIST_URL = f"https://{FW_DOMAIN}/federation/music/artists/artist-uuid"
ALBUM_URL = f"https://{FW_DOMAIN}/federation/music/albums/album-uuid"
TRACK_URL = f"https://{FW_DOMAIN}/federation/music/tracks/track-uuid"
AUDIO_URL = f"https://{FW_DOMAIN}/federation/music/uploads/upload-uuid"
LIBRARY_URL = f"https://{FW_DOMAIN}/federation/music/libraries/library-uuid"
PAGE_URL = f"{LIBRARY_URL}?page=1"
PUBLIC = "https://www.w3.org/ns/activitystreams#Public"

FW_CHANNEL_DOC = {
    "@context": "https://www.w3.org/ns/activitystreams",
    "id": FW_CHANNEL_ACTOR,
    "type": "Person",
    "preferredUsername": "channel",
    "name": "Channel",
    "inbox": FW_CHANNEL_INBOX,
}

FW_ARTIST_DOC = {
    "type": "Artist",
    "id": ARTIST_URL,
    "name": "Remote Artist",
    "published": "2024-01-01T00:00:00Z",
    "musicbrainzId": None,
    "attributedTo": FW_CHANNEL_ACTOR,
}

FW_ALBUM_DOC = {
    "type": "Album",
    "id": ALBUM_URL,
    "name": "Remote Album",
    "published": "2024-01-01T00:00:00Z",
    "released": "2024-02-01",
    "musicbrainzId": None,
    "attributedTo": FW_CHANNEL_ACTOR,
    "artists": [FW_ARTIST_DOC],
}

FW_TRACK_DOC = {
    "type": "Track",
    "id": TRACK_URL,
    "name": "Remote Track",
    "published": "2024-01-01T00:00:00Z",
    "musicbrainzId": None,
    "position": 3,
    "attributedTo": FW_CHANNEL_ACTOR,
    "artists": [FW_ARTIST_DOC],
    "album": FW_ALBUM_DOC,
}

FW_AUDIO_DOC = {
    "type": "Audio",
    "id": AUDIO_URL,
    "name": "Remote Track",
    "published": "2024-01-01T00:00:00Z",
    "actor": FW_CHANNEL_ACTOR,
    "attributedTo": FW_CHANNEL_ACTOR,
    "duration": 195,
    "bitrate": 128000,
    "size": 3120000,
    "url": {
        "type": "Link",
        "href": f"https://{FW_DOMAIN}/media/upload.mp3",
        "mediaType": "audio/mpeg",
    },
    "library": LIBRARY_URL,
    "track": FW_TRACK_DOC,
    "to": [PUBLIC],
}

# Funkwhale library documents carry ``audience`` (not ``to``) for public
# visibility and expose only collection page links — items live under
# ``?page=N``.
FW_LIBRARY_DOC = {
    "@context": ["https://www.w3.org/ns/activitystreams", "https://funkwhale.audio/ns"],
    "type": "Library",
    "id": LIBRARY_URL,
    "name": "Remote Library",
    "attributedTo": FW_CHANNEL_ACTOR,
    "actor": FW_CHANNEL_ACTOR,
    "audience": PUBLIC,
    "followers": f"{LIBRARY_URL}/followers",
    "totalItems": 1,
    "first": PAGE_URL,
    "last": PAGE_URL,
}

FW_PAGE_DOC = {
    "@context": ["https://www.w3.org/ns/activitystreams", "https://funkwhale.audio/ns"],
    "id": PAGE_URL,
    "type": "CollectionPage",
    "partOf": LIBRARY_URL,
    "totalItems": 1,
    "items": [FW_AUDIO_DOC],
}


def _doc_result(url: str, doc: dict) -> FetchResult:
    return FetchResult(
        url=url,
        status_code=200,
        content_type="application/activity+json",
        body=jsonlib.dumps(doc).encode(),
        headers={},
    )


class _FakeFetcher:
    """A ``guarded_fetch`` stub routing URLs to canned documents."""

    def __init__(self, routes: dict):
        self.routes = routes
        self.calls: list[str] = []

    def __call__(self, url: str, *, check_url=None, **kwargs) -> FetchResult:
        self.calls.append(url)
        if check_url is not None:
            check_url(url)
        if url not in self.routes:
            raise FetchError(f"no stub route for {url}", status_code=502)
        response = self.routes[url]
        if isinstance(response, Exception):
            raise response
        return _doc_result(url, response)


@pytest.fixture
def remote_config(config, tmp_path):
    config.federation.instance_domain = "local.invalid"
    config.federation.private_key_path = tmp_path / "actor.pem"
    return config


@pytest.fixture
def fetcher(monkeypatch):
    def _install(routes: dict) -> _FakeFetcher:
        fake = _FakeFetcher(routes)
        monkeypatch.setattr(rc, "guarded_fetch", fake)
        return fake

    return _install


def _fed_config(config, tmp_path):
    fed = config.model_copy(deep=True)
    fed.federation.enabled = True
    fed.federation.instance_domain = "music.example.com"
    fed.federation.private_key_path = Path(fed.storage.local_path).parent / "actor.pem"
    return fed


@pytest.fixture
def fed_config(config, tmp_path):
    return _fed_config(config, tmp_path)


@pytest.fixture
def deliver_mock(monkeypatch):
    mock = MagicMock()
    monkeypatch.setattr("songhive.tasks.federation.deliver_activity", mock)
    return mock


async def _provision(db_session, fed_config, *users):
    for user in users:
        federation_service.ensure_user_actor(user, fed_config)
    await db_session.commit()
    for user in users:
        await db_session.execute(select(User).where(User.id == user.id).execution_options(populate_existing=True))


# ---------------------------------------------------------------------------
# URL parsing
# ---------------------------------------------------------------------------


class TestParseFunkwhaleTargets:
    def test_music_resource_urls(self, remote_config):
        for plural, kind in [
            ("tracks", "track"),
            ("albums", "album"),
            ("artists", "artist"),
            ("libraries", "library"),
            ("uploads", "track"),
        ]:
            target = rc.parse_remote_target(f"https://{FW_DOMAIN}/federation/music/{plural}/some-id", remote_config)
            assert target.kind == RemoteTargetKind.SONGHIVE_RESOURCE_URL, plural
            assert target.resource_kind == kind
            assert target.resource_id == "some-id"

    def test_music_resource_url_with_page_query(self, remote_config):
        target = rc.parse_remote_target(PAGE_URL, remote_config)
        assert target.kind == RemoteTargetKind.SONGHIVE_RESOURCE_URL
        assert target.resource_kind == "library"

    def test_federation_actor_url(self, remote_config):
        target = rc.parse_remote_target(FW_CHANNEL_ACTOR, remote_config)
        assert target.kind == RemoteTargetKind.ACTOR_URL
        assert target.username == "channel"
        assert target.domain == FW_DOMAIN

    def test_library_frontend_url_rewritten(self, remote_config):
        """Funkwhale's ``/library/{uuid}`` page maps to the federation document."""
        uuid = "dcfe753c-c21b-41a7-aea1-1ad66147db0a"
        target = rc.parse_remote_target(f"https://{FW_DOMAIN}/library/{uuid}", remote_config)
        assert target.kind == RemoteTargetKind.SONGHIVE_RESOURCE_URL
        assert target.resource_kind == "library"
        assert target.resource_id == uuid
        assert target.url == f"https://{FW_DOMAIN}/federation/music/libraries/{uuid}"

    def test_non_uuid_library_frontend_url_untouched(self, remote_config):
        target = rc.parse_remote_target(f"https://{FW_DOMAIN}/library/not-a-uuid", remote_config)
        assert target.kind == RemoteTargetKind.OBJECT_URL
        assert target.url == f"https://{FW_DOMAIN}/library/not-a-uuid"

    def test_songhive_resource_urls_unchanged(self, remote_config):
        target = rc.parse_remote_target(f"https://{FW_DOMAIN}/tracks/abc", remote_config)
        assert target.kind == RemoteTargetKind.SONGHIVE_RESOURCE_URL
        assert target.resource_kind == "track"


# ---------------------------------------------------------------------------
# Document helpers
# ---------------------------------------------------------------------------


class TestMusicDocHelpers:
    def test_detect_resource_types(self):
        assert rc._detect_resource_type(FW_AUDIO_DOC) == "track"
        assert rc._detect_resource_type(FW_TRACK_DOC) == "track"
        assert rc._detect_resource_type(FW_ALBUM_DOC) == "album"
        assert rc._detect_resource_type(FW_ARTIST_DOC) == "artist"
        assert rc._detect_resource_type(FW_LIBRARY_DOC) == "library"
        assert rc._detect_resource_type({"type": "Note"}) is None

    def test_parent_urls(self):
        assert rc._parent_url_of(FW_AUDIO_DOC) == LIBRARY_URL
        assert rc._parent_url_of(FW_TRACK_DOC) == ALBUM_URL
        assert rc._parent_url_of(FW_ALBUM_DOC) == ARTIST_URL
        assert rc._parent_url_of(FW_PAGE_DOC) == LIBRARY_URL
        assert rc._parent_url_of(FW_ARTIST_DOC) is None

    def test_audience_counts_as_public(self):
        assert rc._doc_is_public(FW_LIBRARY_DOC) is True
        assert rc._doc_is_public({"audience": "as:Public"}) is True
        assert rc._doc_is_public({"audience": ["Public"]}) is True
        assert rc._doc_is_public({"audience": "https://x.invalid/followers"}) is False


# ---------------------------------------------------------------------------
# Remote dereference of Funkwhale documents
# ---------------------------------------------------------------------------


class TestDereferenceFunkwhaleObjects:
    async def test_audio_caches_embedded_entities(self, db_session, remote_config, fetcher):
        fetcher(
            {
                AUDIO_URL: FW_AUDIO_DOC,
                FW_CHANNEL_ACTOR: FW_CHANNEL_DOC,
            }
        )
        result = await rc.dereference_remote_object(db_session, remote_config, AUDIO_URL)
        await db_session.commit()

        row = result.remote_object
        assert row.resource_type == "track"
        assert row.object_type == "Audio"
        assert row.visibility == "public"
        assert row.parent_url == LIBRARY_URL
        assert row.audio_url == f"https://{FW_DOMAIN}/media/upload.mp3"
        # A bare music resource is cached, not materialized as a feed post.
        assert result.activity is None

        track = await rc._cached_remote_object(db_session, TRACK_URL)
        album = await rc._cached_remote_object(db_session, ALBUM_URL)
        artist = await rc._cached_remote_object(db_session, ARTIST_URL)
        assert track is not None and track.resource_type == "track"
        assert track.parent_url == ALBUM_URL
        assert album is not None and album.resource_type == "album"
        assert album.parent_url == ARTIST_URL
        assert artist is not None and artist.resource_type == "artist"

    async def test_library_scans_first_page(self, db_session, remote_config, fetcher):
        fake = fetcher(
            {
                LIBRARY_URL: FW_LIBRARY_DOC,
                PAGE_URL: FW_PAGE_DOC,
                FW_CHANNEL_ACTOR: FW_CHANNEL_DOC,
            }
        )
        result = await rc.dereference_remote_object(db_session, remote_config, LIBRARY_URL)
        await db_session.commit()

        row = result.remote_object
        assert row.resource_type == "library"
        # ``audience``-only public visibility is honored.
        assert row.visibility == "public"
        # The first collection page was fetched and its item cached under
        # the library.
        assert PAGE_URL in fake.calls
        children = await rc.get_remote_object_children(db_session, row)
        assert [c.canonical_url for c in children] == [AUDIO_URL]
        assert children[0].resource_type == "track"

    async def test_library_frontend_url_dereferences(self, db_session, remote_config, fetcher):
        """A pasted ``/library/{uuid}`` frontend URL resolves through the federation route."""
        uuid = "dcfe753c-c21b-41a7-aea1-1ad66147db0a"
        library_url = f"https://{FW_DOMAIN}/federation/music/libraries/{uuid}"
        page_url = f"{library_url}?page=1"
        library_doc = {**FW_LIBRARY_DOC, "id": library_url, "first": page_url, "last": page_url}
        page_doc = {**FW_PAGE_DOC, "id": page_url, "partOf": library_url}
        fake = fetcher(
            {
                library_url: library_doc,
                page_url: page_doc,
                FW_CHANNEL_ACTOR: FW_CHANNEL_DOC,
            }
        )
        result = await rc.dereference_remote_object(db_session, remote_config, f"https://{FW_DOMAIN}/library/{uuid}")
        await db_session.commit()

        assert result.remote_object.canonical_url == library_url
        assert result.remote_object.resource_type == "library"
        assert fake.calls[0] == library_url

    async def test_collection_page_url_resolves_parent(self, db_session, remote_config, fetcher):
        fetcher(
            {
                PAGE_URL: FW_PAGE_DOC,
                LIBRARY_URL: FW_LIBRARY_DOC,
                FW_CHANNEL_ACTOR: FW_CHANNEL_DOC,
            }
        )
        result = await rc.dereference_remote_object(db_session, remote_config, PAGE_URL)
        await db_session.commit()

        assert result.remote_object.canonical_url == LIBRARY_URL
        assert result.remote_object.resource_type == "library"
        # The page itself is cached too.
        page = await rc._cached_remote_object(db_session, PAGE_URL)
        assert page is not None

    async def test_audio_without_attribution_still_caches(self, db_session, remote_config, fetcher):
        """A music doc with an unresolvable actor still caches — the actor URL is enough."""
        doc = {**FW_AUDIO_DOC, "attributedTo": None, "actor": FW_CHANNEL_ACTOR}
        fake = fetcher(
            {
                AUDIO_URL: doc,
                FW_CHANNEL_ACTOR: FetchError("gone", status_code=502),
            }
        )
        result = await rc.dereference_remote_object(db_session, remote_config, AUDIO_URL)
        assert result.remote_object.resource_type == "track"
        assert result.remote_object.actor_url == FW_CHANNEL_ACTOR
        assert fake.calls[0] == AUDIO_URL

    async def test_actor_url_webfinger_fallback(self, db_session, remote_config, fetcher):
        """A Funkwhale actor URL that fails to dereference retries via WebFinger."""
        canonical_actor = f"https://{FW_DOMAIN}/federation/actors/channel-main"
        webfinger_url = f"https://{FW_DOMAIN}/.well-known/webfinger?resource=acct:channel@{FW_DOMAIN}"
        fetcher(
            {
                FW_CHANNEL_ACTOR: FetchError("not fetchable", status_code=502),
                webfinger_url: {
                    "subject": f"acct:channel@{FW_DOMAIN}",
                    "links": [
                        {"rel": "self", "type": "application/activity+json", "href": canonical_actor},
                    ],
                },
                canonical_actor: {**FW_CHANNEL_DOC, "id": canonical_actor},
            }
        )
        actor = await rc.lookup_remote_actor(db_session, remote_config, FW_CHANNEL_ACTOR)
        assert actor.actor_url == canonical_actor
        assert actor.username == "channel"


# ---------------------------------------------------------------------------
# Object-scoped follows (remote libraries)
# ---------------------------------------------------------------------------


class TestFollowRemoteObject:
    async def _cache_library(self, db_session):
        """Cache a remote library row for the follow tests."""
        row = RemoteObject(
            canonical_url=LIBRARY_URL,
            domain=FW_DOMAIN,
            object_type="Library",
            resource_type="library",
            actor_url=FW_CHANNEL_ACTOR,
            visibility="public",
            name="Remote Library",
            payload={"actor": FW_CHANNEL_ACTOR},
        )
        db_session.add(row)
        await db_session.flush()
        return row

    async def test_follow_remote_library(self, db_session, fed_config, regular_user, fetcher, deliver_mock):
        fetcher({FW_CHANNEL_ACTOR: FW_CHANNEL_DOC})
        await self._cache_library(db_session)
        await _provision(db_session, fed_config, regular_user)

        row = await follows_service.follow_remote_object(db_session, fed_config, regular_user, LIBRARY_URL)

        assert row.state == FOLLOW_STATE_PENDING
        assert row.target_actor_url == LIBRARY_URL
        assert row.target_user_id is None
        assert row.inbox_url == FW_CHANNEL_INBOX

        deliver_mock.delay.assert_called_once()
        activity, inbox_url, key_id, _key = deliver_mock.delay.call_args[0]
        assert activity["type"] == "Follow"
        assert activity["actor"] == regular_user.actor_url
        assert activity["object"] == LIBRARY_URL
        assert inbox_url == FW_CHANNEL_INBOX

    async def test_follow_own_resource_rejected(self, db_session, fed_config, regular_user):
        await _provision(db_session, fed_config, regular_user)
        row = RemoteObject(
            canonical_url=LIBRARY_URL,
            domain=FW_DOMAIN,
            object_type="Library",
            resource_type="library",
            actor_url=regular_user.actor_url,
            visibility="public",
            payload={"actor": regular_user.actor_url},
        )
        db_session.add(row)
        await db_session.flush()

        with pytest.raises(follows_service.FollowError) as exc:
            await follows_service.follow_remote_object(db_session, fed_config, regular_user, LIBRARY_URL)
        assert exc.value.status_code == 400

    async def test_apply_follow_decision_from_controlling_actor(
        self, db_session, fed_config, regular_user, fetcher, deliver_mock
    ):
        """Funkwhale answers a library follow with ``Accept`` from ``library.actor``."""
        fetcher({FW_CHANNEL_ACTOR: FW_CHANNEL_DOC})
        await self._cache_library(db_session)
        await _provision(db_session, fed_config, regular_user)
        row = await follows_service.follow_remote_object(db_session, fed_config, regular_user, LIBRARY_URL)
        await db_session.commit()

        accept = {
            "type": "Accept",
            "actor": FW_CHANNEL_ACTOR,
            "object": {
                "type": "Follow",
                "id": row.activity_id,
                "actor": regular_user.actor_url,
                "object": LIBRARY_URL,
            },
        }
        assert await follows_service.apply_follow_decision(db_session, activity=accept) is True
        await db_session.refresh(row)
        assert row.state == FOLLOW_STATE_ACCEPTED
        assert row.accepted_at is not None

    async def test_apply_follow_decision_foreign_actor_rejected(
        self, db_session, fed_config, regular_user, fetcher, deliver_mock
    ):
        """An ``Accept`` from an unrelated actor does not approve the follow."""
        fetcher({FW_CHANNEL_ACTOR: FW_CHANNEL_DOC})
        await self._cache_library(db_session)
        await _provision(db_session, fed_config, regular_user)
        row = await follows_service.follow_remote_object(db_session, fed_config, regular_user, LIBRARY_URL)
        await db_session.commit()

        accept = {
            "type": "Accept",
            "actor": "https://other.invalid/users/mallory",
            "object": {
                "type": "Follow",
                "id": row.activity_id,
                "actor": regular_user.actor_url,
                "object": LIBRARY_URL,
            },
        }
        assert await follows_service.apply_follow_decision(db_session, activity=accept) is False
        await db_session.refresh(row)
        assert row.state == FOLLOW_STATE_PENDING


# ---------------------------------------------------------------------------
# Remote items in the collection
# ---------------------------------------------------------------------------


def _remote_row(object_id: str = "obj-1", **kwargs) -> RemoteObject:
    defaults = {
        "canonical_url": f"{AUDIO_URL}/{object_id}",
        "domain": FW_DOMAIN,
        "object_type": "Audio",
        "resource_type": "track",
        "actor_url": FW_CHANNEL_ACTOR,
        "visibility": "public",
        "name": "Remote Track",
    }
    defaults.update(kwargs)
    row = RemoteObject(**defaults)
    row.id = object_id
    return row


class TestRemoteCollectionItems:
    def test_add_and_remove_remote_item(self, client, db_session, regular_user, auth_headers):
        db_session.add(_remote_row())
        headers = auth_headers(regular_user)

        response = client.post("/api/v1/collection/remote/obj-1", headers=headers)
        assert response.status_code == 201
        assert response.json()["item_type"] == "remote"

        listing = client.get("/api/v1/collection/", headers=headers)
        assert [item["item_id"] for item in listing.json()] == ["obj-1"]

        response = client.delete("/api/v1/collection/remote/obj-1", headers=headers)
        assert response.status_code == 204

    def test_non_resource_remote_object_not_collectable(self, client, db_session, regular_user, auth_headers):
        """Bare remote posts (no ``resource_type``) cannot be collected."""
        db_session.add(_remote_row(object_id="post-1", resource_type=None, object_type="Note"))
        response = client.post("/api/v1/collection/remote/post-1", headers=auth_headers(regular_user))
        assert response.status_code == 404

    def test_missing_remote_object_404(self, client, regular_user, auth_headers):
        response = client.post("/api/v1/collection/remote/missing", headers=auth_headers(regular_user))
        assert response.status_code == 404


class TestRemoteObjectList:
    def test_collection_lists_saved_remote_resources(self, client, db_session, regular_user, auth_headers):
        """Collected remote objects surface through ``GET /remote/objects``."""
        db_session.add(_remote_row(object_id="album-1", resource_type="album", name="Remote Album"))
        db_session.add(_remote_row(object_id="track-1", resource_type="track", name="Remote Track"))
        headers = auth_headers(regular_user)
        assert client.post("/api/v1/collection/remote/album-1", headers=headers).status_code == 201

        response = client.get("/api/v1/remote/objects", params={"collection": "true"}, headers=headers)
        assert response.status_code == 200
        items = response.json()["items"]
        assert [item["id"] for item in items] == ["album-1"]
        assert items[0]["in_collection"] is True

    def test_collection_filter_by_resource_type(self, client, db_session, regular_user, auth_headers):
        db_session.add(_remote_row(object_id="album-1", resource_type="album"))
        db_session.add(_remote_row(object_id="track-1", resource_type="track"))
        headers = auth_headers(regular_user)
        client.post("/api/v1/collection/remote/album-1", headers=headers)
        client.post("/api/v1/collection/remote/track-1", headers=headers)

        response = client.get(
            "/api/v1/remote/objects",
            params={"collection": "true", "resource_type": "track"},
            headers=headers,
        )
        assert response.status_code == 200
        assert [item["id"] for item in response.json()["items"]] == ["track-1"]

    def test_collection_requires_auth(self, client):
        assert client.get("/api/v1/remote/objects", params={"collection": "true"}).status_code in (401, 403)

    def test_invalid_resource_type_422(self, client, regular_user, auth_headers):
        response = client.get(
            "/api/v1/remote/objects",
            params={"resource_type": "bogus"},
            headers=auth_headers(regular_user),
        )
        assert response.status_code == 422


class TestRemoteMediaResolution:
    """Media resolution for remote tracks with no direct ``audio_url``."""

    async def test_audio_cache_stamps_media_of(self, db_session, remote_config, fetcher):
        """Caching an ``Audio`` doc records which media entity it renders."""
        fetcher({AUDIO_URL: FW_AUDIO_DOC, FW_CHANNEL_ACTOR: FW_CHANNEL_DOC})
        result = await rc.dereference_remote_object(db_session, remote_config, AUDIO_URL)
        assert result.remote_object.media_of_url == TRACK_URL

    async def test_resolve_media_url_prefers_own_link(self, db_session):
        row = _remote_row(audio_url="https://fw.example/media/a.mp3")
        db_session.add(row)
        await db_session.flush()
        assert await rc.resolve_media_url(db_session, row) == "https://fw.example/media/a.mp3"

    async def test_resolve_media_url_via_rendition(self, db_session):
        """A metadata-only ``Track`` resolves to its cached ``Audio`` rendition."""
        db_session.add(_remote_row(object_id="t-1", object_type="Track", canonical_url=TRACK_URL, audio_url=None))
        db_session.add(
            _remote_row(
                object_id="a-1",
                canonical_url=AUDIO_URL,
                audio_url="https://fw.example/media/a.mp3",
                media_of_url=TRACK_URL,
            )
        )
        await db_session.flush()
        track_row = await rc._cached_remote_object(db_session, TRACK_URL)
        assert await rc.resolve_media_url(db_session, track_row) == "https://fw.example/media/a.mp3"

    async def test_resolve_media_url_legacy_payload_fallback(self, db_session):
        """Rows cached before ``media_of_url`` resolve through the payload's ``track.id``."""
        db_session.add(_remote_row(object_id="t-1", object_type="Track", canonical_url=TRACK_URL, audio_url=None))
        db_session.add(
            _remote_row(
                object_id="a-1",
                canonical_url=AUDIO_URL,
                audio_url="https://fw.example/media/a.mp3",
                media_of_url=None,
                payload={"type": "Audio", "track": {"id": TRACK_URL}},
            )
        )
        await db_session.flush()
        track_row = await rc._cached_remote_object(db_session, TRACK_URL)
        assert await rc.resolve_media_url(db_session, track_row) == "https://fw.example/media/a.mp3"

    async def test_resolve_media_url_unplayable(self, db_session):
        db_session.add(_remote_row(object_id="t-1", object_type="Track", canonical_url=TRACK_URL, audio_url=None))
        await db_session.flush()
        track_row = await rc._cached_remote_object(db_session, TRACK_URL)
        assert await rc.resolve_media_url(db_session, track_row) is None

    def test_stream_url_on_resource_response(self, client, db_session, regular_user, auth_headers):
        """A ``Track`` resource with a cached rendition advertises the stream endpoint."""
        db_session.add(_remote_row(object_id="t-1", object_type="Track", canonical_url=TRACK_URL, audio_url=None))
        db_session.add(
            _remote_row(
                object_id="a-1",
                canonical_url=AUDIO_URL,
                audio_url="https://fw.example/media/a.mp3",
                media_of_url=TRACK_URL,
            )
        )
        db_session.flush()
        response = client.get("/api/v1/remote/track/t-1", headers=auth_headers(regular_user))
        assert response.status_code == 200
        assert response.json()["stream_url"] == "/api/v1/remote/objects/t-1/stream"

    def test_stream_url_absent_when_unplayable(self, client, db_session, regular_user, auth_headers):
        db_session.add(_remote_row(object_id="t-1", object_type="Track", canonical_url=TRACK_URL, audio_url=None))
        db_session.flush()
        response = client.get("/api/v1/remote/track/t-1", headers=auth_headers(regular_user))
        assert response.status_code == 200
        assert response.json()["stream_url"] is None

    def test_stream_endpoint_redirects_to_rendition(self, client, db_session, regular_user, auth_headers):
        db_session.add(_remote_row(object_id="t-1", object_type="Track", canonical_url=TRACK_URL, audio_url=None))
        db_session.add(
            _remote_row(
                object_id="a-1",
                canonical_url=AUDIO_URL,
                audio_url="https://fw.example/media/a.mp3",
                media_of_url=TRACK_URL,
            )
        )
        db_session.flush()
        response = client.get(
            "/api/v1/remote/objects/t-1/stream",
            headers=auth_headers(regular_user),
            follow_redirects=False,
        )
        assert response.status_code in (302, 307)
        assert response.headers["location"] == "https://fw.example/media/a.mp3"

    def test_stream_endpoint_404_when_unplayable(self, client, db_session, regular_user, auth_headers):
        db_session.add(_remote_row(object_id="t-1", object_type="Track", canonical_url=TRACK_URL, audio_url=None))
        db_session.flush()
        response = client.get("/api/v1/remote/objects/t-1/stream", headers=auth_headers(regular_user))
        assert response.status_code == 404


class TestRemoteObjectMusicFields:
    def test_audio_document_fields(self):
        row = _remote_row(
            payload={
                "type": "Audio",
                "duration": 250,
                "track": {
                    "type": "Track",
                    "artists": [{"type": "Artist", "name": "Remote Artist"}],
                    "album": {"type": "Album", "name": "Remote Album"},
                },
            }
        )
        fields = rc.remote_object_music_fields(row)
        assert fields == {"duration": 250, "artist_name": "Remote Artist", "album_name": "Remote Album"}

    def test_track_document_fields(self):
        row = _remote_row(
            payload={
                "type": "Track",
                "duration": 95,
                "artists": [{"type": "Artist", "name": "Remote Artist"}],
                "album": {"type": "Album", "name": "Remote Album"},
            }
        )
        fields = rc.remote_object_music_fields(row)
        assert fields["duration"] == 95
        assert fields["artist_name"] == "Remote Artist"
        assert fields["album_name"] == "Remote Album"

    def test_missing_payload(self):
        fields = rc.remote_object_music_fields(_remote_row())
        assert fields == {"duration": None, "artist_name": None, "album_name": None}


# ---------------------------------------------------------------------------
# Music entity serializers
# ---------------------------------------------------------------------------


def _artist():
    artist = Artist(name="Local Artist", musicbrainz_id="mbid-artist")
    artist.id = "artist-1"
    return artist


def _track(**kwargs):
    artist = _artist()
    track = Track(
        title="Local Track",
        artist_id=artist.id,
        audio_file_id="file-1",
        duration=195.5,
        track_number=3,
        disc_number=1,
        visibility=Visibility.PUBLIC.value,
        **kwargs,
    )
    track.id = "track-1"
    return track, artist


class TestMusicSerializers:
    def test_artist_document(self):
        doc = artist_to_music_object(_artist(), "music.example.com", "https://music.example.com/ap/actor")
        assert doc["type"] == "Artist"
        assert doc["id"] == "https://music.example.com/artists/artist-1"
        assert doc["name"] == "Local Artist"
        assert doc["musicbrainzId"] == "mbid-artist"
        assert doc["attributedTo"] == "https://music.example.com/ap/actor"
        assert doc["published"]

    def test_album_document(self):
        album = Album(title="Local Album", artist_id="artist-1", release_year=2020)
        album.id = "album-1"
        doc = album_to_music_object(album, _artist(), "music.example.com", "https://music.example.com/ap/actor")
        assert doc["type"] == "Album"
        assert doc["released"] == "2020-01-01"
        assert doc["artists"][0]["type"] == "Artist"
        assert doc["artists"][0]["id"] == "https://music.example.com/artists/artist-1"

    def test_track_document(self):
        track, artist = _track()
        doc = track_to_music_track_object(track, artist, "music.example.com", "https://music.example.com/users/u")
        assert doc["type"] == "Track"
        assert doc["position"] == 3
        assert doc["disc"] == 1
        assert doc["album"]["type"] == "Album"
        assert doc["artists"][0]["name"] == "Local Artist"

    def test_library_document(self):
        doc = library_to_music_object(
            "https://music.example.com/libraries/lib-1",
            "My Library",
            "https://music.example.com/users/u",
            250,
        )
        assert doc["type"] == "Library"
        assert doc["actor"] == "https://music.example.com/users/u"
        assert doc["audience"] == "https://www.w3.org/ns/activitystreams#Public"
        assert doc["totalItems"] == 250
        assert doc["first"] == "https://music.example.com/libraries/lib-1?page=1"
        assert doc["last"] == "https://music.example.com/libraries/lib-1?page=3"
        assert doc["followers"] == "https://music.example.com/libraries/lib-1/followers"

    def test_collection_page(self):
        doc = music_collection_page(
            "https://music.example.com/libraries/lib-1",
            2,
            250,
            [{"type": "Audio", "id": "x"}],
            "https://music.example.com/users/u",
        )
        assert doc["type"] == "CollectionPage"
        assert doc["partOf"] == "https://music.example.com/libraries/lib-1"
        assert doc["prev"].endswith("?page=1")
        assert doc["next"].endswith("?page=3")
        assert doc["items"] == [{"type": "Audio", "id": "x"}]

    def test_audio_object_funkwhale_fields(self):
        track, artist = _track()
        doc = track_to_audio_object(
            track,
            artist,
            "music.example.com",
            library_url="https://music.example.com/libraries/lib-1",
        )
        assert doc is not None
        assert doc["duration"] == 195
        assert doc["bitrate"] == 0  # no audio_file loaded
        assert doc["size"] == 0
        assert doc["library"] == "https://music.example.com/libraries/lib-1"
        assert doc["track"]["type"] == "Track"
        assert doc["track"]["id"] == "https://music.example.com/tracks/track-1"


# ---------------------------------------------------------------------------
# Served Audio document (Funkwhale ``UploadSerializer`` compatibility)
# ---------------------------------------------------------------------------

ACTIVITY_JSON = "application/activity+json"


@pytest.fixture
def fed_app(fed_config, engine):
    init_db(engine=engine, force=True)
    return create_app(fed_config)


@pytest.fixture
def fed_client(fed_app, db_session, fake_redis_server, monkeypatch):
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


class TestServedAudioDocument:
    async def _published_track(self, db_session, regular_user) -> Track:
        artist = Artist(name="Local Artist")
        db_session.add(artist)
        await db_session.flush()
        track = Track(
            title="Local Track",
            artist_id=str(artist.id),
            owner_id=str(regular_user.id),
            visibility=Visibility.PUBLIC.value,
            federation_object_id="fw-serve",
        )
        db_session.add(track)
        await db_session.commit()
        return track

    async def test_track_audio_document_names_implicit_library(self, fed_client, db_session, regular_user):
        """The served ``Audio`` names the owner's implicit library — Funkwhale
        requires ``library`` on uploads."""
        track = await self._published_track(db_session, regular_user)
        response = fed_client.get(f"/tracks/{track.id}", headers={"Accept": ACTIVITY_JSON})
        assert response.status_code == 200
        doc = response.json()
        actor_url = "https://music.example.com/users/regular"
        assert doc["library"] == f"{actor_url}/library"

    async def test_track_audio_document_names_public_library(self, fed_client, db_session, regular_user):
        """A track in a public ``Library`` names ``/libraries/{id}``."""
        track = await self._published_track(db_session, regular_user)
        library = Library(name="Public Lib", owner_id=str(regular_user.id), visibility=Visibility.PUBLIC.value)
        db_session.add(library)
        await db_session.flush()
        db_session.add(LibraryTrack(library_id=str(library.id), track_id=str(track.id)))
        await db_session.commit()

        response = fed_client.get(f"/tracks/{track.id}", headers={"Accept": ACTIVITY_JSON})
        assert response.status_code == 200
        assert response.json()["library"] == f"https://music.example.com/libraries/{library.id}"
