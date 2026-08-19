"""
OAuth2 client registry and provider helpers.

Provides client registration, lookup, and deletion used by the admin API and
the OAuth2 authorization server, plus the authorization-code + PKCE provider
implementation.
"""

import hashlib
import ipaddress
import json
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple, Union
from urllib.parse import urlparse

from authlib.oauth2.rfc7636.challenge import (
    CODE_CHALLENGE_PATTERN,
    CODE_VERIFIER_PATTERN,
    compare_plain_code_challenge,
    compare_s256_code_challenge,
)
from redis.asyncio import Redis
from redis.exceptions import WatchError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config.schema import SonghiveConfig
from ..models.oauth_client import DEFAULT_GRANT_TYPES, OAuth2Client
from ..models.user import User
from ..services.auth import get_user_by_id, hash_password, verify_password

__all__ = [
    "OAuthClientError",
    "create_oauth_client",
    "list_oauth_clients",
    "count_oauth_clients",
    "get_oauth_client_by_client_id",
    "delete_oauth_client",
    "check_client_secret",
    "OAuth2ProviderError",
    "create_authorization_code",
    "create_token",
    "revoke_token",
    "introspect_token",
]

VALID_GRANT_TYPES = {"authorization_code", "client_credentials", "refresh_token"}
MAX_NAME_LENGTH = 128
MAX_REDIRECT_URI_LENGTH = 512
ALLOWED_REDIRECT_SCHEMES = {"https"}


def _is_loopback_host(host: Optional[str]) -> bool:
    """Return True if the host is a loopback address or localhost."""
    if not host:
        return False
    normalized = host.lower().strip("[]")
    if normalized == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


