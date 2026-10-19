"""
Tests for the payments Celery tasks (tasks/payments.py).

The task helpers take ``config`` and use ``get_session()`` directly — tests
call them on the test engine after ``init_db(engine=..., force=True)``.
"""

import asyncio
import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from songhive.config.schema import PaymentsConfig
from songhive.models._enums import Visibility
from songhive.models.album import Album
from songhive.models.artist import Artist
from songhive.models.base import init_db
from songhive.models.payments import (
    FulfillmentOutbox,
    InstanceSubscription,
    PaymentEvent,
    PaymentOrder,
    PurchaseArtifact,
    Sale,
    SampleDerivative,
)
from songhive.models.stored_file import StoredFile
from songhive.models.track import Track
from songhive.models.user import User
from songhive.storage import get_storage
from songhive.tasks import payments as payment_tasks


def _payments_config(config, **overrides):
    params = {"enabled": True}
    params.update(overrides)
    return config.model_copy(update={"payments": PaymentsConfig(**params)})


def _patch_config(monkeypatch, config):
    # ``tasks.payments`` imports ``load_config`` lazily inside the task bodies.
    monkeypatch.setattr("songhive.config.load_config", lambda *_: config)


async def _make_track(db_session, owner: User, title: str = "T") -> Track:
    artist = Artist(name="Artist")
    db_session.add(artist)
    await db_session.flush()
    track = Track(
        title=title,
        artist_id=artist.id,
        owner_id=owner.id,
        visibility=Visibility.PUBLIC.value,
    )
    db_session.add(track)
    await db_session.commit()
    return track


def _make_order(**overrides) -> PaymentOrder:
    params = {
        "kind": "purchase",
        "status": "pending",
        "currency": "usd",
        "total_minor": 500,
        "checkout_token": secrets.token_hex(16),
        "provider_name": "stripe",
    }
    params.update(overrides)
    return PaymentOrder(**params)


class TestMaterializeTrackSample:
    async def test_renders_and_reports_true(self, db_session, engine, config, monkeypatch, fake_redis):
        init_db(engine=engine, force=True)
        monkeypatch.setattr("songhive.services.redis.get_redis_client", lambda *_: fake_redis)
        monkeypatch.setattr("songhive.services.redis.close_redis_client", AsyncMock())
        monkeypatch.setattr("songhive.storage.get_storage", lambda *_: get_storage(config.storage))
        materialize = AsyncMock(return_value=2)
        monkeypatch.setattr("songhive.services.payments.samples.materialize_track_samples", materialize)

        result = await payment_tasks._materialize_track_sample("track-1", config)
        assert result is True
        materialize.assert_awaited_once()
        # The lock was released.
        assert await fake_redis.get("songhive:payments:sample-lock:track-1") is None

    async def test_skips_when_lock_held(self, db_session, engine, config, monkeypatch, fake_redis):
        from songhive.services.payments import samples

        init_db(engine=engine, force=True)
        monkeypatch.setattr("songhive.services.redis.get_redis_client", lambda *_: fake_redis)
        monkeypatch.setattr("songhive.services.redis.close_redis_client", AsyncMock())
        await samples.acquire_sample_lock(fake_redis, "track-1", 60)
        materialize = AsyncMock()
        monkeypatch.setattr("songhive.services.payments.samples.materialize_track_samples", materialize)

        result = await payment_tasks._materialize_track_sample("track-1", config)
        assert result is True
        materialize.assert_not_awaited()

    async def test_task_wrapper_swallows_errors(self, config, monkeypatch):
        _patch_config(monkeypatch, config)

        async def _boom(*a, **k):
            raise RuntimeError("db gone")

        monkeypatch.setattr(payment_tasks, "_materialize_track_sample", _boom)
        assert await asyncio.to_thread(payment_tasks.materialize_track_sample, "track-1") is False


