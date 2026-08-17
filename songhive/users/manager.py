"""
User lifecycle management.
"""

from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from ..models.user import User
from ..services.auth import create_user, get_user_by_username, hash_password


async def register_user(
    session: AsyncSession,
    username: str,
    email: str,
    password: str,
    display_name: Optional[str] = None,
) -> User:
    """
    Register a new user.

    :raises ValueError: If username or email is already taken.
    """
    existing = await get_user_by_username(session, username)
    if existing:
        raise ValueError(f"Username '{username}' is already taken")

    user = await create_user(session, username, email, password)
    if display_name:
        user.display_name = display_name

    return user


async def change_password(session: AsyncSession, user: User, new_password: str) -> None:
    """Change a user's password."""
    user.password_hash = hash_password(new_password)
    await session.flush()


async def deactivate_user(session: AsyncSession, user: User) -> None:
    """Deactivate a user account."""
    user.is_active = False
    await session.flush()
