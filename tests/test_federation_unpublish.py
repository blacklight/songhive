"""
Federation unpublish tests.

These tests cover Delete(Tombstone) activity creation, the service-level
unpublish helper, and the route transitions that trigger it.
"""

from unittest.mock import patch

import pytest
from pubby.crypto import export_private_key_pem, export_public_key_pem, generate_rsa_keypair

from songhive.config.schema import SonghiveConfig
from songhive.federation.activities import create_audio_activity, create_delete_activity
from songhive.models import Visibility
from songhive.models.artist import Artist
from songhive.models.track import Track
from songhive.models.user import User
from songhive.services.federation import publish_track_activity, unpublish_track_activity


@pytest.fixture
def fed_config(tmp_path):
    """Return a federation-enabled test configuration."""
    return SonghiveConfig(
        auth={"secret_key": "a" * 64},
        database={"url": f"sqlite+aiosqlite:///{tmp_path / 'songhive.db'}"},
        federation={
            "enabled": True,
            "instance_domain": "music.example.com",
            "private_key_path": tmp_path / "actor.pem",
        },
    )


def _make_user_with_keys(username: str = "alice") -> User:
    """Build a user with a provisioned actor URL and keypair."""
    private_key, public_key = generate_rsa_keypair()
    return User(
        username=username,
        email=f"{username}@example.com",
        password_hash="x",
        actor_url=f"https://music.example.com/users/{username}",
        private_key_pem=export_private_key_pem(private_key),
        public_key_pem=export_public_key_pem(public_key),
    )


def _make_public_track():
    """Build a public track with a loaded artist."""
    artist = Artist(name="TestArtist")
    artist.id = "artist-1"
    track = Track(
        title="TestTrack",
        artist_id="artist-1",
        audio_file_id="file-1",
        visibility=Visibility.PUBLIC.value,
    )
    track.id = "track-1"
    track.artist = artist
    return track, artist


def test_create_delete_activity_builds_tombstone():
    """A delete activity references the track by its ActivityPub URL."""
    track, _ = _make_public_track()
    actor_url = "https://music.example.com/users/alice"
    activity = create_delete_activity(actor_url, track, "music.example.com")

    assert activity is not None
    assert activity["type"] == "Delete"
    assert activity["actor"] == actor_url
    assert activity["to"] == ["https://www.w3.org/ns/activitystreams#Public"]
    assert activity["cc"] == [f"{actor_url}/followers"]
    assert activity["object"]["id"] == "https://music.example.com/tracks/track-1"
    assert activity["object"]["type"] == "Tombstone"


def test_create_delete_activity_returns_none_for_missing_track():
    """The helper no-ops when no track is provided."""
    assert create_delete_activity("https://music.example.com/users/alice", None, "music.example.com") is None


def test_unpublish_track_activity_noops_when_federation_disabled():
    """The service helper does nothing when federation is disabled."""
    track, artist = _make_public_track()
    user = _make_user_with_keys()
    config = SonghiveConfig(
        auth={"secret_key": "a" * 64},
        database={"url": "sqlite+aiosqlite:///:memory:"},
        federation={"enabled": False},
    )

    with patch("songhive.tasks.federation.deliver_activity") as mock_deliver:
        result = unpublish_track_activity(track, artist, user, config)

    assert result == 0
    assert not mock_deliver.delay.called


def test_unpublish_track_activity_enqueues_delete_to_followers(fed_config):
    """The helper builds a Delete activity and enqueues one delivery per inbox."""
    track, artist = _make_public_track()
    track.federation_object_id = "pub-1"
    user = _make_user_with_keys()

    with (
        patch(
            "songhive.services.federation.get_follower_inboxes",
            return_value=["https://a.example/inbox", "https://b.example/inbox"],
        ),
        patch("songhive.tasks.federation.deliver_activity") as mock_deliver,
    ):
        result = unpublish_track_activity(track, artist, user, fed_config)

    assert result == 2
    assert mock_deliver.delay.call_count == 2
    calls = [call.args for call in mock_deliver.delay.call_args_list]
    assert calls[0][1] == "https://a.example/inbox"
    assert calls[1][1] == "https://b.example/inbox"
    activity = calls[0][0]
    assert activity["type"] == "Delete"
    assert activity["actor"] == user.actor_url
    assert activity["object"]["id"] == f"{user.actor_url}/objects/pub-1"


def test_create_audio_activity_and_delete_have_same_object_url():
    """The Delete Tombstone points at the same URL the original Create published."""
    track, artist = _make_public_track()
    actor_url = "https://music.example.com/users/alice"

    create = create_audio_activity(actor_url, track, artist, "music.example.com")
    delete = create_delete_activity(actor_url, track, "music.example.com")

    assert create["object"]["id"] == delete["object"]["id"]


def test_publish_track_activity_enqueues_create_to_followers(fed_config):
    """The helper builds a Create(Audio) activity and enqueues one delivery per inbox."""
    track, artist = _make_public_track()
    track.federation_object_id = "pub-1"
    user = _make_user_with_keys()

    with (
        patch(
            "songhive.services.federation.get_follower_inboxes",
            return_value=["https://a.example/inbox", "https://b.example/inbox"],
        ),
        patch("songhive.tasks.federation.deliver_activity") as mock_deliver,
    ):
        result = publish_track_activity(track, artist, user, fed_config, track.federation_object_id)

    assert result == 2
    assert mock_deliver.delay.call_count == 2
    calls = [call.args for call in mock_deliver.delay.call_args_list]
    assert calls[0][1] == "https://a.example/inbox"
    assert calls[1][1] == "https://b.example/inbox"
    activity = calls[0][0]
    assert activity["type"] == "Create"
    assert activity["actor"] == user.actor_url
    assert activity["object"]["id"] == f"{user.actor_url}/objects/pub-1"
