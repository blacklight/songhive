"""
Remote content lookup, dereference, and cache services.

This module implements explicit, bounded remote discovery: a user-supplied
handle or URL is parsed (:func:`parse_remote_target`), gated by the
``remote_search_access`` policy (:func:`check_remote_access`) and the
federation domain allow/block rules (:func:`remote_domain_allowed`), then
fetched through the SSRF-guarded fetcher in ``songhive.federation.fetch``.

Remote actors are cached in pubby's ``federation_actor_cache`` table;
remote objects and resources are cached in the ``remote_objects`` table and
materialized activities attach to them via ``entity_type="remote"``.
"""

import asyncio
import hashlib
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urlparse

from fastapi import HTTPException
from pubby import AttributionMismatch, validate_attribution
from pubby.audience import is_public
from pubby.client import extract_actor_inbox
from pubby.quotes import extract_quote_target
from sqlalchemy import delete, exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config.schema import SonghiveConfig
from ..federation.fetch import FetchError, FetchNotFound, FetchResult, guarded_fetch
from ..models.activity import Activity, ActivityMention
from ..models.collection_item import CollectionItem
from ..models.favorite import Favorite
from ..models.library_track import LibraryTrack
from ..models.remote_object import RemoteObject
from ..models.track import Track
from ..models.user import User
from . import federation as federation_service
from ._common import ilike_contains

logger = logging.getLogger(__name__)

# How fresh a cached actor/object must be to be served without a refetch.
ACTOR_CACHE_TTL_SECONDS = 24 * 3600
# Direct-URL views re-fetch objects older than this to confirm remote state.
OBJECT_FRESHNESS_SECONDS = 60

_HANDLE_RE = re.compile(r"^@?([A-Za-z0-9_.~-]+)@([A-Za-z0-9.\-]+(?::[0-9]+)?)$")
_ACTOR_PATH_RE = re.compile(r"^/(?:users|u|actors|federation/actors)/(?P<name>[^/?#]+)/?$|^/@(?P<name2>[^/?#]+)/?$")
_SONGHIVE_ACTIVITY_RES = (
    re.compile(r"^/users/[^/?#]+/objects/(?P<object_id>[^/?#]+)/?$"),
    re.compile(r"^/activities/(?P<activity_id>[^/?#]+)/?$"),
    re.compile(r"^/users/[^/?#]+/statuses/(?P<status_id>[^/?#]+)/?$"),
)
_SONGHIVE_RESOURCE_KINDS = ("tracks", "albums", "artists", "playlists", "libraries")
_SONGHIVE_RESOURCE_RE = re.compile(
    r"^/(?:api/v1/)?(?P<kind>tracks|albums|artists|playlists|libraries)/(?P<rid>[^/?#]+)/?$"
)
# Funkwhale exposes music entities under ``/federation/music/{kind}/{uuid}``.
_FUNKWHALE_RESOURCE_RE = re.compile(
    r"^/federation/music/(?P<kind>tracks|albums|artists|libraries|uploads)/(?P<rid>[^/?#]+)/?$"
)
# Funkwhale's library frontend URL (``/library/{uuid}``) serves plain HTML —
# it is not dereferenceable, but it shares its uuid with the federation
# document at ``/federation/music/libraries/{uuid}``, so it can be rewritten.
_FUNKWHALE_LIBRARY_PAGE_RE = re.compile(
    r"^/library/(?P<rid>[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})/?$"
)
_ACTOR_TYPES = frozenset({"Person", "Service", "Group", "Application", "Organization"})
_ACTIVITY_WRAPPER_TYPES = frozenset({"Create", "Announce", "Update", "Delete", "Like"})
_TOMBSTONE_TYPES = frozenset({"Tombstone", "Delete"})
_CONTENT_OBJECT_TYPES = frozenset(
    {"Note", "Article", "Page", "Comment", "Event", "Video", "Audio", "Image", "Document"}
)

# Mapping of remote resource ``resource_type`` values to URL path plurals.
RESOURCE_KIND_PLURALS = {
    "track": "tracks",
    "album": "albums",
    "artist": "artists",
    "playlist": "playlists",
    "library": "libraries",
    "profile": "actors",
}
_PLURAL_TO_RESOURCE_KIND = {v: k for k, v in RESOURCE_KIND_PLURALS.items()}
# Funkwhale path plurals that don't map 1:1 — an ``uploads`` URL dereferences
# to an ``Audio`` document, which is a track resource.
_FUNKWHALE_PLURAL_TO_KIND = {**_PLURAL_TO_RESOURCE_KIND, "uploads": "track"}

# ActivityStreams music-entity types in the federated music dialect shared
# by Funkwhale and Songhive. ``Audio`` is the playable upload; the others
# are the catalog entities it embeds.
_MUSIC_RESOURCE_TYPES = {
    "Audio": "track",
    "Track": "track",
    "Album": "album",
    "Artist": "artist",
    "Library": "library",
}
_MUSIC_OBJECT_TYPES = frozenset(_MUSIC_RESOURCE_TYPES)
# Collection pages listing music items (Funkwhale library ``?page=N``).
_COLLECTION_PAGE_TYPES = frozenset({"CollectionPage", "OrderedCollectionPage"})

# Bounded embedded-object caching: a library page may carry up to this many
# items and each music doc embeds at most a handful of nested entities.
_MAX_EMBEDDED_OBJECTS = 120


async def _refresh_instance_policies(session: AsyncSession) -> None:
    """
    Reload the database instance policies into the sync snapshot.

    The domain guards below run synchronous pubby checks; refreshing the
    snapshot here keeps them in step with the admin-set defederation and
    followers-only rows.
    """
    from . import moderation as moderation_service

    await moderation_service.load_instance_policies(session)


class RemoteTargetKind(str, Enum):
    """Classification of a raw remote lookup input."""

    HANDLE = "handle"
    ACTOR_URL = "actor_url"
    OBJECT_URL = "object_url"
    SONGHIVE_ACTIVITY_URL = "songhive_activity_url"
    SONGHIVE_RESOURCE_URL = "songhive_resource_url"
    LOCAL = "local"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True)
class RemoteTarget:
    """A parsed remote lookup input."""

    kind: RemoteTargetKind
    raw: str
    url: Optional[str] = None
    username: Optional[str] = None
    domain: Optional[str] = None
    resource_kind: Optional[str] = None
    resource_id: Optional[str] = None

    @property
    def handle(self) -> Optional[str]:
        """Return the ``user@domain`` handle for handle/actor inputs."""
        if self.username and self.domain:
            return f"{self.username}@{self.domain}"
        return None


def _local_domain(config: SonghiveConfig) -> str:
    """Return the normalized local instance domain, or ``""`` when unset."""
    return federation_service.normalize_instance_domain(config.federation.instance_domain or "")


def parse_remote_target(raw: str, config: Optional[SonghiveConfig] = None) -> RemoteTarget:
    """
    Classify a raw search/lookup input into a typed ``RemoteTarget``.

    Recognized forms: ``@user@domain`` handles, actor/profile URLs
    (``https://domain/users/name``, ``https://domain/@name``), Songhive
    activity URLs (``/users/{u}/objects/{id}``, ``/activities/{id}``,
    ``/users/{u}/statuses/{id}``), Songhive resource URLs
    (``/{tracks,albums,artists,playlists,libraries}/{id}``, optionally under
    ``/api/v1/``), generic http(s) object URLs, local-domain inputs
    (``local``) and everything else (``unsupported``).

    URL shapes are only hints — actual typing happens after the document is
    fetched. Never raises: malformed input classifies as ``unsupported``.
    """
    text = (raw or "").strip()
    if not text:
        return RemoteTarget(kind=RemoteTargetKind.UNSUPPORTED, raw=raw)

    match = _HANDLE_RE.match(text)
    if match:
        username, domain = match.group(1), match.group(2).lower()
        local = _local_domain(config) if config is not None else ""
        host = domain.split(":", 1)[0]
        if local and host == local:
            return RemoteTarget(
                kind=RemoteTargetKind.LOCAL,
                raw=raw,
                username=username,
                domain=domain,
            )
        return RemoteTarget(
            kind=RemoteTargetKind.HANDLE,
            raw=raw,
            username=username,
            domain=domain,
        )

    parsed = urlparse(text)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return RemoteTarget(kind=RemoteTargetKind.UNSUPPORTED, raw=raw)

    domain = parsed.netloc.lower()
    local = _local_domain(config) if config is not None else ""
    if local and parsed.hostname.lower() == local:
        return RemoteTarget(kind=RemoteTargetKind.LOCAL, raw=raw, url=text, domain=domain)

    path = parsed.path or "/"
    for pattern in _SONGHIVE_ACTIVITY_RES:
        if pattern.match(path):
            return RemoteTarget(
                kind=RemoteTargetKind.SONGHIVE_ACTIVITY_URL,
                raw=raw,
                url=text,
                domain=domain,
            )

    resource_match = _SONGHIVE_RESOURCE_RE.match(path)
    if resource_match:
        plural = resource_match.group("kind")
        return RemoteTarget(
            kind=RemoteTargetKind.SONGHIVE_RESOURCE_URL,
            raw=raw,
            url=text,
            domain=domain,
            resource_kind=_PLURAL_TO_RESOURCE_KIND.get(plural),
            resource_id=resource_match.group("rid"),
        )

    fw_match = _FUNKWHALE_RESOURCE_RE.match(path)
    if fw_match:
        plural = fw_match.group("kind")
        return RemoteTarget(
            kind=RemoteTargetKind.SONGHIVE_RESOURCE_URL,
            raw=raw,
            url=text,
            domain=domain,
            resource_kind=_FUNKWHALE_PLURAL_TO_KIND.get(plural),
            resource_id=fw_match.group("rid"),
        )

    fw_library_match = _FUNKWHALE_LIBRARY_PAGE_RE.match(path)
    if fw_library_match:
        rid = fw_library_match.group("rid")
        return RemoteTarget(
            kind=RemoteTargetKind.SONGHIVE_RESOURCE_URL,
            raw=raw,
            url=parsed._replace(path=f"/federation/music/libraries/{rid}", query="").geturl(),
            domain=domain,
            resource_kind="library",
            resource_id=rid,
        )

    actor_match = _ACTOR_PATH_RE.match(path)
    if actor_match:
        name = actor_match.group("name") or actor_match.group("name2")
        return RemoteTarget(
            kind=RemoteTargetKind.ACTOR_URL,
            raw=raw,
            url=text,
            username=name.lstrip("@") if name else None,
            domain=domain,
        )

    return RemoteTarget(kind=RemoteTargetKind.OBJECT_URL, raw=raw, url=text, domain=domain)


_LOCAL_PERMALINK_RE = re.compile(r"^/users/(?P<username>[^/?#]+)/(?:objects|statuses)/(?P<object_id>[^/?#]+)/?$")


