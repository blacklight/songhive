"""
Two-factor authentication tests: TOTP enrollment, WebAuthn security keys,
recovery codes, pending-login flow, and admin clearing.
"""

import hashlib
import json

import pytest
from fastapi import status
from sqlalchemy import select

from songhive.models.two_factor import RecoveryCode, WebAuthnCredential
from songhive.services.auth import create_user
from songhive.users import two_factor

SECRET_KEY = "a" * 32


def _totp_code(secret: str) -> str:
    import pyotp

    return pyotp.TOTP(secret).now()


async def _enable_totp(client, headers) -> dict:
    """Run a full TOTP enrollment for the authenticated user."""
    setup = client.post("/api/v1/auth/2fa/totp/setup", headers=headers)
    assert setup.status_code == status.HTTP_200_OK
    secret = setup.json()["secret"]
    confirm = client.post(
        "/api/v1/auth/2fa/totp/confirm",
        headers=headers,
        json={"code": _totp_code(secret)},
    )
    assert confirm.status_code == status.HTTP_200_OK
    return {"secret": secret, "recovery_codes": confirm.json()["recovery_codes"]}


def _login(client, username="alice", password="secret"):
    return client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
    )


@pytest.mark.asyncio
async def test_totp_setup_returns_secret_and_qr(client, regular_user, auth_headers):
    """TOTP setup returns the secret, otpauth URL, and a QR data URI."""
    response = client.post("/api/v1/auth/2fa/totp/setup", headers=auth_headers(regular_user))
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["secret"]
    assert data["otpauth_url"].startswith("otpauth://totp/")
    assert data["qr_code"].startswith("data:image/png;base64,")


@pytest.mark.asyncio
async def test_totp_secret_not_active_before_confirm(client, regular_user, auth_headers, db_session):
    """A pending TOTP secret does not enable 2FA until a code is confirmed."""
    response = client.post("/api/v1/auth/2fa/totp/setup", headers=auth_headers(regular_user))
    assert response.status_code == status.HTTP_200_OK
    await db_session.refresh(regular_user)
    assert regular_user.totp_secret is None
    assert not await two_factor.user_has_2fa(db_session, regular_user)

    # Login still issues tokens directly while enrollment is pending.
    login = _login(client, username="regular")
    assert login.status_code == status.HTTP_200_OK
    assert "access_token" in login.json()


