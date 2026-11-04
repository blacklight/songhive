"""
Shared helpers for the federation route modules.
"""

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Optional

from fastapi import HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from pubby import InteractionType
from pubby.collections import (
    ordered_collection,
    ordered_collection_index,
    ordered_collection_page,
)
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ...config.schema import SonghiveConfig
from ...federation import doc_cache, get_stream_url
from ...federation.activities import (
    activity_audience,
    build_activity_object,
    build_tombstone_object,
)
from ...federation.notification_helpers import _object_is_public
from ...federation.serializers import (
    track_to_audio_object,
)
from ...federation.storage import get_federation_storage
from ...models import Activity, Track, Visibility
from ...models.base import get_session
from ...models.moderation import ADMIN_ACTION_SUSPEND, AdminUserModeration
from ...services import activities as activities_service
from ...services.federation import ensure_user_actor
from ...services.payments import access as payment_access
from .._common import document_response
from .profile_pages import _federating_user_or_none

ACTIVITY_JSON = "application/activity+json"
JRD_JSON = "application/jrd+json"
AP_CONTEXT = "https://www.w3.org/ns/activitystreams"

logger = logging.getLogger(__name__)

#: Interaction collections served under ``/objects/{id}/<collection>``.
INTERACTION_COLLECTIONS = ("replies", "likes", "shares", "quotes")

#: ``orderedItems`` per served interaction collection page.
INTERACTION_COLLECTION_PAGE_SIZE = 100

#: ``activities.activity_type`` behind each served collection.
_LOCAL_INTERACTION_TYPES = {
    "replies": "reply",
    "likes": "like",
    "shares": "announce",
    "quotes": "quote",
}

#: Pubby ``InteractionType`` behind each served collection.
_REMOTE_INTERACTION_TYPES = {
    "replies": InteractionType.REPLY,
    "likes": InteractionType.LIKE,
    "shares": InteractionType.BOOST,
    "quotes": InteractionType.QUOTE,
}


def _federation_config(request: Request) -> SonghiveConfig:
    """Return the app config when federation is enabled, otherwise 404."""
    config: SonghiveConfig = request.app.state.config
    if not config.federation.enabled or not config.federation.instance_domain:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return config


def _ordered_collection(collection_id: str, items: list[str]) -> dict[str, Any]:
    return ordered_collection(collection_id, items, context=AP_CONTEXT)


def _paged_collection_index(collection_id: str, total_items: int, page_size: int) -> dict:
    """Return an ``OrderedCollection`` index linking its page documents."""
    return ordered_collection_index(collection_id, total_items, page_size, context=AP_CONTEXT)


def _paged_collection_page(collection_id: str, page: int, total_items: int, items: list, page_size: int) -> dict:
    """Return an ``OrderedCollectionPage`` of a paged collection."""
    return ordered_collection_page(collection_id, page, total_items, items, page_size, context=AP_CONTEXT)


def _page_number(request: Request) -> Optional[int]:
    """Return the normalized ``?page=N`` value — ``None`` when absent."""
    page = request.query_params.get("page")
    if page is None:
        return None
    try:
        return max(1, int(page))
    except ValueError:
        return 1


