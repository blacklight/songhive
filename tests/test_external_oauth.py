"""Tests for the reusable external-provider OAuth flow.

The Dropbox provider is used as the concrete OAuth provider; the token
exchange is exercised against a fake ``httpx.AsyncClient`` so no network or
credentials are needed.
"""

import json
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import status

from songhive.external import oauth as oauth_module
from songhive.models.external_library import ExternalLibrary
from songhive.models.library import Library
from songhive.services import secrets as secrets_service

_PENDING_PREFIX = "songhive:external-oauth:pending:"


class _FakeTokenResponse:
    """Mimics an httpx ``Response`` returned by the token endpoint."""

    def __init__(self, status_code: int = 200, payload: dict | None = None):
        self.status_code = status_code
        self.is_error = status_code >= 400
        self._payload = payload or {}

    def json(self) -> dict:
        return self._payload


class _FakeTokenClient:
    """In-memory stand-in for ``httpx.AsyncClient`` in the OAuth module."""

    posts: list[dict] = []
    next_response = _FakeTokenResponse(
        200,
        {
            "access_token": "at-1",
            "refresh_token": "rt-1",
            "account_id": "dbid:42",
            "token_type": "bearer",
            "expires_in": 14400,
        },
    )

    def __init__(self, **kwargs):
        self.kwargs = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url: str, **kwargs):
        self.__class__.posts.append({"url": url, **kwargs})
        return self.__class__.next_response


@pytest.fixture
def token_exchange(monkeypatch: pytest.MonkeyPatch):
    """Patch the OAuth module's httpx client with a fake token endpoint."""
    _FakeTokenClient.posts = []
    _FakeTokenClient.next_response = _FakeTokenResponse(
        200,
        {
            "access_token": "at-1",
            "refresh_token": "rt-1",
            "account_id": "dbid:42",
            "token_type": "bearer",
            "expires_in": 14400,
        },
    )
    monkeypatch.setattr(oauth_module.httpx, "AsyncClient", _FakeTokenClient)
    return _FakeTokenClient


def _begin(client, headers, **overrides):
    body = {
        "provider_type": "dropbox",
        "config": {"app_key": "dbx-key", "app_secret": "dbx-secret"},
        "return_to": "/settings/external-libraries/new",
    }
    body.update(overrides)
    return client.post(
        "/api/v1/external-libraries/oauth/begin",
        json=body,
        headers=headers,
    )


async def _make_library(db_session, owner, scope="user", provider_type="dropbox", config=None):
    """Persist a Library + ExternalLibrary pair for OAuth begin tests."""
    library = Library(name="External", owner_id=owner.id, visibility="private")
    db_session.add(library)
    await db_session.flush()

    external = ExternalLibrary(
        library_id=str(library.id),
        provider_type=provider_type,
        scope=scope,
        config=secrets_service.encrypt_json(config or {}),
        created_by_id=owner.id,
    )
    db_session.add(external)
    await db_session.flush()
    return external


@pytest.mark.asyncio
async def test_begin_requires_auth(client):
    response = client.post(
        "/api/v1/external-libraries/oauth/begin",
        json={"provider_type": "dropbox", "config": {"app_key": "k"}},
    )
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
async def test_begin_returns_authorize_url(client, admin_user, auth_headers):
    response = _begin(client, auth_headers(admin_user))
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["state"]

    parsed = urlsplit(data["authorize_url"])
    assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == "https://www.dropbox.com/oauth2/authorize"
    params = parse_qs(parsed.query)
    assert params["response_type"] == ["code"]
    assert params["client_id"] == ["dbx-key"]
    assert params["state"] == [data["state"]]
    assert params["token_access_type"] == ["offline"]
    assert params["code_challenge_method"] == ["S256"]
    assert params["code_challenge"][0]
    assert params["redirect_uri"] == ["http://testserver/api/v1/external-libraries/oauth/callback"]


@pytest.mark.asyncio
async def test_begin_stores_pending_in_redis(client, admin_user, auth_headers, fake_redis):
    response = _begin(client, auth_headers(admin_user))
    assert response.status_code == status.HTTP_200_OK
    state = response.json()["state"]

    raw = await fake_redis.get(f"{_PENDING_PREFIX}{state}")
    assert raw
    pending = json.loads(raw)
    assert pending["provider_type"] == "dropbox"
    assert pending["user_id"] == str(admin_user.id)
    assert pending["client_id"] == "dbx-key"
    assert pending["client_secret"] == "dbx-secret"
    assert pending["code_verifier"]
    assert pending["return_to"] == "/settings/external-libraries/new"
    assert pending["redirect_uri"].endswith("/api/v1/external-libraries/oauth/callback")


