"""
Reusable OAuth2 authorization-code flow for external-library providers.

Providers that authenticate via OAuth (Dropbox today; Spotify, Tidal and
YouTube are planned) register an :class:`OAuthProviderSpec` describing the
provider's authorize/token endpoints and which adapter config keys carry the
OAuth client credentials and the granted tokens. The rest of the flow is
provider-agnostic:

1. ``begin_flow`` builds the provider's authorization URL (authorization
   code grant + PKCE) and stores the pending flow — client credentials,
   code verifier, redirect URI and the page to return to — in Redis under a
   random ``state`` key.
2. The user authorizes on the provider, which redirects back to the shared
   ``/api/v1/external-libraries/oauth/callback`` endpoint. The callback
   consumes the pending entry (one-time), exchanges the code via
   ``exchange_code`` and stores the resulting config fragment under a
   result key.
3. The SPA claims the result once via ``claim_result`` and merges the
   granted tokens into the provider configuration, which is then saved
   through the normal create/update endpoints (validated and encrypted like
   manually entered credentials).

Nothing here is Dropbox-specific; a new OAuth provider only needs to
register a spec and map its token response fields to adapter config keys.
"""

import base64
import hashlib
import json
import secrets
from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import urlencode

import httpx
from redis.asyncio import Redis

_PENDING_PREFIX = "songhive:external-oauth:pending:"
_RESULT_PREFIX = "songhive:external-oauth:result:"
_PENDING_TTL_SECONDS = 600
_RESULT_TTL_SECONDS = 300
_TOKEN_REQUEST_TIMEOUT = 30

_OAUTH_PROVIDERS: dict[str, "OAuthProviderSpec"] = {}


class OAuthFlowError(Exception):
    """Raised when an OAuth flow step fails; message is safe to show users."""


@dataclass(frozen=True)
class OAuthProviderSpec:
    """Describes how to run an OAuth authorization-code flow for a provider."""

    provider_type: str
    authorize_url: str
    token_url: str
    """Adapter config key holding the OAuth client id (e.g. ``app_key``)."""
    client_id_field: str
    """Adapter config key holding the OAuth client secret; empty disables it."""
    client_secret_field: str = ""
    """OAuth scopes to request during authorization."""
    scopes: tuple[str, ...] = ()
    """Extra query parameters for the authorize URL (e.g. offline access)."""
    extra_authorize_params: dict[str, str] = field(default_factory=dict)
    """Map of token-response field -> adapter config key for granted values."""
    token_fields: dict[str, str] = field(
        default_factory=lambda: {
            "access_token": "access_token",
            "refresh_token": "refresh_token",
        }
    )
    """Send a PKCE S256 challenge (recommended; harmless for most providers)."""
    use_pkce: bool = True


def register_oauth_provider(spec: OAuthProviderSpec) -> None:
    """Register an OAuth provider spec under its provider type."""
    _OAUTH_PROVIDERS[spec.provider_type] = spec


def get_oauth_provider(provider_type: str) -> Optional[OAuthProviderSpec]:
    """Return the OAuth spec for a provider type, or ``None``."""
    return _OAUTH_PROVIDERS.get(provider_type)


def oauth_supported(provider_type: str) -> bool:
    """Return whether the provider supports the interactive OAuth flow."""
    return provider_type in _OAUTH_PROVIDERS


def _pending_key(state: str) -> str:
    return f"{_PENDING_PREFIX}{state}"


def _result_key(state: str) -> str:
    return f"{_RESULT_PREFIX}{state}"


