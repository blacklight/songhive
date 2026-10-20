"""
Shared helpers for the federation route modules.
"""

from dataclasses import dataclass
from typing import Any, Optional

from fastapi import HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
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
from ...federation.serializers import (
    track_to_audio_object,
)
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
        if track is not None:
            return await _track_object_document(db, track, user, config)

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
        return _activity_object_document(activity)
