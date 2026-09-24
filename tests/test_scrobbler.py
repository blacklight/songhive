"""
Tests for the Audioscrobbler scrobbler service and Celery tasks.
"""

import hashlib
from unittest.mock import MagicMock

import pytest
import requests

from songhive.config.schema import ScrobblingConfig
from songhive.models.artist import Artist
from songhive.models.scrobble import ScrobbleConfig
from songhive.models.track import Track
from songhive.services import scrobbler
from songhive.services.scrobbler import ScrobblerError, ScrobblerTemporaryError
from songhive.services.secrets import decrypt_secret
from songhive.tasks import scrobbling as scrobble_tasks


def _scrobbling_config(config, **overrides):
    """Return a copy of ``config`` with scrobbling API credentials set."""
    params = {
        "enabled": True,
        "lastfm_api_key": "lastfm-key",
        "lastfm_api_secret": "lastfm-secret",
        "librefm_api_key": "librefm-key",
        "librefm_api_secret": "librefm-secret",
    }
    params.update(overrides)
    return config.model_copy(update={"scrobbling": ScrobblingConfig(**params)})


class _FakeResponse:
    def __init__(self, payload=None, status_code=200):
        self._payload = payload if payload is not None else {}
        self.status_code = status_code

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload

    def close(self):
        pass


class _RecordingSession:
    """Fake ``requests.Session`` that captures POSTed form payloads."""

    def __init__(self, response):
        self.response = response
        self.posts = []

    def post(self, url, data=None, timeout=None, headers=None):
        self.posts.append({"url": url, "data": dict(data or {})})
        return self.response

    def close(self):
        pass


def _client_with_response(config, response, service="lastfm", session_key="sk"):
    client = scrobbler.make_client(config, service, session_key=session_key)
    fake = _RecordingSession(response)
    client._session = fake
    return client, fake


def test_signature_is_md5_of_sorted_params_plus_secret():
    params = {"track": "Song", "artist": "Band", "method": "track.scrobble"}
    expected = hashlib.md5("artistBandmethodtrack.scrobbletrackSongtopsecret".encode()).hexdigest()
    assert scrobbler.ScrobblerClient._signature(params, "topsecret") == expected


def test_service_api_url():
    assert scrobbler.service_api_url("lastfm") == "https://ws.audioscrobbler.com/2.0/"
    assert scrobbler.service_api_url("librefm") == "https://libre.fm/2.0/"
    with pytest.raises(ScrobblerError):
        scrobbler.service_api_url("spotify")


def test_service_available_requires_keys_and_feature(config):
    configured = _scrobbling_config(config)
    assert scrobbler.service_available(configured, "lastfm")
    assert scrobbler.service_available(configured, "librefm")
    assert not scrobbler.service_available(configured, "other")

    unconfigured = _scrobbling_config(config, librefm_api_key="", librefm_api_secret="")
    assert scrobbler.service_available(unconfigured, "lastfm")
    assert not scrobbler.service_available(unconfigured, "librefm")

    disabled = _scrobbling_config(config, enabled=False)
    assert not scrobbler.service_available(disabled, "lastfm")


def test_make_client_requires_credentials(config):
    with pytest.raises(ScrobblerError):
        scrobbler.make_client(config, "lastfm")


def test_get_mobile_session_posts_signed_payload(config):
    config = _scrobbling_config(config)
    client, fake = _client_with_response(
        config,
        _FakeResponse(
            {
                "session": {"key": "sk-123", "name": "alice"},
            }
        ),
        session_key=None,
    )
    key, name = client.get_mobile_session("alice", "pw")
    assert (key, name) == ("sk-123", "alice")
    post = fake.posts[0]
    assert post["url"] == "https://ws.audioscrobbler.com/2.0/"
    assert post["data"]["method"] == "auth.getMobileSession"
    assert post["data"]["username"] == "alice"
    assert post["data"]["api_key"] == "lastfm-key"
    assert "api_sig" in post["data"]


def test_update_now_playing_includes_track_fields(config):
    config = _scrobbling_config(config)
    client, fake = _client_with_response(config, _FakeResponse({"nowplaying": {}}))
    client.update_now_playing(artist="Band", track="Song", album="LP", duration=180)
    data = fake.posts[0]["data"]
    assert data["method"] == "track.updateNowPlaying"
    assert data["sk"] == "sk"
    assert data["artist"] == "Band"
    assert data["track"] == "Song"
    assert data["album"] == "LP"
    assert data["duration"] == "180"


