"""
Tests for the ``/api/v1/scrobbling`` configuration endpoints.
"""

from unittest.mock import MagicMock

import pytest

from songhive.config.schema import ScrobblingConfig
from songhive.models._enums import Visibility
from songhive.models.artist import Artist
from songhive.models.scrobble import ScrobbleConfig
from songhive.models.track import Track
from songhive.services import scrobbler
from songhive.services.secrets import decrypt_secret


@pytest.fixture
def config(config):
    """Extend the base test config with scrobble service credentials."""
    return config.model_copy(
        update={
            "scrobbling": ScrobblingConfig(
                enabled=True,
                lastfm_api_key="lastfm-key",
                lastfm_api_secret="lastfm-secret",
            )
        }
    )


def _stub_client(monkeypatch, session_key="sk-1", remote_name="remote-alice"):
    """Stub the Audioscrobbler client used by ``scrobbler.connect``."""
    fake = MagicMock()
    fake.get_mobile_session.return_value = (session_key, remote_name)
    monkeypatch.setattr(scrobbler, "make_client", lambda cfg, service, session_key=None: fake)
    return fake


def test_status_unconfigured(client, regular_user, auth_headers):
    response = client.get("/api/v1/scrobbling/", headers=auth_headers(regular_user))
    assert response.status_code == 200
    body = response.json()
    assert body["enabled"] is True
    assert body["config"] is None
    assert body["thresholds"] == {"min_seconds": 30.0, "min_percent": None}
    services = {s["id"]: s for s in body["services"]}
    assert services["lastfm"]["available"] is True
    assert services["librefm"]["available"] is False


def test_status_requires_auth(client):
    assert client.get("/api/v1/scrobbling/").status_code == 401


