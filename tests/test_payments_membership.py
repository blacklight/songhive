"""Tests for the membership lifecycle (services/payments/membership.py)."""

from datetime import datetime, timedelta, timezone
from typing import Optional

import fakeredis.aioredis
import pytest

from songhive.config.schema import SonghiveConfig
from songhive.models.payments import InstanceSubscription
from songhive.services.payments import membership
from songhive.users.manager import register_user


@pytest.fixture
def redis():
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


def _subscription(user_id: str, paid_through: Optional[datetime]) -> InstanceSubscription:
    subscription = InstanceSubscription(user_id=user_id)
    subscription.status = "active" if paid_through else "none"
    subscription.paid_through = paid_through
    return subscription


class TestEffectiveAccountActive:
    def test_plain_user_is_active(self, regular_user):
        assert membership.effective_account_active(regular_user, None)

    def test_admin_suspended_never_active(self, regular_user):
        regular_user.admin_suspended = True
        assert not membership.effective_account_active(regular_user, None)

    def test_payments_required_without_subscription_inactive(self, regular_user):
        regular_user.payments_required = True
        assert not membership.effective_account_active(regular_user, None)

    def test_payments_required_with_future_paid_through_active(self, regular_user):
        regular_user.payments_required = True
        subscription = _subscription(regular_user.id, datetime.now(timezone.utc) + timedelta(days=10))
        assert membership.effective_account_active(regular_user, subscription)

    def test_paid_through_in_past_inactive(self, regular_user):
        regular_user.payments_required = True
        subscription = _subscription(regular_user.id, datetime.now(timezone.utc) - timedelta(days=1))
        assert not membership.effective_account_active(regular_user, subscription)

    def test_grace_period_extends_access(self, regular_user):
        regular_user.payments_required = True
        subscription = _subscription(regular_user.id, datetime.now(timezone.utc) - timedelta(hours=2))
        assert membership.effective_account_active(regular_user, subscription, grace_hours=24)

    def test_unverified_paid_user_stays_inactive(self, regular_user):
        regular_user.payments_required = True
        regular_user.email_verified = False
        subscription = _subscription(regular_user.id, datetime.now(timezone.utc) + timedelta(days=10))
        assert not membership.effective_account_active(regular_user, subscription)


class TestSyncUserActiveFlag:
    async def test_expired_membership_deactivates(self, db_session, regular_user, config):
        regular_user.payments_required = True
        regular_user.is_active = True
        await db_session.flush()

        subscription = await membership.get_or_create_subscription(db_session, regular_user.id)
        subscription.status = "active"
        # Beyond the default 72h grace window.
        subscription.paid_through = datetime.now(timezone.utc) - timedelta(days=10)
        await db_session.flush()

        result = await membership.sync_user_active_flag(db_session, regular_user, config=config)
        assert result is False
        assert regular_user.is_active is False

    async def test_payment_reactivates(self, db_session, regular_user, config):
        regular_user.payments_required = True
        regular_user.is_active = False
        await db_session.flush()

        subscription = await membership.get_or_create_subscription(db_session, regular_user.id)
        subscription.status = "active"
        subscription.paid_through = datetime.now(timezone.utc) + timedelta(days=30)
        await db_session.flush()

        result = await membership.sync_user_active_flag(db_session, regular_user, config=config)
        assert result is True
        assert regular_user.is_active is True

    async def test_suspended_user_stays_inactive_despite_payment(self, db_session, regular_user, config):
        regular_user.payments_required = True
        regular_user.admin_suspended = True
        regular_user.is_active = False
        subscription = await membership.get_or_create_subscription(db_session, regular_user.id)
        subscription.paid_through = datetime.now(timezone.utc) + timedelta(days=30)
        await db_session.flush()

        result = await membership.sync_user_active_flag(db_session, regular_user, config=config)
        assert result is False
        assert regular_user.is_active is False


class TestBillingCapability:
    async def test_round_trip(self, regular_user, config, redis):
        token = await membership.create_billing_capability(redis, regular_user, config)
        assert await membership.resolve_billing_capability(redis, token) == str(regular_user.id)

    async def test_unknown_token_resolves_none(self, redis):
        assert await membership.resolve_billing_capability(redis, "nope") is None

    async def test_revoke(self, regular_user, config, redis):
        token = await membership.create_billing_capability(redis, regular_user, config)
        await membership.revoke_billing_capability(redis, token)
        assert await membership.resolve_billing_capability(redis, token) is None


class TestPaidRegistration:
    async def test_paid_registration_creates_inactive_payments_required_user(self, db_session, tmp_path):
        config = SonghiveConfig(
            database={"url": f"sqlite+aiosqlite:///{tmp_path / 'x.db'}"},
            auth={
                "secret_key": "a" * 64,
                "registration_mode": "paid",
            },
            payments={"enabled": True},
        )
        user = await register_user(
            db_session,
            config,
            username="paidmember",
            email="paidmember@example.com",
            password="secret",
        )
        assert user.is_active is False
        assert user.payments_required is True

    async def test_open_registration_unaffected(self, db_session, tmp_path):
        config = SonghiveConfig(
            database={"url": f"sqlite+aiosqlite:///{tmp_path / 'y.db'}"},
            auth={"secret_key": "a" * 64, "registration_mode": "open"},
        )
        user = await register_user(
            db_session,
            config,
            username="openmember",
            email="openmember@example.com",
            password="secret",
        )
        assert user.is_active is True
        assert user.payments_required is False
