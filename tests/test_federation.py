"""
Federation module tests.
"""

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest
from celery.exceptions import Retry

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
from songhive.tasks.federation import _load_user_actor, deliver_activity, process_incoming


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
    assert obj["tag"] == [{"type": "Hashtag", "name": "#rock"}]
    assert any(
        link["href"] == "https://music.example.com/api/v1/files/file-1/download" and link["mediaType"] == "audio/mpeg"
        for link in obj["url"]
    )


def test_track_to_audio_object_emits_multiple_genre_hashtags():
    """Multi-genre tracks emit one ActivityPub Hashtag tag per genre."""
    artist = Artist(name="TestArtist")
    artist.id = "artist-1"
    track = Track(
        title="TestTrack",
        artist_id="artist-1",
        audio_file_id="file-1",
        duration=120.0,
        genre="Rock; Pop",
        visibility=Visibility.PUBLIC.value,
    )
    track.id = "track-1"

    obj = track_to_audio_object(track, artist, "music.example.com")
    assert obj is not None
    assert obj["tag"] == [
        {"type": "Hashtag", "name": "#rock"},
        {"type": "Hashtag", "name": "#pop"},
    ]


def test_track_to_audio_object_converts_spaces_to_underscores():
    """Genre names with spaces are emitted as underscore-separated hashtags."""
    artist = Artist(name="TestArtist")
    artist.id = "artist-1"
    track = Track(
        title="TestTrack",
        artist_id="artist-1",
        audio_file_id="file-1",
        duration=120.0,
        genre="Hip Hop",
        visibility=Visibility.PUBLIC.value,
    )
    track.id = "track-1"

    obj = track_to_audio_object(track, artist, "music.example.com")
    assert obj is not None
    assert obj["tag"] == [{"type": "Hashtag", "name": "#hip_hop"}]


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


def _fed_config(**kwargs) -> SonghiveConfig:
    """Return a federation-enabled test config."""
    return SonghiveConfig(
        auth={"secret_key": "a" * 64},
        database={"url": "sqlite+aiosqlite:///:memory:"},
        storage={"backend": "local", "local_path": "/tmp/media"},
        federation={
            "enabled": kwargs.get("enabled", True),
            "instance_domain": "music.example.com",
            "blocked_instances": kwargs.get("blocked_instances", []),
            "allowed_instances": kwargs.get("allowed_instances", []),
        },
    )


@asynccontextmanager
async def _fake_session_cm(session=None):
    """Yield a fixed session for federation task tests."""
    yield session or MagicMock()


def test_load_user_actor_not_found(monkeypatch):
    """_load_user_actor returns None when the user does not exist."""
    monkeypatch.setattr("songhive.tasks.federation.load_config", lambda *a, **k: _fed_config())
    monkeypatch.setattr("songhive.tasks.federation.get_session", _fake_session_cm)
    monkeypatch.setattr("songhive.services.auth.get_user_by_username", AsyncMock(return_value=None))

    assert _load_user_actor("missing") is None


def test_load_user_actor_found(monkeypatch):
    """_load_user_actor provisions and returns the matching user."""
    user = MagicMock()
    monkeypatch.setattr("songhive.tasks.federation.load_config", lambda *a, **k: _fed_config())
    monkeypatch.setattr("songhive.tasks.federation.get_session", _fake_session_cm)
    monkeypatch.setattr("songhive.services.auth.get_user_by_username", AsyncMock(return_value=user))
    monkeypatch.setattr("songhive.tasks.federation.ensure_user_actor", MagicMock())

    assert _load_user_actor("alice") is user