def test_scrobble_includes_timestamp(config):
    config = _scrobbling_config(config)
    client, fake = _client_with_response(config, _FakeResponse({"scrobbles": {}}))
    client.scrobble(timestamp=1700000000, artist="Band", track="Song")
    data = fake.posts[0]["data"]
    assert data["method"] == "track.scrobble"
    assert data["timestamp"] == "1700000000"


def test_api_error_raises_scrobbler_error(config):
    config = _scrobbling_config(config)
    client, _ = _client_with_response(config, _FakeResponse({"error": 4, "message": "Authentication Failed"}))
    with pytest.raises(ScrobblerError, match="Authentication Failed"):
        client.update_now_playing(artist="a", track="t")


def test_server_error_is_temporary(config):
    config = _scrobbling_config(config)
    client, _ = _client_with_response(config, _FakeResponse({}, status_code=503))
    with pytest.raises(ScrobblerTemporaryError):
        client.update_now_playing(artist="a", track="t")


def test_network_error_is_temporary(config, monkeypatch):
    config = _scrobbling_config(config)
    client = scrobbler.make_client(config, "lastfm", session_key="sk")

    def _raise(*args, **kwargs):
        raise requests.ConnectionError("unreachable")

    monkeypatch.setattr(client._session, "post", _raise)
    with pytest.raises(ScrobblerTemporaryError):
        client.update_now_playing(artist="a", track="t")


async def test_connect_stores_encrypted_session_key(db_session, regular_user, config, monkeypatch):
    config = _scrobbling_config(config)
    fake_client = MagicMock()
    fake_client.get_mobile_session.return_value = ("sk-abc", "remote-alice")
    monkeypatch.setattr(scrobbler, "make_client", lambda cfg, service, session_key=None: fake_client)

    row = await scrobbler.connect(
        db_session, regular_user, service="lastfm", username="alice", password="pw", config=config
    )
    assert row.service == "lastfm"
    assert row.username == "remote-alice"
    assert row.enabled
    assert row.session_key != "sk-abc"
    assert decrypt_secret(row.session_key) == "sk-abc"
    fake_client.get_mobile_session.assert_called_once_with("alice", "pw")


async def test_connect_replaces_existing_config(db_session, regular_user, config, monkeypatch):
    config = _scrobbling_config(config)
    fake_client = MagicMock()
    fake_client.get_mobile_session.return_value = ("sk-2", "remote-bob")
    monkeypatch.setattr(scrobbler, "make_client", lambda cfg, service, session_key=None: fake_client)

    db_session.add(
        ScrobbleConfig(
            user_id=str(regular_user.id),
            service="librefm",
            username="old",
            session_key="stale",
            enabled=False,
        )
    )
    await db_session.flush()

    row = await scrobbler.connect(
        db_session, regular_user, service="lastfm", username="bob", password="pw", config=config
    )
    assert row.service == "lastfm"
    assert row.username == "remote-bob"
    assert row.enabled
    assert decrypt_secret(row.session_key) == "sk-2"


async def test_thresholds_for_defaults_and_config(db_session, regular_user):
    assert await scrobbler.thresholds_for(db_session, regular_user) == (30.0, None)
    assert await scrobbler.thresholds_for(db_session, None) == (30.0, None)

    db_session.add(
        ScrobbleConfig(
            user_id=str(regular_user.id),
            service="lastfm",
            username="alice",
            session_key="sk",
            min_seconds=45,
            min_percent=10,
        )
    )
    await db_session.flush()
    assert await scrobbler.thresholds_for(db_session, regular_user) == (45.0, 10.0)


async def test_thresholds_for_disabled_config_falls_back(db_session, regular_user):
    db_session.add(
        ScrobbleConfig(
            user_id=str(regular_user.id),
            service="lastfm",
            username="alice",
            session_key="sk",
            enabled=False,
            min_seconds=5,
            min_percent=5,
        )
    )
    await db_session.flush()
    assert await scrobbler.thresholds_for(db_session, regular_user) == (30.0, None)


async def test_update_and_delete_config(db_session, regular_user):
    row = ScrobbleConfig(user_id=str(regular_user.id), service="lastfm", username="alice", session_key="sk")
    db_session.add(row)
    await db_session.flush()

    await scrobbler.update_settings(db_session, row, enabled=False, min_seconds=60, min_percent=50)
    assert not row.enabled
    assert row.min_seconds == 60
    assert row.min_percent == 50

    assert await scrobbler.delete_config(db_session, regular_user)
    assert not await scrobbler.delete_config(db_session, regular_user)