async def _resolve_local_object_page(db: AsyncSession, object_id: str) -> Optional[str]:
    """Resolve a local object permalink id to its SPA page."""
    track_id = (await db.execute(select(Track.id).where(Track.federation_object_id == object_id))).scalar_one_or_none()
    if track_id is not None:
        return f"/tracks/{track_id}"
    activity_id = (
        await db.execute(
            select(Activity.id).where(or_(Activity.local_object_id == object_id, Activity.source_id == object_id))
        )
    ).scalar_one_or_none()
    if activity_id is not None:
        return f"/activities/{activity_id}"
    return None


async def resolve_local_target(db: AsyncSession, target: RemoteTarget) -> str:
    """
    Map a local-domain lookup target to its internal SPA route.

    ``@user@local`` handles resolve to ``/@user``; Songhive-shaped paths map
    to their SPA counterparts (``/api/v1`` prefixes are dropped by the
    resource pattern); object permalinks
    (``/users/{u}/objects|statuses/{id}``) resolve through the local
    track/activity tables. Unrecognized paths are returned unchanged (with
    the query string) so the SPA can render its own not-found page.
    """
    if target.username and not target.url:
        return f"/@{target.username}"

    parsed = urlparse(target.url or target.raw)
    path = parsed.path or "/"

    resource_match = _SONGHIVE_RESOURCE_RE.match(path)
    if resource_match:
        return f"/{resource_match.group('kind')}/{resource_match.group('rid')}"

    actor_match = _ACTOR_PATH_RE.match(path)
    if actor_match:
        name = actor_match.group("name") or actor_match.group("name2")
        if name:
            return f"/@{name.lstrip('@')}"

    permalink_match = _LOCAL_PERMALINK_RE.match(path)
    if permalink_match:
        resolved = await _resolve_local_object_page(db, permalink_match.group("object_id"))
        if resolved:
            return resolved

    if parsed.query:
        return f"{path}?{parsed.query}"
    return path


class RemoteAccessDenied(HTTPException):
    """Remote lookup is denied by the ``remote_search_access`` policy."""


def remote_search_policy(config: SonghiveConfig) -> str:
    """Return the effective ``remote_search_access`` policy value."""
    return config.federation.remote_search_access


def remote_lookup_allowed(user: Optional[User], config: SonghiveConfig) -> bool:
    """Return whether ``user`` may perform explicit remote lookups."""
    policy = remote_search_policy(config)
    if policy == "disabled":
        return False
    if policy == "authenticated":
        return user is not None
    return True


def check_remote_access(user: Optional[User], setting_value: str) -> None:
    """
    Enforce the remote lookup policy for ``user``.

    Raises ``RemoteAccessDenied``: 403 under ``disabled``, 401 for anonymous
    callers under ``authenticated``. ``public`` allows everyone.
    """
    if setting_value == "disabled":
        raise RemoteAccessDenied(status_code=403, detail="Remote lookup is disabled")
    if setting_value == "authenticated" and user is None:
        raise RemoteAccessDenied(status_code=401, detail="Authentication required for remote lookup")


def remote_domain_allowed(url_or_domain: str, config: SonghiveConfig) -> bool:
    """
    Return whether a remote URL or domain passes federation domain rules.

    Normalizes the host, rejects the local instance domain (local targets
    must go through local resolution), then applies the configured
    allow/block lists — blocked domains always lose.
    """
    domain = federation_service.normalize_instance_domain(url_or_domain)
    if not domain:
        return False
    local = _local_domain(config)
    if local and domain == local:
        return False
    return federation_service.is_domain_allowed(domain, config)


def require_remote_domain(url_or_domain: str, config: SonghiveConfig) -> str:
    """Return the normalized remote domain or raise ``FetchError`` (403)."""
    domain = federation_service.normalize_instance_domain(url_or_domain)
    if not domain or not remote_domain_allowed(domain, config):
        raise FetchError(f"Domain is not allowed for remote lookup: {domain or url_or_domain}", status_code=403)
    return domain


def _domain_guard(config: SonghiveConfig):
    """Return a per-hop URL check applying domain moderation during fetch."""

    def _check(url: str) -> None:
        require_remote_domain(url, config)

    return _check


async def fetch_ap_document(url: str, config: SonghiveConfig) -> FetchResult:
    """
    Fetch an ActivityPub document under policy+domain+SSRF guards.

    Domain moderation is re-applied on every redirect hop through
    ``check_url``. Synchronous ``requests`` work runs in a thread.
    """
    require_remote_domain(url, config)
    return await asyncio.to_thread(
        guarded_fetch,
        url,
        check_url=_domain_guard(config),
        timeout=config.federation.fetch_timeout_seconds,
    )


def content_hash(payload: Any) -> str:
    """Return a stable SHA-256 hash of a fetched remote document."""
    import json

    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Remote actor lookup
# ---------------------------------------------------------------------------

#: Marker stored inside cached actor documents when the remote confirms the
#: actor is gone (404/410) — pubby's cache has no tombstone column.
_UNAVAILABLE_FLAG = "songhive:unavailable"


@dataclass
class RemoteActorResult:
    """A normalized remote actor, served from or stored in the actor cache."""

    actor_url: str
    username: str
    domain: str
    display_name: Optional[str] = None
    summary: Optional[str] = None
    avatar_url: Optional[str] = None
    header_url: Optional[str] = None
    profile_url: Optional[str] = None
    inbox_url: Optional[str] = None
    fetched_at: Optional[datetime] = None
    unavailable: bool = False
    cached: bool = False

    @property
    def handle(self) -> str:
        """The ``user@domain`` handle used by internal URLs."""
        return f"{self.username}@{self.domain}"


def _actor_doc_image(actor_doc: dict, key: str) -> Optional[str]:
    """Extract an image URL (``icon``/``image``) from an actor document."""
    value = actor_doc.get(key)
    if isinstance(value, dict):
        url = value.get("url")
        return url if isinstance(url, str) else None
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict) and isinstance(item.get("url"), str):
                return item["url"]
            if isinstance(item, str) and item.startswith(("http://", "https://")):
                return item
    if isinstance(value, str) and value.startswith(("http://", "https://")):
        return value
    return None


def _normalize_actor(actor_url: str, actor_doc: dict, *, cached: bool, fetched_at=None) -> RemoteActorResult:
    """Build a ``RemoteActorResult`` from a cached or freshly fetched actor doc."""
    domain = federation_service.extract_domain(actor_url)
    username = federation_service._actor_doc_username(actor_doc, actor_url)
    return RemoteActorResult(
        actor_url=actor_url,
        username=username,
        domain=domain,
        display_name=federation_service._actor_doc_display_name(actor_doc),
        summary=actor_doc.get("summary") if isinstance(actor_doc.get("summary"), str) else None,
        avatar_url=federation_service._actor_doc_avatar_url(actor_doc),
        header_url=_actor_doc_image(actor_doc, "image"),
        profile_url=federation_service._actor_doc_profile_url(actor_doc, actor_url),
        inbox_url=extract_actor_inbox(actor_doc),
        fetched_at=fetched_at,
        unavailable=bool(actor_doc.get(_UNAVAILABLE_FLAG)),
        cached=cached,
    )


def _request_signer(config: SonghiveConfig):
    """Return a per-hop HTTP signature signer for the instance actor, or None."""
    if not (config.federation.enabled and config.federation.instance_domain):
        return None
    try:
        from pubby.crypto import load_private_key, sign_request

        from ..federation.storage import get_or_create_private_key

        key_path = get_or_create_private_key(config.federation.private_key_path)
        private_key = load_private_key(key_path.read_bytes())
        key_id = f"https://{config.federation.instance_domain}/ap/actor#main-key"
    except Exception:
        logger.warning("Instance actor key unavailable; remote fetches will be unsigned", exc_info=True)
        return None

    def _sign(url: str, headers: dict) -> dict:
        return sign_request(private_key=private_key, key_id=key_id, method="GET", url=url, headers=dict(headers))

    return _sign


async def fetch_remote_document(url: str, config: SonghiveConfig) -> FetchResult:
    """Like :func:`fetch_ap_document` but signed with the instance actor key."""
    require_remote_domain(url, config)
    return await asyncio.to_thread(
        guarded_fetch,
        url,
        check_url=_domain_guard(config),
        sign=_request_signer(config),
        timeout=config.federation.fetch_timeout_seconds,
    )


def _federation_storage(config: SonghiveConfig):
    """Return the shared pubby storage for the configured database."""
    from ..federation.actors import get_federation_storage

    return get_federation_storage(config.database.url)


def _cached_actor_entry(storage, actor_url: str) -> Optional[tuple]:
    """Return ``(actor_data, fetched_at)`` for a cached actor, or ``None``."""
    session = storage.session_factory()
    try:
        row = (
            session.query(storage.actor_cache_model)
            .filter(storage.actor_cache_model.actor_id == actor_url)
            .one_or_none()
        )
        if row is None:
            return None
        fetched_at = row.fetched_at
        if fetched_at is not None and fetched_at.tzinfo is None:
            fetched_at = fetched_at.replace(tzinfo=timezone.utc)
        return dict(row.actor_data), fetched_at
    finally:
        session.close()


def _find_cached_actor_by_handle(storage, username: str, domain: str) -> Optional[str]:
    """
    Resolve ``username@domain`` to a cached actor URL without any fetch.

    ``preferredUsername`` is tried first across the actor cache, followers
    and follow-request tables; the actor URL's path tail is tried as a
    fallback so opaque actor ids (e.g. Mastodon ``/ap/users/<id>`` URIs)
    still resolve when the numeric id was used as the handle's user part.
    """
    host = domain.split(":", 1)[0].lower()
    models = [storage.actor_cache_model, storage.follower_model]
    request_model = getattr(storage, "follow_request_model", None)
    if request_model is not None:
        models.append(request_model)
    tail_match: Optional[str] = None
    session = storage.session_factory()
    try:
        for model in models:
            rows = session.query(model).filter(model.actor_id.ilike(f"%{host}%")).all()
            for row in rows:
                actor_id = row.actor_id
                if not isinstance(actor_id, str):
                    continue
                if federation_service.extract_domain(actor_id) != host:
                    continue
                doc = row.actor_data if isinstance(row.actor_data, dict) else {}
                preferred = doc.get("preferredUsername")
                if isinstance(preferred, str) and preferred.lower() == username.lower():
                    return actor_id
                tail = urlparse(actor_id).path.rstrip("/").rsplit("/", 1)[-1].lstrip("@")
                if tail_match is None and tail.lower() == username.lower():
                    tail_match = actor_id
    finally:
        session.close()
    return tail_match


def _cache_actor(storage, actor_url: str, actor_doc: dict, *, unavailable: bool = False) -> None:
    """Upsert an actor document into pubby's federation actor cache."""
    doc = dict(actor_doc)
    if unavailable:
        doc[_UNAVAILABLE_FLAG] = True
    else:
        doc.pop(_UNAVAILABLE_FLAG, None)
    storage.cache_remote_actor(actor_url, doc, datetime.now(timezone.utc))