class TestProcessPaymentEvent:
    async def _make_event(self, db_session, **overrides) -> PaymentEvent:
        params = {
            "provider": "stripe",
            "provider_event_id": secrets.token_hex(8),
            "scope": "platform",
            "type": "checkout.session.completed",
            "status": "pending",
            "data": {"session_id": "cs_1"},
        }
        params.update(overrides)
        row = PaymentEvent(**params)
        db_session.add(row)
        await db_session.commit()
        return row

    async def test_missing_event_returns_true(self, db_session, engine, config):
        init_db(engine=engine, force=True)
        result = await payment_tasks._process_payment_event("missing", config)
        assert result is True

    async def test_processed_event_is_noop(self, db_session, engine, config):
        init_db(engine=engine, force=True)
        event = await self._make_event(db_session, status="processed")
        result = await payment_tasks._process_payment_event(event.id, config)
        assert result is True

    async def test_dispatches_and_marks_processed(self, db_session, engine, config, monkeypatch):
        init_db(engine=engine, force=True)
        event = await self._make_event(db_session)
        dispatch = AsyncMock(return_value=True)
        monkeypatch.setattr("songhive.services.payments.webhooks.dispatch_event", dispatch)

        result = await payment_tasks._process_payment_event(event.id, config)
        assert result is True
        event_id = event.id
        db_session.expire_all()
        row = await db_session.get(PaymentEvent, event_id)
        assert row.status == "processed"
        assert row.attempts == 1
        assert row.processing_started_at is not None

    async def test_unhandled_event_marked_ignored(self, db_session, engine, config, monkeypatch):
        init_db(engine=engine, force=True)
        event = await self._make_event(db_session)
        monkeypatch.setattr(
            "songhive.services.payments.webhooks.dispatch_event",
            AsyncMock(return_value=False),
        )

        await payment_tasks._process_payment_event(event.id, config)
        event_id = event.id
        db_session.expire_all()
        assert (await db_session.get(PaymentEvent, event_id)).status == "ignored"

    async def test_handler_error_marks_for_retry(self, db_session, engine, config, monkeypatch):
        init_db(engine=engine, force=True)
        event = await self._make_event(db_session)
        monkeypatch.setattr(
            "songhive.services.payments.webhooks.dispatch_event",
            AsyncMock(side_effect=RuntimeError("handler exploded")),
        )

        with pytest.raises(RuntimeError):
            await payment_tasks._process_payment_event(event.id, config)
        event_id = event.id
        db_session.expire_all()
        row = await db_session.get(PaymentEvent, event_id)
        assert row.status == "pending"  # attempts < 6 → retryable
        assert "handler exploded" in row.last_error


class TestCancelProviderSubscription:
    async def test_cancels_and_marks_subscription(self, db_session, engine, config, monkeypatch, regular_user):
        init_db(engine=engine, force=True)
        sub = InstanceSubscription(
            user_id=regular_user.id,
            provider="stripe",
            provider_subscription_id="sub_1",
            status="active",
            paid_through=datetime.now(timezone.utc) + timedelta(days=10),
        )
        db_session.add(sub)
        await db_session.commit()

        provider = MagicMock()
        provider.cancel_subscription = AsyncMock()
        monkeypatch.setattr("songhive.services.payments.providers.get_provider", lambda *_: provider)

        result = await payment_tasks._cancel_provider_subscription("sub_1", config)
        assert result is True
        provider.cancel_subscription.assert_awaited_once_with("sub_1", at_period_end=False)
        sub_id = sub.id
        db_session.expire_all()
        row = await db_session.get(InstanceSubscription, sub_id)
        assert row.status == "canceled"
        assert row.paid_through is None

    async def test_provider_failure_returns_false_via_wrapper(self, config, monkeypatch):
        _patch_config(monkeypatch, config)

        async def _boom(*a, **k):
            raise RuntimeError("stripe unreachable")

        monkeypatch.setattr(payment_tasks, "_cancel_provider_subscription", _boom)
        assert await asyncio.to_thread(payment_tasks.cancel_provider_subscription, "sub_1") is False


class TestSweepMembershipExpirations:
    async def test_counts_changed_flags(self, db_session, engine, config, monkeypatch):
        init_db(engine=engine, force=True)
        for i in range(3):
            db_session.add(
                User(
                    username=f"m{i}",
                    email=f"m{i}@example.com",
                    password_hash="x",
                    is_active=True,
                    payments_required=True,
                )
            )
        db_session.add(
            User(
                username="free",
                email="free@example.com",
                password_hash="x",
                is_active=True,
                payments_required=False,
            )
        )
        await db_session.commit()

        async def _sync(session, user, config=None):
            if user.username == "m2":
                user.is_active = False

        monkeypatch.setattr("songhive.services.payments.membership.sync_user_active_flag", _sync)
        changed = await payment_tasks._sweep_membership_expirations(config)
        assert changed == 1


