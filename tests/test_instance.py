"""
Tests for the public instance metadata endpoints.
"""

from fastapi import status


async def test_instance_v1_without_federation(client):
    """GET /api/v1/instance works when federation is disabled."""
    response = client.get("/api/v1/instance")
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["title"] == "Songhive"
    assert data["description"] == "A federated music sharing service"
    assert "songhive" in data["version"].lower()
    assert "stats" in data
    assert data["registrations"] is True
    assert data["approval_required"] is False
    assert data["invites_enabled"] is False


async def test_instance_v2_without_federation(client):
    """GET /api/v2/instance works when federation is disabled."""
    response = client.get("/api/v2/instance")
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["title"] == "Songhive"
    assert data["domain"] == "testserver"
    assert "songhive" in data["version"].lower()


async def test_instance_peers_without_federation(client):
    """GET /api/v1/instance/peers returns an empty list when federation is off."""
    response = client.get("/api/v1/instance/peers")
    assert response.status_code == status.HTTP_200_OK
    assert response.json() == []


async def test_instance_v1_lists_admin_staff_accounts(client, admin_user, regular_user):
    """GET /api/v1/instance exposes active admins as staff accounts."""
    response = client.get("/api/v1/instance")
    assert response.status_code == status.HTTP_200_OK

    data = response.json()
    staff = data["staff_accounts"]
    assert len(staff) == 1
    admin = staff[0]
    assert admin["username"] == "admin"
    assert admin["display_name"] == "admin"
    # Without federation the handle falls back to the bare username.
    assert admin["acct"] == "admin"
    assert admin["url"].endswith("/@admin")
    assert admin["actor_url"] is None


async def test_instance_v1_staff_accounts_skip_inactive_admins(client, make_user):
    """Inactive admins are not advertised as staff accounts."""
    await make_user("gone", role="admin", is_active=False)

    response = client.get("/api/v1/instance")
    assert response.status_code == status.HTTP_200_OK
    assert response.json()["staff_accounts"] == []


async def test_instance_v1_contact_account_is_first_admin(client, admin_user):
    """contact_account mirrors the first active admin as a Mastodon account."""
    response = client.get("/api/v1/instance")
    assert response.status_code == status.HTTP_200_OK

    account = response.json()["contact_account"]
    assert account["username"] == "admin"
    assert account["acct"] == "admin"
    assert account["url"].endswith("/@admin")


async def test_instance_v1_contact_account_none_without_admins(client):
    """contact_account is null when the instance has no active admins."""
    response = client.get("/api/v1/instance")
    assert response.status_code == status.HTTP_200_OK
    assert response.json()["contact_account"] is None


async def test_instance_v1_contact_from_config(client):
    """Configured contact details are exposed on the instance endpoint."""
    client.app.state.config.federation.contact_name = "Jane Admin"
    client.app.state.config.federation.contact_email = "jane@example.com"
    client.app.state.config.federation.contact_url = "https://example.com/jane"

    response = client.get("/api/v1/instance")
    assert response.status_code == status.HTTP_200_OK

    data = response.json()
    assert data["email"] == "jane@example.com"
    assert data["contact"] == {
        "name": "Jane Admin",
        "email": "jane@example.com",
        "url": "https://example.com/jane",
    }


async def test_instance_v1_contact_null_when_unconfigured(client):
    """The contact object is null when no contact person is configured."""
    response = client.get("/api/v1/instance")
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["email"] == ""
    assert data["contact"] is None


async def test_instance_v2_contact_and_staff(client, admin_user):
    """GET /api/v2/instance exposes contact email, account and staff list."""
    client.app.state.config.federation.contact_email = "staff@example.com"

    response = client.get("/api/v2/instance")
    assert response.status_code == status.HTTP_200_OK

    data = response.json()
    assert data["contact"]["email"] == "staff@example.com"
    assert data["contact"]["account"]["username"] == "admin"
    assert [a["username"] for a in data["staff_accounts"]] == ["admin"]
