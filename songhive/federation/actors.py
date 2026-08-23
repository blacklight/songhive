"""
Actor management for federation.

Each Songhive user has a corresponding ActivityPub actor.  When a user updates
their local profile the matching actor document is refreshed in the pubby actor
storage so remote instances can see the latest display name, bio, avatar and
profile links.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from pubby.storage.adapters.db import DbActivityPubStorage

from ..config.schema import SonghiveConfig
from ..models.user import User
from ..services.federation import ensure_user_actor
from ._common import get_actor_url, get_inbox_url, get_outbox_url
from .storage import create_activitypub_storage

logger = logging.getLogger(__name__)

# Mapping of database URL to a shared pubby storage instance.  Storage is
# created lazily and cached for the lifetime of the process so that profile
# updates can reuse the same backend across calls.  Instances are keyed by
# database URL; tests that need a fresh backend should reset this cache.
_federation_storage_cache: dict[str, DbActivityPubStorage] = {}


def _build_attachment(user: User) -> Optional[list[dict[str, Any]]]:
    """Build an ActivityPub attachment list from the user's profile links."""
    links = getattr(user, "links", None) or []
    if not links:
        return None
    return [
        {
            "type": "PropertyValue",
            "name": link.name,
            "value": link.url,
        }
        for link in links
    ]


def user_to_actor_document(user: User, domain: str) -> dict:
    """
    Convert a User model to an ActivityPub actor document.

    The document includes profile fields (display name, bio, avatar and links)
    that are expected to stay in sync with the local profile.
    """
    actor_url = get_actor_url(domain, user.username)
    document: dict[str, Any] = {
        "@context": [
            "https://www.w3.org/ns/activitystreams",
            "https://w3id.org/security/v1",
        ],
        "id": actor_url,
        "url": actor_url,
        "type": "Person",
        "preferredUsername": user.username,
        "name": user.display_name or user.username,
        "summary": user.bio or "",
        "inbox": get_inbox_url(domain, user.username),
        "outbox": get_outbox_url(domain, user.username),
        "followers": f"{actor_url}/followers",
        "following": f"{actor_url}/following",
        "publicKey": {
            "id": f"{actor_url}#main-key",
            "owner": actor_url,
            "publicKeyPem": user.public_key_pem or "",
        },
    }

    if user.avatar_url and user.avatar_url.startswith(("https://", "http://")):
        document["icon"] = {"type": "Image", "url": user.avatar_url}

    attachment = _build_attachment(user)
    if attachment:
        document["attachment"] = attachment

    return document


def get_federation_storage(database_url: str) -> DbActivityPubStorage:
    """Return a cached pubby storage instance for the configured database URL."""
    if database_url not in _federation_storage_cache:
        _federation_storage_cache[database_url] = create_activitypub_storage(database_url)
    return _federation_storage_cache[database_url]


async def sync_user_actor(user: User, config: SonghiveConfig) -> bool:
    """
    Refresh the cached ActivityPub actor document for a user.

    Returns ``True`` when the actor document was refreshed, ``False`` when
    federation is disabled or the sync could not be performed.  Failures are
    logged rather than raised so that profile updates are not blocked by
    federation storage problems.
    """
    if not config.federation.enabled or not config.federation.instance_domain:
        return False

    ensure_user_actor(user, config)

    try:
        storage = await asyncio.to_thread(get_federation_storage, config.database.url)
    except Exception:
        # Broad catch is intentional: actor sync is a non-critical federation
        # operation and must not block profile updates.  BaseException
        # subclasses (KeyboardInterrupt, SystemExit) are not caught.
        logger.exception("Failed to initialize federation storage for actor sync")
        return False

    actor_doc = user_to_actor_document(user, config.federation.instance_domain)

    try:
        await asyncio.to_thread(
            storage.cache_remote_actor,
            actor_doc["id"],
            actor_doc,
            datetime.now(timezone.utc),
        )
    except Exception:
        # Broad catch is intentional: actor sync is a non-critical federation
        # operation and must not block profile updates.  BaseException
        # subclasses (KeyboardInterrupt, SystemExit) are not caught.
        logger.exception("Failed to cache actor document for %s", user.username)
        return False

    return True