def _patch_process_incoming(monkeypatch, config=None):
    """Apply common monkeypatches for process_incoming tests."""
    monkeypatch.setattr("songhive.tasks.federation.load_config", lambda *a, **k: config or _fed_config())
    monkeypatch.setattr(
        "songhive.federation.storage.get_or_create_private_key",
        lambda *a, **k: MagicMock(read_text=lambda *a, **k: "pem"),
    )
    monkeypatch.setattr("songhive.federation.actors.get_federation_storage", lambda *a, **k: MagicMock())
    monkeypatch.setattr("songhive.tasks.federation.load_private_key", lambda *a, **k: "private_key")
    monkeypatch.setattr("songhive.tasks.federation.init_db", lambda *a, **k: None)


def test_process_incoming_disabled(monkeypatch):
    """process_incoming is a no-op when federation is disabled."""
    monkeypatch.setattr("songhive.tasks.federation.load_config", lambda *a, **k: _fed_config(enabled=False))
    assert process_incoming({"actor": "https://example.com/user"}) is None


def test_process_incoming_no_actor(monkeypatch):
    """process_incoming drops activities without a usable actor."""
    _patch_process_incoming(monkeypatch)
    assert process_incoming({}) is None


def test_process_incoming_blocked_domain(monkeypatch):
    """process_incoming drops activities from blocked domains."""
    _patch_process_incoming(monkeypatch, _fed_config(blocked_instances=["example.com"]))
    assert process_incoming({"actor": "https://example.com/user"}) is None


def test_process_incoming_user_actor_missing(monkeypatch):
    """process_incoming drops activities for unknown local users."""
    _patch_process_incoming(monkeypatch)
    monkeypatch.setattr("songhive.tasks.federation._load_user_actor", lambda username: None)
    assert process_incoming({"actor": "https://example.com/user"}, username="missing") is None


def test_process_incoming_user_actor_no_context(monkeypatch):
    """process_incoming drops activities when the local user has no actor context."""
    _patch_process_incoming(monkeypatch)
    user = MagicMock(actor_url="https://music.example.com/users/alice", private_key_pem=None)
    monkeypatch.setattr("songhive.tasks.federation._load_user_actor", lambda username: user)
    assert process_incoming({"actor": "https://example.com/user"}, username="alice") is None


def test_process_incoming_base64_decode_error(monkeypatch):
    """process_incoming continues when body_b64 cannot be decoded."""
    _patch_process_incoming(monkeypatch)
    processor = MagicMock(process=MagicMock(return_value={"ok": True}))
    monkeypatch.setattr("songhive.tasks.federation.InboxProcessor", MagicMock(return_value=processor))

    result = process_incoming(
        {"actor": "https://example.com/user", "type": "Create"},
        body_b64="not-valid-base64!!!",
    )
    assert result == {"ok": True}
    assert processor.process.call_args.kwargs["body"] is None


def test_process_incoming_signature_verification_error(monkeypatch):
    """process_incoming drops activities with bad signatures."""
    _patch_process_incoming(monkeypatch)
    from songhive.tasks.federation import SignatureVerificationError

    processor = MagicMock(process=MagicMock(side_effect=SignatureVerificationError("bad signature")))
    monkeypatch.setattr("songhive.tasks.federation.InboxProcessor", MagicMock(return_value=processor))

    assert process_incoming({"actor": "https://example.com/user", "type": "Create"}) is None


def test_process_incoming_activitypub_error(monkeypatch):
    """process_incoming drops activities that cannot be processed."""
    _patch_process_incoming(monkeypatch)
    from songhive.tasks.federation import ActivityPubError

    processor = MagicMock(process=MagicMock(side_effect=ActivityPubError("bad activity")))
    monkeypatch.setattr("songhive.tasks.federation.InboxProcessor", MagicMock(return_value=processor))

    assert process_incoming({"actor": "https://example.com/user", "type": "Create"}) is None


def _deliver_self(retries: int = 0, retry_side_effect=None):
    self = MagicMock()
    self.request.retries = retries
    self.retry.side_effect = retry_side_effect or Retry()
    return self


