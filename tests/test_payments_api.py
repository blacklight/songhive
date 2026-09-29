"""
End-to-end payments API tests.

Covers sale management, checkout with the fake provider, webhook-driven
fulfillment, guest redemption, refund revocation, and the paid-membership
login path.
"""

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, update

from songhive.config.schema import SonghiveConfig
from songhive.models._enums import Visibility
from songhive.models.artist import Artist
from songhive.models.payments import (
    ConnectedAccount,
    InstanceSubscription,
    PaymentEvent,
    PaymentOrder,
    PurchaseEntitlement,
    Sale,
)
from songhive.models.stored_file import StoredFile
from songhive.models.track import Track
from songhive.services.payments import membership, sales, webhooks
from songhive.services.payments.providers import set_provider_override
from songhive.services.payments.providers.base import ProviderError, ProviderEvent
from songhive.services.payments.providers.fake import FakePaymentProvider


@pytest.fixture
def config(tmp_path):
    """A payments-enabled config backed by a fresh SQLite database."""
    return SonghiveConfig(
        server={
            "host": "127.0.0.1",
            "port": 8000,
            "debug": True,
            "cors_origins": ["http://localhost:8080"],
        },
        database={"url": f"sqlite+aiosqlite:///{tmp_path / 'songhive.db'}"},
        federation={"enabled": False},
        auth={"secret_key": "a" * 32},
        storage={
            "local_path": str(tmp_path / "media"),
            "backend": "local",
        },
        payments={
            "enabled": True,
            "public_base_url": "http://localhost",
            "supported_currencies": ["usd"],
        },
    )


@pytest.fixture
def provider():
    """Install the deterministic fake payment provider."""
    fake = FakePaymentProvider()
    set_provider_override(fake)
    yield fake
    set_provider_override(None)


async def _make_track(db_session, owner, title="Sellable Track") -> Track:
    artist = Artist(name="Seller Artist")
    db_session.add(artist)
    await db_session.flush()
    seed = secrets.token_bytes(16)
    sha = hashlib.sha256(seed).hexdigest()
    audio_file = StoredFile(
        storage_path=f"files/{sha[:2]}/{sha[2:4]}/{sha}",
        storage_backend="local",
        content_type="audio/mpeg",
        size=len(seed),
        sha256=sha,
        owner_id=owner.id,
        visibility=Visibility.PUBLIC.value,
    )
    db_session.add(audio_file)
    await db_session.flush()
    track = Track(
        title=title,
        artist_id=artist.id,
        owner_id=owner.id,
        visibility=Visibility.PUBLIC.value,
        audio_file_id=audio_file.id,
    )
    db_session.add(track)
    await db_session.flush()
    return track


async def _make_seller_account(db_session, seller) -> ConnectedAccount:
    account = ConnectedAccount(
        user_id=seller.id,
        provider="fake",
        provider_account_id=f"acct_{seller.id}",
        charges_enabled=True,
        payouts_enabled=True,
        details_submitted=True,
        onboarded_at=datetime.now(timezone.utc),
    )
    db_session.add(account)
    await db_session.flush()
    return account


async def _publish_sale(db_session, seller, track, *, price_minor=500, unpaid_policy="sample") -> Sale:
    sale = Sale(
        owner_id=seller.id,
        entity_type="track",
        track_id=track.id,
        status="active",
        price_minor=price_minor,
        currency="USD",
        unpaid_policy=unpaid_policy,
    )
    db_session.add(sale)
    await db_session.flush()
    return sale


async def _dispatch_stored_event(db_session, config, order_id_hint=None):
    """Dispatch the newest pending PaymentEvent row to its handler."""
    result = await db_session.execute(
        select(PaymentEvent).where(PaymentEvent.status == "pending").order_by(PaymentEvent.created_at.desc())
    )
    row = result.scalars().first()
    assert row is not None, "expected a persisted payment event"
    event = ProviderEvent(
        provider=row.provider,
        event_id=row.provider_event_id,
        type=row.type,
        scope=row.scope,
        data=dict(row.data),
    )
    handled = await webhooks.dispatch_event(db_session, event, config)
    row.status = "processed" if handled else "ignored"
    await db_session.flush()
    return row


