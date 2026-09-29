"""
Payments services.

The public surface re-exported here is the single entry point for payment
concerns; routes and other services should not reach into submodules for
things re-exported below. ``access.resolve_track_access`` is the single
source of truth for sale gating; ``membership.sync_user_active_flag`` is the
single writer of ``User.is_active`` for payment state.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional
from urllib.parse import urlparse

if TYPE_CHECKING:  # pragma: no cover
    from ...config.schema import SonghiveConfig


def public_base_url(config: SonghiveConfig) -> Optional[str]:
    """
    Return the canonical public base URL for payment flows, or ``None``.

    Payment features must fail closed without one: Stripe redirects, emailed
    redeem links, and capability URLs all require an absolute HTTPS origin
    that buyers can actually reach. ``payments.public_base_url`` wins; the
    federation instance domain is the fallback. ``http://localhost``-style
    origins are accepted only in debug mode so local development works.
    """
    raw = config.payments.public_base_url
    if raw:
        parsed = urlparse(raw)
        if parsed.scheme == "https" and parsed.netloc:
            return raw.rstrip("/")
        host = parsed.hostname or ""
        if config.server.debug and parsed.scheme == "http" and host in {"localhost", "127.0.0.1", "::1"}:
            return raw.rstrip("/")
        return None
    domain = (config.federation.instance_domain or "").strip()
    if domain:
        return f"https://{domain}"
    return None


def payments_enabled(config: SonghiveConfig) -> bool:
    """Return whether payment features are enabled by configuration."""
    return bool(config.payments.enabled)


def payments_ready(config: SonghiveConfig) -> bool:
    """
    Return whether payments are fully configured for live use.

    Requires ``enabled``, a provider secret key, and a canonical public base
    URL. Features that would have to build buyer-facing URLs fail closed when
    this is False.
    """
    if not config.payments.enabled:
        return False
    if not config.payments.stripe_secret_key:
        return False
    return public_base_url(config) is not None


def payments_advertised(config: SonghiveConfig) -> bool:
    """
    Return whether payments are exposed in public metadata.

    Unlike :func:`payments_ready` this does not require secrets to be set —
    it tells prospective members/buyers that the instance sells memberships
    or media, and lets the UI render purchase controls early.
    """
    return bool(config.payments.enabled)


from . import access, fulfillment, membership, sales, samples, webhooks  # noqa: E402,F401
from .errors import PaymentError  # noqa: E402,F401

__all__ = [
    "PaymentError",
    "access",
    "fulfillment",
    "membership",
    "payments_advertised",
    "payments_enabled",
    "payments_ready",
    "public_base_url",
    "sales",
    "samples",
    "webhooks",
]
