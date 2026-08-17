"""
FastAPI dependency injection helpers.
"""

from typing import AsyncGenerator

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..config.schema import SonghiveConfig
from ..models.base import get_session


def get_config(request: Request) -> SonghiveConfig:
    """Get the application config from the request state."""
    return request.app.state.config


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Get a database session via dependency injection."""
    async with get_session() as session:
        yield session


async def get_current_user(request: Request):
    """
    Extract and validate the current user from the request.
    Returns the User model instance or raises 401.
    """
    # TODO: implement JWT/OAuth2 token validation
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
    )


async def require_admin(current_user=Depends(get_current_user)):
    """Require admin privileges."""
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return current_user
