"""
Stripe payment provider.

Checkout Sessions for artist purchases (direct charges on the artist's
connected account), Billing for instance memberships, Connect for seller
onboarding, and the hosted customer portal for self-service cancellation.

All calls use the async ``*_async`` SDK methods on a client built around an
explicit ``HTTPXClient``. Webhook verification uses Stripe's signature
scheme; payloads are normalized to :class:`ProviderEvent` and never
persisted.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional, cast

from ....config.schema import SonghiveConfig
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

logger = logging.getLogger(__name__)


def _ts(value: Any) -> Optional[datetime]:
    """Convert a Stripe unix timestamp to a tz-aware datetime."""
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None


class StripeProvider(PaymentProvider):
    """Stripe implementation of the payment provider interface."""

    name = "stripe"

    def __init__(self, config: SonghiveConfig):
        import stripe

        if not config.payments.stripe_secret_key:
            raise ValueError("payments.stripe_secret_key is not configured")
        self._config = config
        self._client = stripe.StripeClient(
            api_key=config.payments.stripe_secret_key,
            http_client=stripe.HTTPXClient(),
        )

    # -- checkout -----------------------------------------------------------

    async def create_checkout_session(self, spec: CheckoutSessionSpec) -> CheckoutSessionResult:
        metadata = dict(spec.metadata)
        metadata.setdefault("songhive_order_id", spec.order_id)
        metadata.setdefault("songhive_checkout_token", spec.checkout_token)
        options: Dict[str, Any] = {"idempotency_key": f"checkout-{spec.checkout_token}"}

        if spec.kind == "purchase":
            params: Dict[str, Any] = {
                "mode": "payment",
                "line_items": spec.line_items,
                "success_url": spec.success_url,
                "cancel_url": spec.cancel_url,
                "metadata": metadata,
                "payment_intent_data": {"metadata": metadata},
                "client_reference_id": spec.order_id,
            }
            if spec.customer_email:
                params["customer_email"] = spec.customer_email
            if spec.destination_account_id:
                payment_intent = params["payment_intent_data"]
                if spec.application_fee_minor is not None:
                    payment_intent["application_fee_amount"] = spec.application_fee_minor
                options["stripe_account"] = spec.destination_account_id
            else:
                # Never silently bill a buyer into the instance account for an
                # artist sale — a sale must name its destination account.
                raise ValueError("purchase sessions require destination_account_id")
        else:
            if not spec.price_id:
                raise ValueError("membership sessions require price_id")
            params = {
                "mode": "subscription",
                "line_items": [{"price": spec.price_id, "quantity": 1}],
                "success_url": spec.success_url,
                "cancel_url": spec.cancel_url,
                "metadata": metadata,
                "subscription_data": {"metadata": metadata},
                "client_reference_id": spec.order_id,
            }
            if spec.customer_id:
                params["customer"] = spec.customer_id
            elif spec.customer_email:
                params["customer_email"] = spec.customer_email

        session = await self._client.v1.checkout.sessions.create_async(cast(Any, params), options=cast(Any, options))
        return CheckoutSessionResult(
            session_id=session.id,
            url=session.url or "",
            expires_at=_ts(session.expires_at),
        )

    async def retrieve_checkout_session(self, session_id: str, *, account_id: Optional[str] = None) -> Dict[str, Any]:
        import stripe

        options: Dict[str, Any] = {}
        if account_id:
            options["stripe_account"] = account_id
        try:
            session = await self._client.v1.checkout.sessions.retrieve_async(
                session_id, options=cast(Any, options or None)
            )
        except stripe.StripeError as exc:
            raise ProviderError(f"stripe checkout session retrieval failed: {exc}") from exc
        return session.to_dict()

    async def expire_checkout_session(self, session_id: str) -> None:
        await self._client.v1.checkout.sessions.expire_async(session_id)

    # -- customers / prices --------------------------------------------------

    async def get_or_create_customer(self, *, email: str, user_id: str) -> str:
        existing = await self._client.v1.customers.search_async({"query": f'metadata["songhive_user_id"]:"{user_id}"'})
        for customer in existing.data:
            return customer.id
        customer = await self._client.v1.customers.create_async(
            {"email": email, "metadata": {"songhive_user_id": user_id}}
        )
        return customer.id

    async def ensure_membership_price(
        self,
        *,
        lookup_key: str,
        product_name: str,
        amount_minor: int,
        currency: str,
        interval: str,
    ) -> str:
        """Return the configured recurring price id, provisioning on drift."""
        found = await self._client.v1.prices.list_async({"lookup_keys": [lookup_key], "limit": 1})
        for price in found.data:
            recurring = price.recurring
            if (
                price.unit_amount == amount_minor
                and price.currency == currency.lower()
                and recurring is not None
                and recurring.interval == interval
            ):
                return price.id
            # Amount/interval drifted: provision a fresh price under the same
            # lookup key (older subscribers keep the old price on Stripe's side).
            break

        product = await self._client.v1.products.create_async(
            {"name": product_name},
            options={"idempotency_key": f"product-{lookup_key}"},
        )
        price = await self._client.v1.prices.create_async(
            {
                "product": product.id,
                "unit_amount": amount_minor,
                "currency": currency.lower(),
                "recurring": {"interval": interval},
                "lookup_key": lookup_key,
            },
            options={"idempotency_key": f"price-{lookup_key}-{amount_minor}-{currency}-{interval}"},
        )
        return price.id

    async def create_portal_session(self, *, customer_id: str, return_url: str) -> str:
        session = await self._client.v1.billing_portal.sessions.create_async(
            {"customer": customer_id, "return_url": return_url}
        )
        return session.url

    # -- subscriptions --------------------------------------------------------

    @staticmethod
    def _subscription_state(subscription: Any) -> SubscriptionState:
        data = subscription if isinstance(subscription, dict) else subscription.to_dict()
        items = (data.get("items") or {}).get("data") or []
        period_end = None
        for item in items:
            ts = _ts(item.get("current_period_end"))
            if ts is not None and (period_end is None or ts > period_end):
                period_end = ts
        return SubscriptionState(
            subscription_id=data.get("id", ""),
            customer_id=data.get("customer") or "",
            status=data.get("status") or "none",
            paid_through=period_end,
            cancel_at_period_end=bool(data.get("cancel_at_period_end")),
        )

    async def retrieve_subscription(self, subscription_id: str) -> SubscriptionState:
        subscription = await self._client.v1.subscriptions.retrieve_async(subscription_id)
        return self._subscription_state(subscription)

    async def cancel_subscription(self, subscription_id: str, *, at_period_end: bool) -> SubscriptionState:
        if at_period_end:
            subscription = await self._client.v1.subscriptions.update_async(
                subscription_id, {"cancel_at_period_end": True}
            )
        else:
            subscription = await self._client.v1.subscriptions.cancel_async(subscription_id)
        return self._subscription_state(subscription)

    # -- connect --------------------------------------------------------------

    async def create_connected_account(self, *, user_id: str, email: str, country: str, display_name: str = "") -> str:
        """
        Create a seller account via Accounts v2.

        Accounts v1 creation is disabled for new Connect integrations — Stripe
        rejects ``POST /v1/accounts``. ``merchant`` + ``card_payments`` enables
        the direct charges our checkout sessions perform on the connected
        account and ``recipient`` + ``stripe_transfers`` lets it receive
        transfers. ``identity.country`` is required when the merchant
        configuration is applied. ``defaults.responsibilities`` is immutable
        once set.

        ``payments.stripe_dashboard`` picks the hosted dashboard sellers get
        and pins the only ``responsibilities`` combination Stripe accepts for
        it without special approval:

        - ``"full"`` (default) — the standard Stripe dashboard with
          ``fees_collector="stripe"``/``losses_collector="stripe"``: Stripe
          deducts its fees from the connected account and carries
          negative-balance liability. This is the only combination available
          to self-serve platforms; platform-collected fees/losses with a full
          dashboard are sales-gated.
        - ``"express"`` — the co-branded Express dashboard with
          ``fees_collector="application"`` and ``losses_collector`` from
          ``payments.stripe_losses_collector``. Stripe rejects
          ``"application"`` with ``account_creation_losses_collector_unavailable``
          unless the platform is approved for managed risk, and rejects
          ``"stripe"`` with ``account_controller_unsupported_configuration``
          unless the platform is enrolled in the Express +
          Stripe-managed-liability preview (added in the 2026-06-24 dahlia
          release but still gated).
        """
        import stripe

        dashboard = self._config.payments.stripe_dashboard
        if dashboard == "express":
            responsibilities = {
                "fees_collector": "application",
                "losses_collector": self._config.payments.stripe_losses_collector,
            }
        else:
            responsibilities = {"fees_collector": "stripe", "losses_collector": "stripe"}

        params: Dict[str, Any] = {
            "dashboard": dashboard,
            "identity": {"country": country.upper()},
            "defaults": {"responsibilities": responsibilities},
            "configuration": {
                "merchant": {"capabilities": {"card_payments": {"requested": True}}},
                "recipient": {
                    "capabilities": {
                        "stripe_balance": {"stripe_transfers": {"requested": True}},
                    },
                },
            },
            "metadata": {"songhive_user_id": user_id},
        }
        if email:
            params["contact_email"] = email
        if display_name:
            params["display_name"] = display_name
        try:
            account = await self._client.v2.core.accounts.create_async(cast(Any, params))
        except stripe.StripeError as exc:
            raise ProviderError(f"stripe account creation failed: {exc}") from exc
        return account.id

    async def create_onboarding_link(self, *, account_id: str, refresh_url: str, return_url: str) -> str:
        import stripe

        try:
            link = await self._client.v2.core.account_links.create_async(
                cast(
                    Any,
                    {
                        "account": account_id,
                        "use_case": {
                            "type": "account_onboarding",
                            "account_onboarding": {
                                "configurations": ["merchant", "recipient"],
                                "refresh_url": refresh_url,
                                "return_url": return_url,
                            },
                        },
                    },
                )
            )
        except stripe.StripeError as exc:
            raise ProviderError(f"stripe onboarding link failed: {exc}") from exc
        return link.url

    async def retrieve_account(self, account_id: str) -> ConnectedAccountState:
        import stripe

        try:
            account = await self._client.v2.core.accounts.retrieve_async(
                account_id,
                cast(
                    Any,
                    {"include": ["configuration.merchant", "configuration.recipient", "requirements"]},
                ),
            )
        except stripe.StripeError as exc:
            raise ProviderError(f"stripe account retrieval failed: {exc}") from exc
        return connected_account_state(account.id, account.to_dict())

    # -- refunds ---------------------------------------------------------------

    async def create_refund(
        self,
        *,
        payment_id: str,
        amount_minor: Optional[int] = None,
        account_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {"payment_intent": payment_id}
        if amount_minor is not None:
            params["amount"] = amount_minor
        options: Dict[str, Any] = {}
        if account_id:
            options["stripe_account"] = account_id
        refund = await self._client.v1.refunds.create_async(cast(Any, params), options=cast(Any, options or None))
        return refund.to_dict()

    # -- webhooks ---------------------------------------------------------------

    def _webhook_secret(self, scope: str) -> Optional[str]:
        if scope == "connect":
            return self._config.payments.stripe_connect_webhook_secret
        return self._config.payments.stripe_platform_webhook_secret

    def verify_webhook(self, payload: bytes, signature_header: str, *, scope: str) -> ProviderEvent:
        """
        Verify the Stripe signature and normalize the event.

        The raw payload is only kept in memory long enough to extract the
        normalized fields — it is never persisted.
        """
        import stripe

        secret = self._webhook_secret(scope)
        if not secret:
            raise SignatureVerificationError(f"No webhook secret configured for scope {scope!r}")
        try:
            stripe.WebhookSignature.verify_header(payload, signature_header, secret)
            event = json.loads(payload.decode("utf-8"))
        except stripe.SignatureVerificationError as exc:
            raise SignatureVerificationError(str(exc)) from exc
        except ValueError as exc:
            raise SignatureVerificationError("invalid webhook payload") from exc

        return ProviderEvent(
            provider=self.name,
            event_id=str(event.get("id")),
            type=str(event.get("type")),
            scope=scope,
            data=(event.get("data") or {}).get("object") or {},
            livemode=bool(event.get("livemode")),
        )