@pytest.mark.asyncio
async def test_totp_confirm_rejects_wrong_code(client, regular_user, auth_headers, db_session):
    """Confirming with a wrong code fails and does not persist the secret."""
    setup = client.post("/api/v1/auth/2fa/totp/setup", headers=auth_headers(regular_user))
    assert setup.status_code == status.HTTP_200_OK

    response = client.post(
        "/api/v1/auth/2fa/totp/confirm",
        headers=auth_headers(regular_user),
        json={"code": "000000"},
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    await db_session.refresh(regular_user)
    assert regular_user.totp_secret is None


@pytest.mark.asyncio
async def test_totp_confirm_without_setup_fails(client, regular_user, auth_headers):
    """Confirming without a pending setup returns 400."""
    response = client.post(
        "/api/v1/auth/2fa/totp/confirm",
        headers=auth_headers(regular_user),
        json={"code": "123456"},
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.asyncio
async def test_totp_setup_twice_conflicts(client, regular_user, auth_headers):
    """Re-running setup after enrollment is rejected."""
    await _enable_totp(client, auth_headers(regular_user))
    response = client.post("/api/v1/auth/2fa/totp/setup", headers=auth_headers(regular_user))
    assert response.status_code == status.HTTP_409_CONFLICT


@pytest.mark.asyncio
async def test_login_requires_second_factor(client, regular_user, auth_headers):
    """With 2FA enabled, password login yields an mfa challenge, not tokens."""
    enrollment = await _enable_totp(client, auth_headers(regular_user))

    response = _login(client, username="regular")
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["mfa_required"] is True
    assert data["mfa_token"]
    assert "totp" in data["methods"]
    assert "access_token" not in data

    # Complete the challenge with a current TOTP code.
    verify = client.post(
        "/api/v1/auth/2fa/login",
        json={"mfa_token": data["mfa_token"], "code": _totp_code(enrollment["secret"])},
    )
    assert verify.status_code == status.HTTP_200_OK
    tokens = verify.json()
    assert tokens["access_token"]
    assert tokens["refresh_token"]
    assert tokens["token_type"] == "bearer"


@pytest.mark.asyncio
async def test_login_mfa_rejects_wrong_code(client, regular_user, auth_headers):
    """A wrong second-factor code fails the pending login."""
    await _enable_totp(client, auth_headers(regular_user))
    challenge = _login(client, username="regular").json()

    response = client.post(
        "/api/v1/auth/2fa/login",
        json={"mfa_token": challenge["mfa_token"], "code": "000000"},
    )
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
async def test_login_mfa_token_single_use(client, regular_user, auth_headers):
    """The mfa token cannot be replayed after a successful verification."""
    enrollment = await _enable_totp(client, auth_headers(regular_user))
    challenge = _login(client, username="regular").json()

    verify = client.post(
        "/api/v1/auth/2fa/login",
        json={"mfa_token": challenge["mfa_token"], "code": _totp_code(enrollment["secret"])},
    )
    assert verify.status_code == status.HTTP_200_OK

    replay = client.post(
        "/api/v1/auth/2fa/login",
        json={"mfa_token": challenge["mfa_token"], "code": _totp_code(enrollment["secret"])},
    )
    assert replay.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
async def test_login_mfa_attempt_budget(client, regular_user, auth_headers):
    """Pending logins are dropped after too many failed attempts."""
    await _enable_totp(client, auth_headers(regular_user))
    challenge = _login(client, username="regular").json()

    for _ in range(two_factor.MAX_PENDING_ATTEMPTS):
        client.post(
            "/api/v1/auth/2fa/login",
            json={"mfa_token": challenge["mfa_token"], "code": "000000"},
        )

    # The pending login is now gone — even a correct code fails.
    import pyotp

    # Compute the right code with the stored (decrypted) secret.
    secret = two_factor._decrypt_totp_secret(regular_user)
    response = client.post(
        "/api/v1/auth/2fa/login",
        json={"mfa_token": challenge["mfa_token"], "code": pyotp.TOTP(secret).now()},
    )
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
async def test_login_mfa_invalid_token(client):
    """An unknown mfa token is rejected."""
    response = client.post(
        "/api/v1/auth/2fa/login",
        json={"mfa_token": "bogus", "code": "123456"},
    )
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
async def test_recovery_code_login(client, regular_user, auth_headers):
    """A recovery code completes the second factor and is single-use."""
    enrollment = await _enable_totp(client, auth_headers(regular_user))
    code = enrollment["recovery_codes"][0]

    challenge = _login(client, username="regular").json()
    response = client.post(
        "/api/v1/auth/2fa/login",
        json={"mfa_token": challenge["mfa_token"], "code": code},
    )
    assert response.status_code == status.HTTP_200_OK
    assert response.json()["access_token"]

    # The same code cannot be used twice.
    challenge = _login(client, username="regular").json()
    response = client.post(
        "/api/v1/auth/2fa/login",
        json={"mfa_token": challenge["mfa_token"], "code": code},
    )
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
async def test_recovery_codes_regenerate(client, regular_user, auth_headers):
    """Regenerating recovery codes invalidates the previous batch."""
    enrollment = await _enable_totp(client, auth_headers(regular_user))
    headers = auth_headers(regular_user)

    response = client.post(
        "/api/v1/auth/2fa/recovery-codes",
        headers=headers,
        json={"password": "secret"},
    )
    assert response.status_code == status.HTTP_200_OK
    new_codes = response.json()["recovery_codes"]
    assert len(new_codes) == two_factor.RECOVERY_CODE_COUNT
    assert set(new_codes) != set(enrollment["recovery_codes"])

    # Old codes no longer work.
    challenge = _login(client, username="regular").json()
    old = client.post(
        "/api/v1/auth/2fa/login",
        json={"mfa_token": challenge["mfa_token"], "code": enrollment["recovery_codes"][0]},
    )
    assert old.status_code == status.HTTP_401_UNAUTHORIZED

    challenge = _login(client, username="regular").json()
    new = client.post(
        "/api/v1/auth/2fa/login",
        json={"mfa_token": challenge["mfa_token"], "code": new_codes[0]},
    )
    assert new.status_code == status.HTTP_200_OK


@pytest.mark.asyncio
async def test_recovery_codes_regenerate_requires_password(client, regular_user, auth_headers):
    """Recovery code regeneration re-verifies the password."""
    await _enable_totp(client, auth_headers(regular_user))
    response = client.post(
        "/api/v1/auth/2fa/recovery-codes",
        headers=auth_headers(regular_user),
        json={"password": "wrong"},
    )
    assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.asyncio
async def test_recovery_codes_regenerate_requires_2fa(client, regular_user, auth_headers):
    """Recovery codes cannot be regenerated when 2FA is not enabled."""
    response = client.post(
        "/api/v1/auth/2fa/recovery-codes",
        headers=auth_headers(regular_user),
        json={"password": "secret"},
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.asyncio
async def test_totp_disable(client, regular_user, auth_headers, db_session):
    """Disabling TOTP removes the second-factor requirement at login."""
    await _enable_totp(client, auth_headers(regular_user))
    headers = auth_headers(regular_user)

    response = client.post(
        "/api/v1/auth/2fa/totp/disable",
        headers=headers,
        json={"password": "secret"},
    )
    assert response.status_code == status.HTTP_200_OK
    await db_session.refresh(regular_user)
    assert regular_user.totp_secret is None

    # Recovery codes are gone too — no factor remains.
    remaining = await db_session.execute(select(RecoveryCode).where(RecoveryCode.user_id == regular_user.id))
    assert list(remaining.scalars().all()) == []

    login = _login(client, username="regular")
    assert "access_token" in login.json()


@pytest.mark.asyncio
async def test_totp_disable_requires_password(client, regular_user, auth_headers):
    """Disabling TOTP requires the account password."""
    await _enable_totp(client, auth_headers(regular_user))
    response = client.post(
        "/api/v1/auth/2fa/totp/disable",
        headers=auth_headers(regular_user),
        json={"password": "wrong"},
    )
    assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.asyncio
async def test_two_factor_status(client, regular_user, auth_headers):
    """The status endpoint reports enrollment state."""
    headers = auth_headers(regular_user)
    status_response = client.get("/api/v1/auth/2fa", headers=headers)
    assert status_response.status_code == status.HTTP_200_OK
    data = status_response.json()
    assert data["enabled"] is False
    assert data["totp_enabled"] is False
    assert data["webauthn_credentials"] == []
    assert data["recovery_codes_remaining"] == 0

    await _enable_totp(client, headers)
    data = client.get("/api/v1/auth/2fa", headers=headers).json()
    assert data["enabled"] is True
    assert data["totp_enabled"] is True
    assert data["recovery_codes_remaining"] == two_factor.RECOVERY_CODE_COUNT


@pytest.mark.asyncio
async def test_api_token_unaffected_by_2fa(client, regular_user, auth_headers, db_session):
    """API tokens keep working for accounts with 2FA enabled."""
    await _enable_totp(client, auth_headers(regular_user))

    created = client.post(
        "/api/v1/auth/api-tokens",
        headers=auth_headers(regular_user),
        json={"name": "cli"},
    )
    assert created.status_code == status.HTTP_201_CREATED
    token = created.json()["token"]

    me = client.get("/api/v1/users/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == status.HTTP_200_OK


# ---------------------------------------------------------------------------
# WebAuthn / security keys (driven with a software fake authenticator)
# ---------------------------------------------------------------------------


class _FakeAuthenticator:
    """A minimal software WebAuthn authenticator for tests."""

    def __init__(self, rp_id: str, origin: str):
        from cryptography.hazmat.primitives.asymmetric import ec
        from fido2.cose import ES256

        self.rp_id = rp_id
        self.origin = origin
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.credential_id = b"fake-credential-id-1"
        self.public_key = ES256.from_cryptography_key(self.key.public_key())
        self.counter = 0

    def _client_data(self, challenge: str, type_: str) -> bytes:
        return json.dumps({"type": type_, "challenge": challenge, "origin": self.origin}).encode()

    def register(self, options: dict) -> dict:
        """Produce an attestation response for the given create options."""
        from fido2.utils import websafe_encode
        from fido2.webauthn import AttestationObject, AttestedCredentialData, AuthenticatorData

        challenge = options["publicKey"]["challenge"]
        cred = AttestedCredentialData.create(b"\x00" * 16, self.credential_id, self.public_key)
        rp_hash = hashlib.sha256(self.rp_id.encode()).digest()
        auth_data = AuthenticatorData.create(
            rp_hash,
            AuthenticatorData.FLAG.UP | AuthenticatorData.FLAG.AT,
            self.counter,
            bytes(cred),
        )
        attestation = AttestationObject.create("none", auth_data, {})
        return {
            "id": websafe_encode(self.credential_id),
            "rawId": websafe_encode(self.credential_id),
            "response": {
                "clientDataJSON": websafe_encode(self._client_data(challenge, "webauthn.create")),
                "attestationObject": websafe_encode(bytes(attestation)),
                "transports": ["usb"],
            },
            "type": "public-key",
        }

    def authenticate(self, options: dict) -> dict:
        """Produce an assertion response for the given request options."""
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric.ec import ECDSA
        from fido2.utils import websafe_encode
        from fido2.webauthn import AuthenticatorData

        self.counter += 1
        challenge = options["publicKey"]["challenge"]
        client_data = self._client_data(challenge, "webauthn.get")
        rp_hash = hashlib.sha256(self.rp_id.encode()).digest()
        auth_data = AuthenticatorData.create(rp_hash, AuthenticatorData.FLAG.UP, self.counter)
        signature = self.key.sign(
            bytes(auth_data) + hashlib.sha256(client_data).digest(),
            ECDSA(hashes.SHA256()),
        )
        return {
            "id": websafe_encode(self.credential_id),
            "rawId": websafe_encode(self.credential_id),
            "response": {
                "clientDataJSON": websafe_encode(client_data),
                "authenticatorData": websafe_encode(bytes(auth_data)),
                "signature": websafe_encode(signature),
            },
            "type": "public-key",
        }


def _fake_authenticator() -> _FakeAuthenticator:
    """Build an authenticator matching the TestClient host (rp testserver)."""
    return _FakeAuthenticator(rp_id="testserver", origin="http://testserver")


@pytest.mark.asyncio
async def test_webauthn_register_and_login(client, regular_user, auth_headers):
    """A security key can be registered and used as the login second factor."""
    headers = auth_headers(regular_user)
    authenticator = _fake_authenticator()

    begin = client.post("/api/v1/auth/2fa/webauthn/register/begin", headers=headers)
    assert begin.status_code == status.HTTP_200_OK
    options = begin.json()
    assert options["publicKey"]["challenge"]

    complete = client.post(
        "/api/v1/auth/2fa/webauthn/register/complete",
        headers=headers,
        json={
            "name": "YubiKey",
            "credential": authenticator.register(options),
        },
    )
    assert complete.status_code == status.HTTP_200_OK
    data = complete.json()
    assert data["credential"]["name"] == "YubiKey"
    assert data["credential"]["transports"] == "usb"
    # First factor enrolled → a batch of recovery codes is issued.
    assert len(data["recovery_codes"]) == two_factor.RECOVERY_CODE_COUNT

    # Password login now demands a second factor listing webauthn.
    challenge = _login(client, username="regular").json()
    assert challenge["mfa_required"] is True
    assert "webauthn" in challenge["methods"]

    begin_auth = client.post(
        "/api/v1/auth/2fa/login/webauthn/begin",
        json={"mfa_token": challenge["mfa_token"]},
    )
    assert begin_auth.status_code == status.HTTP_200_OK
    auth_options = begin_auth.json()

    complete_auth = client.post(
        "/api/v1/auth/2fa/login/webauthn/complete",
        json={
            "mfa_token": challenge["mfa_token"],
            "credential": authenticator.authenticate(auth_options),
        },
    )
    assert complete_auth.status_code == status.HTTP_200_OK
    assert complete_auth.json()["access_token"]


@pytest.mark.asyncio
async def test_webauthn_register_bad_response_fails(client, regular_user, auth_headers):
    """A malformed attestation does not register a credential."""
    headers = auth_headers(regular_user)
    begin = client.post("/api/v1/auth/2fa/webauthn/register/begin", headers=headers)
    assert begin.status_code == status.HTTP_200_OK

    response = client.post(
        "/api/v1/auth/2fa/webauthn/register/complete",
        headers=headers,
        json={"credential": {"id": "x", "response": {}}},
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.asyncio
async def test_webauthn_complete_without_begin_fails(client, regular_user, auth_headers):
    """Completing a ceremony that was never begun is rejected."""
    response = client.post(
        "/api/v1/auth/2fa/webauthn/register/complete",
        headers=auth_headers(regular_user),
        json={"credential": {"id": "x", "response": {}}},
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.asyncio
async def test_webauthn_delete(client, regular_user, auth_headers, db_session):
    """Removing the last security key clears leftover recovery codes."""
    headers = auth_headers(regular_user)
    authenticator = _fake_authenticator()
    begin = client.post("/api/v1/auth/2fa/webauthn/register/begin", headers=headers)
    complete = client.post(
        "/api/v1/auth/2fa/webauthn/register/complete",
        headers=headers,
        json={"credential": authenticator.register(begin.json())},
    )
    credential_id = complete.json()["credential"]["id"]

    delete = client.delete(f"/api/v1/auth/2fa/webauthn/{credential_id}", headers=headers)
    assert delete.status_code == status.HTTP_204_NO_CONTENT

    remaining = await db_session.execute(
        select(WebAuthnCredential).where(WebAuthnCredential.user_id == regular_user.id)
    )
    assert list(remaining.scalars().all()) == []
    codes = await db_session.execute(select(RecoveryCode).where(RecoveryCode.user_id == regular_user.id))
    assert list(codes.scalars().all()) == []

    # Login no longer requires a second factor.
    login = _login(client, username="regular")
    assert "access_token" in login.json()


@pytest.mark.asyncio
async def test_webauthn_delete_not_found(client, regular_user, auth_headers):
    """Deleting an unknown credential id returns 404."""
    response = client.delete("/api/v1/auth/2fa/webauthn/nope", headers=auth_headers(regular_user))
    assert response.status_code == status.HTTP_404_NOT_FOUND


# ---------------------------------------------------------------------------
# Admin clearing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_admin_clear_2fa(client, regular_user, admin_user, auth_headers, db_session):
    """Admins can clear a user's 2FA state entirely."""
    await _enable_totp(client, auth_headers(regular_user))
    assert await two_factor.user_has_2fa(db_session, regular_user)

    response = client.post(
        f"/api/v1/admin/users/{regular_user.id}/2fa/clear",
        headers=auth_headers(admin_user),
    )
    assert response.status_code == status.HTTP_200_OK

    await db_session.refresh(regular_user)
    assert regular_user.totp_secret is None
    assert not await two_factor.user_has_2fa(db_session, regular_user)

    login = _login(client, username="regular")
    assert "access_token" in login.json()


@pytest.mark.asyncio
async def test_admin_clear_2fa_forbidden_for_users(client, regular_user, auth_headers):
    """Non-admin users cannot clear 2FA for others."""
    response = client.post(
        f"/api/v1/admin/users/{regular_user.id}/2fa/clear",
        headers=auth_headers(regular_user),
    )
    assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.asyncio
async def test_admin_clear_2fa_unknown_user(client, admin_user, auth_headers):
    """Clearing 2FA for a missing user returns 404."""
    response = client.post(
        "/api/v1/admin/users/nonexistent/2fa/clear",
        headers=auth_headers(admin_user),
    )
    assert response.status_code == status.HTTP_404_NOT_FOUND


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cli_clear_2fa(db_session, monkeypatch, capsys):
    """The admin CLI clears a user's 2FA state."""
    from contextlib import asynccontextmanager
    from types import SimpleNamespace

    from songhive.cli import admin as cli_admin

    @asynccontextmanager
    async def _fake_session(session):
        yield session

    user = await create_user(db_session, "alice", "alice@example.com", "secret")
    user.totp_secret = two_factor.encrypt_secret("JBSWY3DPEHPK3PXP")
    db_session.add(RecoveryCode(user_id=user.id, code_hash="x" * 64))
    db_session.add(WebAuthnCredential(user_id=user.id, credential_id="abc", credential_data=b"blob"))
    await db_session.flush()

    monkeypatch.setattr(cli_admin, "get_session", lambda: _fake_session(db_session))
    args = SimpleNamespace(username="alice")
    await cli_admin._handle_clear_2fa(args)

    captured = capsys.readouterr()
    assert "Two-factor authentication cleared" in captured.out
    await db_session.refresh(user)
    assert user.totp_secret is None
    assert not await two_factor.user_has_2fa(db_session, user)


@pytest.mark.asyncio
async def test_cli_clear_2fa_unknown_user(db_session, monkeypatch, capsys):
    """The CLI reports an error for unknown usernames."""
    from contextlib import asynccontextmanager
    from types import SimpleNamespace

    from songhive.cli import admin as cli_admin

    @asynccontextmanager
    async def _fake_session(session):
        yield session

    monkeypatch.setattr(cli_admin, "get_session", lambda: _fake_session(db_session))
    args = SimpleNamespace(username="ghost")
    with pytest.raises(SystemExit):
        await cli_admin._handle_clear_2fa(args)
    assert "not found" in capsys.readouterr().err