async def _webfinger_actor_url(username: str, domain: str, config: SonghiveConfig) -> str:
    """Resolve ``username@domain`` to an actor URL through guarded WebFinger."""
    resource = f"acct:{username}@{domain}"
    url = f"https://{domain}/.well-known/webfinger?resource={resource}"
    result = await asyncio.to_thread(
        guarded_fetch,
        url,
        check_url=_domain_guard(config),
        sign=_request_signer(config),
        timeout=config.federation.fetch_timeout_seconds,
    )
    data = result.json()
    if not isinstance(data, dict):
        raise FetchError("WebFinger returned an invalid document", url=url)
    for link in data.get("links") or []:
        if not isinstance(link, dict) or link.get("rel") != "self":
            continue
        link_type = str(link.get("type") or "")
        href = link.get("href")
        if link_type.startswith("application/") and isinstance(href, str) and href.startswith(("http://", "https://")):
            return href
    raise FetchError(f"WebFinger for {resource} returned no actor link", url=url)


async def _fetch_actor_document(actor_url: str, config: SonghiveConfig) -> dict:
    """Fetch and validate an ActivityPub actor document."""
    result = await fetch_remote_document(actor_url, config)
    doc = result.json()
    if not isinstance(doc, dict):
        raise FetchError("Remote actor document is not an object", url=result.url)
    # The canonical actor id is the document's own ``id`` — the fetched URL
    # may be a profile page or a redirect alias.
    doc_id = doc.get("id")
    if not isinstance(doc_id, str) or not doc_id.startswith(("http://", "https://")):
        raise FetchError("Remote actor document has no id", url=result.url)
    require_remote_domain(doc_id, config)
    if doc.get("type") not in _ACTOR_TYPES:
        raise FetchError(f"Remote document is not an actor (type={doc.get('type')!r})", status_code=422, url=result.url)
    return doc


async def lookup_remote_actor(
    session: AsyncSession,
    config: SonghiveConfig,
    raw: str,
    *,
    refresh: bool = False,
) -> RemoteActorResult:
    """
    Resolve a handle or actor/profile URL to a normalized remote actor.

    Cache-first: a fresh cached actor is served without any fetch unless
    ``refresh`` is set. On cache miss the actor is resolved through
    WebFinger (handles) or fetched directly (URLs) under the domain/SSRF
    guards, then upserted into pubby's ``federation_actor_cache``.

    Raises ``FetchError`` for disallowed domains and fetch failures,
    ``FetchNotFound`` when the remote confirms the actor is gone, and
    ``HTTPException`` 400/422 for unparsable input or non-actor documents.
    """
    await _refresh_instance_policies(session)
    target = parse_remote_target(raw, config)
    if target.kind == RemoteTargetKind.LOCAL:
        raise HTTPException(status_code=400, detail="Local actors are served by the local user API")
    if target.kind not in (RemoteTargetKind.HANDLE, RemoteTargetKind.ACTOR_URL, RemoteTargetKind.OBJECT_URL):
        raise HTTPException(status_code=400, detail="Unsupported remote actor lookup input")

    storage = await asyncio.to_thread(_federation_storage, config)

    actor_url: Optional[str] = None
    if target.kind == RemoteTargetKind.HANDLE:
        require_remote_domain(target.domain or "", config)
        actor_url = await asyncio.to_thread(
            _find_cached_actor_by_handle, storage, target.username or "", target.domain or ""
        )
        if actor_url is None or refresh:
            actor_url = await _webfinger_actor_url(target.username or "", target.domain or "", config)
    else:
        actor_url = target.url
        require_remote_domain(actor_url or "", config)

    assert actor_url  # for mypy — set on every branch above

    if not refresh:
        cached = await asyncio.to_thread(_cached_actor_entry, storage, actor_url)
        if cached is not None:
            doc, fetched_at = cached
            if (
                fetched_at is None
                or (datetime.now(timezone.utc) - fetched_at).total_seconds() < ACTOR_CACHE_TTL_SECONDS
            ):
                return _normalize_actor(actor_url, doc, cached=True, fetched_at=fetched_at)

    try:
        doc = await _fetch_actor_document(actor_url, config)
    except FetchNotFound:
        # Mark the cached copy unavailable rather than dropping it.
        cached = await asyncio.to_thread(_cached_actor_entry, storage, actor_url)
        if cached is not None:
            stale_doc, _ = cached
            await asyncio.to_thread(_cache_actor, storage, actor_url, stale_doc, unavailable=True)
            result = _normalize_actor(actor_url, stale_doc, cached=True)
            result.unavailable = True
            return result
        raise
    except FetchError as exc:
        # Some federated-music actors (Funkwhale channel/library actors)
        # don't dereference cleanly at their URL — retry once through
        # WebFinger using the URL's path tail as the account name. A 422
        # means the document fetched fine but is not an actor — that's a
        # definitive answer, not a transport failure.
        if (
            exc.status_code == 422
            or target.kind != RemoteTargetKind.ACTOR_URL
            or not target.username
            or not target.domain
        ):
            raise
        actor_url = await _webfinger_actor_url(target.username, target.domain, config)
        doc = await _fetch_actor_document(actor_url, config)

    canonical_url = doc["id"]
    if canonical_url != actor_url:
        require_remote_domain(canonical_url, config)
    await asyncio.to_thread(_cache_actor, storage, canonical_url, doc)
    return _normalize_actor(canonical_url, doc, cached=False, fetched_at=datetime.now(timezone.utc))


async def get_cached_remote_actor(
    session: AsyncSession,
    config: SonghiveConfig,
    handle: str,
) -> Optional[RemoteActorResult]:
    """
    Return a cached remote actor by ``user@domain`` handle — no network fetch.

    Used by the stable deep-link endpoint; ``None`` when the actor was never
    cached or the handle is local/disallowed.
    """
    await _refresh_instance_policies(session)
    target = parse_remote_target(handle if handle.startswith("@") else f"@{handle}", config)
    if target.kind != RemoteTargetKind.HANDLE or not target.username or not target.domain:
        return None
    if not remote_domain_allowed(target.domain, config):
        return None
    storage = await asyncio.to_thread(_federation_storage, config)
    actor_url = await asyncio.to_thread(_find_cached_actor_by_handle, storage, target.username, target.domain)
    if actor_url is None:
        return None
    cached = await asyncio.to_thread(_cached_actor_entry, storage, actor_url)
    if cached is None:
        return None
    doc, fetched_at = cached
    return _normalize_actor(actor_url, doc, cached=True, fetched_at=fetched_at)


# ---------------------------------------------------------------------------
# Remote object dereference and materialization
# ---------------------------------------------------------------------------


@dataclass
class RemoteObjectResult:
    """Outcome of dereferencing a remote object URL."""

    remote_object: RemoteObject
    activity: Optional[Activity] = None
    status: str = "ok"  # "ok" | "gone"
    fetched: bool = True


def _as_url(value: Any) -> Optional[str]:
    """Coerce an ActivityStreams link-ish value to an http(s) URL."""
    if isinstance(value, str) and value.startswith(("http://", "https://")):
        return value
    if isinstance(value, dict):
        for key in ("id", "href", "url"):
            resolved = _as_url(value.get(key))
            if resolved:
                return resolved
    if isinstance(value, list):
        for item in value:
            resolved = _as_url(item)
            if resolved:
                return resolved
    return None


def _media_url(value: Any, media_prefix: Optional[str] = None) -> Optional[str]:
    """Extract a media URL, optionally filtered by ``mediaType`` prefix."""
    if isinstance(value, str) and value.startswith(("http://", "https://")):
        return value
    if isinstance(value, dict):
        media_type = str(value.get("mediaType") or "")
        if media_prefix and media_type and not media_type.startswith(media_prefix):
            return None
        return _as_url(value)
    if isinstance(value, list):
        for item in value:
            resolved = _media_url(item, media_prefix)
            if resolved:
                return resolved
    return None


def _attributed_to(obj: dict) -> Optional[str]:
    """Return the object's ``attributedTo`` actor URL."""
    return _as_url(obj.get("attributedTo"))


def _doc_actor(doc: dict) -> Optional[str]:
    """Return the activity's ``actor`` URL."""
    return _as_url(doc.get("actor"))


def _object_name(obj: dict) -> Optional[str]:
    # Rendition documents (Funkwhale ``Audio``/``Video`` uploads) name
    # themselves "{artist} - {album} - {title}"; the embedded ``track``
    # document carries the plain title, which is what lists should show.
    track = obj.get("track")
    if isinstance(track, dict):
        name = _object_name(track)
        if name:
            return name
    for key in ("name", "title"):
        value = obj.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:500]
    return None


def _detect_resource_type(obj: dict, target: Optional[RemoteTarget] = None) -> Optional[str]:
    """Map a fetched document to a normalized remote resource kind."""
    if target is not None and target.resource_kind:
        return target.resource_kind
    obj_type = obj.get("type")
    # Music entities in the shared Funkwhale/Songhive dialect (``Audio`` is
    # the playable upload; ``Track``/``Album``/``Artist``/``Library`` are
    # catalog entities). A bare ``Audio`` with no music fields still maps
    # to ``track`` — matching the pre-existing Songhive-typed heuristic.
    if obj_type in _MUSIC_RESOURCE_TYPES:
        return _MUSIC_RESOURCE_TYPES[obj_type]
    resource_hint = obj.get("songhive:resourceType") or obj.get("resourceType")
    if isinstance(resource_hint, str) and resource_hint in RESOURCE_KIND_PLURALS:
        return resource_hint
    return None


def _parent_url_of(obj: dict) -> Optional[str]:
    """
    Return the containing music resource URL for a federated music doc.

    The containment graph mirrors the Funkwhale dialect: ``Audio`` →
    ``library``, ``Track`` → ``album``, ``Album`` → first ``artists`` entry,
    collection pages → ``partOf``. ``None`` for non-music documents.
    """
    obj_type = obj.get("type")
    if obj_type == "Audio":
        return _as_url(obj.get("library"))
    if obj_type == "Track":
        return _as_url(obj.get("album"))
    if obj_type == "Album":
        artists = obj.get("artists") or obj.get("artist_credit")
        if isinstance(artists, list) and artists:
            return _as_url(artists[0])
        return _as_url(artists)
    if obj_type in _COLLECTION_PAGE_TYPES:
        return _as_url(obj.get("partOf"))
    return None


_PUBLIC_AUDIENCE_URIS = frozenset(
    {
        "https://www.w3.org/ns/activitystreams#Public",
        "as:Public",
        "Public",
    }
)


