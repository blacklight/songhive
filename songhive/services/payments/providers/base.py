"""
Payment provider abstraction.

All provider communication (hosted checkout, subscriptions, connected
accounts, refunds, webhook verification) goes through this interface. The
application code never talks to Stripe directly — swap-in providers
implement the same surface, and tests use :class:`FakePaymentProvider`.

Security rules baked into the interface:

- :meth:`verify_webhook` is the *only* path that turns a raw request body
  into a :class:`ProviderEvent` — it must verify the signature before any
  processing and raise :class:`SignatureVerificationError` on failure.
- Normalized events carry ids and money fields only; raw provider payloads
  with PII are never persisted.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional


class SignatureVerificationError(ValueError):
    """The webhook signature did not verify."""


class ProviderError(Exception):
    """A provider API call failed."""


@dataclass
class CheckoutSessionSpec:
    """Everything needed to open a hosted checkout session."""

    kind: str  # "purchase" | "membership"
    order_id: str
    checkout_token: str
    # Purchase fields: destination connected account + per-item pricing.
    destination_account_id: Optional[str] = None
    application_fee_minor: Optional[int] = None
    line_items: List[Dict[str, Any]] = field(default_factory=list)
    # Membership fields: an existing customer and a recurring price id.
    customer_id: Optional[str] = None
    customer_email: Optional[str] = None
    price_id: Optional[str] = None
    # Redirects. ``success_url``/``cancel_url`` must be absolute https URLs.
    success_url: str = ""
    cancel_url: str = ""
    metadata: Dict[str, str] = field(default_factory=dict)


@dataclass
class CheckoutSessionResult:
    session_id: str
    url: str
    expires_at: Optional[datetime] = None


@dataclass
class ProviderEvent:
    """
    A normalized webhook event.

    ``data`` holds a small, explicit subset of provider fields — ids, status,
    money amounts, invoice period bounds. It never contains the raw payload.
    """

    provider: str
    event_id: str
    type: str
    scope: str  # "platform" | "connect"
    data: Dict[str, Any] = field(default_factory=dict)
    livemode: bool = True


@dataclass
class ConnectedAccountState:
    account_id: str
    charges_enabled: bool
    payouts_enabled: bool
    details_submitted: bool


def connected_account_state(account_id: str, data: Dict[str, Any]) -> ConnectedAccountState:
    """
    Normalize a connected-account payload into capability flags.

    Handles both Stripe account object shapes: Accounts v1 (top-level
    ``charges_enabled``/``payouts_enabled``/``details_submitted`` booleans —
    the payload carried by ``account.updated`` webhook events) and Accounts
    v2 (``configuration`` capability statuses plus a ``requirements``
    summary — the live ``GET /v2/core/accounts/{id}`` response).
    """
    if data.get("object") != "v2.core.account":
        return ConnectedAccountState(
            account_id=account_id,
            charges_enabled=bool(data.get("charges_enabled")),
            payouts_enabled=bool(data.get("payouts_enabled")),
            details_submitted=bool(data.get("details_submitted")),
        )
    configuration = data.get("configuration") or {}
    merchant_capabilities = (configuration.get("merchant") or {}).get("capabilities") or {}
    recipient_capabilities = (configuration.get("recipient") or {}).get("capabilities") or {}
    card_payments = merchant_capabilities.get("card_payments") or {}
    # Payout capability is reported under whichever configuration exposes the
    # account's Stripe balance — merchant for charge-taking accounts.
    stripe_balance = merchant_capabilities.get("stripe_balance") or recipient_capabilities.get("stripe_balance") or {}
    payouts = stripe_balance.get("payouts") or {}
    requirements = data.get("requirements")
    # ``details_submitted`` requires positive evidence: when requirements were
    # not included in the payload we can't tell submitted from unfinished.
    deadline_status = (((requirements or {}).get("summary") or {}).get("minimum_deadline") or {}).get("status")
    return ConnectedAccountState(
        account_id=account_id,
        charges_enabled=card_payments.get("status") == "active",
        payouts_enabled=payouts.get("status") == "active",
        details_submitted=requirements is not None and deadline_status not in ("currently_due", "past_due"),
    )


@dataclass
class SubscriptionState:
    subscription_id: str
    customer_id: str
    status: str
    paid_through: Optional[datetime]
    cancel_at_period_end: bool


class PaymentProvider(abc.ABC):
    """Abstract payment provider."""

    name: str = "abstract"

    @abc.abstractmethod
    async def create_checkout_session(self, spec: CheckoutSessionSpec) -> CheckoutSessionResult:
        """Open a hosted checkout session for a purchase or membership."""

    @abc.abstractmethod
    async def retrieve_checkout_session(self, session_id: str, *, account_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Retrieve the authoritative state of a checkout session.

        ``account_id`` is required for purchase sessions — direct charges live
        on the seller's connected account and the platform cannot see them
        without the connected-account context.
        """

    @abc.abstractmethod
    async def expire_checkout_session(self, session_id: str) -> None:
        """Expire an open checkout session so it can no longer complete."""

    @abc.abstractmethod
    async def get_or_create_customer(self, *, email: str, user_id: str) -> str:
        """Return the provider customer id for a membership buyer."""

    @abc.abstractmethod
    async def ensure_membership_price(
        self, *, lookup_key: str, product_name: str, amount_minor: int, currency: str, interval: str
    ) -> str:
        """Return the recurring price id, provisioning it when missing/stale."""

    @abc.abstractmethod
    async def create_portal_session(self, *, customer_id: str, return_url: str) -> str:
        """Return a hosted customer-portal URL for managing a subscription."""

    @abc.abstractmethod
    async def retrieve_subscription(self, subscription_id: str) -> SubscriptionState:
        """Return the authoritative subscription state."""

    @abc.abstractmethod
    async def cancel_subscription(self, subscription_id: str, *, at_period_end: bool) -> SubscriptionState:
        """Cancel a subscription, at period end by default."""

    @abc.abstractmethod
    async def create_connected_account(self, *, user_id: str, email: str, country: str, display_name: str = "") -> str:
        """
        Create a connected (seller) account and return its provider id.

        ``country`` is the seller's ISO 3166-1 alpha-2 country of operation —
        providers require it upfront because it determines the legal entity
        type, payout rails, and which verification fields onboarding collects.
        """

    @abc.abstractmethod
    async def create_onboarding_link(self, *, account_id: str, refresh_url: str, return_url: str) -> str:
        """Return the hosted onboarding URL for a connected account."""

    @abc.abstractmethod
    async def retrieve_account(self, account_id: str) -> ConnectedAccountState:
        """Return the connected account's capability flags."""

    @abc.abstractmethod
    async def create_refund(
        self, *, payment_id: str, amount_minor: Optional[int] = None, account_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Refund a payment, fully when ``amount_minor`` is None."""

    @abc.abstractmethod
    def verify_webhook(self, payload: bytes, signature_header: str, *, scope: str) -> ProviderEvent:
        """
        Verify the webhook signature and normalize the event.

        :raises SignatureVerificationError: on signature failure.
        """
