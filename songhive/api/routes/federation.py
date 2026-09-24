"""
Per-user ActivityPub federation routes and WebFinger discovery.
"""

import asyncio
import base64
from typing import Any, Iterable, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ...config.schema import SonghiveConfig
from ...federation import get_actor_url, get_stream_url
from ...federation.activities import (
    activity_audience,
    build_activity_object,
    build_tombstone_object,
)
from ...federation.actors import get_federation_storage
from ...federation.serializers import (
    MUSIC_ENTITY_CONTEXT,
    album_to_music_object,
    artist_to_music_object,
    library_to_music_object,
    music_collection_page,
    track_to_audio_object,
)
from ...models import Activity, Album, Artist, Library, LibraryTrack, Track, User, Visibility
from ...models.moderation import ADMIN_ACTION_SUSPEND, AdminUserModeration
from ...services import activities as activities_service
from ...services import moderation as moderation_service
from ...services.auth import get_user_by_id, get_user_by_username
from ...services.federation import ensure_user_actor, extract_domain, is_domain_allowed
from ...services.storage import StorageService
from ...tasks.federation import process_incoming
from ..deps import get_config, get_current_user_optional, get_db, get_storage_service
from ..semantic_meta import entity_head_tags
from .instance import _admin_users
from .profile_pages import _accepts_activitypub, _accepts_html, _get_active_user, _spa_response

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


def _ordered_collection(collection_id: str, items: list[str]) -> dict[str, Any]:
    return {
        "@context": AP_CONTEXT,
        "id": collection_id,
        "type": "OrderedCollection",
        "totalItems": len(items),
        "orderedItems": items,
    }


_FEDERATING_VISIBILITIES = [v.value for v in Visibility if Visibility.federates(v)]


def _visibility_federates(visibility: str) -> bool:
    try:
        return Visibility.federates(Visibility(visibility))
    except ValueError:
        return False


def _activity_object_response(activity: Activity) -> JSONResponse:
    """
    Serve the ActivityPub document for a stored local activity.

    Soft-deleted activities answer with a ``Tombstone`` object matching the
    shape embedded in ``Delete(Tombstone)`` deliveries. Activities whose
    visibility does not federate never left the instance and answer 404.
    Everything else is served through ``build_activity_object`` — the
    stored payload (or the ``Create`` envelope's embedded object), or a
    synthesized ``Note`` when no payload was recorded.
    """
    if activity.deleted_at is not None:
        tombstone = {"@context": AP_CONTEXT, **build_tombstone_object(activity.source_id)}
        return JSONResponse(content=tombstone, media_type=ACTIVITY_JSON)

    if not _visibility_federates(activity.visibility):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    return JSONResponse(content=build_activity_object(activity), media_type=ACTIVITY_JSON)


async def _get_federating_user(db: AsyncSession, username: str) -> Any:
    """Return an active, non-suspended user or raise 404."""
    user = await _get_active_user(db, username)
    if await moderation_service.user_is_suspended(db, str(user.id)):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return user


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

    Object URLs double as the objects' own ``url`` (e.g. a ``Note`` share's
    permalink on remote servers), so clients not accepting an
    ActivityStreams media type are redirected to the SPA: a track-resolved
    object goes to the track page, an activity-resolved object to the
    activity's own ``/activities/{id}`` page.
    """
    config = _federation_config(request)
    user = await _get_federating_user(db, username)
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
        if not _accepts_activitypub(request):
            # The object's ``url`` is its own dereferenceable id: browsers
            # opening it (e.g. a remote status's "open original" link) land
            # on the track's page.
            return RedirectResponse(url=f"/tracks/{track.id}", status_code=status.HTTP_307_TEMPORARY_REDIRECT)
        document = await _track_object_document(db, track, user, config)
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

    if not _accepts_activitypub(request):
        # Shares carry their own object id as ``url``; redirect browsers to
        # the activity's own page instead of serving raw JSON.
        return RedirectResponse(
            url=f"/activities/{activity.id}",
            status_code=status.HTTP_307_TEMPORARY_REDIRECT,
        )

    return _activity_object_response(activity)


@router.get("/users/{username}/objects/{object_id}/followers")
async def get_object_followers(
    username: str,
    object_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Return the followers collection of a federated object.

    Remote actors may ``Follow`` a local object rather than an actor —
    e.g. Friendica sends ``Follow`` on a thread's root item to subscribe
    to the conversation (FEP-efda followable objects). Pubby stores those
    rows scoped to the object's id; this collection dereferences them so
    the ``followers`` field advertised on served object documents stays
    resolvable. Only objects that ``get_object`` would serve answer — the
    collection follows the object's own dereferenceability.
    """
    config = _federation_config(request)
    user = await _get_federating_user(db, username)
    ensure_user_actor(user, config)
    object_url = f"{user.actor_url}/objects/{object_id}"
    wanted = {object_url}
    track_id = await db.scalar(
        select(Track.id).where(
            Track.federation_object_id == object_id,
            Track.owner_id == str(user.id),
            Track.visibility == Visibility.PUBLIC.value,
        )
    )
    if track_id is None:
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
        if activity is None or activity.deleted_at is not None or not _visibility_federates(activity.visibility):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
        if activity.source_id:
            wanted.add(activity.source_id)

    storage = await asyncio.to_thread(get_federation_storage, config.database.url)
    followers = await asyncio.to_thread(storage.get_followers_of_targets, wanted)
    return JSONResponse(
        content=_ordered_collection(
            f"{object_url}/followers",
            [f.actor_id for f in followers],
        ),
        media_type=ACTIVITY_JSON,
    )


