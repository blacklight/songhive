"""
Secret-at-rest helpers for external library configuration.

Secrets are encrypted with Fernet, keyed from a SHA-256 hash of the
configured ``auth.secret_key``. Rotating ``auth.secret_key`` will
invalidate any previously encrypted external-library configuration.
"""

import base64
import hashlib
import json
from typing import Any

_fernet: Any = None

_SECRET_NAME_TOKENS = frozenset(["secret", "password", "token", "key", "credential"])


def _import_fernet() -> Any:
    """Import Fernet, raising a clear error if cryptography is missing."""
    try:
        from cryptography.fernet import Fernet

        return Fernet
    except ImportError as exc:
        raise RuntimeError(
            "The 'cryptography' package is required for secret-at-rest encryption. "
            "Install it with: pip install cryptography"
        ) from exc


def _get_fernet() -> Any:
    """Return a lazily-initialized Fernet instance keyed from auth.secret_key."""
    global _fernet
    if _fernet is None:
        from ..config.loader import load_config

        config = load_config([])
        secret = config.auth.secret_key
        key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())
        Fernet = _import_fernet()
        _fernet = Fernet(key)
    return _fernet


def encrypt_secret(plaintext: str) -> str:
    """Encrypt ``plaintext`` and return a URL-safe Fernet token string."""
    token = _get_fernet().encrypt(plaintext.encode("utf-8"))
    return token.decode("utf-8")


def decrypt_secret(token: str) -> str:
    """Decrypt a Fernet token string and return the plaintext."""
    plaintext = _get_fernet().decrypt(token.encode("utf-8"))
    return plaintext.decode("utf-8")


def encrypt_json(obj: dict) -> str:
    """Serialize ``obj`` to JSON and encrypt the result."""
    return encrypt_secret(json.dumps(obj, separators=(",", ":")))


def decrypt_json(token: str) -> dict:
    """Decrypt ``token`` and parse the result as JSON."""
    return json.loads(decrypt_secret(token))


def _is_secret_key(key: str) -> bool:
    """Return whether ``key`` looks like a secret-bearing field name."""
    lower = key.lower()
    return any(token in lower for token in _SECRET_NAME_TOKENS)


def redact_config(config: dict) -> dict:
    """
    Shallow-clone ``config`` and replace any key matching the secret-name
    heuristic (``secret``, ``password``, ``token``, ``key``, ``credential``)
    with ``"<redacted>"``.
    """
    redacted = dict(config)
    for key in redacted:
        if _is_secret_key(key):
            redacted[key] = "<redacted>"
    return redacted