class OAuthClientError(ValueError):
    """Raised when an OAuth2 client operation cannot be completed."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def _validate_name(name: str) -> str:
    """Validate and normalize a client name."""
    name = (name or "").strip()
    if not name:
        raise OAuthClientError("Client name is required")
    if len(name) > MAX_NAME_LENGTH:
        raise OAuthClientError("Client name is too long")
    return name


def _validate_redirect_uris(uris: List[str]) -> List[str]:
    """Validate a list of redirect URIs."""
    if not uris:
        raise OAuthClientError("At least one redirect URI is required")

    normalized = []
    for uri in uris:
        if not isinstance(uri, str):
            raise OAuthClientError("redirect_uris must be strings")

        uri = uri.strip()
        if not uri:
            raise OAuthClientError("redirect_uris cannot contain empty values")
        if len(uri) > MAX_REDIRECT_URI_LENGTH:
            raise OAuthClientError("redirect_uris cannot exceed 512 characters")

        parsed = urlparse(uri)
        if not parsed.scheme:
            raise OAuthClientError(f"Invalid redirect_uri: {uri}")
        if parsed.scheme == "http" and not _is_loopback_host(parsed.hostname):
            raise OAuthClientError(f"HTTP redirect_uri is only allowed for localhost/loopback: {uri}")
        if parsed.scheme not in ALLOWED_REDIRECT_SCHEMES | {"http"}:
            raise OAuthClientError(
                f"Invalid redirect_uri scheme for {uri!r}; "
                f"allowed schemes: {', '.join(sorted(ALLOWED_REDIRECT_SCHEMES))} "
                "(http is allowed for localhost/loopback)"
            )
        if not parsed.netloc:
            raise OAuthClientError(f"Invalid redirect_uri: {uri}")
        if parsed.fragment:
            raise OAuthClientError(f"redirect_uris must not contain fragments: {uri}")

        normalized.append(uri)

    return normalized


def _validate_grant_types(grant_types: Optional[List[str]]) -> List[str]:
    """Validate and normalize a list of grant types."""
    if grant_types is None or not grant_types:
        return list(DEFAULT_GRANT_TYPES)

    normalized = []
    for grant in grant_types:
        if not isinstance(grant, str):
            raise OAuthClientError("grant_types must be strings")

        grant = grant.strip().lower()
        if grant not in VALID_GRANT_TYPES:
            raise OAuthClientError(f"Unsupported grant_type: {grant}")

        normalized.append(grant)

    if not normalized:
        return list(DEFAULT_GRANT_TYPES)

    return normalized


async def _generate_unique_client_id(session: AsyncSession) -> str:
    """Generate a unique ``client_id`` for a new OAuth2 client."""
    for _ in range(10):
        client_id = secrets.token_urlsafe(32)
        existing = await get_oauth_client_by_client_id(session, client_id)
        if existing is None:
            return client_id

    raise OAuthClientError("Could not generate a unique client_id")


async def create_oauth_client(
    session: AsyncSession,
    created_by: str,
    name: str,
    redirect_uris: List[str],
    grant_types: Optional[List[str]] = None,
    is_confidential: bool = True,
    owner_id: Optional[str] = None,
) -> tuple[OAuth2Client, Optional[str]]:
    """
    Create a new OAuth2 client.

    :param session: Database session.
    :param created_by: User id creating the client; used as the default owner.
    :param name: Human-readable client name.
    :param redirect_uris: List of allowed redirect URIs.
    :param grant_types: Optional list of allowed grant types; defaults to
        ``authorization_code``.
    :param is_confidential: Whether the client can keep a secret.
    :param owner_id: Optional user id to own the client; defaults to ``created_by``.
    :returns: The created client and the raw client secret (``None`` for public
        clients).
    :raises OAuthClientError: If the input is invalid or a unique client id
        cannot be generated.
    """
    name = _validate_name(name)
    redirect_uris = _validate_redirect_uris(redirect_uris)
    grant_types = _validate_grant_types(grant_types)

    owner_id = owner_id or created_by
    owner = await get_user_by_id(session, owner_id)
    if owner is None:
        raise OAuthClientError("Owner not found")

    client_id = await _generate_unique_client_id(session)

    client_secret: Optional[str] = None
    client_secret_hash: Optional[str] = None
    if is_confidential:
        client_secret = secrets.token_urlsafe(32)
        client_secret_hash = hash_password(client_secret)

    client = OAuth2Client(
        client_id=client_id,
        client_secret_hash=client_secret_hash,
        name=name,
        redirect_uris=redirect_uris,
        grant_types=grant_types,
        owner_id=owner_id,
        is_confidential=is_confidential,
    )
    session.add(client)
    await session.flush()

    return client, client_secret


async def get_oauth_client_by_client_id(
    session: AsyncSession,
    client_id: str,
) -> Optional[OAuth2Client]:
    """Fetch an OAuth2 client by its public ``client_id``."""
    result = await session.execute(select(OAuth2Client).where(OAuth2Client.client_id == client_id))
    return result.scalar_one_or_none()


async def list_oauth_clients(
    session: AsyncSession,
    *,
    limit: int = 100,
    offset: int = 0,
) -> List[OAuth2Client]:
    """List OAuth2 clients ordered by creation date (newest first)."""
    result = await session.execute(
        select(OAuth2Client).order_by(OAuth2Client.created_at.desc()).offset(offset).limit(limit)
    )
    return list(result.scalars().all())


async def count_oauth_clients(session: AsyncSession) -> int:
    """Return the total number of OAuth2 clients."""
    result = await session.execute(select(func.count(OAuth2Client.id)))
    return result.scalar() or 0


async def delete_oauth_client(session: AsyncSession, client_id: str) -> bool:
    """
    Delete an OAuth2 client by its public ``client_id``.

    :returns: ``True`` if a client was deleted, ``False`` if it did not exist.
    """
    client = await get_oauth_client_by_client_id(session, client_id)
    if client is None:
        return False

    await session.delete(client)
    await session.flush()
    return True


def check_client_secret(client: OAuth2Client, client_secret: str) -> bool:
    """Verify a raw client secret against the stored hash."""
    if not client.client_secret_hash or not client_secret:
        return False
    return verify_password(client_secret, client.client_secret_hash)


# --------------------------------------------------------------------------- #
# OAuth2 authorization-code + PKCE provider
# --------------------------------------------------------------------------- #


class OAuth2ProviderError(ValueError):
    """Raised when an OAuth2 provider request cannot be completed."""

    def __init__(
        self,
        message: str,
        status_code: int = 400,
        error: str = "invalid_request",
    ):
        super().__init__(message)
        self.status_code = status_code
        self.error = error


AUTHORIZATION_CODE_TTL_SECONDS = 600  # 10 minutes
OAUTH_AUTHZ_CODE_PREFIX = "oauth:authz:"
OAUTH_ACCESS_TOKEN_PREFIX = "oauth:access:"
OAUTH_REFRESH_TOKEN_PREFIX = "oauth:refresh:"
SUPPORTED_CODE_CHALLENGE_METHODS = {"plain", "S256"}


def _hash_value(value: str) -> str:
    """Return a SHA-256 hash of a token or code for Redis keys."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _authz_code_key(code: str) -> str:
    """Build the Redis key for an authorization code."""
    return f"{OAUTH_AUTHZ_CODE_PREFIX}{_hash_value(code)}"