def _doc_is_public(obj: dict) -> bool:
    """
    Return whether a document is publicly addressed.

    Extends pubby's ``is_public`` (which scans ``to``/``cc``/``bto``/
    ``bcc``) with the ``audience`` field — Funkwhale library documents
    address the public collection through ``audience`` only.
    """
    if is_public(obj):
        return True
    audience = obj.get("audience")
    values = audience if isinstance(audience, list) else [audience]
    return any(v in _PUBLIC_AUDIENCE_URIS for v in values if isinstance(v, str))


async def _cached_remote_object(session: AsyncSession, url: str) -> Optional[RemoteObject]:
    """Return the cache row for ``url`` as canonical or activity URL."""
    return await session.scalar(
        select(RemoteObject).where((RemoteObject.canonical_url == url) | (RemoteObject.activity_url == url))
    )


async def get_cached_remote_object(session: AsyncSession, object_id: str) -> Optional[RemoteObject]:
    """Return the ``remote_objects`` row for its local id, or ``None``."""
    await _refresh_instance_policies(session)
    return await session.get(RemoteObject, object_id)


async def get_remote_object_activity(
    session: AsyncSession,
    remote_object: RemoteObject,
) -> Optional[Activity]:
    """Return the materialized activity mirroring ``remote_object``."""
    return await session.scalar(
        select(Activity).where(
            Activity.source_type == "remote",
            Activity.source_id == remote_object.canonical_url,
        )
    )


async def _unwrap_document(
    doc: dict,
    config: SonghiveConfig,
) -> tuple[Optional[dict], Optional[dict], str]:
    """
    Unwrap an ActivityPub activity document into ``(wrapper, object, kind)``.

    ``kind`` is ``"delete"`` for Delete/Tombstone documents, else the
    wrapper activity type (``Create``, ``Announce``, ``Update``, …) or
    ``"Object"`` for bare objects. String ``object`` references are
    dereferenced once — still under the same guards — keeping the total
    fetch bound at two documents per lookup.
    """
    doc_type = doc.get("type")
    if doc_type not in _ACTIVITY_WRAPPER_TYPES and doc_type not in _TOMBSTONE_TYPES:
        return None, doc, "Object"

    inner = doc.get("object")
    if isinstance(inner, str):
        if doc_type in _TOMBSTONE_TYPES or doc_type == "Delete":
            return doc, None, "delete"
        result = await fetch_remote_document(inner, config)
        inner_doc = result.json()
        if not isinstance(inner_doc, dict):
            raise FetchError("Remote object reference did not resolve to a document", url=inner)
        return doc, inner_doc, str(doc_type)

    if isinstance(inner, dict):
        if inner.get("type") in _TOMBSTONE_TYPES:
            return doc, None, "delete"
        return doc, inner, str(doc_type)

    # A Delete may carry only the object id (or nothing usable).
    if doc_type in _TOMBSTONE_TYPES or doc_type == "Delete":
        return doc, None, "delete"
    raise FetchError(f"Remote {doc_type} activity has no usable object", url=doc.get("id"))


def _normalized_activity_type(kind: str, obj: dict) -> str:
    """Normalize a dereference to a materialized ``activity_type``."""
    if kind == "Announce":
        return "announce"
    if kind == "Update":
        return "update"
    if extract_quote_target(obj):
        return "quote"
    if obj.get("inReplyTo"):
        return "reply"
    return "create"


async def _resolve_parent_activity(session: AsyncSession, obj: dict) -> Optional[Activity]:
    """Resolve ``inReplyTo``/quote targets to already-cached activities."""
    from ..federation.incoming import _resolve_object_activity

    for ref in (obj.get("inReplyTo"), extract_quote_target(obj)):
        if isinstance(ref, str) and ref:
            parent = await _resolve_object_activity(session, ref)
            if parent is not None:
                return parent
    return None


async def _materialize_remote_activity(
    session: AsyncSession,
    *,
    remote_object: RemoteObject,
    wrapper: Optional[dict],
    obj: dict,
    actor_url: str,
    kind: str,
    notify_subscribers: bool = False,
    config: Optional[SonghiveConfig] = None,
) -> Activity:
    """
    Upsert the ``Activity`` row mirroring a cached remote object.

    Remote rows are ``source_type="remote"`` keyed by the canonical object
    URL, attach to the ``remote_objects`` row through ``entity_type=
    "remote"``, and link to a cached parent only — never fetched. When
    ``notify_subscribers`` is set, a newly created row fans out an
    ``activity`` notification to the actor's subscribers — the inbox
    materialization path uses it; explicit lookups do not.
    """
    from ..federation.incoming import (
        _object_language,
        _parse_published,
        _remote_hashtags,
        _remote_mentions,
        _sync_activity_tags,
    )
    from ..federation.notifications import strip_quote_fallback
    from ..services.preview_cards import schedule_preview_card_fetch

    activity_type = _normalized_activity_type(kind, obj)
    parent = await _resolve_parent_activity(session, obj) if activity_type in ("reply", "quote") else None

    existing = await session.scalar(
        select(Activity).where(
            Activity.source_type == "remote",
            Activity.source_id == remote_object.canonical_url,
        )
    )

    content = strip_quote_fallback(obj.get("content"), extract_quote_target(obj))
    fields = {
        "activity_type": activity_type if activity_type != "update" or existing is None else existing.activity_type,
        "source_actor": actor_url,
        "visibility": "public" if remote_object.visibility == "public" else "local",
        "in_reply_to_activity_id": str(parent.id) if parent is not None else None,
        "content": content if isinstance(content, str) and content else None,
        "content_type": "text/html" if isinstance(content, str) and content else "text/plain",
        "language": _object_language(obj),
        "payload": wrapper if wrapper is not None else obj,
        "published_at": _parse_published(obj.get("published")) or remote_object.fetched_at,
        "deleted_at": None,
        "retracted": False,
    }

    if existing is not None:
        for key, value in fields.items():
            setattr(existing, key, value)
        row = existing
    else:
        row = Activity(
            entity_type="remote",
            entity_id=str(remote_object.id),
            activity_type=fields["activity_type"],
            source_type="remote",
            source_actor=actor_url,
            source_id=remote_object.canonical_url,
            owner_user_id=None,
            visibility=fields["visibility"],
            in_reply_to_activity_id=fields["in_reply_to_activity_id"],
            content=fields["content"],
            content_type=fields["content_type"],
            language=fields["language"],
            payload=fields["payload"],
            published_at=fields["published_at"],
        )
        session.add(row)
    await session.flush()

    mentions = await _remote_mentions(session, obj, config)
    await session.refresh(row, ["mentions"])
    existing_mentions = {m.handle for m in row.mentions}
    for mention in mentions:
        if mention["handle"] not in existing_mentions:
            session.add(ActivityMention(activity_id=row.id, **mention))
    await session.flush()
    await _sync_activity_tags(session, row, _remote_hashtags(obj))
    if row.preview_card_id is None:
        schedule_preview_card_fetch(row)
    if existing is None and notify_subscribers:
        from .activities import notify_remote_activity_subscribers

        await notify_remote_activity_subscribers(session, activity=row, config=config)
    return row


async def _upsert_remote_object_row(
    session: AsyncSession,
    *,
    canonical_url: str,
    activity: dict,
    obj: dict,
    actor_url: str,
    target: Optional[RemoteTarget] = None,
    etag: Optional[str] = None,
    last_modified: Optional[str] = None,
) -> RemoteObject:
    """
    Create or refresh the ``remote_objects`` cache row for ``canonical_url``.

    ``activity`` is the wrapping activity document — or the fetched
    document itself for bare objects. Shared by the explicit-dereference
    path and the inbox materialization of standalone posts from followed
    actors; fresh data always marks the row available again.
    """
    row = await _cached_remote_object(session, canonical_url)
    if row is None:
        row = RemoteObject(canonical_url=canonical_url, domain="", object_type="", actor_url="")
        session.add(row)

    activity_url = activity.get("id")
    row.canonical_url = canonical_url
    row.activity_url = activity_url if isinstance(activity_url, str) and activity_url != canonical_url else None
    row.domain = federation_service.extract_domain(canonical_url)
    row.object_type = str(obj.get("type") or "Object")
    row.resource_type = _detect_resource_type(obj, target)
    row.actor_url = actor_url
    row.parent_url = _parent_url_of(obj)
    row.media_of_url = _media_of_url(obj)
    row.visibility = "public" if _doc_is_public(obj) or is_public(activity) else "private"
    row.payload = obj
    row.name = _object_name(obj)
    row.summary = obj.get("summary") if isinstance(obj.get("summary"), str) else None
    row.content = obj.get("content") if isinstance(obj.get("content"), str) else None
    row.image_url = _media_url(obj.get("image"), "image/")
    row.audio_url = _media_url(obj.get("url"), "audio/")
    row.content_hash = content_hash(obj)
    row.etag = etag
    row.last_modified = last_modified
    row.fetched_at = datetime.now(timezone.utc)
    row.unavailable_at = None
    await session.flush()
    return row


async def materialize_remote_post(
    session: AsyncSession,
    *,
    activity: dict,
    config: Optional[SonghiveConfig] = None,
) -> Optional[Activity]:
    """
    Store an inbound ``Create`` object with no local thread target as a
    cached remote post.

    Standalone posts — and replies/quotes whose targets are not cached —
    from actors followed by a local user arrive through the inbox rather
    than explicit lookup. They get the same ``remote_objects`` +
    ``entity_type="remote"`` mirror shape ``dereference_remote_object``
    produces, so they appear on the remote profile's posts tab and stay
    re-fetchable by URL. Returns ``None`` for non-content objects,
    tombstones, attribution failures and disallowed domains; the object
    cache row is still upserted for content documents whose mirror is
    skipped.
    """
    if activity.get("type") != "Create":
        return None
    obj = activity.get("object")
    actor = activity.get("actor")
    if not isinstance(obj, dict) or not isinstance(actor, str):
        return None
    object_id = obj.get("id")
    if (
        not actor.startswith(("http://", "https://"))
        or not isinstance(object_id, str)
        or not object_id.startswith(("http://", "https://"))
        or obj.get("type") in _TOMBSTONE_TYPES
    ):
        return None

    # Attribution sanity is owned by pubby (``strict_attribution`` on the
    # inbox processor) — but this sync runs on the raw activity even when
    # the processor dropped it, so the same guard is applied here.
    try:
        validate_attribution(actor, obj)
    except AttributionMismatch:
        return None
    if config is not None:
        try:
            require_remote_domain(object_id, config)
            require_remote_domain(actor, config)
        except FetchError:
            return None

    row = await _upsert_remote_object_row(
        session,
        canonical_url=object_id,
        activity=activity,
        obj=obj,
        actor_url=actor,
    )
    # Content objects become feed activities — matching the wrapped-object
    # rule in ``dereference_remote_object``, so e.g. a ``Create(Audio)``
    # track post mirrors as an activity as well as a browsable resource.
    if obj.get("type") not in _CONTENT_OBJECT_TYPES:
        return None
    return await _materialize_remote_activity(
        session,
        remote_object=row,
        wrapper=activity,
        obj=obj,
        actor_url=actor,
        kind="Create",
        notify_subscribers=True,
        config=config,
    )