@pytest.mark.asyncio
async def test_begin_requires_client_id(client, admin_user, auth_headers):
    response = _begin(client, auth_headers(admin_user), config={})
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


@pytest.mark.asyncio
async def test_begin_rejects_non_oauth_provider(client, admin_user, auth_headers):
    response = _begin(client, auth_headers(admin_user), provider_type="s3")
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT

    response = _begin(client, auth_headers(admin_user), provider_type="bogus")
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


@pytest.mark.asyncio
async def test_begin_non_admin_blocked_when_user_libraries_disabled(client, regular_user, auth_headers):
    response = _begin(client, auth_headers(regular_user))
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT

    client.app.state.config.external_libraries.allow_user_created_libraries = True
    response = _begin(client, auth_headers(regular_user))
    assert response.status_code == status.HTTP_200_OK


@pytest.mark.asyncio
async def test_begin_existing_library_resolves_redacted(client, regular_user, auth_headers, db_session, fake_redis):
    """ "<redacted>" secrets in the form resolve against the stored config."""
    external = await _make_library(
        db_session,
        regular_user,
        config={"app_key": "stored-key", "app_secret": "stored-secret", "root": "Music"},
    )

    response = _begin(
        client,
        auth_headers(regular_user),
        config={"app_key": "<redacted>", "app_secret": "<redacted>"},
        external_library_id=str(external.id),
    )
    assert response.status_code == status.HTTP_200_OK
    state = response.json()["state"]
    pending = json.loads(await fake_redis.get(f"{_PENDING_PREFIX}{state}"))
    assert pending["client_id"] == "stored-key"
    assert pending["client_secret"] == "stored-secret"


@pytest.mark.asyncio
async def test_begin_foreign_user_library_denied(client, regular_user, other_user, auth_headers, db_session):
    external = await _make_library(db_session, regular_user)
    response = _begin(
        client,
        auth_headers(other_user),
        external_library_id=str(external.id),
    )
    assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.asyncio
async def test_begin_admin_library_requires_admin(client, regular_user, auth_headers, db_session):
    external = await _make_library(db_session, regular_user, scope="admin")
    response = _begin(
        client,
        auth_headers(regular_user),
        external_library_id=str(external.id),
    )
    assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.asyncio
async def test_begin_provider_mismatch_rejected(client, admin_user, auth_headers, db_session):
    external = await _make_library(db_session, admin_user, provider_type="s3")
    response = _begin(
        client,
        auth_headers(admin_user),
        external_library_id=str(external.id),
    )
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


@pytest.mark.asyncio
async def test_callback_completes_flow_and_claim_returns_config(client, admin_user, auth_headers, token_exchange):
    begin = _begin(client, auth_headers(admin_user))
    state = begin.json()["state"]

    callback = client.get(
        f"/api/v1/external-libraries/oauth/callback?state={state}&code=authcode",
        follow_redirects=False,
    )
    assert callback.status_code in (302, 303, 307)
    location = urlsplit(callback.headers["location"])
    assert location.path == "/settings/external-libraries/new"
    params = parse_qs(location.query)
    assert params["oauth_state"] == [state]
    assert params["oauth_provider"] == ["dropbox"]

    # The exchange sent the code, client credentials, and PKCE verifier.
    assert len(_FakeTokenClient.posts) == 1
    exchange = _FakeTokenClient.posts[0]
    assert exchange["url"] == "https://api.dropboxapi.com/oauth2/token"
    form = exchange["data"]
    assert form["grant_type"] == "authorization_code"
    assert form["code"] == "authcode"
    assert form["client_id"] == "dbx-key"
    assert form["client_secret"] == "dbx-secret"
    assert form["code_verifier"]

    claim = client.post(
        "/api/v1/external-libraries/oauth/claim",
        json={"state": state},
        headers=auth_headers(admin_user),
    )
    assert claim.status_code == status.HTTP_200_OK
    payload = claim.json()
    assert payload["provider_type"] == "dropbox"
    assert payload["config"]["access_token"] == "at-1"
    assert payload["config"]["refresh_token"] == "rt-1"
    assert payload["config"]["account_id"] == "dbid:42"
    # Client credentials are carried back so the SPA never stashes secrets.
    assert payload["config"]["app_key"] == "dbx-key"
    assert payload["config"]["app_secret"] == "dbx-secret"

    # Results are one-time.
    second = client.post(
        "/api/v1/external-libraries/oauth/claim",
        json={"state": state},
        headers=auth_headers(admin_user),
    )
    assert second.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.asyncio
