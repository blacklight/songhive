"""
Shared ActivityPub federation helpers.

This module provides small, synchronous utilities for domain allow/block
checks, user actor key provisioning, and follower fan-out collection.  Route
and task callers are responsible for running blocking storage calls in a
thread when inside an async context.
"""

import logging
from datetime import timezone
from typing import Optional

from pubby import Follower, collect_inboxes
from pubby import resolve_actor_inbox as _pubby_resolve_actor_inbox
from pubby.crypto import (
    export_private_key_pem,
    export_public_key_pem,
    generate_rsa_keypair,
)
from pubby.moderation import extract_domain as _pubby_extract_domain
from pubby.moderation import is_domain_blocked as _pubby_is_domain_blocked
from pubby.moderation import normalize_domain as _pubby_normalize_domain

from ..config import SonghiveConfig, get_default_user_agent
from ..federation import get_actor_url
from ..federation.storage import create_activitypub_storage
from ..models import User

logger = logging.getLogger(__name__)


def normalize_instance_domain(domain: str) -> str:
    """
    Normalize a domain for allow/block comparisons.

    Strips URL schemes, paths, ports and credentials, then lower-cases the
    hostname. Delegates to ``pubby.moderation``.
    """
    return _pubby_normalize_domain(domain)


def extract_domain(url_or_actor: str) -> str:
    """Extract a normalized domain from an actor URL or other HTTP(S) value."""
    return _pubby_extract_domain(url_or_actor)


def is_domain_blocked(domain: str, config: SonghiveConfig) -> bool:
    """
    Return True when ``domain`` is blocked or not in the allow-list.

    - Empty allow-list means allow all (except explicit blocks).
    - Blocked domains take precedence over allowed domains.
    - Comparisons are case-insensitive and ignore URL schemes/paths.

    Delegates to ``pubby.moderation.is_domain_blocked`` with the configured
    allow/block lists.
    """
    return _pubby_is_domain_blocked(
        domain,
        allowed=config.federation.allowed_instances,
        blocked=config.federation.blocked_instances,
    )


def is_domain_allowed(domain: str, config: SonghiveConfig) -> bool:
    """Return True when ``domain`` is not blocked and passes the allow-list."""
    return not is_domain_blocked(domain, config)


def provision_federation_keys(user: User, domain: str) -> bool:
    """
    Ensure a user has a complete ActivityPub actor URL and keypair.

    Returns ``True`` only when the user is mutated.  Existing complete
    credentials are left untouched.  If any key field is missing, a fresh
    matching pair is generated and both key fields are replaced.
    """
    complete = bool(user.actor_url and user.private_key_pem and user.public_key_pem)
    if complete:
        return False

    if not user.actor_url:
        user.actor_url = get_actor_url(domain, user.username)

    if not (user.private_key_pem and user.public_key_pem):
        private_key, public_key = generate_rsa_keypair()
        user.private_key_pem = export_private_key_pem(private_key)
        user.public_key_pem = export_public_key_pem(public_key)

    return True


def ensure_user_actor(user: User, config: SonghiveConfig) -> bool:
    """Provision federation keys for ``user`` when federation is configured."""
    if not config.federation.enabled or not config.federation.instance_domain:
        return False
    return provision_federation_keys(user, config.federation.instance_domain)


def get_follower_inboxes(actor_url: str, database_url: str) -> list[str]:
    """
    Return unique follower inboxes for ``actor_url``.

    Reads pubby's follower storage, prefers shared inboxes, and deduplicates.
    """
    storage = create_activitypub_storage(database_url)
    return collect_inboxes(storage.get_followers(actor_id=actor_url))


def _followed_at_key(follower: Follower) -> float:
    """Return a sortable timestamp for a follower's ``followed_at`` stamp."""
    followed_at = follower.followed_at
    if followed_at is None:
        return 0.0
    if followed_at.tzinfo is None:
        followed_at = followed_at.replace(tzinfo=timezone.utc)
    return followed_at.timestamp()


def get_actor_followers(storage, actor_url: str) -> list[Follower]:
    """
    Return the stored followers of ``actor_url``, newest first.

    Reads pubby's follower storage; rows with an empty ``target_actor_id``
    are included by ``get_followers`` for backward compatibility with
    single-actor deployments.
    """
    followers = storage.get_followers(actor_id=actor_url)
    return sorted(followers, key=_followed_at_key, reverse=True)


def count_followers_by_actor(storage) -> dict[str, int]:
    """
    Return per-target-actor follower counts.

    Unassigned followers (empty ``target_actor_id``, from single-actor
    deployments) are tallied under the ``""`` key.
    """
    counts: dict[str, int] = {}
    for follower in storage.get_followers():
        key = follower.target_actor_id or ""
        counts[key] = counts.get(key, 0) + 1
    return counts