def _iter_embedded_music_docs(obj: dict):
    """
    Yield ``(doc, container_url)`` for music entities embedded in ``obj``.

    Covers the shared music dialect: an ``Audio`` embeds ``track`` (which
    embeds ``album`` and ``artists``), ``artists`` and ``library``; a
    ``Track`` embeds ``album`` and ``artists``; an ``Album`` embeds
    ``artists``; a collection page carries music ``items``. ``container_url``
    is the URL the yielded doc is contained in — used as ``parent_url``
    when the doc itself does not name its container. Artist embeds pass no
    container: artists are credited on albums/tracks, not contained in them
    (the containment edge is inverted — ``_parent_url_of`` maps Album →
    artist, not the reverse).
    """
    obj_id = obj.get("id") if isinstance(obj.get("id"), str) else None
    obj_type = obj.get("type")

    def _yield_doc(value, container):
        if isinstance(value, dict):
            yield value, container
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    yield item, container

    if obj_type == "Audio":
        track = obj.get("track")
        if isinstance(track, dict):
            yield track, obj_id
            album = track.get("album")
            if isinstance(album, dict):
                yield album, _as_url(track.get("album"))
                for artist_doc, _ in _yield_doc(album.get("artists") or album.get("artist_credit"), album.get("id")):
                    yield artist_doc, None
            for artist_doc, _ in _yield_doc(track.get("artists") or track.get("artist_credit"), track.get("id")):
                yield artist_doc, None
        for artist_doc, _ in _yield_doc(obj.get("artists") or obj.get("artist_credit"), obj_id):
            yield artist_doc, None
        library = obj.get("library")
        if isinstance(library, dict):
            yield library, None
    elif obj_type == "Track":
        album = obj.get("album")
        if isinstance(album, dict):
            yield album, obj_id
            for artist_doc, _ in _yield_doc(album.get("artists") or album.get("artist_credit"), album.get("id")):
                yield artist_doc, None
        for artist_doc, _ in _yield_doc(obj.get("artists") or obj.get("artist_credit"), obj_id):
            yield artist_doc, None
    elif obj_type == "Album":
        for artist_doc, _ in _yield_doc(obj.get("artists") or obj.get("artist_credit"), obj_id):
            yield artist_doc, None
    elif obj_type in _COLLECTION_PAGE_TYPES:
        items = obj.get("items") or obj.get("orderedItems")
        container = _as_url(obj.get("partOf")) or obj_id
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict) and item.get("type") in _MUSIC_OBJECT_TYPES:
                    yield item, container
                    for nested, nested_container in _iter_embedded_music_docs(item):
                        yield nested, nested_container


async def _cache_embedded_music_docs(
    session: AsyncSession,
    obj: dict,
    actor_url: str,
    config: SonghiveConfig,
) -> None:
    """
    Upsert ``remote_objects`` rows for music entities embedded in ``obj``.

    A single dereference of a Funkwhale/Songhive ``Audio`` or library page
    yields the whole album/artist/library subtree — caching the embedded
    docs makes remote album and library pages render without one fetch per
    child. Only absolute-id, allowed-domain docs with a known music type are
    stored, bounded by ``_MAX_EMBEDDED_OBJECTS``; embedded docs inherit the
    parent's actor and visibility.
    """
    seen: set = set()
    count = 0
    for doc, container_url in _iter_embedded_music_docs(obj):
        if count >= _MAX_EMBEDDED_OBJECTS:
            break
        doc_id = doc.get("id")
        doc_type = doc.get("type")
        if (
            not isinstance(doc_id, str)
            or not doc_id.startswith(("http://", "https://"))
            or doc_type not in _MUSIC_OBJECT_TYPES
            or doc_id in seen
            or not remote_domain_allowed(doc_id, config)
        ):
            continue
        seen.add(doc_id)
        count += 1
        doc_actor = _attributed_to(doc) or actor_url
        await _upsert_remote_object_row(
            session,
            canonical_url=doc_id,
            activity=doc,
            obj=doc,
            actor_url=doc_actor,
        )
        row = await _cached_remote_object(session, doc_id)
        if row is not None and row.parent_url is None:
            row.parent_url = _parent_url_of(doc) or container_url


# ---------------------------------------------------------------------------
# Remote media resolution
# ---------------------------------------------------------------------------
#
# A remote *track* is metadata — its playable media can be a direct link on
# the document (``audio_url``) or a *rendition* sibling object that embeds
# the track (Funkwhale ``Audio``/upload docs carry ``track.id``). Rendition
# rows record that relationship in ``media_of_url`` so the lookup
# ``media_of_url == track.canonical_url`` stays an indexed query. Rows
# cached before the column existed are still found through a bounded
# JSON-path fallback. Resolution happens at play time
# (``/remote/objects/{id}/stream``), which is also the seam where provider
# plugins (external sources whose links are expiring or require resolution,
# e.g. YouTube) will plug in.


def _media_of_url(obj: dict) -> Optional[str]:
    """Return the URL of the media entity ``obj`` renders, when declared."""
    return _as_url(obj.get("track"))


async def rendition_audio_map(session: AsyncSession, canonical_urls: List[str]) -> dict:
    """
    Map ``{entity_url: audio_url}`` for cached renditions of ``canonical_urls``.

    One batched query covers both ``media_of_url``-stamped rows and legacy
    rows whose only rendition link lives in the payload's ``track.id``.
    """
    if not canonical_urls:
        return {}
    rows = (
        await session.execute(
            select(
                RemoteObject.media_of_url,
                RemoteObject.audio_url,
                RemoteObject.payload[("track", "id")].as_string().label("legacy_track_id"),
            ).where(
                RemoteObject.audio_url.is_not(None),
                RemoteObject.unavailable_at.is_(None),
                or_(
                    RemoteObject.media_of_url.in_(canonical_urls),
                    RemoteObject.payload[("track", "id")].as_string().in_(canonical_urls),
                ),
            )
        )
    ).all()
    result: dict = {}
    for media_of, audio_url, legacy_id in rows:
        key = media_of or legacy_id
        if key in canonical_urls and audio_url:
            result.setdefault(key, audio_url)
    return result


async def resolve_media_url(session: AsyncSession, row: RemoteObject) -> Optional[str]:
    """
    Resolve the playable media URL for a remote object at request time.

    Direct ``audio_url`` links win; track-shaped rows fall back to their
    cached rendition's URL. Returns ``None`` when nothing playable is
    cached — the caller (stream endpoint) turns that into a 404.
    """
    if row.unavailable_at is not None:
        return None
    if row.audio_url:
        return row.audio_url
    if row.object_type in ("Audio", "Track", "Video") or row.resource_type == "track":
        return (await rendition_audio_map(session, [row.canonical_url])).get(row.canonical_url)
    return None


def remote_object_stream_url(row: RemoteObject) -> str:
    """Internal playback endpoint for a remote object — resolves at play time."""
    return f"/api/v1/remote/objects/{row.id}/stream"


async def _scan_library_first_page(
    session: AsyncSession,
    library_doc: dict,
    actor_url: str,
    config: SonghiveConfig,
) -> None:
    """
    Fetch a remote ``Library``'s first collection page and cache its items.

    Funkwhale and Songhive library documents only carry page links — the
    tracks live under ``?page=N``. One bounded extra fetch populates the
    library's children so its remote resource page is not empty; deeper
    pages stay lazily resolvable through explicit lookup.
    """
    first = _as_url(library_doc.get("first"))
    if first is None or not remote_domain_allowed(first, config):
        return
    try:
        result = await fetch_remote_document(first, config)
        page_doc = result.json()
    except (FetchError, FetchNotFound):
        return
    if not isinstance(page_doc, dict) or page_doc.get("type") not in _COLLECTION_PAGE_TYPES:
        return
    # Cache the page itself so its own id resolves, then the items.
    page_id = page_doc.get("id")
    if isinstance(page_id, str) and page_id.startswith(("http://", "https://")):
        await _upsert_remote_object_row(
            session,
            canonical_url=page_id,
            activity=page_doc,
            obj=page_doc,
            actor_url=actor_url,
        )
    await _cache_embedded_music_docs(session, page_doc, actor_url, config)


