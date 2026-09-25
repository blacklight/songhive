"""
Shared ActivityPub federation helpers.

This module provides small, synchronous utilities for domain allow/block
checks, user actor key provisioning, and follower fan-out collection.  Route
and task callers are responsible for running blocking storage calls in a
thread when inside an async context.
"""

import logging
from dataclasses import dataclass
from datetime import timezone
from typing import Iterable, Optional
from urllib.parse import urlparse

from pubby import Follower, FollowRequest
from pubby import accept_follow_request as _pubby_accept_follow_request
from pubby import collect_inboxes
from pubby import reject_follow_request as _pubby_reject_follow_request
from pubby import resolve_actor_inbox as _pubby_resolve_actor_inbox
from pubby.crypto import (
    export_private_key_pem,
    export_public_key_pem,
    generate_rsa_keypair,
)
from pubby.moderation import extract_domain as _pubby_extract_domain
from pubby.moderation import is_domain_blocked as _pubby_is_domain_blocked
from pubby.moderation import normalize_domain as _pubby_normalize_domain
from sqlalchemy import and_, or_

from ..config import SonghiveConfig, get_default_user_agent
from ..federation import get_actor_url
from ..federation.storage import create_activitypub_storage
from ..models import User
from ._common import ilike_contains

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


# Database-backed instance moderation policies (``{normalized_domain:
# action}``), layered on top of the configured allow/block lists. The
# snapshot lives here so synchronous callers — the Celery delivery path,
# pubby inbox-resolution adapters — can apply it without an async
# session. ``services.moderation.load_instance_policies`` refreshes it;
# ``set_db_instance_policies`` installs it directly.
_db_instance_policies: dict[str, str] = {}


def set_db_instance_policies(policies: dict[str, str]) -> None:
    """Install the database-backed per-domain moderation policies."""
    global _db_instance_policies
    _db_instance_policies = {normalize_instance_domain(d): a for d, a in policies.items()}


def db_domain_policy(domain: str) -> Optional[str]:
    """Return the database-backed moderation action for ``domain``, if any."""
    if not domain:
        return None
    return _db_instance_policies.get(normalize_instance_domain(domain))


def is_domain_blocked(domain: str, config: SonghiveConfig) -> bool:
    """
    Return True when ``domain`` is blocked or not in the allow-list.

    - Empty allow-list means allow all (except explicit blocks).
    - Blocked domains take precedence over allowed domains.
    - Comparisons are case-insensitive and ignore URL schemes/paths.

    A database ``defederate`` policy counts as a block on top of the
    configured lists. Delegates to ``pubby.moderation.is_domain_blocked``
    with the configured allow/block lists otherwise.
    """
    if db_domain_policy(domain) == "defederate":
        return True
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


def get_object_follower_inboxes(object_ids: Iterable[str], database_url: str) -> list[str]:
    """
    Return unique follower inboxes for any of the given local objects.

    Remote actors may ``Follow`` a local object rather than an actor —
    e.g. Friendica sends ``Follow`` on a thread's root item for
    conversation subscriptions — and pubby stores those rows scoped to
    the object's id. ``object_ids`` typically mixes an activity's own
    object id with the ids of its in-reply-to ancestors, so thread
    subscribers are reached wherever they attached. Unassigned followers
    are never included: they follow the actor, not any object.
    """
    wanted = {oid for oid in object_ids if oid}
    if not wanted:
        return []
    storage = create_activitypub_storage(database_url)
    return collect_inboxes(storage.get_followers_of_targets(wanted))


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


def _requested_at_key(request: FollowRequest) -> float:
    """Return a sortable timestamp for a request's ``requested_at`` stamp."""
    requested_at = request.requested_at
    if requested_at is None:
        return 0.0
    if requested_at.tzinfo is None:
        requested_at = requested_at.replace(tzinfo=timezone.utc)
    return requested_at.timestamp()


def get_actor_follow_requests(storage, actor_url: str) -> list[FollowRequest]:
    """
    Return the pending follow requests of ``actor_url``, newest first.

    Reads pubby's follow-request storage, populated by incoming ``Follow``
    activities while the target user's approval policy is ``manual``.
    """
    requests = storage.get_follow_requests(actor_url)
    return sorted(requests, key=_requested_at_key, reverse=True)


def get_follow_request(storage, actor_url: str, requester_actor_id: str) -> Optional[FollowRequest]:
    """Return the pending request from ``requester_actor_id`` to ``actor_url``."""
    return storage.get_follow_request(requester_actor_id, actor_url)


