"""Tests for the reusable external-provider device-auth framework."""

from __future__ import annotations

import pytest

from songhive.external.device_auth import (
    DeviceAuthChallenge,
    DeviceAuthError,
    DeviceAuthPollResult,
    DeviceAuthProvider,
    begin_flow,
    complete_flow,
    device_auth_supported,
    get_device_auth_provider,
    pkce_paste_supported,
    poll_flow,
    register_device_auth_provider,
)


class FakeDeviceAuthProvider(DeviceAuthProvider):
    """Scripted device-auth provider for framework tests."""

    provider_type = "fakedev"
    supports_device_code = True
    supports_pkce = True

    def __init__(self) -> None:
        self.poll_outcomes: list[DeviceAuthPollResult] = []
        self.pkce_result = DeviceAuthPollResult(
            status="granted",
            config_fragment={"access_token": "pkce-at", "refresh_token": "pkce-rt"},
        )
        self.poll_calls = 0

    def begin_device(self, config: dict) -> DeviceAuthChallenge:
        return DeviceAuthChallenge(
            user_code="ABCD-EFGH",
            verification_uri="https://link.example.com",
            verification_uri_complete="https://link.example.com/ABCD-EFGH",
            expires_in=300,
            interval=2,
            provider_session={"device_code": "dc-123"},
        )

    def poll_device(self, config: dict, session: dict) -> DeviceAuthPollResult:
        self.poll_calls += 1
        if self.poll_outcomes:
            return self.poll_outcomes.pop(0)
        return DeviceAuthPollResult(status="pending")

    def begin_pkce(self, config: dict) -> tuple[str, dict]:
        return "https://auth.example.com/authorize?x=1", {"code_verifier": "v-123"}

    def complete_pkce(self, config: dict, session: dict, redirect_url: str) -> DeviceAuthPollResult:
        if session.get("code_verifier") != "v-123":
            raise DeviceAuthError("bad verifier")
        if "code=" not in redirect_url:
            raise DeviceAuthError("redirect URL has no code")
        return self.pkce_result


@pytest.fixture
def provider():
    provider = FakeDeviceAuthProvider()
    register_device_auth_provider(provider)
    return provider


@pytest.mark.asyncio
async def test_begin_stores_pending_and_returns_challenge(provider, fake_redis):
    """begin_flow returns the challenge and binds a pending entry to the user."""
    result = await begin_flow(fake_redis, provider, user_id="u1", config={"k": "v"}, mode="device")
    assert result.mode == "device"
    assert result.user_code == "ABCD-EFGH"
    assert result.verification_uri_complete.endswith("ABCD-EFGH")
    assert result.interval == 2
    assert device_auth_supported("fakedev")
    assert pkce_paste_supported("fakedev")
    assert get_device_auth_provider("fakedev") is provider


@pytest.mark.asyncio
async def test_poll_pending_then_granted(provider, fake_redis):
    """Poll transitions pending→granted and stashes the result for claim."""
    provider.poll_outcomes.append(DeviceAuthPollResult(status="pending"))
    provider.poll_outcomes.append(
        DeviceAuthPollResult(
            status="granted",
            config_fragment={"access_token": "at", "refresh_token": "rt"},
            display={"username": "tidaluser"},
        )
    )
    begin = await begin_flow(fake_redis, provider, user_id="u1", config={}, mode="device")

    r1 = await poll_flow(fake_redis, begin.state, "u1")
    assert r1.status == "pending"

    # Second poll is rate-limited by the provider interval.
    r2 = await poll_flow(fake_redis, begin.state, "u1")
    assert r2.status == "pending"
    assert r2.retry_after is not None
    assert provider.poll_calls == 1

    # Force the next poll window open by clearing pacing.
    pending = await fake_redis.get("songhive:external-device-auth:pending:" + begin.state)
    import json

    data = json.loads(pending)
    data["next_poll_at"] = 0
    await fake_redis.set("songhive:external-device-auth:pending:" + begin.state, json.dumps(data))

    r3 = await poll_flow(fake_redis, begin.state, "u1")
    assert r3.status == "granted"

    # Claim once.
    done = await complete_flow(fake_redis, begin.state, "u1")
    assert done.config["access_token"] == "at"
    assert done.display["username"] == "tidaluser"

    # Second claim fails.
    with pytest.raises(DeviceAuthError):
        await complete_flow(fake_redis, begin.state, "u1")


@pytest.mark.asyncio
async def test_poll_user_binding(provider, fake_redis):
    """A different user cannot poll or complete another user's flow."""
    begin = await begin_flow(fake_redis, provider, user_id="u1", config={}, mode="device")
    with pytest.raises(DeviceAuthError):
        await poll_flow(fake_redis, begin.state, "u2")
    with pytest.raises(DeviceAuthError):
        await complete_flow(fake_redis, begin.state, "u2")


@pytest.mark.asyncio
async def test_poll_slow_down_bumps_interval(provider, fake_redis):
    """slow_down bumps the poll interval instead of deleting the flow."""
    provider.poll_outcomes.append(DeviceAuthPollResult(status="slow_down"))
    begin = await begin_flow(fake_redis, provider, user_id="u1", config={}, mode="device")
    r = await poll_flow(fake_redis, begin.state, "u1")
    assert r.status == "slow_down"
    assert r.retry_after and r.retry_after >= 7  # 2s interval + 5s penalty


@pytest.mark.asyncio
async def test_poll_expired(provider, fake_redis):
    """Provider-reported expiry deletes the pending entry."""
    provider.poll_outcomes.append(DeviceAuthPollResult(status="expired"))
    begin = await begin_flow(fake_redis, provider, user_id="u1", config={}, mode="device")
    r = await poll_flow(fake_redis, begin.state, "u1")
    assert r.status == "expired"
    # Pending entry is gone; subsequent polls report expired.
    r2 = await poll_flow(fake_redis, begin.state, "u1")
    assert r2.status == "expired"


@pytest.mark.asyncio
async def test_pkce_flow(provider, fake_redis):
    """PKCE begin returns an authorize URL; complete exchanges the redirect."""
    begin = await begin_flow(fake_redis, provider, user_id="u1", config={}, mode="pkce")
    assert begin.mode == "pkce"
    assert begin.authorize_url.startswith("https://auth.example.com/")

    # The code verifier stays server-side — the client only sees the
    # authorize URL and the opaque state token.
    assert "v-123" not in repr(begin)
    import json

    pending = json.loads(await fake_redis.get("songhive:external-device-auth:pending:" + begin.state))
    assert pending["provider_session"]["code_verifier"] == "v-123"

    # Device-style polling doesn't apply to PKCE flows.
    r = await poll_flow(fake_redis, begin.state, "u1")
    assert r.status == "pending"

    with pytest.raises(DeviceAuthError):
        await complete_flow(fake_redis, begin.state, "u1")  # missing redirect_url

    done = await complete_flow(fake_redis, begin.state, "u1", redirect_url="tidal.example.com://login?code=abc")
    assert done.config["access_token"] == "pkce-at"

    # Verifier consumed — replay fails.
    with pytest.raises(DeviceAuthError):
        await complete_flow(fake_redis, begin.state, "u1", redirect_url="tidal.example.com://login?code=abc")
