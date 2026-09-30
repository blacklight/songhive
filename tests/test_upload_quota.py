"""
Upload quota tests: instance default, per-user overrides, and enforcement.
"""

import io

import pytest
from fastapi import status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from songhive.models.stored_file import StoredFile
from songhive.models.user import UPLOAD_QUOTA_UNLIMITED
from songhive.services.storage import get_upload_usage, resolve_upload_quota


def _set_default_quota(client, quota):
    client.app.state.config.storage.upload_quota = quota


def _upload(client, headers, content=b"data", name="file.bin", content_type="application/octet-stream"):
    return client.post(
        "/api/v1/files/upload",
        files={"file": (name, io.BytesIO(content), content_type)},
        headers=headers,
    )


def test_resolve_upload_quota_unlimited_by_default(config, regular_user):
    """With no instance default and no override, the quota is unlimited."""
    assert config.storage.upload_quota is None
    assert resolve_upload_quota(regular_user, config.storage) is None


def test_resolve_upload_quota_inherits_default(config, regular_user):
    """A NULL override inherits the configured instance default."""
    config.storage.upload_quota = 1024
    assert resolve_upload_quota(regular_user, config.storage) == 1024


def test_resolve_upload_quota_admin_is_exempt(config, admin_user):
    """Admin users are never quota-limited."""
    config.storage.upload_quota = 1
    admin_user.upload_quota = 1
    assert resolve_upload_quota(admin_user, config.storage) is None


def test_resolve_upload_quota_overrides(config, regular_user):
    """Per-user overrides win over the instance default in both directions."""
    config.storage.upload_quota = 1024
    regular_user.upload_quota = 64
    assert resolve_upload_quota(regular_user, config.storage) == 64

    regular_user.upload_quota = UPLOAD_QUOTA_UNLIMITED
    assert resolve_upload_quota(regular_user, config.storage) is None

    # A non-null override applies even with no instance default.
    config.storage.upload_quota = None
    regular_user.upload_quota = 64
    assert resolve_upload_quota(regular_user, config.storage) == 64


def test_upload_unlimited_when_no_quota(client, regular_user, auth_headers):
    """Without a configured quota, uploads succeed."""
    response = _upload(client, auth_headers(regular_user))
    assert response.status_code == status.HTTP_200_OK


def test_upload_within_quota(client, regular_user, auth_headers):
    """An upload that fits inside the quota is stored."""
    _set_default_quota(client, 1024)
    response = _upload(client, auth_headers(regular_user), content=b"x" * 16)
    assert response.status_code == status.HTTP_200_OK


def test_upload_exceeding_quota_returns_403(client, regular_user, auth_headers):
    """An upload larger than the quota is rejected with HTTP 403."""
    _set_default_quota(client, 8)
    response = _upload(client, auth_headers(regular_user), content=b"x" * 16)
    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert response.json()["detail"] == "Upload quota exceeded"


def test_upload_quota_counts_existing_usage(client, regular_user, auth_headers):
    """Previously uploaded bytes count against the quota."""
    _set_default_quota(client, 40)
    headers = auth_headers(regular_user)

    assert _upload(client, headers, content=b"a" * 16, name="one.bin").status_code == status.HTTP_200_OK
    assert _upload(client, headers, content=b"b" * 25, name="two.bin").status_code == status.HTTP_403_FORBIDDEN
    assert _upload(client, headers, content=b"c" * 24, name="three.bin").status_code == status.HTTP_200_OK


def test_upload_quota_ignores_duplicate_content(client, regular_user, auth_headers):
    """Re-uploading identical bytes does not consume quota again."""
    _set_default_quota(client, 20)
    headers = auth_headers(regular_user)

    assert _upload(client, headers, content=b"d" * 16).status_code == status.HTTP_200_OK
    response = _upload(client, headers, content=b"d" * 16, name="again.bin")
    assert response.status_code == status.HTTP_200_OK
    assert response.headers["X-Duplicate"] == "true"


