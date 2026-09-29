"""
Tests for the payments Celery sweeps: provider-confirmed order
reconciliation and the guest-fulfillment email outbox.

The reconcile sweep covers webhook loss (e.g. a connect endpoint that was
registered without "listen on connected accounts"): it polls the provider
for each pending order's checkout session and feeds paid sessions through
the same ``checkout.session.completed`` handler a webhook delivery would.
"""

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select

from songhive.config.schema import SonghiveConfig
from songhive.models._enums import Visibility
from songhive.models.artist import Artist
from songhive.models.payments import (
    ConnectedAccount,
    FulfillmentOutbox,
    PaymentOrder,
    PurchaseEntitlement,
    RedeemCapability,
    Sale,
)
from songhive.models.stored_file import StoredFile
from songhive.models.track import Track
from songhive.services.payments import fulfillment
from songhive.services.payments.providers import set_provider_override
from songhive.services.payments.providers.base import CheckoutSessionSpec
from songhive.services.payments.providers.fake import FakePaymentProvider
from songhive.tasks import payments as payments_tasks


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


async def _pending_purchase_order(db_session, config, provider, seller, *, guest_email="buyer@example.com"):
    """Seed a pending guest purchase order wired to a fake provider session."""
    track = await _make_track(db_session, seller)
    sale = Sale(
        owner_id=seller.id,
        entity_type="track",
        track_id=track.id,
        status="active",
        price_minor=500,
        currency="USD",
        unpaid_policy="sample",
    )
    db_session.add(sale)
    await db_session.flush()

    order = await fulfillment.create_purchase_order(db_session, config, sale, buyer=None, guest_email=guest_email)
    result = await provider.create_checkout_session(
        CheckoutSessionSpec(
            kind="purchase",
            order_id=str(order.id),
            checkout_token=order.checkout_token,
            destination_account_id=f"acct_{seller.id}",
            metadata={
                "songhive_order_id": str(order.id),
                "songhive_checkout_token": order.checkout_token,
            },
        )
    )
    order.provider_session_id = result.session_id
    order.provider_name = "fake"
    await db_session.commit()
    return order, track, result.session_id


def _backdate(order, minutes=10):
    order.created_at = datetime.now(timezone.utc) - timedelta(minutes=minutes)


class TestReconcilePendingOrders:
    async def test_paid_session_fulfills_guest_order(self, engine, db_session, regular_user, provider, config):
        """A paid provider session fulfills the order and queues the guest email."""
        from songhive.models.base import init_db

        init_db(engine=engine, force=True)

        await _make_seller_account(db_session, regular_user)
        order, track, session_id = await _pending_purchase_order(db_session, config, provider, regular_user)
        provider.complete_session(
            session_id,
            amount_total=order.total_minor,
            currency=order.currency,
            payment_intent="pi_reconciled",
        )
        _backdate(order)
        await db_session.commit()

        assert await payments_tasks._reconcile_pending_orders(config) == 1

        await db_session.refresh(order)
        assert order.status == "paid"
        assert order.provider_payment_id == "pi_reconciled"

        entitlements = (
            (await db_session.execute(select(PurchaseEntitlement).where(PurchaseEntitlement.order_id == order.id)))
            .scalars()
            .all()
        )
        assert [str(e.track_id) for e in entitlements] == [str(track.id)]

        outbox = (
            (await db_session.execute(select(FulfillmentOutbox).where(FulfillmentOutbox.order_id == order.id)))
            .scalars()
            .all()
        )
        assert len(outbox) == 1
        assert outbox[0].kind == "guest_email"
        assert outbox[0].status == "pending"

    async def test_fresh_order_within_grace_is_skipped(self, engine, db_session, regular_user, provider, config):
        """Webhook delivery wins the race: fresh orders are not polled."""
        from songhive.models.base import init_db

        init_db(engine=engine, force=True)

        await _make_seller_account(db_session, regular_user)
        order, _track, session_id = await _pending_purchase_order(db_session, config, provider, regular_user)
        provider.complete_session(
            session_id,
            amount_total=order.total_minor,
            currency=order.currency,
        )

        assert await payments_tasks._reconcile_pending_orders(config) == 0
        await db_session.refresh(order)
        assert order.status == "pending"

    async def test_provider_expired_session_expires_order(self, engine, db_session, regular_user, provider, config):
        from songhive.models.base import init_db

        init_db(engine=engine, force=True)

        await _make_seller_account(db_session, regular_user)
        order, _track, session_id = await _pending_purchase_order(db_session, config, provider, regular_user)
        provider.sessions[session_id]["status"] = "expired"
        _backdate(order)
        await db_session.commit()

        assert await payments_tasks._reconcile_pending_orders(config) == 1
        await db_session.refresh(order)
        assert order.status == "expired"

    async def test_open_session_left_pending(self, engine, db_session, regular_user, provider, config):
        from songhive.models.base import init_db

        init_db(engine=engine, force=True)

        await _make_seller_account(db_session, regular_user)
        order, _track, _session_id = await _pending_purchase_order(db_session, config, provider, regular_user)
        _backdate(order)
        await db_session.commit()

        assert await payments_tasks._reconcile_pending_orders(config) == 0
        await db_session.refresh(order)
        assert order.status == "pending"

    async def test_amount_mismatch_marks_disputed(self, engine, db_session, regular_user, provider, config):
        from songhive.models.base import init_db

        init_db(engine=engine, force=True)

        await _make_seller_account(db_session, regular_user)
        order, _track, session_id = await _pending_purchase_order(db_session, config, provider, regular_user)
        provider.complete_session(
            session_id,
            amount_total=order.total_minor + 1,
            currency=order.currency,
        )
        _backdate(order)
        await db_session.commit()

        await payments_tasks._reconcile_pending_orders(config)
        await db_session.refresh(order)
        assert order.status == "disputed"

    async def test_membership_reconcile_applies_subscription_state(
        self, engine, db_session, make_user, provider, config
    ):
        """A lost invoice.paid still activates the member via the provider poll."""
        from songhive.models.base import init_db
        from songhive.models.payments import InstanceSubscription

        init_db(engine=engine, force=True)

        user = await make_user("member", is_active=False, email_verified=True)
        user.payments_required = True
        order = PaymentOrder(
            kind="membership",
            buyer_user_id=user.id,
            status="pending",
            currency="usd",
            total_minor=500,
            checkout_token="membership-token",
            provider_name="fake",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
        )
        db_session.add(order)
        await db_session.flush()

        result = await provider.create_checkout_session(
            CheckoutSessionSpec(
                kind="membership",
                order_id=str(order.id),
                checkout_token=order.checkout_token,
                price_id="price_fake",
            )
        )
        order.provider_session_id = result.session_id
        provider.complete_session(
            result.session_id,
            payment_status="paid",
            subscription="sub_reconciled",
            customer="cus_reconciled",
        )
        provider.attach_subscription(
            "sub_reconciled",
            customer_id="cus_reconciled",
            status="active",
            paid_through=datetime.now(timezone.utc) + timedelta(days=30),
        )
        _backdate(order)
        await db_session.commit()

        assert await payments_tasks._reconcile_pending_orders(config) == 1

        await db_session.refresh(order)
        assert order.status == "paid"

        row = (
            await db_session.execute(select(InstanceSubscription).where(InstanceSubscription.user_id == user.id))
        ).scalar_one()
        assert row.provider_subscription_id == "sub_reconciled"
        assert row.paid_through is not None
        await db_session.refresh(user)
        assert bool(user.is_active)


