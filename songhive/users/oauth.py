"""
OAuth2 client registry and provider helpers.

Provides client registration, lookup, and deletion used by the admin API and
the OAuth2 authorization server.
"""

import secrets
from typing import List, Optional
from urllib.parse import urlparse

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.oauth_client import DEFAULT_GRANT_TYPES, OAuth2Client
from ..services.auth import get_user_by_id, hash_password, verify_password

__all__ = [
    "OAuthClientError",
    "create_oauth_client",
    "list_oauth_clients",
    "count_oauth_clients",
    "get_oauth_client_by_client_id",
    "delete_oauth_client",
    "check_client_secret",
]

VALID_GRANT_TYPES = {"authorization_code", "client_credentials", "refresh_token"}
MAX_NAME_LENGTH = 128
MAX_REDIRECT_URI_LENGTH = 512
ALLOWED_REDIRECT_SCHEMES = {"http", "https"}


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
        if parsed.scheme not in ALLOWED_REDIRECT_SCHEMES:
            raise OAuthClientError(
                f"Invalid redirect_uri scheme for {uri!r}; "
                f"allowed schemes: {', '.join(sorted(ALLOWED_REDIRECT_SCHEMES))}"
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
