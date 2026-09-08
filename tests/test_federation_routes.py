"""
Federation route tests.
"""

from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from songhive.api.app import create_app
from songhive.api.deps import get_db
from songhive.models import Visibility
from songhive.models.activity import Activity, ActivityMention
from songhive.models.artist import Artist
from songhive.models.base import init_db
from songhive.models.track import Track

ACTIVITY_JSON = "application/activity+json"
JRD_JSON = "application/jrd+json"


class _MockCeleryTask:
    """Stand-in for a Celery task that records ``.delay()`` calls."""

    def __init__(self):
        self.calls: list[tuple] = []

    def delay(self, *args, **kwargs):
        self.calls.append((args, kwargs))


@pytest.fixture
def fed_config(config, tmp_path):
    """Return a federation-enabled config backed by the test database."""
    fed = config.model_copy(deep=True)
    fed.federation.enabled = True
    fed.federation.instance_domain = "music.example.com"
    fed.federation.instance_name = "Songhive"
    fed.federation.instance_description = "A federated music instance"
    fed.federation.private_key_path = Path(fed.storage.local_path).parent / "actor.pem"
    fed.federation.blocked_instances = ["evil.example"]
    return fed


@pytest.fixture
def fed_app(fed_config, engine):
    """Create a federation-enabled test application."""
    init_db(engine=engine, force=True)
    return create_app(fed_config)


@pytest.fixture
def fed_client(fed_app, db_session, fake_redis_server, monkeypatch):
    """Create a test client for the federation-enabled app."""
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


async def test_federation_routes_disabled_when_federation_off(client, regular_user):
    """Federation endpoints return 404 when federation is disabled."""
    response = client.get("/users/regular", headers={"Accept": ACTIVITY_JSON})
    assert response.status_code == status.HTTP_404_NOT_FOUND


async def test_get_actor_returns_person(fed_client, regular_user):
    """GET /users/{username} returns a valid ActivityPub Person."""
    response = fed_client.get(
        "/users/regular",
        headers={"Accept": "application/activity+json"},
    )
    assert response.status_code == status.HTTP_200_OK
    assert ACTIVITY_JSON in response.headers["content-type"]

    data = response.json()
    assert data["type"] == "Person"
    assert data["preferredUsername"] == "regular"
    assert data["id"] == "https://music.example.com/users/regular"
    assert data["inbox"] == "https://music.example.com/users/regular/inbox"
    assert data["outbox"] == "https://music.example.com/users/regular/outbox"
    assert data["followers"] == "https://music.example.com/users/regular/followers"
    assert data["following"] == "https://music.example.com/users/regular/following"
    assert data["publicKey"]["publicKeyPem"]
    assert data["publicKey"]["id"] == "https://music.example.com/users/regular#main-key"


async def test_get_actor_unknown_user_returns_404(fed_client):
    """GET /users/{username} returns 404 for an unknown user."""
    response = fed_client.get(
        "/users/nobody",
        headers={"Accept": "application/activity+json"},
    )
    assert response.status_code == status.HTTP_404_NOT_FOUND


async def test_alias_returns_actor_for_activitypub_accept(fed_client, regular_user):
    """GET /@{username} returns the actor document for AP clients."""
    response = fed_client.get(
        "/@regular",
        headers={"Accept": "application/activity+json"},
    )
    assert response.status_code == status.HTTP_200_OK
    assert ACTIVITY_JSON in response.headers["content-type"]
    data = response.json()
    assert data["type"] == "Person"
    assert data["preferredUsername"] == "regular"


async def test_alias_returns_actor_for_ld_json_accept(fed_client, regular_user):
    """GET /@{username} returns the actor document for ld+json clients."""
    response = fed_client.get(
        "/@regular",
        headers={"Accept": "application/ld+json"},
    )
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["type"] == "Person"


async def test_alias_redirects_for_html_accept(fed_client, regular_user):
    """GET /@{username} redirects browsers to the local profile route."""
    response = fed_client.get(
        "/@regular",
        headers={"Accept": "text/html"},
        follow_redirects=False,
    )
    assert response.status_code == status.HTTP_307_TEMPORARY_REDIRECT
    assert response.headers["location"] == "/api/v1/users/regular"