class TestExpirePendingOrders:
    async def test_expires_only_due_orders(self, db_session, engine, config, monkeypatch):
        init_db(engine=engine, force=True)
        now = datetime.now(timezone.utc)
        due = _make_order(provider_session_id="cs_due", expires_at=now - timedelta(hours=1))
        future = _make_order(provider_session_id="cs_future", expires_at=now + timedelta(hours=1))
        paid = _make_order(status="paid", expires_at=now - timedelta(hours=1))
        db_session.add_all([due, future, paid])
        await db_session.commit()

        provider = MagicMock()
        provider.expire_checkout_session = AsyncMock()
        monkeypatch.setattr("songhive.services.payments.providers.get_provider", lambda *_: provider)

        expired = await payment_tasks._expire_pending_orders(config)
        assert expired == 1
        provider.expire_checkout_session.assert_awaited_once_with("cs_due")
        due_id, future_id, paid_id = due.id, future.id, paid.id
        db_session.expire_all()
        assert (await db_session.get(PaymentOrder, due_id)).status == "expired"
        assert (await db_session.get(PaymentOrder, future_id)).status == "pending"
        assert (await db_session.get(PaymentOrder, paid_id)).status == "paid"

    async def test_provider_error_still_expires(self, db_session, engine, config, monkeypatch):
        init_db(engine=engine, force=True)
        order = _make_order(
            provider_session_id="cs_x",
            expires_at=datetime.now(timezone.utc) - timedelta(hours=1),
        )
        db_session.add(order)
        await db_session.commit()

        provider = MagicMock()
        provider.expire_checkout_session = AsyncMock(side_effect=RuntimeError("down"))
        monkeypatch.setattr("songhive.services.payments.providers.get_provider", lambda *_: provider)

        expired = await payment_tasks._expire_pending_orders(config)
        assert expired == 1
        order_id = order.id
        db_session.expire_all()
        assert (await db_session.get(PaymentOrder, order_id)).status == "expired"


class TestReconcilePendingOrders:
    async def test_disabled_returns_zero(self, db_session, engine, config):
        init_db(engine=engine, force=True)
        assert await payment_tasks._reconcile_pending_orders(config) == 0

    async def test_expired_provider_session_marks_order(self, db_session, engine, config, monkeypatch):
        init_db(engine=engine, force=True)
        cfg = _payments_config(config)
        order = _make_order(provider_session_id="cs_1")
        db_session.add(order)
        await db_session.commit()
        # created_at must predate the 2-minute reconcile grace window.
        from sqlalchemy import update

        await db_session.execute(
            update(PaymentOrder)
            .where(PaymentOrder.id == order.id)
            .values(created_at=datetime.now(timezone.utc) - timedelta(hours=1))
        )
        await db_session.commit()

        provider = MagicMock()
        provider.retrieve_checkout_session = AsyncMock(return_value={"status": "expired"})
        monkeypatch.setattr("songhive.services.payments.providers.get_provider", lambda *_: provider)

        reconciled = await payment_tasks._reconcile_pending_orders(cfg)
        assert reconciled == 1
        order_id = order.id
        db_session.expire_all()
        assert (await db_session.get(PaymentOrder, order_id)).status == "expired"

    async def test_complete_session_routes_to_webhook_handler(self, db_session, engine, config, monkeypatch):
        init_db(engine=engine, force=True)
        cfg = _payments_config(config)
        order = _make_order(provider_session_id="cs_1")
        db_session.add(order)
        await db_session.commit()
        from sqlalchemy import update

        await db_session.execute(
            update(PaymentOrder)
            .where(PaymentOrder.id == order.id)
            .values(created_at=datetime.now(timezone.utc) - timedelta(hours=1))
        )
        await db_session.commit()

        provider = MagicMock()
        provider.retrieve_checkout_session = AsyncMock(return_value={"status": "complete", "id": "cs_1"})
        monkeypatch.setattr("songhive.services.payments.providers.get_provider", lambda *_: provider)

        async def _fulfill(session, event, cfg):
            o = await session.get(PaymentOrder, order.id)
            o.status = "paid"

        monkeypatch.setattr("songhive.services.payments.webhooks.handle_checkout_completed", _fulfill)

        reconciled = await payment_tasks._reconcile_pending_orders(cfg)
        assert reconciled == 1
        order_id = order.id
        db_session.expire_all()
        assert (await db_session.get(PaymentOrder, order_id)).status == "paid"

    async def test_lookup_failure_isolated(self, db_session, engine, config, monkeypatch):
        """A provider error on one order does not stop the sweep."""
        init_db(engine=engine, force=True)
        cfg = _payments_config(config)
        order = _make_order(provider_session_id="cs_bad")
        db_session.add(order)
        await db_session.commit()
        from sqlalchemy import update

        await db_session.execute(
            update(PaymentOrder)
            .where(PaymentOrder.id == order.id)
            .values(created_at=datetime.now(timezone.utc) - timedelta(hours=1))
        )
        await db_session.commit()

        provider = MagicMock()
        provider.retrieve_checkout_session = AsyncMock(side_effect=RuntimeError("timeout"))
        monkeypatch.setattr("songhive.services.payments.providers.get_provider", lambda *_: provider)

        reconciled = await payment_tasks._reconcile_pending_orders(cfg)
        assert reconciled == 0
        order_id = order.id
        db_session.expire_all()
        assert (await db_session.get(PaymentOrder, order_id)).status == "pending"


