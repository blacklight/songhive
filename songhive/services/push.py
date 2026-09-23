"""
Browser push notification support via the W3C Web Push protocol.

Stores per-user Push API subscriptions and sends VAPID-signed push
messages through the browser's push service. Invalid/expired
subscriptions are removed so retries stop.
"""

import asyncio
import base64
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple, cast

from sqlalchemy import delete, select
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from ..config.schema import SonghiveConfig
from ..models.notification import PushSubscription

logger = logging.getLogger(__name__)

try:
    from pywebpush import WebPushException, webpush

    _HAS_PYWEBPUSH = True
except ImportError:  # pragma: no cover
    _HAS_PYWEBPUSH = False
    WebPushException = Exception  # type: ignore
    webpush = None  # type: ignore


def _normalize_base64url(value: str) -> str:
    """Add padding so ``value`` can be decoded with ``urlsafe_b64decode``."""
    pad = 4 - len(value) % 4
    return value + "=" * (pad % 4)


def _base64url_to_bytes(value: str) -> bytes:
    """Decode a base64url string (with or without padding) to raw bytes."""
    return base64.urlsafe_b64decode(_normalize_base64url(value))


def _bytes_to_base64url(data: bytes) -> str:
    """Encode raw bytes as an unpadded base64url string."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def is_push_available(config: SonghiveConfig) -> bool:
    """Return whether VAPID is configured and push is enabled."""
    if not _HAS_PYWEBPUSH or not config.notifications.push_enabled:
        return False
    return bool(
        config.notifications.vapid_private_key
        and config.notifications.vapid_public_key
        and config.notifications.vapid_subscriber
    )


def get_public_key(config: SonghiveConfig) -> Optional[str]:
    """Return the VAPID public key to expose to the frontend, or ``None``."""
    return config.notifications.vapid_public_key if is_push_available(config) else None


def generate_vapid_keys() -> Tuple[str, str]:
    """Generate a fresh VAPID key pair.

    Returns ``(public_key, private_key)`` as unpadded base64url strings.
    The public key is the uncompressed 65-byte EC point; the private key is
    the 32-byte scalar.
    """
    from cryptography.hazmat.primitives.asymmetric import ec

    private_key = ec.generate_private_key(ec.SECP256R1())
    pv = private_key.private_numbers().private_value.to_bytes(32, "big")
    public = private_key.public_key().public_numbers()
    raw_pub = b"\x04" + public.x.to_bytes(32, "big") + public.y.to_bytes(32, "big")
    return _bytes_to_base64url(raw_pub), _bytes_to_base64url(pv)


async def store_subscription(
    session: AsyncSession,
    user_id: str,
    endpoint: str,
    p256dh: str,
    auth: str,
) -> PushSubscription:
    """Persist a push subscription, replacing any existing one for the endpoint."""
    result = await session.execute(
        select(PushSubscription).where(
            PushSubscription.user_id == user_id,
            PushSubscription.endpoint == endpoint,
        )
    )
    sub = result.scalar_one_or_none()
    if sub is None:
        sub = PushSubscription(
            user_id=user_id,
            endpoint=endpoint,
            p256dh=p256dh,
            auth=auth,
        )
        session.add(sub)
    else:
        sub.p256dh = p256dh
        sub.auth = auth
    await session.flush()
    return sub


async def remove_subscription(session: AsyncSession, user_id: str, endpoint: str) -> int:
    """Remove a user's push subscription for the given endpoint."""
    result = cast(
        CursorResult,
        await session.execute(
            delete(PushSubscription).where(
                PushSubscription.user_id == user_id,
                PushSubscription.endpoint == endpoint,
            )
        ),
    )
    await session.flush()
    return result.rowcount or 0


async def list_subscriptions(session: AsyncSession, user_id: str) -> List[PushSubscription]:
    """Return all active push subscriptions for a user."""
    result = await session.execute(
        select(PushSubscription).where(PushSubscription.user_id == user_id).order_by(PushSubscription.created_at.desc())
    )
    return list(result.scalars().all())