async def test_alias_redirects_without_accept(fed_client, regular_user):
    """GET /@{username} redirects by default for plain browser requests."""
    response = fed_client.get("/@regular", follow_redirects=False)
    assert response.status_code == status.HTTP_307_TEMPORARY_REDIRECT
    assert response.headers["location"] == "/api/v1/users/regular"


async def test_alias_unknown_user_returns_404(fed_client):
    """GET /@{username} returns 404 for an unknown user."""
    response = fed_client.get("/@nobody", headers={"Accept": "application/activity+json"})
    assert response.status_code == status.HTTP_404_NOT_FOUND


async def test_webfinger_returns_user_actor(fed_client, regular_user):
    """WebFinger resolves a local user to their actor URL."""
    response = fed_client.get("/.well-known/webfinger?resource=acct:regular@music.example.com")
    assert response.status_code == status.HTTP_200_OK
    assert JRD_JSON in response.headers["content-type"]

    data = response.json()
    assert data["subject"] == "acct:regular@music.example.com"
    self_link = next(link for link in data["links"] if link["rel"] == "self")
    assert self_link["href"] == "https://music.example.com/users/regular"
    assert self_link["type"] == ACTIVITY_JSON


async def test_webfinger_returns_instance_actor(fed_client):
    """WebFinger preserves the instance actor resource."""
    response = fed_client.get("/.well-known/webfinger?resource=acct:songhive@music.example.com")
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["subject"] == "acct:songhive@music.example.com"
    self_link = next(link for link in data["links"] if link["rel"] == "self")
    assert self_link["href"] == "https://music.example.com/ap/actor"


async def test_webfinger_unknown_user_returns_404(fed_client):
    """WebFinger returns 404 for an unknown local user."""
    response = fed_client.get("/.well-known/webfinger?resource=acct:nobody@music.example.com")
    assert response.status_code == status.HTTP_404_NOT_FOUND


async def test_webfinger_wrong_domain_returns_404(fed_client, regular_user):
    """WebFinger returns 404 for resources on a different domain."""
    response = fed_client.get("/.well-known/webfinger?resource=acct:regular@other.example.com")
    assert response.status_code == status.HTTP_404_NOT_FOUND


async def test_inbox_returns_202_for_allowed_sender(fed_client, regular_user, monkeypatch):
    """POST /users/{username}/inbox accepts allowed senders and enqueues processing."""
    mock_task = _MockCeleryTask()
    monkeypatch.setattr("songhive.api.routes.federation.process_incoming", mock_task)

    activity = {"type": "Follow", "actor": "https://good.example/users/bob"}
    response = fed_client.post("/users/regular/inbox", json=activity)

    assert response.status_code == status.HTTP_202_ACCEPTED
    assert len(mock_task.calls) == 1
    args, kwargs = mock_task.calls[0]
    assert args == (activity,)
    assert kwargs["username"] == "regular"
    assert kwargs["method"] == "POST"
    assert kwargs["path"] == "/users/regular/inbox"
    assert kwargs["headers"] is not None
    assert isinstance(kwargs["body_b64"], str)
    assert len(kwargs["body_b64"]) > 0


async def test_inbox_returns_403_for_blocked_sender(fed_client, regular_user, monkeypatch):
    """POST /users/{username}/inbox rejects blocked senders without enqueuing."""
    mock_task = _MockCeleryTask()
    monkeypatch.setattr("songhive.api.routes.federation.process_incoming", mock_task)

    activity = {"type": "Follow", "actor": "https://evil.example/users/bob"}
    response = fed_client.post("/users/regular/inbox", json=activity)

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert len(mock_task.calls) == 0


