"""
Authentication service: user registration, login, password hashing.
"""

import bcrypt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.user import User, VALID_ROLES


def hash_password(password: str) -> str:
    """Hash a password using bcrypt."""
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    """Verify a password against its hash."""
    return bcrypt.checkpw(password.encode(), password_hash.encode())


async def get_user_by_username(session: AsyncSession, username: str) -> User | None:
    """Fetch a user by username."""
    result = await session.execute(select(User).where(User.username == username))
    return result.scalar_one_or_none()


async def get_user_by_email(session: AsyncSession, email: str) -> User | None:
    """Fetch a user by email."""
    result = await session.execute(select(User).where(User.email == email))
    return result.scalar_one_or_none()


async def create_user(
    session: AsyncSession,
    username: str,
    email: str,
    password: str,
    role: str = "user",
) -> User:
    """Create a new user."""
    if role not in VALID_ROLES:
        raise ValueError(f"Invalid role: {role}")

    user = User(
        username=username,
        email=email,
        password_hash=hash_password(password),
        role=role,
    )
    session.add(user)
    await session.flush()
    return user
