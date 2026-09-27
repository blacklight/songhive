"""
TIDAL session construction and token management.

``tidalapi.Session`` is synchronous and built on ``requests``; every
blocking call is delegated to ``asyncio.to_thread`` by the callers in this
package. Access tokens are cached in Redis (Fernet-encrypted) keyed by a
hash of the refresh token so that a stream request never triggers a token
refresh on the hot path: a refresh happens at most once per expiry window
and is serialized across workers by a Redis lock.

The adapter config fragment (produced by the device-auth or PKCE flow)
carries:

- ``token_type`` / ``access_token`` / ``refresh_token`` / ``expiry_time``
- ``is_pkce`` — PKCE grants are the only ones TIDAL allows for Hi-Res
- ``user_id`` / ``country_code`` — needed to rebuild a session without an
  extra ``GET /sessions`` round trip
- display-only ``username`` / ``email``
- optional ``client_id``/``client_secret``/``client_id_pkce``/
  ``client_secret_pkce`` overrides (fall back to tidalapi built-ins)
"""

import asyncio
import hashlib
import logging
import time
from datetime import datetime, timezone
from typing import Any, Optional

import requests
from redis.asyncio import Redis

from ..errors import ExternalConfigError, ExternalPermissionDenied

logger = logging.getLogger(__name__)

_TOKEN_CACHE_PREFIX = "songhive:tidal:session:"
_REFRESH_LOCK_PREFIX = "songhive:tidal:refresh:"
_REFRESH_LOCK_TIMEOUT = 30
_REFRESH_LOCK_BLOCKING = 15
# Re-refresh this many seconds before the stated expiry.
_EXPIRY_SKEW_SECONDS = 60
# requests has no built-in default timeout: without one a stalled TIDAL
# socket can park a Celery task forever, exactly the silent-hang failure
# mode this bound exists to prevent.
_DEFAULT_REQUEST_TIMEOUT = 30.0


class _TimeoutSession(requests.Session):
    """``requests.Session`` applying a default timeout to every request.

    tidalapi funnels every call (API requests *and* token refreshes)
    through ``session.request_session`` without ever passing ``timeout``,
    so swapping in this subclass bounds all of them at once.
    """

    def __init__(self, timeout: float) -> None:
        super().__init__()
        self._default_timeout = timeout

    def request(self, method: Any, url: Any, *args: Any, **kwargs: Any) -> Any:
        kwargs.setdefault("timeout", self._default_timeout)
        return super().request(method, url, *args, **kwargs)


def _request_timeout_seconds(config: dict) -> float:
    """Resolve the HTTP timeout: per-library override, else instance config."""
    raw = config.get("request_timeout_seconds")
    if isinstance(raw, (int, float)) and raw > 0:
        return float(raw)
    try:
        from ...config.loader import load_config

        instance = load_config([]).external_libraries.tidal.request_timeout_seconds
        if instance > 0:
            return float(instance)
    except Exception:
        pass
    return _DEFAULT_REQUEST_TIMEOUT


_QUALITY_VALUES = ("LOW", "HIGH", "LOSSLESS", "HI_RES_LOSSLESS")


def _import_tidalapi():
    """Import tidalapi lazily so a broken install only disables this provider."""
    try:
        import tidalapi
        from tidalapi.user import LoggedInUser

        return tidalapi, LoggedInUser
    except ImportError as exc:
        raise ExternalConfigError(
            "The 'tidalapi' package is required for TIDAL libraries",
        ) from exc


def _config_fingerprint(config: dict) -> str:
    refresh = str(config.get("refresh_token") or "")
    return hashlib.sha256(refresh.encode("utf-8")).hexdigest()


def _token_cache_key(config: dict) -> str:
    return f"{_TOKEN_CACHE_PREFIX}{_config_fingerprint(config)}"


def _refresh_lock_key(config: dict) -> str:
    return f"{_REFRESH_LOCK_PREFIX}{_config_fingerprint(config)}"