class TestFulfillmentOutbox:
    async def test_guest_redeem_email_sent(self, engine, db_session, regular_user, provider, config, monkeypatch):
        """A paid guest order's outbox row sends the redeem email once."""
        from songhive.models.base import init_db

        init_db(engine=engine, force=True)

        await _make_seller_account(db_session, regular_user)
        order, _track, _session_id = await _pending_purchase_order(db_session, config, provider, regular_user)
        await fulfillment.mark_order_paid(db_session, order, provider_payment_id="pi_x")
        await db_session.commit()

        sent = MagicMock(return_value=True)
        monkeypatch.setattr("songhive.services.email.send_purchase_redeem_email", sent)

        assert await payments_tasks._process_fulfillment_outbox(config) == 1
        assert sent.call_count == 1
        _, to_address, redeem_link, title, _days = sent.call_args.args
        assert to_address == "buyer@example.com"
        assert "/redeem/" in redeem_link

        row = (await db_session.execute(select(FulfillmentOutbox))).scalar_one()
        assert row.status == "sent"

        capabilities = (await db_session.execute(select(RedeemCapability))).scalars().all()
        assert len(capabilities) == 1

    async def test_smtp_failure_retries_instead_of_marking_sent(
        self, engine, db_session, regular_user, provider, config, monkeypatch
    ):
        """send_purchase_redeem_email returning False must not mark the row sent."""
        from songhive.models.base import init_db

        init_db(engine=engine, force=True)

        await _make_seller_account(db_session, regular_user)
        order, _track, _session_id = await _pending_purchase_order(db_session, config, provider, regular_user)
        await fulfillment.mark_order_paid(db_session, order, provider_payment_id="pi_x")
        await db_session.commit()

        sent = MagicMock(return_value=False)
        monkeypatch.setattr("songhive.services.email.send_purchase_redeem_email", sent)

        assert await payments_tasks._process_fulfillment_outbox(config) == 0

        row = (await db_session.execute(select(FulfillmentOutbox))).scalar_one()
        assert row.status == "pending"
        assert row.attempts == 1
        assert row.last_error
        assert row.scheduled_at > datetime.now(timezone.utc)

    async def test_unpaid_order_outbox_row_fails(self, engine, db_session, regular_user, provider, config):
        from songhive.models.base import init_db

        init_db(engine=engine, force=True)

        await _make_seller_account(db_session, regular_user)
        order, _track, _session_id = await _pending_purchase_order(db_session, config, provider, regular_user)
        db_session.add(FulfillmentOutbox(order_id=order.id, kind="guest_email"))
        await db_session.commit()

        assert await payments_tasks._process_fulfillment_outbox(config) == 0
        row = (await db_session.execute(select(FulfillmentOutbox))).scalar_one()
        assert row.status == "failed"
        assert row.last_error == "order not paid"
