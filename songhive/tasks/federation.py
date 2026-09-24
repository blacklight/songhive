"""
Federation tasks: process incoming and deliver outgoing ActivityPub activities.
"""

import asyncio
import base64
import logging
import re
from typing import Optional
from urllib.parse import urlparse

import requests
from pubby import ActivityPubError, FollowPolicy, SignatureVerificationError
from pubby import deliver_activity as pubby_deliver_activity
from pubby.crypto import load_private_key
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


def _follow_policy_for_target(target_actor_id: str, requester_actor_id: str) -> Optional[FollowPolicy]:
    """
    Resolve an incoming Follow's target to the owner's approval policy.

    Only follows of a user actor consult the per-user
    ``followers_approval`` setting — object follows (FEP-efda thread
    subscriptions) and the instance actor return ``None`` so pubby keeps
    its auto-accept default. ``FollowPolicy`` shares its values with
    ``FollowersApproval``.

    Moderation applies on the requester: a suspended requester is
    rejected outright, a blocked requester is rejected, and a limited
    requester always lands as a pending request for manual approval.
    """
    from ..services import moderation as moderation_service
    from ..services.auth import get_user_by_actor_url

    async def _load():
        try:
            async with get_session() as session:
                user = await get_user_by_actor_url(session, target_actor_id)
                if user is None:
                    return None
                if await moderation_service.actor_is_suspended(session, requester_actor_id):
                    return FollowPolicy.REJECT
                if await moderation_service.actor_blocked_by_local(session, user.id, requester_actor_id):
                    return FollowPolicy.REJECT
                if await moderation_service.actor_is_limited(session, requester_actor_id):
                    return FollowPolicy.MANUAL
                return FollowPolicy(user.followers_approval)
        finally:
            await dispose_and_reset()

    return asyncio.run(_load())


def _object_follow_owner_username(config, activity: dict) -> Optional[str]:
    """
    Resolve the local owner username of an object-scoped inbound ``Follow``.

    Shared-inbox deliveries arrive with ``username=None``, which would make
    pubby answer the ``Accept(Follow)`` as the instance actor. Remote music
    servers — Funkwhale in particular — only honor an ``Accept`` whose
    actor is the followed object's owning actor (``library.actor``), so a
    ``Follow`` of ``/libraries/{id}`` or ``/users/{u}/library`` must be
    processed under the owner's actor. Returns ``None`` for non-Follow
    activities, non-local targets and objects with no resolvable owner.
    """
    if activity.get("type") != "Follow":
        return None
    target = activity.get("object")
    if isinstance(target, dict):
        target = target.get("id")
    if not isinstance(target, str):
        return None
    parsed = urlparse(target)
    domain = config.federation.instance_domain
    if parsed.hostname != domain:
        return None
    path = parsed.path or ""

    user_match = re.match(r"^/users/(?P<username>[^/?#]+)/", path)
    if user_match:
        return user_match.group("username")

    library_match = re.match(r"^/libraries/(?P<library_id>[^/?#]+)/?$", path)
    if library_match:
        from ..models.library import Library
        from ..services import moderation as moderation_service
        from ..services.auth import get_user_by_id

        init_db(config.database.url)

        async def _load():
            try:
                async with get_session() as session:
                    library = await session.get(Library, library_match.group("library_id"))
                    if library is None or library.owner_id is None:
                        return None
                    owner = await get_user_by_id(session, library.owner_id)
                    if owner is None or not owner.is_active:
                        return None
                    if await moderation_service.user_is_suspended(session, owner.id):
                        return None
                    return owner.username
            finally:
                await dispose_and_reset()

        return asyncio.run(_load())
    return None


