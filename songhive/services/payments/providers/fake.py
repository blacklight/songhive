"""
Fake payment provider for tests and development.

Deterministically produces checkout URLs, connected accounts, and
subscriptions without any network calls. ``verify_webhook`` accepts a raw
JSON document — tests build events with :meth:`build_event` and POST them to
the webhook endpoints, so the full fulfillment path is exercised end-to-end
without Stripe.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from .base import (
    CheckoutSessionResult,
    CheckoutSessionSpec,
    ConnectedAccountState,
    PaymentProvider,
    ProviderEvent,
    SignatureVerificationError,
    SubscriptionState,
)


class FakePaymentProvider(PaymentProvider):
    """In-memory provider double. Not for production."""

    name = "fake"

    def __init__(self, base_url: str = "https://payments.example.test"):
        self.base_url = base_url.rstrip("/")
        self.sessions: Dict[str, Dict[str, Any]] = {}
        self.customers: Dict[str, str] = {}  # user_id -> customer id
        self.subscriptions: Dict[str, Dict[str, Any]] = {}
        self.accounts: Dict[str, Dict[str, Any]] = {}
        self.prices: Dict[str, str] = {}  # lookup_key -> price id
        self.refunds: List[Dict[str, Any]] = []
        self._counter = 0

    def _next(self, prefix: str) -> str:
        self._counter += 1
        return f"{prefix}_fake_{self._counter}"

    async def create_checkout_session(self, spec: CheckoutSessionSpec) -> CheckoutSessionResult:
        session_id = self._next("cs")
        self.sessions[session_id] = {
            "id": session_id,
            "mode": "subscription" if spec.kind == "membership" else "payment",
            "status": "open",
            "payment_status": "unpaid",
            "metadata": dict(spec.metadata),
            "spec": spec,
            "payment_intent": self._next("pi"),
            "subscription": None,
        }
        expires = datetime.now(timezone.utc) + timedelta(hours=23)
        return CheckoutSessionResult(
            session_id=session_id,
            url=f"{self.base_url}/checkout/{session_id}",
            expires_at=expires,
        )

    async def retrieve_checkout_session(self, session_id: str, *, account_id: Optional[str] = None) -> Dict[str, Any]:
        return self.sessions.get(session_id, {})

    async def expire_checkout_session(self, session_id: str) -> None:
        session = self.sessions.get(session_id)
        if session is not None:
            session["status"] = "expired"

    async def get_or_create_customer(self, *, email: str, user_id: str) -> str:
        existing = self.customers.get(user_id)
        if existing:
            return existing
        customer_id = self._next("cus")
        self.customers[user_id] = customer_id
        return customer_id

    async def ensure_membership_price(
        self,
        *,
        lookup_key: str,
        product_name: str,
        amount_minor: int,
        currency: str,
        interval: str,
    ) -> str:
        wanted = f"price_{lookup_key}_{amount_minor}_{currency.lower()}_{interval}"
        existing = self.prices.get(lookup_key)
        if existing == wanted:
            return existing
        self.prices[lookup_key] = wanted
        return wanted

    async def create_portal_session(self, *, customer_id: str, return_url: str) -> str:
        return f"{self.base_url}/portal/{customer_id}"

    async def retrieve_subscription(self, subscription_id: str) -> SubscriptionState:
        raw = self.subscriptions.get(subscription_id)
        if raw is None:
            return SubscriptionState(
                subscription_id=subscription_id,
                customer_id="",
                status="canceled",
                paid_through=None,
                cancel_at_period_end=False,
            )
        return SubscriptionState(
            subscription_id=subscription_id,
            customer_id=raw.get("customer", ""),
            status=raw.get("status", "active"),
            paid_through=raw.get("paid_through"),
            cancel_at_period_end=raw.get("cancel_at_period_end", False),
        )

    async def cancel_subscription(self, subscription_id: str, *, at_period_end: bool) -> SubscriptionState:
        raw = self.subscriptions.get(subscription_id)
        if raw is None:
            return SubscriptionState(
                subscription_id=subscription_id,
                customer_id="",
                status="canceled",
                paid_through=None,
                cancel_at_period_end=False,
            )
        if at_period_end:
            raw["cancel_at_period_end"] = True
        else:
            raw["status"] = "canceled"
            raw["cancel_at_period_end"] = False
            raw["paid_through"] = None
        return await self.retrieve_subscription(subscription_id)

    async def create_connected_account(self, *, user_id: str, email: str, country: str, display_name: str = "") -> str:
        account_id = self._next("acct")
        self.accounts[account_id] = {
            "id": account_id,
            "user_id": user_id,
            "email": email,
            "country": country,
            "charges_enabled": False,
            "payouts_enabled": False,
            "details_submitted": False,
        }
        return account_id

    async def create_onboarding_link(self, *, account_id: str, refresh_url: str, return_url: str) -> str:
        return f"{self.base_url}/onboard/{account_id}"

    async def retrieve_account(self, account_id: str) -> ConnectedAccountState:
        raw = self.accounts.get(account_id, {})
        return ConnectedAccountState(
            account_id=account_id,
            charges_enabled=bool(raw.get("charges_enabled")),
            payouts_enabled=bool(raw.get("payouts_enabled")),
            details_submitted=bool(raw.get("details_submitted")),
        )

    async def create_refund(
        self,
        *,
        payment_id: str,
        amount_minor: Optional[int] = None,
        account_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        refund = {
            "id": self._next("re"),
            "payment_id": payment_id,
            "amount": amount_minor,
            "account_id": account_id,
        }
        self.refunds.append(refund)
        return refund

    def verify_webhook(self, payload: bytes, signature_header: str, *, scope: str) -> ProviderEvent:
        """
        "Verify" a fake webhook: the signature must be the sha256 of the
        payload prefixed with ``fake:`` — enough to exercise the rejection
        path without real asymmetric crypto.
        """
        expected = "fake:" + hashlib.sha256(payload).hexdigest()
        if signature_header != expected:
            raise SignatureVerificationError("bad fake signature")
        try:
            body = json.loads(payload.decode("utf-8"))
        except ValueError as exc:
            raise SignatureVerificationError("invalid payload") from exc
        return ProviderEvent(
            provider=self.name,
            event_id=str(body.get("id")),
            type=str(body.get("type")),
            scope=scope,
            data=body.get("data") or {},
            livemode=bool(body.get("livemode", False)),
        )

    # -- test helpers -------------------------------------------------------

    def build_event(
        self,
        event_type: str,
        data: Dict[str, Any],
        *,
        event_id: Optional[str] = None,
    ) -> bytes:
        """Serialize a fake webhook payload for posting to the webhook route."""
        body = {
            "id": event_id or self._next("evt"),
            "type": event_type,
            "data": data,
            "livemode": False,
        }
        return json.dumps(body).encode("utf-8")

    @staticmethod
    def signature_for(payload: bytes) -> str:
        """Return the signature header the fake verifier accepts."""
        return "fake:" + hashlib.sha256(payload).hexdigest()

    def complete_session(self, session_id: str, **updates: Any) -> Dict[str, Any]:
        """Mark a fake checkout session as paid (test helper)."""
        session = self.sessions[session_id]
        session["status"] = "complete"
        session["payment_status"] = "paid"
        session.update(updates)
        return session

    def mark_account_onboarded(self, account_id: str) -> None:
        """Flip a fake connected account to fully onboarded (test helper)."""
        self.accounts[account_id].update(charges_enabled=True, payouts_enabled=True, details_submitted=True)

    def attach_subscription(
        self,
        subscription_id: str,
        *,
        customer_id: str,
        status: str = "active",
        paid_through: Optional[datetime] = None,
        cancel_at_period_end: bool = False,
    ) -> None:
        """Register a subscription object in the fake backend (test helper)."""
        self.subscriptions[subscription_id] = {
            "customer": customer_id,
            "status": status,
            "paid_through": paid_through,
            "cancel_at_period_end": cancel_at_period_end,
        }