def _collection_ref_stub(collection_id: str, total_items: int) -> dict:
    """Collection reference embedded on an object doc — mirrors the served index."""
    last_page = max(1, -(-total_items // INTERACTION_COLLECTION_PAGE_SIZE)) if total_items else 1
    return {
        "id": collection_id,
        "type": "OrderedCollection",
        "totalItems": total_items,
        "first": f"{collection_id}?page=1",
        "last": f"{collection_id}?page={last_page}",
    }


def _with_collection_refs(document: Optional[dict], items: dict, config: SonghiveConfig) -> Optional[dict]:
    """
    Attach ``replies``/``likes``/``shares``/``quotes`` collection
    references to a served object document — the mechanism remote
    servers use to discover the interactions an object received.

    Only local documents get refs: a remote-materialized document is
    owned by its origin instance, which advertises its own collections.
    Tombstones carry no collections either — a deleted object has no
    discoverable interactions.
    """
    if not document or document.get("type") == "Tombstone":
        return document
    object_id = document.get("id")
    domain = config.federation.instance_domain
    if not isinstance(object_id, str) or not object_id or not object_id.startswith(f"https://{domain}/"):
        return document
    base = object_id.split("?", 1)[0]
    for coll in INTERACTION_COLLECTIONS:
        document.setdefault(
            coll,
            _collection_ref_stub(f"{base}/{coll}", len(items.get(coll, ()))),
        )
    return document


def _embedded_interaction(source_id: str, actor: str, collection: str, object_url: str) -> dict:
    """Minimal ``Like``/``Announce`` activity for a collection item."""
    activity = {
        "type": "Like" if collection == "likes" else "Announce",
        "actor": actor,
        "object": object_url,
    }
    if source_id:
        activity = {"id": source_id, **activity}
    return activity


async def object_interaction_items(
    db: AsyncSession,
    config: SonghiveConfig,
    *,
    object_url: str,
    activity: Optional[Activity],
    collection: str,
) -> list:
    """
    Build the ``orderedItems`` payload of one interaction collection.

    Local likes/boosts/replies/quotes live as ``Activity`` rows keyed by
    ``in_reply_to_activity_id``; remote ones are pubby
    ``federation_interactions`` records keyed by the object's AP id.
    Remote items already materialized into local rows are deduplicated,
    and only publicly-addressed remote interactions are exposed — the
    same rules the interaction summaries apply.
    """
    items: list = []
    seen_ids: set = set()

    if activity is not None:
        rows = (
            (
                await db.execute(
                    select(Activity)
                    .where(
                        Activity.in_reply_to_activity_id == str(activity.id),
                        Activity.activity_type == _LOCAL_INTERACTION_TYPES[collection],
                        Activity.deleted_at.is_(None),
                        Activity.visibility.in_(_FEDERATING_VISIBILITIES),
                    )
                    .order_by(Activity.published_at.asc())
                )
            )
            .scalars()
            .all()
        )
        for a in rows:
            seen_ids.add(a.source_id)
            if collection in ("replies", "quotes"):
                items.append(a.source_id)
            else:
                items.append(_embedded_interaction(a.source_id, a.source_actor, collection, object_url))

    remote_type = _REMOTE_INTERACTION_TYPES[collection]
    try:
        storage = await asyncio.to_thread(get_federation_storage, config.database)
        remote = await asyncio.to_thread(storage.get_interactions, object_url, remote_type)
    except Exception:
        logger.exception("Failed to fetch remote interactions for %s", object_url)
        remote = []

    for interaction in remote:
        raw = getattr(interaction, "metadata", None) or {}
        if not _object_is_public(raw.get("raw_object")):
            continue
        if collection in ("replies", "quotes"):
            oid = getattr(interaction, "object_id", "") or ""
            if not oid or oid in seen_ids:
                continue
            seen_ids.add(oid)
            items.append(oid)
        else:
            aid = getattr(interaction, "activity_id", "") or ""
            if aid and aid in seen_ids:
                continue
            if aid:
                seen_ids.add(aid)
            items.append(_embedded_interaction(aid, interaction.source_actor_id, collection, object_url))
    return items


_FEDERATING_VISIBILITIES = [v.value for v in Visibility if Visibility.federates(v)]


def _visibility_federates(visibility: str) -> bool:
    try:
        return Visibility.federates(Visibility(visibility))
    except ValueError:
        return False


def _activity_object_document(activity: Activity) -> Optional[dict]:
    """
    Return the ActivityPub document for a stored local activity, or ``None``.

    Soft-deleted activities produce a ``Tombstone`` object matching the shape
    embedded in ``Delete(Tombstone)`` deliveries. Activities whose visibility
    does not federate never left the instance and produce ``None``.
    Everything else comes from ``build_activity_object`` — the stored payload
    (or the ``Create`` envelope's embedded object), or a synthesized ``Note``
    when no payload was recorded.
    """
    if activity.deleted_at is not None:
        return {"@context": AP_CONTEXT, **build_tombstone_object(activity.source_id)}
    if not _visibility_federates(activity.visibility):
        return None
    return build_activity_object(activity)


def _activity_object_response(activity: Activity) -> JSONResponse:
    """Serve the ActivityPub document for a stored local activity, or 404."""
    document = _activity_object_document(activity)
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return JSONResponse(content=document, media_type=ACTIVITY_JSON)


def _document_ttl(config: SonghiveConfig) -> float:
    """The configured TTL for cached dereferenced documents."""
    return config.federation.document_cache_ttl_seconds


def _serve_cached_document(
    request: Request,
    key: tuple,
    ttl: float,
    document: Optional[dict],
    *,
    media_type: str = ACTIVITY_JSON,
) -> Response:
    """
    Serve a dereference document with its cache validators.

    Advertises the cache entry's *remaining* freshness rather than the full
    TTL (a downstream cache must not restart the clock on an aged document)
    and the nginx edge-cache cap via ``X-Accel-Expires``.
    """
    config = request.app.state.config
    return document_response(
        document,
        ttl,
        media_type=media_type,
        freshness=doc_cache.ttl_remaining(key),
        edge_ttl=config.federation.edge_cache_ttl_seconds,
        if_none_match=request.headers.get("if-none-match"),
    )


@dataclass(frozen=True)
class _Rendered:
    """
    A dereference hit: either an AP document to serve or a URL to redirect to.

    Renders return this (or ``None`` for a cacheable 404 miss) instead of a
    bare ``dict | str | None`` union so callers cannot confuse the two.
    """

    document: Optional[dict] = None
    redirect: Optional[str] = None


async def _earliest_track_post(db: AsyncSession, track_id: str) -> Optional[Activity]:
    """
    Return the oldest live local ``create`` activity attached to a track.

    Used as the fallback object for ``GET /tracks/{id}`` when no canonical
    ``Audio`` is published: remote fetches are redirected to the earliest
    surviving share so the track URL keeps resolving to a post.
    """
    result = await db.execute(
        select(Activity)
        .where(
            Activity.entity_type == "track",
            Activity.entity_id == track_id,
            Activity.activity_type == "create",
            Activity.source_type == "local",
            Activity.deleted_at.is_(None),
            Activity.visibility.in_(_FEDERATING_VISIBILITIES),
            ~Activity.owner_user_id.in_(
                select(AdminUserModeration.target_user_id).where(
                    AdminUserModeration.action == ADMIN_ACTION_SUSPEND,
                    AdminUserModeration.target_user_id.is_not(None),
                )
            ),
        )
        .order_by(Activity.published_at.asc(), Activity.id.asc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _track_object_document(db: AsyncSession, track: Track, owner: Any, config: SonghiveConfig) -> Optional[dict]:
    """
    Build the dereferenceable ``Audio`` document for a published track.

    The document extends the object embedded in ``Create`` deliveries with
    the fields remote fetchers require: ``@context`` makes it a valid
    standalone ActivityStreams document (context-less payloads are rejected
    upstream), the ``to``/``cc`` audience lets remote importers classify it
    as a public status instead of a direct-only one, ``library`` names the
    containing federated music library (required by Funkwhale's
    ``UploadSerializer`` — uploads always belong to a library), and
    ``followers`` advertises the object's follower collection so remote
    servers can subscribe to the thread (FEP-efda followable objects).
    """
    if track.artist is None or owner is None or not owner.actor_url or not track.federation_object_id:
        return None

    domain = config.federation.instance_domain
    object_url = f"{owner.actor_url}/objects/{track.federation_object_id}"
    audio_object = track_to_audio_object(
        track,
        track.artist,
        domain,
        get_stream_url(track, domain),
        actor_url=owner.actor_url,
        ap_object_id=object_url,
        library_url=await activities_service._track_library_url(db, track, owner.actor_url, domain),
        sale=await payment_access.effective_gate(db, track),
    )
    if audio_object is None:
        return None

    to, cc = activity_audience(Visibility.PUBLIC, owner.actor_url)
    return {
        "@context": AP_CONTEXT,
        **audio_object,
        "to": to,
        "cc": cc,
        "followers": f"{object_url}/followers",
    }


async def _render_object_document(
    config: SonghiveConfig,
    username: str,
    object_id: str,
) -> Optional[dict]:
    """Render the dereferenceable AP document for ``object_id``, or ``None``.

    Opens its own session: the render may outlive the request that started it
    (waiters share one in-flight render), so it must not borrow a
    request-scoped session whose dependency cleanup would close it mid-query.
    """
    async with get_session() as db:
        user = await _federating_user_or_none(db, username)
        if user is None:
            return None
        ensure_user_actor(user, config)

        result = await db.execute(
            select(Track)
            .options(selectinload(Track.artist), selectinload(Track.audio_file))
            .where(
                Track.federation_object_id == object_id,
                Track.owner_id == str(user.id),
                Track.visibility == Visibility.PUBLIC.value,
            )
        )
        track = result.scalar_one_or_none()
        document: Optional[dict] = None
        activity: Optional[Activity] = None
        if track is not None:
            document = await _track_object_document(db, track, user, config)
        else:
            activity_result = await db.execute(
                select(Activity)
                .options(selectinload(Activity.in_reply_to_activity))
                .where(
                    or_(
                        Activity.local_object_id == object_id,
                        Activity.source_id == object_id,
                    ),
                    Activity.owner_user_id == str(user.id),
                )
            )
            activity = activity_result.scalar_one_or_none()
            if activity is None:
                return None
            document = _activity_object_document(activity)

        # Advertise the object's interaction collections so remote
        # servers can discover its replies/likes/boosts/quotes —
        # skipped for remote-owned and tombstone documents.
        if document and document.get("type") != "Tombstone":
            doc_id = document.get("id")
            if isinstance(doc_id, str) and doc_id.startswith(f"https://{config.federation.instance_domain}/"):
                items = {
                    coll: await object_interaction_items(
                        db,
                        config,
                        object_url=doc_id,
                        activity=activity,
                        collection=coll,
                    )
                    for coll in INTERACTION_COLLECTIONS
                }
                document = _with_collection_refs(document, items, config)
        return document