async def test_inbox_returns_404_for_unknown_user(fed_client, monkeypatch):
    """POST /users/{username}/inbox returns 404 for unknown users."""
    mock_task = _MockCeleryTask()
    monkeypatch.setattr("songhive.api.routes.federation.process_incoming", mock_task)

    activity = {"type": "Follow", "actor": "https://good.example/users/bob"}
    response = fed_client.post("/users/nobody/inbox", json=activity)

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert len(mock_task.calls) == 0


async def test_get_object_returns_audio_for_public_track(fed_client, db_session, regular_user):
    """GET /users/{username}/objects/{object_id} returns a public Audio object."""
    artist = Artist(name="Artist")
    db_session.add(artist)
    await db_session.flush()

    track = Track(
        title="Public Track",
        artist_id=str(artist.id),
        owner_id=str(regular_user.id),
        visibility=Visibility.PUBLIC.value,
        federation_object_id="pub-1",
    )
    db_session.add(track)
    await db_session.commit()

    response = fed_client.get(
        "/users/regular/objects/pub-1",
        headers={"Accept": ACTIVITY_JSON},
    )
    assert response.status_code == status.HTTP_200_OK

    data = response.json()
    assert data["type"] == "Audio"
    assert data["@context"] == "https://www.w3.org/ns/activitystreams"
    assert data["id"] == "https://music.example.com/users/regular/objects/pub-1"
    assert data["to"] == ["https://www.w3.org/ns/activitystreams#Public"]
    assert data["cc"] == ["https://music.example.com/users/regular/followers"]
    assert data["attributedTo"][0] == "https://music.example.com/users/regular"
    assert any(
        link["mediaType"] == "text/html" and link["href"].startswith("https://music.example.com/tracks/")
        for link in data["url"]
    )


async def test_get_object_returns_404_for_private_track(fed_client, db_session, regular_user):
    """GET /users/{username}/objects/{object_id} returns 404 when the track is not public."""
    artist = Artist(name="Artist")
    db_session.add(artist)
    await db_session.flush()

    track = Track(
        title="Private Track",
        artist_id=str(artist.id),
        owner_id=str(regular_user.id),
        visibility=Visibility.PRIVATE.value,
        federation_object_id="pub-2",
    )
    db_session.add(track)
    await db_session.commit()

    response = fed_client.get(
        "/users/regular/objects/pub-2",
        headers={"Accept": ACTIVITY_JSON},
    )
    assert response.status_code == status.HTTP_404_NOT_FOUND


async def test_get_followers_returns_only_requested_actors_followers(fed_client, regular_user, monkeypatch):
    """GET /users/{username}/followers filters to that user's followers."""
    from unittest.mock import MagicMock

    regular_actor_url = "https://music.example.com/users/regular"
    remote_a = MagicMock(actor_id="https://a.example/users/bob", actor_data={"followed_actor": regular_actor_url})
    remote_b = MagicMock(actor_id="https://b.example/users/carol", actor_data={"followed_actor": regular_actor_url})

    storage = MagicMock()
    storage.get_followers.return_value = [remote_a, remote_b]
    monkeypatch.setattr("songhive.api.routes.federation.get_federation_storage", lambda *_: storage)

    response = fed_client.get("/users/regular/followers", headers={"Accept": ACTIVITY_JSON})
    assert response.status_code == status.HTTP_200_OK

    data = response.json()
    assert data["type"] == "OrderedCollection"
    assert data["id"] == f"{regular_actor_url}/followers"
    assert data["orderedItems"] == ["https://a.example/users/bob", "https://b.example/users/carol"]
    storage.get_followers.assert_called_once_with(actor_id=regular_actor_url)


async def test_get_object_returns_404_when_audio_object_is_none(fed_client, db_session, regular_user, monkeypatch):
    """GET /users/{username}/objects/{object_id} returns 404 when serialization fails."""
    monkeypatch.setattr("songhive.api.routes.federation.track_to_audio_object", lambda *args, **kwargs: None)

    artist = Artist(name="Artist")
    db_session.add(artist)
    await db_session.flush()

    track = Track(
        title="Public Track",
        artist_id=str(artist.id),
        owner_id=str(regular_user.id),
        visibility=Visibility.PUBLIC.value,
        federation_object_id="pub-none",
    )
    db_session.add(track)
    await db_session.commit()

    response = fed_client.get(
        "/users/regular/objects/pub-none",
        headers={"Accept": ACTIVITY_JSON},
    )
    assert response.status_code == status.HTTP_404_NOT_FOUND


