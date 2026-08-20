"""
Federation module tests.
"""

import asyncio

import pytest

from songhive.config.schema import SonghiveConfig
from songhive.federation.activities import create_audio_activity
from songhive.federation.actors import (
    get_actor_url,
    get_federation_storage,
    get_inbox_url,
    sync_user_actor,
    user_to_actor_document,
)
from songhive.federation.serializers import track_to_audio_object
from songhive.models import Visibility
from songhive.models.album import Album  # noqa: F401
from songhive.models.artist import Artist
from songhive.models.track import Track
from songhive.models.user import User
from songhive.models.user_link import UserLink


def test_get_actor_url():
    """Test actor URL generation."""
    assert get_actor_url("music.example.com", "alice") == "https://music.example.com/users/alice"


def test_get_inbox_url():
    """Test inbox URL generation."""
    assert get_inbox_url("music.example.com", "alice") == "https://music.example.com/users/alice/inbox"


def test_user_to_actor_document():
    """Test converting a User to an AP actor document."""
    user = User(
        username="alice",
        email="alice@example.com",
        password_hash="x",
        display_name="Alice",
        public_key_pem="-----BEGIN PUBLIC KEY-----\ntest\n-----END PUBLIC KEY-----",
    )
    doc = user_to_actor_document(user, "music.example.com")
    assert doc["type"] == "Person"
    assert doc["preferredUsername"] == "alice"
    assert doc["name"] == "Alice"
    assert "inbox" in doc
    assert "publicKey" in doc


def test_create_audio_activity():
    """Test creating a Create(Audio) activity for a public track."""
    artist = Artist(name="TestArtist")
    artist.id = "artist-1"
    track = Track(
        title="My Song",
        artist_id="artist-1",
        audio_file_id="file-1",
        duration=195.5,
        visibility=Visibility.PUBLIC.value,
    )
    track.id = "track-123"

    activity = create_audio_activity(
        actor_url="https://music.example.com/users/alice",
        track=track,
        artist=artist,
        domain="music.example.com",
        description="A great track",
    )
    assert activity is not None
    assert activity["type"] == "Create"
    assert activity["object"]["type"] == "Audio"
    assert activity["object"]["name"] == "My Song"
    assert activity["object"]["content"] == "A great track"
    assert "PT3M15S" in activity["object"]["duration"]
    assert any(
        link["href"] == "https://music.example.com/api/v1/files/file-1/download" and link["mediaType"] == "audio/mpeg"
        for link in activity["object"]["url"]
    )


def test_track_to_audio_object():
    """Test serializing a public Track to an AP Audio object."""
    artist = Artist(name="TestArtist")
    artist.id = "artist-1"
    track = Track(
        title="TestTrack",
        artist_id="artist-1",
        audio_file_id="file-1",
        duration=120.0,
        genre="Rock",
        visibility=Visibility.PUBLIC.value,
    )
    track.id = "track-1"

    obj = track_to_audio_object(track, artist, "music.example.com")
    assert obj is not None
    assert obj["type"] == "Audio"
    assert obj["name"] == "TestTrack"
    assert obj["duration"] == "PT2M0S"
    assert obj["tag"][0]["name"] == "#Rock"
    assert any(
        link["href"] == "https://music.example.com/api/v1/files/file-1/download" and link["mediaType"] == "audio/mpeg"
        for link in obj["url"]
    )


def test_federation_app_setup(tmp_path):
    """Test that the FastAPI app can set up ActivityPub federation."""
    from fastapi.testclient import TestClient

    from songhive.api.app import create_app
    from songhive.config.schema import SonghiveConfig

    key_path = tmp_path / "actor.pem"
    config = SonghiveConfig(
        database={"url": f"sqlite+aiosqlite:///{tmp_path / 'songhive.db'}"},
        federation={
            "enabled": True,
            "instance_domain": "music.example.com",
            "instance_name": "Songhive",
            "instance_description": "A federated music instance",
            "private_key_path": key_path,
        },
    )

    app = create_app(config)

    with TestClient(app) as client:
        webfinger = client.get("/.well-known/webfinger?resource=acct:songhive@music.example.com")
        assert webfinger.status_code == 200
        assert webfinger.json()["subject"] == "acct:songhive@music.example.com"

        actor = client.get("/ap/actor")
        assert actor.status_code == 200
        data = actor.json()
        assert data["type"] == "Application"
        assert data["preferredUsername"] == "songhive"
        assert "publicKey" in data

        # A second create_app call should reuse the existing persisted key.
        assert key_path.exists()
        assert key_path.stat().st_size > 0


def test_user_to_actor_document_includes_avatar_and_links():
    """Test that the actor document exposes avatar and profile links."""
    user = User(
        username="alice",
        email="alice@example.com",
        password_hash="x",
        display_name="Alice",
        bio="Hello fediverse",
        avatar_url="https://example.com/avatar.png",
        links=[
            UserLink(name="Website", url="https://example.com"),
            UserLink(name="Mastodon", url="https://mastodon.example.com/@alice"),
        ],
    )
    doc = user_to_actor_document(user, "music.example.com")
    assert doc["url"] == "https://music.example.com/users/alice"
    assert doc["icon"] == {"type": "Image", "url": "https://example.com/avatar.png"}
    assert doc["attachment"] == [
        {"type": "PropertyValue", "name": "Website", "value": "https://example.com"},
        {"type": "PropertyValue", "name": "Mastodon", "value": "https://mastodon.example.com/@alice"},
    ]


def test_user_to_actor_document_omits_optional_fields():
    """Test that the actor document omits icon and attachment when not set."""
    user = User(
        username="alice",
        email="alice@example.com",
        password_hash="x",
    )
    doc = user_to_actor_document(user, "music.example.com")
    assert "icon" not in doc
    assert "attachment" not in doc


@pytest.mark.asyncio
async def test_sync_user_actor_skips_when_federation_disabled(db_session, config):
    """Test that sync is a no-op when federation is disabled."""
    user = User(username="alice", email="alice@example.com", password_hash="x")
    db_session.add(user)
    await db_session.flush()

    result = await sync_user_actor(user, config)
    assert result is False


@pytest.mark.asyncio
async def test_sync_user_actor_caches_document(db_session, config):
    """Test that sync stores the user's actor document in pubby storage."""
    user = User(
        username="alice",
        email="alice@example.com",
        password_hash="x",
        display_name="Alice",
        bio="Hello fediverse",
        avatar_url="https://example.com/avatar.png",
        links=[UserLink(name="Website", url="https://example.com")],
    )
    db_session.add(user)
    await db_session.commit()

    fed_config = SonghiveConfig(
        database={"url": config.database.url},
        federation={"enabled": True, "instance_domain": "music.example.com"},
        auth={"secret_key": config.auth.secret_key},
    )

    result = await sync_user_actor(user, fed_config)
    assert result is True

    storage = get_federation_storage(config.database.url)
    actor_url = get_actor_url("music.example.com", "alice")
    cached = await asyncio.to_thread(storage.get_cached_actor, actor_url)

    assert cached is not None
    assert cached["name"] == "Alice"
    assert cached["summary"] == "Hello fediverse"
    assert cached["icon"] == {"type": "Image", "url": "https://example.com/avatar.png"}
    assert cached["attachment"] == [
        {"type": "PropertyValue", "name": "Website", "value": "https://example.com"},
    ]