def _load_incoming_moderation(actor: str, username: Optional[str]) -> tuple:
    """
    Load moderation state for an incoming activity — synchronous wrapper.

    Returns ``(instance_policies, actor_suspended, recipient_suspended)``.
    Refreshing the policy snapshot here keeps every sync domain check in
    this task — including the ``InboxProcessor`` allow/block lists — in
    step with the database moderation rows.
    """
    from ..services import moderation as moderation_service
    from ..services.auth import get_user_by_username

    async def _load():
        try:
            async with get_session() as session:
                policies = await moderation_service.load_instance_policies(session)
                actor_suspended = await moderation_service.actor_is_suspended(session, actor)
                recipient_suspended = False
                if username is not None:
                    user = await get_user_by_username(session, username)
                    recipient_suspended = user is not None and await moderation_service.user_is_suspended(
                        session, user.id
                    )
                return policies, actor_suspended, recipient_suspended
        finally:
            await dispose_and_reset()

    return asyncio.run(_load())


def _refresh_instance_policies(database_url: str) -> dict:
    """Reload the database instance policies into the sync snapshot."""
    from ..services import moderation as moderation_service

    init_db(database_url)

    async def _load():
        try:
            async with get_session() as session:
                return await moderation_service.load_instance_policies(session)
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

    storage = get_federation_storage(config.database.url)
    domain = config.federation.instance_domain

    init_db(config.database.url)
    db_policies, actor_suspended, recipient_suspended = _load_incoming_moderation(actor, username)
    if actor_suspended:
        logger.info("Dropping incoming %s from suspended actor %s", activity.get("type"), actor)
        return None
    if recipient_suspended:
        logger.info("Dropping incoming %s for suspended user %s", activity.get("type"), username)
        return None

    # ``_load_incoming_moderation`` disposes the shared engine on exit, so
    # (re-)init unconditionally before the next session user.
    init_db(config.database.url)

    actor_id: Optional[str]
    private_key_pem: Optional[str]
    # A shared-inbox ``Follow`` of a local music object (a federated
    # library) must be answered by the object's owning actor — Funkwhale
    # matches ``Accept.actor`` against ``library.actor`` — so the owner's
    # actor context is resolved before falling back to the instance actor.
    object_owner = _object_follow_owner_username(config, activity) if username is None else None
    if username is None and object_owner is None:
        actor_id = f"https://{domain}/ap/actor"
        private_key_path = get_or_create_private_key(config.federation.private_key_path)
        private_key_pem = private_key_path.read_text(encoding="utf-8")
    else:
        resolved_username = username or object_owner
        assert resolved_username is not None  # guaranteed by the branch guard
        user = _load_user_actor(resolved_username)
        if user is None:
            logger.warning("Local user %r not found; dropping incoming activity", username or object_owner)
            return None
        actor_id = user.actor_url
        private_key_pem = user.private_key_pem

    # The follow-policy callback resolves Follow targets through the
    # database even for shared-inbox deliveries (``username=None``).
    # ``_load_user_actor`` disposes the shared engine on exit, so (re-)init
    # unconditionally: ``init_db`` is a no-op when an engine is already set.
    init_db(config.database.url)

    if not actor_id or not private_key_pem:
        logger.warning("No actor context available for incoming activity; dropping")
        return None

    key_id = f"{actor_id}#main-key"
    private_key = load_private_key(private_key_pem)
    # Database defederation layers over the configured block list.
    blocked_instances = list(config.federation.blocked_instances) + [
        domain for domain, action in db_policies.items() if action == "defederate"
    ]
    processor = InboxProcessor(
        storage=storage,
        actor_id=actor_id,
        private_key=private_key,
        key_id=key_id,
        allowed_instances=config.federation.allowed_instances,
        blocked_instances=blocked_instances,
        # Object-scoped Follows (thread subscriptions) target URLs under
        # the instance domain that share no path prefix with the actor, so
        # the domain is declared explicitly rather than relying on the
        # actor-derived default.
        local_base_urls=[f"https://{domain}"],
        strict_attribution=True,
        # FEP-044f: quoting is always allowed — incoming QuoteRequest
        # activities get an automatic Accept carrying a dereferenceable
        # QuoteAuthorization.
        auto_approve_quotes=True,
        follow_policy=_follow_policy_for_target,
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

    try:
        _retract_shared_inbox_follows(config, storage, activity, actor, username)
    except Exception as exc:
        logger.warning(
            "Failed to retract followers for deleted actor %s: %s",
            actor,
            exc,
        )

    try:
        _sync_remote_activities(config, activity)
    except Exception as exc:
        logger.warning(
            "Failed to sync remote activity row for incoming %s from %s: %s",
            activity.get("type", "activity"),
            actor,
            exc,
        )

    try:
        _sync_inbox_notifications(config, activity, username, storage=storage)
    except Exception as exc:
        logger.warning(
            "Failed to sync notifications for incoming %s from %s: %s",
            activity.get("type", "activity"),
            actor,
            exc,
        )

    logger.info(
        "Processed incoming %s from %s for actor %s",
        activity.get("type", "activity"),
        actor,
        actor_id,
    )
    return result


def _retract_shared_inbox_follows(
    config,
    storage,
    activity: dict,
    actor: str,
    username: Optional[str],
) -> None:
    """
    Wipe every follow record left by a remote actor that deleted itself.

    Pubby's ``InboxProcessor`` retracts ``(activity.actor, actor_id)`` on a
    self-``Delete``; for shared-inbox deliveries ``actor_id`` is the
    instance actor, so follows of local users would survive. A verified
    self-``Delete`` is authoritative — the processor binds the HTTP signer
    to ``activity.actor`` — so all of the actor's follow records on this
    instance are removed. Per-user deliveries keep pubby's scoping: only
    the addressed actor's follow record is retracted.

    The domain check mirrors the processor's ordering: activities from
    blocked or non-allowed instances return before signature verification,
    so their ``actor`` is unauthenticated and must not drive the wipe.
    """
    if username is not None or activity.get("type") != "Delete":
        return

    obj = activity.get("object")
    if isinstance(obj, dict):
        target = obj.get("id", "")
    elif isinstance(obj, str):
        target = obj
    else:
        return

    if target != actor or is_domain_blocked(extract_domain(actor), config):
        return

    storage.remove_follower(actor, "")
    logger.info("Removed all follower records for deleted remote actor %s", actor)


def _sync_remote_activities(config, activity: dict) -> None:
    """
    Materialize inbound remote replies and quotes into ``Activity`` rows.

    ``Create`` replies and quotes of known local activities become
    ``source_type="remote"`` rows — public ones outright, non-public ones
    when they address a local user — so they render as full cards and
    accept interactions; ``Update``/``Delete`` revise or retract them, and
    an ``Accept`` answering a ``QuoteRequest`` we sent stamps the issued
    authorization onto the quoting post. ``Accept``/``Reject`` activities
    answering a ``Follow`` we sent resolve the local follow row. An
    ``Announce`` from a followed actor materializes as a remote
    ``announce`` row — the boosted object is dereferenced and cached when
    unknown — and an ``Undo`` retracts it. Likes stay interaction-only.
    """
    if activity.get("type") not in ("Create", "Update", "Delete", "Accept", "Reject", "Announce", "Undo"):
        return

    from ..federation.incoming import sync_remote_activity

    init_db(config.database.url)

    async def _run() -> None:
        try:
            async with get_session() as session:
                await sync_remote_activity(session, activity=activity, config=config)
                await session.commit()
        finally:
            await dispose_and_reset()

    asyncio.run(_run())


def _sync_inbox_notifications(config, activity: dict, username: Optional[str], storage=None) -> None:
    """Sync user notifications for a processed inbox activity.

    Creates notifications for new interactions, retracts notifications
    whose source the incoming activity undoes or deletes (``Undo``,
    ``Delete``), and refreshes notification snapshots when an ``Update``
    revises a referenced object or the actor document.

    ``username`` is the local user whose inbox was addressed; for
    shared-inbox deliveries (``username=None``) the recipients are the
    local users the activity addresses or targets.
    """
    from ..federation.notifications import (
        create_inbox_notifications,
        resolve_inbox_recipients,
        retract_inbox_notifications,
        update_inbox_notifications,
    )
    from ..services.auth import get_user_by_username

    init_db(config.database.url)

    # The actor document is normally cached by the signature verification
    # that just ran; it provides the display name and avatar for user cards.
    actor_doc = None
    actor_url = activity.get("actor")
    if storage is not None and isinstance(actor_url, str):
        try:
            doc = storage.get_cached_actor(actor_url)
            if isinstance(doc, dict):
                actor_doc = doc
        except Exception as exc:
            logger.debug("Could not read cached actor %s: %s", actor_url, exc)

    async def _run() -> None:
        try:
            async with get_session() as session:
                if username is not None:
                    user = await get_user_by_username(session, username)
                    recipients = [user] if user is not None else []
                else:
                    recipients = await resolve_inbox_recipients(
                        session,
                        activity=activity,
                        instance_domain=config.federation.instance_domain,
                    )
                for recipient in recipients:
                    await create_inbox_notifications(
                        session,
                        activity=activity,
                        recipient=recipient,
                        actor_doc=actor_doc,
                        instance_domain=config.federation.instance_domain,
                        storage=storage,
                    )
                    await retract_inbox_notifications(
                        session,
                        activity=activity,
                        recipient=recipient,
                    )
                    await update_inbox_notifications(
                        session,
                        activity=activity,
                        recipient=recipient,
                        instance_domain=config.federation.instance_domain,
                    )
                await session.commit()
        finally:
            await dispose_and_reset()

    asyncio.run(_run())


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

    # Refresh the database instance policies so defederation applies on
    # top of the configured allow/block lists.
    _refresh_instance_policies(config.database.url)

    inbox_domain = extract_domain(inbox_url)
    if is_domain_blocked(inbox_domain, config):
        logger.info("Dropping delivery to blocked or non-allowed domain: %s", inbox_domain)
        return None

    private_key = load_private_key(private_key_pem)

    try:
        status_code = pubby_deliver_activity(
            activity,
            inbox_url,
            key_id=actor_key_id,
            private_key=private_key,
            timeout=15.0,
        )
    except requests.RequestException as exc:
        logger.warning("Delivery to %s failed (%s); retrying", inbox_url, type(exc).__name__)
        raise self.retry(countdown=30 * 2**self.request.retries, exc=exc)

    if status_code < 400:
        logger.info("Delivered activity to %s (status %s)", inbox_url, status_code)
        return {"status_code": status_code}

    if status_code >= 500 or status_code == 429:
        logger.warning("Delivery to %s returned %s; retrying", inbox_url, status_code)
        retry_exc = requests.HTTPError(f"Delivery failed with status {status_code}")
        raise self.retry(countdown=30 * 2**self.request.retries, exc=retry_exc)

    logger.warning("Delivery to %s returned %s; giving up", inbox_url, status_code)
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


@celery_app.task(name="songhive.tasks.federation.prune_remote_activities")
def prune_remote_activities(
    older_than_days: Optional[int] = None,
    dry_run: bool = False,
) -> dict:
    """
    Prune stale remote activities and their cached ``remote_objects`` rows.

    ``older_than_days`` overrides the configured
    ``federation.remote_activity_retention_days`` default. Scheduled runs
    (``federation.remote_activity_prune_schedule``) call it without
    arguments; the admin endpoint and CLI may pass an explicit threshold or
    ``dry_run``.
    """
    from ..services.remote_content import prune_stale_remote_activities

    config = load_config([])
    days = older_than_days or config.federation.remote_activity_retention_days
    logger.info("Pruning remote activities older than %s days (dry_run=%s)", days, dry_run)

    init_db(config.database.url)

    async def _run() -> dict:
        try:
            async with get_session() as session:
                result = await prune_stale_remote_activities(session, older_than_days=days, dry_run=dry_run)
                await session.commit()
                return result
        finally:
            await dispose_and_reset()

    return asyncio.run(_run())