def effective_quality(config: dict) -> str:
    """Return the audio quality to request, honoring the PKCE Hi-Res gate."""
    raw = str(config.get("quality") or "LOSSLESS").upper()
    if raw not in _QUALITY_VALUES:
        raw = "LOSSLESS"
    if raw == "HI_RES_LOSSLESS" and not config.get("is_pkce"):
        return "LOSSLESS"
    return raw


def build_session(config: dict, *, with_user: bool = True):
    """
    Build a ``tidalapi.Session`` from the stored config fragment.

    Restores token state by assigning attributes directly — deliberately
    avoiding ``load_oauth_session``'s ``GET /sessions`` round trip — and
    reconstructs ``session.user`` from the stored ``user_id`` so
    ``session.user.favorites`` works without network access.
    """
    tidalapi, LoggedInUser = _import_tidalapi()

    access_token = config.get("access_token")
    refresh_token = config.get("refresh_token")
    if not isinstance(access_token, str) or not access_token:
        raise ExternalConfigError(
            'config["access_token"] is required; connect the account via device auth',
            field="access_token",
        )

    session = tidalapi.Session()
    session.request_session = _TimeoutSession(_request_timeout_seconds(config))

    # Optional admin-configured client credentials override the built-ins.
    for field_name, attr in (
        ("client_id", "client_id"),
        ("client_secret", "client_secret"),
        ("client_id_pkce", "client_id_pkce"),
        ("client_secret_pkce", "client_secret_pkce"),
    ):
        value = config.get(field_name)
        if isinstance(value, str) and value.strip():
            setattr(session.config, attr, value.strip())

    session.token_type = str(config.get("token_type") or "Bearer")
    session.access_token = access_token
    session.refresh_token = refresh_token if isinstance(refresh_token, str) else None
    session.is_pkce = bool(config.get("is_pkce"))

    expiry_raw = config.get("expiry_time")
    if isinstance(expiry_raw, (int, float)):
        session.expiry_time = datetime.fromtimestamp(float(expiry_raw), tz=timezone.utc).replace(tzinfo=None)
    elif isinstance(expiry_raw, str) and expiry_raw:
        try:
            parsed = datetime.fromisoformat(expiry_raw.replace("Z", "+00:00"))
            session.expiry_time = parsed.replace(tzinfo=None)
        except ValueError:
            session.expiry_time = None
    else:
        session.expiry_time = None

    session_id = config.get("session_id")
    if isinstance(session_id, str) and session_id:
        session.session_id = session_id
    country_code = config.get("country_code")
    if isinstance(country_code, str) and country_code:
        session.country_code = country_code

    if with_user:
        user_id = config.get("user_id")
        if user_id is None:
            raise ExternalConfigError(
                'config["user_id"] is required; reconnect the account via device auth',
                field="user_id",
            )
        try:
            session.user = LoggedInUser(session, int(user_id))
        except (TypeError, ValueError) as exc:
            raise ExternalConfigError(
                'config["user_id"] must be an integer',
                field="user_id",
            ) from exc

    session.config.quality = effective_quality(config)
    return session