def _access_token_key(token: str) -> str:
    """Build the Redis key for an OAuth2 access token."""
    return f"{OAUTH_ACCESS_TOKEN_PREFIX}{_hash_value(token)}"


def _refresh_token_key(token: str) -> str:
    """Build the Redis key for an OAuth2 refresh token."""
    return f"{OAUTH_REFRESH_TOKEN_PREFIX}{_hash_value(token)}"


def _encode_json(data: Dict[str, Any]) -> str:
    """Serialize a dictionary to a JSON string with datetime support."""

    def _default(obj: Any) -> Any:
        if isinstance(obj, datetime):
            return obj.isoformat()
        raise TypeError(f"Object of type {type(obj)} is not JSON serializable")

    return json.dumps(data, default=_default)


def _decode_json(value: Optional[Union[str, bytes]]) -> Optional[Dict[str, Any]]:
    """Deserialize a JSON string, returning None on failure."""
    if not value:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return None


def _utc_now() -> datetime:
    """Return the current UTC time."""
    return datetime.now(timezone.utc)


def _token_expires_at(config: SonghiveConfig, token_type: str) -> datetime:
    """Return the expiry datetime for an OAuth2 token."""
    if token_type == "access_token":
        return _utc_now() + timedelta(minutes=config.auth.access_token_expiry_minutes)
    return _utc_now() + timedelta(days=config.auth.refresh_token_expiry_days)


def _parse_expires_at(value: Optional[str]) -> Optional[datetime]:
    """Parse an ISO-format expiry datetime and normalize to UTC."""
    if not value:
        return None
    try:
        expires_at = datetime.fromisoformat(value)
    except ValueError:
        return None
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    else:
        expires_at = expires_at.astimezone(timezone.utc)
    return expires_at


def _token_payload(
    token_type: str,
    client: OAuth2Client,
    user_id: str,
    scope: Optional[str],
    config: SonghiveConfig,
) -> Dict[str, Any]:
    """Build the Redis payload for an issued token."""
    now = _utc_now()
    expires_at = _token_expires_at(config, token_type)
    return {
        "token_type": token_type,
        "client_id": client.client_id,
        "user_id": user_id,
        "scope": scope,
        "created_at": now.isoformat(),
        "expires_at": expires_at.isoformat(),
    }


async def _authenticate_client(
    session: AsyncSession,
    client_id: Optional[str],
    client_secret: Optional[str],
) -> OAuth2Client:
    """
    Authenticate an OAuth2 client from its id and optional secret.

    Confidential clients must provide a valid client secret. Public clients must
    not provide a secret.
    """
    if not client_id:
        raise OAuth2ProviderError(
            "Client authentication failed",
            status_code=401,
            error="invalid_client",
        )

    client = await get_oauth_client_by_client_id(session, client_id)
    if client is None:
        raise OAuth2ProviderError(
            "Client authentication failed",
            status_code=401,
            error="invalid_client",
        )

    if client.is_confidential:
        if not client_secret or not check_client_secret(client, client_secret):
            raise OAuth2ProviderError(
                "Client authentication failed",
                status_code=401,
                error="invalid_client",
            )
    elif client_secret:
        # Public clients cannot authenticate with a secret.
        raise OAuth2ProviderError(
            "Client authentication failed",
            status_code=401,
            error="invalid_client",
        )

    return client


def _require_grant_type(client: OAuth2Client, grant_type: str) -> None:
    """Ensure the client is allowed to use the requested grant type."""
    if grant_type not in client.grant_types:
        raise OAuth2ProviderError(
            "Grant type not supported",
            status_code=400,
            error="unsupported_grant_type",
        )