def _make_activity(owner, object_id: str = "act-1", **overrides) -> Activity:
    """Build a local activity owned by ``owner`` with a valid object identity."""
    actor_url = f"https://music.example.com/users/{owner.username}"
    params = {
        "entity_type": "track",
        "entity_id": "track-1",
        "activity_type": "create",
        "source_type": "local",
        "source_actor": actor_url,
        "source_id": f"{actor_url}/objects/{object_id}",
        "local_object_id": object_id,
        "owner_user_id": str(owner.id),
        "visibility": Visibility.PUBLIC.value,
    }
    params.update(overrides)
    return Activity(**params)


async def test_get_object_returns_stored_activity_payload(fed_client, db_session, regular_user):
    """GET /users/{username}/objects/{object_id} serves a stored AP payload as-is."""
    actor_url = "https://music.example.com/users/regular"
    payload = {
        "@context": "https://www.w3.org/ns/activitystreams",
        "id": f"{actor_url}/objects/like-1",
        "type": "Like",
        "actor": actor_url,
        "object": "https://remote.example/objects/9",
        "to": ["https://www.w3.org/ns/activitystreams#Public"],
        "cc": [f"{actor_url}/followers"],
    }
    db_session.add(_make_activity(regular_user, object_id="like-1", activity_type="like", payload=payload))
    await db_session.commit()

    response = fed_client.get("/users/regular/objects/like-1", headers={"Accept": ACTIVITY_JSON})
    assert response.status_code == status.HTTP_200_OK
    assert ACTIVITY_JSON in response.headers["content-type"]

    data = response.json()
    assert data == payload


async def test_get_object_serves_create_payload_object(fed_client, db_session, regular_user):
    """A stored ``Create`` payload is dereferenced as its embedded object.

    The activity's ``source_id`` identifies the shared/published object (not
    the ``Create`` envelope), so the route must serve the object document —
    e.g. the ``Note`` of a track share — for remote fetches to resolve it.
    """
    actor_url = "https://music.example.com/users/regular"
    note = {
        "id": f"{actor_url}/objects/share-1",
        "type": "Note",
        "name": "TestArtist - My Song",
        "content": "<p>sharing</p>",
        # The share's ``url`` is its own object id — the track page belongs
        # to the canonical ``Audio`` object.
        "url": f"{actor_url}/objects/share-1",
        "to": ["https://www.w3.org/ns/activitystreams#Public"],
        "cc": [f"{actor_url}/followers"],
    }
    payload = {
        "@context": "https://www.w3.org/ns/activitystreams",
        "id": f"{actor_url}/activities/create-1",
        "type": "Create",
        "actor": actor_url,
        "object": note,
        "to": note["to"],
        "cc": note["cc"],
    }
    db_session.add(_make_activity(regular_user, object_id="share-1", payload=payload))
    await db_session.commit()

    response = fed_client.get("/users/regular/objects/share-1", headers={"Accept": ACTIVITY_JSON})
    assert response.status_code == status.HTTP_200_OK

    data = response.json()
    assert data["@context"] == "https://www.w3.org/ns/activitystreams"
    assert data["type"] == "Note"
    assert data["id"] == f"{actor_url}/objects/share-1"
    # The envelope is not re-served as the object document.
    assert "object" not in data


async def test_get_object_redirects_browsers_to_track_page(fed_client, db_session, regular_user):
    """A browser opening a track-resolved object URL lands on the track page."""
    artist = Artist(name="Artist")
    db_session.add(artist)
    await db_session.flush()

    track = Track(
        title="Public Track",
        artist_id=str(artist.id),
        owner_id=str(regular_user.id),
        visibility=Visibility.PUBLIC.value,
        federation_object_id="pub-1",
    )
    db_session.add(track)
    await db_session.commit()

    response = fed_client.get("/users/regular/objects/pub-1", follow_redirects=False)
    assert response.status_code == status.HTTP_307_TEMPORARY_REDIRECT
    assert response.headers["location"] == f"/tracks/{track.id}"