def _pkce_challenge(verifier: str) -> str:
    """Return the base64url-encoded SHA-256 PKCE challenge for a verifier."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


async def begin_flow(
    redis: Redis,
    spec: OAuthProviderSpec,
    *,
    user_id: str,
    config: dict,
    redirect_uri: str,
    return_to: str,
    external_library_id: Optional[str] = None,
) -> tuple[str, str]:
    """
    Start an OAuth authorization flow and return ``(state, authorize_url)``.

    The pending entry binds the flow to ``user_id`` and keeps the client
    credentials and PKCE verifier server-side until the callback consumes it.
    """
    raw_client_id = config.get(spec.client_id_field)
    client_id = raw_client_id.strip() if isinstance(raw_client_id, str) else ""
    if not client_id:
        raise OAuthFlowError(
            f'config["{spec.client_id_field}"] is required to connect via OAuth',
        )

    client_secret = ""
    if spec.client_secret_field:
        raw_secret = config.get(spec.client_secret_field)
        if isinstance(raw_secret, str):
            client_secret = raw_secret.strip()

    state = secrets.token_urlsafe(24)
    code_verifier = secrets.token_urlsafe(64) if spec.use_pkce else ""

    pending = {
        "provider_type": spec.provider_type,
        "user_id": user_id,
        "client_id": client_id,
        "client_secret": client_secret,
        "code_verifier": code_verifier,
        "redirect_uri": redirect_uri,
        "return_to": return_to,
        "external_library_id": external_library_id,
    }
    await redis.set(_pending_key(state), json.dumps(pending), ex=_PENDING_TTL_SECONDS)

    params: dict[str, str] = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": state,
    }
    if spec.scopes:
        params["scope"] = " ".join(spec.scopes)
    if code_verifier:
        params["code_challenge"] = _pkce_challenge(code_verifier)
        params["code_challenge_method"] = "S256"
    params.update(spec.extra_authorize_params)

    return state, f"{spec.authorize_url}?{urlencode(params)}"


async def consume_pending(redis: Redis, state: str) -> Optional[dict]:
    """Atomically load and delete the pending flow for ``state``."""
    raw = await redis.getdel(_pending_key(state))
    if not raw:
        return None
    try:
        pending = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return pending if isinstance(pending, dict) else None


async def exchange_code(
    spec: OAuthProviderSpec,
    pending: dict,
    code: str,
) -> dict[str, Any]:
    """
    Exchange an authorization code for tokens and return the config fragment.

    The fragment maps token-response fields to adapter config keys per
    ``spec.token_fields`` (e.g. ``access_token``/``refresh_token``).
    """
    data: dict[str, str] = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": pending["redirect_uri"],
        "client_id": pending["client_id"],
    }
    if pending.get("client_secret"):
        data["client_secret"] = pending["client_secret"]
    if pending.get("code_verifier"):
        data["code_verifier"] = pending["code_verifier"]

    async with httpx.AsyncClient(timeout=_TOKEN_REQUEST_TIMEOUT) as client:
        try:
            response = await client.post(
                spec.token_url,
                data=data,
                headers={"Accept": "application/json"},
            )
        except httpx.HTTPError as exc:
            raise OAuthFlowError(f"Token exchange failed: {exc.__class__.__name__}") from exc

    try:
        payload = response.json()
    except ValueError:
        payload = {}

    if response.is_error or "error" in payload:
        detail = payload.get("error_description") or payload.get("error") or f"HTTP {response.status_code}"
        raise OAuthFlowError(f"Token exchange failed: {detail}")

    fragment: dict[str, Any] = {}
    for token_field, config_key in spec.token_fields.items():
        value = payload.get(token_field)
        if isinstance(value, str) and value:
            fragment[config_key] = value

    if "access_token" in spec.token_fields.values() and not fragment.get("access_token"):
        raise OAuthFlowError("Token exchange returned no access token")
    if not fragment:
        raise OAuthFlowError("Token exchange returned no tokens")
    return fragment


async def store_result(redis: Redis, state: str, result: dict) -> None:
    """Store the completed OAuth result for the SPA to claim."""
    await redis.set(_result_key(state), json.dumps(result), ex=_RESULT_TTL_SECONDS)


async def claim_result(redis: Redis, state: str, user_id: str) -> Optional[dict]:
    """Load and delete a completed OAuth result when it belongs to ``user_id``.

    A claim by a different user does not consume the entry, so the rightful
    owner can still pick it up.
    """
    key = _result_key(state)
    raw = await redis.get(key)
    if not raw:
        return None
    try:
        result = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(result, dict) or result.get("user_id") != user_id:
        return None
    await redis.delete(key)
    return result