def _verify_pkce(
    code_challenge: str,
    code_challenge_method: str,
    code_verifier: str,
) -> None:
    """Validate a PKCE code verifier against the stored challenge."""
    if code_challenge_method not in SUPPORTED_CODE_CHALLENGE_METHODS:
        raise OAuth2ProviderError(
            "Unsupported code challenge method",
            error="invalid_request",
        )

    if not CODE_CHALLENGE_PATTERN.match(code_challenge):
        raise OAuth2ProviderError(
            "Invalid code challenge",
            error="invalid_request",
        )

    if not CODE_VERIFIER_PATTERN.match(code_verifier):
        raise OAuth2ProviderError(
            "Invalid code verifier",
            error="invalid_request",
        )

    if code_challenge_method == "S256":
        if not compare_s256_code_challenge(code_verifier, code_challenge):
            raise OAuth2ProviderError(
                "Code challenge failed",
                error="invalid_grant",
            )
    elif not compare_plain_code_challenge(code_verifier, code_challenge):
        raise OAuth2ProviderError(
            "Code challenge failed",
            error="invalid_grant",
        )


async def create_authorization_code(
    session: AsyncSession,
    redis: Redis,
    user: User,
    response_type: str,
    client_id: str,
    redirect_uri: str,
    code_challenge: Optional[str],
    code_challenge_method: str = "S256",
    scope: Optional[str] = None,
    state: Optional[str] = None,
) -> Tuple[str, Optional[str]]:
    """
    Create and store an OAuth2 authorization code for the given resource owner.

    Returns the raw authorization code and the ``state`` value to echo back to
    the client.
    """
    if response_type != "code":
        raise OAuth2ProviderError(
            "Unsupported response type",
            error="unsupported_response_type",
        )

    client = await get_oauth_client_by_client_id(session, client_id)
    if client is None:
        raise OAuth2ProviderError("Invalid client", error="invalid_client")

    if redirect_uri not in client.redirect_uris:
        raise OAuth2ProviderError("Invalid redirect URI", error="invalid_request")

    _require_grant_type(client, "authorization_code")

    if not code_challenge:
        raise OAuth2ProviderError("Missing code_challenge", error="invalid_request")

    if not code_challenge_method:
        code_challenge_method = "S256"

    if code_challenge_method not in SUPPORTED_CODE_CHALLENGE_METHODS:
        raise OAuth2ProviderError(
            "Unsupported code challenge method",
            error="invalid_request",
        )

    if not CODE_CHALLENGE_PATTERN.match(code_challenge):
        raise OAuth2ProviderError("Invalid code challenge", error="invalid_request")

    code = secrets.token_urlsafe(32)
    expires_at = _utc_now() + timedelta(seconds=AUTHORIZATION_CODE_TTL_SECONDS)
    payload = {
        "client_id": client.client_id,
        "redirect_uri": redirect_uri,
        "user_id": user.id,
        "code_challenge": code_challenge,
        "code_challenge_method": code_challenge_method,
        "scope": scope,
        "state": state,
        "expires_at": expires_at.isoformat(),
    }

    await redis.set(
        _authz_code_key(code),
        _encode_json(payload),
        ex=AUTHORIZATION_CODE_TTL_SECONDS,
    )

    return code, state


