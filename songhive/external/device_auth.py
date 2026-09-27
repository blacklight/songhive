"""
Reusable device-authorization and paste-redirect (PKCE) flows for
external-library providers.

Providers such as TIDAL cannot use the redirect-back OAuth flow in
``external.oauth`` — the user authorizes on the provider's own page (a
"link code" flow) or completes a PKCE flow that terminates at a URI the
provider app cannot redirect back from. This module is the provider-
agnostic side of that handshake:

1. ``begin_flow`` asks the provider hook for a device challenge (or a
   PKCE authorize URL) and stores the pending flow — opaque provider
   session data, poll pacing, expiry — in Redis under a random ``state``
   key bound to the initiating user.
2. ``poll_flow`` performs *one* provider token request per call, honoring
   the provider's poll interval and ``slow_down`` semantics. On grant the
   pending entry is consumed atomically and the resulting config fragment
   is stored under a result key for the SPA to claim once.
3. ``complete_flow`` either claims a granted result (device mode) or, for
   PKCE mode, accepts the user-pasted final redirect URL and lets the
   provider hook exchange it against the stored verifier.

Provider hooks are synchronous by nature (e.g. ``tidalapi``) and are
invoked via ``asyncio.to_thread`` inside this module.
"""

import asyncio
import json
import secrets
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Literal, Optional

from redis.asyncio import Redis

_PENDING_PREFIX = "songhive:external-device-auth:pending:"
_RESULT_PREFIX = "songhive:external-device-auth:result:"
_RESULT_TTL_SECONDS = 300
_POLL_CLOCK_SKEW = 0.5

_DEVICE_AUTH_PROVIDERS: dict[str, "DeviceAuthProvider"] = {}


class DeviceAuthError(Exception):
    """Raised when a device-auth step fails; message is safe to show users."""


@dataclass(frozen=True)
class DeviceAuthChallenge:
    """What a provider returns from a device-code begin call."""

    user_code: str
    verification_uri: str
    verification_uri_complete: Optional[str] = None
    expires_in: int = 300
    interval: int = 5
    """Opaque provider data round-tripped to ``poll_device``."""
    provider_session: dict = field(default_factory=dict)


@dataclass(frozen=True)
class DeviceAuthPollResult:
    """Outcome of a single provider token poll."""

    status: Literal["pending", "slow_down", "expired", "denied", "granted"]
    """Granted config fragment (safe to merge into adapter config)."""
    config_fragment: Optional[dict] = None
    """Non-secret display fields (e.g. username) for the UI."""
    display: dict = field(default_factory=dict)
    error: Optional[str] = None
    """Updated provider session when it must be persisted across polls."""
    provider_session: Optional[dict] = None


class DeviceAuthProvider(ABC):
    """Interface a provider implements to support device auth / PKCE paste.

    Hooks are synchronous — provider SDKs such as ``tidalapi`` block on
    ``requests`` — so the framework invokes them via ``asyncio.to_thread``.
    """

    provider_type: str
    supports_device_code: bool = True
    supports_pkce: bool = False

    @abstractmethod
    def begin_device(self, config: dict) -> DeviceAuthChallenge:
        """Start a device-code flow and return the challenge to display."""
        raise DeviceAuthError("Provider does not support device authorization")

    @abstractmethod
    def poll_device(self, config: dict, session: dict) -> DeviceAuthPollResult:
        """Perform one non-blocking token request for a pending device flow."""
        raise DeviceAuthError("Provider does not support device authorization")

    def begin_pkce(self, config: dict) -> tuple[str, dict]:
        """Return ``(authorize_url, provider_session)`` for a PKCE flow."""
        raise DeviceAuthError("Provider does not support PKCE")

    def complete_pkce(
        self,
        config: dict,
        session: dict,
        redirect_url: str,
    ) -> DeviceAuthPollResult:
        """Exchange a user-pasted redirect URL for a config fragment."""
        raise DeviceAuthError("Provider does not support PKCE")


def register_device_auth_provider(provider: DeviceAuthProvider) -> None:
    """Register a device-auth provider under its provider type."""
    _DEVICE_AUTH_PROVIDERS[provider.provider_type] = provider


def get_device_auth_provider(provider_type: str) -> Optional[DeviceAuthProvider]:
    """Return the device-auth provider for a provider type, or ``None``."""
    return _DEVICE_AUTH_PROVIDERS.get(provider_type)


def device_auth_supported(provider_type: str) -> bool:
    """Return whether the provider supports the interactive device flow."""
    provider = _DEVICE_AUTH_PROVIDERS.get(provider_type)
    return bool(provider and provider.supports_device_code)