def _completed_session_data(order) -> dict:
    """Build a JSON-safe ``checkout.session.completed`` payload for an order."""
    return {
        "id": order.provider_session_id,
        "metadata": {
            "songhive_order_id": str(order.id),
            "songhive_checkout_token": order.checkout_token,
        },
        "payment_status": "paid",
        "amount_total": order.total_minor,
        "currency": order.currency,
        "payment_intent": order.provider_payment_id or "pi_test",
    }


def _post_webhook(client, provider, event_type, data, scope="connect", event_id=None):
    payload = provider.build_event(event_type, data, event_id=event_id)
    signature = provider.signature_for(payload)
    path = "/api/v1/payments/webhooks/stripe-connect" if scope == "connect" else "/api/v1/payments/webhooks/stripe"
    return client.post(
        path,
        content=payload,
        headers={"Content-Type": "application/json", "X-Signature": signature},
    )


class TestPaymentsDisabled:
    async def test_sales_404_when_disabled(self, client, auth_headers, regular_user):
        # The default conftest config has payments disabled.
        response = client.get(
            "/api/v1/payments/sales/offer/track/abc",
            headers=auth_headers(regular_user),
        )
        assert response.status_code == 404


class TestSalesApi:
    async def test_create_draft_sale(self, client, db_session, auth_headers, regular_user, provider):
        track = await _make_track(db_session, regular_user)
        response = client.post(
            "/api/v1/payments/sales",
            json={
                "entity_type": "track",
                "entity_id": str(track.id),
                "price_minor": 500,
                "currency": "USD",
                "unpaid_policy": "sample",
                "publish": False,
            },
            headers=auth_headers(regular_user),
        )
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["status"] == "draft"
        assert body["price_minor"] == 500

    async def test_offer_returns_active_sale(self, client, db_session, auth_headers, regular_user, provider):
        track = await _make_track(db_session, regular_user)
        sale = await _publish_sale(db_session, regular_user, track)
        response = client.get(f"/api/v1/payments/sales/offer/track/{track.id}")
        assert response.status_code == 200, response.text
        assert response.json()["id"] == str(sale.id)

    async def test_offer_404_without_sale(self, client, db_session, regular_user, provider):
        track = await _make_track(db_session, regular_user)
        response = client.get(f"/api/v1/payments/sales/offer/track/{track.id}")
        assert response.status_code == 404

    async def test_cannot_sell_others_track(self, client, db_session, auth_headers, regular_user, other_user, provider):
        track = await _make_track(db_session, regular_user)
        response = client.post(
            "/api/v1/payments/sales",
            json={
                "entity_type": "track",
                "entity_id": str(track.id),
                "price_minor": 500,
                "currency": "USD",
                "unpaid_policy": "sample",
            },
            headers=auth_headers(other_user),
        )
        assert response.status_code in (403, 404)