def resolve_actor_inbox(
    actor_url: str,
    config: SonghiveConfig,
    *,
    key_id: Optional[str] = None,
    private_key_pem: Optional[str] = None,
    timeout: float = 10.0,
) -> Optional[str]:
    """
    Resolve a remote actor's inbox URL.

    Consults the ``federation_actor_cache`` table first (via pubby's
    storage); on a miss the actor document is fetched over HTTP(S) — signed
    when a key is available — and cached.  Returns ``None`` for non-HTTP(S)
    actor ids, blocked or non-allowed domains, and resolution failures.

    This is a thin Songhive adapter around ``pubby.resolve_actor_inbox``:
    it creates the storage backend from Songhive config and passes the
    configured allow/block lists and ``User-Agent``.

    This is a synchronous, ``requests``-based call: async callers should
    invoke it through ``asyncio.to_thread``.
    """
    if not actor_url.startswith(("http://", "https://")):
        return None
    storage = create_activitypub_storage(config.database.url)
    return _pubby_resolve_actor_inbox(
        actor_url,
        storage,
        private_key=private_key_pem,
        key_id=key_id,
        allowed_instances=config.federation.allowed_instances,
        blocked_instances=config.federation.blocked_instances,
        user_agent=get_default_user_agent(),
        timeout=timeout,
    )


def unpublish_track_activity(
    track,
    artist,
    user: User,
    config: SonghiveConfig,
    ap_object_id: Optional[str] = None,
) -> int:
    """
    Publish a ``Delete(Tombstone)`` activity for a track to follower inboxes.

    Returns the number of remote inboxes enqueued. The function no-ops when
    federation is disabled or the user has no actor credentials.

    ``ap_object_id`` should match the id used in the original ``Create(Audio)``
    activity so the remote ``Tombstone`` targets the right object. When omitted,
    ``track.federation_object_id`` is used as a fallback.
    """
    if not config.federation.enabled or not config.federation.instance_domain:
        return 0
    if not track or not user or not user.actor_url or not user.private_key_pem:
        return 0
    if artist is None:
        return 0

    from ..federation.activities import create_delete_activity
    from ..tasks.federation import deliver_activity

    object_id = ap_object_id or track.federation_object_id
    if object_id and not object_id.startswith(("http://", "https://")):
        object_id = f"{user.actor_url}/objects/{object_id}"

    activity = create_delete_activity(
        actor_url=user.actor_url,
        track=track,
        domain=config.federation.instance_domain,
        ap_object_id=object_id,
    )
    if not activity:
        return 0

    inboxes = get_follower_inboxes(user.actor_url, config.database.url)
    actor_key_id = f"{user.actor_url}#main-key"
    for inbox in inboxes:
        deliver_activity.delay(activity, inbox, actor_key_id, user.private_key_pem)  # type: ignore

    return len(inboxes)


def publish_actor_update(
    user: User,
    config: SonghiveConfig,
    actor_document: Optional[dict] = None,
) -> int:
    """
    Publish an ``Update`` activity for the user's actor to follower inboxes.

    This pushes profile changes (display name, bio, avatar, links) to remote
    instances so they can refresh their cached copy of the actor document
    instead of waiting for a re-fetch.

    Returns the number of remote inboxes enqueued. The function no-ops when
    federation is disabled or the user has no actor credentials.

    ``actor_document`` may be supplied to reuse an already-serialized actor
    document; otherwise it is rebuilt from the current user record.
    """
    if (
        not config.federation.enabled
        or not config.federation.instance_domain
        or not user
        or not user.actor_url
        or not user.private_key_pem
    ):
        logger.debug("Skipping ActivityPub actor update")
        return 0

    from ..federation.activities import create_update_actor_activity
    from ..tasks.federation import deliver_activity

    document = actor_document
    if document is None:
        from ..federation.actors import user_to_actor_document

        document = user_to_actor_document(user, config.federation.instance_domain)

    activity = create_update_actor_activity(user.actor_url, document)
    inboxes = get_follower_inboxes(user.actor_url, config.database.url)
    actor_key_id = f"{user.actor_url}#main-key"
    for inbox in inboxes:
        try:
            deliver_activity.delay(activity, inbox, actor_key_id, user.private_key_pem)  # type: ignore
        except Exception as e:
            logger.warning("Cannot send update activity to inbox %s: %s: %s", inbox, type(e), e)

    return len(inboxes)