async def test_track_fields_maps_relations(db_session, regular_user):
    from sqlalchemy import select

    artist = Artist(name="The Band")
    db_session.add(artist)
    await db_session.flush()
    track = Track(
        title="Song",
        artist_id=artist.id,
        owner_id=str(regular_user.id),
        duration=200.0,
        track_number=3,
        musicbrainz_id="mbid-1",
    )
    db_session.add(track)
    await db_session.flush()
    track = await db_session.scalar(select(Track).where(Track.id == track.id))

    fields = scrobbler.track_fields(track)
    assert fields["artist"] == "The Band"
    assert fields["track"] == "Song"
    assert fields["duration"] == 200
    assert fields["track_number"] == 3
    assert fields["mbid"] == "mbid-1"


def test_enqueue_now_playing_dispatches_task(_no_real_celery_broker):
    scrobbler.enqueue_now_playing("u1", "t1")
    assert _no_real_celery_broker.called
    name = _no_real_celery_broker.call_args[0][0]
    assert name == "songhive.tasks.scrobbling.now_playing"


def test_enqueue_scrobble_dispatches_task(_no_real_celery_broker):
    scrobbler.enqueue_scrobble("u1", "t1", 1700000000)
    name = _no_real_celery_broker.call_args[0][0]
    assert name == "songhive.tasks.scrobbling.scrobble"


def test_enqueue_never_raises(_no_real_celery_broker):
    _no_real_celery_broker.side_effect = RuntimeError("broker down")
    scrobbler.enqueue_now_playing("u1", "t1")
    scrobbler.enqueue_scrobble("u1", "t1")


def test_scrobble_dedup_ttl():
    row = ScrobbleConfig(user_id="u", service="lastfm", username="a", session_key="sk", min_seconds=30, min_percent=25)
    # 300s track: 25% = 75s > 30s, so the seconds threshold wins.
    assert scrobble_tasks._scrobble_dedup_ttl(row, 300.0) == 25
    # 60s track: 25% = 15s < 30s, so the percent threshold wins.
    assert scrobble_tasks._scrobble_dedup_ttl(row, 60.0) == 10
    # Unknown duration falls back to the seconds threshold.
    assert scrobble_tasks._scrobble_dedup_ttl(row, None) == 25
    # Tiny thresholds still get the floor.
    row.min_seconds = 6
    assert scrobble_tasks._scrobble_dedup_ttl(row, 8.0) == 5


def test_dedup_helpers_use_redis(config, fake_redis_server):
    key = scrobble_tasks._dedup_key("np", "u1", "t1")
    assert not scrobble_tasks._recently_submitted(config, key)
    scrobble_tasks._mark_submitted(config, key, 60)
    assert scrobble_tasks._recently_submitted(config, key)


async def test_record_listen_enqueues_scrobble(db_session, regular_user, _no_real_celery_broker):
    from songhive.services.streaming import record_listen

    artist = Artist(name="A")
    db_session.add(artist)
    await db_session.flush()
    track = Track(
        title="Song",
        artist_id=artist.id,
        owner_id=str(regular_user.id),
        duration=100.0,
    )
    db_session.add(track)
    db_session.add(
        ScrobbleConfig(
            user_id=str(regular_user.id),
            service="lastfm",
            username="alice",
            session_key="sk",
        )
    )
    await db_session.flush()

    await record_listen(db_session, str(regular_user.id), str(track.id))

    names = [call[0][0] for call in _no_real_celery_broker.call_args_list]
    assert "songhive.tasks.scrobbling.scrobble" in names


async def test_record_listen_skips_unconfigured_users(db_session, regular_user, _no_real_celery_broker):
    from songhive.services.streaming import record_listen

    artist = Artist(name="A")
    db_session.add(artist)
    await db_session.flush()
    track = Track(title="Song", artist_id=artist.id, owner_id=str(regular_user.id))
    db_session.add(track)
    await db_session.flush()

    await record_listen(db_session, str(regular_user.id), str(track.id))

    names = [call[0][0] for call in _no_real_celery_broker.call_args_list]
    assert "songhive.tasks.scrobbling.scrobble" not in names


async def test_remote_object_fields_maps_metadata(db_session):
    """A cached remote ``Audio`` rendition yields clean scrobble fields."""
    from songhive.models.remote_object import RemoteObject

    row = RemoteObject(
        canonical_url="https://remote.example/uploads/1",
        domain="remote.example",
        object_type="Audio",
        resource_type="track",
        actor_url="https://remote.example/users/bob",
        name="The Band - The Album - The Song",
        payload={
            "type": "Audio",
            "id": "https://remote.example/uploads/1",
            "name": "The Band - The Album - The Song",
            "duration": 240,
            "track": {
                "type": "Track",
                "id": "https://remote.example/tracks/1",
                "name": "The Song",
                "position": 4,
                "musicbrainz_recordingid": "mbid-remote",
                "artists": [{"name": "The Band"}],
                "album": {"name": "The Album"},
            },
        },
    )
    db_session.add(row)
    await db_session.flush()

    fields = scrobbler.remote_object_fields(row)
    assert fields["artist"] == "The Band"
    assert fields["track"] == "The Song"
    assert fields["album"] == "The Album"
    assert fields["album_artist"] == "The Band"
    assert fields["duration"] == 240
    assert fields["track_number"] == 4
    assert fields["mbid"] == "mbid-remote"


