"""
Federation route tests.
"""

from pathlib import Path

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from songhive.api.app import create_app
from songhive.api.deps import get_db
from songhive.models._enums import Visibility
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
    assert data["id"] == "https://music.example.com/users/regular/objects/pub-1"
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
