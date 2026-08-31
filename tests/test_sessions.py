"""
Tests for the user session management endpoints.
"""

import pytest
from fastapi import status

from songhive.users.tokens import _hash_token


@pytest.mark.asyncio
async def test_list_sessions_after_login(client, config):
    """A successful login creates a session that is returned by the list endpoint."""
    client.post(
        "/api/v1/auth/register",
        json={
            "username": "alice",
            "email": "alice@example.com",
            "password": "secret",
        },
    )
    login_response = client.post(
        "/api/v1/auth/login",
        json={"username": "alice", "password": "secret"},
    )
    data = login_response.json()
    access_token = data["access_token"]
    refresh_token = data["refresh_token"]

    response = client.get(
        "/api/v1/auth/sessions",
        headers={"Authorization": f"Bearer {access_token}"},
        params={"current_session_id": _hash_token(refresh_token)},
    )
    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert body["total"] == 1
    assert len(body["items"]) == 1

    session = body["items"][0]
    assert session["id"] == _hash_token(refresh_token)
    assert session["is_current"] is True
    assert session["user_agent"] == "testclient"
    assert session["ip_address"] == "testclient"
    assert session["created_at"] is not None
    assert session["expires_at"] is not None


@pytest.mark.asyncio
async def test_list_sessions_without_auth(client):
    """The session list endpoint requires authentication."""
    response = client.get("/api/v1/auth/sessions")
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
async def test_list_sessions_mark_other_sessions(client, config):
    """The current_session_id query param marks the matching session as current."""
    client.post(
        "/api/v1/auth/register",
        json={
            "username": "alice",
            "email": "alice@example.com",
            "password": "secret",
        },
    )
    first = client.post(
        "/api/v1/auth/login",
        json={"username": "alice", "password": "secret"},
    ).json()
    second = client.post(
        "/api/v1/auth/login",
        json={"username": "alice", "password": "secret"},
    ).json()

    first_hash = _hash_token(first["refresh_token"])
    second_hash = _hash_token(second["refresh_token"])

    response = client.get(
        "/api/v1/auth/sessions",
        headers={"Authorization": f"Bearer {first['access_token']}"},
        params={"current_session_id": first_hash},
    )
    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert body["total"] == 2

    sessions = {s["id"]: s for s in body["items"]}
    assert sessions[first_hash]["is_current"] is True
    assert sessions[second_hash]["is_current"] is False


@pytest.mark.asyncio
async def test_revoke_session_prevents_refresh(client, config):
    """Revoking a session deletes the refresh token and prevents refresh."""
    client.post(
        "/api/v1/auth/register",
        json={
            "username": "alice",
            "email": "alice@example.com",
            "password": "secret",
        },
    )
    login = client.post(
        "/api/v1/auth/login",
        json={"username": "alice", "password": "secret"},
    ).json()
    refresh_token = login["refresh_token"]
    session_id = _hash_token(refresh_token)

    response = client.delete(
        f"/api/v1/auth/sessions/{session_id}",
        headers={"Authorization": f"Bearer {login['access_token']}"},
    )
    assert response.status_code == status.HTTP_200_OK
    assert response.json()["success"] is True

    refresh_response = client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": refresh_token},
    )
    assert refresh_response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
async def test_revoke_other_user_session_returns_404(client, db_session, make_user, auth_headers):
    """A user cannot revoke another user's session."""
    alice = await make_user("alice")
    await make_user("bob")

    # Log in as Bob to create a session through the app.
    bob_login = client.post(
        "/api/v1/auth/login",
        json={"username": "bob", "password": "secret"},
    ).json()
    session_id = _hash_token(bob_login["refresh_token"])

    response = client.delete(
        f"/api/v1/auth/sessions/{session_id}",
        headers=auth_headers(alice),
    )
    assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.asyncio
async def test_revoke_session_blocks_access_token(client, config):
    """Revoking a session immediately blocks the associated access token."""
    client.post(
        "/api/v1/auth/register",
        json={
            "username": "alice",
            "email": "alice@example.com",
            "password": "secret",
        },
    )
    login = client.post(
        "/api/v1/auth/login",
        json={"username": "alice", "password": "secret"},
    ).json()
    access_token = login["access_token"]
    session_id = _hash_token(login["refresh_token"])

    response = client.delete(
        f"/api/v1/auth/sessions/{session_id}",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert response.status_code == status.HTTP_200_OK
    assert response.json()["success"] is True

    # The previously valid access token should now be rejected.
    me_response = client.get(
        "/api/v1/users/me",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert me_response.status_code == status.HTTP_401_UNAUTHORIZED