def resolve_follow_request(
    storage,
    request: FollowRequest,
    user: User,
    *,
    accept: bool,
) -> dict:
    """
    Approve or decline a pending follow request and enqueue the reply.

    Delegates to pubby's ``accept_follow_request``/``reject_follow_request``
    — approval promotes the request to a stored follower — and routes the
    ``Accept``/``Reject`` through the ``deliver_activity`` Celery task so it
    is signed with the user's key and retried like any other delivery.
    Returns the delivered activity.
    """
    if not user.actor_url or not user.private_key_pem:
        raise ValueError("User has no federation actor credentials")

    from ..tasks.federation import deliver_activity

    actor_key_id = f"{user.actor_url}#main-key"

    def _deliver(inbox_url: str, activity: dict) -> None:
        deliver_activity.delay(activity, inbox_url, actor_key_id, user.private_key_pem)  # type: ignore

    if accept:
        return _pubby_accept_follow_request(
            storage,
            request,
            actor_id=user.actor_url,
            deliver=_deliver,
        )
    return _pubby_reject_follow_request(
        storage,
        request,
        actor_id=user.actor_url,
        deliver=_deliver,
    )


def _actor_doc_avatar_url(actor_doc: Optional[dict]) -> Optional[str]:
    """Extract an avatar URL from a cached ActivityPub actor document."""
    if not actor_doc:
        return None
    icon = actor_doc.get("icon")
    if isinstance(icon, dict):
        return icon.get("url")
    if isinstance(icon, list):
        for item in icon:
            if isinstance(item, dict):
                url = item.get("url")
                if url:
                    return url
    if isinstance(icon, str):
        return icon
    return None


def _actor_doc_display_name(actor_doc: Optional[dict]) -> Optional[str]:
    """Extract a display name from a cached ActivityPub actor document."""
    if not actor_doc:
        return None
    name = actor_doc.get("name")
    if isinstance(name, str) and name.strip():
        return name.strip()
    return None


def _actor_doc_username(actor_doc: Optional[dict], actor_url: str) -> str:
    """Return the actor's ``preferredUsername``, else derive one from the URL."""
    if actor_doc:
        preferred = actor_doc.get("preferredUsername")
        if isinstance(preferred, str) and preferred.strip():
            return preferred.strip()
    path = urlparse(actor_url).path.rstrip("/")
    if not path:
        return ""
    return path.rsplit("/", 1)[-1].lstrip("@")


def _actor_doc_profile_url(actor_doc: Optional[dict], actor_url: str) -> str:
    """Return the actor's profile page URL, falling back to the actor id."""
    url = (actor_doc or {}).get("url")
    if isinstance(url, str) and url.startswith(("http://", "https://")):
        return url
    if isinstance(url, list):
        for item in url:
            if isinstance(item, dict):
                href = item.get("href")
                if isinstance(href, str) and href.startswith(("http://", "https://")):
                    return href
    return actor_url


def actor_doc_handle(actor_doc: Optional[dict], actor_url: str) -> Optional[str]:
    """
    Return the actor's ``user@domain`` handle.

    ``preferredUsername`` is authoritative — actor ids may be opaque URIs
    (e.g. Mastodon's ``/ap/users/<id>`` scheme, where the path tail is an
    internal numeric id rather than the username) — so the URL tail is only
    a fallback for documents without one.
    """
    username = _actor_doc_username(actor_doc, actor_url)
    domain = extract_domain(actor_url)
    if not username or not domain:
        return None
    return f"{username}@{domain}"


def cached_actor_docs(storage, actor_urls: Iterable[str]) -> dict[str, dict]:
    """
    Map actor URLs to their cached actor documents.

    Reads pubby's actor cache, followers and follow-request tables — all
    carry the actor document — without any network fetch. URLs with no
    cached document are absent from the result.
    """
    wanted = {u for u in actor_urls if isinstance(u, str) and u.startswith(("http://", "https://"))}
    if not wanted:
        return {}
    # Only the DB storage adapter exposes the underlying models — file
    # storage and test doubles may not, in which case there is nothing to
    # scan beyond ``get_cached_actor`` (handled by the caller).
    models = [
        model
        for model in (
            getattr(storage, "actor_cache_model", None),
            getattr(storage, "follower_model", None),
            getattr(storage, "follow_request_model", None),
        )
        if model is not None
    ]
    if not models or not hasattr(storage, "session_factory"):
        return {}
    docs: dict[str, dict] = {}
    session = storage.session_factory()
    try:
        for model in models:
            for row in session.query(model).filter(model.actor_id.in_(wanted)).all():
                if row.actor_id in docs:
                    continue
                doc = row.actor_data if isinstance(row.actor_data, dict) else {}
                docs[row.actor_id] = doc
    finally:
        session.close()
    return docs


