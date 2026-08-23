"""
Tests for FastAPI dependency helpers in ``songhive.api.deps``.
"""

import hashlib
import secrets
from typing import Optional

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from songhive.api.deps import (
    get_current_user_optional,
    get_db,
    require_access,
)
from songhive.api.errors import install_error_handlers
from songhive.api.middleware.auth import create_access_token
from songhive.models._enums import Visibility
from songhive.models.artist import Artist
from songhive.models.stored_file import StoredFile
from songhive.models.track import Track
from songhive.models.user import User
from songhive.services import sharing


def _override_db(session):
    """Return a FastAPI dependency that yields the provided session."""

    async def _db():
        yield session

    return _db


@pytest.fixture
def deps_client(db_session, config, fake_redis):
    """Create a test client with a minimal app for dependency tests."""
    app = FastAPI()
    app.state.config = config
    app.state.redis = fake_redis
    install_error_handlers(app)

    @app.get("/test/me")
    async def me(user: Optional[User] = Depends(get_current_user_optional)):
        return {"user_id": user.id if user is not None else None}

    @app.get("/test/tracks/{track_id}", dependencies=[Depends(require_access("track"))])
    async def get_track(track_id: str):
        return {"track_id": track_id}

    @app.get("/test/files/{file_id}", dependencies=[Depends(require_access("file"))])
    async def get_file(file_id: str):
        return {"file_id": file_id}

    app.dependency_overrides[get_db] = _override_db(db_session)

    with TestClient(app) as client:
        yield client


async def _make_track(db_session, owner, visibility: str = Visibility.PRIVATE.value) -> Track:
    """Create and persist a test track."""
    artist = Artist(name="Test Artist")
    db_session.add(artist)
    await db_session.flush()

    track = Track(
        title="Test Track",
        artist_id=artist.id,
        owner_id=owner.id if owner is not None else None,
        visibility=visibility,
    )
    db_session.add(track)
    await db_session.flush()
    return track


async def _make_file(
    db_session,
    owner: Optional[User] = None,
    visibility: str = Visibility.PRIVATE.value,
) -> StoredFile:
    """Create and persist a test stored file."""
    seed = secrets.token_bytes(16)
    sha = hashlib.sha256(seed).hexdigest()
    stored_file = StoredFile(
        storage_path=f"files/{sha[:2]}/{sha[2:4]}/{sha}",
        storage_backend="local",
        content_type="audio/mpeg",
        size=len(seed),
        sha256=sha,
        owner_id=owner.id if owner is not None else None,
        visibility=visibility,
    )
    db_session.add(stored_file)
    await db_session.flush()
    return stored_file


def test_get_current_user_optional_unauthenticated(deps_client):
    """An unauthenticated request returns no user."""
    response = deps_client.get("/test/me")
    assert response.status_code == 200
    assert response.json()["user_id"] is None


def test_get_current_user_optional_valid_token(deps_client, regular_user, config):
    """A valid token resolves to the authenticated user."""
    token = create_access_token(str(regular_user.id), config.auth.secret_key)
    response = deps_client.get("/test/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.json()["user_id"] == str(regular_user.id)


def test_get_current_user_optional_invalid_token(deps_client):
    """An invalid token is treated as an unauthenticated request."""
    response = deps_client.get("/test/me", headers={"Authorization": "Bearer not-a-token"})
    assert response.status_code == 200
    assert response.json()["user_id"] is None


def test_get_current_user_optional_inactive_user(deps_client, inactive_user, config):
    """A token for an inactive user resolves to no user."""
    token = create_access_token(str(inactive_user.id), config.auth.secret_key)
    response = deps_client.get("/test/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.json()["user_id"] is None


@pytest.mark.asyncio
async def test_require_access_owner(deps_client, regular_user, db_session, config):
    """The track owner can access a private track."""
    track = await _make_track(db_session, owner=regular_user, visibility=Visibility.PRIVATE.value)
    token = create_access_token(str(regular_user.id), config.auth.secret_key)

    response = deps_client.get(
        f"/test/tracks/{track.id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert response.json()["track_id"] == str(track.id)


@pytest.mark.asyncio
async def test_require_access_other_user_forbidden(deps_client, regular_user, make_user, db_session, config):
    """A non-owner is denied access to a private track."""
    track = await _make_track(db_session, owner=regular_user, visibility=Visibility.PRIVATE.value)
    other_user = await make_user("other", email_verified=True)
    token = create_access_token(str(other_user.id), config.auth.secret_key)

    response = deps_client.get(
        f"/test/tracks/{track.id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "Access denied"
    assert response.headers["content-type"].startswith("application/problem+json")


@pytest.mark.asyncio
async def test_require_access_public_track_anonymous(deps_client, regular_user, db_session):
    """Anonymous requests can access public tracks."""
    track = await _make_track(db_session, owner=regular_user, visibility=Visibility.PUBLIC.value)

    response = deps_client.get(f"/test/tracks/{track.id}")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_require_access_public_file_anonymous(deps_client, regular_user, db_session):
    """Anonymous requests can access public files."""
    stored_file = await _make_file(db_session, owner=regular_user, visibility=Visibility.PUBLIC.value)

    response = deps_client.get(f"/test/files/{stored_file.id}")
    assert response.status_code == 200
    assert response.json()["file_id"] == str(stored_file.id)


@pytest.mark.asyncio
async def test_require_access_local_track_authenticated(deps_client, regular_user, make_user, db_session, config):
    """Any authenticated user can access a local track."""
    track = await _make_track(db_session, owner=regular_user, visibility=Visibility.LOCAL.value)
    other_user = await make_user("other", email_verified=True)
    token = create_access_token(str(other_user.id), config.auth.secret_key)

    response = deps_client.get(
        f"/test/tracks/{track.id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_require_access_local_track_anonymous_forbidden(deps_client, regular_user, db_session):
    """Anonymous requests cannot access local tracks."""
    track = await _make_track(db_session, owner=regular_user, visibility=Visibility.LOCAL.value)

    response = deps_client.get(f"/test/tracks/{track.id}")
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_require_access_missing_item(deps_client, regular_user, db_session, config):
    """Accessing a missing track returns 404, not 403."""
    token = create_access_token(str(regular_user.id), config.auth.secret_key)
    response = deps_client.get(
        "/test/tracks/00000000-0000-0000-0000-000000000000",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "Not found"
    assert response.headers["content-type"].startswith("application/problem+json")


def test_require_access_unknown_item_type():
    """Requesting a dependency for an unknown item type raises RuntimeError."""
    with pytest.raises(RuntimeError):
        require_access("unknown")


@pytest.mark.asyncio
async def test_require_access_with_share_token(deps_client, regular_user, db_session):
    """A valid share token in the query string grants anonymous access."""
    track = await _make_track(db_session, owner=regular_user, visibility=Visibility.PRIVATE.value)
    _, raw = await sharing.create_share_token(db_session, "track", track.id, created_by=regular_user.id)

    response = deps_client.get(f"/test/tracks/{track.id}?token={raw}")
    assert response.status_code == 200
    assert response.json()["track_id"] == str(track.id)


@pytest.mark.asyncio
async def test_require_access_with_revoked_share_token(deps_client, regular_user, db_session):
    """A revoked share token does not grant access."""
    track = await _make_track(db_session, owner=regular_user, visibility=Visibility.PRIVATE.value)
    token, raw = await sharing.create_share_token(db_session, "track", track.id, created_by=regular_user.id)
    await sharing.revoke_share_token(db_session, token.id)

    response = deps_client.get(f"/test/tracks/{track.id}?token={raw}")
    assert response.status_code == 403
