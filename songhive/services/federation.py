"""
Shared ActivityPub federation helpers.

This module provides small, synchronous utilities for domain allow/block
checks, user actor key provisioning, and follower fan-out collection.  Route
and task callers are responsible for running blocking storage calls in a
thread when inside an async context.
"""

import logging
from typing import Optional
from urllib.parse import urlparse

from pubby.crypto import export_private_key_pem, export_public_key_pem, generate_rsa_keypair

from ..config.schema import SonghiveConfig
from ..federation._common import get_actor_url
from ..federation.storage import create_activitypub_storage
from ..models._enums import Visibility
from ..models.user import User

logger = logging.getLogger(__name__)


def normalize_instance_domain(domain: str) -> str:
    """
    Normalize a domain for allow/block comparisons.

    Strips URL schemes and paths, then lower-cases the hostname.
    """
    if not domain:
        return ""
    value = domain.strip().lower()
    if value.startswith(("http://", "https://")):
        parsed = urlparse(value)
        host = parsed.hostname or ""
    else:
        host = value.split("/")[0]
    return host.strip()


def extract_domain(url_or_actor: str) -> str:
    """Extract a normalized domain from an actor URL or other HTTP(S) value."""
    if not url_or_actor:
        return ""
    return normalize_instance_domain(url_or_actor)


def is_domain_blocked(domain: str, config: SonghiveConfig) -> bool:
    """
    Return True when ``domain`` is blocked or not in the allow-list.

    - Empty allow-list means allow all (except explicit blocks).
    - Blocked domains take precedence over allowed domains.
    - Comparisons are case-insensitive and ignore URL schemes/paths.
    """
    normalized = normalize_instance_domain(domain)
    if not normalized:
        return False

    allowed = {normalize_instance_domain(d) for d in config.federation.allowed_instances}
    blocked = {normalize_instance_domain(d) for d in config.federation.blocked_instances}

    if normalized in blocked:
        return True
    if allowed and normalized not in allowed:
        return True
    return False


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
    followers = storage.get_followers(actor_id=actor_url)

    seen: set[str] = set()
    inboxes: list[str] = []
    for follower in followers:
        inbox = follower.shared_inbox or follower.inbox
        if not inbox:
            continue
        if inbox in seen:
            continue
        seen.add(inbox)
        inboxes.append(inbox)

    return inboxes


def publish_track_activity(
    track,
    artist,
    user: User,
    config: SonghiveConfig,
    ap_object_id: Optional[str] = None,
) -> int:
    """
    Publish a ``Create(Audio)`` activity to the user's follower inboxes.

    Returns the number of remote inboxes enqueued. The function no-ops when
    federation is disabled, the track is not public, the user has no actor
    credentials, or the audio object cannot be serialized.

    ``ap_object_id`` is the ActivityPub object id that will appear on the
    ``Audio`` object. Callers should persist it on ``track.federation_object_id``
    so that a later ``Delete(Tombstone)`` can reference the same id.
    """
    if not config.federation.enabled or not config.federation.instance_domain:
        return 0
    if not track or track.visibility != Visibility.PUBLIC.value:
        return 0
    if not user or not user.actor_url or not user.private_key_pem:
        return 0
    if not artist:
        return 0

    from ..federation.activities import create_audio_activity
    from ..tasks.federation import deliver_activity

    object_id = ap_object_id or track.federation_object_id
    if object_id and not object_id.startswith(("http://", "https://")):
        object_id = f"{user.actor_url}/objects/{object_id}"

    activity = create_audio_activity(
        actor_url=user.actor_url,
        track=track,
        artist=artist,
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