def cached_actor_handles(storage, actor_urls: Iterable[str]) -> dict[str, str]:
    """Map actor URLs to ``user@domain`` handles from cached actor documents."""
    docs = cached_actor_docs(storage, actor_urls)
    return {url: handle for url, doc in docs.items() if (handle := actor_doc_handle(doc, url))}


def cached_actor_handle(storage, actor_url: str) -> Optional[str]:
    """Return the cached ``user@domain`` handle for one actor URL, if any."""
    return cached_actor_handles(storage, [actor_url]).get(actor_url)


@dataclass(frozen=True)
class RemoteActorMatch:
    """A cached remote actor matched by :func:`search_remote_actors`."""

    actor_url: str
    username: str
    domain: str
    display_name: Optional[str] = None
    avatar_url: Optional[str] = None
    profile_url: Optional[str] = None
    follower: bool = False

    @property
    def handle(self) -> str:
        """The ``user@domain`` handle a mention resolves through."""
        return f"{self.username}@{self.domain}"


def _remote_actor_query(model, term: str, domain: str):
    """Build the match predicate for a cached-actor table (actor URL + doc)."""
    clauses = [
        or_(
            ilike_contains(model.actor_id, term),
            ilike_contains(model.actor_data["preferredUsername"].as_string(), term),
            ilike_contains(model.actor_data["name"].as_string(), term),
        )
    ]
    if domain:
        clauses.append(ilike_contains(model.actor_id, domain))
    return and_(*clauses)


def search_remote_actors(
    storage,
    query: str,
    config: SonghiveConfig,
) -> list[RemoteActorMatch]:
    """
    Return cached remote actors matching ``query``, best matches first.

    Searches pubby's ``federation_actor_cache`` and ``federation_followers``
    tables — both keyed by actor URL and carrying the actor document in a
    JSON ``actor_data`` column — matching on ``preferredUsername``, ``name``
    and the actor URL itself.  A ``user@domain`` query additionally narrows
    on the actor URL's domain.

    Actors on the local instance domain or on blocked domains are excluded.
    Followers are preferred on duplicates and rank ahead of plain cache
    entries at equal match quality.  This is a synchronous call — invoke it
    through ``asyncio.to_thread`` from async code.
    """
    query = (query or "").strip().lstrip("@")
    if not query or not config.federation.enabled:
        return []
    term, _, domain_part = query.partition("@")
    domain_part = domain_part.lower()
    local_domain = normalize_instance_domain(config.federation.instance_domain or "")

    candidates: dict[str, RemoteActorMatch] = {}
    session = storage.session_factory()
    try:
        # Followers first so a duplicate cache row cannot clobber the flag.
        for model, is_follower in (
            (storage.follower_model, True),
            (storage.actor_cache_model, False),
        ):
            for row in session.query(model).filter(_remote_actor_query(model, term, domain_part)).all():
                actor_id = row.actor_id
                if (
                    actor_id in candidates
                    or not isinstance(actor_id, str)
                    or not actor_id.startswith(("http://", "https://"))
                ):
                    continue
                domain = extract_domain(actor_id)
                if (
                    not domain
                    or domain == local_domain
                    or (domain_part and domain_part not in domain)
                    or is_domain_blocked(domain, config)
                ):
                    continue
                actor_doc = row.actor_data if isinstance(row.actor_data, dict) else {}
                username = _actor_doc_username(actor_doc, actor_id)
                if not username:
                    continue
                candidates[actor_id] = RemoteActorMatch(
                    actor_url=actor_id,
                    username=username,
                    domain=domain,
                    display_name=_actor_doc_display_name(actor_doc),
                    avatar_url=_actor_doc_avatar_url(actor_doc),
                    profile_url=_actor_doc_profile_url(actor_doc, actor_id),
                    follower=is_follower,
                )
    finally:
        session.close()

    def _rank(match: RemoteActorMatch) -> tuple:
        username = match.username.lower()
        display = (match.display_name or "").lower()
        needle = term.lower()
        if needle in (username, display):
            score = 0
        elif username.startswith(needle) or display.startswith(needle):
            score = 1
        else:
            score = 2
        return score, not match.follower, username, match.domain

    return sorted(candidates.values(), key=_rank)


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
    if db_domain_policy(extract_domain(actor_url)) == "defederate":
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