def test_deliver_activity_disabled(monkeypatch):
    """deliver_activity is a no-op when federation is disabled."""
    monkeypatch.setattr("songhive.tasks.federation.load_config", lambda *a, **k: _fed_config(enabled=False))
    self = _deliver_self()
    assert deliver_activity.run.__func__(self, {"type": "Create"}, "https://example.com/inbox", "key", "pem") is None
    self.retry.assert_not_called()


def test_deliver_activity_blocked_domain(monkeypatch):
    """deliver_activity drops deliveries to blocked domains."""
    monkeypatch.setattr(
        "songhive.tasks.federation.load_config",
        lambda *a, **k: _fed_config(blocked_instances=["example.com"]),
    )
    self = _deliver_self()
    assert deliver_activity.run.__func__(self, {"type": "Create"}, "https://example.com/inbox", "key", "pem") is None
    self.retry.assert_not_called()


def test_deliver_activity_success(monkeypatch):
    """deliver_activity posts signed requests and returns the response."""
    monkeypatch.setattr("songhive.tasks.federation.load_config", lambda *a, **k: _fed_config())
    monkeypatch.setattr("songhive.tasks.federation.load_private_key", lambda pem: "private_key")
    monkeypatch.setattr("songhive.tasks.federation.sign_request", MagicMock(return_value={"Signature": "sig"}))

    response = MagicMock(status_code=200)
    monkeypatch.setattr("songhive.tasks.federation.requests.post", MagicMock(return_value=response))

    self = _deliver_self()
    result = deliver_activity.run.__func__(
        self,
        {"type": "Create"},
        "https://example.com/inbox",
        "https://music.example.com/actor#main-key",
        "pem",
    )
    assert result is response


def test_deliver_activity_request_exception_retries(monkeypatch):
    """deliver_activity retries on request exceptions."""
    from requests import RequestException

    monkeypatch.setattr("songhive.tasks.federation.load_config", lambda *a, **k: _fed_config())
    monkeypatch.setattr("songhive.tasks.federation.load_private_key", lambda pem: "private_key")
    monkeypatch.setattr("songhive.tasks.federation.sign_request", MagicMock(return_value={}))

    exc = RequestException("network down")
    monkeypatch.setattr("songhive.tasks.federation.requests.post", MagicMock(side_effect=exc))

    self = _deliver_self(retries=1)
    with pytest.raises(Retry):
        deliver_activity.run.__func__(self, {"type": "Create"}, "https://example.com/inbox", "key", "pem")

    self.retry.assert_called_once()


def test_deliver_activity_5xx_retries(monkeypatch):
    """deliver_activity retries on 5xx responses."""
    monkeypatch.setattr("songhive.tasks.federation.load_config", lambda *a, **k: _fed_config())
    monkeypatch.setattr("songhive.tasks.federation.load_private_key", lambda pem: "private_key")
    monkeypatch.setattr("songhive.tasks.federation.sign_request", MagicMock(return_value={}))

    response = MagicMock(status_code=503)
    monkeypatch.setattr("songhive.tasks.federation.requests.post", MagicMock(return_value=response))

    self = _deliver_self(retries=0)
    with pytest.raises(Retry):
        deliver_activity.run.__func__(self, {"type": "Create"}, "https://example.com/inbox", "key", "pem")

    self.retry.assert_called_once()


def test_deliver_activity_4xx_gives_up(monkeypatch):
    """deliver_activity gives up on non-retryable 4xx responses."""
    monkeypatch.setattr("songhive.tasks.federation.load_config", lambda *a, **k: _fed_config())
    monkeypatch.setattr("songhive.tasks.federation.load_private_key", lambda pem: "private_key")
    monkeypatch.setattr("songhive.tasks.federation.sign_request", MagicMock(return_value={}))

    response = MagicMock(status_code=400)
    monkeypatch.setattr("songhive.tasks.federation.requests.post", MagicMock(return_value=response))

    self = _deliver_self()
    assert deliver_activity.run.__func__(self, {"type": "Create"}, "https://example.com/inbox", "key", "pem") is None
    self.retry.assert_not_called()
