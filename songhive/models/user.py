"""
User model.
"""

from datetime import datetime
from enum import Enum
from typing import List, Optional

from sqlalchemy import Boolean, CheckConstraint, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from .base import Base, TZDateTime
from .invite import Invite
from .user_link import UserLink


class UserRole(str, Enum):
    """Valid user roles."""

    USER = "user"
    MODERATOR = "moderator"
    ADMIN = "admin"


class ProfileVisibility(str, Enum):
    """Directory visibility levels for user profiles."""

    PUBLIC = "public"
    LOCAL = "local"
    PRIVATE = "private"


class FollowersApproval(str, Enum):
    """How new follower requests are handled."""

    ACCEPT = "accept"
    MANUAL = "manual"
    REJECT = "reject"


VALID_ROLES = {r.value for r in UserRole}
VALID_PROFILE_VISIBILITIES = {v.value for v in ProfileVisibility}
VALID_FOLLOWERS_APPROVALS = {v.value for v in FollowersApproval}
_ROLE_CHECK = f"role IN ({', '.join(repr(r) for r in VALID_ROLES)})"
_PROFILE_VISIBILITY_CHECK = f"profile_visibility IN ({', '.join(repr(v) for v in VALID_PROFILE_VISIBILITIES)})"
_FOLLOWERS_APPROVAL_CHECK = f"followers_approval IN ({', '.join(repr(v) for v in VALID_FOLLOWERS_APPROVALS)})"


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            _ROLE_CHECK,
            name="ck_users_role",
        ),
        CheckConstraint(
            _PROFILE_VISIBILITY_CHECK,
            name="ck_users_profile_visibility",
        ),
        CheckConstraint(
            _FOLLOWERS_APPROVAL_CHECK,
            name="ck_users_followers_approval",
        ),
    )
    __allow_unmapped__ = True

    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(128))
    display_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    bio: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    avatar_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    role: Mapped[str] = mapped_column(String(32), default="user", index=True)
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    email_verification_token: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    email_verification_token_raw: Optional[str] = None
    password_reset_token: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    password_reset_expires_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime(), nullable=True)
    last_login: Mapped[Optional[datetime]] = mapped_column(TZDateTime(), nullable=True)
    status_content_type: Mapped[str] = mapped_column(
        String(64),
        default="text/markdown",
        server_default="text/markdown",
    )
    preview_cards_enabled: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        server_default="1",
    )
    profile_visibility: Mapped[str] = mapped_column(
        String(16),
        default="public",
        server_default="public",
    )
    followers_approval: Mapped[str] = mapped_column(
        String(16),
        default="accept",
        server_default="accept",
    )
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

    # Two-factor authentication: encrypted TOTP shared secret. Security keys
    # and recovery codes live in their own tables keyed by user id.
    totp_secret: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Federation fields
    actor_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True, unique=True)
    private_key_pem: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    public_key_pem: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    @validates("role")
    def _validate_role(self, _: str, value: Optional[str]) -> Optional[str]:
        if value is not None and value not in VALID_ROLES:
            raise ValueError(f"Invalid role: {value}")
        return value

    @validates("profile_visibility")
    def _validate_profile_visibility(self, _: str, value: Optional[str]) -> Optional[str]:
        if value is not None and value not in VALID_PROFILE_VISIBILITIES:
            raise ValueError(f"Invalid profile_visibility: {value}")
        return value

    @validates("followers_approval")
    def _validate_followers_approval(self, _: str, value: Optional[str]) -> Optional[str]:
        if value is not None and value not in VALID_FOLLOWERS_APPROVALS:
            raise ValueError(f"Invalid followers_approval: {value}")
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