async def test_get_object_redirects_browsers_to_entity_feed(fed_client, db_session, regular_user):
    """A browser opening a share's object URL lands on the entity's feed."""
    db_session.add(_make_activity(regular_user, object_id="share-1"))
    await db_session.commit()

    response = fed_client.get("/users/regular/objects/share-1", follow_redirects=False)
    assert response.status_code == status.HTTP_307_TEMPORARY_REDIRECT
    assert response.headers["location"] == "/tracks/track-1/activities"


async def test_get_object_synthesizes_note_for_content_activity(fed_client, db_session, regular_user):
    """A payload-less local activity is served as a Note object."""
    actor_url = "https://music.example.com/users/regular"
    parent = _make_activity(regular_user, object_id="parent-1")
    db_session.add(parent)
    await db_session.flush()

    activity = _make_activity(
        regular_user,
        object_id="note-1",
        activity_type="reply",
        content='<p>hi <a href="https://remote.example/users/bob">@bob@remote.example</a></p>',
        in_reply_to_activity_id=str(parent.id),
    )
    db_session.add(activity)
    await db_session.flush()
    db_session.add(
        ActivityMention(
            activity_id=str(activity.id),
            handle="@bob@remote.example",
            actor_url="https://remote.example/users/bob",
        )
    )
    await db_session.commit()

    response = fed_client.get("/users/regular/objects/note-1", headers={"Accept": ACTIVITY_JSON})
    assert response.status_code == status.HTTP_200_OK

    data = response.json()
    assert data["type"] == "Note"
    assert data["id"] == f"{actor_url}/objects/note-1"
    assert data["attributedTo"] == actor_url
    assert data["content"].startswith("<p>hi")
    assert data["published"]
    assert data["to"] == ["https://www.w3.org/ns/activitystreams#Public"]
    assert data["cc"] == [f"{actor_url}/followers"]
    assert data["inReplyTo"] == f"{actor_url}/objects/parent-1"
    assert data["tag"] == [
        {"type": "Mention", "href": "https://remote.example/users/bob", "name": "@bob@remote.example"}
    ]


async def test_get_object_returns_tombstone_for_deleted_activity(fed_client, db_session, regular_user):
    """A soft-deleted activity is served as a Tombstone object."""
    activity = _make_activity(
        regular_user,
        object_id="gone-1",
        deleted_at=datetime.now(timezone.utc),
    )
    db_session.add(activity)
    await db_session.commit()

    response = fed_client.get("/users/regular/objects/gone-1", headers={"Accept": ACTIVITY_JSON})
    assert response.status_code == status.HTTP_200_OK

    data = response.json()
    assert data["type"] == "Tombstone"
    assert data["id"] == "https://music.example.com/users/regular/objects/gone-1"
    assert data["@context"] == "https://www.w3.org/ns/activitystreams"


async def test_get_object_serves_followers_activity(fed_client, db_session, regular_user):
    """Federating non-public visibilities (e.g. followers) are dereferenceable."""
    db_session.add(_make_activity(regular_user, object_id="fol-1", visibility=Visibility.FOLLOWERS.value))
    await db_session.commit()

    response = fed_client.get("/users/regular/objects/fol-1", headers={"Accept": ACTIVITY_JSON})
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["type"] == "Note"
    assert data["to"] == ["https://music.example.com/users/regular/followers"]


@pytest.mark.parametrize("visibility", [Visibility.PRIVATE.value, Visibility.LOCAL.value])
async def test_get_object_returns_404_for_non_federated_activity(fed_client, db_session, regular_user, visibility):
    """private/local activities never left the instance and answer 404."""
    db_session.add(_make_activity(regular_user, object_id="hidden-1", visibility=visibility))
    await db_session.commit()

    response = fed_client.get("/users/regular/objects/hidden-1", headers={"Accept": ACTIVITY_JSON})
    assert response.status_code == status.HTTP_404_NOT_FOUND


