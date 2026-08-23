"""
Tests for RFC 7807 Problem Details error responses.
"""

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from songhive.api.errors import install_error_handlers
from songhive.config.schema import ServerConfig, SonghiveConfig


class _Item(BaseModel):
    """A minimal request body model used for validation error tests."""

    name: str


def _validation_app(config: SonghiveConfig) -> FastAPI:
    """Return a test app with a body-validated route."""
    app = FastAPI()
    app.state.config = config

    @app.post("/items")
    async def _create_item(item: _Item):
        return item

    install_error_handlers(app)
    return app


def _boom_app(config: SonghiveConfig) -> FastAPI:
    """Return a test app with a route that always raises an exception."""
    app = FastAPI()
    app.state.config = config

    @app.get("/boom")
    async def _boom():
        raise RuntimeError("something went wrong")

    install_error_handlers(app)
    return app


def test_404_unknown_route(client):
    """A request to an unknown route returns a 404 problem detail."""
    response = client.get("/api/v1/does-not-exist/")
    assert response.status_code == 404

    body = response.json()
    assert body["type"] == "about:blank"
    assert body["title"] == "Not Found"
    assert body["status"] == 404
    assert body["detail"] == "Not Found"
    assert body["instance"] == "/api/v1/does-not-exist/"
    assert response.headers["content-type"].startswith("application/problem+json")


def test_422_validation_error(config):
    """An invalid request body returns a 422 problem detail with the errors list."""
    app = _validation_app(config)
    with TestClient(app) as client:
        response = client.post("/items", json={})

    assert response.status_code == 422
    body = response.json()
    assert body["type"] == "about:blank"
    assert body["title"] == "Validation failed"
    assert body["status"] == 422
    assert "detail" in body
    assert "errors" in body
    assert isinstance(body["errors"], list)
    assert response.headers["content-type"].startswith("application/problem+json")


def test_401_unauthenticated(client):
    """An unauthenticated request to a protected route returns 401 with WWW-Authenticate."""
    response = client.get("/api/v1/history/")
    assert response.status_code == 401

    body = response.json()
    assert body["type"] == "about:blank"
    assert body["title"] == "Unauthorized"
    assert body["status"] == 401
    assert body["detail"] == "Not authenticated"
    assert body["instance"] == "/api/v1/history/"
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.headers["content-type"].startswith("application/problem+json")


def test_500_debug_exposes_message(config):
    """The catch-all handler exposes the exception message in debug mode."""
    app = _boom_app(config)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/boom")

    assert response.status_code == 500
    body = response.json()
    assert body["type"] == "about:blank"
    assert body["title"] == "Internal server error"
    assert body["status"] == 500
    assert body["detail"] == "something went wrong"
    assert response.headers["content-type"].startswith("application/problem+json")


def test_500_prod_hides_message(config):
    """The catch-all handler hides the exception message when not in debug mode."""
    prod_config = config.model_copy(update={"server": ServerConfig(debug=False)})
    app = _boom_app(prod_config)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/boom")

    assert response.status_code == 500
    body = response.json()
    assert body["type"] == "about:blank"
    assert body["title"] == "Internal server error"
    assert body["status"] == 500
    assert "something went wrong" not in body["detail"]
    assert body["detail"] == "An internal server error occurred."
    assert response.headers["content-type"].startswith("application/problem+json")