async def _get_oauth_token(
    redis: Redis,
    token: str,
    token_type_hint: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Load an OAuth2 access or refresh token from Redis.

    ``token_type_hint`` may be ``access_token`` or ``refresh_token``.
    """
    if token_type_hint == "access_token":
        keys = [_access_token_key(token)]
    elif token_type_hint == "refresh_token":
        keys = [_refresh_token_key(token)]
    else:
        keys = [_access_token_key(token), _refresh_token_key(token)]

    for key in keys:
        value = await redis.get(key)
        data = _decode_json(value)
        if data is None:
            continue

        expires_at = _parse_expires_at(data.get("expires_at"))
        if expires_at is None or _utc_now() > expires_at:
            await redis.delete(key)
            continue

        return data

    return None


async def _issue_oauth_token_pair(
    redis: Redis,
    config: SonghiveConfig,
    client: OAuth2Client,
    user_id: str,
    scope: Optional[str],
) -> Dict[str, Any]:
    """Issue a new OAuth2 access and refresh token pair and store them in Redis."""
    access_token = secrets.token_urlsafe(32)
    refresh_token = secrets.token_urlsafe(32)

    now = _utc_now()
    access_expires_at = _token_expires_at(config, "access_token")
    refresh_expires_at = _token_expires_at(config, "refresh_token")
    access_ttl = int((access_expires_at - now).total_seconds())
    refresh_ttl = int((refresh_expires_at - now).total_seconds())

    await redis.set(
        _access_token_key(access_token),
        _encode_json(_token_payload("access_token", client, user_id, scope, config)),
        ex=access_ttl,
    )
    await redis.set(
        _refresh_token_key(refresh_token),
        _encode_json(_token_payload("refresh_token", client, user_id, scope, config)),
        ex=refresh_ttl,
    )

    expires_in = (config.auth.access_token_expiry_minutes or 0) * 60
    return {
        "access_token": access_token,
        "token_type": "Bearer",
        "expires_in": expires_in,
        "refresh_token": refresh_token,
        "scope": scope,
    }


async def _consume_authorization_code(
    redis: Redis,
    client: OAuth2Client,
    code: str,
    redirect_uri: str,
    code_verifier: str,
) -> Dict[str, Any]:
    """Atomically load, validate and delete an authorization code."""
    key = _authz_code_key(code)

    async with redis.pipeline(transaction=True) as pipe:
        await pipe.watch(key)
        value = await pipe.get(key)
        data = _decode_json(value)
        if data is None:
            await pipe.reset()
            raise OAuth2ProviderError("Invalid authorization code", error="invalid_grant")

        expires_at = _parse_expires_at(data.get("expires_at"))
        if expires_at is None or _utc_now() > expires_at:
            await pipe.reset()
            await redis.delete(key)
            raise OAuth2ProviderError("Invalid authorization code", error="invalid_grant")
        if data.get("client_id") != client.client_id:
            await pipe.reset()
            raise OAuth2ProviderError("Invalid authorization code", error="invalid_grant")
        if data.get("redirect_uri") != redirect_uri:
            await pipe.reset()
            raise OAuth2ProviderError("Invalid redirect URI", error="invalid_grant")

        try:
            _verify_pkce(
                data["code_challenge"],
                data["code_challenge_method"],
                code_verifier,
            )
        except OAuth2ProviderError:
            await pipe.reset()
            raise

        pipe.multi()
        pipe.delete(key)
        try:
            await pipe.execute()
        except WatchError:
            raise OAuth2ProviderError("Invalid authorization code", error="invalid_grant") from None

    return data


async def _consume_refresh_token(
    redis: Redis,
    client: OAuth2Client,
    refresh_token: str,
) -> Dict[str, Any]:
    """Atomically load, validate and delete a refresh token."""
    key = _refresh_token_key(refresh_token)

    async with redis.pipeline(transaction=True) as pipe:
        await pipe.watch(key)
        value = await pipe.get(key)
        data = _decode_json(value)
        if data is None:
            await pipe.reset()
            raise OAuth2ProviderError("Invalid refresh token", error="invalid_grant")

        expires_at = _parse_expires_at(data.get("expires_at"))
        if expires_at is None or _utc_now() > expires_at:
            await pipe.reset()
            await redis.delete(key)
            raise OAuth2ProviderError("Invalid refresh token", error="invalid_grant")
        if data.get("client_id") != client.client_id:
            await pipe.reset()
            raise OAuth2ProviderError("Invalid refresh token", error="invalid_grant")
        if data.get("token_type") != "refresh_token":
            await pipe.reset()
            raise OAuth2ProviderError("Invalid refresh token", error="invalid_grant")

        pipe.multi()
        pipe.delete(key)
        try:
            await pipe.execute()
        except WatchError:
            raise OAuth2ProviderError("Invalid refresh token", error="invalid_grant") from None

    return data


async def _create_token_from_authorization_code(
    session: AsyncSession,
    redis: Redis,
    config: SonghiveConfig,
    client: OAuth2Client,
    code: Optional[str],
    redirect_uri: Optional[str],
    code_verifier: Optional[str],
    scope: Optional[str],
) -> Dict[str, Any]:
    """Exchange an authorization code and PKCE verifier for a token pair."""
    if not code:
        raise OAuth2ProviderError("Missing authorization code", error="invalid_request")
    if not redirect_uri:
        raise OAuth2ProviderError("Missing redirect URI", error="invalid_request")
    if not code_verifier:
        raise OAuth2ProviderError("Missing code verifier", error="invalid_request")

    data = await _consume_authorization_code(
        redis,
        client,
        code,
        redirect_uri,
        code_verifier,
    )

    user_id = data["user_id"]
    user = await get_user_by_id(session, user_id)
    if user is None or not user.is_active:
        raise OAuth2ProviderError("User not active", error="invalid_grant")

    final_scope = scope or data.get("scope")
    return await _issue_oauth_token_pair(redis, config, client, user_id, final_scope)


async def _create_token_from_refresh_token(
    session: AsyncSession,
    redis: Redis,
    config: SonghiveConfig,
    client: OAuth2Client,
    refresh_token: Optional[str],
    scope: Optional[str],
) -> Dict[str, Any]:
    """Issue a new token pair using a valid refresh token."""
    if not refresh_token:
        raise OAuth2ProviderError("Missing refresh token", error="invalid_request")

    data = await _consume_refresh_token(redis, client, refresh_token)

    user_id = data["user_id"]
    user = await get_user_by_id(session, user_id)
    if user is None or not user.is_active:
        raise OAuth2ProviderError("User not active", error="invalid_grant")

    final_scope = scope or data.get("scope")
    return await _issue_oauth_token_pair(redis, config, client, user_id, final_scope)


async def create_token(
    session: AsyncSession,
    redis: Redis,
    config: SonghiveConfig,
    grant_type: str,
    client_id: Optional[str],
    client_secret: Optional[str],
    code: Optional[str] = None,
    redirect_uri: Optional[str] = None,
    code_verifier: Optional[str] = None,
    refresh_token: Optional[str] = None,
    scope: Optional[str] = None,
) -> Dict[str, Any]:
    """Create an OAuth2 token response for a valid token request."""
    client = await _authenticate_client(session, client_id, client_secret)

    if grant_type == "authorization_code":
        return await _create_token_from_authorization_code(
            session,
            redis,
            config,
            client,
            code,
            redirect_uri,
            code_verifier,
            scope,
        )

    if grant_type == "refresh_token":
        _require_grant_type(client, "refresh_token")
        return await _create_token_from_refresh_token(
            session,
            redis,
            config,
            client,
            refresh_token,
            scope,
        )

    raise OAuth2ProviderError(
        "Unsupported grant type",
        status_code=400,
        error="unsupported_grant_type",
    )


async def revoke_token(
    session: AsyncSession,
    redis: Redis,
    token: str,
    token_type_hint: Optional[str] = None,
    client_id: Optional[str] = None,
    client_secret: Optional[str] = None,
) -> None:
    """
    Revoke an OAuth2 access or refresh token.

    If the token exists in Redis it is deleted. Missing or invalid tokens are
    handled silently as required by RFC 7009.
    """
    if not token:
        return

    if client_id:
        try:
            client = await _authenticate_client(session, client_id, client_secret)
        except OAuth2ProviderError:
            return
        data = await _get_oauth_token(redis, token, token_type_hint)
        if data is not None and data.get("client_id") == client.client_id:
            token_type = data.get("token_type")
            if token_type == "access_token":
                await redis.delete(_access_token_key(token))
            elif token_type == "refresh_token":
                await redis.delete(_refresh_token_key(token))
        # If the token does not belong to the client, succeed silently.
        return

    data = await _get_oauth_token(redis, token, token_type_hint)
    if data is not None:
        token_type = data.get("token_type")
        if token_type == "access_token":
            await redis.delete(_access_token_key(token))
        elif token_type == "refresh_token":
            await redis.delete(_refresh_token_key(token))
        return

    if token_type_hint == "access_token":
        await redis.delete(_access_token_key(token))
    elif token_type_hint == "refresh_token":
        await redis.delete(_refresh_token_key(token))
    else:
        await redis.delete(_access_token_key(token), _refresh_token_key(token))


async def introspect_token(
    session: AsyncSession,
    redis: Redis,
    token: str,
    token_type_hint: Optional[str] = None,
    client_id: Optional[str] = None,
    client_secret: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Return the active status and metadata for an OAuth2 token.

    Client authentication is required: the caller must prove they are the
    client to whom the token was issued before any metadata is returned.
    """
    client = await _authenticate_client(session, client_id, client_secret)

    data = await _get_oauth_token(redis, token, token_type_hint)
    if data is None:
        return {"active": False}

    if data.get("client_id") != client.client_id:
        return {"active": False}

    user_id = data.get("user_id")
    if not user_id:
        return {"active": False}
    user = await get_user_by_id(session, user_id)
    if user is None or not user.is_active:
        return {"active": False}

    expires_at = _parse_expires_at(data.get("expires_at"))
    if expires_at is None or _utc_now() > expires_at:
        return {"active": False}

    return {
        "active": True,
        "client_id": data.get("client_id"),
        "username": user.username,
        "token_type": data.get("token_type"),
        "scope": data.get("scope"),
        "exp": int(expires_at.timestamp()),
        "sub": user.id,
    }