async def test_claim_by_other_user_does_not_consume(client, admin_user, other_user, auth_headers, token_exchange):
    begin = _begin(client, auth_headers(admin_user))
    state = begin.json()["state"]
    client.get(
        f"/api/v1/external-libraries/oauth/callback?state={state}&code=authcode",
        follow_redirects=False,
    )

    other = client.post(
        "/api/v1/external-libraries/oauth/claim",
        json={"state": state},
        headers=auth_headers(other_user),
    )
    assert other.status_code == status.HTTP_404_NOT_FOUND

    owner = client.post(
        "/api/v1/external-libraries/oauth/claim",
        json={"state": state},
        headers=auth_headers(admin_user),
    )
    assert owner.status_code == status.HTTP_200_OK


@pytest.mark.asyncio
async def test_callback_provider_error_redirects(client, admin_user, auth_headers):
    begin = _begin(client, auth_headers(admin_user))
    state = begin.json()["state"]

    callback = client.get(
        f"/api/v1/external-libraries/oauth/callback?state={state}&error=access_denied&error_description=User+denied",
        follow_redirects=False,
    )
    assert callback.status_code in (302, 303, 307)
    location = urlsplit(callback.headers["location"])
    assert location.path == "/settings/external-libraries/new"
    params = parse_qs(location.query)
    assert params["oauth_error"] == ["User denied"]
    # The state is echoed so the SPA can restore the stashed form.
    assert params["oauth_state"] == [state]


@pytest.mark.asyncio
async def test_callback_unknown_state(client, token_exchange):
    response = client.get(
        "/api/v1/external-libraries/oauth/callback?state=bogus&code=x",
        follow_redirects=False,
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.asyncio
async def test_callback_exchange_failure_redirects(client, admin_user, auth_headers, token_exchange):
    _FakeTokenClient.next_response = _FakeTokenResponse(
        400,
        {"error": "invalid_grant", "error_description": "code expired"},
    )
    begin = _begin(client, auth_headers(admin_user))
    state = begin.json()["state"]

    callback = client.get(
        f"/api/v1/external-libraries/oauth/callback?state={state}&code=authcode",
        follow_redirects=False,
    )
    assert callback.status_code in (302, 303, 307)
    params = parse_qs(urlsplit(callback.headers["location"]).query)
    assert "code expired" in params["oauth_error"][0]
    assert params["oauth_state"] == [state]


@pytest.mark.asyncio
async def test_callback_return_to_rejects_external_urls(client, admin_user, auth_headers, token_exchange):
    """Absolute or scheme-relative ``return_to`` values fall back to ``/``."""
    for return_to in ("https://evil.example/phish", "//evil.example/phish"):
        begin = _begin(client, auth_headers(admin_user), return_to=return_to)
        state = begin.json()["state"]

        callback = client.get(
            f"/api/v1/external-libraries/oauth/callback?state={state}&code=authcode",
            follow_redirects=False,
        )
        assert callback.status_code in (302, 303, 307)
        location = urlsplit(callback.headers["location"])
        assert not location.netloc
        assert location.path == "/"


@pytest.mark.asyncio
async def test_claim_unknown_state(client, admin_user, auth_headers):
    response = client.post(
        "/api/v1/external-libraries/oauth/claim",
        json={"state": "nope"},
        headers=auth_headers(admin_user),
    )
    assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.asyncio
async def test_providers_report_oauth_supported(client, admin_user, auth_headers):
    response = client.get(
        "/api/v1/external-libraries/providers",
        headers=auth_headers(admin_user),
    )
    assert response.status_code == status.HTTP_200_OK
    providers = {item["provider_type"]: item for item in response.json()}
    assert providers["dropbox"]["oauth_supported"] is True
    assert providers["dropbox"]["oauth_callback_url"].endswith("/api/v1/external-libraries/oauth/callback")
    assert providers["s3"]["oauth_supported"] is False
    assert providers["s3"]["oauth_callback_url"] is None


@pytest.mark.asyncio
async def test_admin_providers_report_oauth_supported(client, admin_user, auth_headers):
    response = client.get(
        "/api/v1/admin/external-libraries/providers",
        headers=auth_headers(admin_user),
    )
    assert response.status_code == status.HTTP_200_OK
    providers = {item["provider_type"]: item for item in response.json()}
    assert providers["dropbox"]["oauth_supported"] is True
    assert providers["dropbox"]["oauth_callback_url"].endswith("/api/v1/external-libraries/oauth/callback")
    assert providers["s3"]["oauth_supported"] is False
