"""
Two-factor authentication models.

The TOTP shared secret lives on the ``users`` row (encrypted at rest);
WebAuthn/FIDO2 security keys and single-use recovery codes get their own
tables keyed by ``user_id``.
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import ForeignKey, LargeBinary, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TZDateTime


class WebAuthnCredential(Base):
    """A registered WebAuthn/FIDO2 security key for a user."""

    __tablename__ = "webauthn_credentials"

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    # base64url-encoded credential id as reported by the authenticator.
    credential_id: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    # Serialized ``fido2.webauthn.AttestedCredentialData`` blob.
    credential_data: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    # Comma-separated transport hints ("usb", "nfc", "ble", "internal").
    transports: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)


class RecoveryCode(Base):
    """A single-use recovery code, stored as a SHA-256 hash."""

    __tablename__ = "recovery_codes"

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    used_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime(), nullable=True)
