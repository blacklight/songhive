"""
YouTube credential handling: OAuth tokens and browser-session cookies.

Two credential shapes live on the (encrypted) library config:

- **OAuth** (``auth_mode="oauth"``, set by the device-authorization flow):
  ``access_token``/``refresh_token``/``expiry_time`` plus the
  ``client_id``/``client_secret`` pair that issued them. Tokens drive both
  the YouTube Music API (as a ytmusicapi OAuth token dict) and the YouTube
  Data API v3 (as a Bearer credential).
- **Browser** (``auth_mode="browser"``): pasted request headers or a
  Netscape cookie export. Drives ytmusicapi's BROWSER auth and yt-dlp's
  cookiefile — see ``cookies.py``.

OAuth access tokens are short-lived; the refresh is cached in Redis
(Fernet-encrypted, keyed by the refresh-token hash) under a per-account
lock so concurrent stream requests never stampede Google's token endpoint.
"""

import asyncio
import hashlib
import logging
import time
from datetime import datetime
from typing import Any, Optional

import requests
from redis.asyncio import Redis

from ..errors import ExternalConfigError, ExternalPermissionDenied
from .conf import client_id, client_secret, request_timeout_seconds
from .cookies import auth_headers_for_config

logger = logging.getLogger(__name__)

_TOKEN_CACHE_PREFIX = "songhive:youtube:session:"
_REFRESH_LOCK_PREFIX = "songhive:youtube:refresh:"
_REFRESH_LOCK_TIMEOUT = 30
_REFRESH_LOCK_BLOCKING = 15
# Re-refresh this many seconds before the stated expiry.
_EXPIRY_SKEW_SECONDS = 60


def _import_ytmusicapi():
    """Import ytmusicapi lazily so a broken install only disables this provider."""
    try:
        from ytmusicapi import YTMusic
        from ytmusicapi.auth.oauth import OAuthCredentials

        return YTMusic, OAuthCredentials
    except ImportError as exc:
        raise ExternalConfigError(
            "The 'ytmusicapi' package is required for YouTube libraries",
        ) from exc


def auth_mode(config: dict) -> Optional[str]:
    """Return ``oauth`` or ``browser`` depending on the stored credentials."""
    explicit = str(config.get("auth_mode") or "").lower()
    if explicit in ("oauth", "browser"):
        return explicit
    if config.get("access_token") or config.get("refresh_token"):
        return "oauth"
    if auth_headers_for_config(config) is not None or config.get("cookies"):
        return "browser"
    return None


def oauth_credentials(config: dict) -> Any:
    """Return a ytmusicapi ``OAuthCredentials`` for the configured client pair."""
    _, OAuthCredentials = _import_ytmusicapi()
    cid, csecret = client_id(config), client_secret(config)
    if not cid or not csecret:
        raise ExternalConfigError(
            "YouTube OAuth client credentials are not configured; "
            "set external_libraries.youtube.client_id/client_secret or "
            "use the browser-session auth mode",
            field="client_id",
        )
    return OAuthCredentials(cid, csecret)


def _expiry_ts(config: dict) -> Optional[float]:
    raw = config.get("expiry_time")
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, str) and raw:
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


def _expired(config: dict, skew: int = _EXPIRY_SKEW_SECONDS) -> bool:
    expires_at = _expiry_ts(config)
    if expires_at is None:
        return False
    return time.time() >= expires_at - skew


def _config_fingerprint(config: dict) -> str:
    refresh = str(config.get("refresh_token") or config.get("cookies") or "")
    return hashlib.sha256(refresh.encode("utf-8")).hexdigest()


def _refresh_token_now(config: dict) -> dict:
    """Synchronous OAuth refresh_token grant; returns the token payload."""
    credentials = oauth_credentials(config)
    try:
        payload = credentials.refresh_token(str(config["refresh_token"]))
    except Exception as exc:
        raise ExternalPermissionDenied(
            "YouTube token refresh failed; reconnect the account",
            operation="token_refresh",
        ) from exc
    if not isinstance(payload, dict) or not payload.get("access_token"):
        error = payload.get("error") if isinstance(payload, dict) else None
        raise ExternalPermissionDenied(
            f"YouTube refresh token was rejected ({error or 'no token'}); reconnect the account",
            operation="token_refresh",
        )
    return payload


async def _cache_token(redis: Redis, config: dict, access_token: str, expiry_ts: Optional[float]) -> None:
    """Store the refreshed token encrypted in Redis for other workers."""
    ttl = int((expiry_ts or 0) - time.time()) - _EXPIRY_SKEW_SECONDS
    if ttl <= 0:
        return
    try:
        from ...services.secrets import encrypt_json

        await redis.set(
            f"{_TOKEN_CACHE_PREFIX}{_config_fingerprint(config)}",
            encrypt_json({"access_token": access_token, "expiry_time": expiry_ts}),
            ex=ttl,
        )
    except Exception:
        logger.warning("Failed to cache refreshed YouTube token", exc_info=True)


