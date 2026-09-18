"""
Single-user mode and public instance stats tests.

Covers the ``single_user_username``/``public_stats_enabled`` settings, the
``single_user`` field on the instance endpoints, the anonymous ``/``
redirect, and the gated ``GET /api/v1/instance/stats`` endpoint.
"""

import json

import pytest
from fastapi import status

from songhive.models import Visibility
from songhive.models.artist import Artist
from songhive.models.setting import Setting
from songhive.models.track import Track


async def _set_setting(db_session, key: str, value) -> None:
    """Write a runtime setting row directly."""
    db_session.add(Setting(key=key, value=json.dumps(value)))
    await db_session.flush()


async def _make_track(db_session, owner, visibility: str) -> Track:
    """Create and persist a track with the given visibility."""
    artist = Artist(name="Stats Artist")
    db_session.add(artist)
    await db_session.flush()
    track = Track(
        title="Stats Track",
        artist_id=artist.id,
        owner_id=owner.id if owner is not None else None,
        visibility=visibility,
    )
    db_session.add(track)
    await db_session.flush()
    return track


# ---------------------------------------------------------------------------
# single_user_username setting
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_single_user_username_defaults_to_empty(client, admin_user, auth_headers):
    """The setting is listed with its default for admins."""
    response = client.get("/api/v1/admin/settings", headers=auth_headers(admin_user))
    assert response.status_code == status.HTTP_200_OK
    data = {item["key"]: item for item in response.json()}
    assert data["single_user_username"]["value"] == ""
    assert data["single_user_username"]["type"] == "str"
    assert data["public_stats_enabled"]["value"] is False


@pytest.mark.asyncio
async def test_single_user_username_accepts_active_user(client, admin_user, regular_user, auth_headers):
    """An active username is accepted."""
    response = client.put(
        "/api/v1/admin/settings/single_user_username",
        headers=auth_headers(admin_user),
        json={"value": "regular"},
    )
    assert response.status_code == status.HTTP_200_OK
    assert response.json()["value"] == "regular"


@pytest.mark.asyncio
async def test_single_user_username_rejects_unknown_or_inactive(client, admin_user, inactive_user, auth_headers):
    """Unknown and inactive usernames are rejected with 422."""
    for value in ("missing-user", "inactive"):
        response = client.put(
            "/api/v1/admin/settings/single_user_username",
            headers=auth_headers(admin_user),
            json={"value": value},
        )
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


@pytest.mark.asyncio
async def test_single_user_username_empty_clears(client, admin_user, regular_user, auth_headers):
    """An empty string disables single-user mode."""
    headers = auth_headers(admin_user)
    client.put(
        "/api/v1/admin/settings/single_user_username",
        headers=headers,
        json={"value": "regular"},
    )
    response = client.put(
        "/api/v1/admin/settings/single_user_username",
        headers=headers,
        json={"value": ""},
    )
    assert response.status_code == status.HTTP_200_OK


# ---------------------------------------------------------------------------
# Instance metadata
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_instance_single_user_null_by_default(client):
    """The instance endpoints report ``single_user: null`` when unset."""
    for path in ("/api/v1/instance", "/api/v2/instance"):
        response = client.get(path)
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["single_user"] is None


@pytest.mark.asyncio
async def test_instance_single_user_exposed(client, db_session, regular_user):
    """The configured username appears on both instance endpoints."""
    await _set_setting(db_session, "single_user_username", "regular")

    for path in ("/api/v1/instance", "/api/v2/instance"):
        response = client.get(path)
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["single_user"] == "regular"


# ---------------------------------------------------------------------------
# Server-side / redirect
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_home_serves_spa_without_single_user(client):
    """Without the setting, ``/`` serves the SPA to anonymous visitors."""
    response = client.get("/")
    assert response.status_code == status.HTTP_200_OK
    assert response.headers["content-type"].startswith("text/html")


@pytest.mark.asyncio
async def test_home_redirects_anonymous_to_single_user(client, db_session, regular_user):
    """Anonymous HTML requests to ``/`` redirect to ``/@{username}``."""
    await _set_setting(db_session, "single_user_username", "regular")

    response = client.get("/", headers={"Accept": "text/html"}, follow_redirects=False)
    assert response.status_code == status.HTTP_302_FOUND
    assert response.headers["location"] == "/@regular"


