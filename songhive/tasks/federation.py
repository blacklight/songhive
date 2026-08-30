"""
Federation tasks: process incoming and deliver outgoing ActivityPub activities.
"""

import asyncio
import base64
import json
import logging
from typing import Optional

import requests
from pubby import ActivityPubError, SignatureVerificationError
from pubby.crypto import load_private_key, sign_request
from pubby.handlers._inbox import InboxProcessor

from ..config import load_config
from ..federation.actors import get_federation_storage
from ..federation.storage import get_or_create_private_key
from ..models.base import dispose_and_reset, get_session, init_db
from ..models.user import User
from ..services.admin_tasks import provision_federation_keys as _provision_federation_keys
from ..services.federation import ensure_user_actor, extract_domain, is_domain_blocked
from .celery import celery_app

logger = logging.getLogger(__name__)


def _load_user_actor(username: str) -> Optional[User]:
    """Load a local user and ensure they have federation keys."""
    from ..services.auth import get_user_by_username

    async def _load():
        try:
            async with get_session() as session:
                user = await get_user_by_username(session, username)
                if user is None:
                    return None
                config = load_config([])
                ensure_user_actor(user, config)
                return user
        finally:
            await dispose_and_reset()

    return asyncio.run(_load())


@celery_app.task(name="songhive.tasks.federation.process_incoming")
def process_incoming(
    activity: dict,
    username: Optional[str] = None,
    method: str = "POST",
    path: str = "/ap/inbox",
    headers: Optional[dict[str, str]] = None,
    body_b64: Optional[str] = None,
):
    """
    Process an incoming ActivityPub activity (e.g., Follow, Like).

    :param activity: The parsed incoming ActivityPub activity.
    :param username: Optional local user the activity is addressed to.
    :param method: HTTP method of the incoming request.
    :param path: Request path of the incoming request.
    :param headers: Request headers (for signature verification).
    :param body_b64: Base64-encoded raw request body (for signature verification).
    """
    config = load_config([])
    if not config.federation.enabled or not config.federation.instance_domain:
        logger.debug("Federation disabled or no instance domain; skipping incoming activity")
        return None

    actor = activity.get("actor")
    if not isinstance(actor, str):
        logger.warning("Incoming activity has no usable actor; dropping")
        return None

    sender_domain = extract_domain(actor)
    if is_domain_blocked(sender_domain, config):
        logger.info("Dropping activity from blocked or non-allowed domain: %s", sender_domain)
        return None

    storage = get_federation_storage(config.database.url)
    domain = config.federation.instance_domain

    actor_id: Optional[str]
    private_key_pem: Optional[str]
    if username is None:
        actor_id = f"https://{domain}/ap/actor"
        private_key_path = get_or_create_private_key(config.federation.private_key_path)
        private_key_pem = private_key_path.read_text(encoding="utf-8")
    else:
        init_db(config.database.url)
        user = _load_user_actor(username)
        if user is None:
            logger.warning("Local user %r not found; dropping incoming activity", username)
            return None
        actor_id = user.actor_url
        private_key_pem = user.private_key_pem

    if not actor_id or not private_key_pem:
        logger.warning("No actor context available for incoming activity; dropping")
        return None

    key_id = f"{actor_id}#main-key"
    private_key = load_private_key(private_key_pem)
    processor = InboxProcessor(
        storage=storage,
        actor_id=actor_id,
        private_key=private_key,
        key_id=key_id,
    )

    body: Optional[bytes] = None
    if body_b64:
        try:
            body = base64.b64decode(body_b64)
        except Exception:
            logger.warning("Failed to decode base64 request body for incoming activity")

    try:
        result = processor.process(
            activity,
            method=method,
            path=path,
            headers=headers,
            body=body,
        )
    except SignatureVerificationError:
        logger.warning(
            "Signature verification failed for incoming %s from %s for actor %s",
            activity.get("type", "activity"),
            actor,
            actor_id,
        )
        return None
    except ActivityPubError as exc:
        logger.warning(
            "ActivityPub error processing incoming %s from %s for actor %s: %s",
            activity.get("type", "activity"),
            actor,
            actor_id,
            exc,
        )
        return None

    logger.info(
        "Processed incoming %s from %s for actor %s",
        activity.get("type", "activity"),
        actor,
        actor_id,
    )
    return result


@celery_app.task(
    bind=True,
    name="songhive.tasks.federation.deliver_activity",
    max_retries=7,
    default_retry_delay=30,
)
def deliver_activity(
    self,
    activity: dict,
    inbox_url: str,
    actor_key_id: str,
    private_key_pem: str,
):
    """
    Deliver an ActivityPub activity to a remote inbox.

    Signs the request with HTTP signatures via pubby and retries transient
    failures with exponential backoff.
    """
    config = load_config([])
    if not config.federation.enabled or not config.federation.instance_domain:
        logger.debug("Federation disabled or no instance domain; skipping delivery")
        return None

    inbox_domain = extract_domain(inbox_url)
    if is_domain_blocked(inbox_domain, config):
        logger.info("Dropping delivery to blocked or non-allowed domain: %s", inbox_domain)
        return None

    body = json.dumps(activity, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    headers = {
        "Content-Type": "application/activity+json",
        "Accept": "application/activity+json",
    }

    private_key = load_private_key(private_key_pem)
    signed_headers = sign_request(
        private_key=private_key,
        key_id=actor_key_id,
        method="POST",
        url=inbox_url,
        body=body,
        headers=headers,
    )

    try:
        response = requests.post(inbox_url, data=body, headers=signed_headers, timeout=15)
    except requests.RequestException as exc:
        logger.warning("Delivery to %s failed (%s); retrying", inbox_url, type(exc).__name__)
        raise self.retry(countdown=30 * 2**self.request.retries, exc=exc)

    if 200 <= response.status_code < 400:
        logger.info("Delivered activity to %s (status %s)", inbox_url, response.status_code)
        return response

    if response.status_code >= 500 or response.status_code == 429:
        logger.warning("Delivery to %s returned %s; retrying", inbox_url, response.status_code)
        retry_exc = requests.HTTPError(
            f"Delivery failed with status {response.status_code}",
            response=response,
        )
        raise self.retry(countdown=30 * 2**self.request.retries, exc=retry_exc)

    logger.warning("Delivery to %s returned %s; giving up", inbox_url, response.status_code)
    return None


@celery_app.task(name="songhive.tasks.federation.provision_federation_keys")
def provision_federation_keys(dry_run: bool = False) -> int:
    """
    Celery task that back-fills ActivityPub actor URLs and keypairs.

    Loads the runtime configuration, initializes the database, and runs the
    async provisioning helper inside ``asyncio.run``.
    """
    import asyncio

    from ..config import load_config

    logger.info("Starting federation key provisioning (dry_run=%s)", dry_run)

    config = load_config([])
    init_db(config.database.url)

    async def _run() -> int:
        try:
            async with get_session() as session:
                count = await _provision_federation_keys(session, config, dry_run=dry_run)
                await session.commit()
                return count
        finally:
            await dispose_and_reset()

    return asyncio.run(_run())
