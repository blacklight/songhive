"""
FastAPI dependency injection helpers.
"""

from typing import AsyncGenerator, Optional

from fastapi import Depends, HTTPException, Request, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from ..config.schema import SonghiveConfig
from ..models.base import get_session
from ..models.user import User
from ..services import acl
from ..services.auth import get_user_by_id
from ..services.storage import StorageService
from ..storage import get_storage
from .middleware.auth import decode_access_token, extract_token

bearer_scheme = HTTPBearer(
    auto_error=False,
    scheme_name="BearerAuth",
    bearerFormat="JWT",
    description="JWT access token obtained from /api/v1/auth/login or /api/v1/auth/refresh",
)


def _token_from_credentials_or_request(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials],
) -> Optional[str]:
    """Extract the bearer token from resolved credentials or the request header."""
    if isinstance(credentials, HTTPAuthorizationCredentials):
        return credentials.credentials
    return extract_token(request)


def get_config(request: Request) -> SonghiveConfig:
    """Get the application config from the request state."""
    config: SonghiveConfig = request.app.state.config
    return config


async def get_effective_config(request: Request) -> SonghiveConfig:
    """Return the effective config as stored in app state."""
    return get_config(request)


def get_redis(request: Request) -> Redis:
    """Get the shared async Redis client from the request state."""
    redis: Redis = request.app.state.redis
    return redis


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Get a database session via dependency injection."""
    async with get_session() as session:
        yield session


async def _get_current_user(
    request: Request,
    db: AsyncSession,
    credentials: Optional[HTTPAuthorizationCredentials] = Security(bearer_scheme),
) -> Optional[User]:
    """Extract and validate the current user, returning ``None`` on any failure."""
    token = _token_from_credentials_or_request(request, credentials)
    if not token:
        return None

    config = get_config(request)
    user_id = decode_access_token(token, config.auth.secret_key)
    if user_id is None:
        return None

    user = await get_user_by_id(db, user_id)
    if user is None or not user.is_active:
        return None

    return user


async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
    credentials: Optional[HTTPAuthorizationCredentials] = Security(bearer_scheme),
) -> User:
    """
    Extract and validate the current user from the request.

    Returns the User model instance or raises 401 for missing, invalid,
    inactive, or deleted users.
    """
    user = await _get_current_user(request, db, credentials)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


async def get_current_user_optional(
    request: Request,
    db: AsyncSession = Depends(get_db),
    credentials: Optional[HTTPAuthorizationCredentials] = Security(bearer_scheme),
) -> Optional[User]:
    """Extract and validate the current user, returning ``None`` when unauthenticated."""
    return await _get_current_user(request, db, credentials)


def _get_share_token(request: Request) -> Optional[str]:
    """Read a share token from the header, cookie, or query string (legacy)."""
    token = request.headers.get("X-Share-Token")
    if token:
        return token
    token = request.cookies.get("share_token")
    if token:
        return token
    return request.query_params.get("token")


def require_access(item_type: str):
    """Return a FastAPI dependency that enforces access to ``item_type`` resources."""
    id_key = acl.ITEM_ID_KEYS.get(item_type)
    if id_key is None:
        raise RuntimeError(f"Unknown item type: {item_type!r}")

    async def _dep(
        request: Request,
        db: AsyncSession = Depends(get_db),
        user: Optional[User] = Depends(get_current_user_optional),
        token: Optional[str] = Depends(_get_share_token),
    ) -> bool:
        """Load the requested item and verify the requester may access it."""
        item_id = request.path_params.get(id_key)
        if not item_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Not found",
            )

        item = await acl.get_item(db, item_type, item_id)
        if item is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Not found",
            )

        if not await acl.can_access(db, user, item_type, item_id, share_token=token):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied",
            )

        return True

    return _dep


def get_storage_service(request: Request) -> StorageService:
    """
    Get or create a cached StorageService, recreating it when storage config
    changes.
    """
    config = get_config(request)
    current = config.storage
    cached = getattr(request.app.state, "storage_service", None)
    cached_config = getattr(request.app.state, "storage_service_config", None)

    # Compare against a deep copy so runtime config mutations (e.g. in tests) force a
    # fresh backend and prevent a cached service from using stale connection state.
    if cached is not None and cached_config == current:
        return cached

    backend = get_storage(current)
    service = StorageService(backend, current)
    request.app.state.storage_service = service
    request.app.state.storage_service_config = current.model_copy(deep=True)
    return service


async def require_admin(current_user: User = Depends(get_current_user)):
    """Require admin privileges."""
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return current_user
