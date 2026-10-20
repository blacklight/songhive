"""
Inbox, outbox, and follower/following federation routes.
"""

import asyncio
import base64
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ...federation import doc_cache, get_actor_url
from ...federation.activities import (
    build_outbox_activity,
)
from ...federation.actors import get_federation_storage
from ...models import Activity, User, Visibility
from ...models.base import get_session
from ...models.follow import FOLLOW_STATE_ACCEPTED, Follow
from ...services.auth import get_user_by_username
from ...services.federation import extract_domain, is_domain_allowed
from ...tasks.federation import process_incoming
from ..deps import get_db
from .federation_common import (
    _document_ttl,
    _federating_user_or_none,
    _federation_config,
    _ordered_collection,
    _page_number,
    _paged_collection_index,
    _paged_collection_page,
    _serve_cached_document,
)

router = APIRouter(include_in_schema=False)


async def _enqueue_incoming(request: Request, username: Optional[str]) -> JSONResponse:
    """
    Validate and queue an inbound ActivityPub activity for processing.

    Shared by the per-user and shared inbox endpoints: rejects malformed
    JSON and senders from blocked/non-allowed instances, then hands the
    activity (with the raw request for signature verification) to the
    ``process_incoming`` task. Returns the 202 acknowledgement.
    """
    config = _federation_config(request)
    try:
        body = await request.body()
        activity = await request.json()
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid JSON") from e

    actor_ref: Any = activity.get("actor", "") if isinstance(activity, dict) else ""
    if isinstance(actor_ref, list) and actor_ref:
        actor_ref = actor_ref[0]
    if isinstance(actor_ref, dict):
        actor_ref = actor_ref.get("id", "")

    sender_domain = extract_domain(actor_ref) if isinstance(actor_ref, str) else ""
    if not sender_domain or not is_domain_allowed(sender_domain, config):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)

    process_incoming.delay(  # type: ignore
        activity,
        username=username,
        method=request.method,
        path=request.url.path,
        headers=dict(request.headers),
        body_b64=base64.b64encode(body).decode("ascii"),
    )  # type: ignore
    return JSONResponse(content={"status": "ok"}, status_code=status.HTTP_202_ACCEPTED)


@router.post("/ap/inbox")
async def post_shared_inbox(request: Request):
    """
    Accept and queue a shared-inbox ActivityPub activity.

    Takes precedence over pubby's own ``/ap/inbox`` route so deliveries
    addressed to local users still flow through ``process_incoming`` —
    reply materialization and per-recipient notifications — instead of
    ending at interaction storage.
    """
    return await _enqueue_incoming(request, username=None)


