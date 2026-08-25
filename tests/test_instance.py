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