class TestProcessFulfillmentOutbox:
    async def _make_row(self, db_session, order, **overrides) -> FulfillmentOutbox:
        params = {
            "order_id": order.id,
            "kind": "redeem_email",
            "status": "pending",
            "scheduled_at": datetime.now(timezone.utc) - timedelta(minutes=1),
        }
        params.update(overrides)
        row = FulfillmentOutbox(**params)
        db_session.add(row)
        await db_session.commit()
        return row

    async def test_disabled_returns_zero(self, db_session, engine, config):
        init_db(engine=engine, force=True)
        assert await payment_tasks._process_fulfillment_outbox(config) == 0

    async def test_unpaid_order_fails_row(self, db_session, engine, config):
        init_db(engine=engine, force=True)
        order = _make_order(status="pending")
        db_session.add(order)
        await db_session.commit()
        row = await self._make_row(db_session, order)

        delivered = await payment_tasks._process_fulfillment_outbox(_payments_config(config))
        assert delivered == 0
        row_id = row.id
        db_session.expire_all()
        row = await db_session.get(FulfillmentOutbox, row_id)
        assert row.status == "failed"
        assert row.last_error == "order not paid"

    async def test_sends_redeem_email(self, db_session, engine, config, monkeypatch, regular_user):
        init_db(engine=engine, force=True)
        order = _make_order(status="paid", buyer_user_id=regular_user.id)
        db_session.add(order)
        await db_session.commit()
        row = await self._make_row(db_session, order)

        from songhive.services.payments import fulfillment

        monkeypatch.setattr(fulfillment, "order_guest_email", AsyncMock(return_value="guest@example.com"))
        monkeypatch.setattr(fulfillment, "issue_redeem_capability", AsyncMock(return_value="tok"))
        monkeypatch.setattr(fulfillment, "redeem_url", lambda base, token: f"{base}/r/{token}")
        send = MagicMock(return_value=True)
        monkeypatch.setattr("songhive.services.email.send_purchase_redeem_email", send)

        cfg = _payments_config(config, public_base_url="https://music.example")
        delivered = await payment_tasks._process_fulfillment_outbox(cfg)
        assert delivered == 1
        send.assert_called_once()
        row_id = row.id
        db_session.expire_all()
        row = await db_session.get(FulfillmentOutbox, row_id)
        assert row.status == "sent"
        assert row.completed_at is not None

    async def test_missing_guest_email_fails_row(self, db_session, engine, config, monkeypatch, regular_user):
        init_db(engine=engine, force=True)
        order = _make_order(status="paid", buyer_user_id=regular_user.id)
        db_session.add(order)
        await db_session.commit()
        row = await self._make_row(db_session, order)

        from songhive.services.payments import fulfillment

        monkeypatch.setattr(fulfillment, "order_guest_email", AsyncMock(return_value=None))

        delivered = await payment_tasks._process_fulfillment_outbox(_payments_config(config))
        assert delivered == 0
        row_id = row.id
        db_session.expire_all()
        row = await db_session.get(FulfillmentOutbox, row_id)
        assert row.status == "failed"
        assert "guest email" in row.last_error

    async def test_send_failure_schedules_retry(self, db_session, engine, config, monkeypatch, regular_user):
        init_db(engine=engine, force=True)
        order = _make_order(status="paid", buyer_user_id=regular_user.id)
        db_session.add(order)
        await db_session.commit()
        row = await self._make_row(db_session, order)

        from songhive.services.payments import fulfillment

        monkeypatch.setattr(fulfillment, "order_guest_email", AsyncMock(return_value="guest@example.com"))
        monkeypatch.setattr(fulfillment, "issue_redeem_capability", AsyncMock(return_value="tok"))
        monkeypatch.setattr(fulfillment, "redeem_url", lambda base, token: f"{base}/r/{token}")
        monkeypatch.setattr("songhive.services.email.send_purchase_redeem_email", MagicMock(return_value=False))

        cfg = _payments_config(config, public_base_url="https://music.example")
        delivered = await payment_tasks._process_fulfillment_outbox(cfg)
        assert delivered == 0
        row_id = row.id
        db_session.expire_all()
        row = await db_session.get(FulfillmentOutbox, row_id)
        assert row.status == "pending"
        assert row.scheduled_at > datetime.now(timezone.utc)
        assert "SMTP" in row.last_error


