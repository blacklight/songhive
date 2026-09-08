"""
Per-user ActivityPub federation routes and WebFinger discovery.
"""

import asyncio
import base64
from html import escape
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ...config.schema import SonghiveConfig
from ...federation import get_actor_url, get_stream_url
from ...federation.activities import (
    activity_audience,
    build_activity_object,
    build_tombstone_object,
)
from ...federation.actors import get_federation_storage, user_to_actor_document
from ...federation.serializers import track_to_audio_object
from ...models import Activity, Track, Visibility
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


def _track_object_document(track: Track, owner: Any, config: SonghiveConfig) -> Optional[dict]:
    """
    Build the dereferenceable ``Audio`` document for a published track.

    The document extends the object embedded in ``Create`` deliveries with
    the fields remote fetchers require: ``@context`` makes it a valid
    standalone ActivityStreams document (context-less payloads are rejected
    upstream), and the ``to``/``cc`` audience lets remote importers classify
    it as a public status instead of a direct-only one.
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
    )
    if audio_object is None:
        return None

    to, cc = activity_audience(Visibility.PUBLIC, owner.actor_url)
    return {
        "@context": AP_CONTEXT,
        **audio_object,
        "to": to,
        "cc": cc,
    }


def _spa_index_path() -> Path:
    """Return the path of the built SPA entry point."""
    return Path(__file__).resolve().parents[2] / "static" / "index.html"


def _spa_response(alternate_url: Optional[str] = None) -> HTMLResponse:
    """
    Serve the SPA shell for browser requests.

    When ``alternate_url`` points at a dereferenceable ActivityPub object,
    the page is annotated with the discovery hints remote servers look for
    when they fetch an HTML page: a ``Link: rel="alternate"`` response header
    and a matching ``<link rel="alternate">`` element in the document head.
    """
    index = _spa_index_path()
    if not index.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    body = index.read_text(encoding="utf-8")
    headers: dict[str, str] = {}
    if alternate_url:
        tag = f'<link rel="alternate" type="{ACTIVITY_JSON}" href="{escape(alternate_url, quote=True)}">'
        body = body.replace("</head>", f"{tag}</head>", 1) if "</head>" in body else f"{body}{tag}"
        headers["Link"] = f'<{alternate_url}>; rel="alternate"; type="{ACTIVITY_JSON}"'

    return HTMLResponse(content=body, headers=headers)


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
    """
    Return the ActivityPub document for a federated object.

    Public tracks resolve through ``Track.federation_object_id`` to their
    ``Audio`` object; activities resolve through ``Activity.local_object_id``
    or ``Activity.source_id`` and are served as their stored payload (or a
    synthesized ``Note`` when no payload was recorded). Soft-deleted
    activities are served as ``Tombstone`` objects matching the shape
    embedded in ``Delete(Tombstone)`` deliveries. Live activities are only
    served for visibilities that federate — ``private`` and ``local``
    objects never left the instance and answer 404.
    """
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
    if track is not None:
        document = _track_object_document(track, user, config)
        if document is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

        return JSONResponse(content=document, media_type=ACTIVITY_JSON)

    activity_result = await db.execute(
        select(Activity).where(
            or_(
                Activity.local_object_id == object_id,
                Activity.source_id == object_id,
            ),
            Activity.owner_user_id == str(user.id),
        )
    )
    activity = activity_result.scalar_one_or_none()
    if activity is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    if activity.deleted_at is not None:
        tombstone = {"@context": AP_CONTEXT, **build_tombstone_object(activity.source_id)}
        return JSONResponse(content=tombstone, media_type=ACTIVITY_JSON)

    try:
        federates = Visibility.federates(Visibility(activity.visibility))
    except ValueError:
        federates = False
    if not federates:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    return JSONResponse(content=build_activity_object(activity), media_type=ACTIVITY_JSON)


@router.get("/tracks/{track_id}")
async def get_track_page(
    track_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Dereference the canonical track page URL.

    ``/tracks/{id}`` is primarily a Vue SPA route, but it is also advertised
    as the ``text/html`` ``url`` of every published ``Audio`` object, so
    remote servers fetch it when a user pastes the link into a remote search
    box (e.g. Mastodon's URL lookup). Clients accepting an ActivityStreams
    media type receive the track's ``Audio`` object; browsers receive the SPA
    shell annotated with ``Link``/``<link rel="alternate">`` discovery hints
    pointing at the object.

    The object is only served while the track is published to the fediverse
    (``federation_object_id`` set): unpublished tracks keep answering 404 so
    a remote fetch cannot resurrect a retracted post under a different id.
    """
    config = _federation_config(request)
    result = await db.execute(
        select(Track)
        .options(selectinload(Track.artist), selectinload(Track.audio_file))
        .where(
            Track.id == track_id,
            Track.visibility == Visibility.PUBLIC.value,
            Track.federation_object_id.isnot(None),
        )
    )
    track = result.scalar_one_or_none()
    owner: Any = track.owner if track is not None else None
    if track is None or track.artist is None or owner is None or not owner.is_active:
        track = None
        owner = None
    else:
        ensure_user_actor(owner, config)

    if _accepts_activitypub(request):
        if track is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
        document = _track_object_document(track, owner, config)
        if document is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
        return JSONResponse(content=document, media_type=ACTIVITY_JSON)

    alternate_url = f"{owner.actor_url}/objects/{track.federation_object_id}" if track is not None else None
    return _spa_response(alternate_url)


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