def test_served_tombstone_matches_delete_payload_object():
    """The served Tombstone object matches the one embedded in Delete deliveries."""
    from songhive.federation.activities import build_tombstone_object, create_tombstone_delete_activity

    delete = create_tombstone_delete_activity("https://x.example/users/a", "https://x.example/objects/1")
    assert delete["type"] == "Delete"
    assert delete["object"] == build_tombstone_object("https://x.example/objects/1")


async def test_get_object_returns_404_for_other_users_activity(fed_client, db_session, regular_user, other_user):
    """An activity is only served under its owner's object namespace."""
    db_session.add(_make_activity(other_user, object_id="act-other"))
    await db_session.commit()

    response = fed_client.get("/users/regular/objects/act-other", headers={"Accept": ACTIVITY_JSON})
    assert response.status_code == status.HTTP_404_NOT_FOUND


async def test_get_object_returns_404_for_unknown_object(fed_client, regular_user):
    """An object id matching neither a track nor an activity returns 404."""
    response = fed_client.get("/users/regular/objects/nope", headers={"Accept": ACTIVITY_JSON})
    assert response.status_code == status.HTTP_404_NOT_FOUND


async def test_track_page_serves_audio_object_for_activitypub_accept(fed_client, db_session, regular_user):
    """GET /tracks/{id} dereferences to the Audio object for AP clients."""
    artist = Artist(name="Artist")
    db_session.add(artist)
    await db_session.flush()

    track = Track(
        title="Public Track",
        artist_id=str(artist.id),
        owner_id=str(regular_user.id),
        visibility=Visibility.PUBLIC.value,
        federation_object_id="pub-page",
    )
    db_session.add(track)
    await db_session.commit()

    response = fed_client.get(f"/tracks/{track.id}", headers={"Accept": ACTIVITY_JSON})
    assert response.status_code == status.HTTP_200_OK
    assert ACTIVITY_JSON in response.headers["content-type"]

    actor_url = "https://music.example.com/users/regular"
    data = response.json()
    assert data["@context"] == "https://www.w3.org/ns/activitystreams"
    assert data["type"] == "Audio"
    assert data["id"] == f"{actor_url}/objects/pub-page"
    assert data["to"] == ["https://www.w3.org/ns/activitystreams#Public"]
    assert data["cc"] == [f"{actor_url}/followers"]
    assert data["attributedTo"][0] == actor_url
    assert any(
        link["mediaType"] == "text/html" and link["href"] == f"https://music.example.com/tracks/{track.id}"
        for link in data["url"]
    )


async def test_track_page_serves_audio_object_for_mastodon_accept(fed_client, db_session, regular_user):
    """The combined ld+json/activity+json/html Accept sent by Mastodon gets the object."""
    artist = Artist(name="Artist")
    db_session.add(artist)
    await db_session.flush()

    track = Track(
        title="Public Track",
        artist_id=str(artist.id),
        owner_id=str(regular_user.id),
        visibility=Visibility.PUBLIC.value,
        federation_object_id="pub-page",
    )
    db_session.add(track)
    await db_session.commit()

    response = fed_client.get(
        f"/tracks/{track.id}",
        headers={
            "Accept": 'application/ld+json; profile="https://www.w3.org/ns/activitystreams", '
            "application/activity+json, text/html;q=0.1"
        },
    )
    assert response.status_code == status.HTTP_200_OK
    assert ACTIVITY_JSON in response.headers["content-type"]
    assert response.json()["type"] == "Audio"