class TestConnectApi:
    async def test_onboard_creates_account_and_returns_url(
        self, client, db_session, auth_headers, regular_user, provider
    ):
        response = client.post(
            "/api/v1/payments/connect/onboard",
            json={"return_path": "/settings?tab=billing", "refresh_path": "/settings?tab=billing", "country": "US"},
            headers=auth_headers(regular_user),
        )
        assert response.status_code == 200, response.text
        assert "/onboard/" in response.json()["onboarding_url"]

        account = await sales.get_connected_account(db_session, regular_user.id)
        assert account is not None
        assert account.provider_account_id

    async def test_onboard_requires_country_for_new_accounts(self, client, auth_headers, regular_user, provider):
        response = client.post(
            "/api/v1/payments/connect/onboard",
            json={},
            headers=auth_headers(regular_user),
        )
        assert response.status_code == 400
        assert "country" in response.json()["detail"]

    async def test_onboard_reuses_account_without_country(
        self, client, db_session, auth_headers, regular_user, provider
    ):
        await _make_seller_account(db_session, regular_user)
        response = client.post(
            "/api/v1/payments/connect/onboard",
            json={},
            headers=auth_headers(regular_user),
        )
        assert response.status_code == 200, response.text

    async def test_onboard_returns_502_on_provider_error(
        self, client, auth_headers, regular_user, provider, monkeypatch
    ):
        async def _boom(**kwargs):
            raise ProviderError("stripe says no")

        monkeypatch.setattr(provider, "create_connected_account", _boom)
        response = client.post(
            "/api/v1/payments/connect/onboard",
            json={"country": "US"},
            headers=auth_headers(regular_user),
        )
        assert response.status_code == 502

    async def test_status_returns_502_on_provider_error(
        self, client, db_session, auth_headers, regular_user, provider, monkeypatch
    ):
        await _make_seller_account(db_session, regular_user)

        async def _boom(account_id):
            raise ProviderError("stripe unreachable")

        monkeypatch.setattr(provider, "retrieve_account", _boom)
        response = client.get("/api/v1/payments/connect/status", headers=auth_headers(regular_user))
        assert response.status_code == 502

    async def test_account_updated_accepts_v2_payload(self, client, db_session, regular_user, provider, config):
        account = await _make_seller_account(db_session, regular_user)
        v2_payload = {
            "id": account.provider_account_id,
            "object": "v2.core.account",
            "configuration": {
                "merchant": {
                    "capabilities": {
                        "card_payments": {"status": "active"},
                        "stripe_balance": {"payouts": {"status": "active"}},
                    }
                },
            },
            "requirements": {"entries": [], "summary": {"minimum_deadline": {"status": "eventually_due"}}},
        }
        response = _post_webhook(client, provider, "account.updated", v2_payload)
        assert response.status_code == 200, response.text
        await _dispatch_stored_event(db_session, config)

        await db_session.refresh(account)
        assert account.charges_enabled is True
        assert account.payouts_enabled is True
        assert account.details_submitted is True
        assert account.onboarded_at is not None

    async def test_account_updated_accepts_v1_payload(self, client, db_session, regular_user, provider, config):
        account = await _make_seller_account(db_session, regular_user)
        account.details_submitted = False
        account.charges_enabled = False
        await db_session.flush()

        v1_payload = {
            "id": account.provider_account_id,
            "object": "account",
            "charges_enabled": True,
            "payouts_enabled": True,
            "details_submitted": True,
        }
        response = _post_webhook(client, provider, "account.updated", v1_payload)
        assert response.status_code == 200, response.text
        await _dispatch_stored_event(db_session, config)

        await db_session.refresh(account)
        assert account.charges_enabled is True
        assert account.payouts_enabled is True
        assert account.details_submitted is True


class TestCheckoutAndFulfillment:
    async def test_guest_checkout_fulfills_via_webhook(
        self, client, db_session, auth_headers, regular_user, provider, config
    ):
        track = await _make_track(db_session, regular_user)
        await _publish_sale(db_session, regular_user, track)
        await _make_seller_account(db_session, regular_user)

        response = client.post(
            "/api/v1/payments/checkout",
            json={
                "item_type": "track",
                "item_id": str(track.id),
                "guest_email": "buyer@example.com",
            },
        )
        assert response.status_code == 201, response.text
        order_id = response.json()["order_id"]

        order = await db_session.get(PaymentOrder, order_id)
        assert order.status == "pending"
        assert order.provider_session_id

        # Provider confirms the payment via webhook.
        order.provider_payment_id = "pi_test_1"
        response = _post_webhook(
            client,
            provider,
            "checkout.session.completed",
            _completed_session_data(order),
        )
        assert response.status_code == 200, response.text

        await _dispatch_stored_event(db_session, config)
        await db_session.refresh(order)
        assert order.status == "paid"

        entitlements = (
            (await db_session.execute(select(PurchaseEntitlement).where(PurchaseEntitlement.order_id == order.id)))
            .scalars()
            .all()
        )
        assert len(entitlements) == 1
        assert entitlements[0].status == "active"
        assert str(entitlements[0].track_id) == str(track.id)

    async def test_checkout_requires_guest_email(self, client, db_session, regular_user, provider):
        track = await _make_track(db_session, regular_user)
        await _publish_sale(db_session, regular_user, track)
        response = client.post(
            "/api/v1/payments/checkout",
            json={"item_type": "track", "item_id": str(track.id)},
        )
        assert response.status_code == 422

    async def test_webhook_rejects_bad_signature(self, client, db_session, regular_user, provider):
        payload = provider.build_event("checkout.session.completed", {})
        response = client.post(
            "/api/v1/payments/webhooks/stripe-connect",
            content=payload,
            headers={
                "Content-Type": "application/json",
                "X-Signature": "fake:invalid",
            },
        )
        assert response.status_code == 400

    async def test_amount_mismatch_flags_disputed(self, client, db_session, regular_user, provider, config):
        track = await _make_track(db_session, regular_user)
        sale = await _publish_sale(db_session, regular_user, track)
        await _make_seller_account(db_session, regular_user)

        response = client.post(
            "/api/v1/payments/checkout",
            json={
                "item_type": "track",
                "item_id": str(track.id),
                "guest_email": "buyer@example.com",
            },
        )
        order = await db_session.get(PaymentOrder, response.json()["order_id"])

        data = _completed_session_data(order)
        data["amount_total"] = sale.price_minor + 999
        _post_webhook(client, provider, "checkout.session.completed", data)
        await _dispatch_stored_event(db_session, config)
        await db_session.refresh(order)
        assert order.status == "disputed"