async def test_load_scrobble_fields_folds_rendition(db_session):
    """A rendition id resolves to its media entity's scrobble fields."""
    from songhive.models.remote_object import RemoteObject

    track = RemoteObject(
        canonical_url="https://remote.example/tracks/1",
        domain="remote.example",
        object_type="Track",
        resource_type="track",
        actor_url="https://remote.example/users/bob",
        name="The Song",
        payload={
            "type": "Track",
            "id": "https://remote.example/tracks/1",
            "name": "The Song",
            "artists": [{"name": "The Band"}],
            "album": {"name": "The Album"},
        },
    )
    rendition = RemoteObject(
        canonical_url="https://remote.example/uploads/1",
        domain="remote.example",
        object_type="Audio",
        resource_type="track",
        actor_url="https://remote.example/users/bob",
        name="The Band - The Album - The Song",
        media_of_url=track.canonical_url,
        payload={"type": "Audio", "id": "https://remote.example/uploads/1"},
    )
    db_session.add_all([track, rendition])
    await db_session.flush()

    fields = await scrobble_tasks._load_scrobble_fields(db_session, str(rendition.id), "remote")
    assert fields is not None
    # Fields come from the entity row, not the rendition's composite name.
    assert fields["track"] == "The Song"
    assert fields["artist"] == "The Band"
    assert fields["album"] == "The Album"

    assert await scrobble_tasks._load_scrobble_fields(db_session, "missing", "remote") is None


def test_enqueue_remote_dispatches_entity_kind(_no_real_celery_broker):
    scrobbler.enqueue_now_playing("u1", "ro1", "remote")
    scrobbler.enqueue_remote_scrobble("u1", "ro1", 1700000000)
    names = [call[0][0] for call in _no_real_celery_broker.call_args_list]
    assert "songhive.tasks.scrobbling.now_playing" in names
    assert "songhive.tasks.scrobbling.scrobble" in names
    for call in _no_real_celery_broker.call_args_list:
        args = call.kwargs.get("args") or call[0][1]
        assert args[-1] == "remote"


async def test_record_remote_listen_records_history_and_enqueues(db_session, regular_user, _no_real_celery_broker):
    from sqlalchemy import select

    from songhive.models.history import ListeningHistory
    from songhive.models.remote_object import RemoteObject
    from songhive.services.streaming import record_remote_listen

    row = RemoteObject(
        canonical_url="https://remote.example/tracks/1",
        domain="remote.example",
        object_type="Track",
        resource_type="track",
        actor_url="https://remote.example/users/bob",
        name="The Song",
    )
    db_session.add(row)
    db_session.add(
        ScrobbleConfig(
            user_id=str(regular_user.id),
            service="lastfm",
            username="alice",
            session_key="sk",
        )
    )
    await db_session.flush()

    await record_remote_listen(db_session, str(regular_user.id), str(row.id))

    entry = await db_session.scalar(select(ListeningHistory).where(ListeningHistory.remote_object_id == str(row.id)))
    assert entry is not None
    assert entry.track_id is None
    names = [call[0][0] for call in _no_real_celery_broker.call_args_list]
    assert "songhive.tasks.scrobbling.scrobble" in names


async def test_record_remote_listen_skips_unconfigured_users(db_session, regular_user, _no_real_celery_broker):
    from sqlalchemy import select

    from songhive.models.history import ListeningHistory
    from songhive.models.remote_object import RemoteObject
    from songhive.services.streaming import record_remote_listen

    row = RemoteObject(
        canonical_url="https://remote.example/tracks/1",
        domain="remote.example",
        object_type="Track",
        resource_type="track",
        actor_url="https://remote.example/users/bob",
        name="The Song",
    )
    db_session.add(row)
    await db_session.flush()

    await record_remote_listen(db_session, str(regular_user.id), str(row.id))

    entry = await db_session.scalar(select(ListeningHistory).where(ListeningHistory.remote_object_id == str(row.id)))
    assert entry is not None
    names = [call[0][0] for call in _no_real_celery_broker.call_args_list]
    assert "songhive.tasks.scrobbling.scrobble" not in names
