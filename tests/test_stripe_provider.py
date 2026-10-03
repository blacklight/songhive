"""
Stripe provider unit tests.

The Connect surface must use Accounts v2 — Stripe rejects ``POST /v1/accounts``
for new integrations. These tests exercise the SDK call shape against a mocked
client so a drift back to ``client.v1.accounts`` fails loudly.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
import stripe

from songhive.config.schema import SonghiveConfig
from songhive.services.payments.providers.base import ProviderError, connected_account_state
from songhive.services.payments.providers.stripe_provider import StripeProvider


@pytest.fixture
def provider(tmp_path):
    config = SonghiveConfig(
        server={"host": "127.0.0.1", "port": 8000},
        database={"url": f"sqlite+aiosqlite:///{tmp_path / 'songhive.db'}"},
        auth={"secret_key": "a" * 32},
        storage={"local_path": str(tmp_path / "media"), "backend": "local"},
        payments={
            "enabled": True,
            "public_base_url": "http://localhost",
            "stripe_secret_key": "sk_test_dummy",
        },
    )
    instance = StripeProvider(config)
    instance._client = MagicMock()
    return instance


V2_ACCOUNT_PAYLOAD = {
    "id": "acct_v2_123",
    "object": "v2.core.account",
    "dashboard": "express",
    "configuration": {
        "merchant": {
            "capabilities": {
                "card_payments": {"status": "active", "status_details": []},
                "stripe_balance": {
                    "payouts": {"status": "pending", "status_details": []},
                },
            },
        },
        "recipient": {
            "capabilities": {
                "stripe_balance": {
                    "stripe_transfers": {"status": "active", "status_details": []},
                },
            },
        },
    },
    "requirements": {
        "entries": [],
        "summary": {"minimum_deadline": {"status": "currently_due", "time": "2026-10-01T00:00:00.000Z"}},
    },
}


class TestConnectedAccountState:
    """Shape-agnostic account payload normalization."""

    def test_v1_payload(self):
        state = connected_account_state(
            "acct_1",
            {
                "object": "account",
                "charges_enabled": True,
                "payouts_enabled": True,
                "details_submitted": True,
            },
        )
        assert state.charges_enabled is True
        assert state.payouts_enabled is True
        assert state.details_submitted is True

    def test_v1_webhook_payload_without_object_key(self):
        state = connected_account_state("acct_1", {"charges_enabled": True, "details_submitted": False})
        assert state.charges_enabled is True
        assert state.payouts_enabled is False
        assert state.details_submitted is False

    def test_v2_payload_pending(self):
        state = connected_account_state("acct_v2_123", V2_ACCOUNT_PAYLOAD)
        assert state.charges_enabled is True
        assert state.payouts_enabled is False
        assert state.details_submitted is False

    def test_v2_payload_fully_active(self):
        payload = {
            **V2_ACCOUNT_PAYLOAD,
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
        state = connected_account_state("acct_v2_123", payload)
        assert state.charges_enabled is True
        assert state.payouts_enabled is True
        assert state.details_submitted is True

    def test_v2_payload_without_requirements_not_submitted(self):
        payload = {**V2_ACCOUNT_PAYLOAD}
        del payload["requirements"]
        state = connected_account_state("acct_v2_123", payload)
        assert state.details_submitted is False


class TestCreateConnectedAccount:
    async def test_uses_accounts_v2(self, provider):
        provider._client.v2.core.accounts.create_async = AsyncMock(return_value=MagicMock(id="acct_v2_123"))
        account_id = await provider.create_connected_account(
            user_id="u1", email="seller@example.com", country="us", display_name="Seller"
        )
        assert account_id == "acct_v2_123"

        params = provider._client.v2.core.accounts.create_async.await_args.args[0]
        assert params["dashboard"] == "full"
        assert params["contact_email"] == "seller@example.com"
        assert params["display_name"] == "Seller"
        assert params["identity"]["country"] == "US"
        assert params["metadata"] == {"songhive_user_id": "u1"}
        # Full dashboard always pairs with Stripe-collected fees and
        # Stripe-carried losses — the only self-serve combination.
        assert params["defaults"]["responsibilities"] == {
            "fees_collector": "stripe",
            "losses_collector": "stripe",
        }
        merchant = params["configuration"]["merchant"]
        assert merchant["capabilities"]["card_payments"] == {"requested": True}
        recipient = params["configuration"]["recipient"]
        assert recipient["capabilities"]["stripe_balance"]["stripe_transfers"] == {"requested": True}
        provider._client.v1.accounts.create_async.assert_not_called()

    async def test_express_dashboard(self, provider):
        provider._config.payments.stripe_dashboard = "express"
        provider._client.v2.core.accounts.create_async = AsyncMock(return_value=MagicMock(id="acct_v2_123"))
        await provider.create_connected_account(user_id="u1", email="", country="DE")
        params = provider._client.v2.core.accounts.create_async.await_args.args[0]
        assert params["dashboard"] == "express"
        assert params["defaults"]["responsibilities"] == {
            "fees_collector": "application",
            "losses_collector": "stripe",
        }

    async def test_express_losses_collector_opt_in(self, provider):
        provider._config.payments.stripe_dashboard = "express"
        provider._config.payments.stripe_losses_collector = "application"
        provider._client.v2.core.accounts.create_async = AsyncMock(return_value=MagicMock(id="acct_v2_123"))
        await provider.create_connected_account(user_id="u1", email="", country="DE")
        params = provider._client.v2.core.accounts.create_async.await_args.args[0]
        assert params["defaults"]["responsibilities"]["losses_collector"] == "application"

    async def test_full_dashboard_ignores_losses_collector(self, provider):
        provider._config.payments.stripe_losses_collector = "application"
        provider._client.v2.core.accounts.create_async = AsyncMock(return_value=MagicMock(id="acct_v2_123"))
        await provider.create_connected_account(user_id="u1", email="", country="DE")
        params = provider._client.v2.core.accounts.create_async.await_args.args[0]
        assert params["dashboard"] == "full"
        assert params["defaults"]["responsibilities"]["losses_collector"] == "stripe"

    async def test_omits_empty_contact_email(self, provider):
        provider._client.v2.core.accounts.create_async = AsyncMock(return_value=MagicMock(id="acct_v2_123"))
        await provider.create_connected_account(user_id="u1", email="", country="DE")
        params = provider._client.v2.core.accounts.create_async.await_args.args[0]
        assert "contact_email" not in params
        assert "display_name" not in params

    async def test_stripe_error_becomes_provider_error(self, provider):
        provider._client.v2.core.accounts.create_async = AsyncMock(
            side_effect=stripe.InvalidRequestError("disabled", param=None)
        )
        with pytest.raises(ProviderError):
            await provider.create_connected_account(user_id="u1", email="seller@example.com", country="US")


class TestOnboardingLink:
    async def test_uses_v2_account_links(self, provider):
        provider._client.v2.core.account_links.create_async = AsyncMock(
            return_value=MagicMock(url="https://connect.stripe.test/link")
        )
        url = await provider.create_onboarding_link(
            account_id="acct_v2_123",
            refresh_url="https://app.test/settings?tab=billing",
            return_url="https://app.test/settings?tab=billing",
        )
        assert url == "https://connect.stripe.test/link"

        params = provider._client.v2.core.account_links.create_async.await_args.args[0]
        assert params["account"] == "acct_v2_123"
        use_case = params["use_case"]
        assert use_case["type"] == "account_onboarding"
        onboarding = use_case["account_onboarding"]
        assert onboarding["configurations"] == ["merchant", "recipient"]
        assert onboarding["refresh_url"] == "https://app.test/settings?tab=billing"
        assert onboarding["return_url"] == "https://app.test/settings?tab=billing"
        provider._client.v1.account_links.create_async.assert_not_called()

    async def test_stripe_error_becomes_provider_error(self, provider):
        provider._client.v2.core.account_links.create_async = AsyncMock(
            side_effect=stripe.InvalidRequestError("boom", param=None)
        )
        with pytest.raises(ProviderError):
            await provider.create_onboarding_link(
                account_id="acct_v2_123",
                refresh_url="https://app.test/r",
                return_url="https://app.test/r",
            )


class TestRetrieveAccount:
    async def test_maps_v2_capability_statuses(self, provider):
        account = MagicMock()
        account.id = "acct_v2_123"
        account.to_dict.return_value = V2_ACCOUNT_PAYLOAD
        provider._client.v2.core.accounts.retrieve_async = AsyncMock(return_value=account)

        state = await provider.retrieve_account("acct_v2_123")
        assert state.account_id == "acct_v2_123"
        assert state.charges_enabled is True
        assert state.payouts_enabled is False
        assert state.details_submitted is False

        params = provider._client.v2.core.accounts.retrieve_async.await_args.args[1]
        assert "configuration.merchant" in params["include"]
        assert "configuration.recipient" in params["include"]
        assert "requirements" in params["include"]
        provider._client.v1.accounts.retrieve_async.assert_not_called()

    async def test_stripe_error_becomes_provider_error(self, provider):
        provider._client.v2.core.accounts.retrieve_async = AsyncMock(
            side_effect=stripe.InvalidRequestError("gone", param=None)
        )
        with pytest.raises(ProviderError):
            await provider.retrieve_account("acct_v2_123")