class TestRefundRevocation:
    async def test_refund_webhook_revokes_entitlements(self, client, db_session, regular_user, provider, config):
        track = await _make_track(db_session, regular_user)
        sale = await _publish_sale(db_session, regular_user, track)
        await _make_seller_account(db_session, regular_user)

        response = client.post(
            "/api/v1/payments/checkout",
            json={
                "item_type": "track",
                "item_id": str(track.id),
                "guest_email": "buyer@example.com",
            },
        )
        order = await db_session.get(PaymentOrder, response.json()["order_id"])
        data = _completed_session_data(order)
        data["payment_intent"] = "pi_refund_me"
        _post_webhook(client, provider, "checkout.session.completed", data)
        await _dispatch_stored_event(db_session, config)
        await db_session.refresh(order)
        assert order.status == "paid"

        # Refund through the provider webhook revokes access.
        _post_webhook(
            client,
            provider,
            "charge.refunded",
            {
                "payment_intent": "pi_refund_me",
                "amount": sale.price_minor,
                "amount_refunded": sale.price_minor,
                "refunded": True,
            },
        )
        await _dispatch_stored_event(db_session, config)
        await db_session.refresh(order)
        assert order.status == "refunded"

        entitlements = (
            (await db_session.execute(select(PurchaseEntitlement).where(PurchaseEntitlement.order_id == order.id)))
            .scalars()
            .all()
        )
        assert all(e.status == "revoked" for e in entitlements)


class TestRedeemFlow:
    async def test_redeem_capability_returns_download_token(self, client, db_session, regular_user, provider, config):
        from songhive.services.payments import fulfillment

        track = await _make_track(db_session, regular_user)
        await _publish_sale(db_session, regular_user, track, unpaid_policy="none")
        await _make_seller_account(db_session, regular_user)

        response = client.post(
            "/api/v1/payments/checkout",
            json={
                "item_type": "track",
                "item_id": str(track.id),
                "guest_email": "buyer@example.com",
            },
        )
        order = await db_session.get(PaymentOrder, response.json()["order_id"])
        _post_webhook(
            client,
            provider,
            "checkout.session.completed",
            _completed_session_data(order),
        )
        await _dispatch_stored_event(db_session, config)

        # Simulate the emailed redemption link.
        token = await fulfillment.issue_redeem_capability(db_session, order, config)
        await db_session.commit()

        response = client.post("/api/v1/payments/redeem", json={"token": token})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["order"]["id"] == str(order.id)
        assert body["download_token"]

        # The download token lists the purchased snapshot.
        response = client.get(f"/api/v1/payments/download/{body['download_token']}")
        assert response.status_code == 200, response.text
        tracks = response.json()["tracks"]
        assert [t["track_id"] for t in tracks] == [str(track.id)]

    async def test_redeem_rejects_unknown_token(self, client, provider):
        response = client.post("/api/v1/payments/redeem", json={"token": "does-not-exist"})
        assert response.status_code == 404