async def test_track_page_serves_spa_with_discovery_hints_for_browsers(
    fed_client, db_session, regular_user, tmp_path, monkeypatch
):
    """GET /tracks/{id} serves the SPA shell + rel=alternate hints for HTML clients."""
    artist = Artist(name="Artist")
    db_session.add(artist)
    await db_session.flush()

    track = Track(
        title="Public Track",
        artist_id=str(artist.id),
        owner_id=str(regular_user.id),
        visibility=Visibility.PUBLIC.value,
        federation_object_id="pub-page",
    )
    db_session.add(track)
    await db_session.commit()

    index = tmp_path / "index.html"
    index.write_text("<html><head><title>songhive</title></head><body></body></html>", encoding="utf-8")
    monkeypatch.setattr("songhive.api.routes.federation._spa_index_path", lambda: index)

    response = fed_client.get(f"/tracks/{track.id}", headers={"Accept": "text/html"})
    assert response.status_code == status.HTTP_200_OK
    assert "text/html" in response.headers["content-type"]

    object_url = "https://music.example.com/users/regular/objects/pub-page"
    assert response.headers["Link"] == f'<{object_url}>; rel="alternate"; type="{ACTIVITY_JSON}"'
    assert f'<link rel="alternate" type="{ACTIVITY_JSON}" href="{object_url}"></head>' in response.text


async def test_track_page_serves_plain_spa_for_unpublished_track(
    fed_client, db_session, regular_user, tmp_path, monkeypatch
):
    """HTML requests for unpublished tracks get the SPA shell without AP hints."""
    artist = Artist(name="Artist")
    db_session.add(artist)
    await db_session.flush()

    track = Track(
        title="Public Track",
        artist_id=str(artist.id),
        owner_id=str(regular_user.id),
        visibility=Visibility.PUBLIC.value,
        federation_object_id=None,
    )
    db_session.add(track)
    await db_session.commit()

    index = tmp_path / "index.html"
    index.write_text("<html><head></head><body></body></html>", encoding="utf-8")
    monkeypatch.setattr("songhive.api.routes.federation._spa_index_path", lambda: index)

    response = fed_client.get(f"/tracks/{track.id}", headers={"Accept": "text/html"})
    assert response.status_code == status.HTTP_200_OK
    assert "Link" not in response.headers
    assert 'rel="alternate"' not in response.text


async def test_track_page_returns_404_for_unpublished_track(fed_client, db_session, regular_user):
    """AP requests for tracks without a federation_object_id return 404."""
    artist = Artist(name="Artist")
    db_session.add(artist)
    await db_session.flush()

    track = Track(
        title="Public Track",
        artist_id=str(artist.id),
        owner_id=str(regular_user.id),
        visibility=Visibility.PUBLIC.value,
        federation_object_id=None,
    )
    db_session.add(track)
    await db_session.commit()

    response = fed_client.get(f"/tracks/{track.id}", headers={"Accept": ACTIVITY_JSON})
    assert response.status_code == status.HTTP_404_NOT_FOUND


async def test_track_page_returns_404_for_private_track(fed_client, db_session, regular_user):
    """AP requests for non-public tracks return 404."""
    artist = Artist(name="Artist")
    db_session.add(artist)
    await db_session.flush()

    track = Track(
        title="Private Track",
        artist_id=str(artist.id),
        owner_id=str(regular_user.id),
        visibility=Visibility.PRIVATE.value,
        federation_object_id="pub-priv",
    )
    db_session.add(track)
    await db_session.commit()

    response = fed_client.get(f"/tracks/{track.id}", headers={"Accept": ACTIVITY_JSON})
    assert response.status_code == status.HTTP_404_NOT_FOUND


async def test_track_page_returns_404_for_unknown_track(fed_client):
    """AP requests for unknown track ids return 404."""
    response = fed_client.get("/tracks/nope", headers={"Accept": ACTIVITY_JSON})
    assert response.status_code == status.HTTP_404_NOT_FOUND