async def _fold_renditions(session: AsyncSession, rows: List[RemoteObject]) -> List[RemoteObject]:
    """
    Replace rendition rows with their cached media-target entity rows.

    Memberships and listings should reference the entity (``Track``) rather
    than its upload rendition when both exist in the cache.
    """
    targets = {row.media_of_url for row in rows if row.media_of_url}
    if not targets:
        return rows
    target_rows = list(
        (
            await session.execute(
                select(RemoteObject).where(
                    RemoteObject.canonical_url.in_(targets),
                    RemoteObject.unavailable_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    by_url = {row.canonical_url: row for row in target_rows}
    return [by_url[row.media_of_url] if row.media_of_url in by_url else row for row in rows]


async def get_remote_object_children(
    session: AsyncSession,
    remote_object: RemoteObject,
    *,
    limit: int = 100,
) -> List[RemoteObject]:
    """
    Return cached children of a remote music resource — no fetch.

    Children are cached ``remote_objects`` rows whose ``parent_url`` is the
    resource's canonical URL: tracks/uploads under a library, tracks under
    an album, albums under an artist. Rendition rows (a Funkwhale
    ``Audio``/``Video`` upload) fold onto their cached media-target
    entities — excluding them outright would leave a library whose items
    all have cached ``Track`` docs looking empty.
    """
    stmt = (
        select(RemoteObject)
        .where(
            RemoteObject.parent_url == remote_object.canonical_url,
            RemoteObject.unavailable_at.is_(None),
            # Only browsable resources — collection page rows cached by a
            # library scan carry the same ``parent_url`` but are not items.
            RemoteObject.resource_type.isnot(None),
        )
        .order_by(RemoteObject.fetched_at.asc())
        .limit(limit)
    )
    rows = list((await session.execute(stmt)).scalars().all())
    folded = await _fold_renditions(session, rows)
    seen: set = set()
    children: List[RemoteObject] = []
    for row in folded:
        key = str(row.id)
        if key in seen:
            continue
        seen.add(key)
        children.append(row)
    return children


async def get_remote_objects_children_map(
    session: AsyncSession,
    parents: List[RemoteObject],
    *,
    limit: int = 100,
) -> Dict[str, List[RemoteObject]]:
    """
    Return ``{parent canonical_url: children}`` for a batch of parents.

    One query covers every parent so container pages can nest one level of
    grandchildren (an artist's albums with their tracks) without N+1.
    """
    urls = [p.canonical_url for p in parents]
    if not urls:
        return {}
    stmt = (
        select(RemoteObject)
        .where(
            RemoteObject.parent_url.in_(urls),
            RemoteObject.unavailable_at.is_(None),
            RemoteObject.resource_type.isnot(None),
        )
        .order_by(RemoteObject.fetched_at.asc())
    )
    rows = list((await session.execute(stmt)).scalars().all())
    # Fold renditions onto cached entities, but keep the rendition's own
    # ``parent_url`` as the grouping key — a library's uploads stay listed
    # under the library even though the folded ``Track``'s parent is its
    # album.
    folded = await _fold_renditions(session, rows)
    result: Dict[str, List[RemoteObject]] = {}
    seen: Dict[str, set] = {}
    for orig, row in zip(rows, folded):
        if orig.parent_url is None:
            continue
        bucket = result.setdefault(orig.parent_url, [])
        bucket_seen = seen.setdefault(orig.parent_url, set())
        if len(bucket) < limit and str(row.id) not in bucket_seen:
            bucket_seen.add(str(row.id))
            bucket.append(row)
    return result


async def get_remote_object_parent(
    session: AsyncSession,
    remote_object: RemoteObject,
) -> Optional[RemoteObject]:
    """Return the cached containing resource of ``remote_object``, if any."""
    if not remote_object.parent_url:
        return None
    return await _cached_remote_object(session, remote_object.parent_url)


async def _mark_remote_gone(
    session: AsyncSession,
    cached: Optional[RemoteObject],
    url: str,
) -> Optional[RemoteObjectResult]:
    """Tombstone the cache row for ``url`` and its materialized activity."""
    if cached is None:
        return None
    cached.unavailable_at = datetime.now(timezone.utc)
    activity = await session.scalar(
        select(Activity).where(
            Activity.source_type == "remote",
            Activity.source_id == cached.canonical_url,
        )
    )
    if activity is not None:
        activity.deleted_at = datetime.now(timezone.utc)
        activity.retracted = True
    await session.flush()
    return RemoteObjectResult(remote_object=cached, activity=activity, status="gone")


async def dereference_remote_object(
    session: AsyncSession,
    config: SonghiveConfig,
    url: str,
    *,
    refresh: bool = False,
) -> RemoteObjectResult:
    """
    Fetch and cache one explicitly requested remote object or activity URL.

    Applies the domain/SSRF guards, unwraps ``Create``/``Announce``/
    ``Update``/``Delete`` envelopes (one bounded follow of a string
    ``object`` reference), validates attribution against the publishing
    actor, upserts the ``remote_objects`` cache row, and materializes the
    mirror ``Activity``. Deletes and 404/410 responses tombstone the row.

    Raises ``FetchError``/``FetchNotFound`` for disallowed domains, fetch
    failures and remote-gone objects without a cached copy, and
    ``HTTPException`` 422 for unsupported document shapes.
    """
    await _refresh_instance_policies(session)
    require_remote_domain(url, config)
    target = parse_remote_target(url, config)
    # A normalized target (e.g. a Funkwhale ``/library/{uuid}`` page
    # rewritten to its federation document URL) is what actually gets
    # fetched; the doc's own ``id`` stays the canonical url either way.
    url = target.url or url

    cached = await _cached_remote_object(session, url)
    if cached is not None and not refresh:
        activity = await session.scalar(
            select(Activity).where(
                Activity.source_type == "remote",
                Activity.source_id == cached.canonical_url,
            )
        )
        return RemoteObjectResult(
            remote_object=cached,
            activity=activity,
            status="gone" if cached.unavailable_at is not None else "ok",
            fetched=False,
        )

    try:
        result = await fetch_remote_document(url, config)
    except FetchNotFound:
        gone = await _mark_remote_gone(session, cached, url)
        if gone is not None:
            return gone
        raise

    doc = result.json()
    if not isinstance(doc, dict):
        raise HTTPException(status_code=422, detail="Remote document is not an object")

    wrapper, obj, kind = await _unwrap_document(doc, config)
    if kind == "delete" or obj is None:
        gone = await _mark_remote_gone(session, cached, url)
        if gone is not None:
            return gone
        raise FetchNotFound("Remote object no longer exists", url=url)

    # A collection page (``{library}?page=N`` pasted into the lookup box)
    # is not itself a browsable resource — cache its items under the
    # parent collection, then keep resolving the parent as the object.
    page_doc: Optional[dict] = None
    if wrapper is None and obj.get("type") in _COLLECTION_PAGE_TYPES:
        part_of = _as_url(obj.get("partOf"))
        if part_of is not None and remote_domain_allowed(part_of, config):
            page_doc = obj
            try:
                page_result = await fetch_remote_document(part_of, config)
                parent_doc = page_result.json()
            except (FetchError, FetchNotFound):
                parent_doc = None
            if isinstance(parent_doc, dict):
                obj = parent_doc
                kind = "Object"

    object_id = obj.get("id")
    if not isinstance(object_id, str) or not object_id.startswith(("http://", "https://")):
        raise FetchError("Remote object has no id", status_code=422, url=result.url)
    require_remote_domain(object_id, config)

    actor_url = _doc_actor(wrapper) if wrapper is not None else None
    actor_url = actor_url or _attributed_to(obj) or _doc_actor(obj)
    if actor_url is None:
        raise HTTPException(status_code=422, detail="Remote object has no resolvable author")
    require_remote_domain(actor_url, config)

    # ``attributedTo`` must match the publishing actor and the object id
    # must share its authority — pubby's rules for inbound delivery apply
    # to explicit fetches too. Music entities carry no ``attributedTo``
    # requirement of their own; their owning actor is ``actor``.
    if obj.get("type") not in _MUSIC_OBJECT_TYPES or _attributed_to(obj) is not None:
        validate_attribution(actor_url, obj)

    # Resolve (and cache) the publishing actor through the actor path so
    # profile rendering and audience checks share one cache. Music docs
    # whose actor document is not fetchable (e.g. a Funkwhale library
    # actor with no WebFinger entry) still cache — the actor URL alone is
    # enough to render attribution.
    try:
        actor = await lookup_remote_actor(session, config, actor_url)
        resolved_actor_url = actor.actor_url
    except FetchError as exc:
        if obj.get("type") not in _MUSIC_OBJECT_TYPES:
            raise FetchError(f"Could not resolve remote object author: {exc}", status_code=exc.status_code) from exc
        resolved_actor_url = actor_url

    if kind not in ("Create", "Announce", "Update", "Object") or obj.get("type") in _TOMBSTONE_TYPES:
        raise HTTPException(status_code=422, detail=f"Unsupported remote activity type: {kind}")

    row = await _upsert_remote_object_row(
        session,
        canonical_url=object_id,
        # ``activity`` is the wrapping activity doc, else the object itself —
        # not the originally fetched doc, which may be a collection page
        # swapped out for its parent collection above.
        activity=wrapper or obj,
        obj=obj,
        actor_url=resolved_actor_url,
        target=target,
        etag=result.headers.get("etag"),
        last_modified=result.headers.get("last_modified"),
    )

    # Music documents embed their catalog entities (track → album →
    # artists, audio → track/library); cache them so remote album/artist
    # pages render children without one fetch per entity. Library docs
    # carry no items — scan their first collection page instead. Both
    # paths are bounded and re-use the same domain guards.
    if obj.get("type") in _MUSIC_OBJECT_TYPES:
        await _cache_embedded_music_docs(session, obj, resolved_actor_url, config)
        if obj.get("type") == "Library":
            await _scan_library_first_page(session, obj, resolved_actor_url, config)
    if page_doc is not None:
        page_actor = _attributed_to(page_doc) or _doc_actor(page_doc) or resolved_actor_url
        page_id = page_doc.get("id")
        if isinstance(page_id, str) and remote_domain_allowed(page_id, config):
            await _upsert_remote_object_row(
                session,
                canonical_url=page_id,
                activity=page_doc,
                obj=page_doc,
                actor_url=page_actor,
            )
        await _cache_embedded_music_docs(session, page_doc, page_actor, config)

    # Wrapped objects (Create/Announce/…) and bare content objects become
    # feed activities; a bare resource (e.g. a dereferenced ``Audio`` track)
    # is cached, not materialized.
    should_materialize = wrapper is not None or (obj.get("type") in _CONTENT_OBJECT_TYPES and row.resource_type is None)
    activity = (
        await _materialize_remote_activity(
            session,
            remote_object=row,
            wrapper=wrapper,
            obj=obj,
            actor_url=resolved_actor_url,
            kind=kind,
        )
        if should_materialize
        else None
    )
    return RemoteObjectResult(remote_object=row, activity=activity, status="ok")


def actor_handle_from_url(actor_url: str) -> str:
    """Derive a ``user@domain`` handle from an actor URL's path tail."""
    parsed = urlparse(actor_url)
    name = parsed.path.rstrip("/").rsplit("/", 1)[-1].lstrip("@") or parsed.hostname or actor_url
    return f"{name}@{parsed.hostname}" if parsed.hostname else name


def remote_object_page_url(remote_object: RemoteObject, *, actor_handle: Optional[str] = None) -> str:
    """Return the internal SPA URL for a cached remote object."""
    if remote_object.resource_type:
        return f"/remote/{remote_object.resource_type}/{remote_object.id}"
    handle = actor_handle or actor_handle_from_url(remote_object.actor_url)
    return f"/activities/@{handle}/{remote_object.id}"


async def resolve_actor_handle_map(
    config: SonghiveConfig,
    actor_urls: Iterable[Optional[str]],
) -> dict[str, str]:
    """
    Resolve actor URLs to ``user@domain`` handles from cached actor docs.

    ``preferredUsername`` wins over the URL tail, so opaque actor ids (e.g.
    Mastodon ``/ap/users/<id>`` URIs) map to the real handle. Local-domain
    actor URLs are excluded — local profiles route by bare username, not
    ``user@domain``.
    """
    local = federation_service.normalize_instance_domain(config.federation.instance_domain or "")
    urls = {
        url
        for url in actor_urls
        if isinstance(url, str)
        and url.startswith(("http://", "https://"))
        and federation_service.extract_domain(url) != local
    }
    if not urls:
        return {}
    try:
        storage = await asyncio.to_thread(_federation_storage, config)
        return await asyncio.to_thread(federation_service.cached_actor_handles, storage, urls)
    except Exception:
        # Display-only resolution — a storage error (e.g. a transient lock
        # while a request transaction is open) must not fail the request;
        # callers fall back to the actor URL's path tail.
        logger.debug("Could not resolve cached actor handles", exc_info=True)
        return {}


def remote_object_payload_name(obj: dict) -> Optional[str]:
    """Return the display name embedded in a remote object document."""
    return _object_name(obj)


def activity_remote_object(activity: Activity) -> Optional[dict]:
    """
    Summarize the remote music object embedded in an activity payload.

    Materialized remote activities wrap the cached object document, so the
    summary is derivable straight from the payload without a database hit.
    Only music resources qualify — note-type mirrors render their own
    content — and the local page URL targets ``entity_id``, the
    ``remote_objects`` row id for ``entity_type="remote"`` activities.
    """
    if activity.entity_type != "remote":
        return None
    payload = activity.payload if isinstance(activity.payload, dict) else {}
    obj = payload.get("object")
    if not isinstance(obj, dict):
        return None
    resource_type = _detect_resource_type(obj)
    if resource_type is None:
        return None
    canonical = obj.get("id")
    return {
        "id": str(activity.entity_id),
        "name": _object_name(obj),
        "resource_type": resource_type,
        "object_type": str(obj.get("type") or "Object"),
        "domain": federation_service.extract_domain(canonical) if isinstance(canonical, str) and canonical else None,
        "image_url": _media_url(obj.get("image"), "image/"),
        "url": f"/remote/{resource_type}/{activity.entity_id}",
        **_music_fields(obj),
    }


def remote_object_display_name(remote_object: RemoteObject) -> Optional[str]:
    """Return the display name for a cached remote object.

    Rendition rows cached before ``_object_name`` learned to prefer the
    embedded track title still carry the "{artist} - {album} - {title}"
    rendition name — normalize at read time so the fix applies to the
    whole cache without a refetch.
    """
    payload = remote_object.payload
    if isinstance(payload, dict) and isinstance(payload.get("track"), dict):
        name = _object_name(payload)
        if name:
            return name
    return remote_object.name


def _music_fields(payload: dict) -> dict:
    track = payload.get("track")
    if not isinstance(track, dict):
        track = payload
    artists = track.get("artists") or track.get("artist_credit") or []
    artist_name: Optional[str] = None
    if isinstance(artists, list) and artists:
        first = artists[0]
        if isinstance(first, dict):
            artist_name = first.get("name") if isinstance(first.get("name"), str) else None
    elif isinstance(artists, dict):
        artist_name = artists.get("name") if isinstance(artists.get("name"), str) else None
    album = track.get("album")
    album_name = album.get("name") if isinstance(album, dict) and isinstance(album.get("name"), str) else None
    duration = payload.get("duration")
    if not isinstance(duration, (int, float)) or isinstance(duration, bool):
        duration = track.get("duration")
    return {
        "duration": int(duration) if isinstance(duration, (int, float)) and not isinstance(duration, bool) else None,
        "artist_name": artist_name,
        "album_name": album_name,
    }


def remote_object_music_fields(row: RemoteObject) -> dict:
    """
    Playback metadata extracted from a cached document payload.

    Funkwhale ``Audio`` documents embed the music entity under ``track``
    (with ``artists``/``album``); bare ``Track`` documents carry the same
    fields at the top level. Returns ``duration`` (seconds), ``artist_name``
    and ``album_name`` — ``None`` when the payload doesn't declare them.
    """
    payload = row.payload if isinstance(row.payload, dict) else {}
    return _music_fields(payload)


# Bounds for collection-closure and container-expansion graph walks over
# cached ``remote_objects`` parent links.
_REMOTE_CLOSURE_DEPTH = 6
_REMOTE_CLOSURE_LIMIT = 2000
_REMOTE_DESCENDANT_LIMIT = 500


async def _remote_seed_ids(session: AsyncSession, user: User) -> set:
    """Remote object ids the user collected directly or favorited."""
    collected = await session.execute(
        select(CollectionItem.item_id).where(
            CollectionItem.user_id == user.id,
            CollectionItem.item_type == "remote",
        )
    )
    favorited = await session.execute(
        select(Favorite.remote_object_id).where(
            Favorite.user_id == user.id,
            Favorite.remote_object_id.is_not(None),
        )
    )
    return {str(i) for i in collected.scalars().all()} | {str(i) for i in favorited.scalars().all() if i is not None}


async def remote_collection_object_ids(session: AsyncSession, user: User) -> set:
    """
    Return the ``remote_objects`` ids visible in the user's collection.

    Seeds are the user's ``item_type="remote"`` collection entries and
    remote favorites. The closure expands to cached children of collected
    containers (a collected album's tracks) and to cached ancestors of every
    collected/child row (a collected track's album and artist), so a single
    collected remote track surfaces its album and artist too — matching how
    local catalog entities render their containers.
    """
    seed_ids = await _remote_seed_ids(session, user)
    if not seed_ids:
        return set()

    seeds = (
        await session.execute(
            select(
                RemoteObject.id,
                RemoteObject.canonical_url,
                RemoteObject.parent_url,
                RemoteObject.media_of_url,
            ).where(
                RemoteObject.id.in_(seed_ids),
                RemoteObject.unavailable_at.is_(None),
            )
        )
    ).all()

    # Fold rendition seeds onto their cached targets — collecting a
    # Funkwhale upload surfaces the ``Track`` it renders instead.
    rendition_targets = {row.media_of_url for row in seeds if row.media_of_url}
    target_by_url = {}
    if rendition_targets:
        target_rows = (
            await session.execute(
                select(RemoteObject.id, RemoteObject.canonical_url, RemoteObject.parent_url).where(
                    RemoteObject.canonical_url.in_(rendition_targets),
                    RemoteObject.unavailable_at.is_(None),
                )
            )
        ).all()
        target_by_url = {row.canonical_url: row for row in target_rows}
    known = {}
    for row in seeds:
        target = target_by_url.get(row.media_of_url) if row.media_of_url else None
        if target is not None:
            known[str(target.id)] = (str(target.canonical_url), target.parent_url)
        else:
            known[str(row.id)] = (str(row.canonical_url), row.parent_url)

    # Descend from collected containers into their cached children.
    frontier_urls = {url for url, _ in known.values()}
    for _ in range(_REMOTE_CLOSURE_DEPTH):
        if not frontier_urls or len(known) >= _REMOTE_CLOSURE_LIMIT:
            break
        children = (
            await session.execute(
                select(
                    RemoteObject.id,
                    RemoteObject.canonical_url,
                    RemoteObject.parent_url,
                    RemoteObject.media_of_url,
                )
                .where(
                    RemoteObject.parent_url.in_(frontier_urls),
                    RemoteObject.unavailable_at.is_(None),
                )
                .limit(_REMOTE_CLOSURE_LIMIT)
            )
        ).all()
        # Fold rendition children onto their cached targets — a collected
        # library surfaces the tracks its uploads render.
        child_targets = {row.media_of_url for row in children if row.media_of_url}
        child_target_by_url = {}
        if child_targets:
            target_rows = (
                await session.execute(
                    select(RemoteObject.id, RemoteObject.canonical_url, RemoteObject.parent_url).where(
                        RemoteObject.canonical_url.in_(child_targets),
                        RemoteObject.unavailable_at.is_(None),
                    )
                )
            ).all()
            child_target_by_url = {row.canonical_url: row for row in target_rows}
        frontier_urls = set()
        for child in children:
            folded = child_target_by_url.get(child.media_of_url) if child.media_of_url else None
            member = folded if folded is not None else child
            key = str(member.id)
            if key not in known:
                known[key] = (str(member.canonical_url), member.parent_url)
                frontier_urls.add(str(member.canonical_url))

    # Ascend from every collected row (and its children) to cached parents.
    frontier_parents = {str(parent) for _, parent in known.values() if parent}
    for _ in range(_REMOTE_CLOSURE_DEPTH):
        if not frontier_parents or len(known) >= _REMOTE_CLOSURE_LIMIT:
            break
        parents = (
            await session.execute(
                select(RemoteObject.id, RemoteObject.canonical_url, RemoteObject.parent_url)
                .where(
                    RemoteObject.canonical_url.in_(frontier_parents),
                    RemoteObject.unavailable_at.is_(None),
                )
                .limit(_REMOTE_CLOSURE_LIMIT)
            )
        ).all()
        frontier_parents = set()
        for parent_row in parents:
            key = str(parent_row.id)
            if key not in known:
                known[key] = (str(parent_row.canonical_url), parent_row.parent_url)
                if parent_row.parent_url:
                    frontier_parents.add(str(parent_row.parent_url))

    return set(known)


async def get_favorited_remote_object_ids(
    session: AsyncSession,
    user: Optional[User],
    remote_object_ids: set,
) -> set:
    """Return the subset of ``remote_object_ids`` the user has favorited."""
    if user is None or not remote_object_ids:
        return set()
    result = await session.execute(
        select(Favorite.remote_object_id).where(
            Favorite.user_id == user.id,
            Favorite.remote_object_id.in_(remote_object_ids),
        )
    )
    return {str(row) for row in result.scalars().all()}


async def remote_activity_ids(session: AsyncSession, canonical_urls: List[str]) -> dict:
    """Map ``{canonical_url: activity_id}`` for materialized remote activities."""
    if not canonical_urls:
        return {}
    rows = (
        await session.execute(
            select(Activity.id, Activity.source_id).where(
                Activity.source_type == "remote",
                Activity.source_id.in_(canonical_urls),
            )
        )
    ).all()
    return {str(source_id): str(activity_id) for activity_id, source_id in rows}


async def ensure_remote_activity(session: AsyncSession, remote_object: RemoteObject) -> Activity:
    """
    Return the ``Activity`` mirror for a cached remote object.

    Objects that arrived inside a federated ``Create``/``Announce`` already
    have one; resources reached only through direct lookup or embedded
    caching get a synthetic ``Create`` wrapper so like/boost/reply/quote work
    the same way as on local content — the interaction service already
    federates ``source_type="remote"`` targets.
    """
    existing = await get_remote_object_activity(session, remote_object)
    if existing is not None:
        return existing
    obj = remote_object.payload if isinstance(remote_object.payload, dict) else {}
    wrapper = {
        "type": "Create",
        "id": remote_object.activity_url or remote_object.canonical_url,
        "actor": remote_object.actor_url,
        "object": obj,
        "published": obj.get("published"),
    }
    return await _materialize_remote_activity(
        session,
        remote_object=remote_object,
        wrapper=wrapper,
        obj=obj,
        actor_url=remote_object.actor_url,
        kind="Create",
    )


# Remote ``resource_type`` values whose cached descendants are tracks.
_CONTAINER_RESOURCE_TYPES = frozenset({"album", "artist", "library", "playlist"})


async def _remote_track_descendants(
    session: AsyncSession,
    row: RemoteObject,
    *,
    limit: int = _REMOTE_DESCENDANT_LIMIT,
) -> List[RemoteObject]:
    """Cached track-shaped descendants of a container remote object."""
    descendants: List[RemoteObject] = []
    frontier = [row.canonical_url]
    for _ in range(_REMOTE_CLOSURE_DEPTH):
        if not frontier or len(descendants) >= limit:
            break
        children = list(
            (
                await session.execute(
                    select(RemoteObject)
                    .where(
                        RemoteObject.parent_url.in_(frontier),
                        RemoteObject.unavailable_at.is_(None),
                        RemoteObject.resource_type.is_not(None),
                    )
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
        frontier = []
        for child in children:
            if child.resource_type == "track":
                descendants.append(child)
            else:
                frontier.append(child.canonical_url)
    return (await _fold_renditions(session, descendants))[:limit]


async def resolve_remote_track_ids(
    session: AsyncSession,
    config: SonghiveConfig,
    object_ids: List[str],
) -> List[str]:
    """
    Resolve ``remote_objects`` ids to track-shaped remote object ids.

    Track resources resolve to themselves; container resources (album,
    artist, library, playlist) expand to their cached track descendants —
    adding a remote album to a playlist adds its tracks. Unknown,
    unavailable, or domain-blocked ids are skipped.
    """
    await _refresh_instance_policies(session)
    rows = list(
        (
            await session.execute(
                select(RemoteObject).where(
                    RemoteObject.id.in_(object_ids),
                    RemoteObject.unavailable_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    # Key by the originally requested id so a rendition id resolves to its
    # cached entity row.
    by_id = {str(orig.id): folded for orig, folded in zip(rows, await _fold_renditions(session, rows))}
    result: List[str] = []
    seen: set = set()

    def _add(row: RemoteObject) -> None:
        key = str(row.id)
        if key not in seen and remote_domain_allowed(row.domain, config):
            seen.add(key)
            result.append(key)

    for oid in object_ids:
        row = by_id.get(str(oid))
        if row is None:
            continue
        if row.resource_type == "track":
            _add(row)
        elif row.resource_type in _CONTAINER_RESOURCE_TYPES:
            for child in await _remote_track_descendants(session, row):
                _add(child)
    return result


# Browse-sort fields the frontend browse lists can request, mapped onto
# the closest ``remote_objects`` column. ``fetched_at`` stands in for the
# local ``*_at`` timestamps; ``artist_name``/``album_title``/
# ``release_year`` have no remote column (they are payload-derived) so
# they fall back to ``fetched_at`` — the client-side merge still positions
# each loaded page by its own sort key.
_REMOTE_OBJECT_SORT_FIELDS: Dict[str, Any] = {
    "name": RemoteObject.name,
    "title": RemoteObject.name,
    "created_at": RemoteObject.fetched_at,
    "updated_at": RemoteObject.fetched_at,
}


async def list_cached_remote_objects(
    session: AsyncSession,
    config: SonghiveConfig,
    *,
    resource_type: Optional[str] = None,
    query: Optional[str] = None,
    user: Optional[User] = None,
    collection_only: bool = False,
    favorites_only: bool = False,
    library_id: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
    sort_by: str = "created_at",
    sort_dir: str = "desc",
) -> tuple[List[RemoteObject], int]:
    """
    Browse cached remote resources — never fetches remotely.

    ``collection_only`` restricts to the user's remote collection closure —
    directly collected rows plus remote favorites, expanded to cached
    children of collected containers and cached ancestors of every member,
    so a collected remote track also surfaces its album and artist.
    ``favorites_only`` restricts to remote favorites; ``library_id``
    restricts to cached remote objects that are members of a local library.
    ``query`` matches name, summary, and canonical URL case-insensitively —
    the remote side of the browse-list search box. Anonymous callers have
    no collection or favorites, so both flags short-circuit to empty for
    them. ``sort_by`` takes the local browse list's field names — the map
    above picks the closest ``remote_objects`` column — and ``sort_dir``
    the direction, so offset pagination walks remote rows in the same
    order the merged view displays them.
    """
    await _refresh_instance_policies(session)
    if (collection_only or favorites_only) and user is None:
        return [], 0

    conditions: List[Any] = [
        RemoteObject.unavailable_at.is_(None),
        RemoteObject.resource_type.is_not(None),
    ]
    if resource_type:
        conditions.append(RemoteObject.resource_type == resource_type)
    if query:
        conditions.append(
            or_(
                ilike_contains(RemoteObject.name, query),
                ilike_contains(RemoteObject.summary, query),
                ilike_contains(RemoteObject.canonical_url, query),
            )
        )
    if user is None:
        conditions.append(RemoteObject.visibility == "public")
    if collection_only:
        assert user is not None
        closure = await remote_collection_object_ids(session, user)
        if not closure:
            return [], 0
        conditions.append(RemoteObject.id.in_(closure))
    if favorites_only:
        assert user is not None
        conditions.append(
            exists().where(
                Favorite.remote_object_id == RemoteObject.id,
                Favorite.user_id == user.id,
            )
        )
    if library_id:
        conditions.append(
            exists().where(
                LibraryTrack.remote_object_id == RemoteObject.id,
                LibraryTrack.library_id == library_id,
            )
        )

    field = _REMOTE_OBJECT_SORT_FIELDS.get(sort_by, RemoteObject.fetched_at)
    direction = sort_dir if sort_dir in ("asc", "desc") else "desc"
    # Nulls sink to the end in either direction, matching the merged list's
    # sort which places keyless remote entries after keyed ones. The id
    # tiebreak keeps the page order stable so offset pagination cannot
    # skip or repeat rows that share the primary key.
    primary = (field.asc() if direction == "asc" else field.desc()).nulls_last()
    secondary = RemoteObject.id.asc() if direction == "asc" else RemoteObject.id.desc()
    stmt = select(RemoteObject).where(*conditions).order_by(primary, secondary)
    rows = [row for row in (await session.execute(stmt)).scalars().all() if remote_domain_allowed(row.domain, config)]
    return rows[offset : offset + limit], len(rows)


async def search_cached_remote_objects(
    session: AsyncSession,
    config: SonghiveConfig,
    term: str,
    *,
    user: Optional[User],
    limit: int = 10,
) -> List[RemoteObject]:
    """
    Match cached remote objects against ``term`` — never fetches remotely.

    Only ``public`` rows are returned to anonymous callers; authenticated
    users additionally see non-public cached objects (inbox-delivered
    content addressed to the instance). Rows on domains that are no longer
    allowed are always filtered out.
    """
    await _refresh_instance_policies(session)
    like = f"%{term}%"
    stmt = (
        select(RemoteObject)
        .where(
            RemoteObject.unavailable_at.is_(None),
            or_(
                RemoteObject.name.ilike(like),
                RemoteObject.summary.ilike(like),
                RemoteObject.content.ilike(like),
                RemoteObject.canonical_url.ilike(like),
            ),
        )
        .order_by(RemoteObject.fetched_at.desc())
        .limit(limit * 4)
    )
    if user is None:
        stmt = stmt.where(RemoteObject.visibility == "public")
    rows = (await session.execute(stmt)).scalars().all()
    return [row for row in rows if remote_domain_allowed(row.domain, config)][:limit]


async def list_cached_actor_activities(
    session: AsyncSession,
    actor_url: str,
    *,
    user: Optional[User],
    limit: int = 20,
    offset: int = 0,
    reveal: bool = False,
) -> tuple[List[Activity], int]:
    """
    List activities materialized for ``actor_url`` — cache only.

    Powers the remote profile's activity tab: strictly already-cached
    objects, no outbox crawling. Non-public rows are filtered through
    ``can_view_activity``. ``reveal`` lifts the followers-only
    moderation gate for this actor (the "show anyway" opt-in on
    limited profiles).
    """
    from .activities import can_view_activity

    stmt = (
        select(Activity)
        .where(
            Activity.source_type == "remote",
            Activity.source_actor == actor_url,
            Activity.entity_type == "remote",
            Activity.deleted_at.is_(None),
        )
        .order_by(Activity.published_at.desc())
        .limit(limit * 4 + offset)
    )
    rows = (await session.execute(stmt)).scalars().all()
    reveal_actor_url = actor_url if reveal else None
    visible = [row for row in rows if await can_view_activity(session, user, row, reveal_actor_url)]
    return visible[offset : offset + limit], len(visible)


async def prune_stale_remote_activities(
    session: AsyncSession,
    *,
    older_than_days: int,
    dry_run: bool = False,
) -> dict:
    """
    Prune stale remote activities and their ``remote_objects`` cache rows.

    A remote activity is stale when its ``published_at`` is older than
    ``older_than_days`` and no *live* activity (local or remote) replies to
    or quotes it — a local like/boost/reply/quote row therefore protects the
    thread it hangs from, since those interactions attach through
    ``in_reply_to_activity_id``. Because remote descendants may themselves
    be prunable, the eligible set is computed to a fixpoint: stale parents
    whose only live children are also being pruned collapse in the same
    run, while a thread carrying any live child keeps its ancestors.

    ``remote_objects`` rows mirrored by a pruned activity (matched on
    ``canonical_url == source_id``) are removed with it — they are always
    retrievable again through explicit remote URL lookup. Bare remote
    resources (tracks/albums/…) without an activity row are never touched.

    Returns counters describing what was (or, for ``dry_run``, would be)
    pruned. The caller owns the transaction.
    """
    from collections import defaultdict

    cutoff = datetime.now(timezone.utc) - timedelta(days=older_than_days)

    stale_rows = (
        await session.execute(
            select(Activity.id, Activity.in_reply_to_activity_id).where(
                Activity.source_type == "remote",
                Activity.deleted_at.is_(None),
                Activity.published_at < cutoff,
            )
        )
    ).all()
    stale = {row.id: row.in_reply_to_activity_id for row in stale_rows}
    result = {
        "older_than_days": older_than_days,
        "cutoff": cutoff.isoformat(),
        "dry_run": dry_run,
        "candidates": 0,
        "pruned_activities": 0,
        "pruned_remote_objects": 0,
    }
    if not stale:
        return result

    live_child_rows = (
        await session.execute(
            select(Activity.id, Activity.in_reply_to_activity_id).where(
                Activity.in_reply_to_activity_id.in_(stale.keys()),
                Activity.deleted_at.is_(None),
            )
        )
    ).all()
    children: dict = defaultdict(list)
    for child_id, parent_id in live_child_rows:
        children[parent_id].append(child_id)

    deletion: set = set()
    progressed = True
    while progressed:
        progressed = False
        for activity_id in stale:
            if activity_id in deletion:
                continue
            if all(child in deletion for child in children.get(activity_id, [])):
                deletion.add(activity_id)
                progressed = True

    result["candidates"] = len(deletion)
    if dry_run or not deletion:
        return result

    source_ids = (await session.execute(select(Activity.source_id).where(Activity.id.in_(deletion)))).scalars().all()
    deleted_objects = await session.execute(
        delete(RemoteObject).where(RemoteObject.canonical_url.in_([sid for sid in source_ids if sid]))
    )
    await session.execute(delete(Activity).where(Activity.id.in_(deletion)))
    await session.flush()

    result["pruned_activities"] = len(deletion)
    result["pruned_remote_objects"] = int(getattr(deleted_objects, "rowcount", 0) or 0)
    return result