async def _cached_token(redis: Redis, config: dict) -> Optional[dict]:
    try:
        raw = await redis.get(f"{_TOKEN_CACHE_PREFIX}{_config_fingerprint(config)}")
    except Exception:
        return None
    if not raw:
        return None
    try:
        from ...services.secrets import decrypt_json

        cached = decrypt_json(raw)
    except Exception:
        return None
    return cached if isinstance(cached.get("access_token"), str) else None


async def valid_access_token(config: dict, redis: Optional[Redis] = None) -> str:
    """
    Return a usable OAuth access token for ``config``.

    Fast path: the stored (or Redis-cached) token is still valid. Otherwise
    refresh under a per-account lock so only one request pays the round trip.
    """
    mode = auth_mode(config)
    if mode != "oauth":
        raise ExternalConfigError(
            "This operation requires OAuth credentials; connect the account via device auth",
            field="access_token",
        )
    access_token = config.get("access_token")
    if isinstance(access_token, str) and access_token and not _expired(config):
        return access_token
    if not config.get("refresh_token"):
        raise ExternalConfigError(
            'config["refresh_token"] is required to refresh the session; reconnect the account',
            field="refresh_token",
        )
    if redis is None:
        payload = await asyncio.to_thread(_refresh_token_now, config)
        return str(payload["access_token"])

    cached = await _cached_token(redis, config)
    if cached is not None:
        return str(cached["access_token"])

    lock_key = f"{_REFRESH_LOCK_PREFIX}{_config_fingerprint(config)}"
    async with redis.lock(
        lock_key,
        timeout=_REFRESH_LOCK_TIMEOUT,
        blocking_timeout=_REFRESH_LOCK_BLOCKING,
    ):
        # Double-check after acquiring: another worker may have refreshed.
        cached = await _cached_token(redis, config)
        if cached is not None:
            return str(cached["access_token"])

        payload = await asyncio.to_thread(_refresh_token_now, config)
        expiry_ts = time.time() + float(payload.get("expires_in") or 0) or None
        await _cache_token(redis, config, str(payload["access_token"]), expiry_ts)
        return str(payload["access_token"])


def _oauth_token_dict(config: dict, access_token: str) -> dict:
    """Build the token dict ytmusicapi's OAuth auth expects."""
    return {
        "token_type": str(config.get("token_type") or "Bearer"),
        "access_token": access_token,
        "refresh_token": config.get("refresh_token"),
        "scope": str(config.get("scope") or "https://www.googleapis.com/auth/youtube"),
        "expires_in": int(config.get("expires_in") or 3600),
        "expires_at": int(time.time() + int(config.get("expires_in") or 3600)),
    }


def _timeout_session(timeout: float) -> requests.Session:
    """A ``requests.Session`` that applies a default timeout to every call."""

    session = requests.Session()
    original = session.request

    def _request(method, url, *args, **kwargs):
        kwargs.setdefault("timeout", timeout)
        return original(method, url, *args, **kwargs)

    session.request = _request  # type: ignore[method-assign]
    return session


async def ytmusic_for_config(config: dict, redis: Optional[Redis] = None) -> Any:
    """
    Build an authenticated ``YTMusic`` client for the library config.

    OAuth sessions get a fresh-enough access token (self-refreshing via the
    stored refresh token); browser sessions pass their captured headers to
    ytmusicapi's cookie auth.
    """
    YTMusic, _ = _import_ytmusicapi()
    timeout = request_timeout_seconds(config)
    session = _timeout_session(timeout)

    mode = auth_mode(config)
    if mode == "oauth":
        access_token = await valid_access_token(config, redis)
        return await asyncio.to_thread(
            YTMusic,
            _oauth_token_dict(config, access_token),
            requests_session=session,
            oauth_credentials=oauth_credentials(config),
        )
    if mode == "browser":
        headers = auth_headers_for_config(config)
        return await asyncio.to_thread(YTMusic, headers or {}, requests_session=session)
    raise ExternalConfigError(
        "YouTube credentials are required; connect via device auth or paste a browser session",
        field="access_token",
    )


def credential_fragment_from_token(
    token: dict,
    *,
    oauth_client_id: str,
    oauth_client_secret: str,
    display: Optional[dict] = None,
) -> dict:
    """Build the adapter config fragment from a granted device-flow token."""
    fragment: dict[str, Any] = {
        "auth_mode": "oauth",
        "token_type": token.get("token_type") or "Bearer",
        "access_token": token.get("access_token"),
        "refresh_token": token.get("refresh_token"),
        "expires_in": token.get("expires_in"),
        "scope": token.get("scope"),
        "client_id": oauth_client_id,
        "client_secret": oauth_client_secret,
    }
    expiry = token.get("expires_at")
    if isinstance(expiry, (int, float)) and expiry:
        fragment["expiry_time"] = float(expiry)
    elif isinstance(token.get("expires_in"), (int, float)):
        fragment["expiry_time"] = time.time() + float(token["expires_in"])
    for key in ("account_name", "channel_handle"):
        if display and display.get(key):
            fragment[key] = display[key]
    return fragment