@router.post("/users/{username}/inbox")
async def post_inbox(
    username: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Accept and queue a per-user inbox ActivityPub activity."""
    _federation_config(request)
    user = await get_user_by_username(db, username)
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    return await _enqueue_incoming(request, username=username)


# Activity types listed in the actor's outbox — ``create`` and ``announce``
# match what remote consumers (Funkwhale outbox scans, Mastodon profile
# backfill) expect; reactions stay off the public outbox.
_OUTBOX_ACTIVITY_TYPES = ("create", "announce")
_OUTBOX_PAGE_SIZE = 20


def _outbox_conditions(user: User) -> tuple:
    """Return the predicates selecting an owner's publicly listed activities."""
    return (
        Activity.source_type == "local",
        Activity.owner_user_id == str(user.id),
        Activity.deleted_at.is_(None),
        Activity.retracted.is_(False),
        Activity.visibility == Visibility.PUBLIC.value,
        Activity.activity_type.in_(_OUTBOX_ACTIVITY_TYPES),
    )


@router.get("/users/{username}/outbox")
async def get_outbox(
    username: str,
    request: Request,
):
    """
    Return the user's outbox collection.

    The index document (``?page`` absent) links paged
    ``OrderedCollectionPage``s whose ``orderedItems`` carry the full
    activity envelopes — the shape Funkwhale scans for ``Audio`` uploads
    and Mastodon-compatible servers use for profile backfill. Only
    ``public`` ``create``/``announce`` activities of the actor are listed.
    """
    config = _federation_config(request)
    ttl = _document_ttl(config)
    page_num = _page_number(request)
    outbox_url = f"{get_actor_url(config.federation.instance_domain, username)}/outbox"

    async def _render() -> Optional[dict]:
        async with get_session() as db:
            user = await _federating_user_or_none(db, username)
            if user is None:
                return None
            conditions = _outbox_conditions(user)
            total = (await db.execute(select(func.count(Activity.id)).where(*conditions))).scalar_one()
            if page_num is None:
                return _paged_collection_index(outbox_url, total, _OUTBOX_PAGE_SIZE)
            rows = (
                (
                    await db.execute(
                        select(Activity)
                        .options(
                            selectinload(Activity.mentions),
                            selectinload(Activity.in_reply_to_activity),
                        )
                        .where(*conditions)
                        .order_by(Activity.published_at.desc(), Activity.id.desc())
                        .offset((page_num - 1) * _OUTBOX_PAGE_SIZE)
                        .limit(_OUTBOX_PAGE_SIZE)
                    )
                )
                .scalars()
                .all()
            )
            items = [doc for doc in (build_outbox_activity(row) for row in rows) if doc is not None]
            return _paged_collection_page(outbox_url, page_num, total, items, _OUTBOX_PAGE_SIZE)

    document = await doc_cache.get_or_render(("coll", username, "outbox", page_num), ttl, _render)
    return _serve_cached_document(request, ("coll", username, "outbox", page_num), ttl, document)


@router.get("/users/{username}/activities/{activity_id}")
async def get_activity_document(
    username: str,
    activity_id: str,
    request: Request,
):
    """
    Dereference an activity envelope id delivered to remote inboxes.

    ``{actor}/activities/{uuid}`` envelopes minted at delivery time are
    recorded on ``Activity.ap_activity_id``; payload-less activities are
    dereferenceable under their row id, matching the id
    ``build_outbox_activity`` synthesizes for them.
    """
    config = _federation_config(request)
    ttl = _document_ttl(config)

    async def _render() -> Optional[dict]:
        async with get_session() as db:
            user = await _federating_user_or_none(db, username)
            if user is None:
                return None
            result = await db.execute(
                select(Activity)
                .options(
                    selectinload(Activity.mentions),
                    selectinload(Activity.in_reply_to_activity),
                )
                .where(
                    Activity.owner_user_id == str(user.id),
                    Activity.source_type == "local",
                    Activity.deleted_at.is_(None),
                    Activity.retracted.is_(False),
                    or_(Activity.id == activity_id, Activity.ap_activity_id == activity_id),
                )
            )
            activity = result.scalar_one_or_none()
            if activity is None:
                return None
            return build_outbox_activity(activity)

    document = await doc_cache.get_or_render(("actenv", username, activity_id), ttl, _render)
    return _serve_cached_document(request, ("actenv", username, activity_id), ttl, document)


@router.get("/users/{username}/followers")
async def get_followers(
    username: str,
    request: Request,
):
    """Return the user's followers collection."""
    config = _federation_config(request)
    ttl = _document_ttl(config)

    async def _render() -> Optional[dict]:
        async with get_session() as db:
            if await _federating_user_or_none(db, username) is None:
                return None
        storage = await asyncio.to_thread(get_federation_storage, config.database)
        actor_url = get_actor_url(config.federation.instance_domain, username)
        followers = await asyncio.to_thread(storage.get_followers, actor_id=actor_url)
        return _ordered_collection(
            f"{actor_url}/followers",
            [f.actor_id for f in followers],
        )

    document = await doc_cache.get_or_render(("coll", username, "followers"), ttl, _render)
    return _serve_cached_document(request, ("coll", username, "followers"), ttl, document)


@router.get("/users/{username}/following")
async def get_following(
    username: str,
    request: Request,
):
    """Return the user's following collection."""
    config = _federation_config(request)
    ttl = _document_ttl(config)

    async def _render() -> Optional[dict]:
        async with get_session() as db:
            user = await _federating_user_or_none(db, username)
            if user is None:
                return None
            follows = (
                (
                    await db.execute(
                        select(Follow)
                        .where(
                            Follow.user_id == str(user.id),
                            Follow.state == FOLLOW_STATE_ACCEPTED,
                        )
                        .order_by(Follow.created_at.desc())
                    )
                )
                .scalars()
                .all()
            )
            # Object-scoped follows (remote libraries, thread
            # subscriptions) share the table — only actor follows belong
            # in the ``following`` collection.
            actor_urls = [
                f.target_actor_url
                for f in follows
                if (f.actor_data or {}).get("type")
                in (None, "Person", "Service", "Group", "Organization", "Application")
            ]
            actor_url = get_actor_url(config.federation.instance_domain, username)
            return _ordered_collection(f"{actor_url}/following", actor_urls)

    document = await doc_cache.get_or_render(("coll", username, "following"), ttl, _render)
    return _serve_cached_document(request, ("coll", username, "following"), ttl, document)