@router.get("/users/{username}/quote_authorizations/{auth_id:path}")
async def get_quote_authorization(
    username: str,
    auth_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Dereference a ``QuoteAuthorization`` issued for this actor (FEP-044f).

    Pubby's inbox processor auto-approves incoming ``QuoteRequest``
    activities and stores the authorization under the addressed actor's URL
    — ``{actor_url}/quote_authorizations/{id}`` — so remote servers can
    fetch it to clear the pending state on their quote posts. Requests for
    the instance actor's authorizations are served by pubby's own adapter
    route (``/ap/actor/quote_authorizations/{id}``).
    """
    config = _federation_config(request)
    user = await _get_federating_user(db, username)
    ensure_user_actor(user, config)
    storage = await asyncio.to_thread(get_federation_storage, config.database.url)
    document = await asyncio.to_thread(
        storage.get_quote_authorization,
        f"{user.actor_url}/quote_authorizations/{auth_id}",
    )
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return JSONResponse(content=document, media_type=ACTIVITY_JSON)


@router.get("/activities/{activity_id}")
async def get_activity_page(
    activity_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Dereference the activity permalink URL.

    ``/activities/{id}`` is primarily a Vue SPA route, but it is also the
    URL the frontend offers for copying (``ActivityCard``'s permalink), so
    remote servers fetch it when a user pastes the link into a remote
    search box (e.g. Mastodon's URL lookup). Clients accepting an
    ActivityStreams media type receive the same document the canonical
    ``/users/{username}/objects/{id}`` route serves for local activities;
    remote-sourced activities redirect (303 See Other) to their origin's
    object id, which stays authoritative. Browsers receive the SPA shell
    annotated with a ``Link``/``<link rel="alternate">`` discovery hint
    pointing at the canonical object URL.

    Like the object route, a local activity is only dereferenceable while
    its owner is an active local user. Browser requests always receive the
    SPA shell — including for unknown or non-dereferenceable ids, since the
    SPA renders its own not-found state.
    """
    _federation_config(request)
    result = await db.execute(select(Activity).where(Activity.id == activity_id))
    activity = result.scalar_one_or_none()

    remote = activity is not None and activity.source_type != "local"
    owner = None
    if activity is not None and not remote and activity.owner_user_id:
        owner = await get_user_by_id(db, activity.owner_user_id)
    owner_suspended = owner is not None and await moderation_service.user_is_suspended(db, str(owner.id))
    dereferenceable = remote or (owner is not None and owner.is_active and not owner_suspended)
    federates = remote or (activity is not None and _visibility_federates(activity.visibility))

    if _accepts_activitypub(request):
        if activity is None or not dereferenceable:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
        if remote:
            # Remote objects are authoritative on their origin instance.
            return RedirectResponse(url=activity.source_id, status_code=status.HTTP_303_SEE_OTHER)
        return _activity_object_response(activity)

    alternate_url = activity.source_id if activity is not None and dereferenceable and federates else None
    return _spa_response(alternate_url)


@router.get("/tracks/{track_id}")
async def get_track_page(
    track_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: Optional[User] = Depends(get_current_user_optional),
    storage: StorageService = Depends(get_storage_service),
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
    (``federation_object_id`` set). When no ``Audio`` exists, remote fetches
    are instead redirected (303 See Other) to the track's earliest surviving
    local share — the object-dereference route then serves that activity's
    ``Note`` document — so the track URL still resolves to a post. Tracks
    with neither a published object nor live shares answer 404, so a remote
    fetch cannot resurrect a retracted post under a different id — unless
    the client also accepts HTML (card crawlers offer both media types),
    in which case it receives the SPA page like any browser request.
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
    if (
        track is None
        or track.artist is None
        or owner is None
        or not owner.is_active
        or await moderation_service.user_is_suspended(db, str(owner.id))
    ):
        track = None
        owner = None
    else:
        ensure_user_actor(owner, config)

    if _accepts_activitypub(request):
        document = await _track_object_document(db, track, owner, config) if track is not None else None
        if document is not None:
            return JSONResponse(content=document, media_type=ACTIVITY_JSON)
        share = await _earliest_track_post(db, track_id)
        if share is not None:
            return RedirectResponse(url=share.source_id, status_code=status.HTTP_303_SEE_OTHER)
        # Clients that also accept HTML (e.g. Mastodon's card crawler, which
        # offers activity+json + text/html) still get the SPA page below —
        # only pure ActivityPub dereferences 404 on an unfederated track.
        if not _accepts_html(request):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    alternate_url = f"{owner.actor_url}/objects/{track.federation_object_id}" if track is not None else None
    og_tags = await entity_head_tags(request, db, user, storage, "track", track_id)
    return _spa_response(alternate_url, extra_tags=og_tags)


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


@router.get("/users/{username}/outbox")
async def get_outbox(
    username: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Return the user's outbox collection."""
    config = _federation_config(request)
    await _get_federating_user(db, username)
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
    await _get_federating_user(db, username)
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
    await _get_federating_user(db, username)
    actor_url = get_actor_url(config.federation.instance_domain, username)
    return JSONResponse(
        content=_ordered_collection(f"{actor_url}/following", []),
        media_type=ACTIVITY_JSON,
    )


@router.get("/nodeinfo/2.1")
@router.get("/nodeinfo/2.0")
@router.get("/nodeinfo/2.1.json")
@router.get("/nodeinfo/2.0.json")
async def nodeinfo_document(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Return the NodeInfo document enriched with instance contact metadata.

    Songhive's router is mounted before pubby's bindings, so these routes
    take precedence over the plain documents registered by the pubby
    adapters. The base document still comes from the federation handler
    (usage stats, software name/version); ``metadata`` additionally carries
    the node name/description, the configured contact person
    (``maintainer``) and the actor URLs of the active admin accounts
    (``staffAccounts``), matching the conventions used by PeerTube and
    Friendica.
    """
    config = _federation_config(request)
    handler = getattr(request.app.state, "federation_handler", None)
    if handler is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    # ``get_nodeinfo_document`` performs synchronous storage calls.
    document = await asyncio.to_thread(handler.get_nodeinfo_document)
    metadata = document.setdefault("metadata", {})
    metadata["nodeName"] = config.federation.instance_name
    metadata["nodeDescription"] = config.federation.instance_description

    maintainer = {
        key: value
        for key, value in (
            ("name", config.federation.contact_name),
            ("email", config.federation.contact_email),
            ("url", config.federation.contact_url),
        )
        if value
    }
    if maintainer:
        metadata["maintainer"] = maintainer

    domain = config.federation.instance_domain
    admins = await _admin_users(db)
    metadata["staffAccounts"] = [admin.actor_url or get_actor_url(domain, admin.username) for admin in admins]

    return JSONResponse(content=document)


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
    if await moderation_service.user_is_suspended(db, str(user.id)):
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


# ---------------------------------------------------------------------------
# Federated music entities (Funkwhale-compatible dialect)
#
# Songhive artists/albums/libraries are published as dereferenceable
# ActivityPub music documents under ``/artists/{id}``, ``/albums/{id}`` and
# ``/libraries/{id}`` so remote instances — Funkwhale and other Songhive
# instances alike — can resolve, search and follow them. A user's implicit
# library of public tracks is served under ``{actor_url}/library``.
# ---------------------------------------------------------------------------

_LIBRARY_PAGE_SIZE = 100


def _instance_actor_url(config: SonghiveConfig) -> str:
    return f"https://{config.federation.instance_domain}/ap/actor"


def _owner_actor_url(owner: Optional[User], config: SonghiveConfig) -> str:
    if owner is not None:
        ensure_user_actor(owner, config)
        if owner.actor_url:
            return owner.actor_url
    return _instance_actor_url(config)


def _audio_items(
    tracks: Iterable[Track],
    domain: str,
    actor_url: str,
    library_url: str,
) -> list[dict]:
    return [
        doc
        for track in tracks
        if (
            doc := track_to_audio_object(
                track,
                track.artist,
                domain,
                actor_url=actor_url,
                library_url=library_url,
            )
        )
        is not None
    ]


def _library_track_query(library_id: str):
    return (
        select(Track)
        .join(LibraryTrack, LibraryTrack.track_id == Track.id)
        .where(
            LibraryTrack.library_id == library_id,
            Track.visibility == Visibility.PUBLIC.value,
        )
        .options(
            selectinload(Track.artist),
            selectinload(Track.album),
            selectinload(Track.audio_file),
            selectinload(Track.genre_associations),
        )
        .order_by(LibraryTrack.created_at.desc())
    )


def _library_track_count(library_id: str):
    return (
        select(func.count(Track.id))
        .join(LibraryTrack, LibraryTrack.track_id == Track.id)
        .where(
            LibraryTrack.library_id == library_id,
            Track.visibility == Visibility.PUBLIC.value,
        )
    )


async def _followers_collection(
    storage: Any,
    object_url: str,
) -> JSONResponse:
    """Return an ``OrderedCollection`` of an object's follower actor ids."""
    followers = await storage.get_followers_of_targets({object_url})
    actor_ids = sorted({f.actor_id for f in followers})
    return JSONResponse(
        {
            "@context": MUSIC_ENTITY_CONTEXT,
            "id": f"{object_url}/followers",
            "type": "OrderedCollection",
            "totalItems": len(actor_ids),
            "orderedItems": actor_ids,
        },
        media_type=ACTIVITY_JSON,
    )


@router.get("/artists/{artist_id}")
async def get_artist_document(
    artist_id: str,
    request: Request,
    config: SonghiveConfig = Depends(get_config),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Serve an Artist as a federated music ``Artist`` document.

    Artists have no visibility of their own (they are public containers),
    so the document is always dereferenceable. ``attributedTo`` points at
    the instance actor — artists are shared catalog entities with no owner.
    """
    if _accepts_html(request) and not _accepts_activitypub(request):
        return _spa_response()
    artist = (
        await db.execute(select(Artist).options(selectinload(Artist.image_file)).where(Artist.id == artist_id))
    ).scalar_one_or_none()
    if artist is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    doc = artist_to_music_object(artist, config.federation.instance_domain, _instance_actor_url(config))
    doc["@context"] = MUSIC_ENTITY_CONTEXT
    return JSONResponse(doc, media_type=ACTIVITY_JSON)


@router.get("/albums/{album_id}")
async def get_album_document(
    album_id: str,
    request: Request,
    config: SonghiveConfig = Depends(get_config),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Serve a public Album as a federated music ``Album`` document."""
    if _accepts_html(request) and not _accepts_activitypub(request):
        return _spa_response()
    album = (
        await db.execute(
            select(Album)
            .options(selectinload(Album.artist), selectinload(Album.cover_file))
            .where(Album.id == album_id, Album.visibility == Visibility.PUBLIC.value)
        )
    ).scalar_one_or_none()
    if album is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    doc = album_to_music_object(album, album.artist, config.federation.instance_domain, _instance_actor_url(config))
    doc["@context"] = MUSIC_ENTITY_CONTEXT
    return JSONResponse(doc, media_type=ACTIVITY_JSON)


@router.get("/libraries/{library_id}")
async def get_library_document(
    library_id: str,
    request: Request,
    config: SonghiveConfig = Depends(get_config),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Serve a public Library as a federated music ``Library`` collection.

    ``GET /libraries/{id}`` returns the collection index; ``?page=N``
    returns a ``CollectionPage`` of ``Audio`` items — the same shapes
    Funkwhale exposes under ``/federation/music/libraries/{uuid}``, so a
    remote Funkwhale or Songhive instance can scan the whole library.
    Non-public libraries answer 404 so private data is never hinted at.
    """
    library = (
        await db.execute(
            select(Library)
            .options(selectinload(Library.owner))
            .where(
                Library.id == library_id,
                Library.visibility == Visibility.PUBLIC.value,
            )
        )
    ).scalar_one_or_none()
    if library is None:
        if _accepts_html(request) and not _accepts_activitypub(request):
            return _spa_response()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    domain = config.federation.instance_domain
    actor_url = _owner_actor_url(library.owner, config)
    library_url = f"https://{domain}/libraries/{library.id}"
    page = request.query_params.get("page")
    if page is None:
        if _accepts_html(request) and not _accepts_activitypub(request):
            return _spa_response()
        total = (await db.execute(_library_track_count(library.id))).scalar_one()
        doc = library_to_music_object(
            library_url,
            library.name,
            actor_url,
            total,
            summary=library.description,
            page_size=_LIBRARY_PAGE_SIZE,
        )
        doc["@context"] = MUSIC_ENTITY_CONTEXT
        doc["published"] = library.created_at.isoformat()
        return JSONResponse(doc, media_type=ACTIVITY_JSON)

    try:
        page_num = max(1, int(page))
    except ValueError:
        page_num = 1
    total = (await db.execute(_library_track_count(library.id))).scalar_one()
    tracks = (
        (
            await db.execute(
                _library_track_query(library.id).offset((page_num - 1) * _LIBRARY_PAGE_SIZE).limit(_LIBRARY_PAGE_SIZE)
            )
        )
        .scalars()
        .all()
    )
    return JSONResponse(
        music_collection_page(
            library_url,
            page_num,
            total,
            _audio_items(tracks, domain, actor_url, library_url),
            actor_url,
            page_size=_LIBRARY_PAGE_SIZE,
        ),
        media_type=ACTIVITY_JSON,
    )


@router.get("/libraries/{library_id}/followers")
async def get_library_followers(
    library_id: str,
    request: Request,
    config: SonghiveConfig = Depends(get_config),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Serve the follower collection of a public federated library."""
    exists = (
        await db.execute(
            select(Library.id).where(
                Library.id == library_id,
                Library.visibility == Visibility.PUBLIC.value,
            )
        )
    ).scalar_one_or_none()
    if exists is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    storage = get_federation_storage(config.database.url)
    library_url = f"https://{config.federation.instance_domain}/libraries/{library_id}"
    return await _followers_collection(storage, library_url)


@router.get("/users/{username}/library")
async def get_user_library_document(
    username: str,
    request: Request,
    config: SonghiveConfig = Depends(get_config),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Serve a user's implicit library of public tracks as a ``Library``.

    ``GET /users/{u}/library`` returns the collection index; ``?page=N``
    returns a ``CollectionPage`` of ``Audio`` items. This gives every
    Songhive user a followable federated library without a ``Library``
    row — the same shape Funkwhale publishes per-channel. The implicit
    library only contains public tracks, matching the visibility of a
    public ``Library``.
    """
    user = await _get_federating_user(db, username)
    domain = config.federation.instance_domain
    actor_url = _owner_actor_url(user, config)
    library_url = f"{actor_url}/library"
    base_q = select(Track).where(
        Track.owner_id == user.id,
        Track.visibility == Visibility.PUBLIC.value,
    )
    count_q = select(func.count(Track.id)).where(
        Track.owner_id == user.id,
        Track.visibility == Visibility.PUBLIC.value,
    )

    page = request.query_params.get("page")
    if page is None:
        total = (await db.execute(count_q)).scalar_one()
        doc = library_to_music_object(
            library_url,
            f"{user.display_name or user.username}'s library",
            actor_url,
            total,
            page_size=_LIBRARY_PAGE_SIZE,
        )
        doc["@context"] = MUSIC_ENTITY_CONTEXT
        return JSONResponse(doc, media_type=ACTIVITY_JSON)

    try:
        page_num = max(1, int(page))
    except ValueError:
        page_num = 1
    total = (await db.execute(count_q)).scalar_one()
    tracks = (
        (
            await db.execute(
                base_q.options(
                    selectinload(Track.artist),
                    selectinload(Track.album),
                    selectinload(Track.audio_file),
                    selectinload(Track.genre_associations),
                )
                .order_by(Track.created_at.desc())
                .offset((page_num - 1) * _LIBRARY_PAGE_SIZE)
                .limit(_LIBRARY_PAGE_SIZE)
            )
        )
        .scalars()
        .all()
    )
    return JSONResponse(
        music_collection_page(
            library_url,
            page_num,
            total,
            _audio_items(tracks, domain, actor_url, library_url),
            actor_url,
            page_size=_LIBRARY_PAGE_SIZE,
        ),
        media_type=ACTIVITY_JSON,
    )


@router.get("/users/{username}/library/followers")
async def get_user_library_followers(
    username: str,
    request: Request,
    config: SonghiveConfig = Depends(get_config),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Serve the follower collection of a user's implicit library."""
    user = await _get_federating_user(db, username)
    actor_url = _owner_actor_url(user, config)
    storage = get_federation_storage(config.database.url)
    return await _followers_collection(storage, f"{actor_url}/library")
