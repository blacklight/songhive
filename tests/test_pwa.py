"""
Tests for the Progressive Web App manifest endpoint.
"""

from fastapi import status


def test_pwa_manifest_default(client):
    """GET /manifest.webmanifest returns a valid manifest."""
    response = client.get("/manifest.webmanifest")
    assert response.status_code == status.HTTP_200_OK
    assert response.headers["content-type"].startswith("application/manifest+json")

    data = response.json()
    assert data["name"] == "Songhive"
    assert data["short_name"] == "Songhive"
    assert data["start_url"] == "/"
    assert data["scope"] == "/"
    assert data["display"] == "standalone"
    assert data["orientation"] == "any"
    assert data["theme_color"] == "#f9f8f7"
    assert data["background_color"] == "#f9f8f7"

    icons = data["icons"]
    srcs = {icon["src"] for icon in icons}
    assert "/pwa/pwa-192x192.png" in srcs
    assert "/pwa/pwa-512x512.png" in srcs
    assert "/pwa/maskable-192x192.png" in srcs
    assert "/pwa/maskable-512x512.png" in srcs
    assert "/pwa/apple-touch-icon.png" in srcs


def test_pwa_manifest_uses_instance_name(client):
    """The manifest name follows the configured instance name."""
    client.app.state.config.federation.instance_name = "My Hive"

    response = client.get("/manifest.webmanifest")
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["name"] == "My Hive"
    assert data["short_name"] == "My Hive"


def test_pwa_manifest_short_name_is_truncated(client):
    """A long instance name produces a short_name of at most 12 characters."""
    client.app.state.config.federation.instance_name = "A Very Long Instance Name"

    response = client.get("/manifest.webmanifest")
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["name"] == "A Very Long Instance Name"
    assert len(data["short_name"]) <= 12
    assert data["short_name"].startswith("A Very Long")


def test_pwa_manifest_dark_theme(client):
    """The ?theme query selects the dark color scheme."""
    response = client.get("/manifest.webmanifest?theme=dark")
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["theme_color"] == "#1f2927"
    assert data["background_color"] == "#1f2927"


def test_pwa_manifest_accent_color(client):
    """The ?accent query overrides the manifest theme_color."""
    response = client.get("/manifest.webmanifest?theme=light&accent=%23ff0000")
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["theme_color"] == "#ff0000"
    assert data["background_color"] == "#f9f8f7"


def test_pwa_manifest_rejects_invalid_theme(client):
    """An unknown ?theme value is rejected with a validation error."""
    response = client.get("/manifest.webmanifest?theme=purple")
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


def test_pwa_manifest_json_alias(client):
    """GET /manifest.json returns the same manifest as regular JSON."""
    response = client.get("/manifest.json")
    assert response.status_code == status.HTTP_200_OK
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["name"] == "Songhive"