async def test_track_page_returns_404_for_inactive_owner(fed_client, db_session, regular_user):
    """AP requests return 404 when the track owner is deactivated."""
    artist = Artist(name="Artist")
    db_session.add(artist)
    await db_session.flush()

    track = Track(
        title="Public Track",
        artist_id=str(artist.id),
        owner_id=str(regular_user.id),
        visibility=Visibility.PUBLIC.value,
        federation_object_id="pub-inactive",
    )
    db_session.add(track)
    regular_user.is_active = False
    await db_session.commit()

    response = fed_client.get(f"/tracks/{track.id}", headers={"Accept": ACTIVITY_JSON})
    assert response.status_code == status.HTTP_404_NOT_FOUND


async def test_inbox_rejects_invalid_json(fed_client, regular_user, monkeypatch):
    """POST /users/{username}/inbox rejects non-JSON bodies."""
    mock_task = _MockCeleryTask()
    monkeypatch.setattr("songhive.api.routes.federation.process_incoming", mock_task)

    response = fed_client.post(
        "/users/regular/inbox",
        data="not-json",
        headers={"Content-Type": "text/plain"},
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert len(mock_task.calls) == 0


async def test_inbox_accepts_actor_as_list(fed_client, regular_user, monkeypatch):
    """POST /users/{username}/inbox extracts the first actor from a list."""
    mock_task = _MockCeleryTask()
    monkeypatch.setattr("songhive.api.routes.federation.process_incoming", mock_task)

    activity = {"type": "Follow", "actor": ["https://good.example/users/bob"]}
    response = fed_client.post("/users/regular/inbox", json=activity)
    assert response.status_code == status.HTTP_202_ACCEPTED
    assert len(mock_task.calls) == 1


async def test_inbox_accepts_actor_as_dict(fed_client, regular_user, monkeypatch):
    """POST /users/{username}/inbox extracts the actor id from a dict."""
    mock_task = _MockCeleryTask()
    monkeypatch.setattr("songhive.api.routes.federation.process_incoming", mock_task)

    activity = {"type": "Follow", "actor": {"id": "https://good.example/users/bob"}}
    response = fed_client.post("/users/regular/inbox", json=activity)
    assert response.status_code == status.HTTP_202_ACCEPTED
    assert len(mock_task.calls) == 1


async def test_get_outbox_returns_ordered_collection(fed_client, regular_user):
    """GET /users/{username}/outbox returns an empty OrderedCollection."""
    response = fed_client.get("/users/regular/outbox", headers={"Accept": ACTIVITY_JSON})
    assert response.status_code == status.HTTP_200_OK

    data = response.json()
    assert data["type"] == "OrderedCollection"
    assert data["totalItems"] == 0
    assert data["orderedItems"] == []


async def test_get_following_returns_ordered_collection(fed_client, regular_user):
    """GET /users/{username}/following returns an empty OrderedCollection."""
    response = fed_client.get("/users/regular/following", headers={"Accept": ACTIVITY_JSON})
    assert response.status_code == status.HTTP_200_OK

    data = response.json()
    assert data["type"] == "OrderedCollection"
    assert data["totalItems"] == 0
    assert data["orderedItems"] == []


async def test_webfinger_requires_resource(fed_client):
    """WebFinger returns 400 when the resource parameter is missing."""
    response = fed_client.get("/.well-known/webfinger")
    assert response.status_code == status.HTTP_400_BAD_REQUEST


async def test_webfinger_rejects_non_acct_resource(fed_client):
    """WebFinger returns 404 for resources that do not start with acct:."""
    response = fed_client.get("/.well-known/webfinger?resource=https://example.com/user")
    assert response.status_code == status.HTTP_404_NOT_FOUND


async def test_webfinger_rejects_missing_at_symbol(fed_client):
    """WebFinger returns 404 when the resource has no @ separator."""
    response = fed_client.get("/.well-known/webfinger?resource=acct:regular")
    assert response.status_code == status.HTTP_404_NOT_FOUND


async def test_webfinger_accepts_leading_at_symbol(fed_client, regular_user):
    """WebFinger strips a leading @ from the username in the resource."""
    response = fed_client.get("/.well-known/webfinger?resource=acct:@regular@music.example.com")
    assert response.status_code == status.HTTP_200_OK
    assert response.json()["subject"] == "acct:regular@music.example.com"