class TestOrderTitle:
    async def test_track_title(self, db_session, regular_user):
        track = await _make_track(db_session, regular_user, "My Song")
        sale = Sale(
            owner_id=regular_user.id,
            entity_type="track",
            track_id=track.id,
            status="active",
            price_minor=500,
            currency="usd",
        )
        db_session.add(sale)
        await db_session.flush()
        order = _make_order(sale_id=sale.id)
        db_session.add(order)
        await db_session.commit()
        assert await payment_tasks._order_title(db_session, order) == '"My Song"'

    async def test_album_title(self, db_session, regular_user):
        artist = Artist(name="A")
        db_session.add(artist)
        await db_session.flush()
        album = Album(title="My Album", artist_id=artist.id, owner_id=regular_user.id)
        db_session.add(album)
        await db_session.flush()
        sale = Sale(
            owner_id=regular_user.id,
            entity_type="album",
            album_id=album.id,
            status="active",
            price_minor=500,
            currency="usd",
        )
        db_session.add(sale)
        await db_session.flush()
        order = _make_order(sale_id=sale.id)
        db_session.add(order)
        await db_session.commit()
        assert await payment_tasks._order_title(db_session, order) == '"My Album"'

    async def test_no_sale_fallback(self, db_session):
        order = _make_order()
        db_session.add(order)
        await db_session.commit()
        assert await payment_tasks._order_title(db_session, order) == "your purchase"


class TestCleanupPayments:
    async def test_removes_expired_artifacts_and_failed_samples(self, db_session, engine, config, regular_user):
        init_db(engine=engine, force=True)
        now = datetime.now(timezone.utc)

        order = _make_order(status="paid")
        db_session.add(order)
        track = await _make_track(db_session, regular_user)
        stored = StoredFile(
            storage_path="artifacts/ab/cd/deadbeef",
            storage_backend="local",
            content_type="application/zip",
            size=10,
            sha256=hashlib.sha256(b"zip").hexdigest(),
        )
        db_session.add(stored)
        await db_session.flush()

        artifact = PurchaseArtifact(
            order_id=order.id,
            snapshot_hash="s" * 64,
            stored_file_id=stored.id,
            expires_at=now - timedelta(hours=1),
        )
        kept_artifact = PurchaseArtifact(
            order_id=order.id,
            snapshot_hash="k" * 64,
            expires_at=now + timedelta(hours=1),
        )
        failed_sample = SampleDerivative(
            track_id=track.id,
            source_sha256="f" * 64,
            start_seconds=0,
            length_seconds=30,
            status="failed",
        )
        pending_sample = SampleDerivative(
            track_id=track.id,
            source_sha256="p" * 64,
            start_seconds=0,
            length_seconds=30,
            status="pending",
        )
        db_session.add_all([artifact, kept_artifact, failed_sample, pending_sample])
        await db_session.commit()
        artifact_id, kept_id, sample_id, pending_id = (
            artifact.id,
            kept_artifact.id,
            failed_sample.id,
            pending_sample.id,
        )
        stored_id = stored.id

        removed = await payment_tasks._cleanup_payments(config)
        assert removed == 2

        db_session.expire_all()
        assert await db_session.get(PurchaseArtifact, artifact_id) is None
        assert await db_session.get(StoredFile, stored_id) is None
        assert await db_session.get(PurchaseArtifact, kept_id) is not None
        assert await db_session.get(SampleDerivative, sample_id) is None
        assert await db_session.get(SampleDerivative, pending_id) is not None
