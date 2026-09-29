"""
Small crypto helpers for payment data.

Guest emails are encrypted at rest with a Fernet instance derived from
``auth.secret_key`` under a payments-specific domain separation label, so
order rows never contain plaintext PII and rotating the auth key is the only
key-management step an operator takes. ``buyer_email_key_id`` records the
derivation label so a future key rotation can re-wrap old rows.

Capability and login tokens are never stored raw: only their SHA-256 digests
hit the database.
"""

import base64
import hashlib
import secrets
from typing import Any, Optional

_fernet: Any = None

_KEY_ID = "payments-v1"


def _get_fernet() -> Any:
    """Return a lazily-initialized Fernet instance keyed for payments."""
    global _fernet
    if _fernet is None:
        from cryptography.fernet import Fernet

        from ...config.loader import load_config

        config = load_config([])
        digest = hashlib.sha256(f"songhive:{_KEY_ID}:{config.auth.secret_key}".encode()).digest()
        _fernet = Fernet(base64.urlsafe_b64encode(digest))
    return _fernet


def encrypt_email(email: str) -> str:
    """Encrypt a buyer email address for storage on a payment order."""
    return _get_fernet().encrypt(email.encode("utf-8")).decode("utf-8")


def decrypt_email(token: str) -> str:
    """Decrypt a stored buyer email token."""
    return _get_fernet().decrypt(token.encode("utf-8")).decode("utf-8")


def email_key_id() -> str:
    """Return the key derivation label stored alongside encrypted emails."""
    return _KEY_ID


def email_hash(email: str) -> str:
    """Return the normalized-email digest used for lookups and audit joins."""
    return hashlib.sha256(normalize_email(email).encode("utf-8")).hexdigest()


def normalize_email(email: str) -> str:
    """Normalize an email for hashing: trimmed and case-folded."""
    return email.strip().lower()


def new_token() -> str:
    """Return a fresh URL-safe opaque token."""
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    """Return the SHA-256 digest stored instead of a raw capability token."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def verify_token(token: str, expected_hash: Optional[str]) -> bool:
    """Constant-time comparison of a raw token against its stored digest."""
    if not expected_hash:
        return False
    return secrets.compare_digest(token_hash(token), expected_hash)
