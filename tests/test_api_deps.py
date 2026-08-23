"""
Tests for FastAPI dependency helpers in ``songhive.api.deps``.
"""

import hashlib
import secrets
from typing import Optional

import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient

from songhive.api.deps import (
    get_current_user,
    get_current_user_optional,
    get_db,
    get_effective_config,
    get_redis,
    get_storage_service,
    require_access,
    require_admin,
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

    @app.get("/test/protected")
    async def protected(user: User = Depends(get_current_user)):
        return {"user_id": user.id}

    @app.get("/test/admin")
    async def admin(user: User = Depends(require_admin)):
        return {"user_id": user.id}

    @app.get("/test/effective-config")
    async def effective_config(request: Request):
        cfg = await get_effective_config(request)
        return {"host": cfg.server.host}

    @app.get("/test/redis")
    async def redis(request: Request):
        return {"ok": get_redis(request) is not None}

    @app.get("/test/storage-service")
    async def storage_service(request: Request):
        service = get_storage_service(request)
        return {"service_id": id(service)}

    @app.get("/test/no-track", dependencies=[Depends(require_access("track"))])
    async def no_track():
        return {"ok": True}

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


def test_get_current_user_required(deps_client, regular_user, config):
    """A valid token on a protected route resolves the user."""
    token = create_access_token(str(regular_user.id), config.auth.secret_key)
    response = deps_client.get("/test/protected", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.json()["user_id"] == str(regular_user.id)


def test_get_current_user_missing_token(deps_client):
    """A protected route without a token returns 401."""
    response = deps_client.get("/test/protected")
    assert response.status_code == 401
    assert response.json()["detail"] == "Not authenticated"


def test_get_current_user_invalid_token(deps_client, config):
    """A protected route with an invalid token returns 401."""
    import jwt

    # Token without a subject claim should decode to None.
    token = jwt.encode({"foo": "bar"}, config.auth.secret_key, algorithm="HS256")
    response = deps_client.get("/test/protected", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


def test_get_current_user_inactive_user(deps_client, inactive_user, config):
    """A protected route with an inactive user token returns 401."""
    token = create_access_token(str(inactive_user.id), config.auth.secret_key)
    response = deps_client.get("/test/protected", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


def test_get_effective_config(deps_client):
    """get_effective_config returns the app config."""
    response = deps_client.get("/test/effective-config")
    assert response.status_code == 200
    assert response.json()["host"] == "127.0.0.1"


def test_get_redis(deps_client):
    """get_redis returns the configured Redis client."""
    response = deps_client.get("/test/redis")
    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_get_storage_service_uses_cache_without_change(deps_client):
    """get_storage_service returns the same instance when config is unchanged."""
    response1 = deps_client.get("/test/storage-service")
    response2 = deps_client.get("/test/storage-service")
    assert response1.status_code == 200
    assert response2.status_code == 200
    assert response1.json()["service_id"] == response2.json()["service_id"]


def test_get_storage_service_caches_and_recreates(deps_client, config):
    """get_storage_service caches the service and rebuilds on config change."""
    response1 = deps_client.get("/test/storage-service")
    assert response1.status_code == 200
    service_id1 = response1.json()["service_id"]

    # Mutate storage config to force a fresh StorageService.
    config.storage.local_path = config.storage.local_path.parent / "other_storage"

    response2 = deps_client.get("/test/storage-service")
    assert response2.status_code == 200
    service_id2 = response2.json()["service_id"]
    assert service_id2 != service_id1


def test_require_admin_allows_admin(deps_client, admin_user, config):
    """Admin-only routes allow admin users."""
    token = create_access_token(str(admin_user.id), config.auth.secret_key)
    response = deps_client.get("/test/admin", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.json()["user_id"] == str(admin_user.id)


def test_require_admin_rejects_regular_user(deps_client, regular_user, config):
    """Admin-only routes reject non-admin users."""
    token = create_access_token(str(regular_user.id), config.auth.secret_key)
    response = deps_client.get("/test/admin", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 403
    assert response.json()["detail"] == "Admin access required"


def test_require_access_missing_item_id(deps_client):
    """require_access raises 404 when the path parameter is missing."""
    response = deps_client.get("/test/no-track")
    assert response.status_code == 404
    assert response.json()["detail"] == "Not found"


@pytest.mark.asyncio
async def test_require_access_with_share_token_header(deps_client, regular_user, db_session):
    """A valid share token in the X-Share-Token header grants anonymous access."""
    track = await _make_track(db_session, owner=regular_user, visibility=Visibility.PRIVATE.value)
    _, raw = await sharing.create_share_token(db_session, "track", track.id, created_by=regular_user.id)

    response = deps_client.get(
        f"/test/tracks/{track.id}",
        headers={"X-Share-Token": raw},
    )
    assert response.status_code == 200
    assert response.json()["track_id"] == str(track.id)


@pytest.mark.asyncio
async def test_require_access_with_share_token_cookie(deps_client, regular_user, db_session):
    """A valid share token in the share_token cookie grants anonymous access."""
    track = await _make_track(db_session, owner=regular_user, visibility=Visibility.PRIVATE.value)
    _, raw = await sharing.create_share_token(db_session, "track", track.id, created_by=regular_user.id)

    response = deps_client.get(
        f"/test/tracks/{track.id}",
        cookies={"share_token": raw},
    )
    assert response.status_code == 200
    assert response.json()["track_id"] == str(track.id)


@pytest.mark.asyncio
async def test_get_db_yields_session(engine):
    """get_db yields an async SQLAlchemy session."""
    from sqlalchemy.ext.asyncio import AsyncSession

    from songhive.models.base import init_db

    init_db(engine=engine, force=True)
    gen = get_db()
    session = await gen.__anext__()
    assert isinstance(session, AsyncSession)
    await gen.aclose()
