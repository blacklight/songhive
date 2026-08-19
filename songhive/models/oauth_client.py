"""
OAuth2 client registration model.

This model stores registered OAuth2 clients. Client secrets are stored as a
bcrypt hash; the raw secret is exposed only once when the client is created.
"""

from typing import Optional

from sqlalchemy import JSON, Boolean, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


def _default_grant_types() -> list[str]:
    """Return the default grant types for a new OAuth2 client."""
    return ["authorization_code"]


class OAuth2Client(Base):
    """
    SQLAlchemy model for an OAuth2 client.
    """

    __tablename__ = "oauth_clients"

    client_id: Mapped[str] = mapped_column(
        String(64),
        unique=True,
        index=True,
        nullable=False,
    )
    client_secret_hash: Mapped[Optional[str]] = mapped_column(
        String(128),
        nullable=True,
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    redirect_uris: Mapped[list[str]] = mapped_column(
        JSON,
        default=list,
        nullable=False,
    )
    grant_types: Mapped[list[str]] = mapped_column(
        JSON,
        default=_default_grant_types,
        nullable=False,
    )
    owner_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    is_confidential: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        insert_default=True,
        nullable=False,
    )