class TestMembershipApi:
    async def test_quote_returns_configured_price(self, client, provider):
        response = client.get("/api/v1/payments/membership/quote")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["price_minor"] == 500
        assert body["currency"].lower() == "usd"
        assert body["interval"] == "month"

    async def test_unpaid_user_login_returns_402_with_billing_token(self, client, db_session, make_user, provider):
        user = await make_user("unpaid", is_active=False, email_verified=True)
        user.payments_required = True
        await db_session.commit()

        response = client.post(
            "/api/v1/auth/login",
            json={"username": "unpaid", "password": "secret"},
        )
        assert response.status_code == 402, response.text
        body = response.json()
        assert body["billing_required"] is True
        billing_token = body["billing_token"]

        # The billing token authorizes membership status only.
        response = client.get(
            "/api/v1/payments/membership/status",
            params={"billing_token": billing_token},
        )
        assert response.status_code == 200, response.text
        assert response.json()["payments_required"] is True

        # It must not authenticate ordinary endpoints.
        response = client.get(
            "/api/v1/users/me",
            headers={"Authorization": f"Bearer {billing_token}"},
        )
        assert response.status_code == 401

    async def test_membership_status_via_order_token(self, client, db_session, make_user, provider):
        """The checkout_token in the success redirect authorizes status reads."""
        user = await make_user("buyer", is_active=False, email_verified=True)
        user.payments_required = True
        order = PaymentOrder(
            kind="membership",
            buyer_user_id=user.id,
            status="pending",
            currency="usd",
            total_minor=500,
            checkout_token="opaque-order-token",
            provider_name="fake",
        )
        db_session.add(order)
        await db_session.commit()

        response = client.get(
            "/api/v1/payments/membership/status",
            params={"order": "opaque-order-token"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["payments_required"] is True

        # A purchase order's token must not authorize membership status.
        purchase_order = PaymentOrder(
            kind="purchase",
            buyer_user_id=user.id,
            status="paid",
            currency="usd",
            total_minor=500,
            checkout_token="purchase-order-token",
            provider_name="fake",
        )
        db_session.add(purchase_order)
        await db_session.commit()
        response = client.get(
            "/api/v1/payments/membership/status",
            params={"order": "purchase-order-token"},
        )
        assert response.status_code == 401

    async def test_invoice_paid_before_checkout_completed_keeps_active(
        self, client, db_session, make_user, provider, config
    ):
        """Stripe delivers events in any order: invoice.paid may precede
        checkout.session.completed — the latter must not regress status."""
        user = await make_user("subscribed", is_active=False, email_verified=True)
        user.payments_required = True
        await db_session.commit()

        login = client.post(
            "/api/v1/auth/login",
            json={"username": "subscribed", "password": "secret"},
        )
        billing_token = login.json()["billing_token"]

        response = client.post(
            "/api/v1/payments/membership/checkout",
            params={"billing_token": billing_token},
        )
        assert response.status_code == 201, response.text

        result = await db_session.execute(select(PaymentOrder).where(PaymentOrder.buyer_user_id == user.id))
        order = result.scalar_one()
        subscription = await membership.get_subscription(db_session, user.id)
        assert subscription is not None
        customer_id = subscription.provider_customer_id

        # invoice.paid lands first — activates the subscription.
        period_end = int(datetime.now(timezone.utc).timestamp()) + 30 * 86400
        response = _post_webhook(
            client,
            provider,
            "invoice.paid",
            {
                "id": "in_test",
                "subscription": "sub_test",
                "customer": customer_id,
                "lines": {"data": [{"period": {"end": period_end}}]},
            },
            scope="platform",
        )
        assert response.status_code == 200, response.text
        await _dispatch_stored_event(db_session, config)
        await db_session.refresh(subscription)
        assert subscription.status == "active"

        # checkout.session.completed arrives second — must not downgrade.
        data = _completed_session_data(order)
        data["subscription"] = "sub_test"
        data["customer"] = customer_id
        response = _post_webhook(
            client,
            provider,
            "checkout.session.completed",
            data,
            scope="platform",
            event_id="evt_same",
        )
        assert response.status_code == 200, response.text
        await _dispatch_stored_event(db_session, config)
        await db_session.refresh(subscription)
        assert subscription.status == "active"
        await db_session.commit()

        # Redelivering the same event is idempotent, not a 500.
        response = _post_webhook(
            client,
            provider,
            "checkout.session.completed",
            data,
            scope="platform",
            event_id="evt_same",
        )
        assert response.status_code == 200, response.text
        assert response.json()["duplicate"] is True

    async def test_suspended_user_login_is_401(self, client, db_session, make_user, provider):
        user = await make_user("suspended", is_active=False, email_verified=True)
        user.payments_required = True
        user.admin_suspended = True
        await db_session.commit()

        response = client.post(
            "/api/v1/auth/login",
            json={"username": "suspended", "password": "secret"},
        )
        assert response.status_code == 401

    async def _membership_order(self, db_session, user, checkout_token, status="paid"):
        order = PaymentOrder(
            kind="membership",
            buyer_user_id=user.id,
            status=status,
            currency="usd",
            total_minor=500,
            checkout_token=checkout_token,
            provider_name="fake",
        )
        db_session.add(order)
        await db_session.commit()
        return order

    async def test_membership_session_via_order_token(self, client, db_session, make_user, provider):
        """A fresh paid membership order token mints a real session."""
        user = await make_user("minted", is_active=True, email_verified=True)
        await self._membership_order(db_session, user, "mint-order-token")

        response = client.post(
            "/api/v1/payments/membership/session",
            json={"order": "mint-order-token"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["access_token"]

        # The minted cookies authenticate ordinary endpoints.
        response = client.get("/api/v1/users/me")
        assert response.status_code == 200, response.text
        assert response.json()["username"] == "minted"

    async def test_membership_session_via_billing_token(self, client, db_session, make_user, provider):
        user = await make_user("capmint", is_active=False, email_verified=True)
        user.payments_required = True
        await db_session.commit()

        login = client.post(
            "/api/v1/auth/login",
            json={"username": "capmint", "password": "secret"},
        )
        assert login.status_code == 402
        billing_token = login.json()["billing_token"]

        # No session mint while the account is unpaid-inactive.
        response = client.post(
            "/api/v1/payments/membership/session",
            json={"billing_token": billing_token},
        )
        assert response.status_code == 403

        # Webhook-confirmed payment flips the user active.
        user.is_active = True
        await db_session.commit()

        response = client.post(
            "/api/v1/payments/membership/session",
            json={"billing_token": billing_token},
        )
        assert response.status_code == 200, response.text
        response = client.get("/api/v1/users/me")
        assert response.json()["username"] == "capmint"

    async def test_membership_session_single_use(self, client, db_session, make_user, provider):
        user = await make_user("replay", is_active=True, email_verified=True)
        await self._membership_order(db_session, user, "replay-order-token")

        response = client.post(
            "/api/v1/payments/membership/session",
            json={"order": "replay-order-token"},
        )
        assert response.status_code == 200, response.text

        # Replaying the same credential must not mint a second session.
        # Drop the minted cookies so the request exercises the scoped path.
        client.cookies.clear()
        response = client.post(
            "/api/v1/payments/membership/session",
            json={"order": "replay-order-token"},
        )
        assert response.status_code == 403

    async def test_membership_session_order_freshness(self, client, db_session, make_user, provider):
        """Order tokens only mint shortly after the order's last update."""
        user = await make_user("stale", is_active=True, email_verified=True)
        order = await self._membership_order(db_session, user, "stale-order-token")

        stale = datetime.now(timezone.utc) - timedelta(hours=2)
        await db_session.execute(update(PaymentOrder).where(PaymentOrder.id == order.id).values(updated_at=stale))
        await db_session.commit()

        response = client.post(
            "/api/v1/payments/membership/session",
            json={"order": "stale-order-token"},
        )
        assert response.status_code == 403

    async def test_membership_session_requires_credential(self, client, provider):
        response = client.post("/api/v1/payments/membership/session", json={})
        assert response.status_code == 401

    async def test_membership_session_rejects_suspended(self, client, db_session, make_user, provider):
        user = await make_user("susmint", is_active=True, email_verified=True)
        user.admin_suspended = True
        await self._membership_order(db_session, user, "suspended-order-token")

        response = client.post(
            "/api/v1/payments/membership/session",
            json={"order": "suspended-order-token"},
        )
        assert response.status_code == 403

    async def _active_subscription(self, db_session, user, provider):
        paid_through = datetime.now(timezone.utc) + timedelta(days=30)
        subscription = InstanceSubscription(
            user_id=user.id,
            provider_customer_id="cus_test",
            provider_subscription_id="sub_test",
            status="active",
            paid_through=paid_through,
        )
        db_session.add(subscription)
        await db_session.commit()
        provider.attach_subscription("sub_test", customer_id="cus_test", paid_through=paid_through)
        return subscription

    async def test_membership_portal_via_order_token(self, client, db_session, make_user, provider):
        """A fresh order token may open the billing portal for its order."""
        user = await make_user("portaluser", is_active=True, email_verified=True)
        await self._membership_order(db_session, user, "portal-order-token")
        await self._active_subscription(db_session, user, provider)

        response = client.post(
            "/api/v1/payments/membership/portal",
            params={"order": "portal-order-token"},
        )
        assert response.status_code == 200, response.text
        assert "cus_test" in response.json()["portal_url"]

        # A stale order token cannot act.
        await db_session.execute(
            update(PaymentOrder)
            .where(PaymentOrder.checkout_token == "portal-order-token")
            .values(updated_at=datetime.now(timezone.utc) - timedelta(hours=2))
        )
        await db_session.commit()
        response = client.post(
            "/api/v1/payments/membership/portal",
            params={"order": "portal-order-token"},
        )
        assert response.status_code == 403

    async def test_membership_cancel_via_order_token(self, client, db_session, make_user, provider):
        user = await make_user("canceluser", is_active=True, email_verified=True)
        await self._membership_order(db_session, user, "cancel-order-token")
        await self._active_subscription(db_session, user, provider)

        response = client.post(
            "/api/v1/payments/membership/cancel",
            params={"order": "cancel-order-token"},
            json={"immediate": False},
        )
        assert response.status_code == 200, response.text
        assert response.json()["cancel_at_period_end"] is True


class TestMediaEnforcement:
    """Payment gating on serialized responses and byte-delivery routes."""

    async def test_track_response_gates_unpaid_anonymous(self, client, db_session, regular_user, provider):
        track = await _make_track(db_session, regular_user)
        await _publish_sale(db_session, regular_user, track, unpaid_policy="none")
        await db_session.commit()

        response = client.get(f"/api/v1/tracks/{track.id}")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["paid"] is True
        assert body["unpaid_policy"] == "none"
        assert body["audio_url"] is None
        assert body["can_download"] is False

    async def test_track_response_full_stream_policy(self, client, db_session, regular_user, provider):
        track = await _make_track(db_session, regular_user)
        await _publish_sale(db_session, regular_user, track, unpaid_policy="full_stream")
        await db_session.commit()

        response = client.get(f"/api/v1/tracks/{track.id}")
        body = response.json()
        assert body["paid"] is True
        assert body["audio_url"] == f"/api/v1/stream/{track.id}"
        # Streaming rights do not include original downloads.
        assert body["can_download"] is False

    async def test_track_response_owner_full_access(self, client, db_session, regular_user, auth_headers, provider):
        track = await _make_track(db_session, regular_user)
        await _publish_sale(db_session, regular_user, track, unpaid_policy="none")
        await db_session.commit()

        response = client.get(f"/api/v1/tracks/{track.id}", headers=auth_headers(regular_user))
        body = response.json()
        assert body["paid"] is True
        assert body["audio_url"] is not None
        assert body["can_download"] is True

    async def test_track_download_denied_unpaid(self, client, db_session, regular_user, provider):
        track = await _make_track(db_session, regular_user)
        await _publish_sale(db_session, regular_user, track, unpaid_policy="full_stream")
        await db_session.commit()

        response = client.get(f"/api/v1/tracks/{track.id}/download")
        assert response.status_code == 403

    async def test_file_download_denied_unpaid(self, client, db_session, regular_user, provider):
        track = await _make_track(db_session, regular_user)
        await _publish_sale(db_session, regular_user, track, unpaid_policy="full_stream")
        await db_session.commit()

        response = client.get(f"/api/v1/files/{track.audio_file_id}/download")
        assert response.status_code == 403

    async def test_file_metadata_hides_url_unpaid(self, client, db_session, regular_user, provider):
        track = await _make_track(db_session, regular_user)
        await _publish_sale(db_session, regular_user, track, unpaid_policy="full_stream")
        await db_session.commit()

        response = client.get(f"/api/v1/files/{track.audio_file_id}")
        assert response.status_code == 200, response.text
        assert response.json()["url"] is None