def _expired(config: dict, skew: int = _EXPIRY_SKEW_SECONDS) -> bool:
    """Return whether the stored access token needs a refresh."""
    expiry_raw = config.get("expiry_time")
    if expiry_raw is None:
        # Unknown expiry: assume usable; the API layer retries on 401 anyway.
        return False
    if isinstance(expiry_raw, (int, float)):
        expires_at = float(expiry_raw)
    elif isinstance(expiry_raw, str):
        try:
            expires_at = datetime.fromisoformat(expiry_raw.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return True
    else:
        return True
    return time.time() >= expires_at - skew


async def session_for_config(
    config: dict,
    redis: Optional[Redis],
) -> Any:
    """
    Return a ready-to-use ``tidalapi.Session`` for ``config``.

    Fast path: the stored access token is still valid → build and return.
    Otherwise check the Redis token cache, and only on a miss refresh under
    a Redis lock so concurrent stream requests don't stampede TIDAL.
    """
    session = build_session(config)
    if not session.refresh_token or not _expired(config):
        return session
    if redis is None:
        # No cache available: refresh inline (still inside a thread).
        await asyncio.to_thread(_refresh_session, session, config)
        return session

    cache_key = _token_cache_key(config)
    raw = await redis.get(cache_key)
    if raw:
        try:
            from ...services.secrets import decrypt_json

            cached = decrypt_json(raw)
            if isinstance(cached.get("access_token"), str):
                session.access_token = cached["access_token"]
                return session
        except Exception:
            logger.debug("TIDAL token cache entry unreadable; refreshing")

    lock_key = _refresh_lock_key(config)
    async with redis.lock(
        lock_key,
        timeout=_REFRESH_LOCK_TIMEOUT,
        blocking_timeout=_REFRESH_LOCK_BLOCKING,
    ):
        # Double-check after acquiring: another worker may have refreshed.
        raw = await redis.get(cache_key)
        if raw:
            try:
                from ...services.secrets import decrypt_json

                cached = decrypt_json(raw)
                if isinstance(cached.get("access_token"), str):
                    session.access_token = cached["access_token"]
                    return session
            except Exception:
                pass

        await asyncio.to_thread(_refresh_session, session, config)
        await _cache_token(redis, cache_key, session)
    return session


def _refresh_session(session: Any, config: dict) -> None:
    """Refresh ``session.access_token`` via the stored refresh token."""
    try:
        ok = session.token_refresh(session.refresh_token)
    except Exception as exc:
        raise ExternalPermissionDenied(
            "TIDAL token refresh failed; reconnect the account",
            operation="token_refresh",
        ) from exc
    if not ok:
        raise ExternalPermissionDenied(
            "TIDAL refresh token was rejected; reconnect the account",
            operation="token_refresh",
        )


async def _cache_token(redis: Redis, cache_key: str, session: Any) -> None:
    """Store the refreshed token in Redis (Fernet-encrypted) for other workers."""
    expiry = getattr(session, "expiry_time", None)
    ttl = 0
    expiry_ts: Optional[float] = None
    if isinstance(expiry, datetime):
        expiry_ts = expiry.replace(tzinfo=timezone.utc).timestamp()
        ttl = int(expiry_ts - time.time()) - _EXPIRY_SKEW_SECONDS
    if ttl <= 0:
        return
    try:
        from ...services.secrets import encrypt_json

        await redis.set(
            cache_key,
            encrypt_json(
                {
                    "access_token": session.access_token,
                    "expiry_time": expiry_ts,
                }
            ),
            ex=ttl,
        )
    except Exception:
        logger.warning("Failed to cache refreshed TIDAL token", exc_info=True)


def credential_fragment_from_session(session: Any, *, is_pkce: bool) -> dict:
    """
    Build the adapter config fragment from an authenticated session.

    Sensitive fields (``access_token``/``refresh_token``) are redacted by the
    standard config redaction on the way out and encrypted at rest.
    """
    expiry = getattr(session, "expiry_time", None)
    fragment: dict[str, Any] = {
        "token_type": getattr(session, "token_type", None) or "Bearer",
        "access_token": session.access_token,
        "refresh_token": session.refresh_token,
        "is_pkce": is_pkce,
        "session_id": getattr(session, "session_id", None),
        "country_code": getattr(session, "country_code", None),
        "user_id": getattr(getattr(session, "user", None), "id", None),
    }
    if isinstance(expiry, datetime):
        fragment["expiry_time"] = expiry.replace(tzinfo=timezone.utc).isoformat()
    display = {}
    user = getattr(session, "user", None)
    for attr in ("username", "email"):
        value = getattr(user, attr, None)
        if isinstance(value, str) and value:
            display[attr] = value
    fragment.update(display)
    return fragment


def fragment_expires_in(config: dict) -> Optional[int]:
    """Return seconds until the stored access token expires (None if unknown)."""
    expiry_raw = config.get("expiry_time")
    if isinstance(expiry_raw, (int, float)):
        return max(0, int(float(expiry_raw) - time.time()))
    if isinstance(expiry_raw, str) and expiry_raw:
        try:
            ts = datetime.fromisoformat(expiry_raw.replace("Z", "+00:00")).timestamp()
            return max(0, int(ts - time.time()))
        except ValueError:
            return None
    return None
