"""
Per-user ActivityPub federation routes and WebFinger discovery.
"""

import asyncio
import base64
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ...config.schema import SonghiveConfig
from ...federation._common import get_actor_url, get_stream_url
from ...federation.actors import get_federation_storage, user_to_actor_document
from ...federation.serializers import track_to_audio_object
from ...models._enums import Visibility
from ...models.track import Track
from ...services.auth import get_user_by_username
from ...services.federation import ensure_user_actor, extract_domain, is_domain_allowed
from ...tasks.federation import process_incoming
from ..deps import get_db

router = APIRouter(include_in_schema=False)

ACTIVITY_JSON = "application/activity+json"
JRD_JSON = "application/jrd+json"
LD_JSON = "application/ld+json"
AP_CONTEXT = "https://www.w3.org/ns/activitystreams"


def _federation_config(request: Request) -> SonghiveConfig:
    """Return the app config when federation is enabled, otherwise 404."""
    config: SonghiveConfig = request.app.state.config
    if not config.federation.enabled or not config.federation.instance_domain:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return config


def _accepts_activitypub(request: Request) -> bool:
    """Return True when the client requests an ActivityPub document."""
    accept = request.headers.get("accept", "")
    return ACTIVITY_JSON in accept or LD_JSON in accept


def _ordered_collection(collection_id: str, items: list[str]) -> dict[str, Any]:
    return {
        "@context": AP_CONTEXT,
        "id": collection_id,
        "type": "OrderedCollection",
        "totalItems": len(items),
        "orderedItems": items,
    }


async def _get_active_user(db: AsyncSession, username: str) -> Any:
    """Return an active user or raise 404."""
    user = await get_user_by_username(db, username)
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return user


@router.get("/users/{username}")
async def get_actor(
    username: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Return a user's ActivityPub Person document."""
    config = _federation_config(request)
    user = await _get_active_user(db, username)
    ensure_user_actor(user, config)
    actor = user_to_actor_document(user, config.federation.instance_domain)
    return JSONResponse(content=actor, media_type=ACTIVITY_JSON)


@router.get("/@{username}")
async def get_user_alias(
    username: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Resolve a Mastodon-like alias to an actor or local profile."""
    config = _federation_config(request)
    user = await _get_active_user(db, username)

    if _accepts_activitypub(request):
        ensure_user_actor(user, config)
        actor = user_to_actor_document(user, config.federation.instance_domain)
        return JSONResponse(content=actor, media_type=ACTIVITY_JSON)

    return RedirectResponse(url=f"/api/v1/users/{username}", status_code=status.HTTP_307_TEMPORARY_REDIRECT)


@router.get("/users/{username}/objects/{object_id}")
async def get_object(
    username: str,
    object_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Return a public track's ActivityPub Audio object."""
    config = _federation_config(request)
    user = await _get_active_user(db, username)
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
    if track is None or track.artist is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    actor_url = user.actor_url
    object_url = f"{actor_url}/objects/{object_id}"
    stream_url = get_stream_url(track, config.federation.instance_domain)
    audio_object = track_to_audio_object(
        track,
        track.artist,
        config.federation.instance_domain,
        stream_url,
        actor_url=actor_url,
        ap_object_id=object_url,
    )
    if audio_object is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    return JSONResponse(content=audio_object, media_type=ACTIVITY_JSON)


@router.post("/users/{username}/inbox")
async def post_inbox(
    username: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Accept and queue a per-user inbox ActivityPub activity."""
    config = _federation_config(request)
    user = await get_user_by_username(db, username)
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    try:
        body = await request.body()
        activity = await request.json()
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid JSON") from e

    actor_ref = activity.get("actor", "") if isinstance(activity, dict) else ""
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


@router.get("/users/{username}/outbox")
async def get_outbox(
    username: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Return the user's outbox collection."""
    config = _federation_config(request)
    await _get_active_user(db, username)
    actor_url = get_actor_url(config.federation.instance_domain, username)
    return JSONResponse(
        content=_ordered_collection(f"{actor_url}/outbox", []),
        media_type=ACTIVITY_JSON,
    )


@router.get("/users/{username}/followers")
async def get_followers(
    username: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Return the user's followers collection."""
    config = _federation_config(request)
    await _get_active_user(db, username)
    storage = await asyncio.to_thread(get_federation_storage, config.database.url)
    actor_url = get_actor_url(config.federation.instance_domain, username)
    followers = await asyncio.to_thread(storage.get_followers, actor_id=actor_url)
    return JSONResponse(
        content=_ordered_collection(
            f"{actor_url}/followers",
            [f.actor_id for f in followers],
        ),
        media_type=ACTIVITY_JSON,
    )


@router.get("/users/{username}/following")
async def get_following(
    username: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Return the user's following collection."""
    config = _federation_config(request)
    await _get_active_user(db, username)
    actor_url = get_actor_url(config.federation.instance_domain, username)
    return JSONResponse(
        content=_ordered_collection(f"{actor_url}/following", []),
        media_type=ACTIVITY_JSON,
    )


@router.get("/.well-known/webfinger")
async def webfinger(
    request: Request,
    db: AsyncSession = Depends(get_db),
    resource: Optional[str] = None,
):
    """
    WebFinger discovery for local users and the instance actor.
    """
    if resource is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="resource parameter is required")

    config = _federation_config(request)
    domain = config.federation.instance_domain

    if not resource.lower().startswith("acct:"):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    rest = resource[5:]
    if rest.startswith("@"):
        rest = rest[1:]
    if "@" not in rest:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    name, resource_domain = rest.split("@", 1)
    if resource_domain.lower() != domain.lower():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    instance_username = config.federation.instance_name.lower().replace(" ", "-")
    if name.lower() == instance_username:
        actor_url = f"https://{domain}/ap/actor"
        return JSONResponse(
            content={
                "subject": f"acct:{instance_username}@{domain}",
                "aliases": [actor_url],
                "links": [
                    {
                        "rel": "self",
                        "type": ACTIVITY_JSON,
                        "href": actor_url,
                    },
                    {
                        "rel": "http://webfinger.net/rel/profile-page",
                        "type": "text/html",
                        "href": actor_url,
                    },
                ],
            },
            media_type=JRD_JSON,
        )

    user = await get_user_by_username(db, name.lower())
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    actor_url = get_actor_url(domain, user.username)
    return JSONResponse(
        content={
            "subject": f"acct:{user.username}@{domain}",
            "aliases": [actor_url],
            "links": [
                {
                    "rel": "self",
                    "type": ACTIVITY_JSON,
                    "href": actor_url,
                },
                {
                    "rel": "http://webfinger.net/rel/profile-page",
                    "type": "text/html",
                    "href": actor_url,
                },
            ],
        },
        media_type=JRD_JSON,
    )