def test_connect_creates_config(client, regular_user, auth_headers, db_session, monkeypatch):
    _stub_client(monkeypatch)
    response = client.post(
        "/api/v1/scrobbling/connect",
        headers=auth_headers(regular_user),
        json={"service": "lastfm", "username": "alice", "password": "pw"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["service"] == "lastfm"
    assert body["username"] == "remote-alice"
    assert body["enabled"] is True
    assert body["min_seconds"] == 30
    assert body["min_percent"] == 25
    assert "session_key" not in body
    assert "password" not in body


def test_connect_rejects_unavailable_service(client, regular_user, auth_headers):
    response = client.post(
        "/api/v1/scrobbling/connect",
        headers=auth_headers(regular_user),
        json={"service": "librefm", "username": "alice", "password": "pw"},
    )
    assert response.status_code == 422


def test_connect_rejects_unknown_service(client, regular_user, auth_headers):
    response = client.post(
        "/api/v1/scrobbling/connect",
        headers=auth_headers(regular_user),
        json={"service": "spotify", "username": "alice", "password": "pw"},
    )
    assert response.status_code == 422


def test_connect_bad_credentials_returns_422(client, regular_user, auth_headers, monkeypatch):
    fake = MagicMock()
    fake.get_mobile_session.side_effect = scrobbler.ScrobblerError("Invalid password")
    monkeypatch.setattr(scrobbler, "make_client", lambda cfg, service, session_key=None: fake)
    response = client.post(
        "/api/v1/scrobbling/connect",
        headers=auth_headers(regular_user),
        json={"service": "lastfm", "username": "alice", "password": "wrong"},
    )
    assert response.status_code == 422


def test_update_settings(client, regular_user, auth_headers, db_session):
    db_session.add(
        ScrobbleConfig(
            user_id=str(regular_user.id),
            service="lastfm",
            username="alice",
            session_key="sk",
        )
    )
    response = client.put(
        "/api/v1/scrobbling/",
        headers=auth_headers(regular_user),
        json={"enabled": False, "min_seconds": 60, "min_percent": 10},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["enabled"] is False
    assert body["min_seconds"] == 60
    assert body["min_percent"] == 10


def test_update_settings_validates_bounds(client, regular_user, auth_headers, db_session):
    db_session.add(
        ScrobbleConfig(
            user_id=str(regular_user.id),
            service="lastfm",
            username="alice",
            session_key="sk",
        )
    )
    for payload in (
        {"min_seconds": 0},
        {"min_seconds": 99999},
        {"min_percent": 0},
        {"min_percent": 101},
    ):
        response = client.put("/api/v1/scrobbling/", headers=auth_headers(regular_user), json=payload)
        assert response.status_code == 422, payload


def test_update_settings_requires_config(client, regular_user, auth_headers):
    response = client.put(
        "/api/v1/scrobbling/",
        headers=auth_headers(regular_user),
        json={"min_seconds": 45},
    )
    assert response.status_code == 404


def test_delete_config(client, regular_user, auth_headers, db_session):
    db_session.add(
        ScrobbleConfig(
            user_id=str(regular_user.id),
            service="lastfm",
            username="alice",
            session_key="sk",
        )
    )
    assert client.delete("/api/v1/scrobbling/", headers=auth_headers(regular_user)).status_code == 204
    assert client.delete("/api/v1/scrobbling/", headers=auth_headers(regular_user)).status_code == 404


def test_status_reflects_config(client, regular_user, auth_headers, db_session):
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
    response = client.get("/api/v1/scrobbling/", headers=auth_headers(regular_user))
    body = response.json()
    assert body["thresholds"] == {"min_seconds": 45.0, "min_percent": 10.0}
    assert body["config"]["username"] == "alice"
    assert body["config"]["min_seconds"] == 45


def test_feature_disabled_returns_404(client, regular_user, auth_headers):
    client.app.state.config.scrobbling.enabled = False
    # GET stays reachable so the UI can present the disabled state.
    response = client.get("/api/v1/scrobbling/", headers=auth_headers(regular_user))
    assert response.status_code == 200
    assert response.json()["enabled"] is False
    for method, kwargs in (
        (
            "post",
            {"url": "/api/v1/scrobbling/connect", "json": {"service": "lastfm", "username": "a", "password": "p"}},
        ),
        ("put", {"url": "/api/v1/scrobbling/", "json": {"min_seconds": 45}}),
        ("delete", {"url": "/api/v1/scrobbling/"}),
    ):
        assert getattr(client, method)(headers=auth_headers(regular_user), **kwargs).status_code == 404


async def _make_track(db_session, owner, **kwargs):
    artist = Artist(name="NP Artist")
    db_session.add(artist)
    await db_session.flush()
    track = Track(
        title="NP Song",
        artist_id=artist.id,
        owner_id=str(owner.id),
        visibility=kwargs.pop("visibility", Visibility.PUBLIC.value),
        duration=200.0,
        **kwargs,
    )
    db_session.add(track)
    await db_session.flush()
    return track


def _task_names(send_task):
    return [call.args[0] for call in send_task.call_args_list]


async def test_report_now_playing_enqueues_task(client, regular_user, auth_headers, db_session, _no_real_celery_broker):
    db_session.add(
        ScrobbleConfig(
            user_id=str(regular_user.id),
            service="lastfm",
            username="alice",
            session_key="sk",
        )
    )
    track = await _make_track(db_session, regular_user)
    response = client.post(f"/api/v1/scrobbling/now-playing/{track.id}", headers=auth_headers(regular_user))
    assert response.status_code == 204
    names = _task_names(_no_real_celery_broker)
    assert "songhive.tasks.scrobbling.now_playing" in names


async def test_report_now_playing_without_config_is_noop(
    client, regular_user, auth_headers, db_session, _no_real_celery_broker
):
    track = await _make_track(db_session, regular_user)
    response = client.post(f"/api/v1/scrobbling/now-playing/{track.id}", headers=auth_headers(regular_user))
    assert response.status_code == 204
    assert "songhive.tasks.scrobbling.now_playing" not in _task_names(_no_real_celery_broker)


async def test_report_now_playing_requires_access(client, regular_user, other_user, auth_headers, db_session):
    track = await _make_track(db_session, regular_user, visibility=Visibility.PRIVATE.value)
    response = client.post(f"/api/v1/scrobbling/now-playing/{track.id}", headers=auth_headers(other_user))
    assert response.status_code == 404


def test_report_now_playing_unknown_track(client, regular_user, auth_headers):
    response = client.post("/api/v1/scrobbling/now-playing/no-such-track", headers=auth_headers(regular_user))
    assert response.status_code == 404


def test_report_now_playing_requires_auth(client):
    assert client.post("/api/v1/scrobbling/now-playing/t1").status_code == 401


async def test_session_key_is_fernet_encrypted(db_session, regular_user, config, monkeypatch):
    _stub_client(monkeypatch, session_key="raw-session-key")
    row = await scrobbler.connect(
        db_session,
        regular_user,
        service="lastfm",
        username="alice",
        password="pw",
        config=config,
    )
    assert row.session_key != "raw-session-key"
    assert decrypt_secret(row.session_key) == "raw-session-key"
