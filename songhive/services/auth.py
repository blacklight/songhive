"""
Authentication service: user registration, login, password hashing.
"""

from typing import Optional

import bcrypt
from pydantic import EmailStr, TypeAdapter, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.user import User, VALID_ROLES

_EMAIL_VALIDATOR = TypeAdapter(EmailStr)


def _parse_email(email: str) -> Optional[str]:
    try:
        return _EMAIL_VALIDATOR.validate_python(email).lower()
    except ValidationError:
        return None


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


async def get_user_by_id(session: AsyncSession, user_id: str) -> User | None:
    """Fetch a user by primary key id."""
    result = await session.execute(select(User).where(User.id == user_id))
    return result.scalar_one_or_none()


async def get_user_by_username_or_email(session: AsyncSession, value: str) -> Optional[User]:
    """Fetch a user by username or email, treating ``@`` as an email indicator."""
    value = value.strip().lower()
    email = _parse_email(value)
    if email:
        return await get_user_by_email(session, email)
    return await get_user_by_username(session, value)


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