def test_upload_quota_admin_exempt(client, admin_user, auth_headers):
    """Admin uploads bypass the quota entirely."""
    _set_default_quota(client, 8)
    response = _upload(client, auth_headers(admin_user), content=b"x" * 32)
    assert response.status_code == status.HTTP_200_OK


async def test_user_quota_override_higher(client, regular_user, auth_headers, db_session):
    """A per-user override higher than the instance default applies."""
    _set_default_quota(client, 8)
    regular_user.upload_quota = 1024
    await db_session.flush()

    response = _upload(client, auth_headers(regular_user), content=b"x" * 32)
    assert response.status_code == status.HTTP_200_OK


async def test_user_quota_override_lower(client, regular_user, auth_headers, db_session):
    """A per-user override lower than the instance default applies."""
    _set_default_quota(client, 10 * 1024)
    regular_user.upload_quota = 8
    await db_session.flush()

    response = _upload(client, auth_headers(regular_user), content=b"x" * 32)
    assert response.status_code == status.HTTP_403_FORBIDDEN


async def test_user_quota_unlimited_override(client, regular_user, auth_headers, db_session):
    """The ``-1`` sentinel lifts the quota even when a default is configured."""
    _set_default_quota(client, 8)
    regular_user.upload_quota = UPLOAD_QUOTA_UNLIMITED
    await db_session.flush()

    response = _upload(client, auth_headers(regular_user), content=b"x" * 32)
    assert response.status_code == status.HTTP_200_OK


async def test_user_quota_override_without_default(client, regular_user, auth_headers, db_session):
    """A per-user quota applies even when the instance default is unset."""
    regular_user.upload_quota = 8
    await db_session.flush()

    response = _upload(client, auth_headers(regular_user), content=b"x" * 32)
    assert response.status_code == status.HTTP_403_FORBIDDEN


async def test_user_quota_zero_blocks_uploads(client, regular_user, auth_headers, db_session):
    """A zero-byte override blocks every new upload."""
    regular_user.upload_quota = 0
    await db_session.flush()

    response = _upload(client, auth_headers(regular_user), content=b"x")
    assert response.status_code == status.HTTP_403_FORBIDDEN


def test_bulk_upload_quota_errors_per_file(client, regular_user, auth_headers):
    """Bulk uploads flag only the files that exceed the quota."""
    _set_default_quota(client, 16)
    files = [
        ("files", ("one.bin", io.BytesIO(b"a" * 10), "application/octet-stream")),
        ("files", ("two.bin", io.BytesIO(b"b" * 10), "application/octet-stream")),
    ]
    response = client.post(
        "/api/v1/files/upload/bulk",
        files=files,
        headers=auth_headers(regular_user),
    )
    assert response.status_code == status.HTTP_200_OK
    results = response.json()
    assert results[0]["stored_file"] is not None
    assert results[1]["error"] == "Upload quota exceeded"


def test_entity_image_upload_enforces_quota(client, regular_user, auth_headers):
    """Entity image uploads count against the uploader's quota."""
    headers = auth_headers(regular_user)
    response = client.post(
        "/api/v1/libraries/",
        params={"visibility": "private"},
        json={"name": "Images"},
        headers=headers,
    )
    assert response.status_code == status.HTTP_201_CREATED
    library_id = response.json()["id"]

    _set_default_quota(client, 8)
    image = client.post(
        f"/api/v1/libraries/{library_id}/image",
        files={"file": ("image.jpg", io.BytesIO(b"fake image data"), "image/jpeg")},
        headers=headers,
    )
    assert image.status_code == status.HTTP_403_FORBIDDEN
    assert image.json()["detail"] == "Upload quota exceeded"