def pkce_paste_supported(provider_type: str) -> bool:
    """Return whether the provider supports the paste-redirect PKCE flow."""
    provider = _DEVICE_AUTH_PROVIDERS.get(provider_type)
    return bool(provider and provider.supports_pkce)


def _pending_key(state: str) -> str:
    return f"{_PENDING_PREFIX}{state}"


def _result_key(state: str) -> str:
    return f"{_RESULT_PREFIX}{state}"


def _pending_ttl(provider_expires_in: Optional[int]) -> int:
    """Bound the pending entry by the provider's expiry plus a margin."""
    if provider_expires_in and provider_expires_in > 0:
        return int(provider_expires_in) + 60
    return 900


async def _store_pending(redis: Redis, state: str, pending: dict) -> None:
    ttl = _pending_ttl(pending.get("provider_expires_in"))
    await redis.set(_pending_key(state), json.dumps(pending), ex=ttl)


async def _load_pending(redis: Redis, state: str) -> Optional[dict]:
    raw = await redis.get(_pending_key(state))
    if not raw:
        return None
    try:
        pending = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return pending if isinstance(pending, dict) else None


@dataclass(frozen=True)
class DeviceAuthBeginResponse:
    """Public-facing begin result for the API layer."""

    state: str
    mode: Literal["device", "pkce"]
    user_code: Optional[str] = None
    verification_uri: Optional[str] = None
    verification_uri_complete: Optional[str] = None
    authorize_url: Optional[str] = None
    expires_in: Optional[int] = None
    interval: Optional[int] = None


async def begin_flow(
    redis: Redis,
    provider: DeviceAuthProvider,
    *,
    user_id: str,
    config: dict,
    mode: Literal["device", "pkce"] = "device",
    external_library_id: Optional[str] = None,
) -> DeviceAuthBeginResponse:
    """Start a device-auth or PKCE-paste flow and return display data."""
    if mode == "pkce":
        if not provider.supports_pkce:
            raise DeviceAuthError(f"Provider does not support PKCE: {provider.provider_type}")
        authorize_url, provider_session = await asyncio.to_thread(provider.begin_pkce, config)
        state = secrets.token_urlsafe(24)
        pending: dict[str, Any] = {
            "provider_type": provider.provider_type,
            "user_id": user_id,
            "mode": "pkce",
            "provider_session": provider_session,
            "config": config,
            "external_library_id": external_library_id,
            # PKCE flows have no provider-enforced poll pacing; give the
            # pending entry a generous window while the user pastes back.
            "provider_expires_in": 600,
        }
        await _store_pending(redis, state, pending)
        return DeviceAuthBeginResponse(
            state=state,
            mode="pkce",
            authorize_url=authorize_url,
            expires_in=600,
        )

    if not provider.supports_device_code:
        raise DeviceAuthError(f"Provider does not support device auth: {provider.provider_type}")
    challenge = await asyncio.to_thread(provider.begin_device, config)
    state = secrets.token_urlsafe(24)
    pending = {
        "provider_type": provider.provider_type,
        "user_id": user_id,
        "mode": "device",
        "provider_session": challenge.provider_session,
        "config": config,
        "external_library_id": external_library_id,
        "interval": max(1, int(challenge.interval)),
        "next_poll_at": 0.0,
        "provider_expires_in": int(challenge.expires_in),
        "expires_at": time.time() + int(challenge.expires_in),
    }
    await _store_pending(redis, state, pending)
    return DeviceAuthBeginResponse(
        state=state,
        mode="device",
        user_code=challenge.user_code,
        verification_uri=challenge.verification_uri,
        verification_uri_complete=challenge.verification_uri_complete,
        expires_in=challenge.expires_in,
        interval=challenge.interval,
    )


@dataclass(frozen=True)
class DeviceAuthPollResponse:
    """Public-facing poll result for the API layer."""

    status: Literal["pending", "slow_down", "granted", "expired", "denied", "unknown"]
    retry_after: Optional[float] = None
    detail: Optional[str] = None


