"""
User model.
"""

from datetime import datetime
from enum import Enum
from typing import List, Optional

from sqlalchemy import Boolean, CheckConstraint, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from .base import Base
from .invite import Invite
from .user_link import UserLink


class UserRole(str, Enum):
    """Valid user roles."""

    USER = "user"
    MODERATOR = "moderator"
    ADMIN = "admin"


VALID_ROLES = {r.value for r in UserRole}
_ROLE_CHECK = f"role IN ({', '.join(repr(r) for r in VALID_ROLES)})"


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            _ROLE_CHECK,
            name="ck_users_role",
        ),
    )
    __allow_unmapped__ = True

    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(128))
    display_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    bio: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    avatar_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, insert_default=True, default=True)
    role: Mapped[str] = mapped_column(String(32), insert_default="user", default="user", index=True)
    email_verified: Mapped[bool] = mapped_column(Boolean, insert_default=False, default=False)
    email_verification_token: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    email_verification_token_raw: Optional[str] = None
    password_reset_token: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    password_reset_expires_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    last_login: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    links: Mapped[List["UserLink"]] = relationship(
        "UserLink",
        back_populates="user",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    invites: Mapped[List["Invite"]] = relationship(
        "Invite",
        back_populates="creator",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    # Federation fields
    actor_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True, unique=True)
    private_key_pem: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    public_key_pem: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    @validates("role")
    def _validate_role(self, _: str, value: Optional[str]) -> Optional[str]:
        if value is not None and value not in VALID_ROLES:
            raise ValueError(f"Invalid role: {value}")
        return value

    @validates("avatar_url")
    def _validate_avatar_url(self, _key: str, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        value = value.strip()
        if not value:
            return None
        if not value.startswith(("https://", "http://")):
            raise ValueError("Avatar URL must start with http:// or https://")
        return value

    @property
    def is_admin(self) -> bool:
        """Convenience property for backward compatibility."""
        return self.role == "admin"
