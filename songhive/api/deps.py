"""
FastAPI dependency injection helpers.
"""

from typing import AsyncGenerator

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..config.schema import SonghiveConfig
from ..models.base import get_session
from ..models.user import User
from ..services.auth import get_user_by_id
from .middleware.auth import decode_access_token, extract_token


def get_config(request: Request) -> SonghiveConfig:
    """Get the application config from the request state."""
    return request.app.state.config


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Get a database session via dependency injection."""
    async with get_session() as session:
        yield session


async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> User:
    """
    Extract and validate the current user from the request.

    Returns the User model instance or raises 401 for missing, invalid,
    inactive, or deleted users.
    """
    token = extract_token(request)
    exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
        headers={"WWW-Authenticate": "Bearer"},
    )

    if not token:
        raise exc

    config = get_config(request)
    user_id = decode_access_token(token, config.auth.secret_key)
    if user_id is None:
        raise exc

    user = await get_user_by_id(db, user_id)
    if user is None or not user.is_active:
        raise exc

    return user


async def require_admin(current_user: User = Depends(get_current_user)):
    """Require admin privileges."""
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return current_user