async def poll_flow(
    redis: Redis,
    state: str,
    user_id: str,
) -> DeviceAuthPollResponse:
    """
    Perform one provider token poll for the pending flow bound to ``state``.

    Enforces the provider's poll interval server-side so a misbehaving client
    cannot trigger ``slow_down`` penalties by polling too fast.
    """
    pending = await _load_pending(redis, state)
    if pending is None:
        return DeviceAuthPollResponse(status="expired", detail="Flow expired or is invalid")
    if pending.get("user_id") != user_id:
        raise DeviceAuthError("This authorization flow belongs to a different user")
    if pending.get("mode") != "device":
        return DeviceAuthPollResponse(status="pending", detail="PKCE flows complete via submit")

    now = time.time()
    if pending.get("expires_at") and now > float(pending["expires_at"]):
        await redis.delete(_pending_key(state))
        return DeviceAuthPollResponse(status="expired", detail="Authorization code expired")

    next_poll_at = float(pending.get("next_poll_at") or 0.0)
    if now < next_poll_at - _POLL_CLOCK_SKEW:
        return DeviceAuthPollResponse(status="pending", retry_after=max(0.0, next_poll_at - now))

    provider = get_device_auth_provider(str(pending.get("provider_type") or ""))
    if provider is None:
        await redis.delete(_pending_key(state))
        return DeviceAuthPollResponse(status="denied", detail="Provider is no longer available")

    try:
        outcome = await asyncio.to_thread(
            provider.poll_device,
            pending.get("config") or {},
            pending.get("provider_session") or {},
        )
    except DeviceAuthError as exc:
        await redis.delete(_pending_key(state))
        return DeviceAuthPollResponse(status="denied", detail=str(exc)[:200])
    except Exception:
        await redis.delete(_pending_key(state))
        return DeviceAuthPollResponse(status="denied", detail="Provider token request failed")

    if outcome.status == "granted":
        await redis.delete(_pending_key(state))
        await redis.set(
            _result_key(state),
            json.dumps(
                {
                    "provider_type": pending.get("provider_type"),
                    "user_id": user_id,
                    "config": outcome.config_fragment or {},
                    "display": outcome.display,
                }
            ),
            ex=_RESULT_TTL_SECONDS,
        )
        return DeviceAuthPollResponse(status="granted")

    if outcome.status in ("expired", "denied"):
        await redis.delete(_pending_key(state))
        return DeviceAuthPollResponse(status=outcome.status, detail=(outcome.error or outcome.status)[:200])

    # pending / slow_down: update pacing and persist any provider session
    # mutation, then let the client keep polling.
    interval = int(pending.get("interval") or 5)
    if outcome.status == "slow_down":
        interval += 5
    pending["interval"] = interval
    pending["next_poll_at"] = now + interval
    if outcome.provider_session is not None:
        pending["provider_session"] = outcome.provider_session
    await _store_pending(redis, state, pending)
    return DeviceAuthPollResponse(status=outcome.status, retry_after=interval)


@dataclass(frozen=True)
class DeviceAuthCompleteResponse:
    """Completed flow result: the granted adapter config fragment."""

    provider_type: str
    config: dict
    display: dict = field(default_factory=dict)


async def complete_flow(
    redis: Redis,
    state: str,
    user_id: str,
    *,
    redirect_url: Optional[str] = None,
) -> DeviceAuthCompleteResponse:
    """
    Finish a flow: claim a granted device-flow result, or exchange a pasted
    PKCE redirect URL against the stored verifier.
    """
    pending = await _load_pending(redis, state)

    if pending is not None:
        if pending.get("user_id") != user_id:
            raise DeviceAuthError("This authorization flow belongs to a different user")
        if pending.get("mode") != "pkce":
            raise DeviceAuthError("The device authorization has not been granted yet")
        if not redirect_url:
            raise DeviceAuthError("A redirect URL is required to complete this flow")
        provider = get_device_auth_provider(str(pending.get("provider_type") or ""))
        if provider is None:
            raise DeviceAuthError("Provider is no longer available")
        # Consume the pending entry atomically before the exchange so a
        # verifier can never be replayed.
        await redis.getdel(_pending_key(state))
        try:
            outcome = await asyncio.to_thread(
                provider.complete_pkce,
                pending.get("config") or {},
                pending.get("provider_session") or {},
                redirect_url,
            )
        except DeviceAuthError:
            raise
        except Exception as exc:
            raise DeviceAuthError("Token exchange failed") from exc
        if outcome.status != "granted" or not outcome.config_fragment:
            raise DeviceAuthError(outcome.error or "Token exchange returned no tokens")
        return DeviceAuthCompleteResponse(
            provider_type=str(pending.get("provider_type") or ""),
            config=outcome.config_fragment,
            display=outcome.display,
        )

    # No pending flow: either it was granted (result waiting) or it expired.
    raw = await redis.get(_result_key(state))
    if raw:
        try:
            result = json.loads(raw)
        except (TypeError, ValueError):
            result = None
        if isinstance(result, dict):
            if result.get("user_id") != user_id:
                raise DeviceAuthError("This authorization flow belongs to a different user")
            await redis.delete(_result_key(state))
            return DeviceAuthCompleteResponse(
                provider_type=str(result.get("provider_type") or ""),
                config=result.get("config") or {},
                display=result.get("display") or {},
            )

    raise DeviceAuthError("Authorization flow expired or is invalid")
