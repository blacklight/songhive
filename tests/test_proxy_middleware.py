"""Tests for the X-Forwarded-Proto proxy middleware."""

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from songhive.api.middleware.proxy import ForwardedProtoMiddleware


@pytest.fixture
def proxy_app():
    """Return a small FastAPI app with the scheme middleware and a slash route."""
    app = FastAPI()
    return app


def _client_for(app: FastAPI, trusted_hops: int) -> TestClient:
    """Build a TestClient with the middleware configured for the given trust level."""
    app.add_middleware(ForwardedProtoMiddleware, trusted_hops=trusted_hops)

    @app.get("/tags/")
    def list_tags(request: Request):
        return {"scheme": request.url.scheme, "url": str(request.url)}

    return TestClient(app, follow_redirects=False)


def test_trusted_forwarded_proto_https(proxy_app):
    """A trusted X-Forwarded-Proto header makes redirects use https."""
    client = _client_for(proxy_app, trusted_hops=1)
    response = client.get("/tags", headers={"X-Forwarded-Proto": "https"})
    assert response.status_code == 307
    assert response.headers["location"].startswith("https://")


def test_untrusted_forwarded_proto_is_ignored(proxy_app):
    """When trusted_hops is 0, X-Forwarded-Proto is ignored and the default scheme is used."""
    client = _client_for(proxy_app, trusted_hops=0)
    response = client.get("/tags", headers={"X-Forwarded-Proto": "https"})
    assert response.status_code == 307
    assert response.headers["location"].startswith("http://")


def test_no_forwarded_proto_uses_default_scheme(proxy_app):
    """Without the header the request keeps the default http scheme."""
    client = _client_for(proxy_app, trusted_hops=1)
    response = client.get("/tags/")
    assert response.status_code == 200
    assert response.json()["scheme"] == "http"


def test_forwarded_proto_scheme_visible_to_route(proxy_app):
    """The trusted scheme is reflected in request.url.scheme."""
    client = _client_for(proxy_app, trusted_hops=1)
    response = client.get("/tags/", headers={"X-Forwarded-Proto": "https"})
    assert response.status_code == 200
    assert response.json()["scheme"] == "https"
    assert response.json()["url"].startswith("https://")