def _notification_body(notification: Dict[str, Any], config: SonghiveConfig) -> str:
    """Build a short body text for the push message."""
    payload = notification.get("payload") or {}
    actor = payload.get("actor_display_name") or payload.get("actor_name") or notification.get("actor_url") or "Someone"
    ntype = notification.get("type") or "notification"
    instance = config.federation.instance_name or "Songhive"
    return f"{actor} — {ntype} on {instance}"


def _notification_url(notification: Dict[str, Any]) -> str:
    """Return the best URL for the notification click-through."""
    source = notification.get("source_url")
    if source and isinstance(source, str):
        if source.startswith("/"):
            return source
        return f"/notifications?highlight={notification.get('id') or ''}"
    return "/notifications"


def _push_payload(
    notification: Dict[str, Any],
    config: SonghiveConfig,
) -> Dict[str, Any]:
    """Build the JSON payload delivered to the service worker."""
    return {
        "title": config.federation.instance_name or "Songhive",
        "body": _notification_body(notification, config),
        "icon": "/pwa/pwa-192x192.png",
        "badge": "/pwa/pwa-72x72.png",
        "tag": notification.get("id"),
        "data": {
            "url": _notification_url(notification),
            "notification_id": notification.get("id"),
        },
    }


def _send_webpush_sync(
    subscription: PushSubscription,
    payload: Dict[str, Any],
    config: SonghiveConfig,
) -> None:
    """Send one push message synchronously (runs in a thread from async code)."""
    if not _HAS_PYWEBPUSH or webpush is None:
        raise RuntimeError("pywebpush is not installed")

    private_key = config.notifications.vapid_private_key
    assert private_key is not None
    assert config.notifications.vapid_subscriber is not None

    vapid_claims = {
        "sub": config.notifications.vapid_subscriber,
        "exp": int((datetime.now(timezone.utc) + timedelta(hours=12)).timestamp()),
    }

    webpush(
        subscription_info={
            "endpoint": subscription.endpoint,
            "keys": {
                "p256dh": subscription.p256dh,
                "auth": subscription.auth,
            },
        },
        data=json.dumps(payload),
        vapid_private_key=private_key,
        vapid_claims=vapid_claims,
        ttl=86400,
        timeout=config.notifications.push_timeout,
    )


async def send_notification_to_user(
    session: AsyncSession,
    user_id: str,
    notification: Dict[str, Any],
    config: SonghiveConfig,
) -> int:
    """Send a push message to every active subscription for ``user_id``.

    Invalid/expired subscriptions (HTTP 404/410 from the push service) are
    deleted. Returns the number of messages successfully delivered.
    """
    if not is_push_available(config):
        return 0

    subscriptions = await list_subscriptions(session, user_id)
    if not subscriptions:
        return 0

    payload = _push_payload(notification, config)
    sent = 0

    for sub in subscriptions:
        try:
            # pywebpush uses blocking requests; run it in a worker thread.
            await asyncio.to_thread(_send_webpush_sync, sub, payload, config)
            sent += 1
        except WebPushException as exc:
            status_code = getattr(exc, "status_code", None)
            if status_code in (404, 410, 403):
                logger.info(
                    "Push subscription %s... is no longer valid; removing",
                    sub.endpoint[:40],
                )
                try:
                    await session.delete(sub)
                    await session.flush()
                except Exception as del_exc:
                    logger.warning("Could not remove invalid push subscription: %s", del_exc)
            else:
                logger.warning(
                    "Failed to send push to %s...: %s",
                    sub.endpoint[:40],
                    exc,
                )
        except Exception as exc:
            logger.warning(
                "Failed to send push to %s...: %s",
                sub.endpoint[:40],
                exc,
            )

    return sent