def test_set_user_quota_endpoint(client, admin_user, regular_user, auth_headers):
    """Admins can set, clear, and lift per-user quota overrides."""
    headers = auth_headers(admin_user)

    response = client.post(
        f"/api/v1/admin/users/{regular_user.id}/quota",
        json={"quota": 2048},
        headers=headers,
    )
    assert response.status_code == status.HTTP_200_OK
    assert response.json()["upload_quota"] == 2048

    response = client.post(
        f"/api/v1/admin/users/{regular_user.id}/quota",
        json={"quota": UPLOAD_QUOTA_UNLIMITED},
        headers=headers,
    )
    assert response.status_code == status.HTTP_200_OK
    assert response.json()["upload_quota"] == UPLOAD_QUOTA_UNLIMITED

    response = client.post(
        f"/api/v1/admin/users/{regular_user.id}/quota",
        json={"quota": None},
        headers=headers,
    )
    assert response.status_code == status.HTTP_200_OK
    assert response.json()["upload_quota"] is None


def test_set_user_quota_endpoint_validation(client, admin_user, regular_user, auth_headers):
    """Values below the unlimited sentinel are rejected."""
    response = client.post(
        f"/api/v1/admin/users/{regular_user.id}/quota",
        json={"quota": -2},
        headers=auth_headers(admin_user),
    )
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


def test_set_user_quota_endpoint_unknown_user(client, admin_user, auth_headers):
    """Setting a quota for a missing user returns 404."""
    response = client.post(
        "/api/v1/admin/users/00000000-0000-0000-0000-000000000000/quota",
        json={"quota": 1},
        headers=auth_headers(admin_user),
    )
    assert response.status_code == status.HTTP_404_NOT_FOUND


def test_set_user_quota_endpoint_requires_admin(client, regular_user, other_user, auth_headers):
    """Non-admin users cannot set quota overrides."""
    response = client.post(
        f"/api/v1/admin/users/{other_user.id}/quota",
        json={"quota": 1},
        headers=auth_headers(regular_user),
    )
    assert response.status_code == status.HTTP_403_FORBIDDEN


async def test_users_me_reports_quota_status(client, regular_user, auth_headers, db_session):
    """``/users/me`` exposes the effective quota and current usage."""
    headers = auth_headers(regular_user)

    me = client.get("/api/v1/users/me", headers=headers)
    assert me.status_code == status.HTTP_200_OK
    assert me.json()["upload_quota"] is None
    assert me.json()["upload_quota_used"] == 0

    _set_default_quota(client, 1024)
    assert _upload(client, headers, content=b"x" * 16).status_code == status.HTTP_200_OK

    me = client.get("/api/v1/users/me", headers=headers)
    assert me.json()["upload_quota"] == 1024
    assert me.json()["upload_quota_used"] == 16


async def test_get_upload_usage_only_counts_user_uploads(client, regular_user, auth_headers, db_session):
    """Derived rows under non-``files`` prefixes do not count as usage."""
    headers = auth_headers(regular_user)
    _upload(client, headers, content=b"x" * 16)

    # A row under another prefix (e.g. a generated cover) is not usage.
    db_session.add(
        StoredFile(
            storage_path="covers/ab/cd/abcdef",
            storage_backend="local",
            content_type="image/jpeg",
            size=10_000,
            sha256="deadbeef" * 8,
            owner_id=str(regular_user.id),
            visibility="private",
        )
    )
    await db_session.flush()

    assert await get_upload_usage(db_session, str(regular_user.id)) == 16


async def test_upload_quota_user_delete_recheck(client, regular_user, auth_headers, db_session):
    """Files are counted from ``StoredFile`` rows owned by the user."""
    headers = auth_headers(regular_user)
    _set_default_quota(client, 1024)

    _upload(client, headers, content=b"x" * 16)
    result = await db_session.execute(select(StoredFile).where(StoredFile.owner_id == str(regular_user.id)))
    assert sum(f.size for f in result.scalars()) == 16


async def test_user_upload_quota_rejects_invalid_sentinels(db_session, regular_user):
    """The model check constraint rejects values below the unlimited sentinel."""
    regular_user.upload_quota = -2
    with pytest.raises(IntegrityError):
        await db_session.flush()
