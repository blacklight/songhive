"""
TIDAL implementation of the device-auth / PKCE-paste provider hooks.

Device flow: ``Session.get_link_login()`` returns the link + user code;
polling posts the ``urn:ietf:params:oauth:grant-type:device_code`` grant
once per call (the generic framework handles pacing and ``slow_down``).

PKCE flow: ``Session.pkce_login_url()`` produces the authorize URL; the
verifier lives in the Redis pending entry until ``complete_pkce`` trades
the user-pasted redirect URL for tokens via ``pkce_get_auth_token``.
"""

import logging
from typing import Any

from ..device_auth import (
    DeviceAuthChallenge,
    DeviceAuthError,
    DeviceAuthPollResult,
    DeviceAuthProvider,
)
from .session import credential_fragment_from_session

logger = logging.getLogger(__name__)


def _new_session(config: dict):
    """Build a bare ``tidalapi.Session`` honoring client-id overrides."""
    from .session import _import_tidalapi

    tidalapi, _ = _import_tidalapi()
    session = tidalapi.Session()
    for field_name, attr in (
        ("client_id", "client_id"),
        ("client_secret", "client_secret"),
        ("client_id_pkce", "client_id_pkce"),
        ("client_secret_pkce", "client_secret_pkce"),
    ):
        value = config.get(field_name)
        if isinstance(value, str) and value.strip():
            setattr(session.config, attr, value.strip())
    return session


def _fragment_with_session_info(session: Any, is_pkce: bool) -> dict:
    """Populate user/session fields on ``session`` and return the fragment."""
    try:
        request = session.request.request("GET", "sessions")
        if request.ok:
            info = request.json()
            session.session_id = info.get("sessionId")
            session.country_code = info.get("countryCode")
            user_id = info.get("userId")
            if user_id is not None:
                from tidalapi.user import LoggedInUser

                session.user = LoggedInUser(session, int(user_id))
                # Best-effort display fields; failure here must not fail auth.
                try:
                    fetched = session.user.factory()
                    session.user = fetched
                except Exception:
                    pass
    except Exception:
        logger.warning("TIDAL session info fetch failed after auth", exc_info=True)
    return credential_fragment_from_session(session, is_pkce=is_pkce)


class TidalDeviceAuthProvider(DeviceAuthProvider):
    """TIDAL device-code and PKCE-paste hooks for ``external.device_auth``."""

    provider_type = "tidal"
    supports_device_code = True
    supports_pkce = True

    def begin_device(self, config: dict) -> DeviceAuthChallenge:
        session = _new_session(config)
        try:
            link = session.get_link_login()
        except Exception as exc:
            raise DeviceAuthError(f"Could not start TIDAL device login: {exc.__class__.__name__}") from exc
        return DeviceAuthChallenge(
            user_code=link.user_code,
            verification_uri=link.verification_uri,
            verification_uri_complete=link.verification_uri_complete,
            expires_in=int(link.expires_in),
            interval=max(1, int(link.interval)),
            provider_session={"device_code": link.device_code},
        )

    def poll_device(self, config: dict, session_data: dict) -> DeviceAuthPollResult:
        """One non-blocking device_code grant POST against the token endpoint."""
        session = _new_session(config)
        device_code = session_data.get("device_code")
        if not device_code:
            return DeviceAuthPollResult(status="denied", error="Missing device code")

        params = {
            "client_id": session.config.client_id,
            "client_secret": session.config.client_secret,
            "device_code": device_code,
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "scope": "r_usr w_usr w_sub",
        }
        try:
            response = session.request_session.post(
                session.config.api_oauth2_token,
                params,
            )
        except Exception:
            return DeviceAuthPollResult(status="pending", error="token endpoint unreachable")

        try:
            payload = response.json()
        except ValueError:
            payload = {}

        if response.ok:
            try:
                session.process_auth_token(payload, is_pkce_token=False)
            except Exception as exc:
                raise DeviceAuthError("TIDAL login succeeded but session setup failed") from exc
            fragment = _fragment_with_session_info(session, is_pkce=False)
            return DeviceAuthPollResult(
                status="granted",
                config_fragment=fragment,
                display={k: fragment[k] for k in ("username", "email", "user_id", "country_code") if fragment.get(k)},
            )

        error = str(payload.get("error") or "")
        if error == "authorization_pending":
            return DeviceAuthPollResult(status="pending")
        if error == "slow_down":
            return DeviceAuthPollResult(status="slow_down")
        if error in ("expired_token", "access_denied"):
            return DeviceAuthPollResult(status="expired" if error == "expired_token" else "denied")
        return DeviceAuthPollResult(
            status="denied",
            error=str(payload.get("error_description") or error or f"HTTP {response.status_code}")[:200],
        )

    def begin_pkce(self, config: dict) -> tuple[str, dict]:
        session = _new_session(config)
        try:
            url = session.pkce_login_url()
        except Exception as exc:
            raise DeviceAuthError(f"Could not start TIDAL PKCE login: {exc.__class__.__name__}") from exc
        return url, {
            "code_verifier": session.config.code_verifier,
            "client_unique_key": session.config.client_unique_key,
        }

    def complete_pkce(
        self,
        config: dict,
        session_data: dict,
        redirect_url: str,
    ) -> DeviceAuthPollResult:
        session = _new_session(config)
        verifier = session_data.get("code_verifier")
        if not verifier:
            raise DeviceAuthError("Missing PKCE verifier")
        session.config.code_verifier = verifier
        if session_data.get("client_unique_key"):
            session.config.client_unique_key = session_data["client_unique_key"]
        try:
            token_payload = session.pkce_get_auth_token(redirect_url)
            session.process_auth_token(token_payload, is_pkce_token=True)
        except Exception as exc:
            raise DeviceAuthError(f"TIDAL PKCE exchange failed: {exc.__class__.__name__}") from exc
        fragment = _fragment_with_session_info(session, is_pkce=True)
        return DeviceAuthPollResult(
            status="granted",
            config_fragment=fragment,
            display={k: fragment[k] for k in ("username", "email", "user_id", "country_code") if fragment.get(k)},
        )
