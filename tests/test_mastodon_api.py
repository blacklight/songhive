"""
Mastodon-compatible API binding tests.

These tests assert that pubby's read-only Mastodon API routes are bound
without shadowing Songhive's existing /api/v1 routes.
"""

from pathlib import Path

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from songhive.api.app import create_app
from songhive.api.deps import get_db
from songhive.models.base import init_db


@pytest.fixture
def fed_config(config, tmp_path):
    """Return a federation-enabled config backed by the test database."""
    fed = config.model_copy(deep=True)
    fed.federation.enabled = True
    fed.federation.instance_domain = "music.example.com"
    fed.federation.instance_name = "Songhive"
    fed.federation.instance_description = "A federated music instance"
    fed.federation.private_key_path = Path(fed.storage.local_path).parent / "actor.pem"
    return fed


@pytest.fixture
def fed_app(fed_config, engine):
    """Create a federation-enabled test application."""
    init_db(engine=engine, force=True)
    return create_app(fed_config)


@pytest.fixture
def fed_client(fed_app, db_session, fake_redis_server, monkeypatch):
    """Create a test client for the federation-enabled app."""
    from fakeredis.aioredis import FakeRedis

    def _get_redis_client(_config):
        return FakeRedis(server=fake_redis_server, decode_responses=True)

    monkeypatch.setattr("songhive.api.app.get_redis_client", _get_redis_client)

    async def _db():
        yield db_session

    with TestClient(fed_app) as client:
        client.app.dependency_overrides[get_db] = _db
        yield client
        client.app.dependency_overrides.pop(get_db, None)


async def test_mastodon_instance_v1(fed_client):
    """GET /api/v1/instance returns Mastodon-compatible instance metadata."""
    response = fed_client.get("/api/v1/instance")
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["title"] == "Songhive"
    assert "songhive" in data["version"].lower()
    assert "stats" in data


async def test_mastodon_instance_v2(fed_client):
    """GET /api/v2/instance returns Mastodon-compatible instance metadata."""
    response = fed_client.get("/api/v2/instance")
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["title"] == "Songhive"
    assert data["domain"] == "music.example.com"


async def test_mastodon_instance_peers(fed_client):
    """GET /api/v1/instance/peers returns a list of known peer domains."""
    response = fed_client.get("/api/v1/instance/peers")
    assert response.status_code == status.HTTP_200_OK
    assert isinstance(response.json(), list)


async def test_mastodon_account_lookup(fed_client):
    """GET /api/v1/accounts/lookup resolves the instance actor."""
    response = fed_client.get("/api/v1/accounts/lookup?acct=songhive@music.example.com")
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["username"] == "songhive"
    assert data["acct"] == "songhive@music.example.com"


async def test_mastodon_account_lookup_bare_username(fed_client):
    """GET /api/v1/accounts/lookup accepts a bare username."""
    response = fed_client.get("/api/v1/accounts/lookup?acct=songhive")
    assert response.status_code == status.HTTP_200_OK
    assert response.json()["username"] == "songhive"


async def test_mastodon_account_get(fed_client):
    """GET /api/v1/accounts/:id returns the instance actor account."""
    response = fed_client.get("/api/v1/accounts/1")
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["id"] == "1"
    assert data["username"] == "songhive"


async def test_mastodon_account_statuses(fed_client):
    """GET /api/v1/accounts/:id/statuses returns a list of statuses."""
    response = fed_client.get("/api/v1/accounts/1/statuses")
    assert response.status_code == status.HTTP_200_OK
    assert isinstance(response.json(), list)


async def test_mastodon_route_does_not_shadow_songhive_routes(fed_client):
    """Songhive's existing /api/v1/tracks route still resolves."""
    response = fed_client.get("/api/v1/tracks")
    assert response.status_code == status.HTTP_200_OK
    assert "items" not in response.json()
