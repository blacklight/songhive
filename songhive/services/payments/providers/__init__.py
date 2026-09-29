"""
Payment provider registry.

``get_provider`` resolves the configured provider once per process. Tests
substitute the fake by monkeypatching :func:`get_provider` or calling
:func:`set_provider_override`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from .base import (
    CheckoutSessionResult,
    CheckoutSessionSpec,
    ConnectedAccountState,
    PaymentProvider,
    ProviderError,
    ProviderEvent,
    SignatureVerificationError,
    SubscriptionState,
    connected_account_state,
)
from .fake import FakePaymentProvider

if TYPE_CHECKING:  # pragma: no cover
    from ....config.schema import SonghiveConfig

__all__ = [
    "CheckoutSessionResult",
    "CheckoutSessionSpec",
    "ConnectedAccountState",
    "FakePaymentProvider",
    "PaymentProvider",
    "ProviderError",
    "ProviderEvent",
    "SignatureVerificationError",
    "SubscriptionState",
    "connected_account_state",
    "get_provider",
    "set_provider_override",
]

_provider: Optional[PaymentProvider] = None
_override: Optional[PaymentProvider] = None


def set_provider_override(provider: Optional[PaymentProvider]) -> None:
    """Install (or clear) a test-time provider override."""
    global _override
    _override = provider


def get_provider(config: SonghiveConfig) -> PaymentProvider:
    """Return the configured payment provider, constructing it lazily."""
    global _provider
    if _override is not None:
        return _override
    if _provider is None:
        from .stripe_provider import StripeProvider

        _provider = StripeProvider(config)
    return _provider
