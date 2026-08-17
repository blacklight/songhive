"""
Federation module tests.
"""

from songhive.federation.activities import create_audio_activity
from songhive.federation.actors import (
    get_actor_url,
    get_inbox_url,
    user_to_actor_document,
)
from songhive.federation.serializers import track_to_audio_object
from songhive.models.album import Album  # noqa: F401
from songhive.models.artist import Artist
from songhive.models.track import Track
from songhive.models.user import User


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
    """Test creating a Create(Audio) activity."""
    activity = create_audio_activity(
        actor_url="https://music.example.com/users/alice",
        track_url="https://music.example.com/tracks/123",
        stream_url="https://music.example.com/api/v1/stream/123",
        title="My Song",
        description="A great track",
        duration=195.5,
    )
    assert activity["type"] == "Create"
    assert activity["object"]["type"] == "Audio"
    assert activity["object"]["name"] == "My Song"
    assert "PT3M15S" in activity["object"]["duration"]


def test_track_to_audio_object():
    """Test serializing a Track to an AP Audio object."""
    artist = Artist(name="TestArtist")
    artist.id = "artist-1"
    track = Track(title="TestTrack", artist_id="artist-1", duration=120.0, genre="Rock")
    track.id = "track-1"

    obj = track_to_audio_object(
        track,
        artist,
        "music.example.com",
        "https://music.example.com/api/v1/stream/track-1",
    )
    assert obj["type"] == "Audio"
    assert obj["name"] == "TestTrack"
    assert obj["duration"] == "PT2M0S"
    assert obj["tag"][0]["name"] == "#Rock"


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
