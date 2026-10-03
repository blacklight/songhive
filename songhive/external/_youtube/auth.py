"""
YouTube implementation of the generic device-auth provider hooks.

``ytmusicapi.auth.oauth.OAuthCredentials`` wraps Google's OAuth
device-authorization flow for TV/limited-input clients:

- ``begin_device`` calls ``get_code()`` → ``device_code`` + ``user_code`` +
  ``verification_url``; the pending device code rides along in the generic
  Redis-stored ``provider_session``.
- ``poll_device`` performs one ``token_from_code`` grant POST per call;
  Google's pending/slow_down/expired/denied responses map onto the shared
  ``DeviceAuthPollResult`` statuses.

PKCE isn't supported — YouTube has no redirect-back flow a self-hosted
instance can terminate; users without OAuth client credentials paste a
captured browser session into the ``request_headers`` config field instead.
"""

import logging

from ..device_auth import (
    DeviceAuthChallenge,
    DeviceAuthError,
    DeviceAuthPollResult,
    DeviceAuthProvider,
)
from .session import credential_fragment_from_token, oauth_credentials

logger = logging.getLogger(__name__)


def _account_display(config: dict) -> dict:
    """Best-effort account name/channel for the UI after a grant."""
    import asyncio

    async def _fetch() -> dict:
        from .session import ytmusic_for_config

        try:
            ytm = await ytmusic_for_config(config)
            info = await asyncio.to_thread(ytm.get_account_info)
        except Exception:
            return {}
        display: dict[str, str] = {}
        if isinstance(info, dict):
            if info.get("accountName"):
                display["account_name"] = str(info["accountName"])
            if info.get("channelHandle"):
                display["channel_handle"] = str(info["channelHandle"])
        return display

    # poll_device runs inside asyncio.to_thread, so no loop is active here.
    try:
        return asyncio.run(_fetch())
    except Exception:
        logger.debug("YouTube account info fetch failed after auth", exc_info=True)
        return {}


class YouTubeDeviceAuthProvider(DeviceAuthProvider):
    """Google OAuth device-flow hooks for the YouTube provider."""

    provider_type = "youtube"
    supports_device_code = True
    supports_pkce = False

    def begin_device(self, config: dict) -> DeviceAuthChallenge:
        try:
            credentials = oauth_credentials(config)
        except Exception as exc:
            raise DeviceAuthError(
                "YouTube OAuth client credentials are not configured on this instance; "
                "ask an admin for external_libraries.youtube.client_id/client_secret"
            ) from exc
        try:
            code = credentials.get_code()
        except Exception as exc:
            raise DeviceAuthError(f"Could not start YouTube device login: {exc.__class__.__name__}") from exc
        if not isinstance(code, dict) or not code.get("device_code"):
            raise DeviceAuthError("YouTube did not return a device code")
        return DeviceAuthChallenge(
            user_code=str(code.get("user_code") or ""),
            verification_uri=str(code.get("verification_url") or "https://www.google.com/device"),
            expires_in=int(code.get("expires_in") or 1800),
            interval=max(1, int(code.get("interval") or 5)),
            provider_session={
                "device_code": code["device_code"],
                "client_id": credentials.client_id,
                "client_secret": credentials.client_secret,
            },
        )

    def poll_device(self, config: dict, session_data: dict) -> DeviceAuthPollResult:
        device_code = session_data.get("device_code")
        if not device_code:
            return DeviceAuthPollResult(status="denied", error="Missing device code")
        # The challenge pins the issuing client pair so a mid-flow config
        # change can't swap which OAuth app the token belongs to.
        credentials_config = {
            "client_id": session_data.get("client_id") or config.get("client_id"),
            "client_secret": session_data.get("client_secret") or config.get("client_secret"),
        }
        try:
            credentials = oauth_credentials(credentials_config)
        except Exception as exc:
            return DeviceAuthPollResult(status="denied", error=str(exc))
        try:
            token = credentials.token_from_code(str(device_code))
        except Exception as exc:
            return DeviceAuthPollResult(status="denied", error=f"Token request failed: {exc.__class__.__name__}")
        if not isinstance(token, dict):
            return DeviceAuthPollResult(status="denied", error="Empty token response")

        error = str(token.get("error") or "")
        if token.get("access_token"):
            fragment = credential_fragment_from_token(
                token,
                oauth_client_id=str(credentials_config.get("client_id") or ""),
                oauth_client_secret=str(credentials_config.get("client_secret") or ""),
                display=_account_display(
                    {
                        **config,
                        **credentials_config,
                        **credential_fragment_from_token(
                            token,
                            oauth_client_id=str(credentials_config.get("client_id") or ""),
                            oauth_client_secret=str(credentials_config.get("client_secret") or ""),
                        ),
                    }
                ),
            )
            display = {k: fragment[k] for k in ("account_name", "channel_handle") if fragment.get(k)}
            return DeviceAuthPollResult(
                status="granted",
                config_fragment=fragment,
                display=display,
            )
        if error == "authorization_pending":
            return DeviceAuthPollResult(status="pending")
        if error == "slow_down":
            return DeviceAuthPollResult(status="slow_down")
        if error == "expired_token":
            return DeviceAuthPollResult(status="expired")
        if error == "access_denied":
            return DeviceAuthPollResult(status="denied", error="Authorization denied")
        return DeviceAuthPollResult(
            status="denied",
            error=str(token.get("error_description") or error or "authorization failed")[:200],
        )
