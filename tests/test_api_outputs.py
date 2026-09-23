"""API tests for output stream routes."""

import pytest
from fastapi import status

from songhive.models.output_stream import OutputStream


@pytest.mark.asyncio
async def test_list_providers(client, regular_user, auth_headers, monkeypatch):
    """GET /outputs/providers lists the fake provider when allowed."""
    client.app.state.config.streams.allow_user_created_outputs = True
    response = client.get(
        "/api/v1/outputs/providers",
        headers=auth_headers(regular_user),
    )
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    types = {p["provider_type"] for p in data}
    assert "fake" in types
    fake = next(p for p in data if p["provider_type"] == "fake")
    assert fake["user_configurable"] is True
    assert fake["can_create"] is True


@pytest.mark.asyncio
async def test_create_output_redacts_secrets(client, regular_user, auth_headers, db_session):
    """POST /outputs encrypts secrets and redacts them in the response."""
    client.app.state.config.streams.allow_user_created_outputs = True
    response = client.post(
        "/api/v1/outputs",
        headers=auth_headers(regular_user),
        json={
            "provider_type": "fake",
            "name": "my output",
            "config": {"items": {}, "secret": "hunter2"},
        },
    )
    assert response.status_code == status.HTTP_201_CREATED
    data = response.json()
    assert data["provider_type"] == "fake"
    assert data["name"] == "my output"
    assert data["config"]["secret"] == "<redacted>"
    assert data["capabilities"]["pause_supported"] is True


@pytest.mark.asyncio
async def test_patch_preserves_redacted_sentinel(client, regular_user, auth_headers, db_session, config):
    """PATCH with the redacted sentinel does not clobber the stored secret."""
    client.app.state.config.streams.allow_user_created_outputs = True
    create_resp = client.post(
        "/api/v1/outputs",
        headers=auth_headers(regular_user),
        json={
            "provider_type": "fake",
            "name": "patch me",
            "config": {"items": {}, "secret": "hunter2"},
        },
    )
    output_id = create_resp.json()["id"]

    patch_resp = client.patch(
        f"/api/v1/outputs/{output_id}",
        headers=auth_headers(regular_user),
        json={
            "name": "patched name",
            "config": {"items": {}, "secret": "<redacted>"},
        },
    )
    assert patch_resp.status_code == status.HTTP_200_OK
    data = patch_resp.json()
    assert data["name"] == "patched name"
    assert data["config"]["secret"] == "<redacted>"

    # Verify the stored secret was not overwritten.
    from songhive.services.secrets import decrypt_json

    output = await db_session.get(OutputStream, output_id)
    await db_session.refresh(output)
    decrypted = decrypt_json(output.config)
    assert decrypted["secret"] == "hunter2"


@pytest.mark.asyncio
async def test_owner_scoping(client, regular_user, other_user, auth_headers, config):
    """User B cannot read or patch user A's output."""
    client.app.state.config.streams.allow_user_created_outputs = True
    create_resp = client.post(
        "/api/v1/outputs",
        headers=auth_headers(regular_user),
        json={"provider_type": "fake", "name": "owner-test", "config": {"items": {}}},
    )
    output_id = create_resp.json()["id"]

    get_resp = client.get(
        f"/api/v1/outputs/{output_id}",
        headers=auth_headers(other_user),
    )
    assert get_resp.status_code == status.HTTP_404_NOT_FOUND

    patch_resp = client.patch(
        f"/api/v1/outputs/{output_id}",
        headers=auth_headers(other_user),
        json={"name": "stolen"},
    )
    assert patch_resp.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.asyncio
async def test_http_output_stream_url_and_redaction(client, regular_user, auth_headers):
    """A native HTTP output reports its mount URL and redacts the listen token."""
    client.app.state.config.streams.allow_user_created_outputs = True
    response = client.post(
        "/api/v1/outputs",
        headers=auth_headers(regular_user),
        json={
            "provider_type": "http",
            "name": "my mount",
            "config": {
                "mount": "radio",
                "format": "mp3",
                "bitrate": "128k",
                "sample_rate": 44100,
                "listen_token": "s3cret",
            },
        },
    )
    assert response.status_code == status.HTTP_201_CREATED
    data = response.json()
    assert data["provider_type"] == "http"
    assert data["stream_url"] == "/streams/radio"
    assert data["config"]["listen_token"] == "<redacted>"
    assert data["capabilities"]["multi_listener"] is True


@pytest.mark.asyncio
async def test_http_mount_must_be_unique(client, regular_user, auth_headers):
    """Creating a second HTTP output with the same mount slug is rejected."""
    client.app.state.config.streams.allow_user_created_outputs = True
    cfg = {"mount": "radio", "format": "mp3", "bitrate": "128k", "sample_rate": 44100}
    first = client.post(
        "/api/v1/outputs",
        headers=auth_headers(regular_user),
        json={"provider_type": "http", "name": "one", "config": cfg},
    )
    assert first.status_code == status.HTTP_201_CREATED

    # Equivalent slugs (leading/trailing slashes) also conflict.
    second = client.post(
        "/api/v1/outputs",
        headers=auth_headers(regular_user),
        json={"provider_type": "http", "name": "two", "config": {**cfg, "mount": "/radio/"}},
    )
    assert second.status_code == status.HTTP_409_CONFLICT

    # PATCHing an unrelated output onto the taken mount is rejected too.
    other = client.post(
        "/api/v1/outputs",
        headers=auth_headers(regular_user),
        json={"provider_type": "http", "name": "other", "config": {**cfg, "mount": "other"}},
    )
    other_id = other.json()["id"]
    patch = client.patch(
        f"/api/v1/outputs/{other_id}",
        headers=auth_headers(regular_user),
        json={"config": {**cfg, "mount": "radio"}},
    )
    assert patch.status_code == status.HTTP_409_CONFLICT

    # PATCHing the same output with its own mount is fine.
    first_id = first.json()["id"]
    patch_ok = client.patch(
        f"/api/v1/outputs/{first_id}",
        headers=auth_headers(regular_user),
        json={"config": cfg},
    )
    assert patch_ok.status_code == status.HTTP_200_OK


@pytest.mark.asyncio
async def test_validate_output(client, regular_user, auth_headers, config):
    """POST /outputs/{id}/validate returns capabilities."""
    client.app.state.config.streams.allow_user_created_outputs = True
    create_resp = client.post(
        "/api/v1/outputs",
        headers=auth_headers(regular_user),
        json={"provider_type": "fake", "name": "validate-me", "config": {"items": {}}},
    )
    output_id = create_resp.json()["id"]

    response = client.post(
        f"/api/v1/outputs/{output_id}/validate",
        headers=auth_headers(regular_user),
    )
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["ok"] is True
    assert data["capabilities"]["pause_supported"] is True