@pytest.mark.asyncio
async def test_home_no_redirect_for_authenticated(client, db_session, regular_user, auth_headers):
    """Authenticated visitors keep the regular home page."""
    await _set_setting(db_session, "single_user_username", "regular")

    response = client.get(
        "/",
        headers={"Accept": "text/html", **auth_headers(regular_user)},
    )
    assert response.status_code == status.HTTP_200_OK
    assert response.headers["content-type"].startswith("text/html")


@pytest.mark.asyncio
async def test_home_no_redirect_for_activitypub(client, db_session, regular_user):
    """ActivityPub clients are never redirected; `/` stays the SPA shell."""
    await _set_setting(db_session, "single_user_username", "regular")

    for accept in (
        "application/activity+json",
        'application/ld+json; profile="https://www.w3.org/ns/activitystreams"',
    ):
        response = client.get("/", headers={"Accept": accept})
        assert response.status_code == status.HTTP_200_OK
        assert response.headers["content-type"].startswith("text/html")


@pytest.mark.asyncio
async def test_home_redirect_advertises_rel_me(client, db_session, regular_user):
    """The single-user redirect advertises ``rel="me"`` in the ``Link`` header."""
    await _set_setting(db_session, "single_user_username", "regular")

    response = client.get("/", headers={"Accept": "text/html"}, follow_redirects=False)
    assert response.status_code == status.HTTP_302_FOUND
    link = response.headers["Link"]
    assert '<http://testserver/@regular>; rel="me"' in link
    assert '<http://testserver/users/regular>; rel="me"' in link


@pytest.mark.asyncio
async def test_home_spa_advertises_rel_me_for_authenticated(client, db_session, regular_user, auth_headers):
    """Authenticated visitors get ``rel="me"`` links in the body and ``Link`` header."""
    await _set_setting(db_session, "single_user_username", "regular")

    response = client.get(
        "/",
        headers={"Accept": "text/html", **auth_headers(regular_user)},
    )
    assert response.status_code == status.HTTP_200_OK
    assert '<link rel="me" href="http://testserver/@regular">' in response.text
    assert '<link rel="me" href="http://testserver/users/regular">' in response.text
    link = response.headers["Link"]
    assert '<http://testserver/@regular>; rel="me"' in link
    assert '<http://testserver/users/regular>; rel="me"' in link


@pytest.mark.asyncio
async def test_home_spa_advertises_rel_me_for_activitypub(client, db_session, regular_user):
    """ActivityPub clients get the SPA shell with ``rel="me"`` hints as well."""
    await _set_setting(db_session, "single_user_username", "regular")

    response = client.get("/", headers={"Accept": "application/activity+json"})
    assert response.status_code == status.HTTP_200_OK
    assert '<link rel="me" href="http://testserver/@regular">' in response.text
    assert '<link rel="me" href="http://testserver/users/regular">' in response.text
    assert 'rel="me"' in response.headers["Link"]


@pytest.mark.asyncio
async def test_home_no_rel_me_without_single_user(client):
    """Without the setting, ``/`` serves the SPA with no ``rel="me"`` hints."""
    response = client.get("/")
    assert response.status_code == status.HTTP_200_OK
    assert 'rel="me"' not in response.text
    assert "Link" not in response.headers


# ---------------------------------------------------------------------------
# GET /api/v1/instance/stats
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_instance_stats_gated_by_setting(client, db_session, regular_user):
    """The stats endpoint answers 404 until the setting is enabled."""
    response = client.get("/api/v1/instance/stats")
    assert response.status_code == status.HTTP_404_NOT_FOUND

    await _set_setting(db_session, "public_stats_enabled", True)

    response = client.get("/api/v1/instance/stats")
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert set(data) == {"tracks", "albums", "artists", "libraries", "users"}


@pytest.mark.asyncio
async def test_instance_stats_visibility_filtered(client, db_session, regular_user, auth_headers):
    """Anonymous callers see public counts; the owner sees their private rows."""
    await _set_setting(db_session, "public_stats_enabled", True)
    await _make_track(db_session, regular_user, Visibility.PUBLIC.value)
    await _make_track(db_session, regular_user, Visibility.PRIVATE.value)

    response = client.get("/api/v1/instance/stats")
    assert response.status_code == status.HTTP_200_OK
    assert response.json()["tracks"] == 1

    response = client.get("/api/v1/instance/stats", headers=auth_headers(regular_user))
    assert response.status_code == status.HTTP_200_OK
    assert response.json()["tracks"] == 2
